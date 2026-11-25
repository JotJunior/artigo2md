# Feature Specification: Persistência da versão desktop (job-store)

**Feature**: `job-store`
**Created**: 2026-11-24
**Status**: Draft

> **Origem**: item 3 do roadmap da versão desktop macOS (fonte: `docs/roadmap.md`), com base no briefing 2.0 e na constitution 1.1.1 (fonte: `docs/briefing.md`) (fonte: `docs/constitution.md`). Depende da feature `container-stack`, já entregue, que fornece o Postgres local (fonte: `docs/specs/container-stack/spec.md`, FR-001). Esta feature entrega só a persistência: o modelo de dados, as migrações e a camada de acesso. Nenhuma rota de API, fila ou tela entra aqui; quem usa a camada é a futura `project-api` (fonte: `docs/roadmap.md`, item 6).

## Clarifications

### Session 2026-11-24

- Q: Qual o estado final do job quando nenhum motor passa na avaliação e o melhor resultado tem `accepted=false`? → A: O job termina como "concluído", com resultado gravado e `accepted=false`; não há estado intermediário (decisão dec-008; fonte: `src/artigo2md/router.py`, função `convert`, que só levanta erro quando todos os motores falham)
- Q: Um documento pode ter mais de um job não final (pendente ou em andamento) ao mesmo tempo? → A: Não; um novo job é recusado enquanto houver outro job não final do mesmo documento, e a regra é garantida por restrição no banco (decisão do operador, block-001)
- Q: Como recuperar um job em andamento cujo worker caiu ou cuja mensagem foi reentregue? → A: Sem retorno de estado: um job em andamento só sai para falho ou cancelado; reprocessar cria um job novo; FR-010 permanece como está (decisão do operador, block-002)
- Q: O que acontece ao arquivar ou excluir um projeto com jobs pendentes ou em andamento? → A: Ambas as operações são permitidas; os jobs não finais viram "cancelado" na mesma transação e os resultados tardios deles são recusados (decisão do operador, block-003)
- Q: A camada deve oferecer desarquivar projeto? → A: Sim; se o nome colidir com um projeto ativo, a operação é recusada com mensagem em português, sem renomear (decisão do operador, block-004)

### Session 2026-11-25 (respostas do operador aos itens humanos dos checklists, block-006)

- Q: A lista fechada de tipos de evento basta para a futura tela de histórico? → A: Sim; novos tipos entram por migração (api CHK026)
- Q: Aceita-se que um job órfão em andamento só saia por intervenção explícita da `project-api`, sem expiração automática? → A: Sim, aceito (api CHK027)
- Q: Riscos residuais L-01 (usuário da imagem, sem papel de menor privilégio), L-02 (sem TLS em loopback/rede interna) e L-03 (texto sem limite de tamanho)? → A: Aceitos (security CHK021, CHK022)
- Q: Acompanhar avisos de segurança conhecidos do `asyncpg`? → A: Não; só a verificação de licença (security CHK023)
- Q: Volume esperado por projeto além dos 1.000 documentos de SC-003? → A: Manter só a meta de SC-003; os demais volumes ficam declarados como não especificados (performance CHK005)
- Q: Limite de crescimento do volume `pgdata`? → A: Nenhum; o usuário exclui projetos quando quiser (FR-011) (performance CHK017)

## User Scenarios & Testing

### User Story 1 - Guardar projetos, fontes e documentos (Priority: P1)

A futura `project-api` precisa registrar os projetos do usuário, as fontes de cada projeto (uma pasta ou um arquivo, indicados no lugar onde estão ou copiados para o projeto) e os documentos (PDFs) que pertencem a essas fontes, para que o app desktop mostre a árvore de arquivos de cada projeto e o status de cada um (fonte: `docs/briefing.md`, itens 7 e 8 do escopo).

**Why this priority**: Sem projetos, fontes e documentos persistidos não existe o que converter nem o que exibir; todas as demais histórias dependem desta base. Cada Mac tem o seu banco e o app é monousuário, sem login nem contas (fonte: `docs/briefing.md`, seção 2).

**Independent Test**: Com o Postgres da stack no ar e o schema aplicado, criar um projeto, adicionar uma fonte do tipo pasta indicada no lugar e uma fonte do tipo arquivo copiada para o projeto, registrar documentos nelas, listar tudo de volta e conferir que os dados voltam idênticos, inclusive depois de reiniciar a conexão.

**Acceptance Scenarios**:

