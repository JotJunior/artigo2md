# Research: Fila de conversão e worker nativo

**Feature**: `conversion-queue` | **Date**: 2026-11-24 | **Spec**: [spec.md](./spec.md)

> Convenção deste documento: **[FONTE]** = fato lido em fonte rastreável (citada); **[PROPOSTA]** =
> decisão de design desta feature (nome, valor de política, estrutura), legítima porque o artefato ainda
> não existe; **[A VERIFICAR]** = afirmação sobre software de terceiros que NÃO pôde ser conferida em
> fonte oficial nesta execução (o acesso à rede do agente foi bloqueado pelo guard de comandos) e que
> vira critério de aceite de uma task (leitura da fonte oficial ou teste de integração com o broker real).

## Fontes consultadas

| ID | Fonte | O que foi lido |
|----|-------|----------------|
| L01 | `src/artigo2md/router.py` | `ENGINES = ("auto", "docling", "pymupdf", "mineru", "markitdown")`; dataclasses `Quality`, `Attempt`, `ConversionResult.report()` (remove `markdown`); `convert()` levanta `ValueError` para motor inválido e `RuntimeError("Nenhum motor conseguiu converter ...")` quando todos falham; mensagens de progresso `"Inspecionando o PDF"`, `"Convertendo com {name}"`, `"{name} não passou na avaliação (...)"` |
| L02 | `src/artigo2md/pdfscan.py` | `InvalidPdfError(ValueError)` com mensagens em português (senha, formato, ilegível); `Analysis.to_dict()` com `pages, text_pages, scanned_pages, total_chars, table_hints, formula_score, article_score, fully_scanned` |
| L03 | `src/artigo2md/worker.py` | `ConversionWorker(idle_timeout=300)`, contexto `spawn`, reaper de ociosidade, protocolo do pipe `("progress", m)`, `("done", {...})`, `("error", str)`; `EOFError/OSError` no pipe vira `RuntimeError("O processo de conversão terminou inesperadamente (possível falta de memória)...")`; o filho envia só `str(exc)`, sem o tipo |
| L04 | `src/artigo2md/infra.py` | `HOST = "127.0.0.1"`; `resolve_ports()` na ordem ambiente → `ARTIGO2MD_STACK_ENV` → `./stack/.env` → padrão; `SERVICES["rabbitmq"] = ("ARTIGO2MD_RABBITMQ_PORT", 5672)`; `parse_env_file()` sem `eval` |
| L05 | `src/artigo2md/cli.py` | subcomandos `convert`, `serve`, `doctor`; `serve --data-dir` (padrão `~/artigo2md-dados`) e `--idle-timeout` (padrão `300`); `doctor` importa `infra` localmente |
| L06 | `src/artigo2md/web.py:24` | `from .router import ENGINES` no processo principal (importar `router` não carrega Docling: `engines.py` importa Docling só dentro das funções) |
| L07 | `stack/compose.yaml` | serviço `rabbitmq`, imagem `rabbitmq:4.4-management`, porta `127.0.0.1:${ARTIGO2MD_RABBITMQ_PORT}:5672`, `RABBITMQ_DEFAULT_USER/PASS` obrigatórios; nenhum `RABBITMQ_DEFAULT_VHOST` definido |
| L08 | `stack/.env.example` | chaves `ARTIGO2MD_RABBITMQ_PORT`, `RABBITMQ_DEFAULT_USER`, `RABBITMQ_DEFAULT_PASS` |
| L09 | `stack/THIRD_PARTY_LICENSES.md` | registro de licenças existente (formato: componente, licença como na fonte, Princípio II, fonte oficial, data) |
| L10 | `tests/test_stack_integration.py:36-38` | integração com Docker real atrás de `ARTIGO2MD_STACK=1` |
| L11 | `uv.lock` | `psutil` 7.2.2 já resolvido como dependência transitiva (de `accelerate`) |
| L12 | `psutil-7.2.2.dist-info/METADATA` (wheel resolvido pelo `uv.lock`, cache local do uv) | `License: BSD-3-Clause` |
| L13 | `psutil/__init__.py` 7.2.2 (mesmo wheel) | `virtual_memory().available` = "memory that can be given instantly to processes without the system going into swap"; `Process.memory_full_info()` traz USS no macOS; `Process.children()` |
| L14 | `docs/specs/container-stack/research.md` S06 | série RabbitMQ 4.4 em suporte comunitário |
| L15 | `https://pypi.org/pypi/pika/json` — informado pelo operador (resposta ao `block-001`, consulta dele em 2026-11-24) | campo `license` = `BSD-3-Clause`, versão 1.5.0 |
| L16 | `https://github.com/pika/pika`, arquivo LICENSE — informado pelo operador (resposta ao `block-001`) | texto BSD 3-clause, "Copyright 2009-2021 Tony Garnock-Jones, Gavin M. Roy, Pivotal Software, Inc and others" |
| L17 | imagem local `rabbitmq:4.4-management` (RabbitMQ 4.4.1), lida em 2026-11-25 sem rede (`docker run --rm --network none --entrypoint sh ... rabbitmqctl --version` e leitura de `/opt/rabbitmq/plugins/rabbit-4.4.1/ebin/rabbit.app` e `priv/schema/rabbit.schema`) | `rabbitmqctl --version` = `4.4.1`; `{consumer_timeout, 1800000}` (comentário "30 minutes", referência `https://www.rabbitmq.com/docs/consumers#acknowledgement-timeout`); `consumer_timeout_response = notify_consumer`; descrição do schema: "Approximate maximum time a consumer can spend processing a message before the channel is terminated, in milliseconds" |
| L18 | `os.pathconf('/', 'PC_PATH_MAX')` e `'PC_NAME_MAX'` no macOS da execução (2026-11-25) | `1024` e `255` |

