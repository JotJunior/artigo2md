# Feature Specification: Controle da infraestrutura pelo app desktop

**Feature**: `desktop-stack-control`
**Created**: 2026-11-26
**Status**: Draft

> **Origem**: item 5 do roadmap da versão desktop macOS (fonte: `docs/roadmap.md`), com base no briefing 2.0 e na constitution 1.2.0 (fonte: `docs/briefing.md`) (fonte: `docs/constitution.md`). Depende de `container-stack` (compose, scripts de subir e derrubar, parâmetros) e de `desktop-shell` (janela, barra lateral, configurações persistidas). Usa também o worker de `conversion-queue` e a API de `project-api`, que já existem.
> **Efeitos sobre features anteriores**: (1) a barra lateral do `desktop-shell` (FR-002: "Arquivos" e "Configurações") ganha a entrada "Infraestrutura"; (2) o `desktop-shell` FR-015 proibia toda conexão de rede "nesta feature"; esta feature autoriza ao app somente conexões ao endereço de loopback `127.0.0.1`, para verificar os serviços locais, e nenhuma outra (ver FR-037); (3) o `desktop-shell` FR-006 (somente leitura) trata dos arquivos do usuário; os arquivos que esta feature cria (parâmetros e pasta de dados do app) pertencem ao app.
> **Fora de escopo**: instalar, empacotar ou distribuir o runtime Docker (é pré-requisito, fonte: `docs/briefing.md`, seção 5); `.dmg`, empacotamento da stack, do worker e do ambiente `uv` dentro do `.app` e distribuição (feature `desktop-release`; nesta feature a pessoa indica a pasta do repositório, FR-016); projetos, fontes e processamento de documentos (`desktop-projects`, `desktop-processing`); apagar os dados da stack e atualizar imagens de container (ficam nos scripts da `container-stack`, não no app, nesta feature); Windows e Linux.

## Clarifications

### Session 2026-11-27

Respostas do operador aos bloqueios humanos do clarify (feature-00c), registradas na execução como block-001, block-002 e block-003 e aplicadas nesta spec.

- Q: Como o app localiza os arquivos da stack (compose e scripts) e o executável do worker? → A: A pessoa indica a pasta do repositório nas configurações e o app usa o ambiente `uv` local desse repositório; empacotar tudo dentro do `.app` fica para `desktop-release`. (block-001, decisão estrutural, eixo arquitetura; dec-012) Aplicado em FR-016, FR-018, FR-019 e nos cenários de US2 e US3.
- Q: O que acontece com os workers e a stack ao fechar o app? → A: O app para os workers com encerramento calmo (o job não concluído volta à fila) e deixa a stack no ar. (block-002; dec-010) Aplicado em FR-024, US3 cenário 10 e nos casos de borda.
- Q: Quem dispara o download dos modelos do Docling? → A: O próprio app, num passo explícito "Preparar modelos", confirmado antes da primeira conversão; o mecanismo de progresso de download de modelos é novo e entra como requisito desta feature (hoje não existe: fonte `docs/specs/conversion-queue/contracts/worker-cli.md` não define mensagem nem contrato de progresso). (block-003; dec-010) Aplicado em FR-036 e FR-030 a FR-034, US6 e nos casos de borda.

## User Scenarios & Testing

### User Story 1 - Saber se o Docker está pronto ao abrir o app (Priority: P1)

A pessoa abre o app e, sem digitar nada, vê se existe um runtime Docker no Mac (Docker Desktop, OrbStack ou colima), se ele está em execução e se está pronto para subir a infraestrutura. Mesmo sem Docker, o app continua útil para navegar pastas e ler Markdown.

**Why this priority**: Todo o resto da feature depende de saber a situação do runtime; o Docker é pré-requisito do app para usuários que não são técnicos (fonte: `docs/briefing.md`, seções 2 e 5). Sozinha, esta story já entrega valor: troca uma falha obscura por um diagnóstico em português.

**Independent Test**: Abrir o app em três situações (sem nenhum runtime, com um runtime instalado e parado, com um runtime em execução) e conferir o diagnóstico mostrado em cada uma.

**Acceptance Scenarios**:

1. **Given** um Mac sem nenhum dos três runtimes, **When** o app abre, **Then** a tela de infraestrutura informa, em português, que nenhum runtime Docker foi encontrado e oferece o guia de instalação (cobre FR-001, FR-002)
2. **Given** um runtime instalado mas parado, **When** o app abre, **Then** o diagnóstico diz qual runtime foi encontrado, que ele não está em execução e como iniciá-lo (cobre FR-001, FR-002, FR-006)
3. **Given** um runtime em execução com o Docker Compose disponível, **When** o app abre, **Then** o diagnóstico mostra "Docker pronto", qual runtime está em uso, e libera as ações de subir a infraestrutura (cobre FR-001, FR-002)
4. **Given** um runtime em execução sem o subcomando do Docker Compose, **When** o diagnóstico roda, **Then** o app informa que falta o Docker Compose, em português, e não libera o subir (cobre FR-002)
5. **Given** o diagnóstico em andamento ou concluído como "nenhum runtime", **When** a pessoa usa "Arquivos" e "Configurações", **Then** tudo continua funcionando, sem erro e sem espera (cobre FR-003)
6. **Given** o diagnóstico mostrando um problema, **When** a pessoa resolve o problema fora do app e aciona "Verificar novamente", **Then** o diagnóstico é refeito e reflete a nova situação sem reiniciar o app (cobre FR-003)

---

### User Story 2 - Subir e parar a infraestrutura com uma ação (Priority: P1)

Com o Docker pronto, a pessoa aciona "Iniciar infraestrutura" e o app sobe o compose da `container-stack` (Postgres, RabbitMQ e API), aguarda os serviços ficarem saudáveis e mostra o resultado. "Parar infraestrutura" derruba tudo sem perder os dados. Na primeira vez, o app prepara sozinho os parâmetros que a pessoa não técnica não saberia preencher.

**Why this priority**: É o núcleo da feature: esconder a infraestrutura do usuário e cuidar do ciclo de vida da stack (fonte: `docs/roadmap.md`, justificativa do item 5). Sem isto, as features `desktop-projects` e `desktop-processing` não têm backend para usar.

**Independent Test**: Num Mac com runtime pronto e sem arquivo de parâmetros, acionar "Iniciar infraestrutura", conferir que os três serviços ficam saudáveis; acionar "Parar infraestrutura" e conferir que os containers somem e os dados continuam.

**Acceptance Scenarios**:

1. **Given** o Docker pronto e nenhum arquivo de parâmetros da stack, **When** a pessoa aciona "Iniciar infraestrutura" pela primeira vez, **Then** o app cria os parâmetros com credenciais geradas, portas padrão e pasta de dados, sem pedir nenhum valor de parâmetro (credenciais, portas, pasta de dados), e sobe a stack; se as imagens da stack ainda não existem na máquina, o app não chama o `up.sh` nem baixa nada: devolve `imagesMissing` e mostra a lista da primeira execução com a etapa "imagens da infraestrutura", o tamanho aproximado e o pedido de confirmação (US6-1, FR-031), e só sobe a stack depois da confirmação e do download (cobre FR-007, FR-010, FR-012, FR-031)
2. **Given** os parâmetros recém-criados, **When** se inspecionam a tela, as mensagens, os registros do app e a lista de processos, **Then** nenhuma senha aparece em nenhum deles (cobre FR-011)
3. **Given** a stack subindo, **When** o app aguarda, **Then** a tela mostra a etapa atual e o resultado de cada serviço em português, e continua respondendo a cliques (cobre FR-007, FR-015)
4. **Given** a stack no ar, **When** a pessoa aciona "Parar infraestrutura", **Then** os containers do projeto são removidos, os volumes com os dados do Postgres e do RabbitMQ continuam existindo e a tela mostra "Infraestrutura parada" (cobre FR-008)
5. **Given** a stack no ar, **When** a pessoa aciona "Iniciar infraestrutura" de novo, **Then** nada é duplicado nem quebrado e a tela informa que já estava no ar (cobre FR-009)
6. **Given** a stack parada, **When** a pessoa aciona "Parar infraestrutura", **Then** a operação termina sem erro informando que já estava parada (cobre FR-009)
7. **Given** um arquivo de parâmetros já existente, **When** o app inicia a stack, **Then** o arquivo não é sobrescrito nem tem credencial regenerada (cobre FR-010)
8. **Given** uma porta usada por outro programa (por exemplo, a do Postgres), **When** a pessoa aciona "Iniciar infraestrutura", **Then** o app informa em português qual serviço e qual porta estão em conflito, nada do outro programa é alterado, e a pessoa pode trocar a porta nas configurações avançadas e tentar de novo (cobre FR-013, FR-014)
9. **Given** um serviço que não fica saudável no tempo de espera, **When** o subir termina, **Then** o app informa qual serviço falhou e como ver os detalhes técnicos, e não deixa a tela num estado de "iniciando" para sempre (cobre FR-014)
10. **Given** uma operação de subir ou parar em andamento, **When** a pessoa tenta acionar outra operação de ciclo de vida, **Then** o app a recusa com um aviso e a operação em andamento não é afetada (cobre FR-015)
11. **Given** a pasta de dados do app proposta pelo app, **When** a stack e, depois, os workers são iniciados, **Then** os dois usam exatamente o mesmo caminho, mostrado na tela (cobre FR-012)
12. **Given** nenhuma pasta do repositório indicada, ou uma pasta sem os arquivos da stack ou sem o `uv` disponível, **When** a pessoa aciona "Iniciar infraestrutura" ou "Iniciar workers", **Then** o app informa em português o que não encontrou e como resolver, sem executar nada, e "Arquivos", "Configurações" e o diagnóstico do Docker seguem funcionando (cobre FR-016, FR-003)
13. **Given** um caminho de pasta com espaços ou caracteres especiais nos parâmetros, **When** o app inicia a stack e os workers, **Then** os valores são usados exatamente como digitados, sem interpretação de comando (cobre FR-017)

---

### User Story 3 - Iniciar e parar o pool de workers nativos (Priority: P1)

Com a stack no ar, a pessoa aciona "Iniciar workers" e o app inicia, como processo auxiliar nativo do Mac, o pool de workers de conversão que consome a fila do RabbitMQ. "Parar workers" encerra o pool com calma, devolvendo à fila o que não terminou.

**Why this priority**: Sem o worker, os jobs ficam na fila sem serem convertidos (fonte: `README.md`, seção "Ordem de uso"). Os workers rodam nativos para usar a GPU Apple e o OCR do Vision (fonte: `docs/briefing.md`, seção 4), por isso não podem ser parte do compose.

**Independent Test**: Com a stack no ar, iniciar os workers e conferir que o painel mostra o pool ativo; pará-los e conferir que nenhum processo de worker sobra.

**Acceptance Scenarios**:

1. **Given** a stack no ar, **When** a pessoa aciona "Iniciar workers", **Then** o pool inicia, a partir do ambiente `uv` da pasta do repositório indicada, com a pasta de dados do app, o número de workers e o limite de memória das configurações, e o painel mostra o pool em execução (cobre FR-016, FR-018)
2. **Given** a stack parada, **When** a pessoa aciona "Iniciar workers", **Then** o app recusa e orienta a iniciar a infraestrutura antes; e **Given** a stack no ar mas a etapa "modelos de conversão" não concluída, **When** a pessoa aciona "Iniciar workers", **Then** o app recusa com `modelsMissing`, informa em português que os modelos de conversão ainda não foram preparados e leva ao passo "Preparar modelos" (cobre FR-018, FR-036)
3. **Given** as configurações de workers, **When** a pessoa abre "Configurações", **Then** vê o número de workers (padrão 4) e a memória livre mínima em MB (padrão 4822), com valores inválidos recusados e as escolhas mantidas ao reabrir o app; a mesma tela traz a pasta do repositório (cobre FR-016, FR-019)
4. **Given** um número de workers acima do que a RAM do Mac comporta, **When** o pool inicia, **Then** o aviso em português que o próprio worker emite aparece para a pessoa (cobre FR-019, FR-023)
5. **Given** o pool em execução com um job em andamento, **When** a pessoa aciona "Parar workers", **Then** o app encerra o pool com calma, aguarda o prazo de encerramento do worker e, esgotado o prazo, força o encerramento, e ao final nenhum processo de worker nem de conversão filho continua ativo (cobre FR-020)
6. **Given** o pool em execução, **When** a pessoa aciona "Iniciar workers" de novo, **Then** nenhum segundo pool é iniciado e o app informa que já está em execução (cobre FR-021)
7. **Given** o app encerrado à força com o pool ativo, **When** o app abre de novo, **Then** ele detecta o pool deixado pela sessão anterior e oferece parar ou continuar usando, sem iniciar outro (cobre FR-021)
8. **Given** um worker que termina de forma inesperada (por exemplo, credenciais recusadas), **When** o app percebe, **Then** o painel mostra o pool como "parado de forma inesperada" com o motivo traduzido para o português, sem reiniciar sozinho em laço, e oferece "Reiniciar" (cobre FR-022)
9. **Given** o pool em execução, **When** a pessoa abre os detalhes, **Then** vê as últimas mensagens do worker em português, sem nenhuma credencial (cobre FR-023)
10. **Given** a pessoa fechando o app com os workers em execução (e a stack no ar), **When** o app encerra, **Then** o pool é parado com encerramento com calma, a tela mostra "Encerrando workers…" enquanto aguarda, o job não concluído volta à fila, nenhum processo de worker resta e Postgres, RabbitMQ e API continuam no ar (cobre FR-024, FR-020)

---

### User Story 4 - Ser guiado na instalação do Docker, passo a passo (Priority: P2)