1. **Given** o schema aplicado, **When** um projeto é criado com um nome, **Then** ele é gravado com identificador próprio, data de criação e estado ativo, e pode ser lido de volta com os mesmos dados (cobre FR-001)
2. **Given** um projeto, **When** são registradas uma fonte do tipo pasta indicada no lugar e uma fonte do tipo arquivo copiada para o projeto, **Then** cada fonte guarda seu tipo, seu modo (no lugar ou copiada) e seu caminho, e pertence ao projeto (cobre FR-002)
3. **Given** uma fonte, **When** documentos são registrados nela, **Then** cada documento guarda o caminho do PDF, pertence à fonte e ao projeto, e aparece com status "ainda não processado" (cobre FR-003, FR-009)
4. **Given** um documento já registrado num projeto, **When** o mesmo caminho é registrado de novo nesse projeto, **Then** nenhum documento duplicado é criado e o registro existente é devolvido (cobre FR-003)
5. **Given** um projeto com fontes, documentos e histórico, **When** o projeto é arquivado, **Then** nada é apagado, o projeto deixa de aparecer na listagem padrão e continua consultável (cobre FR-011)
6. **Given** um projeto com todo o seu histórico, **When** o projeto é excluído, **Then** suas fontes, documentos, jobs, tentativas, resultados e eventos são removidos, nenhum arquivo do disco é tocado e os dados de outros projetos permanecem (cobre FR-011)
7. **Given** dois projetos, **When** ambos referenciam o mesmo caminho de arquivo, **Then** cada um tem o seu próprio documento, sem interferência entre eles (cobre FR-003)
8. **Given** um projeto com jobs pendentes ou em andamento, **When** o projeto é arquivado ou excluído, **Then** a operação é permitida e, na mesma transação, os jobs não finais passam para "cancelado" (no arquivamento, com o evento de cancelamento registrado; na exclusão, junto com todo o resto), e uma tentativa posterior de gravar resultado nesses jobs é recusada (cobre FR-010, FR-011)
9. **Given** um projeto arquivado, **When** ele é desarquivado e nenhum projeto ativo tem o mesmo nome (ignorando maiúsculas e minúsculas), **Then** o projeto volta a ser ativo e a aparecer na listagem padrão; **When** já existe um projeto ativo com esse nome, **Then** o desarquivamento é recusado com mensagem em português, sem renomear nada (cobre FR-001, FR-011)

---

### User Story 2 - Registrar jobs, tentativas por motor e resultados com qualidade (Priority: P1)

A `project-api` registra cada pedido de conversão de um documento como um job, as tentativas feitas por cada motor naquele job (com a nota de qualidade de cada uma) e, quando o job termina com sucesso, o resultado final: o caminho do Markdown gerado, o motor vencedor, as métricas de qualidade e os avisos. Assim o usuário consulta depois o que foi feito, com qual motor e com que qualidade (fonte: `docs/briefing.md`, itens 2 e 11 do escopo; `docs/roadmap.md`, item 3).

**Why this priority**: Guardar o histórico e os resultados de cada conversão é o motivo declarado da versão desktop ter um banco (fonte: `docs/briefing.md`, seção 1). Depende só do modelo de dados de projetos e documentos e já entrega valor consultável.

**Independent Test**: Para um documento, registrar um job que passa por duas tentativas (uma reprovada pela avaliação, outra aceita), finalizá-lo com sucesso e gravar o resultado; ler tudo de volta e comparar cada campo de qualidade com o objeto de origem (`Quality`, em `src/artigo2md/router.py`).

**Acceptance Scenarios**:

1. **Given** um documento registrado, **When** um job de conversão é criado para ele, **Then** o job nasce no estado "pendente", guarda o motor solicitado (um dos valores aceitos pelo roteador: `auto`, `docling`, `pymupdf`, `mineru`, `markitdown`) e a data do pedido (cobre FR-004)
2. **Given** um job em andamento, **When** duas tentativas são registradas, **Then** cada uma guarda o nome do motor, a duração em segundos e as métricas de qualidade avaliadas (ou o texto do erro, quando o motor falhou), na ordem em que ocorreram (cobre FR-005)
3. **Given** um job concluído com sucesso, **When** o resultado é gravado, **Then** ele guarda o caminho do arquivo Markdown, o motor vencedor, os seis campos de qualidade (`score`, `accepted`, `tables_found`, `tables_expected`, `chars_per_page`, `issues`) e os avisos, e esse conjunto lido de volta é idêntico ao gravado, inclusive a ordem dos itens de `issues` (cobre FR-006, FR-007)
4. **Given** um documento já convertido, **When** o usuário pede reprocessamento com outro motor, **Then** um novo job é criado e o job anterior, suas tentativas e seu resultado permanecem intactos (cobre FR-004)
5. **Given** um job já concluído, falho ou cancelado, **When** se tenta mudá-lo para outro estado de andamento, **Then** a alteração é recusada com mensagem em português e o registro não muda (cobre FR-010)
6. **Given** um documento que já tem um job pendente ou em andamento, **When** se tenta criar outro job para o mesmo documento, **Then** a criação é recusada com mensagem em português e nenhum registro é criado; **When** o job existente chega a um estado final, **Then** um novo job pode ser criado (cobre FR-004)
7. **Given** um job em andamento cujo worker caiu, **When** se tenta devolvê-lo ao estado pendente, **Then** a mudança é recusada; o job só pode passar a falho ou cancelado, e o reprocessamento cria um job novo (cobre FR-010)
8. **Given** nenhum motor passou na avaliação e o melhor resultado tem `accepted=false`, **When** o job é finalizado, **Then** ele termina como "concluído" com o resultado e `accepted=false` gravados, sem estado intermediário (cobre FR-006, FR-010)
9. **Given** um job com todos os motores falhando, **When** o job é finalizado como falho, **Then** as tentativas com seus erros ficam guardadas e nenhum resultado é gravado (cobre FR-005, FR-006)
10. **Given** um job que termina, **When** o estado final, o resultado e o evento de conclusão são gravados, **Then** os três são gravados juntos ou nenhum deles é gravado (cobre FR-014)

---

### User Story 3 - Criar e evoluir o schema por migrações versionadas (Priority: P1)

