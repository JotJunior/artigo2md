# Feature Specification: API da versão desktop (project-api)

**Feature**: `project-api`
**Created**: 2026-11-25
**Status**: Draft

> **Origem**: item 6 do roadmap da versão desktop macOS (fonte: `docs/roadmap.md`), com base no briefing 2.0 e na constitution 1.2.0 (fonte: `docs/briefing.md`) (fonte: `docs/constitution.md`). Depende de `job-store`, que entrega o schema e a camada de repositório no banco da stack (fonte: `docs/specs/job-store/contracts/repository.md`), e de `conversion-queue`, que entrega o worker nativo e o contrato de mensagens da fila, `schema_version` 1 (fonte: `docs/specs/conversion-queue/contracts/queue-messages.md`, `src/artigo2md/jobqueue/contract.py`). Estende a API base de `container-stack`, que hoje só responde `GET /health` (fonte: `src/artigo2md/api/app.py`, `docs/specs/container-stack/contracts/api-health.md`). As features `desktop-projects` e `desktop-processing` consomem esta API e só podem usar o que ela de fato expõe (fonte: `docs/roadmap.md`, itens 7 e 8).
>
> **Regra de veracidade (Constitution V)**: nesta spec, nomes de campos, estados, motores e tipos de evento vêm dos arquivos citados; o desenho das rotas e dos corpos HTTP é decisão desta feature, vai para o `plan` e para o contrato publicado, e nada dele é afirmado aqui como fato externo.

## Clarifications

### Session 2026-11-25

- Q: Para fontes no modo copiada para o projeto, quem coloca o PDF no diretório de dados do app? → A: O app desktop copia o arquivo para a pasta do host que o container monta e registra o caminho na API; a API não recebe nem grava o conteúdo do PDF (FR-006).
- Q: Como a API passa a conhecer o caminho do host equivalente ao diretório de dados montado? → A: Por configuração fixa da stack: o par (caminho do host, ponto de montagem no container) vem de variável de ambiente, e a conversão host-container usa só esses dois valores; os nomes das variáveis ficam para o `plan` (FR-006, FR-011, FR-025).
- Q: Como fica o cancelamento de job em andamento e a recuperação de job órfão? → A: A API só marca o job como cancelado (ou falho, no caso de órfão) e ignora o resultado que o worker ainda publicar; o worker não é interrompido. Mensagem de cancelamento na fila fica para uma nova versão do esquema, fora desta entrega (FR-018; fonte: `docs/specs/conversion-queue/spec.md`, FR-021).
- Q: Quais metas, origens e riscos o dono do produto decidiu antes de `execute-task`? → A: Decisões do operador em block-001/dec-023 (2026-11-25): meta SSE de 2 s em 95% e todos os limites [PROPOSTA] aceitos; origem de produção `tauri://localhost` (dev `http://localhost:1420` só por variável explícita); risco de publicador local forjar `result`/`error` aceito (premissa P-02 da `conversion-queue`); ausência de limitação de taxa aceita (monousuário local); runtime Docker Desktop 29.9.2 no macOS (`desktop-linux`) com recusa clara se o usuário `10001` não acessar o bind mount; `uv lock` só adiciona `pika` ao grupo `api`; capacidade do consumidor só medida no cenário 4 (sem limiar; premissa P-07 da `conversion-queue`); diretórios de jobs substituídos mantidos até a exclusão do projeto.
- Q: O que a API entrega além do Markdown e do zip de exportação (assets)? → A: Nada além dos `.md` e do zip de Markdown; imagens e outros assets ficam para uma feature futura que também altere o worker (FR-021).

## User Scenarios & Testing

### User Story 1 - Gerenciar projetos e registrar o que processar (Priority: P1)

O app desktop precisa de um lugar único para criar projetos, renomeá-los, arquivá-los e excluí-los, e para registrar neles as fontes (pastas ou arquivos indicados no lugar, ou copiados para o projeto) e os documentos de cada fonte. A API roda em container e **não enxerga o disco do host**: para uma pasta indicada no lugar, quem lista os PDFs é o app desktop, e a API recebe essa lista pronta.

**Why this priority**: sem projetos, fontes e documentos registrados não existe nada para enfileirar; é a base de todas as outras histórias e já entrega valor sozinha (o usuário organiza seu trabalho e vê o status de cada arquivo).

**Independent Test**: Com a stack no ar, criar um projeto, registrar uma fonte do tipo pasta indicada no lugar com uma lista de caminhos de PDF enviada pelo cliente, listar os documentos do projeto com o status de cada um, renomear, arquivar, desarquivar e excluir o projeto, conferindo as respostas e o que ficou no banco.

**Acceptance Scenarios**:

1. **Given** a API no ar, **When** o cliente cria um projeto com um nome, **Then** o projeto passa a existir como ativo, com identificador, nome e datas, e aparece na listagem de projetos (cobre FR-001)
2. **Given** um projeto ativo com o nome "Estudos", **When** o cliente cria outro projeto ativo com o mesmo nome (ignorando maiúsculas e minúsculas conforme a regra do job-store, NFC + `casefold`; uma letra com acento distinta da sem acento **não** colide), **Then** a criação é recusada com mensagem em português e nada é gravado (cobre FR-001)
3. **Given** um projeto existente, **When** o cliente o renomeia para um nome livre, **Then** a listagem mostra o novo nome e a data de atualização muda; se o nome já estiver em uso por outro projeto ativo, a operação é recusada com mensagem em português (cobre FR-002)
4. **Given** um projeto ativo, **When** o cliente o arquiva, **Then** ele sai da listagem padrão, aparece quando se pede também os arquivados, os jobs não finais dele viram "cancelado" e o nome fica livre; **When** o cliente o desarquiva e o nome está em uso por um projeto ativo, **Then** a operação é recusada sem renomear (cobre FR-003)
5. **Given** um projeto com fontes, documentos, jobs e histórico, **When** o cliente o exclui, **Then** tudo o que pertence a ele deixa de existir e nada de outros projetos é afetado (cobre FR-004)
6. **Given** um projeto, **When** o cliente registra uma fonte do tipo pasta, indicada no lugar, junto com a lista de caminhos de PDF dessa pasta obtida pelo app, **Then** a fonte e um documento por caminho são gravados, e listar os documentos devolve cada um com o status "ainda não processado" (cobre FR-005, FR-007)
7. **Given** a mesma pasta registrada de novo com uma lista que repete alguns caminhos e traz outros novos, **When** o cliente a envia, **Then** só os caminhos novos criam documentos e os repetidos mantêm o documento e o histórico que já tinham (cobre FR-007)
8. **Given** um caminho que não é absoluto, ou vazio, ou que não termina em `.pdf`, **When** o cliente tenta registrá-lo como documento, **Then** o registro é recusado com mensagem em português que cita o caminho, sem afetar os demais caminhos válidos da mesma lista (cobre FR-008)
9. **Given** um projeto com documentos já processados e outros não, **When** o cliente lista os documentos do projeto, **Then** cada item traz o caminho, o status (um de "ainda não processado", pendente, em andamento, concluído, falho, cancelado) e o resumo do último resultado quando houver (cobre FR-009)

---

### User Story 2 - Enfileirar documentos e gravar o resultado das conversões (Priority: P1)

O usuário pede para converter um ou mais documentos (ou todos os ainda não processados de um projeto). A API cria um job para cada documento, publica a mensagem do job na fila da stack no formato do contrato da fila, e passa a **consumir os eventos** que os workers nativos publicam (progresso, resultado, erro), gravando o histórico, as tentativas por motor e o resultado no banco por meio da camada do job-store. Os workers não acessam o banco; esta API é quem grava.

**Why this priority**: é o coração da versão desktop (processar pastas em paralelo e guardar histórico e resultados); sem isto o usuário não obtém nenhum Markdown.

**Independent Test**: Com a stack no ar e um worker rodando no host, registrar um documento, pedir a conversão e conferir que o job passa por pendente, em andamento e concluído, que as tentativas e a nota de qualidade ficam gravadas e que o caminho do Markdown fica associado ao documento; repetir com um PDF inválido e conferir o job falho com o motivo em português.

**Acceptance Scenarios**:

1. **Given** um documento sem job ativo, **When** o cliente pede a conversão com o motor automático, **Then** um job pendente é registrado, uma mensagem de job com `schema_version` 1 e o caminho absoluto do PDF no host é publicada na fila de jobs, e a resposta devolve o identificador do job (cobre FR-010, FR-011)
2. **Given** vários documentos de um projeto, **When** o cliente pede a conversão de todos os não processados, **Then** um job é criado e publicado por documento, os documentos que já têm job pendente ou em andamento são ignorados com aviso na resposta, e a resposta informa quantos foram enfileirados e quantos ignorados (cobre FR-010, FR-012)
3. **Given** um documento com job pendente ou em andamento, **When** o cliente pede de novo a conversão do mesmo documento, **Then** o pedido é recusado com mensagem em português e nenhuma segunda mensagem é publicada (cobre FR-012)
4. **Given** o primeiro evento de um job vindo do worker, **When** a API o consome, **Then** o job passa a em andamento com o instante de início, e cada evento de progresso fica disponível para o acompanhamento ao vivo (cobre FR-013)
5. **Given** um evento de resultado válido, **When** a API o consome, **Then** cada tentativa do relatório é gravada com motor, duração, nota e avisos, o job fica concluído com o caminho do Markdown, o motor vencedor e a qualidade, e o histórico do documento registra a conclusão, tudo numa operação que não deixa gravação pela metade (cobre FR-014)
6. **Given** um evento de erro marcado como passível de nova tentativa, **When** a API o consome, **Then** o job continua em andamento e o aviso da falha passageira fica visível no acompanhamento; **Given** um evento de erro definitivo, **Then** o job fica falho com o motivo em português (cobre FR-015)
7. **Given** o mesmo evento entregue duas vezes (mesmo job e mesma tentativa), **When** a API o consome, **Then** o histórico e o resultado não são duplicados e nenhum erro é registrado como falha da API (cobre FR-016)
8. **Given** um evento que não obedece ao contrato (JSON inválido, tipo desconhecido, campos fora do formato, versão de esquema não suportada) ou que cita um job inexistente, **When** a API o consome, **Then** ele é descartado e registrado em log em português sem derrubar o consumo, e os demais eventos continuam sendo tratados (cobre FR-016)
9. **Given** a API parada enquanto workers publicaram eventos, **When** ela volta, **Then** os eventos acumulados são consumidos e o estado dos jobs e dos resultados converge para o que os workers fizeram, sem intervenção manual (cobre FR-017)
10. **Given** a publicação da mensagem na fila falhar (broker de mensagens fora do ar), **When** o cliente pede a conversão, **Then** o pedido responde com mensagem em português, e o job criado para ele não fica pendente para sempre: termina falho com o motivo registrado, e o documento pode ser enfileirado de novo (cobre FR-011)

