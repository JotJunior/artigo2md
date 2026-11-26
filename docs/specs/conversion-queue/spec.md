# Feature Specification: Fila de conversão e worker nativo

**Feature**: `conversion-queue`
**Created**: 2026-11-24
**Status**: Draft

> **Origem**: item 4 do roadmap da versão desktop macOS (fonte: `docs/roadmap.md`), com base no briefing 2.0 e na constitution 1.1.1 (fonte: `docs/briefing.md`) (fonte: `docs/constitution.md`). Depende da `container-stack`, já entregue: o RabbitMQ do compose, publicado só em `127.0.0.1` (fonte: `stack/compose.yaml`). Esta feature entrega o lado do **worker** e o **contrato de mensagens**; a API que enfileira jobs e consome eventos (`project-api`) e a persistência no Postgres (`job-store`) são features futuras e ficam fora do escopo. Pelo briefing, os workers não acessam o banco: recebem jobs pela fila e publicam eventos de progresso e de resultado; quem grava no Postgres é a API (fonte: `docs/briefing.md`, seção 4).

## Clarifications

### Session 2026-11-24

- Q: Qual o escopo do cancelamento de job nesta feature? → A: Fora do escopo: o contrato não tem mensagem de cancelamento e o worker não implementa descarte nem interrupção de job; o cancelamento (inclusive de jobs ainda não iniciados) fica para `project-api`/`desktop-processing`, que acrescentam o tipo de mensagem por nova versão do esquema.
- Q: Como o worker conhece o diretório de dados do app? → A: Opção de linha de comando obrigatória do `artigo2md worker`, sem caminho padrão presumido; sem ela o worker recusa iniciar com mensagem em português.
- Q: Qual a convenção de nome e local do Markdown gravado? → A: `<diretório de dados>/<job_id>/<nome-do-pdf>.md`, com o nome derivado do arquivo do PDF, independente de onde o PDF está; o worker valida `job_id` e nome para confinar a escrita ao diretório de dados.
- Q: De onde o worker obtém as credenciais do RabbitMQ? → A: Primeiro das variáveis de ambiente `RABBITMQ_DEFAULT_USER` e `RABBITMQ_DEFAULT_PASS` herdadas do processo; na ausência, das mesmas chaves do arquivo de parâmetros da stack, na ordem de resolução do `doctor` (arquivo indicado por `ARTIGO2MD_STACK_ENV`, depois `stack/.env`).
- Q: O worker grava os assets (imagens e arquivos auxiliares) junto do Markdown? → A: Não nesta feature: grava só o arquivo Markdown (o `ConversionResult` do roteador não tem assets); a lacuna fica registrada para a `project-api`.

### Session 2026-11-25

- Q: Acrescentar campo opcional a um evento é mudança compatível? → A: Sim: mesma `schema_version`; consumidores de eventos MUST ignorar campos desconhecidos. O `job` continua rejeitando campos extras; remover/renomear/mudar tipo exige `schema_version` nova (`contracts/queue-messages.md` §Evolução).
- Q: Qual a ordem de entrega dos eventos para o consumidor? → A: Eventos de um mesmo job e tentativa saem em sequência de um único slot, com confirmação do broker, `progress` antes de `result`/`error`; sem ordem entre jobs nem entre tentativas; um único evento terminal por (`job_id`, `attempt`) em condições normais, e a reentrega pode repeti-lo (`queue-messages.md` §Eventos).
- Q: Como tratar a janela "cópia de retry confirmada, `ack` do original perdido"? → A: Aceita como limitação do at-least-once, com cadeia duplicada limitada (nenhuma mensagem passa de `max_attempts`) e deduplicação do consumidor por (`job_id`, `attempt`); alternativas rejeitadas: estado persistente no worker e descarte por `redelivered` (`queue-messages.md` §Garantias de entrega, item 4).
- Q: O que significa "resultado já calculado não é perdido" (US5-4/FR-012)? → A: O Markdown atômico já está no destino e o job é reentregue e reprocessado de forma idempotente, sem estado persistente próprio do worker.
- Q: O que ocorre ao iniciar sem o pool ou sem o limite de memória antes da medição (US4-2)? → A: Antes da medição, recusa de início com a mensagem "medição pendente" e código de saída 2; a tarefa 7.3 converteu as duas opções em padrão derivado de `ram-measurement.md` (`--workers` 4, `--min-free-memory` 4822 MB) e removeu a recusa.
- Q: O gate de RAM é por slot ou único? → A: Único por processo, serializado por lock; o slot que passa reserva memória (`--min-free-memory`) até o conversor terminar de carregar ou falhar, e os demais slots descontam a reserva. O slot em `reconnecting` não avalia o gate; ao reconectar vai a `cold` e aplica o gate; conversor já vivo não reavalia (`data-model.md` §Slot).
- Q: Qual o critério verificável de "sem espera ativa" com a fila vazia? → A: O slot ocioso bloqueia em espera do broker (`process_data_events` com `time_limit` positivo) e não executa laço sem espera; verificável por contagem de iterações do laço num intervalo com canal falso e, com o broker real, por CPU do processo próxima de zero (`psutil`, janela fixa).
- Q: Há limite de tempo por conversão? → A: Opcional: `--conversion-timeout` (padrão `0` = sem limite, FR-025); o prazo do broker para o `ack` é 30 min (`consumer_timeout` 1800000 ms no RabbitMQ 4.4.1 da imagem da stack; `research.md` Decision 11).
- Q: Quais os limites de entrada do job? → A: Corpo até 16384 bytes e profundidade JSON até 8, política PROPOSTA justificada em `queue-messages.md` §`job`; acima disso `invalid_message`.