## Decision 1: Biblioteca cliente AMQP 0-9-1 — `pika` 1.5.0 (licença BSD-3-Clause)

- **Decision** [PROPOSTA]: usar `pika` (cliente AMQP 0-9-1 síncrono, `BlockingConnection`), uma conexão por
  slot do pool, cada uma numa thread própria do processo principal. Entra como **extra opcional** `queue`
  do `pyproject.toml`, nunca exigido por `convert`/`doctor`/`serve` (FR-017).
- **Licença**: `BSD-3-Clause` [FONTE: L15, L16] — permissiva (Princípio II, FR-020). A consulta direta à
  rede foi bloqueada pelo guard desta execução; a licença foi informada pelo operador em resposta ao
  bloqueio humano `block-001`, citando os metadados oficiais do PyPI (versão 1.5.0, consulta em
  2026-11-24) e o LICENSE do repositório oficial. O agente não conferiu essas fontes por conta própria.
- **Versão**: `pika` ainda NÃO está no `pyproject.toml` nem no `uv.lock` (verificado por grep na retomada
  da etapa `plan`); nenhuma versão havia sido fixada antes nos artefatos desta feature. Para casar com a
  versão cuja licença foi verificada, o extra `queue` fixa `pika==1.5.0` [PROPOSTA]. Qualquer outra versão
  (upgrade ou a resolvida pelo `uv lock`, se divergir) exige nova verificação de licença em fonte oficial
  e registro antes da adoção. Critério de aceite da task de adoção: `uv.lock` resolve `pika` 1.5.0 e a
  linha correspondente entra em `stack/THIRD_PARTY_LICENSES.md` (fonte oficial + data). Compatibilidade
  da 1.5.0 com Python `>=3.10` [A VERIFICAR na task de adoção, pelo `uv lock`].
- **Rationale**: o worker já é síncrono e baseado em processos (`ConversionWorker`, L03); um cliente
  bloqueante numa thread por slot reaproveita esse modelo sem introduzir asyncio. Pika não declara
  dependências de runtime [A VERIFICAR nos metadados oficiais junto com a licença] — menos licenças a auditar.