Quem mantém o projeto ou a futura `project-api` consegue criar o schema do zero num Postgres vazio e evoluí-lo em versões futuras, com a certeza de saber qual versão está aplicada e de que reaplicar não estraga nada.

**Why this priority**: Sem migrações versionadas o schema não é reproduzível na máquina de cada usuário, e uma atualização do app que mude o banco corromperia dados de histórico. É pré-requisito do uso real da camada.

**Independent Test**: Num banco vazio, aplicar as migrações e conferir a versão registrada; aplicar de novo e conferir que nada muda; simular um banco com versão mais nova que a conhecida e conferir a recusa.

**Acceptance Scenarios**:

1. **Given** um Postgres sem nenhuma tabela do projeto, **When** as migrações são aplicadas, **Then** o schema completo é criado, cada migração aplicada fica registrada com sua versão e a data, e o processo termina com sucesso (cobre FR-012)
2. **Given** um banco com todas as migrações já aplicadas, **When** as migrações são aplicadas de novo, **Then** nada é alterado e o resultado informa que o banco já está atualizado (cobre FR-012)
3. **Given** um banco com parte das migrações aplicadas, **When** as migrações são aplicadas, **Then** só as pendentes são executadas, na ordem das versões (cobre FR-012)
4. **Given** uma migração que falha no meio, **When** ela é aplicada, **Then** nenhuma alteração parcial dela permanece no banco, a versão não é registrada e a mensagem de erro em português indica qual migração falhou (cobre FR-012)
5. **Given** um banco que registra uma versão mais nova do que a conhecida pelo código, **When** as migrações são aplicadas, **Then** o processo recusa continuar e informa, em português, que o banco foi criado por uma versão mais nova do app (cobre FR-012)
6. **Given** dois processos aplicando as migrações ao mesmo tempo, **When** ambos terminam, **Then** cada migração foi aplicada exatamente uma vez e nenhum dos dois termina com erro por causa da concorrência (cobre FR-012)
7. **Given** uma migração já aplicada cujo conteúdo foi alterado depois, **When** as migrações são aplicadas, **Then** a divergência é detectada e o processo recusa continuar, informando qual migração mudou (cobre FR-012)

---

### User Story 4 - Consultar o histórico de eventos (Priority: P2)

A `project-api` registra eventos de histórico (por exemplo: documento adicionado, job criado, tentativa iniciada, conversão concluída, job cancelado) e o app consulta a linha do tempo de um documento ou de um projeto, em ordem cronológica.

**Why this priority**: Dá a visão de "o que aconteceu e quando" que o usuário espera do histórico (fonte: `docs/roadmap.md`, item 8). Pode ser entregue depois do núcleo de jobs e resultados porque estes já guardam o estado final.

**Independent Test**: Registrar uma sequência de eventos de um documento e de outro, consultar a linha do tempo de cada um e conferir a ordem e o isolamento entre eles.

**Acceptance Scenarios**:

1. **Given** um documento com jobs e tentativas, **When** eventos são registrados, **Then** cada evento guarda seu tipo, o instante, a mensagem e a ligação com o projeto e, quando houver, com o documento e o job (cobre FR-008)
2. **Given** eventos de dois documentos intercalados no tempo, **When** se consulta a linha do tempo de um deles, **Then** só aparecem os eventos dele, em ordem cronológica estável, inclusive para eventos com o mesmo instante (cobre FR-008)
3. **Given** um evento já registrado, **When** se tenta alterá-lo ou removê-lo diretamente, **Then** a camada não oferece essa operação: o histórico só cresce, exceto pela remoção do projeto inteiro (cobre FR-008, FR-011)
4. **Given** um projeto com centenas de eventos, **When** a linha do tempo é consultada com limite e deslocamento, **Then** a camada devolve só a página pedida (cobre FR-008)

---

### User Story 5 - Camada de acesso exclusiva da `project-api`, testada de ponta a ponta (Priority: P2)

A equipe de desenvolvimento dispõe de uma única camada de repositório assíncrona para ler e gravar tudo o que esta feature modela, com testes de integração que rodam contra o Postgres da stack quando uma variável de ambiente os habilita. Os workers de conversão e a CLI não acessam o banco: só a `project-api` usa a camada (fonte: `docs/briefing.md`, seção 4).

**Why this priority**: Entrega a fronteira que a `project-api` vai consumir e protege a decisão arquitetural de que os workers ficam sem banco. Tem valor isolado como contrato verificável, mas nenhum usuário final depende dela sozinha.

**Independent Test**: Rodar `uv run pytest` sem Docker e conferir que passa; habilitar a variável de ambiente com a stack no ar e conferir que os testes de integração passam e não tocam os dados reais do usuário; conferir por teste automatizado que os módulos de conversão não importam a camada.

**Acceptance Scenarios**:

1. **Given** a máquina sem Docker e sem a variável de ambiente, **When** `uv run pytest` roda, **Then** a suíte passa e os testes de integração aparecem como pulados com o motivo em português (cobre FR-019)
2. **Given** a stack no ar e a variável de ambiente definida, **When** os testes de integração rodam, **Then** eles criam um banco temporário próprio, aplicam as migrações nele, executam os cenários desta spec e removem o banco temporário ao final, mesmo quando um teste falha (cobre FR-019)
3. **Given** a stack no ar com dados reais do usuário, **When** os testes de integração rodam, **Then** nenhum registro do banco real do usuário é lido, alterado ou removido (cobre FR-019)
4. **Given** o código do worker, da CLI (`convert`, `doctor`) e da interface web atual, **When** um teste automatizado inspeciona o que eles importam, **Then** nenhum deles depende da camada de persistência nem do driver do banco (cobre FR-017, FR-021)
5. **Given** a camada configurada, **When** qualquer operação é chamada, **Then** ela é assíncrona e não bloqueia o laço de eventos da API enquanto aguarda o banco (cobre FR-014)
6. **Given** nomes e caminhos com acentos, espaços internos, aspas, barras e caracteres de comando de banco, **When** são gravados e lidos de volta, **Then** voltam idênticos ao valor gravado e nada é interpretado como comando; o nome de projeto é a única exceção declarada, pois é gravado sem espaços nas pontas (cobre FR-001, FR-015)
7. **Given** parâmetros de conexão ausentes ou inválidos, **When** a camada é inicializada, **Then** ela falha com mensagem em português que cita só o nome do parâmetro, nunca o valor da senha (cobre FR-016)
8. **Given** o schema criado pelas migrações e os módulos da camada, **When** um teste automatizado inspeciona os identificadores (tabelas, colunas, índices, gatilhos, funções, classes, módulos e arquivos), **Then** todos estão em inglês, em ASCII e em `snake_case`/`PascalCase` conforme o tipo, e os comentários e mensagens ao usuário estão em português; a verificação automática cobre a forma (ASCII, caixa) e a revisão humana cobre o idioma (cobre FR-020)
9. **Given** qualquer instante gravado (criação, pedido, início, fim, evento, aplicação de migração), **When** é lido de volta, **Then** vem com fuso horário definido (`datetime` com `tzinfo`), e a ordem cronológica é a mesma com qualquer fuso configurado na máquina ou na sessão do banco (cobre FR-022)

---

### User Story 6 - Licença do driver confirmada em fonte oficial (Priority: P3)

Quem mantém o projeto consulta o registro de licenças do repositório e vê o driver do Postgres escolhido, a licença confirmada, o enquadramento no Princípio II e o endereço da fonte oficial consultada.

**Why this priority**: A constitution exige a verificação da licença de toda nova dependência em fonte oficial na etapa `plan` da feature que a introduz (fonte: `docs/constitution.md`, Princípio II). Sem isso a feature não pode ser entregue, mas o registro em si não muda o comportamento para o usuário.

**Independent Test**: Abrir o registro de licenças e conferir que o driver aparece com licença permissiva, fonte oficial citada e sem a marca "presumida".

**Acceptance Scenarios**:

1. **Given** o driver escolhido no `plan`, **When** se consulta o registro de licenças, **Then** ele consta com licença MIT, Apache ou BSD, o enquadramento no Princípio II e o endereço da fonte oficial onde a licença foi confirmada (cobre FR-018)
2. **Given** um candidato a driver cuja licença não pôde ser confirmada em fonte oficial ou que é GPL ou AGPL, **When** a escolha é feita, **Then** ele é rejeitado como dependência obrigatória e a pendência vira bloqueio para decisão do operador, nunca uma suposição (cobre FR-018)
3. **Given** a imagem da API da `container-stack`, **When** o driver é incluído, **Then** a imagem continua sem nenhum motor de conversão pesado (Docling, pymupdf4llm) (cobre FR-018)

---

### Edge Cases