## User Scenarios & Testing

### User Story 1 - Converter vários PDFs em paralelo a partir da fila (Priority: P1)

Quem processa uma pasta inteira de artigos coloca os jobs na fila do RabbitMQ e inicia o comando `artigo2md worker` no próprio Mac. O worker consome os jobs, converte cada PDF com o mesmo roteador de motores que a CLI já usa, grava o Markdown e publica o resultado, processando vários PDFs ao mesmo tempo conforme o tamanho do pool configurado.

**Why this priority**: É o ganho central da versão desktop: processar pastas inteiras em paralelo, em vez de uma conversão por vez como na interface web atual (fonte: `docs/briefing.md`, seção 4; `CLAUDE.md`, descrição de `web.py`). Sem esta história não há processamento por fila.

**Independent Test**: Com o RabbitMQ do compose no ar, publicar jobs para um conjunto de PDFs de teste, iniciar o worker com um pool de N processos e conferir que cada PDF gera um arquivo Markdown e um evento de resultado, e que nunca mais de N conversões ocorrem ao mesmo tempo.

**Acceptance Scenarios**:

1. **Given** o RabbitMQ no ar e um job válido na fila apontando para um PDF existente no disco do host, **When** o worker consome o job, **Then** o Markdown é gravado no diretório de dados do app e um evento de resultado, com o mesmo identificador do job, é publicado (cobre FR-001, FR-006, FR-007, FR-008)
2. **Given** um pool configurado com N processos e mais de N jobs na fila, **When** o worker trabalha, **Then** no máximo N conversões ocorrem simultaneamente e cada processo do pool nunca segura mais de um job não confirmado (cobre FR-003, FR-004)
3. **Given** um job em conversão, **When** o roteador avança de etapa (inspeção, tentativa de motor, nova tentativa com outro motor), **Then** eventos de progresso com o identificador do job são publicados em português (cobre FR-008)
4. **Given** um job concluído, **When** o evento de resultado é publicado, **Then** ele traz o relatório da conversão que o roteador já produz (motor usado, tentativas com nota de qualidade e avisos, análise do PDF) e o caminho do Markdown gravado (cobre FR-008, FR-009)
5. **Given** o worker em execução, **When** ele conclui uma conversão, **Then** nenhuma conexão a banco de dados é aberta e nenhum dado é lido de fora do disco do host e do broker local (cobre FR-007, FR-016)
6. **Given** o job que escolhe um motor específico entre os motores já aceitos pelo roteador, **When** o worker o consome, **Then** a conversão usa o motor pedido, e na ausência de escolha usa a escolha automática (cobre FR-006)

---

### User Story 2 - Nenhum job se perde: retry com atraso e fila de mensagens mortas (Priority: P1)

Quem confia na fila para processar centenas de PDFs precisa ter certeza de que uma falha passageira não descarta o job, de que uma falha definitiva não fica tentando para sempre e de que tudo o que falhou de vez fica guardado para inspeção.

**Why this priority**: Sem garantia de entrega e sem tratamento de falhas, a fila perde trabalho silenciosamente e o histórico futuro (`job-store`) ficaria incompleto. É requisito explícito do roadmap: retry com atraso e fila de mensagens mortas (fonte: `docs/roadmap.md`, item 4).

**Independent Test**: Publicar um job que falha de forma passageira (simulada) e conferir que ele volta à fila após o atraso e termina com sucesso; publicar um job com PDF inválido e conferir que vai direto à fila de mensagens mortas, sem nova tentativa; publicar um job que sempre falha e conferir que, esgotadas as tentativas, vai à fila de mensagens mortas.

**Acceptance Scenarios**:

1. **Given** um job cuja conversão falha por motivo passageiro (por exemplo, o processo de conversão terminou por falta de memória), **When** o worker trata a falha, **Then** o job volta à fila de jobs somente depois de um atraso configurável, com a contagem de tentativas incrementada, e um evento de erro marca a tentativa como passível de nova tentativa (cobre FR-002, FR-010)
2. **Given** um job que esgotou o número máximo de tentativas, **When** a última tentativa falha, **Then** o job vai para a fila de mensagens mortas com o motivo da falha e um evento de erro definitivo é publicado (cobre FR-010, FR-011)
3. **Given** um job que aponta para um PDF inexistente, ilegível, protegido por senha ou inválido, **When** o worker o consome, **Then** o job é encerrado como falha definitiva na primeira tentativa, vai para a fila de mensagens mortas sem passar pelo retry, e o evento de erro traz a mensagem em português que o inspetor de PDF já produz (cobre FR-010, FR-011)
4. **Given** uma mensagem que não é JSON válido ou não obedece ao contrato, **When** o worker a recebe, **Then** ela vai para a fila de mensagens mortas sem derrubar o worker nem travar a fila (cobre FR-009, FR-011)
5. **Given** o worker que termina de forma abrupta (encerrado à força) no meio de uma conversão, **When** o broker detecta a conexão perdida, **Then** o job não confirmado volta à fila e é processado por outro worker ou pelo mesmo ao reiniciar, sem perda (cobre FR-003, FR-012)
6. **Given** um job entregue duas vezes (por exemplo, após reentrega), **When** o worker o processa de novo, **Then** o Markdown é regravado no mesmo destino sem produzir arquivos duplicados e o consumidor dos eventos consegue reconhecer a repetição pelo identificador do job (cobre FR-012)

---

### User Story 3 - Contrato de mensagens versionado para jobs, progresso e resultado (Priority: P1)

A equipe que vai construir a `project-api` e o app desktop precisa de um contrato único, escrito e testável, das mensagens que trafegam pela fila, para integrar sem ler o código do worker.

**Why this priority**: O contrato é a única ligação entre o worker (esta feature) e a API (feature futura); definir o formato aqui, com versão, evita retrabalho e permite as duas features avançarem em paralelo (fonte: `docs/roadmap.md`, item 4, justificativa).

**Independent Test**: Validar, sem Docker, mensagens de exemplo de cada tipo contra o contrato; validar que uma mensagem com versão desconhecida é rejeitada com motivo claro.

**Acceptance Scenarios**:

1. **Given** o contrato publicado no repositório, **When** se consulta o documento, **Then** ele descreve os tipos de mensagem job, progresso, resultado e erro, o campo de versão do esquema, o identificador do job e a origem de cada campo no código existente (cobre FR-009)
2. **Given** uma mensagem de job válida, **When** ela é validada, **Then** é aceita e o worker a executa (cobre FR-009)
3. **Given** uma mensagem com versão de esquema desconhecida, **When** o worker a recebe, **Then** ela é rejeitada sem ser executada, vai para a fila de mensagens mortas e o motivo cita a versão recebida e as versões suportadas (cobre FR-009, FR-011)
4. **Given** mensagens de progresso, resultado e erro publicadas pelo worker, **When** elas são validadas contra o contrato, **Then** 100% obedecem ao contrato e carregam o identificador do job que as originou (cobre FR-008, FR-009)
5. **Given** uma mudança futura incompatível do contrato, **When** a versão do esquema é incrementada, **Then** mensagens da versão anterior continuam identificáveis como tal e não são confundidas com as novas (cobre FR-009)

---

### User Story 4 - Proteção de RAM e número de workers medido (Priority: P2)

Quem roda o worker num Mac com pouca memória não quer que vários conversores carregados ao mesmo tempo esgotem a RAM e travem a máquina. O número padrão de workers só pode ser fixado depois de medido o consumo real.

**Why this priority**: O briefing registra o risco de RAM com vários workers em paralelo e deixa o número padrão de workers como item pendente, que depende da RAM disponível (fonte: `docs/briefing.md`, seções 8 e "Itens a Definir"); os modelos ocupam de 1 a 2 GB cada depois de carregados (fonte: `README.md`, parágrafo "Memória"). Prioridade P2 porque o worker funciona com um pool explícito antes de a medição existir.

**Independent Test**: Executar a medição documentada em um Mac real com PDFs de teste, para pools de tamanhos diferentes, e conferir que o relatório da medição existe com números reais; simular pouca memória livre e conferir que o worker não inicia novo conversor.

**Acceptance Scenarios**:

1. **Given** a medição de RAM concluída, **When** se consulta o relatório, **Then** ele registra a máquina usada, os PDFs usados, os tamanhos de pool testados e, para cada um, o consumo de memória observado, e o número padrão de workers é derivado desses números (cobre FR-015)
2. **Given** o relatório de medição existente, **When** o worker é iniciado sem informar o tamanho do pool ou sem `--min-free-memory`, **Then** ele usa os padrões derivados do relatório (`--workers` 4, `--min-free-memory` 4822 MB) e nunca um número fixado sem medição; antes de a medição existir, a recusa com a mensagem "medição pendente" e código de saída 2 valia (cobre FR-001, FR-004, FR-015)
3. **Given** a memória livre abaixo do necessário para carregar mais um conversor, **When** um processo do pool precisaria iniciar uma conversão, **Then** ele não inicia o conversor, não confirma nem consome novo job, avisa em português o motivo e tenta de novo quando houver memória (cobre FR-005)
4. **Given** um conversor ocioso por mais tempo que o limite configurado, **When** o limite é atingido, **Then** o processo de conversão é encerrado e a RAM volta ao sistema, como já ocorre na interface web (cobre FR-005, FR-006)
5. **Given** um pool configurado com N maior do que a RAM suporta, **When** o worker inicia, **Then** ele avisa em português que o valor pode esgotar a memória, e aplica a proteção do cenário 3 em vez de recusar o valor (cobre FR-004, FR-005)

---

### User Story 5 - Encerrar e reconectar sem perder trabalho (Priority: P2)

Quem fecha o app, interrompe o worker com Ctrl+C ou reinicia o RabbitMQ não pode perder jobs nem deixar processos de conversão órfãos consumindo memória.

**Why this priority**: O app desktop vai iniciar e parar o pool de workers como processos auxiliares (fonte: `docs/roadmap.md`, item 5); o worker precisa ser um bom cidadão do ciclo de vida. Depende da P1 para ter o que encerrar.

**Independent Test**: Enviar SIGINT e SIGTERM ao worker durante uma conversão e conferir que o job termina ou volta à fila e que nenhum processo filho permanece; derrubar e subir o RabbitMQ com o worker rodando e conferir que o consumo é retomado sozinho.

**Acceptance Scenarios**:

1. **Given** um worker convertendo um PDF, **When** recebe SIGINT ou SIGTERM, **Then** ele para de consumir novos jobs, conclui o job em andamento dentro de um prazo configurável ou, esgotado o prazo, o devolve à fila sem confirmar, e encerra todos os processos filhos antes de sair (cobre FR-013)
2. **Given** o worker em execução, **When** o RabbitMQ é derrubado e depois sobe de novo, **Then** o worker reconecta sozinho, declara a topologia de novo e volta a consumir, avisando em português durante a indisponibilidade (cobre FR-014)
3. **Given** o RabbitMQ fora do ar quando o comando `artigo2md worker` inicia, **When** o worker tenta conectar, **Then** ele informa em português que o RabbitMQ não está acessível e como subir a infraestrutura, e tenta de novo em intervalos em vez de sair com erro de pilha (cobre FR-014, FR-018)
4. **Given** um job em conversão no momento da queda do broker, **When** o broker volta, **Then** o Markdown atômico já gravado no destino permanece e o job é reentregue e reprocessado de forma idempotente (mesmo destino, sem resultado divergente), sem estado persistente próprio do worker (cobre FR-012, FR-014)

---

### User Story 6 - Isolamento local e compatibilidade com a CLI atual (Priority: P2)

Quem usa só a CLI (`convert`, `doctor`, `serve`) e a pessoa que processa documentos sensíveis precisam da garantia de que a feature não altera o que já funciona e não abre caminho para fora da máquina.

**Why this priority**: A promessa central do projeto é nada ser enviado para a internet (fonte: `README.md:4`) (fonte: `docs/constitution.md`, Princípio I) e o briefing exige que a CLI continue funcionando sem a infraestrutura (fonte: `docs/briefing.md`, item 13 do MVP).

**Independent Test**: Rodar a suíte `uv run pytest` sem Docker e sem RabbitMQ e conferir que passa; rodar `artigo2md convert` num PDF sem a infraestrutura e conferir o mesmo resultado de antes; inspecionar as conexões do worker.

**Acceptance Scenarios**:

1. **Given** o RabbitMQ derrubado e a nova dependência da fila ausente do ambiente de execução do `convert`, **When** `artigo2md convert` roda num PDF, **Then** a conversão termina como antes desta feature, sem tentar conectar a nenhum serviço e sem carregar o módulo da fila (cobre FR-017)
2. **Given** o worker em execução, **When** se inspecionam suas conexões de rede, **Then** a única conexão de rede é com o endereço de loopback `127.0.0.1` na porta do RabbitMQ, e nenhuma conexão sai da máquina (cobre FR-016)
3. **Given** o processo principal do worker, **When** ele inicia e fica ocioso, **Then** nenhum motor pesado (Docling, pymupdf4llm) está carregado nele: só os processos auxiliares de conversão os carregam (cobre FR-006)
4. **Given** a suíte de testes, **When** `uv run pytest` roda sem Docker, **Then** passa; os testes de integração com o RabbitMQ real só rodam quando uma variável de ambiente os habilita (cobre FR-019)
5. **Given** a nova dependência de código para falar com o RabbitMQ, **When** a feature é planejada, **Then** a licença dela é confirmada em fonte oficial e registrada no registro de licenças do repositório antes da adoção, e uma dependência GPL ou AGPL é rejeitada (cobre FR-020)