A pessoa não técnica que não tem Docker é conduzida por um guia em português, com linguagem simples: escolhe um dos três runtimes com ajuda de uma comparação, segue os passos de instalação e primeira abertura e, ao final, o app reconhece o runtime sozinho.

**Why this priority**: O briefing aponta que a instalação do Docker pode afastar usuários não técnicos se não for bem guiada (fonte: `docs/briefing.md`, seções 2 e 8). Depende da story 1 para reconhecer o fim do guia, mas o guia em si tem valor isolado.

**Independent Test**: Num Mac sem runtime, abrir o guia, percorrer todos os passos de um dos runtimes e conferir que, após a instalação feita fora do app, o app detecta o runtime e encerra o guia.

**Acceptance Scenarios**:

1. **Given** nenhum runtime detectado, **When** a pessoa abre o guia, **Then** ela vê uma comparação em português simples entre Docker Desktop, OrbStack e colima e escolhe um deles (cobre FR-004)
2. **Given** um runtime escolhido, **When** a pessoa avança pelo guia, **Then** cada passo traz uma instrução curta em português e o guia indica quantos passos faltam (cobre FR-004)
3. **Given** um passo que depende de endereço oficial do runtime, **When** ele é exibido, **Then** o endereço aparece como texto que a pessoa pode copiar, e o app não abre o navegador, não baixa o instalador e não executa instalador algum (cobre FR-005)
4. **Given** o guia aberto e o Mac sem internet, **When** a pessoa navega pelos passos, **Then** todo o conteúdo do guia continua disponível, pois está dentro do app (cobre FR-005)
5. **Given** a pessoa no meio do guia, **When** ela fecha o app e o abre de novo, **Then** o guia reabre no passo em que parou (cobre FR-006)
6. **Given** o guia aberto e o runtime instalado e iniciado pela pessoa, **When** o app o detecta, **Then** o guia mostra "Docker pronto" e se encerra, levando à tela de infraestrutura (cobre FR-006, FR-003)
7. **Given** um runtime instalado porém parado, **When** o guia chega ao passo de iniciar, **Then** ele explica como iniciar aquele runtime e refaz a verificação sozinho a cada poucos segundos (cobre FR-006)

---

### User Story 5 - Acompanhar a saúde dos serviços num painel (Priority: P2)

A pessoa abre a entrada "Infraestrutura" na barra lateral e vê, num só lugar, o estado do runtime, do Postgres, do RabbitMQ, da API e do pool de workers, com um indicador resumido sempre visível na barra lateral.

**Why this priority**: Sem visibilidade, uma falha vira "o app não faz nada". O painel dá o diagnóstico em português sem exigir conhecimento de containers; não bloqueia subir nem parar, por isso fica depois das stories P1.

**Independent Test**: Com a stack no ar, conferir o painel todo verde; parar um serviço por fora do app e conferir que o painel mostra o problema em poucos segundos.

**Acceptance Scenarios**:

1. **Given** o app aberto, **When** a pessoa seleciona "Infraestrutura" na barra lateral, **Then** o painel mostra o runtime, o Postgres, o RabbitMQ, a API e o pool de workers, cada um com um estado em português (cobre FR-025, FR-026)
2. **Given** qualquer tela do app, **When** a pessoa olha a barra lateral, **Then** um indicador resumido mostra se a infraestrutura está toda saudável, parcial ou parada (cobre FR-025)
3. **Given** a stack parada, **When** o painel é exibido, **Then** cada serviço aparece como "parado" e não como erro (cobre FR-026)
4. **Given** a stack subindo, **When** um serviço ainda não responde, **Then** ele aparece como "iniciando" e passa a "saudável" quando responde (cobre FR-026)
5. **Given** um serviço respondendo à verificação, **When** o painel avalia o estado, **Then** o critério usado é o de acessibilidade por loopback sem credenciais, o mesmo do `artigo2md doctor`, e cada estado mostra o horário da última verificação (cobre FR-026)
6. **Given** a stack no ar e o painel visível, **When** um serviço é derrubado por fora do app, **Then** o painel mostra a mudança em até 10 segundos, sem a pessoa precisar acionar nada (cobre FR-027)
7. **Given** a API no ar com a consumidora de eventos desconectada, **When** o painel consulta a API, **Then** exibe o aviso "consumidor de eventos parado" (cobre FR-028)
8. **Given** a stack iniciada pelo app, **When** a pessoa consulta o painel, **Then** vê o endereço local de cada serviço (Postgres, RabbitMQ, API) como texto, sem que o app abra o navegador, e o painel de gestão do RabbitMQ não está publicado em nenhuma porta (cobre FR-029)

---

### User Story 6 - Acompanhar os downloads da primeira execução (Priority: P2)

Na primeira execução, a infraestrutura precisa baixar as imagens de container e os modelos de conversão, algo demorado e que usa a internet. O app avisa antes, pede confirmação e mostra o progresso de cada download, para a pessoa saber que não travou.

**Why this priority**: Os modelos do Docling somam alguns GB e são baixados na primeira conversão (fonte: `README.md`, linhas 34-35); sem progresso visível, a primeira experiência parece uma falha, e o briefing lista o peso dos modelos como risco de abandono (fonte: `docs/briefing.md`, seção 8). Os downloads só ocorrem na primeira execução, por isso a prioridade é P2.

**Independent Test**: Numa máquina sem imagens nem modelos, iniciar a infraestrutura e a primeira preparação, confirmar o aviso e acompanhar o progresso até "concluído"; reiniciar o app e conferir que nenhum download é pedido de novo.

**Acceptance Scenarios**:

1. **Given** uma máquina sem as imagens da stack nem os modelos, **When** a pessoa aciona "Iniciar infraestrutura" pela primeira vez, **Then** o app mostra a lista da primeira execução com as etapas "imagens da infraestrutura" e "modelos de conversão", cada uma com seu estado, e a segunda oferece o passo explícito "Preparar modelos" (cobre FR-030, FR-036)
2. **Given** a lista da primeira execução, **When** a pessoa vai iniciar um download, **Then** o app informa em português que a internet é necessária só nesta etapa, o tamanho aproximado, onde os arquivos ficam e pede confirmação explícita; sem confirmação, nada é baixado (cobre FR-031)
3. **Given** um download em andamento, **When** o painel é exibido, **Then** mostra percentual quando o tamanho total é conhecido; quando não é, mostra o volume já baixado, o contador de itens concluídos (`itemsDone/itemsTotal`) e a hora da última atualização, valores que mudam entre duas leituras consecutivas com o download em curso (ou a tela indica "sem resposta" se nada mudar por mais de 5 segundos); atualizado ao menos a cada 5 segundos, com a janela respondendo (cobre FR-032)
4. **Given** menos espaço livre em disco do que o download exige, **When** a pessoa vai confirmar o download dos modelos, **Then** o app avisa quanto espaço falta e não inicia o download (cobre FR-033)
5. **Given** a internet indisponível ou uma queda no meio do download, **When** o download falha, **Then** o app mostra o motivo em português, mantém o estado da etapa como "falhou" e oferece "Tentar de novo" (cobre FR-034)
6. **Given** o app fechado no meio de um download, **When** ele abre de novo, **Then** mostra o estado real das etapas, não trata etapa interrompida como concluída e oferece retomar (cobre FR-034)
7. **Given** as duas etapas concluídas, **When** a pessoa inicia a infraestrutura nas vezes seguintes, **Then** nenhum download é pedido nem feito, e o app funciona sem internet (cobre FR-035)
8. **Given** a etapa "modelos de conversão" pendente, **When** a pessoa aciona "Preparar modelos" e confirma, **Then** o app dispara o download dos modelos pelo mecanismo novo de FR-036, sem converter nenhum documento, e o progresso aparece na lista; sem essa confirmação, nem o app nem a primeira conversão baixam os modelos (cobre FR-036, FR-031, FR-032)
9. **Given** a etapa "modelos de conversão" pendente, **When** a pessoa aciona "Iniciar workers" (momento 1), **Then** o app recusa com `modelsMissing`, informa em português que os modelos ainda não foram preparados e leva ao passo "Preparar modelos", sem iniciar nenhum processo de worker e sem baixar nada; **And** **When** uma conversão é solicitada por quem consome (momento 2; a interface de conversão pertence a `desktop-processing`) com a etapa pendente, **Then** o estado "modelos de conversão: pendente" fica visível ao app e a quem o consome, a mensagem em português é a mesma e nenhum download escondido é iniciado (cobre FR-018, FR-036, FR-035)

---

### User Story 7 - Tudo local, em português e sem tocar na CLI (Priority: P2)

A pessoa que processa documentos sensíveis tem a garantia de que o app, ao cuidar da infraestrutura, só conversa com serviços da própria máquina, de que todas as mensagens estão em português e de que a linha de comando continua funcionando como antes.

**Why this priority**: É a promessa central do projeto (fonte: `README.md:4`) (fonte: `docs/constitution.md`, Princípios I e III); a feature não pode entregar o controle da infraestrutura quebrando essa promessa. É transversal, por isso é verificada junto com as demais stories.

**Independent Test**: Observar as conexões de rede do app numa sessão completa (diagnóstico, subir, workers, painel, parar), revisar todas as telas e rodar `convert`, `doctor` e `serve`.

**Acceptance Scenarios**:

1. **Given** uma sessão completa de uso, **When** se observam as conexões do processo do app, **Then** todas têm destino `127.0.0.1` e nenhuma sai da máquina (cobre FR-037)
2. **Given** o app em uso, **When** se procura telemetria, relatório de uso ou verificação de atualização, **Then** não há nenhum (cobre FR-037)
3. **Given** todas as telas e mensagens desta feature, **When** são revisadas, **Then** todo texto visível ao usuário está em português do Brasil, inclusive as mensagens traduzidas dos scripts e dos códigos de saída (cobre FR-038, FR-014)
4. **Given** esta feature entregue, **When** se executam `artigo2md convert`, `doctor` e `serve`, **Then** o comportamento é idêntico ao anterior (cobre FR-039)
5. **Given** cada dependência nova obrigatória, **When** a licença é conferida em fonte oficial, **Then** ela se enquadra no Princípio II e fica registrada no plano (cobre FR-040)
6. **Given** a suíte de testes desta feature, **When** `uv run pytest`, `cargo test` e os testes npm rodam, **Then** passam sem Docker e, com a variável de ambiente de integração, os testes que usam o compose também passam (cobre FR-041)

---

### Edge Cases

- **Dois runtimes em execução ao mesmo tempo** (por exemplo, Docker Desktop e colima): o app mostra o estado de cada um e qual está respondendo como o Docker em uso; se não for possível determinar qual está em uso, informa a ambiguidade em português em vez de escolher um sem avisar (cobre FR-001, FR-002)
- **Runtime parado no meio de uma operação** (o runtime é fechado enquanto a stack sobe): a operação termina com a mensagem de "Docker indisponível", sem estado de "iniciando" eterno (cobre FR-014, FR-015)
- **Pasta de dados inexistente, sem permissão de escrita ou com link simbólico no caminho**: o app informa o problema em português, não inicia a stack nem os workers, e oferece escolher outra pasta; o caminho precisa ser o mesmo para a stack e para os workers (cobre FR-012, FR-014)
- **Arquivo de parâmetros existente mas inválido** (credencial vazia, com o valor de exemplo, senha curta ou com `$`): o app traduz a recusa do script para o nome da variável a revisar, nunca mostra o valor e nunca o regenera sozinho (cobre FR-010, FR-011, FR-014)
- **Porta de serviço inválida nas configurações avançadas** (fora de 1 a 65535 ou não numérica): o app recusa o valor antes de gravar, em português; o endereço de publicação não é configurável e permanece em loopback (cobre FR-013, FR-037)
- **Um worker deixado órfão por falha do app que ainda mantém um job**: ao reabrir, a oferta de parar usa o encerramento com calma, e o job não confirmado volta à fila por comportamento do worker (cobre FR-020, FR-021)
- **Fechar o app com um job em andamento**: o encerramento com calma aguarda o prazo do worker (padrão 30 s) e, esgotado, força o encerramento; o app só termina depois que nenhum processo de worker resta, exceto se o teto de 60 s para o fechamento (SC-005; `plan.md` M21) for atingido. Passado o teto, o app sai mesmo assim, registra os PIDs dos processos remanescentes (sem credenciais) e a próxima abertura aplica FR-021 (pool órfão: oferece parar ou continuar usando, com o encerramento com calma); o job não confirmado volta à fila por comportamento do worker (cobre FR-024, FR-020, FR-021; resolve ux CHK009)
- **Pasta do repositório movida ou apagada depois de indicada**: a próxima operação reverifica a pasta (FR-016), informa em português o que falta e não executa nada; o pool ou a stack já em execução não são afetados (cobre FR-016)
- **Fechar o app com a stack no ar**: a stack continua no ar de propósito; ao reabrir, o app mostra o estado real dela e a pessoa pode pará-la (cobre FR-024, FR-025)
- **Programa do próprio usuário rodando `artigo2md worker` à mão**: o app só gerencia os processos que ele mesmo iniciou e não encerra os do usuário (cobre FR-021)
- **Falta de memória livre**: o worker adia conversores e avisa; o app exibe o aviso nos detalhes do pool e não o trata como falha (cobre FR-019, FR-023)
- **Código de saída do worker 3, 4 ou 5** (dependências da fila ausentes, credenciais ausentes ou recusadas, topologia incompatível): cada um vira uma mensagem própria em português com o próximo passo, sem repetir o texto técnico em inglês como mensagem principal (cobre FR-022, FR-014)
- **Painel com a API no ar mas o banco indisponível**: o serviço indisponível aparece como "com problema", os demais mantêm o estado próprio, e a verificação nunca trava o painel (cobre FR-026, FR-027)
- **Verificação de saúde lenta**: cada verificação tem tempo máximo curto; um serviço que não responde no limite aparece como indisponível sem congelar a janela (cobre FR-027, FR-015)
- **Sem espaço em disco durante o download das imagens da stack**: a falha é traduzida, a etapa fica "falhou" e a pessoa pode tentar de novo depois de liberar espaço (cobre FR-033, FR-034)
- **Download de modelos interrompido porque o app foi fechado ou encerrado à força**: a etapa não é tratada como concluída; ao reabrir, o estado real é recalculado e "Preparar modelos" é oferecido de novo, com retomada quando o mecanismo permitir (cobre FR-034, FR-036)
- **Etapa de download concluída e dado removido por fora do app** (a pessoa apagou o cache de modelos): o estado volta a "pendente" na próxima verificação, e o download exige nova confirmação (cobre FR-030, FR-031, FR-035)
- **Duas janelas ou duas instâncias do app**: apenas uma gerencia o ciclo de vida; a segunda mostra o estado e recusa as ações de subir e parar com aviso em português (cobre FR-015, FR-021)