- Nome de projeto vazio, só com espaços ou igual (ignorando maiúsculas e minúsculas) ao de outro projeto ativo: a criação é recusada com mensagem em português; um projeto arquivado libera o seu nome para reuso (cobre FR-001)
- Desarquivar um projeto cujo nome já pertence a outro projeto ativo (criado depois do arquivamento): o desarquivamento é recusado com mensagem em português; o projeto permanece arquivado e nenhum nome é alterado automaticamente (cobre FR-001, FR-011)
- Arquivar um projeto já arquivado ou desarquivar um projeto já ativo: a operação é recusada com mensagem em português e nada muda, de forma simétrica (cobre FR-011)
- Nome de projeto com espaços ou caracteres de espaço Unicode nas pontas: é gravado sem eles; nomes que diferem só por caixa, conforme `casefold` (inclusive `Á`/`á` e `ß`/`ss`), contam como iguais; `a` e `á` continuam diferentes (cobre FR-001)
- Fonte do tipo pasta indicada no lugar cujo caminho não existe no disco do host: a camada grava o caminho como informado e não verifica o sistema de arquivos, porque a API não enxerga o disco do host; a lista de arquivos vem do app desktop (fonte: `docs/roadmap.md`, item 6) (cobre FR-002)
- Tipo de fonte ou modo de fonte fora dos valores aceitos (por exemplo, "cópia" no lugar de copiada): a gravação é recusada, sem registro parcial (cobre FR-002)
- Documento cujo arquivo foi renomeado ou removido no disco depois do registro: o registro e o histórico permanecem; a camada não apaga nem altera nada por causa do disco (cobre FR-003, FR-011)
- Motor com nome fora da lista conhecida numa tentativa ou resultado (por exemplo, um motor novo no futuro): a camada guarda o nome como texto e não o rejeita; só o motor solicitado no job é validado contra os valores aceitos pelo roteador (cobre FR-004, FR-005)
- Tentativa que falhou: as métricas de qualidade ficam ausentes e o texto do erro fica preenchido; uma tentativa nunca tem as duas informações vazias (cobre FR-005)
- Job que termina com sucesso sem resultado, ou um segundo resultado para o mesmo job: o primeiro caso é recusado e o segundo é recusado, pois cada job concluído com sucesso tem exatamente um resultado (cobre FR-006)
- Nenhum motor passou na avaliação, mas houve um melhor resultado com `accepted=false`: o job termina como concluído, com o resultado gravado e `accepted=false`; só quando todos os motores falham o job termina como falho, sem resultado (cobre FR-006, FR-007, FR-010)
- Novo job para um documento que já tem um job pendente ou em andamento (inclusive em chamadas simultâneas): a criação é recusada com mensagem em português, distinguível de erro de banco, e a regra vale mesmo sob concorrência por ser garantida pelo banco (cobre FR-004, FR-013)
- Worker que caiu com o job em andamento, ou mensagem reentregue depois do término: o job não volta a pendente; só pode terminar como falho ou cancelado, e o reprocessamento cria um job novo (cobre FR-010)
- Resultado ou finalização tardia de um job que foi cancelado por arquivamento ou exclusão do projeto: a gravação é recusada como a de qualquer job em estado final, sem alterar o registro e sem criar resultado (cobre FR-010, FR-011)
- Resultado com `issues` vazio: é um resultado válido; a lista vazia volta como lista vazia, não como ausente (cobre FR-007)
- Nota de qualidade fora do intervalo de 0 a 100, ou `chars_per_page` negativo: a gravação é recusada com mensagem em português (cobre FR-007)
- Evento de histórico ligado a um job ou documento que não existe, ou a um documento de outro projeto: a gravação é recusada (cobre FR-008)
- Estado de um documento cujo job mais recente foi cancelado ou falhou, mas que tem um job anterior concluído com sucesso: o status do documento reflete o job mais recente, e o resultado do último job bem-sucedido continua disponível para consulta (cobre FR-009)
- Entrega repetida da mesma notificação de término de um job (comportamento esperado de filas): a segunda gravação de um job já terminado é recusada sem alterar o registro nem duplicar resultado ou evento, e a recusa é distinguível de um erro de banco (cobre FR-010)
- Banco indisponível durante uma operação: a camada falha com mensagem em português e sem deixar gravação parcial; reconectar e repetir funciona (cobre FR-014, FR-016)
- Parâmetro de endereço do banco apontando para um host remoto (fora da lista fechada, ou o nome `postgres` resolvendo para endereço público): a camada recusa a conexão com mensagem em português, pois nenhum serviço remoto é permitido (cobre FR-016)
- `convert`, `doctor` e `serve` com o banco derrubado ou sem driver instalado: comportam-se exatamente como hoje, sem aviso e sem tentar conectar (cobre FR-021)
- Dados de histórico e retenção: os registros ficam guardados por tempo indeterminado, até o usuário excluir o projeto (cobre FR-011)

## Requirements

### Functional Requirements