---

### Edge Cases

- **PDF que desaparece ou é movido entre o enfileiramento e o consumo**: tratado como PDF inexistente, falha definitiva sem retry, com mensagem em português e evento de erro (cobre FR-007, FR-010).
- **Disco de destino sem espaço ou diretório de dados sem permissão de escrita**: falha passageira passível de retry limitado; o Markdown nunca fica gravado pela metade (a gravação publica o arquivo completo ou nada) (cobre FR-007, FR-010).
- **Falha ao publicar o evento de resultado**: o job não é confirmado; é reentregue e reprocessado, e a idempotência da gravação (FR-012) evita resultado divergente (cobre FR-003, FR-012).
- **Processo de conversão morto pelo sistema por falta de memória**: tratado como falha passageira (retry com atraso), reaproveitando o aviso de falta de memória que o `ConversionWorker` já emite (fonte: `src/artigo2md/worker.py`) (cobre FR-005, FR-010).
- **Fila de jobs vazia**: o worker fica ocioso e os conversores ociosos são descarregados após o limite de ociosidade, sem consumir CPU em espera ativa: o slot ocioso bloqueia em espera do broker (`process_data_events` com `time_limit` positivo) e não executa laço sem espera, verificável por contagem de iterações do laço num intervalo com canal falso e, com o broker real, por CPU do processo próxima de zero medida com `psutil` numa janela fixa (cobre FR-005).
- **Mensagem de job com motor inválido**: o motor não pertence ao conjunto aceito pelo roteador; falha definitiva, sem retry, com mensagem em português listando os motores válidos (cobre FR-009, FR-010).
- **Job em duplicidade processado em dois workers simultâneos**: o destino do Markdown é escrito de forma atômica, de modo que o arquivo final é sempre completo e o último a terminar vence (cobre FR-012).
- **Credenciais do RabbitMQ ausentes ou erradas**: o worker informa em português qual variável revisar, nunca imprime o valor da credencial e não entra em laço de reconexão agressivo (cobre FR-014, FR-016).
- **Pool com N igual a 1**: equivale a um consumidor único, com os mesmos controles de RAM e de encerramento (cobre FR-004).
- **Cancelamento de um job**: fora do escopo; o worker não trata cancelamento nem descarta jobs, e um job enfileirado é sempre processado até o fim (cobre FR-021).
- **`job_id` ou nome de PDF que resolveria fora do diretório de dados** (por exemplo, com separadores de caminho ou `..`): falha definitiva sem retry, nada é gravado fora do diretório de dados (cobre FR-022, FR-010).
- **Diretório de dados não informado na inicialização**: o worker recusa iniciar e informa em português a opção que falta (cobre FR-022, FR-018).

## Requirements

### Functional Requirements