---

### User Story 3 - Acompanhar o progresso ao vivo (Priority: P2)

O app desktop mostra, para os jobs de um projeto, as mensagens de progresso e as mudanças de estado conforme acontecem, sem o usuário recarregar nada. A API oferece esse fluxo de eventos ao vivo (SSE, como pedido na descrição da feature).

**Why this priority**: é o que dá visibilidade ao processamento paralelo na tela de processamento; a conversão em si já funciona sem ele (consulta por status), por isso P2.

**Independent Test**: Abrir o fluxo ao vivo de um projeto, enfileirar dois documentos e conferir que chegam as mensagens de progresso em português, a mudança para em andamento e o estado final de cada job, na ordem em que ocorreram para cada job; desconectar e reconectar e conferir que o estado atual pode ser lido pela consulta normal.

**Acceptance Scenarios**:

1. **Given** um cliente conectado ao fluxo ao vivo de um projeto, **When** um worker publica progresso de um job desse projeto, **Then** o cliente recebe o evento com o identificador do job, a tentativa e o texto em português, em até 2 s [meta aceita pelo operador, block-001/dec-023] (cobre FR-019)
2. **Given** um cliente conectado ao fluxo de um projeto, **When** ocorrem eventos de jobs de outro projeto, **Then** o cliente não os recebe (cobre FR-019)
3. **Given** um job que muda de estado (em andamento, concluído, falho, cancelado), **When** a mudança é gravada, **Then** o cliente conectado recebe o novo estado do job (cobre FR-019)
4. **Given** um cliente que perdeu a conexão, **When** ele reconecta, **Then** o fluxo volta a funcionar sem erro, e o estado atual de todos os documentos continua disponível pela consulta de status para o cliente se atualizar (cobre FR-020)
5. **Given** vários clientes conectados ao mesmo tempo, **When** um deles fecha a conexão, **Then** os demais seguem recebendo eventos e nenhum recurso do cliente que saiu fica retido (cobre FR-020)

---

### User Story 4 - Consultar o resultado e o histórico, e obter o Markdown (Priority: P2)

Para cada documento, o usuário vê o status, as tentativas (motor, duração, nota de qualidade, avisos), o resultado vigente e o histórico de eventos, e abre o Markdown no visualizador do app. A API entrega o conteúdo do Markdown e os arquivos de apoio (assets) que ele referenciar.

**Why this priority**: sem isto o resultado fica trancado no volume; ainda assim é consequência da US2, por isso P2.

**Independent Test**: Para um documento concluído, ler o status, as tentativas, o resultado e o histórico pela API e baixar o Markdown, conferindo que o conteúdo é byte a byte o arquivo gravado pelo worker; pedir o Markdown de um documento sem resultado e de um caminho fora do diretório de dados e conferir as recusas.

**Acceptance Scenarios**:

1. **Given** um documento com job concluído, **When** o cliente consulta o resultado, **Then** a API devolve o motor, a nota de qualidade, se foi aceito, a contagem de tabelas encontradas e esperadas, os avisos e o instante da gravação, todos com os nomes e tipos que o job-store já define (cobre FR-022)
2. **Given** um documento com mais de uma tentativa de motor, **When** o cliente consulta as tentativas, **Then** elas vêm na ordem em que ocorreram, cada uma com motor, duração, qualidade ou erro (cobre FR-022)
3. **Given** um projeto com atividade, **When** o cliente consulta o histórico, **Then** recebe os eventos de histórico em ordem cronológica, filtrados por projeto ou por documento, com paginação por limite e deslocamento (cobre FR-023)
4. **Given** um documento com resultado, **When** o cliente pede o Markdown, **Then** recebe o conteúdo exato do arquivo gravado pelo worker (cobre FR-024)
5. **Given** um documento sem resultado (nunca processado, pendente, em andamento ou falho), **When** o cliente pede o Markdown, **Then** a resposta é uma recusa em português que informa que não há resultado, sem acessar o disco (cobre FR-024)
6. **Given** um evento de resultado cujo caminho do Markdown não está dentro do diretório de dados montado (por exemplo, com `..`, ou apontando para outro lugar), **When** a API o consome, **Then** o caminho não é aceito como resultado, o job passa a `failed` com motivo em português, o fato é registrado em português e o arquivo nunca é lido nem entregue (cobre FR-024, FR-025)
7. **Given** um projeto com vários documentos concluídos, **When** o cliente pede a exportação dos Markdown do projeto, **Then** recebe um único arquivo compactado com um Markdown por documento concluído, com nomes sem colisão (cobre FR-021)