- **Pontos de API usados** [A VERIFICAR contra o código/docs oficiais do pika na task de adoção]:
  `BlockingConnection`, `ConnectionParameters(host, port, virtual_host, credentials, heartbeat)`,
  `PlainCredentials`, `channel.basic_qos(prefetch_count=1)`, `channel.confirm_delivery()`,
  `basic_publish(..., mandatory=True)` levantando erro em mensagem não roteável/nack,
  `connection.process_data_events(time_limit=...)` para manter o heartbeat durante a conversão,
  `basic_ack`/`basic_nack`/`basic_cancel`, exceções de conexão (`AMQPConnectionError`).
- **Alternatives considered**:
  - B: `aio-pika` (asyncio). Heartbeat resolvido pelo event loop, mas traz cadeia de dependências
    (`aiormq`, `pamqp`, `yarl`, ...) com licenças a verificar uma a uma e exige reescrever o loop em asyncio.
  - C: `kombu`. Abstração multi-broker desnecessária para um único broker local.
  - D: implementar AMQP 0-9-1 à mão (como o handshake do `doctor`). Rejeitado: confirms, heartbeat e
    reconexão são complexos demais para reimplementar com qualidade.

## Decision 2: Memória livre e medição — `psutil` (licença verificada)

- **Decision**: `psutil` como dependência do extra `queue`, importado só pelo módulo da fila.
  `psutil.virtual_memory().available` para o gate de RAM (FR-005) e `Process(pid).memory_full_info()`
  (USS) / `memory_info().rss` para a medição (FR-015) [FONTE: L13].
- **Licença**: `BSD-3-Clause` [FONTE: L12, metadados do pacote 7.2.2 resolvido no `uv.lock`] — permissiva.
- **Rationale**: já está no lockfile (L11), então não amplia o conjunto instalado; `available` é a métrica
  documentada para "memória que pode ser dada sem swap" (L13).
- **Alternatives considered**: parsear `vm_stat` via subprocess (frágil, só macOS, sem testes portáveis);
  `os.sysconf` (dá o total, não a memória disponível).

## Decision 3: Modelo de concorrência — N slots = N threads × (1 conexão + 1 `ConversionWorker`)

- **Decision** [PROPOSTA]: o processo principal de `artigo2md worker` cria N slots. Cada slot é uma
  thread com sua própria conexão/canal AMQP (`prefetch_count=1`) e seu próprio `ConversionWorker`
  (processo filho `spawn`, L03). Máximo de N conversões simultâneas por construção (FR-003/FR-004).
- **Heartbeat durante conversões longas**: `ConversionWorker.convert` ganha um parâmetro opcional
  `tick: Callable[[], None] | None`; enquanto espera o filho, usa `conn.poll(intervalo)` e chama `tick()`
  entre esperas. O slot passa `tick = lambda: connection.process_data_events(time_limit=0)`. A interface
  web continua chamando sem `tick` (comportamento idêntico).
- **Rationale**: reaproveita o padrão de processo auxiliar sem duplicá-lo (FR-006); clientes bloqueantes
  não são thread-safe entre threads [A VERIFICAR], por isso uma conexão por thread.