- **FR-001**: O sistema MUST fornecer o subcomando `artigo2md worker`, executável nativamente no macOS (para que os motores usem a GPU Apple via MPS e o OCR do Vision via `ocrmac`; fonte: `docs/briefing.md`, seção 4), que conecta ao RabbitMQ da infraestrutura local e consome jobs de conversão até ser encerrado. O subcomando MUST aceitar os parâmetros de configuração desta feature (tamanho do pool, limites de RAM, atraso e número de tentativas do retry, limite de ociosidade, prazo de encerramento, limite por conversão) por opções de linha de comando, com valores padrão documentados, inclusive `--workers` (4) e `--min-free-memory` (4822 MB), cujos padrões vêm do relatório de FR-015 (`ram-measurement.md`; FR-004, FR-005): antes de o relatório existir eles MUST NOT ter padrão e eram obrigatórios.
- **FR-002**: O sistema MUST definir a topologia de mensagens do RabbitMQ composta por: uma exchange de jobs, uma fila de jobs durável, um mecanismo de retry com atraso (a mensagem que falha de forma passageira volta à fila de jobs somente após o atraso configurado) e uma fila de mensagens mortas durável para o que esgotar as tentativas ou for inválido. A declaração da topologia MUST ser idempotente e feita pelo worker ao iniciar e ao reconectar; se já existir uma topologia incompatível, o worker MUST falhar com mensagem clara em português em vez de sobrescrevê-la. Os nomes concretos de exchange e filas são definidos no `plan`, e os eventos (FR-008) trafegam por uma exchange própria, separada da de jobs.
- **FR-003**: O sistema MUST consumir com prefetch 1: cada consumidor mantém no máximo um job não confirmado. A confirmação (ack) MUST ocorrer somente depois de o Markdown estar gravado e o evento de resultado (ou de erro definitivo) estar publicado; antes disso, a perda da conexão do worker MUST fazer o job voltar à fila.
- **FR-004**: O sistema MUST permitir configurar o pool de N processos de conversão; as conversões simultâneas MUST ser no máximo N. O valor padrão de N é 4: foi fixado só depois da medição de RAM de FR-015 e consta no relatório da medição (`ram-measurement.md`, "Derivação dos padrões"); o pool com N igual a 1 MUST ser suportado.
- **FR-005**: O sistema MUST proteger a RAM do computador: antes de iniciar um novo processo de conversão (que carrega modelos de 1 a 2 GB; fonte: `README.md`, "Memória"), o worker MUST verificar se há memória livre suficiente e, se não houver, MUST NOT iniciar o conversor, MUST NOT consumir novo job para ele, MUST avisar em português e tentar de novo depois. O limite de memória livre exigido MUST ser configurável, com padrão derivado da medição de FR-015 (4822 MB; `ram-measurement.md`). O gate MUST ser único por processo e serializado por lock: o slot que passa no gate reserva memória (o valor de `--min-free-memory`) até o conversor terminar de carregar ou falhar, e os demais slots frios descontam a reserva da memória livre; o slot em reconexão não avalia o gate (ao reconectar vai a `cold` e aplica o gate) e o conversor já vivo não reavalia. Os processos de conversão ociosos MUST ser encerrados após um limite de ociosidade configurável, devolvendo a RAM, como o `ConversionWorker` já faz hoje.
- **FR-006**: O sistema MUST reutilizar o pipeline existente em vez de duplicá-lo: a inspeção do PDF (`pdfscan.analyze`), o roteador de motores com avaliação de qualidade e fallback (`router.convert`) e o padrão de processo auxiliar de conversão de `worker.py` (processo iniciado por `spawn`, encerrado após ociosidade). O job MAY escolher um dos motores aceitos pelo roteador; na ausência, MUST valer a escolha automática. O processo principal do worker MUST NOT importar Docling nem pymupdf4llm (fonte: `CLAUDE.md`); só os processos auxiliares os carregam.
- **FR-007**: O worker MUST ler o PDF diretamente do disco do host, a partir do caminho recebido no job, e MUST gravar o Markdown no diretório de dados do app, sem copiar o PDF por meio da fila e sem acessar o banco de dados. A gravação MUST ser atômica (o arquivo final nunca fica incompleto) e o destino MUST ser determinado pelos dados do job (FR-022), de modo que reprocessar o mesmo job regrave o mesmo destino.
- **FR-008**: O worker MUST publicar eventos para cada job: progresso (reaproveitando as mensagens de progresso em português que o roteador já emite via callback), resultado (com o relatório da conversão e o caminho do Markdown gravado) e erro (indicando se a falha é passível de nova tentativa ou definitiva). Todo evento MUST carregar o identificador do job. O worker MUST NOT acessar nenhum banco de dados.
- **FR-009**: O sistema MUST definir e documentar no repositório um contrato de mensagens em JSON, versionado, para os tipos job, progresso, resultado e erro. Toda mensagem MUST ter um campo de versão do esquema e o identificador do job. O contrato MUST ser derivado dos tipos reais já existentes no código (`Analysis`, `Quality`, `Attempt`, `ConversionResult.report()` e o conjunto `router.ENGINES`; fonte: `src/artigo2md/router.py`, `src/artigo2md/pdfscan.py`), sem campos inventados. Uma mensagem inválida, com versão de esquema desconhecida ou com motor inválido MUST ser rejeitada sem execução e encaminhada à fila de mensagens mortas, com o motivo registrado. Os nomes concretos dos campos são definidos no `plan`, no documento de contrato.
- **FR-010**: O sistema MUST distinguir falha passageira de falha definitiva. Falha passageira (por exemplo, processo de conversão encerrado por falta de memória, erro de escrita temporário) MUST gerar retry com o atraso configurável, até o número máximo de tentativas configurável; falha definitiva (PDF inexistente, ilegível, protegido por senha ou inválido, motor inválido, mensagem fora do contrato) MUST ir direto à fila de mensagens mortas, sem retry. Atraso e número máximo de tentativas MUST ter valores padrão documentados, definidos no `plan`.
- **FR-011**: A fila de mensagens mortas MUST preservar a mensagem original, o motivo da falha (em português) e a contagem de tentativas, para inspeção e reenvio manual, e MUST ser durável.
- **FR-012**: O sistema MUST oferecer entrega pelo menos uma vez (at-least-once): um job pode ser entregue mais de uma vez, e o processamento MUST ser idempotente na saída: reprocessar o mesmo job regrava o mesmo destino sem arquivos duplicados e emite eventos com o mesmo identificador, para que o consumidor reconheça a repetição. O worker MUST NOT manter estado persistente próprio para deduplicar.
- **FR-013**: Ao receber SIGINT ou SIGTERM, o worker MUST parar de consumir novos jobs, concluir o job em andamento dentro de um prazo configurável ou, esgotado o prazo, devolvê-lo à fila sem confirmar, e MUST encerrar todos os processos de conversão filhos antes de sair, sem deixar processos órfãos.
- **FR-014**: O worker MUST reconectar sozinho quando o RabbitMQ cair ou estiver fora do ar na inicialização, com intervalos crescentes limitados, declarando a topologia de novo e retomando o consumo, e MUST avisar em português o motivo e como subir a infraestrutura, sem exibir pilha de erro nem a credencial.
- **FR-015**: O sistema MUST entregar, como artefato versionado no repositório, uma medição real do uso de RAM do worker num Mac, feita antes de fixar o número padrão de workers: máquina e memória total, PDFs usados (com perfil: texto nativo, escaneado, com tabelas), tamanhos de pool testados, consumo de memória de cada processo de conversão (ocioso, carregado, no pico) e o comportamento da proteção de RAM de FR-005. O número padrão de N e o limite padrão de memória livre MUST ser derivados desses números, citando-os. Nenhum valor padrão MUST ser fixado sem essa medição.
- **FR-016**: O worker MUST conectar ao RabbitMQ somente no endereço de loopback `127.0.0.1` (o endereço não é parametrizável, como no `doctor`; fonte: `src/artigo2md/infra.py`), MUST NOT enviar telemetria, MUST NOT abrir nenhuma conexão para fora da máquina e MUST NOT imprimir credenciais em mensagens, logs ou eventos.
- **FR-017**: O sistema MUST manter `convert`, `doctor` e `serve` com comportamento idêntico ao atual, sem Docker e sem RabbitMQ: o módulo da fila e a dependência nova MUST NOT ser importados nem exigidos por esses subcomandos.
- **FR-018**: O sistema MUST emitir em português todas as mensagens ao usuário do worker (avisos, erros, progresso, motivo de falha); nomes de campos do contrato, de código e de arquivos seguem em inglês.
- **FR-019**: O sistema MUST cobrir a feature com testes automatizados: contrato, topologia (por mock ou fake do cliente), retry, mensagens mortas, idempotência, proteção de RAM e encerramento MUST rodar sem Docker na suíte `uv run pytest`; os testes de integração com o RabbitMQ real MUST ficar atrás de uma variável de ambiente, conforme o Princípio IV (fonte: `docs/constitution.md`).
- **FR-020**: A dependência de código nova para falar com o RabbitMQ MUST ter licença permissiva (MIT, Apache ou BSD), confirmada em fonte oficial na etapa `plan` e registrada no registro de licenças do repositório; uma dependência GPL ou AGPL MUST ser rejeitada (fonte: `docs/constitution.md`, Princípio II).
- **FR-021**: O cancelamento de job está FORA do escopo desta feature. O contrato de mensagens (FR-009) NÃO MUST ter tipo de mensagem de cancelamento, e o worker MUST NOT implementar descarte nem interrupção de job por cancelamento. A `project-api`/`desktop-processing` acrescentam o tipo por nova versão do esquema, e o worker, ao receber versão desconhecida, aplica FR-009.
- **FR-022**: O diretório de dados do app MUST ser uma opção de linha de comando obrigatória do `artigo2md worker`, sem caminho padrão presumido; sem ela, o worker MUST recusar iniciar com mensagem em português. O Markdown MUST ser gravado em `<diretório de dados>/<job_id>/<nome-do-pdf>.md`, com o nome derivado do arquivo do PDF do job, independente de onde o PDF está. O worker MUST validar o `job_id` e o nome derivado de modo que o destino resolvido fique sempre dentro do diretório de dados (sem travessia de caminho) e, caso contrário, tratar como falha definitiva (FR-010). O worker MUST gravar somente o arquivo Markdown; assets (imagens e arquivos auxiliares) ficam para feature posterior.
- **FR-023**: O worker MUST obter a porta do RabbitMQ pela mesma ordem de resolução que o `doctor` já usa para as portas da stack (variável de ambiente, depois o arquivo indicado, depois `stack/.env`, depois o padrão; fonte: `src/artigo2md/infra.py` e `README.md`). As credenciais MUST vir primeiro das variáveis de ambiente `RABBITMQ_DEFAULT_USER` e `RABBITMQ_DEFAULT_PASS` herdadas do processo que inicia o worker (o app desktop pode fornecê-las ao iniciar o pool) e, na ausência, das mesmas chaves do arquivo de parâmetros da stack, na mesma ordem de resolução do `doctor` (fonte: `stack/.env.example`). Sem credenciais, o worker MUST informar em português apenas o nome das variáveis a revisar, nunca o valor (FR-016).