## Requirements

### Functional Requirements

#### Detecção do runtime Docker

- **FR-001**: O sistema MUST detectar, para cada um de Docker Desktop, OrbStack e colima, se está não instalado, instalado e parado, ou em execução, e MUST mostrar essa situação ao usuário em português.
- **FR-002**: O sistema MUST classificar a situação geral como "Docker pronto" (ao menos um runtime em execução e o Docker Compose disponível), "nenhum runtime encontrado", "runtime instalado e parado" ou "Docker Compose ausente", MUST mostrar qual runtime está em uso quando houver mais de um e MUST informar a ambiguidade quando não puder determiná-lo. As ações de subir a infraestrutura MUST ficar liberadas somente em "Docker pronto".
- **FR-003**: O sistema MUST executar o diagnóstico ao abrir o app, sob demanda ("Verificar novamente") e automaticamente enquanto o guia de instalação estiver aberto, sem bloquear a interface e sem conexão de rede; as telas "Arquivos" e "Configurações" MUST continuar funcionando integralmente sem Docker.

#### Guia de instalação do Docker

- **FR-004**: O sistema MUST oferecer um guia passo a passo, em português e em linguagem para pessoas não técnicas, que apresente os três runtimes numa comparação simples, deixe a pessoa escolher um e a conduza pelos passos de instalação e de primeira abertura daquele runtime, indicando quantos passos faltam.
- **FR-005**: O conteúdo de cada passo do guia MUST se basear na documentação oficial do runtime, conferida na etapa `plan` com a fonte registrada (nenhum endereço, comando ou nome de menu é presumido por esta spec). O guia MUST ser parte do app (funciona sem internet), MUST mostrar endereços oficiais como texto copiável e MUST NOT abrir o navegador, baixar nem executar instalador.
- **FR-006**: O guia MUST orientar como iniciar um runtime instalado e parado, refazer a verificação automaticamente, encerrar-se sozinho ao detectar "Docker pronto" e lembrar o passo em que a pessoa parou entre execuções do app.

#### Ciclo de vida da stack

- **FR-007**: O sistema MUST oferecer a ação "Iniciar infraestrutura", que sobe o compose da `container-stack` com o mesmo comportamento do script de subir dela (validação dos parâmetros antes de chamar o Docker, detecção de porta ocupada, espera pelos serviços saudáveis) e mostra o resultado de cada serviço em português (fonte: `stack/up.sh`).
- **FR-008**: O sistema MUST oferecer a ação "Parar infraestrutura", que derruba os containers preservando os volumes de dados, com o mesmo comportamento do script de derrubar sem opções (fonte: `stack/down.sh`). O app MUST NOT oferecer, nesta feature, apagar os dados da stack nem atualizar imagens.
- **FR-009**: As ações de iniciar e parar MUST ser idempotentes: iniciar com a stack no ar não duplica serviços, não recria volumes nem perde dados e informa que já estava no ar; parar com a stack parada termina sem erro e informa que já estava parada.
- **FR-010**: Quando o arquivo de parâmetros da stack não existir, o sistema MUST criá-lo na primeira inicialização com credenciais geradas aleatoriamente que cumpram as regras da `container-stack` (senha com 12 ou mais caracteres, sem `$`, diferente do valor de exemplo), portas padrão e a pasta de dados do app, com permissão de leitura restrita ao usuário. O sistema MUST NOT sobrescrever um arquivo existente nem regenerar credenciais já gravadas (fonte: `docs/specs/container-stack/spec.md`, FR-004 e comentário final da seção de decisões de infraestrutura; `stack/.env.example`).
- **FR-011**: As credenciais MUST NOT aparecer na tela, nas mensagens, nos registros do app, nos detalhes técnicos exibidos nem na linha de comando de nenhum processo iniciado; MUST chegar aos workers pelo ambiente do processo, como o worker já prevê (fonte: `docs/specs/conversion-queue/spec.md`, FR-023 e FR-024). Mensagens sobre credencial citam só o nome da variável.
- **FR-012**: O sistema MUST manter uma única pasta de dados do app como fonte de verdade para a stack e para os workers: propor um padrão dentro da pasta pessoal do usuário (caminho exato definido no `plan`), criá-la quando ausente, mostrá-la na tela e validar que ela existe, é gravável e não contém link simbólico no caminho, pois a stack e o worker exigem o mesmo caminho real (fonte: `stack/.env.example`, comentário de `ARTIGO2MD_DATA_DIR`).
- **FR-013**: O sistema MUST permitir trocar as portas dos serviços (Postgres, RabbitMQ e API; a do painel de gestão do RabbitMQ não, porque o app não o publica, FR-029) numa área de configurações avançadas, validando que cada valor é numérico entre 1 e 65535, aplicando-o na próxima inicialização; o endereço de publicação MUST permanecer fixo em `127.0.0.1` (fonte: `stack/.env.example`; `docs/specs/container-stack/spec.md`, FR-006).
- **FR-014**: O sistema MUST traduzir o resultado de cada operação para uma mensagem própria em português, com o próximo passo, a partir dos códigos de saída dos scripts da stack (subir: 2 parâmetros inválidos, 3 Docker indisponível, 4 porta ocupada, 5 serviço não saudável; derrubar: 1 exclusão não confirmada, 2 opções inválidas, 3 Docker indisponível; fonte: `stack/up.sh`, `stack/down.sh`) e dos códigos de saída do worker (fonte: `docs/specs/conversion-queue/contracts/worker-cli.md`). A saída técnica original MUST ficar disponível sob demanda, sem credenciais, e MUST NOT ser a mensagem principal.
- **FR-015**: Operações longas MUST NOT congelar a interface e MUST mostrar a etapa atual; o sistema MUST permitir uma só operação de ciclo de vida por vez (iniciar, parar, iniciar workers, parar workers) e MUST recusar, com aviso em português, outra que chegue enquanto uma está em andamento, inclusive vinda de uma segunda instância do app.
- **FR-016**: O sistema MUST localizar os arquivos da stack (compose e scripts) e o executável do worker de forma definida e verificável antes de qualquer operação, informando em português o que não encontrou e como resolver. A localização MUST partir de uma "pasta do repositório" que a pessoa indica na tela de configurações e que é persistida como as demais configurações; o app MUST usar o ambiente `uv` local dessa pasta para iniciar o worker e MUST NOT empacotar a stack, o worker nem o ambiente de execução dentro do `.app` nesta feature (o empacotamento fica para `desktop-release`). Antes de qualquer operação, o sistema MUST verificar que a pasta indicada contém o compose e os scripts de subir e derrubar da `container-stack` (fonte: `stack/compose.yaml`, `stack/up.sh`, `stack/down.sh`), que o `uv` está disponível e que o comando do worker pode ser iniciado a partir dela (fonte: `README.md`, seção "Fila de conversão (worker)", comando `uv run artigo2md worker`; saída 3 do worker quando falta o extra `queue`, fonte: `docs/specs/conversion-queue/contracts/worker-cli.md`). Sem pasta indicada, ou com pasta inválida, o sistema MUST informar em português o que falta e como resolver, e MUST NOT executar nenhuma operação de infraestrutura nem de workers; "Arquivos", "Configurações" e o diagnóstico do Docker seguem funcionando (FR-003). O `uv` e as dependências do repositório são pré-requisitos instalados fora do app; o app MUST NOT instalá-los nem baixá-los.
- **FR-016A**: Como o app não empacota o repositório (FR-016), a pasta indicada pode ter versão diferente da do app. Antes de usar o subcomando `artigo2md models` (FR-036), o sistema MUST verificar que ele existe e que o contrato é compatível: cada evento do `models` traz o campo `schema_version` (`contracts/models-cli.md`; `[PROPOSTA]`, análogo ao `schema_version` do worker, `docs/specs/conversion-queue/contracts/worker-cli.md`), que o app compara com a versão que conhece. Subcomando ausente (saída 2 de uso inválido ao rodar `models status --json`), evento sem `schema_version` ou com versão desconhecida MUST resultar na mensagem em português "A pasta do repositório indicada tem uma versão incompatível com este app: falta o comando de preparação de modelos ou o contrato mudou. Atualize o repositório e verifique de novo.", sem executar nenhuma operação que dependa de `models` e sem tentar interpretar o resto da saída; o estado é `RepoCheck.status = modelsUnsupported` (`data-model.md`). O app nunca usa `--management-panel` do `up.sh` (FR-029), portanto a ausência dessa opção num repositório mais antigo não bloqueia nada: apenas a ausência de `models` bloqueia (resolve api CHK011).
- **FR-017**: Todo valor controlado pelo usuário ou derivado dele (caminhos, portas, números) MUST ser passado aos processos iniciados como argumento isolado, sem interpretação por shell, de modo que espaços e caracteres especiais não alterem o comando executado.