---

### User Story 5 - Reprocessar com outro motor e cancelar (Priority: P3)

Quando o resultado de um documento sai ruim, o usuário pede para reprocessar escolhendo outro motor entre os que o roteador aceita (`auto`, `docling`, `pymupdf`, `mineru`, `markitdown`; fonte: `src/artigo2md/router.py`). Também pode cancelar o que ainda não terminou. O reprocessamento cria um job novo; o histórico anterior permanece.

**Why this priority**: melhora a qualidade e o controle do usuário, mas depende de US2 e US4 estarem entregues.

**Independent Test**: Para um documento concluído, pedir o reprocessamento com um motor diferente, conferir que um novo job é criado e executado, que o resultado anterior continua no histórico e que o resultado vigente passa a ser o do novo job; cancelar um job pendente e conferir o estado cancelado.

**Acceptance Scenarios**:

1. **Given** um documento com job concluído, **When** o cliente pede o reprocessamento com o motor `mineru`, **Then** um job novo é criado e publicado com `engine` igual a `mineru` e `attempt` igual a 1, e o job anterior e seu resultado permanecem consultáveis (cobre FR-026)
2. **Given** um pedido de reprocessamento com motor fora da lista do roteador, **When** a API o valida, **Then** ele é recusado com mensagem em português que lista os motores válidos, sem criar job (cobre FR-026)
3. **Given** um documento com job pendente ou em andamento, **When** o cliente pede o reprocessamento, **Then** o pedido é recusado, pela mesma regra de um job ativo por documento (cobre FR-012, FR-026)
4. **Given** um job pendente, **When** o cliente o cancela, **Then** o job fica "cancelado", o histórico registra o cancelamento e qualquer evento do worker que chegue depois para esse job é ignorado sem erro (cobre FR-018)
5. **Given** um job já em estado final, **When** o cliente tenta cancelá-lo, **Then** a operação é recusada com mensagem em português (cobre FR-018)

---

### User Story 6 - Operar só no computador do usuário, com segurança (Priority: P2)

A API roda em container na máquina do usuário, em loopback, sem login (o app é monousuário e local; fonte: `docs/briefing.md`). Ela precisa ser inofensiva para o resto da máquina e da rede: não enxerga o disco do host além do diretório de dados do app, não carrega motor de conversão e não pode ser usada por páginas web ou outras máquinas.

**Why this priority**: são obrigações da Constitution (princípios I e II) e da descrição da feature; sem elas a feature não pode ser entregue, embora não tenha uma jornada de uso própria.

**Independent Test**: Subir a stack, conferir que a API só responde em `127.0.0.1`, que recusa `Host` e origens estranhas, que o container só tem o diretório de dados montado do host e que importar a aplicação não carrega nenhum motor de conversão; rodar a bateria de caminhos maliciosos e conferir as recusas.

**Acceptance Scenarios**:

1. **Given** a stack no ar, **When** se tenta conectar à API por qualquer endereço que não seja `127.0.0.1` da própria máquina, **Then** a conexão não é possível (cobre FR-027)
2. **Given** um pedido com `Host` diferente de `127.0.0.1` ou `localhost`, **When** a API o recebe, **Then** ele é recusado como na API base (cobre FR-027)
3. **Given** uma página web de origem não autorizada no navegador do usuário, **When** ela tenta chamar a API ou abrir o fluxo ao vivo, **Then** o navegador não consegue ler a resposta e a API recusa operações que alteram dados vindas dessa origem (cobre FR-028)
4. **Given** a aplicação importada em processo novo, **When** se inspecionam os módulos carregados, **Then** nem `docling` nem `pymupdf4llm` estão carregados (cobre FR-029)
5. **Given** o container da API em execução, **When** se inspecionam os volumes, **Then** o único acesso ao disco do host é o diretório de dados do app, e nenhum outro caminho do host está montado (cobre FR-030)
6. **Given** um valor de caminho recebido do cliente ou da fila com `..`, caractere nulo, link simbólico ou caminho absoluto fora do diretório de dados, **When** a API precisa ler um arquivo dele, **Then** a leitura é recusada e nada fora do diretório de dados é lido (cobre FR-025)
7. **Given** qualquer falha (banco, fila, validação), **When** a API responde, **Then** a mensagem está em português, sem senha, credencial nem detalhe interno do servidor (cobre FR-031)
8. **Given** o banco ou o broker de mensagens indisponíveis, **When** o cliente chama operações que dependem deles, **Then** recebe mensagem em português indicando como subir a infraestrutura, e a API volta a funcionar sozinha quando eles retornam, sem precisar ser reiniciada (cobre FR-032)

---

### Edge Cases