- **Alternatives**: `multiprocessing.Pool` com conexão única (o canal teria de ser compartilhado e o ack
  sairia de outro processo); um consumidor com prefetch N (viola "cada processo nunca segura mais de um
  job", US1-2).

## Decision 4: Classificação de falhas (passageira × definitiva)

- **Decision** [PROPOSTA]: o filho de `worker.py` passa a enviar o tipo da falha: `("error", msg)` continua
  para falha genérica e surge `("invalid_pdf", msg)` quando a exceção é `InvalidPdfError` (L02). No pai,
  `ConversionWorker.convert` levanta `ConversionError(RuntimeError)` com atributo `kind` (`"invalid_pdf"`,
  `"error"`, o mesmo tipo da mensagem do filho) e `WorkerCrashed(RuntimeError)` para a morte do filho (mensagem atual preservada, L03).
  Como ambas são subclasses de `RuntimeError`, `web.py` (que captura `Exception`) não muda.
- Validações feitas **no processo principal, antes de tocar o filho** (falha definitiva, sem retry):
  JSON inválido, fora do contrato, `schema_version` desconhecida, motor fora de `router.ENGINES`,
  `job_id`/nome que escapariam do diretório de dados, PDF inexistente ou que não é arquivo regular.
- Tabela final de códigos: ver `contracts/queue-messages.md` §Códigos de erro.

## Decision 5: Topologia e retry com atraso

- **Decision** [PROPOSTA — nomes e estrutura são design desta feature]:

| Objeto | Tipo | Durável | Argumentos | Função |
|--------|------|---------|------------|--------|
| `artigo2md.jobs` | exchange `direct` | sim | — | entrada de jobs (routing key `convert`) |
| `artigo2md.jobs.convert` | fila clássica | sim | `x-queue-type=classic` | jobs a converter |
| `artigo2md.retry` | exchange `direct` | sim | — | jobs aguardando nova tentativa |
| `artigo2md.jobs.retry` | fila clássica | sim | `x-queue-type=classic`, `x-dead-letter-exchange=artigo2md.jobs`, `x-dead-letter-routing-key=convert` | espera do retry; a mensagem expira e volta à fila de jobs |
| `artigo2md.dead` | exchange `direct` | sim | — | mensagens mortas |
| `artigo2md.jobs.dead` | fila clássica | sim | `x-queue-type=classic` | inspeção e reenvio manual (FR-011) |
| `artigo2md.events` | exchange `topic` | sim | — | eventos (`job.progress`, `job.result`, `job.error`), separada da de jobs (FR-002) |
| `artigo2md.events.all` | fila clássica | sim | `x-queue-type=classic` | binding `job.#`; guarda os eventos até a `project-api` consumi-los |

- **Bindings** [PROPOSTA]: `artigo2md.jobs.convert` ← `artigo2md.jobs` (routing key `convert`);
  `artigo2md.jobs.retry` ← `artigo2md.retry` (`convert`); `artigo2md.jobs.dead` ← `artigo2md.dead` (`dead`);
  `artigo2md.events.all` ← `artigo2md.events` (`job.#`). Constantes únicas em `jobqueue/topology.py`.
- **Retry**: em falha passageira o slot publica uma cópia do job com `attempt + 1` em `artigo2md.retry`
  com a propriedade por mensagem `expiration = retry_delay_ms` (não TTL da fila, para que mudar
  `--retry-delay` não gere topologia incompatível), espera o confirm e só então faz `ack` do original.
- **Semântica do broker** [CONFIRMADA no broker real, tarefa 6.1.3; ver "Verificação no broker real"]: expiração por mensagem + `x-dead-letter-exchange` devolvendo a
  mensagem à exchange de jobs; mensagens expiradas só são removidas ao chegar à cabeça da fila (com atraso
  único por configuração, a ordem FIFO não atrasa ninguém além do configurado; dois workers com
  `--retry-delay` diferentes podem atrasar um ao outro — limitação documentada).
- **Mensagens mortas**: publicação explícita pelo worker em `artigo2md.dead` com o **corpo original byte a
  byte** e cabeçalhos com motivo, contagem de tentativas, código e horário (FR-011) — o dead-lettering
  nativo do broker não carregaria o motivo em português.
- **Topologia incompatível**: a declaração usa os mesmos argumentos sempre; se o broker recusar por
  divergência (canal fechado com erro de pré-condição 406 [CONFIRMADO no broker real, tarefa 6.1.4]), o
  worker sai com mensagem em português citando o objeto, sem tentar apagar/sobrescrever (FR-002).
- **Fila clássica, não quorum**: broker local de nó único, sem replicação; evita o limite de entregas e as
  regras de dead-lettering próprias de filas quorum [A VERIFICAR se quiser reavaliar].
- **Alternatives**: plugin de mensagens atrasadas (dependência extra no broker, imagem sem modificação
  exigida pelo Princípio II); TTL por fila (muda com a configuração → conflito de topologia);
  `basic_nack(requeue=True)` imediato (retry sem atraso, laço quente).

## Decision 6: Diretório de dados e travessia de caminho (FR-022)

- **Decision** [PROPOSTA]:
  - `job_id` deve casar `^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$` (UUID cabe; sem `.`, `/`, `\`, espaço,
    NUL — logo `.` e `..` são impossíveis).
  - nome = `Path(pdf_path).stem` (só o componente final); rejeitado se vazio, se começar por `.`, se tiver
    NUL, `/` ou `\`, ou se `stem + ".md"` exceder o limite de nome do sistema de arquivos (erro
    `ENAMETOOLONG` na gravação também vira falha definitiva).
  - `data_dir` é resolvido (`Path.resolve(strict=True)`) na inicialização; deve existir e ser diretório
    gravável, senão o worker recusa iniciar (mensagem em português).
  - destino = `data_dir / job_id / (stem + ".md")`; após criar `data_dir / job_id`, o diretório é
    resolvido de novo e precisa ter `parent == data_dir` resolvido (bloqueia symlink plantado em
    `<job_id>` apontando para fora).
  - gravação atômica: arquivo temporário oculto no mesmo diretório, `flush` + `os.fsync`, `os.replace`
    para o destino; temporário removido em qualquer erro. Reprocessar o mesmo job regrava o mesmo destino
    (FR-012); o último a terminar vence.
- **Rationale**: validação por allowlist e verificação após `resolve()` cobrem `..`, separadores, caminhos
  absolutos e symlinks.

## Decision 7: Valores padrão de política [PROPOSTA]

| Parâmetro | Opção | Padrão | Origem |
|-----------|-------|--------|--------|
| Diretório de dados | `--data-dir` | **obrigatório, sem padrão** | FR-022 |
| Tamanho do pool | `--workers` | **4** (tarefa 7.3: derivado de `ram-measurement.md`; era obrigatório até existir a medição) | FR-004/FR-015 |
| Memória livre mínima para iniciar conversor | `--min-free-memory` (MB) | **4822** (tarefa 7.3: derivado de `ram-measurement.md`; era obrigatório até existir a medição) | FR-005/FR-015 |
| Atraso do retry | `--retry-delay` (s) | 30 | PROPOSTA |
| Tentativas máximas (inclui a 1ª) | `--max-attempts` | 3 | PROPOSTA |
| Ociosidade do conversor | `--idle-timeout` (s) | 300 | mesmo padrão do `serve` (L05) |
| Prazo de encerramento | `--shutdown-timeout` (s) | 30 | PROPOSTA |
| Limite por conversão | `--conversion-timeout` (s) | 0 (sem limite) | PROPOSTA; Decision 11 |
| Reconexão | interno | 1 s dobrando até 15 s | PROPOSTA; teto < 60 s para cumprir SC-007 |
| Reavaliação da memória | interno | a cada 10 s | PROPOSTA |

Os dois números acima foram fixados só depois da medição (tarefa 7.3, regra da Decision 8, `ram-measurement.md`); antes dela este plano não fixava nenhum (FR-015, SC-004).

## Decision 8: Procedimento de medição de RAM (FR-015)

- **Decision** [PROPOSTA]: script `tools/measure_worker_ram.py` (fora do pacote instalado), executado
  manualmente num Mac real com a stack no ar. Para cada tamanho de pool em {1, 2, 3} (mínimo de três,
  SC-004; acrescentar 4 se a máquina tiver memória para isso), publica o mesmo lote de PDFs, inicia
  `artigo2md worker --workers N ...` e amostra a cada 0,5 s, com `psutil`, o RSS e o USS de cada processo
  filho (`Process.children()`), mais `virtual_memory().available` e `.total` [FONTE: L13]. Registra por
  processo: ocioso (após spawn, antes do 1º job), carregado (após o 1º job), pico; e o comportamento do gate
  de memória (quantas vezes adiou iniciar conversor).
- **Lote**: pelo menos um PDF de texto nativo, um escaneado e um com tabelas, identificados por nome,
  páginas e SHA-256 no relatório (nenhum PDF de usuário é versionado).
- **Máquina**: modelo e memória via `sysctl -n hw.model hw.memsize`, versão via `sw_vers` [CONFIRMADO no Mac da medição, tarefa 7.2: `hw.model`, `hw.memsize` e `sw_vers`
  responderam com os valores registrados em `ram-measurement.md`].
- **Saída**: `docs/specs/conversion-queue/ram-measurement.md` com os números brutos e a derivação.
- **Regra de derivação** (forma fixada aqui; constantes só com os números):
  `min_free_memory = ceil(pico_USS_por_conversor × margem)` e
  `workers_padrão = max(1, floor((memória_total − reserva_do_sistema) / pico_USS_por_conversor))`,
  com `margem` e `reserva_do_sistema` justificadas pelos números medidos. Só então os padrões entram no
  código e na ajuda do comando, citando o relatório.

## Decision 9: Credenciais, porta e vhost

- Porta: `infra.resolve_ports()["rabbitmq"]` (L04) — mesma ordem do `doctor` (FR-023).
- Credenciais: `RABBITMQ_DEFAULT_USER`/`RABBITMQ_DEFAULT_PASS` do ambiente; na ausência, das mesmas chaves
  do arquivo de `ARTIGO2MD_STACK_ENV`, depois `./stack/.env`, via `infra.parse_env_file` (L04, L08).
- Host fixo `infra.HOST` (`127.0.0.1`), sem opção (FR-016).
- Vhost: o padrão do broker (`/`); o compose não define `RABBITMQ_DEFAULT_VHOST` (L07) [CONFIRMADO no broker
  real, tarefa 6.1.4: a API de gestão lista o vhost `/`].
- Credenciais nunca vão para mensagens, logs, eventos nem `repr` de objetos de configuração.
- **Credenciais não chegam aos filhos** (tarefa 1.4, FR-024): depois de lidas, ficam só em `WorkerSettings`
  (`repr` mascarado) e `RABBITMQ_DEFAULT_USER`/`RABBITMQ_DEFAULT_PASS` são **removidas de `os.environ`** do
  processo principal **antes** de qualquer processo filho `spawn` ser iniciado (o filho herda o ambiente do
  pai no momento do `spawn`). Aceite: sonda em subprocesso iniciado pelo `ConversionWorker` não enxerga as duas
  variáveis (implementação em 3.3.4, teste em 5.2.6).

## Decision 11: Prazo do broker para o `ack` e limite por conversão (tarefa 1.6)

- **Fato** [FONTE: L17]: no RabbitMQ 4.4.1 da imagem da stack, `consumer_timeout` padrão = **1800000 ms (30 min)**:
  tempo máximo aproximado que um consumidor pode gastar com uma mensagem antes de o **canal ser terminado**.
  Lido do `rabbit.app`/`rabbit.schema` embarcados na imagem, não de documentação on-line (sem acesso à rede nesta
  execução); a referência de documentação on-line citada no próprio arquivo é
  `https://www.rabbitmq.com/docs/consumers#acknowledgement-timeout` (não conferida).
- **Decision** [PROPOSTA]: opção opcional `--conversion-timeout` (segundos; padrão `0` = sem limite, para não
  fixar número sem medição). Ao estourar, `ConversionWorker` encerra o filho e levanta `ConversionTimeout`
  (código `conversion_timeout`, passageira, mesmo retry). Quando usado, deve ser **menor** que o prazo do broker
  (30 min por padrão). Sem limite do worker, um job acima de 30 min perde o canal e é reentregue (at-least-once).
- **Heartbeat**: independe do prazo do broker; o slot mantém `process_data_events` a cada `tick` (Decision 3).

## Decision 10: Validação do contrato sem dependência nova

- **Decision**: validador escrito à mão em `jobqueue/contract.py` (só biblioteca padrão), com testes que
  validam os exemplos do contrato e todas as mensagens emitidas pelo worker (SC-006). Sem `jsonschema`
  (evita outra licença e outra dependência). Limites de entrada (tarefa 1.5): corpo do job até 16384 bytes e
  profundidade JSON até 8, verificados **antes** de `json.loads` (ver `contracts/queue-messages.md`, §`job`).
  Limite de nome de arquivo do sistema: 255 bytes [FONTE: L18].

## Verificação da API do `pika` 1.5.0 (tarefa 2.2, 2026-11-25)

Fonte: código do pacote instalado do `uv.lock` (`.venv/lib/python3.12/site-packages/pika/`, versão `1.5.0`,
`pika.__version__`), lido com `inspect.signature` e leitura dos arquivos `adapters/blocking_connection.py`,
`exceptions.py` e `spec.py`. Os itens marcados **[A CONFIRMAR NO BROKER]** só se comprovam na integração 6.1 (resolvidos em "Verificação no broker real", abaixo).

| Ponto de API (Decision 1) | Resultado | Fonte (arquivo) |
|---|---|---|
| `BlockingConnection(parameters)` | existe; **emite `DeprecationWarning`** na criação: "BlockingConnection is deprecated and will be removed in Pika 2.0. Use Connection instead, which runs its own IOLoop on a background thread and provides a thread-safe blocking API that does not stall heartbeats on slow message processing" | `adapters/blocking_connection.py` (`__init__`) |
| `ConnectionParameters(host, port, virtual_host, credentials, heartbeat, connection_attempts, retry_delay, socket_timeout, ...)` | assinatura confere; há também `max_inbound_message_body_size` | `connection.py` (`inspect.signature`) |
| `PlainCredentials(username, password, erase_on_connect=False)` | confere | `credentials.py` |
| `channel.basic_qos(prefetch_size=0, prefetch_count=0, global_qos=False)` | confere | `blocking_connection.py` |
| `channel.confirm_delivery()` | existe; liga o modo de confirmação do publicador | idem |
| `channel.basic_publish(exchange, routing_key, body, properties=None, mandatory=False)` | em modo de confirmação levanta `UnroutableError` (devolvida por `Basic.Return` + `Ack`) e `NackError` (`Basic.Nack`); `mandatory` sem confirmação não tem como aguardar o `Basic.Return` | idem (docstring) |
| `connection.process_data_events(time_limit=0)` | confere (`time_limit: float | None`); `None` bloqueia até haver evento | idem |
| `basic_ack(delivery_tag=0, multiple=False)`, `basic_nack(delivery_tag=0, multiple=False, requeue=True)`, `basic_cancel(consumer_tag)`, `basic_consume(queue, on_message_callback, auto_ack=False, exclusive=False, consumer_tag=None, arguments=None)` | conferem | idem |
| `exchange_declare(exchange, exchange_type, passive, durable, auto_delete, internal, arguments)`, `queue_declare(queue, passive, durable, exclusive, auto_delete, arguments)`, `queue_bind(queue, exchange, routing_key, arguments)` | conferem | idem |

**Exceções reais (2.2.2)**

- Broker inacessível: `pika.exceptions.AMQPConnectionError` — **comprovado** conectando a `127.0.0.1:1`
  (`ConnectionRefusedError` embrulhado em `AMQPConnectorSocketConnectError`; o `repr` traz host e porta, não credencial).
- Credenciais recusadas: `ProbableAuthenticationError` ("probable authentication error") e `AuthenticationError`
  (subclasses de `AMQPConnectionError`); vhost negado: `ProbableAccessDeniedError` — [CONFIRMADO na tarefa 6.1.4:
  o RabbitMQ 4.4.1 produz `ProbableAuthenticationError` para usuário/senha errados]; o código de saída 4 cobre as duas.
- Erro de pré-condição de topologia divergente (código de saída 5): `pika.exceptions.ChannelClosedByBroker`
  com `reply_code == 406` (`pika.spec.PRECONDITION_FAILED`, `spec.py`) e `reply_text`; o canal fecha e é preciso
  abrir outro. [CONFIRMADO na tarefa 6.1.4: texto e objeto citado conferidos no broker real].
- Publicação não confirmada/não roteável: `NackError` e `UnroutableError` (ambas `AMQPChannelError`); perda de
  conexão no meio: `StreamLostError`/`ConnectionClosed` (`AMQPConnectionError`); heartbeat vencido: `AMQPHeartbeatTimeout`.

**Uma conexão por thread (2.2.3)**: a docstring de `BlockingConnection.add_callback_threadsafe` afirma "This is the
only thread-safe method in `BlockingConnection`. All other methods are not thread-safe" — a regra da Decision 3 está
**confirmada**: cada slot tem a própria `BlockingConnection`/canal, na sua thread.

**Achado — `BlockingConnection` obsoleta (risco novo, a revisar pelo operador)**: o pika 1.5.0 já avisa que
`BlockingConnection` será removida no Pika 2.0 e recomenda `pika.adapters.thread_safe_connection.Connection`
(arquivo presente no pacote), que roda o próprio IOLoop em thread de fundo e não deixa o heartbeat parar durante
processamento lento. Decisão desta feature [PROPOSTA]: manter `BlockingConnection` com a versão **fixada** em
`1.5.0` (o upgrade para 2.0 já exige nova verificação de licença, Decision 1) e filtrar esse `DeprecationWarning`
no módulo da fila para não poluir o log do worker; o `tick` da Decision 3 cobre o heartbeat. Se o operador
preferir o adaptador thread-safe, a mudança fica confinada a `jobqueue/slot.py`/`service.py` e à Decision 3.


## Verificação no broker real (tarefa 6.1, 2026-11-25)

Fonte: `ARTIGO2MD_STACK=1 uv run pytest tests/test_jobqueue_integration.py` contra a imagem `rabbitmq:4.4-management`
(só o serviço `rabbitmq` da stack de teste, em loopback; o worker como processo real, com o motor `markitdown`).
Cada linha abaixo é a leitura de uma execução de fato, não da documentação.

| Item [A VERIFICAR / A CONFIRMAR NO BROKER] | Resultado | Teste |
|---|---|---|
| Expiração por mensagem + `x-dead-letter-exchange` devolve o job à fila de jobs (RabbitMQ 4.4) | **Confirmado**: com `--retry-delay 3` a cópia `attempt=2` ficou em `artigo2md.jobs.retry` (1 mensagem) e o `result` da tentativa 2 veio ≥ 2,5 s depois do `error` da tentativa 1 | `test_scenario_3_transient_failure_returns_after_the_delay_and_ends_with_result` |
| Vhost padrão é `/` | **Confirmado**: `GET /api/vhosts` lista `/` e as 4 filas da feature estão em `/api/queues/%2F`; a conexão do worker usa `virtual_host="/"` | `test_vhost_default_and_precondition_error_with_a_divergent_topology` |
| Erro de pré-condição com topologia divergente | **Confirmado**: `pika.exceptions.ChannelClosedByBroker`, `reply_code == 406`, `reply_text` = `PRECONDITION_FAILED - inequivalent arg 'x-queue-type' for queue 'artigo2md.jobs.convert' in vhost '/': received 'quorum' but current is 'classic'`. O objeto citado é o da declaração que falhou; `topology.declare` converte para `TopologyMismatch` (objeto `artigo2md.events.all` no teste) e o worker sai com código 5 sem alterar o broker | idem; `test_worker_exits_5_on_a_divergent_topology_and_changes_nothing` |
| Exceção para credenciais recusadas | **Confirmado**: `pika.exceptions.ProbableAuthenticationError` (subclasse de `AMQPConnectionError`), com `ConnectionClosedByBroker: (403) 'ACCESS_REFUSED - Login was refused using authentication mechanism PLAIN...'` no texto; `AuthenticationError` não foi observada, mas o reconhecimento cobre as duas e `ProbableAccessDeniedError`. O worker sai com código 4 e a mensagem não cita a senha | `test_refused_credentials_exit_4_and_the_real_exception_is_recognized` |
| `UnroutableError`/`NackError` com `mandatory` e `confirm_delivery` | Cobertos só pelo corretor falso (a topologia real nunca devolve não roteável nos testes); sem divergência observada | `tests/test_jobqueue_slot.py` |

Também observado no broker real: o reinício do RabbitMQ (com o worker ocioso e com um job de ~7 s em andamento)
não perde mensagem confirmada; o worker avisa em português ("RabbitMQ inacessível em 127.0.0.1:<porta>... Nova
tentativa em N s."), reconecta, redeclara a topologia e o job interrompido termina com `result`
(`test_scenario_9_*`).