#### Pool de workers nativos

- **FR-018**: O sistema MUST oferecer "Iniciar workers", que inicia o pool de workers de conversão como processo auxiliar nativo do app, iniciado pelo ambiente `uv` da pasta do repositório indicada (FR-016), somente com a stack no ar (RabbitMQ acessível) e com a etapa "modelos de conversão" concluída (FR-036), usando a pasta de dados do app, o número de workers e a memória livre mínima das configurações; com a stack parada, MUST recusar e orientar a iniciar a infraestrutura antes; com os modelos não preparados, MUST recusar com a mensagem em português "os modelos de conversão ainda não foram preparados" e levar ao passo "Preparar modelos" (contrato: `modelsMissing`, `contracts/tauri-commands.md`), sem iniciar nenhum processo.
- **FR-019**: A tela de configurações MUST oferecer a pasta do repositório (FR-016), o número de workers (inteiro maior ou igual a 1; padrão 4) e a memória livre mínima em MB (inteiro maior ou igual a 0; padrão 4822), com os valores persistidos como as demais configurações; os padrões são os do worker, derivados da medição de RAM (fonte: `src/artigo2md/jobqueue/settings.py`, `DEFAULT_WORKERS` e `DEFAULT_MIN_FREE_MEMORY_MB`; `docs/specs/conversion-queue/ram-measurement.md`). O sistema MUST mostrar ao usuário o aviso de pool acima da RAM que o worker emite.
- **FR-020**: O sistema MUST oferecer "Parar workers", que solicita o encerramento com calma, aguarda o prazo de encerramento do worker (padrão do worker: 30 s) e, esgotado o prazo, força o encerramento; ao final MUST NOT restar processo de worker nem de conversão filho ativo, e o job não concluído volta à fila por comportamento do próprio worker (fonte: `docs/specs/conversion-queue/spec.md`, FR-013).
- **FR-021**: O sistema MUST gerenciar apenas os processos que ele mesmo iniciou, MUST NOT iniciar um segundo pool com um já ativo e, ao abrir, MUST detectar um pool deixado por uma sessão anterior do app e oferecer parar ou continuar usando; MUST NOT encerrar processos de worker iniciados pelo usuário fora do app.
- **FR-022**: Quando o pool terminar de forma inesperada, o sistema MUST mostrá-lo como "parado de forma inesperada" com o motivo traduzido para o português a partir do código de saída, MUST NOT reiniciá-lo automaticamente em laço e MUST oferecer "Reiniciar".
- **FR-023**: O sistema MUST mostrar nos detalhes do pool as últimas mensagens do worker (em português, já emitidas por ele), limitadas a uma quantidade finita de linhas, sem credenciais.
- **FR-024**: Ao fechar o app com o pool de workers em execução, o sistema MUST parar o pool com o mesmo encerramento com calma de FR-020 (o job não concluído volta à fila por comportamento do worker), mostrando o estado "Encerrando workers…" enquanto aguarda, sem exigir confirmação da pessoa. O app só termina depois que nenhum processo de worker resta, exceto se o teto de 60 s do fechamento (SC-005; `plan.md` M21) for atingido: então o app sai, registra os PIDs remanescentes e a próxima abertura aplica FR-021 (pool órfão). Ao fechar o app, o sistema MUST NOT derrubar a infraestrutura: Postgres, RabbitMQ e API continuam no ar, e jobs enfileirados aguardam na fila até os workers serem iniciados de novo (contrato com `desktop-processing`: sem o app aberto não há worker consumindo a fila). Com a stack no ar e o pool já parado, o fechamento não executa nenhuma ação sobre a stack nem sobre processos. Se o app for encerrado à força, aplica-se FR-021 na próxima abertura.

#### Painel de saúde