- **Cliente envia caminhos de um host diferente do que o worker enxerga**: a API não verifica o disco; o caminho é gravado como informado (como o job-store) e, se o PDF não existir na hora da conversão, o worker devolve erro definitivo de PDF não encontrado, que a API registra como job falho com o motivo em português (cobre FR-008, FR-015).
- **Fonte "copiada para o projeto"**: o arquivo precisa estar no diretório de dados para o worker lê-lo; como ele chega lá e como a API descobre o caminho dele no host foi resolvido nas clarificações Q1/Q2 (FR-006): o app desktop copia o arquivo para a pasta do host que o container monta e a API conhece o par (caminho do host, ponto de montagem) por `ARTIGO2MD_DATA_DIR_HOST`/`ARTIGO2MD_DATA_DIR_MOUNT`.
- **Pasta com muitos arquivos**: a lista enviada pelo app pode ter até 1.000 documentos (referência do job-store); a listagem de documentos do projeto responde numa única consulta nesse volume. Acima disso funciona sem meta de tempo.
- **Arquivos removidos da pasta depois do registro**: o documento e seu histórico permanecem; a API não verifica o disco, e o novo job falhará com PDF não encontrado se o arquivo sumiu.
- **Evento de job já final (cancelado, falho ou concluído)**: a gravação tardia é recusada pelo job-store; a API trata como notificação repetida e não registra erro (cobre FR-016).
- **Evento de job de projeto arquivado ou excluído**: ignorado com log, sem derrubar o consumo (cobre FR-016).
- **API reiniciada com job "em andamento" sem worker vivo**: o job-store não expira jobs; a API oferece ao cliente uma forma de falhar ou cancelar o job órfão e só então criar outro (cobre FR-018, FR-033).
- **Mensagens mortas**: jobs que esgotaram as tentativas chegam à API como evento de erro definitivo; a API não lê nem reenvia a fila de mensagens mortas por conta própria (fora do escopo).
- **Dois clientes pedem a conversão do mesmo documento ao mesmo tempo**: um recebe o job, o outro recebe a recusa de job ativo; nunca há dois jobs ativos do documento (cobre FR-012).
- **Nome de arquivo repetido em pastas diferentes na exportação em zip**: nomes sem colisão na exportação (cobre FR-021).
- **Exclusão de projeto com jobs em andamento**: os jobs não finais viram "cancelado" na mesma operação e os eventos tardios deles são ignorados; os Markdown gerados do projeto e os arquivos que o projeto copiou ficam sujeitos a FR-004 (cobre FR-004, FR-016).
- **Corpo de pedido grande demais ou campo com tipo errado**: recusa com mensagem em português, sem gravação parcial (cobre FR-031).

## Requirements

### Functional Requirements

**Projetos, fontes e documentos**

- **FR-001**: O sistema MUST permitir criar projeto com um nome e listar projetos (somente ativos por padrão, com opção de incluir os arquivados), devolvendo identificador, nome, estado e datas; o nome só pode repetir entre projetos se um deles estiver arquivado, pela mesma regra de unicidade do job-store.
- **FR-002**: O sistema MUST permitir renomear um projeto, recusando nome vazio ou em uso por outro projeto ativo. A camada do job-store hoje não tem a operação de renomear (fonte: `docs/specs/job-store/contracts/repository.md`, tabela de operações); esta feature MUST acrescentá-la à camada, com a mesma regra de nome.
- **FR-003**: O sistema MUST permitir arquivar e desarquivar projeto, com os efeitos já definidos no job-store: arquivar cancela os jobs não finais na mesma operação e libera o nome; desarquivar é recusado se o nome estiver em uso por projeto ativo.
- **FR-004**: O sistema MUST permitir excluir projeto, removendo os registros que dependem dele (fontes, documentos, jobs, tentativas, resultados e histórico) sem afetar outros projetos, e MUST remover do diretório de dados os Markdown gerados e os arquivos copiados que pertençam só a esse projeto. A exclusão MUST NOT tocar em arquivos de fontes indicadas no lugar, que a API nem enxerga. Os diretórios `<data_dir>/<job_id>/` de jobs substituídos (reprocessamento, tentativas falhas) MUST ser mantidos e removidos somente junto com a exclusão do projeto (decisão do operador, block-001/dec-023). Os diretórios `<data_dir>/<job_id>/` de jobs substituídos (reprocessamento, tentativas falhas) MUST ser mantidos e removidos somente junto com a exclusão do projeto (decisão do operador, block-001/dec-023).
- **FR-005**: O sistema MUST permitir registrar em um projeto uma fonte do tipo pasta ou arquivo, no modo indicada no lugar ou copiada para o projeto, aceitando só os valores definidos no job-store (`folder` ou `file`; `in_place` ou `copied`).
- **FR-006**: O sistema MUST suportar fontes no modo copiada para o projeto, com o arquivo copiado dentro do diretório de dados do app. Quem copia o arquivo é o app desktop, direto para a pasta do host que o container monta; a API MUST NOT receber nem gravar o conteúdo do PDF, e apenas registra o caminho informado pelo app (validado conforme FR-008 e confinado conforme FR-025). A API conhece o caminho do host equivalente ao diretório de dados montado por configuração fixa da stack (o par caminho do host e ponto de montagem no container, vindo de variável de ambiente cujos nomes o `plan` define); o `pdf_path` do job de uma fonte copiada MUST ser derivado desse par pela mesma regra única de FR-025.
- **FR-007**: O sistema MUST receber do cliente a lista de caminhos de PDF de uma fonte do tipo pasta e registrar um documento por caminho, de forma idempotente por (projeto, caminho): repetir o envio não duplica documentos nem apaga o histórico. A API MUST NOT listar, ler nem verificar pastas ou arquivos do host.
- **FR-008**: O sistema MUST validar cada caminho de documento antes de gravá-lo: não vazio, absoluto, sem caractere nulo e com extensão `.pdf`; um caminho inválido MUST ser recusado com mensagem em português que o cita, sem impedir o registro dos caminhos válidos da mesma lista. O limite de tamanho do caminho é **1024 bytes em UTF-8** (`PC_PATH_MAX`), o maior caminho legítimo que o contrato da fila prevê para `pdf_path`; a mensagem de job inteira tem no máximo 16384 bytes (fonte de ambos: `queue-messages.md`, "Limites de entrada").
- **FR-009**: O sistema MUST permitir listar os documentos de um projeto com o status de cada um (ainda não processado, pendente, em andamento, concluído, falho ou cancelado) e o resumo do último resultado, com os campos que o job-store já define para essa listagem.