- **FR-024**: Depois de lidas, as credenciais do RabbitMQ MUST ficar só em `WorkerSettings` (com `repr` mascarado) e as variáveis `RABBITMQ_DEFAULT_USER` e `RABBITMQ_DEFAULT_PASS` MUST ser removidas de `os.environ` do processo principal antes de qualquer processo filho `spawn` iniciar (o filho herda o ambiente), de modo que os processos de conversão (Docling, pymupdf4llm) nunca as enxerguem.
- **FR-025**: O subcomando MUST aceitar a opção opcional `--conversion-timeout` (segundos; padrão `0` = sem limite). Ao exceder o limite, o worker MUST encerrar o processo de conversão e tratar a falha como passageira (código `conversion_timeout`, mesmo retry de FR-010). Quando usado, o limite MUST ser menor que o prazo do broker para o `ack` (30 min no RabbitMQ 4.4.1 da stack; `research.md` Decision 11).

> Decisões de infraestrutura: idempotência tratada em FR-012; scheduling periódico, rotação de chaves, refresh de tokens externos, mutex multi-pod e backup não se aplicam (worker local de um só usuário, sem réplicas e sem dados persistentes próprios além do Markdown gravado). A persistência dos resultados no Postgres é responsabilidade da `job-store`/`project-api`.