- **FR-025**: O sistema MUST ter a entrada "Infraestrutura" na barra lateral, com um painel que mostra o runtime, o Postgres, o RabbitMQ, a API e o pool de workers, e um indicador resumido da saúde da infraestrutura visível em qualquer tela.
- **FR-026**: Cada serviço MUST ter um estado em português entre "parado", "iniciando", "saudável" e "com problema", com o horário da última verificação. O critério de "saudável" para Postgres, RabbitMQ e API MUST ser o de acessibilidade por loopback sem credenciais, o mesmo do `artigo2md doctor` (handshake do protocolo ou endpoint de saúde), e MUST NOT usar credenciais (fonte: `src/artigo2md/infra.py`; `docs/specs/container-stack/spec.md`, Clarifications, dec-014). Um serviço parado MUST NOT ser apresentado como erro.
- **FR-027**: O sistema MUST sondar os serviços periodicamente, e não apenas com o painel visível: o temporizador do núcleo sonda a cada 2 s enquanto o app estiver aberto, porque o indicador resumido da barra lateral (FR-025) é visível em toda tela; além disso há verificação sob demanda ("Verificar novamente"). Cada verificação tem tempo máximo curto, de modo que um serviço que não responde apareça como "com problema" sem congelar a janela. A interface lê o retrato com a cadência do plano (3 s; 1 s com operação em andamento; `research.md`, Decision 10) e a mantém com a janela oculta ou minimizada; qualquer redução da cadência nessa condição depende da pendência de performance CHK016 do dono do produto e não é decidida aqui (resolve performance CHK013). A aritmética de SC-003 (2 s de espera + 3 s de rodada + 3 s de leitura da interface = 8 s, pior caso) vale para esta cadência.
- **FR-028**: Com a API no ar, o painel MUST mostrar o aviso "consumidor de eventos parado" quando a API informar que a consumidora de eventos dos workers está desconectada (fonte: `README.md`, seção da API, `GET /v1/status`).
- **FR-029**: O painel MUST mostrar o endereço local de cada serviço (Postgres, RabbitMQ, API), com as portas em vigor, como texto, sem abrir o navegador. A stack iniciada pelo app MUST NOT publicar o painel de gestão do RabbitMQ: o painel só é publicado, em `127.0.0.1`, por opt-in explícito fora do app, e a stack MUST deixar de publicá-lo por padrão (proteção contra DNS rebinding; decisão do operador em block-004, dec-023; muda o comportamento da `container-stack`).

#### Primeira execução: downloads

- **FR-030**: O sistema MUST apresentar uma lista da primeira execução com as etapas "imagens da infraestrutura" e "modelos de conversão", cada uma com estado (pendente, baixando, concluída, falhou) e progresso, e MUST recalcular o estado real a cada verificação, sem presumir concluída uma etapa interrompida ou removida por fora do app.
- **FR-031**: Antes de qualquer download (inclusive o do passo "Preparar modelos", FR-036), o sistema MUST informar, em português, que a internet é necessária só nessa etapa, o tamanho aproximado, onde os arquivos ficam, e MUST obter confirmação explícita; sem confirmação, MUST NOT baixar nada. O tamanho aproximado dos modelos MUST citar fonte (o README cita cerca de 3 GB livres para os modelos do Docling; fonte: `README.md:34`).
- **FR-032**: Durante um download, o sistema MUST mostrar o percentual quando o tamanho total for conhecido. Quando o tamanho total for desconhecido (`totalBytes` nulo), MUST mostrar, em termos verificáveis: (a) o volume já baixado em unidade legível, (b) o contador de itens concluídos sobre o total de itens (`itemsDone/itemsTotal`, por exemplo "1 de 2 modelos") e (c) a hora da última atualização recebida (`updatedAt`), que é o sinal de atividade: com o download em curso, duas leituras consecutivas do retrato, separadas por até 5 segundos, MUST diferir em `downloadedBytes` ou em `updatedAt`; se nenhuma das duas mudar por mais de 5 segundos, a tela MUST indicar que a etapa está sem resposta em vez de exibir um valor parado como se houvesse atividade. A atualização ocorre ao menos a cada 5 segundos, sem congelar a interface (resolve ux CHK006).
- **FR-033**: Antes de iniciar um download, o sistema MUST verificar o espaço livre em disco e, se for insuficiente, avisar quanto falta e não iniciar o download.
- **FR-034**: Falhas de download (sem internet, interrupção, disco cheio) MUST ser traduzidas para o português, manter a etapa como "falhou" e oferecer "Tentar de novo"; ao reabrir o app com um download interrompido, o sistema MUST mostrar o estado real e oferecer retomar.
- **FR-035**: Concluídas as duas etapas, as inicializações seguintes MUST funcionar sem internet e MUST NOT baixar nada sem pedido explícito da pessoa (Princípio I).
- **FR-036**: O download dos modelos do Docling MUST ser disparado pelo próprio app, num passo explícito "Preparar modelos" oferecido na lista da primeira execução, que só executa depois da confirmação de FR-031 e antes da primeira conversão; a primeira conversão real MUST NOT ser o gatilho do download. Como hoje não existe contrato nem mensagem do worker que informe progresso de download de modelos (fonte: `docs/specs/conversion-queue/contracts/worker-cli.md`), esta feature MUST incluir um mecanismo novo, do lado do componente de conversão e usado pelo app, que (a) baixe os modelos sem converter documento algum, (b) informe o progresso (percentual quando o tamanho total é conhecido, senão o volume já baixado) e o resultado final (concluído ou falhou, com o motivo) de forma legível pelo app, em português e sem credenciais, e (c) permita verificar quais modelos já estão presentes, para o recálculo de estado de FR-030. O formato do mecanismo e o ponto onde ele vive MUST ser definidos na etapa `plan`, a partir do código real e da documentação do Docling com fonte registrada; esta spec não presume nenhum comando, mensagem ou formato. O download MUST ser feito pelo componente de conversão, em processo próprio iniciado pelo app, nunca por uma conexão de rede do próprio app (FR-037), e o comportamento de `convert`, `doctor` e `serve` não muda (FR-039).

#### Transversais