**Enfileiramento e consumo de eventos**

- **FR-010**: O sistema MUST permitir enfileirar a conversão de um documento, de uma lista de documentos ou de todos os documentos ainda não processados de um projeto, escolhendo o motor entre os do roteador (padrão `auto`). Limites aceitos pelo operador (block-001/dec-023): corpo de pedido até 16 MiB e até 10 000 caminhos ou ids por pedido (acima: 413, sem gravação parcial); o valor de 10 000 cobre a referência de 1.000 documentos por pasta.
- **FR-011**: (prazo de confirmação da publicação: 10 s por documento e truncamento de 2000 caracteres nos textos livres gravados dos eventos, aceitos pelo operador, block-001/dec-023) Para cada documento enfileirado, o sistema MUST registrar o job no banco e publicar a mensagem de job exatamente no formato do contrato da fila (`schema_version` 1; campos `schema_version`, `type`, `job_id`, `pdf_path`, `engine`, `attempt`; nenhum campo a mais; fonte: `queue-messages.md`), com o caminho absoluto do PDF no host. Se a publicação não for confirmada, o job criado MUST terminar falho, com o motivo registrado, e nunca ficar pendente sem mensagem na fila.
- **FR-012**: O sistema MUST recusar, com mensagem em português, enfileirar documento que já tenha job pendente ou em andamento; em pedidos em lote, esses documentos MUST ser ignorados e a resposta MUST informar quantos foram enfileirados e quantos ignorados.
- **FR-013**: O sistema MUST consumir continuamente os eventos de progresso, resultado e erro que os workers publicam e MUST atribuí-los ao job pelo identificador do job; o primeiro evento recebido de um job pendente MUST movê-lo para em andamento.
- **FR-014**: Ao consumir um evento de resultado válido, o sistema MUST gravar, por meio do job-store, cada tentativa do relatório (motor, duração, qualidade ou erro), o resultado (caminho do Markdown, motor vencedor e qualidade, com os avisos) e a conclusão do job, de forma atômica quanto ao estado do job.
- **FR-015**: Ao consumir um evento de erro, o sistema MUST manter o job em andamento quando o evento indicar nova tentativa e MUST terminá-lo como falho, com o motivo em português do evento, quando o evento for definitivo.
- **FR-016**: O consumo de eventos MUST ser idempotente por (job, tentativa) e tolerante: evento repetido, evento de job já final, de job inexistente, de projeto arquivado ou excluído, ou fora do contrato (inclusive `schema_version` não suportada) MUST ser descartado com registro em português, sem derrubar o consumo nem bloquear os eventos seguintes, e campos desconhecidos em eventos válidos MUST ser ignorados.
- **FR-017**: O sistema MUST sobreviver a reinícios e quedas do broker de mensagens e do banco: eventos acumulados enquanto a API estava parada MUST ser consumidos quando ela volta, e o consumidor MUST reconectar sozinho, sem reinício manual.
- **FR-018**: O sistema MUST permitir cancelar um job pendente ou em andamento, registrando o cancelamento no histórico, e MUST recusar o cancelamento de job em estado final. Como o contrato da fila v1 não tem mensagem de cancelamento e o worker não é interrompido (fonte: `queue-messages.md`, "Campos comuns"), cancelar um job em andamento apenas o marca como cancelado e o resultado que o worker ainda publicar MUST ser ignorado conforme FR-016. Para job em andamento sem worker vivo (órfão), o sistema MUST oferecer uma forma explícita de falhá-lo ou cancelá-lo antes de criar outro job do mesmo documento. Uma mensagem de cancelamento na fila fica fora desta entrega (nova versão do esquema; fonte: `docs/specs/conversion-queue/spec.md`, FR-021).

**Progresso ao vivo**