- **FR-001**: O sistema MUST persistir **projetos** com identificador, nome, instantes de criação e de última alteração e estado (ativo ou arquivado); o nome MUST ser gravado sem espaços (de qualquer tipo Unicode) nas pontas, MUST ser não vazio depois disso e único entre projetos ativos, ignorando maiúsculas e minúsculas, e essa unicidade MUST valer também ao desarquivar um projeto. "Ignorando maiúsculas e minúsculas" é a comparação por `casefold` do Python sobre o nome normalizado em NFC, calculada pela camada e gravada numa coluna própria; ela independe da localidade do banco, trata `Á`/`á` como iguais e, por decisão explícita, trata `ß` e `ss` como iguais (`Straße` = `STRASSE`).
- **FR-002**: O sistema MUST persistir **fontes** pertencentes a um projeto, cada uma com tipo (pasta ou arquivo), modo (indicada no lugar ou copiada para o projeto) e caminho; tipo e modo MUST aceitar somente os valores definidos, e a camada MUST NOT verificar a existência do caminho no sistema de arquivos; duas fontes com o mesmo caminho no mesmo projeto são permitidas (cada uma com seu identificador), pois a fonte não é chave de negócio.
- **FR-003**: O sistema MUST persistir **documentos** (PDFs) pertencentes a uma fonte e a um projeto, cada um com seu caminho, e MUST garantir que o mesmo caminho não gere dois documentos no mesmo projeto; projetos distintos MAY referenciar o mesmo caminho de forma independente; registrar de novo um caminho já registrado no projeto devolve o documento existente, que mantém a fonte original (a fonte informada na segunda chamada só é validada como pertencente ao projeto).
- **FR-004**: O sistema MUST persistir **jobs de conversão**, cada um ligado a um documento, com motor solicitado (validado contra os valores aceitos pelo roteador: `auto`, `docling`, `pymupdf`, `mineru`, `markitdown`; fonte: `src/artigo2md/router.py`, constante `ENGINES`), estado, instantes de pedido, início e fim e resumo do erro quando falhou; um documento MAY ter vários jobs ao longo do tempo, mas MUST ter no máximo um job não final (pendente ou em andamento) por vez: a criação de um novo job MUST ser recusada, com mensagem em português e de forma distinguível de erro de banco, enquanto houver outro job não final do mesmo documento, e essa regra MUST ser garantida por restrição no próprio banco; criar um novo job (reprocessamento) MUST NOT alterar os jobs anteriores.
- **FR-005**: O sistema MUST persistir as **tentativas por motor** de cada job, na ordem em que ocorreram, com o nome do motor, a duração em segundos e, conforme o resultado da tentativa, as métricas de qualidade avaliadas ou o texto do erro (fonte: `src/artigo2md/router.py`, classe `Attempt`); o nome do motor MUST ser guardado como texto livre, sem restrição à lista conhecida. O texto do erro do motor, `issues`, o resumo do erro do job e as mensagens de evento são **texto livre que pode conter trechos derivados de documento**; permanecem só no banco local e MUST NOT ser enviados para fora da máquina (Princípio I).
- **FR-006**: O sistema MUST persistir o **resultado** de cada job concluído com sucesso, com o caminho do arquivo Markdown, o motor vencedor, as métricas de qualidade e os avisos; cada job concluído com sucesso MUST ter exatamente um resultado e um job que não foi concluído com sucesso MUST NOT ter resultado; um job cujo melhor resultado tem `accepted=false` MUST terminar como concluído, com esse resultado gravado (sem estado intermediário), e só o job em que todos os motores falharam termina como falho.
- **FR-007**: O sistema MUST guardar as métricas de qualidade com todos os campos de `router.Quality` — `score`, `accepted`, `tables_found`, `tables_expected`, `chars_per_page` e `issues` (fonte: `src/artigo2md/router.py`, classe `Quality`) — de modo que a leitura devolva exatamente o que foi gravado, inclusive a ordem e o texto de `issues`; MUST recusar `score` fora do intervalo de 0 a 100 e valores negativos de contagem e de densidade. Os **avisos** do resultado são o conjunto de mensagens de `issues` da avaliação do motor vencedor (fonte: `src/artigo2md/router.py`, função `evaluate`).
- **FR-008**: O sistema MUST persistir **eventos de histórico** somente de acréscimo (sem operação de alteração nem de remoção individual), cada um com tipo, instante, mensagem e ligação ao projeto e, opcionalmente, ao documento e ao job; MUST validar que documento e job citados pertencem ao projeto do evento e MUST permitir a consulta da linha do tempo por projeto e por documento, em ordem cronológica estável e com paginação; a ordem é a do instante em que a linha do evento foi gravada (não o início da transação) e, em empate, a do identificador crescente. As operações da camada gravam elas mesmas, na mesma transação, os eventos `project_created`, `source_added`, `document_added` (só quando o documento é novo), `job_created`, `job_started`, `attempt_recorded`, `job_succeeded`, `job_failed`, `job_cancelled`, `project_archived` e `project_unarchived`; `attempt_started` e eventos de outra origem são registrados pela `project-api` via `add_event`; excluir o projeto não grava evento, pois o histórico é removido junto.
- **FR-009**: O sistema MUST permitir listar os documentos de um projeto com o **status** de cada um e o resumo do último resultado, numa única consulta e sem paginação (o limite de referência é 1.000 documentos, SC-003; acima disso a operação funciona sem garantia de tempo); o status é derivado do job mais recente do documento ("ainda não processado" quando não há job). Cada item informa o documento (identificador, fonte, caminho), o status, o identificador e o instante de pedido do job mais recente e o resumo do último resultado, isto é, do job bem-sucedido mais recente: identificador do job, caminho do Markdown, motor, nota, aceito ou não e instante da gravação; o resumo é ausente quando nenhum job do documento foi bem-sucedido.
- **FR-010**: O sistema MUST validar as transições de estado do job (de pendente para em andamento; de em andamento para concluído, falho ou cancelado; de pendente também para cancelado) e MUST recusar, com mensagem em português e sem alterar o registro, qualquer tentativa de alterar um job em estado final; MUST NOT existir transição de retorno (por exemplo, de em andamento para pendente): um job em andamento só sai para concluído, falho ou cancelado, e a recuperação de um worker que caiu é feita criando um job novo; a recusa MUST ser distinguível de erro de banco, para que uma notificação repetida seja tratada como já processada. Não há expiração automática de job em andamento: quem recupera um job órfão é a `project-api`, chamando `fail_job` ou `cancel_job` sobre ele e só depois `create_job` para o mesmo documento.
- **FR-011**: O sistema MUST permitir **arquivar** um projeto sem apagar nada, ocultando-o da listagem padrão (arquivar um projeto já arquivado é recusado com mensagem em português), MUST permitir **desarquivar** um projeto (recusando, com mensagem em português e sem renomear, quando já existir um projeto ativo com o mesmo nome, ignorando maiúsculas e minúsculas; desarquivar um projeto já ativo é recusado de forma simétrica, com mensagem em português), e MUST permitir **excluir** um projeto, removendo junto todos os seus dados dependentes (fontes, documentos, jobs, tentativas, resultados e eventos) e nenhum dado de outros projetos; arquivar e excluir MUST ser permitidos mesmo com jobs pendentes ou em andamento, e esses jobs MUST passar para cancelado na mesma transação da operação (resultados tardios deles são recusados conforme FR-010); nenhuma operação desta camada MUST tocar arquivos do disco; os dados são mantidos por tempo indeterminado até a exclusão do projeto.
- **FR-012**: O sistema MUST fornecer **migrações versionadas e ordenadas** do schema, aplicáveis a um banco vazio ou a um banco com versões anteriores; MUST registrar a versão e o instante de cada migração aplicada; MUST ser idempotente (reaplicar não altera nada); MUST aplicar cada migração por inteiro ou nada; MUST serializar aplicações concorrentes, de modo que cada migração rode uma só vez; MUST recusar um banco com versão mais nova do que a conhecida; e MUST detectar e recusar uma migração já aplicada cujo conteúdo mudou. As migrações só avançam (não há reversão automática).
- **FR-013**: O sistema MUST manter o modelo de dados com integridade referencial: nenhum documento sem fonte e projeto, nenhum job sem documento, nenhuma tentativa ou resultado sem job, nenhum evento sem projeto e nunca dois jobs não finais para o mesmo documento, mesmo diante de chamadas concorrentes.
- **FR-014**: O sistema MUST fornecer uma **camada de repositório assíncrona** como único ponto de acesso ao banco para tudo o que esta feature modela, com todas as operações assíncronas e com suporte a agrupar várias gravações numa única transação (por exemplo, estado final do job, resultado e evento de conclusão, gravados juntos ou nenhum).
- **FR-015**: O sistema MUST NOT montar comandos de banco por concatenação de valores vindos do usuário (nomes, caminhos, mensagens); nomes e caminhos com acentos, espaços, aspas e caracteres especiais MUST ser gravados e lidos de volta sem alteração.
- **FR-016**: O sistema MUST obter os parâmetros de conexão dos mesmos parâmetros da stack (`stack/.env`: usuário, senha, nome do banco e porta do Postgres; fonte: `stack/compose.yaml`), com endereço padrão `127.0.0.1`, restrito à lista fechada `127.0.0.1`, `::1`, `localhost` e ao nome de serviço `postgres` (a escolha, conforme a camada rode no host ou no container da API, é do chamador); o nome `postgres` só é aceito se resolver exclusivamente para endereços de loopback ou privados (`stack/compose.yaml` não declara sub-rede nem rede nomeada, então não há faixa mais estreita que se possa citar) e a conexão MUST usar o endereço já validado, sem resolver o nome de novo; MUST falhar, com mensagem em português que cita só o nome do parâmetro, quando algum parâmetro estiver ausente ou inválido; MUST NOT registrar nem exibir a senha em mensagem, log ou erro; MUST NOT conectar a nenhum serviço remoto nem enviar telemetria (fonte: `docs/constitution.md`, Princípio I).
- **FR-017**: O sistema MUST restringir o uso da camada de persistência à `project-api`: o worker, a CLI (`convert`, `doctor`, `serve`) e os módulos de conversão MUST NOT importar a camada nem o driver do banco, e um teste automatizado MUST verificar essa fronteira.
- **FR-018**: O sistema MUST usar um driver do Postgres com licença permissiva (MIT, Apache ou BSD) como dependência obrigatória, confirmada em fonte oficial na etapa `plan` e registrada no registro de licenças do repositório com o enquadramento no Princípio II e a fonte consultada; um candidato com licença não confirmada, GPL ou AGPL MUST ser rejeitado; a inclusão do driver MUST NOT trazer Docling nem pymupdf4llm para a imagem da API.
- **FR-019**: O sistema MUST cobrir a feature com testes automatizados: os testes que não precisam do banco MUST rodar sem Docker; os testes de integração contra o Postgres da stack MUST ficar atrás de uma variável de ambiente (convenção do Princípio IV), MUST usar um banco temporário próprio, criado e removido a cada execução, e MUST NOT ler nem alterar o banco real do usuário.
- **FR-020**: O sistema MUST usar nomes em inglês para tudo o que for sintaxe (tabelas, colunas, enums, índices, funções, classes, módulos e arquivos), e MUST escrever em português os comentários, as mensagens ao usuário e a documentação (fonte: regra do operador; `docs/constitution.md`, Princípio III).
- **FR-021**: O sistema MUST manter o comportamento atual de `convert`, `doctor` e `serve` inalterado: sem conectar ao banco, sem exigir o banco no ar e sem exigir o driver para funcionar (paridade com `container-stack`, FR-010).
- **FR-022**: O sistema MUST guardar todos os instantes com fuso horário definido, de forma que a ordem cronológica seja a mesma em qualquer configuração da máquina.