- **FR-037**: O app MUST conectar-se somente ao endereço de loopback `127.0.0.1`, para verificar e consultar os serviços locais; MUST NOT abrir conexão para fora da máquina, MUST NOT enviar telemetria nem verificar atualizações e MUST NOT usar serviço remoto (Princípio I). Os downloads de imagens e de modelos da primeira execução, permitidos pelo Princípio I, são feitos pelo runtime Docker e pelos componentes de conversão, nunca por uma conexão do app, e só depois da confirmação de FR-031.
- **FR-038**: Todo texto visível ao usuário desta feature (telas, guia, mensagens traduzidas, avisos, estados) MUST estar em português do Brasil (Princípio III).
- **FR-039**: O sistema MUST manter `convert`, `doctor` e `serve` com comportamento idêntico ao atual, sem Docker e sem a stack (fonte: `docs/specs/container-stack/spec.md`, FR-010; `docs/specs/conversion-queue/spec.md`, FR-017).
- **FR-040**: Toda dependência de código nova e obrigatória MUST se enquadrar no Princípio II da constitution, com a licença conferida em fonte oficial e registrada na etapa `plan`.
- **FR-042**: As telas novas desta feature (entrada "Infraestrutura", painel de saúde, lista da primeira execução, guia de instalação, seção de configurações da infraestrutura) MUST herdar o padrão de acessibilidade já praticado pelo `desktop-shell` (levantado em `desktop/src/components/Sidebar.svelte`, `FileTree.svelte`, `TreeNode.svelte`, `App.svelte`: rótulos `aria-label` vindos de `desktop/src/lib/messages.ts`, `aria-current`, `aria-expanded`, `aria-busy`, `role="status"` para avisos, ordem de foco por teclado e `tabindex` só onde há papel interativo) e MUST: (a) ser operáveis integralmente pelo teclado (Tab/Shift+Tab, Enter e Espaço nas ações; foco visível), (b) ser lidas pelo VoiceOver com rótulo acessível em português para cada controle, estado e serviço, (c) comunicar o estado do indicador resumido da barra lateral (saudável, parcial, parada) e de cada serviço também por texto e rótulo acessível, nunca só por cor, e (d) anunciar mudanças de estado e avisos por região `role="status"`. Qualquer limiar numérico de contraste só entra com fonte oficial consultada; sem ela fica como PROPOSTA a validar, sem valor inventado (resolve ux CHK015).
- **FR-041**: O sistema MUST cobrir a feature com testes automatizados (`uv run pytest`, `cargo test` e testes npm) que rodam sem Docker; os testes que dependem do compose no ar MUST ficar atrás de uma variável de ambiente, conforme o Princípio IV (fonte: `docs/constitution.md`).

> Decisões de infraestrutura: scheduling = sondagem periódica de saúde (FR-027, sem agendador persistente); idempotência = FR-009 e FR-021; backup/restore, rotação de chaves, refresh de token, mutex multi-pod: não se aplicam (app local monousuário; as credenciais só valem na primeira inicialização do volume, fonte: `stack/.env.example`, e a troca delas fica fora desta feature junto com o apagar de dados).

### Key Entities

- **Runtime Docker**: cada um de Docker Desktop, OrbStack e colima, com situação (não instalado, instalado e parado, em execução) e indicação de qual está em uso.
- **Infraestrutura (stack)**: o conjunto Postgres, RabbitMQ e API, com estado (parada, iniciando, no ar, com problema) e a operação de ciclo de vida em andamento, se houver.
- **Pool de workers**: o processo auxiliar nativo que roda os workers de conversão, com número de workers, memória livre mínima, estado (parado, em execução, parado de forma inesperada) e as últimas mensagens.
- **Serviço monitorado**: cada serviço do painel (runtime, Postgres, RabbitMQ, API, pool), com estado, horário da última verificação e endereço local.
- **Parâmetros da infraestrutura**: portas, pasta de dados do app e credenciais geradas; as credenciais nunca são exibidas.
- **Pasta do repositório**: caminho indicado pela pessoa nas configurações, de onde vêm o compose, os scripts da stack e o ambiente `uv` do worker; verificada antes de cada operação.
- **Etapa da primeira execução**: "imagens da infraestrutura" ou "modelos de conversão", com estado, progresso, tamanho aproximado e confirmação do usuário.
- **Passo do guia**: passo de instalação de um runtime, com texto em português, fonte oficial registrada e a marca de em qual passo a pessoa parou.

## Success Criteria

### Measurable Outcomes

- **SC-001**: Num Mac com runtime pronto, imagens e modelos já baixados, 100% das execuções de "Iniciar infraestrutura" seguida de "Iniciar workers" levam todos os serviços e o pool ao estado "saudável" em até 3 minutos, com uma ação da pessoa por etapa (meta desta feature; a parte da stack parte da meta de 2 minutos de `docs/specs/container-stack/spec.md`, SC-001, que é uma meta e não uma medição).
- **SC-002**: Em avaliação manual de aceitação, uma pessoa não técnica sem Docker chega, seguindo só o guia, ao diagnóstico "Docker pronto" em até 20 minutos, sem contar o tempo de download do instalador.
- **SC-003**: Em 100% das verificações, o painel reflete a mudança de estado de um serviço em até 10 segundos.
- **SC-004**: Zero conexões do app para destinos diferentes de `127.0.0.1` numa sessão completa de uso (diagnóstico, subir, workers, painel, parar), verificado por observação do tráfego do processo do app, como em `docs/specs/desktop-shell/spec.md`, SC-004.
- **SC-005**: Após "Parar workers" e "Parar infraestrutura", em até 60 segundos não resta nenhum processo de worker nem container do projeto em 100% das tentativas, e 100% dos dados gravados antes continuam disponíveis ao iniciar de novo. O orçamento vale para a **soma** das duas ações acionadas em sequência (cronometrada do acionamento de "Parar workers" ao fim de "Parar infraestrutura"), não para cada uma isoladamente; "Parar workers" usa até 45 s (30 s de prazo do worker mais margem), o que deixa o restante para o `down.sh`, cujo tempo real é medido no teste de integração. Se a soma passar de 60 s, a tela não interrompe nem anula nenhuma operação (cada uma segue até o fim e mostra o resultado real) e o **teste de integração falha**, registrando as duas durações; o fechamento do app tem teto próprio de 60 s (edge "Fechar o app com um job em andamento"). A meta de 60 s não foi alterada (resolve performance CHK005; a adequação da meta ao produto segue na pendência performance CHK014).
- **SC-006**: 100% do texto visível desta feature está em português, verificado por revisão de todas as telas, mensagens e passos do guia.
- **SC-007**: Em inspeção da tela, das mensagens, dos registros do app e da lista de processos durante uma sessão completa, 0 ocorrências de senha ou outra credencial.
- **SC-008**: Durante um download da primeira execução, o progresso exibido é atualizado ao menos a cada 5 segundos em 100% das observações e a janela responde a cliques durante todo o download.
- **SC-009**: A suíte `uv run pytest` e os testes `cargo test` e npm passam em 100% sem Docker; os testes de integração com o compose passam quando habilitados pela variável de ambiente.
- **SC-010**: Acessibilidade das telas novas (FR-042): em verificação manual com VoiceOver, 100% dos controles, serviços e estados da entrada "Infraestrutura" são anunciados em português e operáveis só pelo teclado; um teste npm confere que o indicador resumido e cada serviço têm rótulo acessível em texto para todos os estados e que nenhum estado é expresso só por cor.

## Delta Requirements

**MODIFIED (container-stack, block-004/dec-023)**: o painel de gestão do RabbitMQ deixa de ser publicado por padrão (FR-029); a publicação passa a opt-in explícito, sempre em `127.0.0.1`. Ajustes nos artefatos e testes da `container-stack` listados em `plan.md`, seção "Painel de gestão do RabbitMQ fora da publicação padrão".

**Skip (demais mudanças)**: o corpus canônico `docs/specs/current/` não existe neste projeto, e as outras mudanças em features anteriores (entrada "Infraestrutura" na barra lateral e conexões de loopback liberadas ao app) são aditivas e registradas no cabeçalho desta spec — feature-00c, 2026-11-26