- **FR-019**: O sistema MUST oferecer um fluxo de eventos ao vivo por projeto (SSE) que entregue, para os jobs do projeto, as mensagens de progresso e as mudanças de estado, cada uma com identificador do job, tentativa e texto em português, sem entregar eventos de outros projetos.
- **FR-020**: Limites aceitos pelo operador (block-001/dec-023): até 32 assinantes SSE simultâneos (acima: 503), fila interna de 1000 eventos por assinante (cheia: conexão encerrada) e comentário de keep-alive a cada 15 s. O fluxo ao vivo MUST tolerar desconexão e reconexão do cliente, MUST suportar vários clientes simultâneos e MUST liberar os recursos de um cliente que sai; o estado atual dos documentos MUST continuar obtível pela consulta de status, para o cliente se atualizar ao reconectar.

**Entrega de Markdown, assets e histórico**

- **FR-021**: O sistema MUST permitir exportar, em um único arquivo compactado, os Markdown dos documentos concluídos de um projeto, sem colisão de nomes. Limite aceito pelo operador (block-001/dec-023): soma de Markdown exportada até 512 MiB (acima: 413); projeto sem documento concluído → recusa 404 `no_result`, sem zip vazio. A API MUST entregar apenas os arquivos `.md` e esse zip de Markdown: o worker grava só `<data_dir>/<job_id>/<stem>.md` e o relatório da conversão não lista arquivos de apoio (fonte: `src/artigo2md/jobqueue/output.py`, `contract.py`); imagens e outros assets ficam fora desta entrega, para uma feature futura que também altere o worker.
- **FR-022**: O sistema MUST permitir consultar, por documento, o resultado vigente (motor, nota, aceito, tabelas encontradas e esperadas, avisos, instante) e as tentativas por motor em ordem, com os campos e tipos que o job-store já define.
- **FR-023**: O sistema MUST permitir consultar o histórico de eventos de um projeto ou de um documento, em ordem cronológica, com paginação por limite e deslocamento.
- **FR-024**: O sistema MUST entregar o conteúdo do Markdown do resultado vigente de um documento, e MUST recusar com mensagem em português o pedido de documento sem resultado.
- **FR-025**: O sistema MUST só ler arquivos dentro do diretório de dados montado: todo caminho que chega pela fila ou pelo cliente MUST ser confinado a esse diretório (sem `..`, sem caractere nulo, sem link simbólico que escape, sem caminho absoluto fora dele) antes de qualquer leitura, e o caminho do host MUST ser convertido para o caminho dentro do container por uma regra única e testável.

**Segurança, isolamento e operação**

- **FR-026**: O sistema MUST permitir reprocessar um documento com outro motor da lista do roteador (`auto`, `docling`, `pymupdf`, `mineru`, `markitdown`), criando um job novo, preservando o histórico e os resultados anteriores, e recusando motor fora da lista com mensagem que lista os válidos. O reprocessamento MUST obedecer à regra de um job ativo por documento (FR-012).
- **FR-027**: O sistema MUST aceitar conexões só em `127.0.0.1` do host e MUST manter a recusa de `Host` diferente de `127.0.0.1` ou `localhost` já existente na API base.
- **FR-028**: O sistema MUST proteger a API contra uso por páginas web de origem não autorizada no navegador do usuário: só a origem do app desktop pode ler respostas ou executar operações que alteram dados. A origem de produção é `tauri://localhost` (confirmada pelo operador, block-001/dec-023; fonte citada pelo operador: tauri 2.12.7 (versão do `Cargo.lock` da `desktop-shell`), `src/manager/mod.rs`, `get_app_url` e o teste com `cfg custom_protocol`, que no macOS retorna `tauri://localhost` quando `frontendDist` é caminho; `desktop/src-tauri/tauri.conf.json` da `desktop-shell` usa `frontendDist ../dist` e `devUrl http://localhost:1420`). A origem de desenvolvimento `http://localhost:1420` MUST ficar desligada por padrão e só ser liberada por variável de ambiente explícita [PROPOSTA — nome em `contracts/configuration.md`].
- **FR-029**: O sistema MUST NOT importar `docling`, `pymupdf4llm` nem outro motor de conversão em nenhum caminho da aplicação, verificável pelo conteúdo de `sys.modules` num processo novo.
- **FR-030**: O container da API MUST receber do host só o diretório de dados do app; o restante da configuração de segurança do container da API base (usuário sem privilégio, sistema de arquivos somente leitura, sem capacidades extras) MUST ser mantido. Se esse usuário não conseguir ler ou remover arquivos gravados pelo worker no diretório montado, a API MUST recusar a operação com mensagem clara em português, sem executar o container como root e sem alterar permissões automaticamente (decisão do operador, block-001/dec-023). Se esse usuário não conseguir ler ou remover arquivos gravados pelo worker no diretório montado, a API MUST recusar a operação com mensagem clara em português, sem executar o container como root e sem alterar permissões automaticamente (decisão do operador, block-001/dec-023).
- **FR-031**: Toda mensagem de erro devolvida ao cliente ou gravada em log MUST estar em português e MUST NOT conter senha, credencial, texto bruto de erro do driver nem detalhe interno do servidor; erros de validação MUST ser claros e não gravar nada parcialmente, **exceto** o registro por lista de FR-007/FR-008, em que os caminhos válidos são gravados e os inválidos voltam em `rejected` com mensagem (resposta `created`/`existing`/`rejected`).
- **FR-032**: Com o banco ou o broker de mensagens indisponíveis, a API MUST continuar viva (o `/health` continua respondendo como hoje), MUST responder às operações que dependem deles com mensagem em português que indica como subir a infraestrutura, e MUST voltar a operar sozinha quando eles retornam.
- **FR-033**: O sistema MUST manter o `GET /health` com o mesmo caminho, campos e significado do contrato da API base, e MUST publicar neste repositório o contrato HTTP completo desta API (rotas, parâmetros, corpos, status, erros e a estratégia de versionamento, incluindo um eventual prefixo de versão das rotas de negócio), com a origem de cada campo no código existente, para que `desktop-projects` e `desktop-processing` usem só o que está publicado. A documentação interativa automática continua desligada.
- **FR-034**: O sistema MUST ter testes automatizados que passem em `uv run pytest` sem Docker, usando fakes para o banco e a fila, e testes de integração com a stack real habilitados por variável de ambiente (Princípio IV), incluindo um ciclo completo com um PDF real convertido por um worker.