> Decisões de infraestrutura: scheduling, rotação de chaves, refresh de token e mutex multi-pod não se aplicam (banco local, monousuário, sem réplicas); a serialização de migrações concorrentes é tratada em FR-012. Idempotência: o tratamento de notificações repetidas de término de job é tratado em FR-010. Backup e restauração ficam fora do escopo (o histórico é local, vive no volume nomeado da `container-stack` e os resultados podem ser regerados reprocessando os PDFs).

### Key Entities

- **Projeto**: agrupamento de trabalho do usuário, com nome, estado (ativo ou arquivado) e instantes de criação e alteração; dono de fontes, documentos, jobs e eventos.
- **Fonte**: pasta ou arquivo associado a um projeto, com tipo, modo (no lugar ou copiada) e caminho; origem dos documentos.
- **Documento**: um PDF de uma fonte dentro de um projeto, identificado pelo caminho; possui status derivado do job mais recente.
- **Job de conversão**: pedido de converter um documento, com motor solicitado, estado e instantes; um documento tem zero ou mais jobs, no máximo um deles não final (pendente ou em andamento) por vez.
- **Tentativa**: passagem de um motor por um job, com duração e com métricas de qualidade ou erro; um job tem uma ou mais tentativas, ordenadas.
- **Resultado**: saída de um job concluído com sucesso: caminho do Markdown, motor vencedor, métricas de qualidade (`Quality`) e avisos; no máximo um por job.
- **Evento de histórico**: registro imutável do que aconteceu, com tipo, instante, mensagem e ligação ao projeto, documento e job.
- **Versão do schema**: registro das migrações aplicadas, com versão e instante.