### Key Entities

- **Job de conversão**: pedido de conversão de um PDF: identificador do job, caminho do PDF no disco do host, motor desejado (entre os aceitos pelo roteador, ou escolha automática) e contagem de tentativas; é a mensagem de entrada do worker.
- **Evento de progresso**: mensagem curta em português sobre a etapa da conversão de um job, com o identificador do job.
- **Evento de resultado**: conclusão de um job: caminho do Markdown gravado, motor usado e o relatório da conversão (análise do PDF, tentativas com nota de qualidade e avisos).
- **Evento de erro**: falha de um job, com o motivo em português e a indicação de passível de nova tentativa ou definitiva.
- **Contrato de mensagens**: documento versionado que define tipos, campos e versão do esquema das mensagens acima.
- **Topologia de mensagens**: conjunto de exchanges e filas (jobs, retry com atraso, mensagens mortas, eventos) declarado de forma idempotente pelo worker.
- **Pool de workers**: o conjunto de N processos de conversão do comando `artigo2md worker`, com a proteção de RAM.
- **Relatório de medição de RAM**: artefato que registra a máquina, os PDFs, os tamanhos de pool testados e o consumo real, e justifica o número padrão de workers.

## Success Criteria

### Measurable Outcomes

- **SC-001**: Com um lote de 10 PDFs de teste e um pool de N processos, 100% dos PDFs válidos geram Markdown e um evento de resultado, e o número máximo de conversões simultâneas observado nunca passa de N.
- **SC-002**: Em 10 repetições de encerramento forçado do worker no meio de uma conversão, 100% dos jobs são reprocessados até o fim após o reinício, sem nenhum job perdido e sem nenhum Markdown incompleto no destino.
- **SC-003**: Numa falha passageira simulada, 100% dos jobs voltam à fila somente depois do atraso configurado e terminam com sucesso na tentativa seguinte; numa falha definitiva (PDF inválido, mensagem fora do contrato ou versão desconhecida), 100% vão à fila de mensagens mortas sem nenhuma nova tentativa, com o motivo registrado.
- **SC-004**: O relatório de medição de RAM existe no repositório, com números reais obtidos por execução em um Mac para pelo menos três tamanhos de pool distintos, e o número padrão de workers coincide com o derivado desses números; nenhum número padrão aparece fixado antes do relatório.
- **SC-005**: Com a memória livre simulada abaixo do limite, 0 novos processos de conversão são iniciados e o aviso em português aparece em 100% dos casos.
- **SC-006**: 100% das mensagens publicadas pelo worker (progresso, resultado e erro) passam na validação do contrato versionado, e 100% das mensagens com versão de esquema desconhecida são rejeitadas sem execução.
- **SC-007**: Com o RabbitMQ derrubado e subido de novo com o worker em execução, o consumo é retomado sem intervenção manual em até 1 minuto após o broker voltar, e nenhuma mensagem confirmada é perdida.
- **SC-008**: Após SIGINT ou SIGTERM, 0 processos de conversão filhos permanecem ativos depois de o worker sair, e o job em andamento termina ou volta à fila em 100% dos casos.
- **SC-009**: Sem Docker nem RabbitMQ, `uv run pytest` passa e o `artigo2md convert` produz o mesmo resultado que antes desta feature em 100% dos casos testados; os testes de integração com o broker real passam quando habilitados pela variável de ambiente.
- **SC-010**: Zero conexões do worker para endereços fora de `127.0.0.1` durante a execução de um lote completo, e zero credenciais nas mensagens, nos logs e nos eventos.

## Delta Requirements

**Skip**: o corpus canônico `docs/specs/current/` não existe neste projeto, e a feature acrescenta um subcomando e um módulo novos sem alterar o comportamento de `convert`, `doctor` ou `serve` — feature-00c, 2026-11-24