> Decisões de infraestrutura: scheduling, rotação de chaves, refresh de token externo, mutex multi-pod e backup N/A nesta feature (instância única local, sem login nem sessão; a persistência e o backup do banco pertencem a `job-store`/`container-stack`). Idempotência é tratada em FR-007 (registro de documentos), FR-012 (um job ativo por documento) e FR-016 (eventos por job e tentativa).

### Key Entities

- **Projeto**: agrupa fontes, documentos e jobs; tem nome único entre os ativos, estado ativo ou arquivado e datas (fonte: `src/artigo2md/store/models.py`, `Project`).
- **Fonte**: pasta ou arquivo registrado em um projeto, no modo indicada no lugar ou copiada (`Source`).
- **Documento**: um PDF de uma fonte, identificado pelo caminho dentro do projeto; seu status é derivado do job mais recente (`Document`, `DocumentStatus`).
- **Job**: um pedido de conversão de um documento com um motor, com os estados pendente, em andamento, concluído, falho e cancelado (`Job`).
- **Tentativa**: uma passagem de um motor sobre o documento, com duração, qualidade ou erro (`Attempt`).
- **Resultado**: o Markdown final de um job concluído, com motor, qualidade e avisos (`Result`, `QualityRecord`).
- **Evento de histórico**: registro cronológico do que aconteceu com projetos, documentos e jobs (`HistoryEvent`).
- **Mensagem de fila**: job, progresso, resultado ou erro, trocados com os workers no contrato `schema_version` 1 (`contract.py`).
- **Diretório de dados do app**: pasta do host montada no container, onde os workers gravam o Markdown e, nas fontes copiadas, ficam os arquivos do projeto (`output.py`).

## Success Criteria

### Measurable Outcomes

- **SC-001**: Com a stack e um worker no ar, o usuário vai de um projeto vazio a um Markdown aberto sem nenhuma intervenção manual fora do app, nos 100% dos casos de um ciclo completo com um PDF de amostra do repositório (teste de integração com a stack real).
- **SC-002**: Em 100% dos testes de erro (validação, regra de negócio, banco ou fila fora do ar), a mensagem devolvida está em português e não contém senha, credencial nem texto bruto de driver.
- **SC-003**: Em 100% dos testes de caminhos maliciosos (`..`, nulo, link simbólico, caminho fora do diretório de dados), nenhum arquivo fora do diretório de dados é lido ou entregue.
- **SC-004**: Um evento de progresso publicado por um worker chega ao cliente conectado ao fluxo ao vivo em até 2 s em 95% das medições [meta aceita pelo operador, block-001/dec-023].
- **SC-005**: Reiniciar a API no meio de uma fila de 20 documentos não perde nenhum evento: ao final, 100% dos jobs estão em estado final coerente com o que os workers publicaram, sem resultado, tentativa nem histórico duplicados; uma mensagem de job republicada na partida (entrega at-least-once, plan R-06) é tolerada e o resultado repetido que ela gerar é descartado.
- **SC-006**: Enfileirar de uma vez os 1.000 documentos de um projeto não deixa nenhum job pendente sem mensagem na fila; os jobs cuja publicação falhou terminam falhos com motivo, em 100% dos casos testados com a fila fora do ar.
- **SC-007**: A API só aceita conexão em `127.0.0.1`, recusa `Host` e origens não autorizadas, e nenhum motor de conversão é carregado ao importar a aplicação (verificação automatizada).
- **SC-008**: Os desenvolvedores de `desktop-projects` e `desktop-processing` constroem suas telas usando só o contrato HTTP publicado, sem precisar ler o código da API, e o contrato cobre 100% das rotas expostas. Critério observável: cada rota registrada na aplicação consta em `contracts/http-api.md` com método, caminho, parâmetros, corpo, status e erros (teste automatizado compara as rotas da aplicação com as do contrato, tarefa 11.5), e cada campo de resposta tem tipo e origem citados; o desenvolvedor vê a rota sem consultar `src/artigo2md/api/`.

## Delta Requirements

**Skip**: feature acrescenta rotas novas à API base sem alterar comportamento ativo catalogado em `docs/specs/current/` (corpus inexistente neste repositório) — feature-00c, 2026-11-25