### Assumptions

- Os estados do job e os tipos de evento exatos, os nomes das tabelas e colunas (em inglês) e o driver ficam para o `plan`; esta spec só fixa o que o modelo precisa distinguir. O contrato de mensagens dos workers pertence à feature `conversion-queue` (fonte: `docs/roadmap.md`, item 4) e não é definido aqui.
- "Avisos" são as mensagens de `Quality.issues` do motor vencedor, única fonte de avisos existente no código hoje (fonte: `src/artigo2md/router.py`); eventuais avisos de outra origem entram como evento de histórico, sem campo próprio.
- As migrações são só de avanço e são aplicadas por chamada explícita da camada (usada pelos testes e, no futuro, pela `project-api` ao iniciar); esta feature não acrescenta subcomando à CLI.
- O resumo da inspeção do PDF (`Analysis`) não é persistido nesta feature.
- Nenhuma ferramenta, versão de driver ou porta além das já documentadas na `container-stack` é presumida por esta spec.
- Riscos residuais **aceitos pelo operador** (block-006): L-01 (conexão com o usuário da imagem do Postgres, sem papel de menor privilégio), L-02 (sem TLS em loopback ou na rede interna da stack) e L-03 (texto sem limite de tamanho em nomes, caminhos e mensagens). Também aceito: job órfão em andamento só sai por intervenção explícita da `project-api`, sem expiração automática; a lista fechada de tipos de evento (novos tipos entram por migração); nenhum acompanhamento de avisos de segurança do driver além da verificação de licença (FR-018); `pgdata` sem limite de crescimento, com o usuário excluindo projetos quando quiser (FR-011).
- Volumes e tempos **não especificados** (decisão do operador, block-006): só SC-001 e SC-003 têm meta numérica. Não há meta de tempo para `list_events`, `create_job`, `complete_job`, arquivar nem excluir projeto grande, nem volume esperado de jobs, tentativas e eventos por projeto; nenhum número é presumido.
- Residual conhecido do nome `postgres` (L-04): como a stack não declara sub-rede, um host de rede local privada que responda por esse nome (por exemplo via domínio de busca do resolvedor) passaria na verificação de endereço privado; a lista fechada de hosts e o fato de ser o próprio usuário quem define `ARTIGO2MD_POSTGRES_HOST` limitam o risco. Registrado no plan; não é bloqueio.

## Success Criteria

### Measurable Outcomes

- **SC-001**: Num Postgres vazio, aplicar todas as migrações termina com sucesso em até 10 segundos em 100% das execuções (o tempo exclui a espera pelo lock de outro processo), e uma segunda aplicação não altera nenhum registro nem o schema. Ambiente de referência: a máquina de desenvolvimento onde o teste roda, com o Postgres da stack local, cache quente; o teste registra na saída o ambiente medido (sistema, versão do Python e `server_version` do Postgres).
- **SC-002**: Para 100% dos objetos `Quality` usados nos testes (incluindo `issues` vazio, `issues` com vários itens e caracteres acentuados), os seis campos lidos do banco são idênticos aos gravados.
- **SC-003**: Listar os documentos de um projeto com 1.000 documentos, com o status e o resumo do último resultado de cada um, leva até 2 segundos no mesmo ambiente de referência do SC-001, com cache quente. "Uma única consulta" é verificada por um contador no ponto único de execução de SQL do repositório, que deve registrar exatamente 1 comando para a operação.
- **SC-004**: Em 100% das verificações automatizadas, nenhum módulo do worker, da CLI e dos motores importa a camada de persistência ou o driver do banco.
- **SC-005**: `uv run pytest` passa sem Docker instalado; os testes de integração passam quando habilitados pela variável de ambiente, com a stack no ar, antes do merge.
- **SC-006**: Zero registros do banco real do usuário são alterados durante a execução dos testes de integração (verificado pelo uso exclusivo de um banco temporário removido ao final).
- **SC-007**: Zero ocorrências da senha do banco em mensagens de erro, logs e saída dos testes nos cenários de falha de conexão e de parâmetro inválido.
- **SC-008**: 100% dos itens de licença desta feature (driver) constam no registro de licenças com fonte oficial citada e enquadramento no Princípio II; zero itens com licença não confirmada.
- **SC-009**: Em 100% das verificações automatizadas, todo identificador de SQL da camada é ASCII no formato `snake_case`; o idioma (inglês nos identificadores, português em comentários, mensagens e documentação) é conferido em revisão humana na tarefa de conformidade (cobre FR-020; forma de verificação em US5-8).
- **SC-010**: Em 100% das leituras de instantes nos testes, o valor volta com fuso definido e a ordem é a mesma com a sessão do banco em fusos diferentes (cobre FR-022; forma de verificação em US5-9).

## Delta Requirements

**Skip**: o corpus canônico `docs/specs/current/` não existe neste projeto e esta feature é puramente nova (nenhum comportamento ativo é alterado; `convert`, `doctor` e `serve` permanecem idênticos, FR-021) — feature-00c, 2026-11-24
