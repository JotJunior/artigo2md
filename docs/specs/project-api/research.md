# Research: API da versão desktop (project-api)

**Feature**: `project-api` | **Date**: 2026-11-25 | **Spec**: [spec.md](spec.md)

> Regra de veracidade (Constitution V): cada fato sobre código, contrato ou dependência cita o arquivo de origem.
> As decisões abaixo nasceram como **[PROPOSTA]** e foram **implementadas e verificadas** (`src/artigo2md/api/`,
> `src/artigo2md/jobqueue/schema.py`, `src/artigo2md/store/`, `stack/`; medições no quickstart, onda-014): cada uma
> indica onde está implementada. A tabela de fontes descreve o estado do repositório **antes** desta feature (em `main`),
> para registrar o ponto de partida. Nenhuma página externa foi consultada: nenhuma dependência nova além de
> `pika==1.5.0` (Decision 3), e o único valor vindo de outra feature (origem do app desktop) é citado com a
> confirmação do operador (Decision 9).

## Fontes

| Id | Fonte | O que sustenta |
|----|-------|----------------|
| F01 | `src/artigo2md/api/app.py` | API base: `FastAPI(docs_url=None, redoc_url=None, openapi_url=None)`, `TrustedHostMiddleware(allowed_hosts=["127.0.0.1", "localhost"])`, só `GET /health` |
| F02 | `stack/api/Dockerfile` | imagem `python:3.13-slim-trixie`, `uv sync --frozen --only-group api`, `USER 10001:10001`, `uvicorn ... --host 0.0.0.0 --port 8000` |
| F03 | `stack/compose.yaml` (em `main`, antes desta feature) | serviço `api`: `read_only: true`, `tmpfs: /tmp`, `cap_drop: ALL`, `no-new-privileges`, porta `127.0.0.1:${ARTIGO2MD_API_PORT}:8000`, **sem `volumes` e sem `environment`**; serviços `postgres` e `rabbitmq` com hostnames de serviço |
| F04 | `pyproject.toml` | grupo `api = ["fastapi>=0.115", "uvicorn>=0.30", "asyncpg>=0.32.0,<0.33"]`; extra `queue = ["pika==1.5.0", "psutil"]` |
| F05 | `src/artigo2md/jobqueue/contract.py:19-20` | `from ..pdfscan import Analysis` e `from ..router import ENGINES, Attempt, ConversionResult, Quality`; `validate_report` deriva o shape de `report` por introspecção desses dataclasses (`_dataclass_kinds`, `_analysis_kinds`) |
| F06 | `src/artigo2md/pdfscan.py:11` | `import pypdfium2 as pdfium` no nível do módulo |
| F07 | `src/artigo2md/jobqueue/topology.py:13`, `amqp.py:15`, `output.py:16` | os três importam de `.contract` (logo, hoje, todos arrastam `router`/`pdfscan`) |
| F08 | `docs/specs/conversion-queue/contracts/queue-messages.md` | contrato da fila `schema_version` 1, exchanges, routing keys, garantias de entrega e deduplicação por (`job_id`, `attempt`) |
| F09 | `src/artigo2md/jobqueue/topology.py` | fila durável `artigo2md.events.all` ligada a `artigo2md.events` com `job.#`; exchange de jobs `artigo2md.jobs`, routing key `convert` |
| F10 | `docs/specs/job-store/contracts/repository.md` + `src/artigo2md/store/repository.py` | operações da camada; não há `rename_project`, `get_job`, `get_document`, `list_sources` nem listagem de jobs; `delete_project` "Nenhum arquivo é tocado" (`repository.py`, docstring de `delete_project`) |
| F11 | `src/artigo2md/store/repository.py`, `_TRANSITIONS` (em `main`) | transições: `failed` só a partir de `running`; `cancelled` a partir de `pending` ou `running` |
| F12 | `src/artigo2md/store/migrations/0001_initial.sql` | `jobs_timestamps_check` exige `finished_at` em estado final mas **não** exige `started_at`; `history_events_event_type_check` com 12 tipos fixos, sem `project_renamed` |
| F13 | `docs/specs/job-store/contracts/repository.md`, "Configuração" | `DbSettings.from_env`: `ARTIGO2MD_POSTGRES_HOST` (aceita `postgres`, validado para loopback/privado), `ARTIGO2MD_POSTGRES_PORT`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` |
| F14 | `src/artigo2md/jobqueue/settings.py:18-19, 82-86` | credenciais `RABBITMQ_DEFAULT_USER`/`RABBITMQ_DEFAULT_PASS`; `WorkerSettings.host` fixo em `infra.HOST` (loopback), sem opção |
| F15 | `src/artigo2md/jobqueue/amqp.py` | `open_connection(settings)` usa `settings.host`, `settings.port`, `settings.credentials` (duck typing); `BlockingConnection`; aviso de obsolescência aceito com pika fixado em 1.5.0 |
| F16 | `docs/specs/conversion-queue/research.md`, L15/L16 | licença do pika 1.5.0 = `BSD-3-Clause`, verificada pelo operador |
| F17 | `src/artigo2md/jobqueue/output.py` + `queue-messages.md` | o worker grava `<data_dir>/<job_id>/<stem>.md`; `data_dir` vem de `--data-dir`, resolvido com `Path.resolve(strict=True)` (`settings.py:136-146`) |
| F18 | `docs/specs/job-store/research.md`, Decision 7 + `src/artigo2md/store/models.py:20-21` | precedente: `QualityRecord` espelha `router.Quality` e `REQUESTED_ENGINES` espelha `router.ENGINES` **sem importar `router`**, com teste que confere a igualdade |
| F19 | `/Users/jot/Projects/_lab/artigo2md-desktop-shell/docs/specs/desktop-shell/plan.md:240` (worktree da feature `desktop-shell`, ainda não integrada a `main`) | origem do app: `tauri://localhost` em produção, `http://localhost:1420` só em desenvolvimento; "A origem exata de produção no macOS é confirmada no cenário 8 do quickstart". **Confirmada pelo operador em block-001/dec-023**: fonte citada pelo operador: tauri 2.12.7 (versão do `Cargo.lock` da `desktop-shell`), `src/manager/mod.rs`, `get_app_url` e o teste com `cfg custom_protocol`, que no macOS retorna `tauri://localhost` quando `frontendDist` é caminho; `desktop/src-tauri/tauri.conf.json` da `desktop-shell` usa `frontendDist ../dist` e `devUrl http://localhost:1420` |
| F20 | `docs/specs/job-store/contracts/migrations.md` | migrações versionadas com checksum, aplicadas "por chamada explícita" (`apply_migrations(settings)`, `store/migrator.py:205`); sem subcomando de CLI |
| F21 | `stack/.env.example` | variáveis hoje existentes; nenhuma define diretório de dados |
| F22 | `src/artigo2md/infra.py:30-35` | portas padrão (`ARTIGO2MD_API_PORT` 8778 etc.) e `ARTIGO2MD_STACK_ENV` |
| F23 | `tests/test_api_base.py:53-56` | teste existente de FR-029: processo novo, `sys.modules` sem `docling`/`pymupdf4llm` |

## Decision 1: Validar eventos sem importar `router`/`pdfscan` (ponto 2 da onda-001)

**Problema**: a imagem da API instala só o grupo `api` (F02, F04); `contract.py` importa `router` e `pdfscan` no
nível do módulo (F05), e `pdfscan` importa `pypdfium2` (F06). `topology.py`, `amqp.py` e `output.py` importam de
`contract.py` (F07). Importar qualquer um deles na API quebra a imagem.

**Decision** (implementada em `src/artigo2md/jobqueue/schema.py`; testes `tests/test_jobqueue_schema.py`): extrair de `contract.py` um módulo novo **`src/artigo2md/jobqueue/schema.py`**, só com
biblioteca padrão, que passa a ser a **única** definição do contrato `schema_version` 1:

- constantes (`SCHEMA_VERSION`, `SUPPORTED_VERSIONS`, `CONTENT_TYPE`, `MAX_BODY_BYTES`, `MAX_JSON_DEPTH`,
  `JOB_ID_RE`, `TYPE_*`, `EVENT_TYPES`, `ROUTING_KEYS`, `ERROR_CODES`), `ContractError`, `json_depth`,
  `build_job`, `encode`, `validate_event`, `validate_report`;
- o shape de `report` declarado como **mapas estáticos** (`REPORT_KINDS`, `ANALYSIS_KINDS`, `ATTEMPT_KINDS`,
  `QUALITY_KINDS`) em vez de introspecção, e `ENGINES` como tupla literal.

Hoje só a validação de `report` (introspecção) e a checagem de `engine` em `parse_job` usam `router`/`pdfscan`
(`contract.py:172`, `:268-335`); `build_job`, `build_progress`, `build_result`, `build_error`, `encode` e
`utc_now_iso` usam só biblioteca padrão (`contract.py:184-252`). Por isso todo o conteúdo vai para `schema.py`, e
`contract.py` vira um módulo fino que reexporta `schema.py` (os imports atuais do worker continuam válidos) e
guarda só a função de introspecção usada pelo teste de igualdade; `topology.py`, `amqp.py` e `output.py` passam a
importar de `schema.py`. Um teste (no ambiente de desenvolvimento, onde `router` existe)
confere que os mapas estáticos são **iguais** aos derivados por introspecção de `ConversionResult`, `Analysis`,
`Attempt` e `Quality`, e que `schema.ENGINES == router.ENGINES` — o mesmo padrão já aceito no job-store (F18).
Um segundo teste importa `artigo2md.jobqueue.schema`, `topology` e `amqp` em processo novo e confere que
`pypdfium2`, `artigo2md.router` e `artigo2md.pdfscan` não estão em `sys.modules`.

**Rationale**: uma única fonte em uso (o worker e a API validam com o mesmo `validate_event`), sem cópia que possa
divergir em silêncio: qualquer mudança nos dataclasses do roteador quebra o teste de igualdade. Segue precedente do
próprio projeto (F18). Não muda o contrato publicado (F08) nem o comportamento do worker.

**Alternatives considered**:
- *Instalar `pypdfium2` na imagem da API*: rejeitada — `router` importa `engines` e puxa o código de conversão para
  a API; contraria o espírito de FR-029 e de US6 ("não carrega motor de conversão") e aumenta a imagem.
- *Copiar a validação para a API*: rejeitada — é exatamente a duplicação divergente que a onda-001 apontou.
- *Gerar um JSON Schema e validar com biblioteca*: rejeitada — dependência nova (licença a verificar) para um
  contrato pequeno que já tem validador em Python.

## Decision 2: Consumidor de eventos dentro do processo da API

**Decision** (implementada em `api/broker.py`, `api/consumer.py`, `api/background.py`): o consumidor de `artigo2md.events.all` (F09) roda **no mesmo processo** do uvicorn, numa
thread dedicada, e entrega cada evento ao laço `asyncio` da aplicação (`asyncio.run_coroutine_threadsafe`), que
grava no banco pela camada `store` e repassa ao fluxo ao vivo (Decision 7).

**Rationale**: a spec atribui o consumo à própria API (US2, FR-013) e o fluxo SSE precisa dos eventos no mesmo
processo para entregar em até 2 s sem um segundo canal; uma instância local única (spec, nota de infraestrutura).
Não cria serviço nem container novo, então não altera o eixo de arquitetura já fixado no roadmap ("API FastAPI em
container que consome os eventos dos workers").

**Alternatives considered**: serviço/container separado para o consumidor — rejeitado: exigiria um canal extra
entre consumidor e API para o SSE e seria um componente novo na stack (decisão de arquitetura fora desta feature).

## Decision 3: Cliente AMQP = `pika` 1.5.0 já verificado

**Decision** (implementada; `pyproject.toml` e `uv.lock`): acrescentar `pika==1.5.0` ao grupo `api` (mesma versão fixada no extra `queue`, F04).
Duas conexões `BlockingConnection`, cada uma confinada à sua thread (o pika não é thread-safe; F15): uma
**consumidora** e uma **publicadora** (com `confirm_delivery()` e `mandatory=True`, como o worker). A abertura
reutiliza `jobqueue.amqp.open_connection` com um objeto de configuração da API (Decision 5).

**Rationale**: licença já verificada em fonte (BSD-3-Clause, F16), versão já em `uv.lock` no extra `queue` (o grupo `api` precisa do `uv.lock` regenerado com `uv lock`, porque a imagem usa `uv sync --frozen --only-group api`); nenhuma dependência nova
a verificar (Princípio II). O aviso de obsolescência da `BlockingConnection` é o mesmo aceito no worker (F15).

**Alternatives considered**: `aio-pika` (cliente asyncio) — rejeitado nesta entrega: traria `aiormq` e `pamqp`
como dependências novas, cuja licença teria de ser verificada em fonte oficial nesta etapa (Princípio II), sem
ganho funcional para um consumidor único.

## Decision 4: Ordem e idempotência no consumo (FR-013 a FR-017)

**Decision** (implementada em `api/broker.py` e `api/consumer.py`):

1. `prefetch_count` 16 na conexão consumidora; mensagens tratadas **em sequência**, uma por vez, na ordem de
   entrega — preserva a ordem `progress` → `result`/`error` de um mesmo (`job_id`, `attempt`) (F08, "Ordem").
2. `ack` só depois de o tratamento terminar: gravado, ou **descartado de propósito** (fora do contrato, job
   inexistente, job em estado final, duplicata). Descartes são registrados em log em português (sem o corpo).
3. Banco indisponível durante o tratamento: a mensagem **não** recebe `ack` nem `nack`; a thread espera com
   recuo exponencial (1 s a 30 s) usando `connection.sleep()` (mantém o heartbeat) e tenta de novo a **mesma**
   mensagem — não há reenfileiramento que embaralhe a ordem. Conexão AMQP caída: o broker devolve as mensagens
   sem `ack` (F08, "Garantias" 3) e a thread reconecta com o mesmo recuo (FR-017).
4. `job_id` do evento ≠ decimal de um `jobs.id` existente → descarte "job inexistente". O `job_id` da fila é
   sempre `str(jobs.id)` (inteiro positivo; casa com `JOB_ID_RE`).
5. Tratamento por tipo:
   - `progress`: se o job está `pending`, `start_job`; se está `running`, nada no banco; em ambos repassa ao
     fluxo ao vivo. Job final → descarte silencioso (FR-016).
   - `result`: numa transação (`store.transaction()`): `start_job` se `pending`; `record_attempt` para cada item
     de `report.attempts` na ordem; `complete_job(markdown_path, engine, quality, message)`. `quality` é a do item
     de `attempts` cujo `engine` é o vencedor e tem `quality` não nula (a primeira, na ordem); se nenhuma
     existir, o evento é tratado como fora do contrato. Job já final → `InvalidJobTransitionError` → duplicata,
     descarte sem erro (primeiro `result` vence, F08 "Garantias" 4).
   - `error` com `retryable=true`: se `pending`, `start_job`; o job continua `running`; aviso repassado ao fluxo
     ao vivo e ao log; **nada** gravado no histórico (evita duplicatas em reentregas: a tabela não tem chave por
     tentativa da fila).
   - `error` com `retryable=false`: numa transação, `start_job` se `pending` e `fail_job(job_id, error_summary=reason, message="Conversão falhou: <reason>")` (assinatura `fail_job(job_id, error_summary, message)`, `repository.py`).
6. Caminho do Markdown fora do diretório de dados (FR-025, US4-6): o resultado não é aceito; o job vai a `failed`
   com motivo em português ("O Markdown foi gravado fora da pasta de dados da API. Confira se o --data-dir do worker é
   o mesmo caminho de ARTIGO2MD_DATA_DIR em stack/.env.", `OUTSIDE_DATA_DIR_SUMMARY` em `consumer.py`) e o caminho
   recebido não é gravado nem lido. Sem a pasta de dados configurada o motivo é outro (`DATA_DIR_UNAVAILABLE_SUMMARY`).
7. Evento de job cujo projeto está **arquivado** é descartado (log `INFO`), como o de job final: arquivar já cancelou
   os jobs não finais.

**Rationale**: a máquina de estados do job-store já recusa gravação tardia em job final (F11), o que torna a
deduplicação do `result` e do `error` definitivo uma consequência da transição, sem estado extra.

**Alternatives considered**: `nack(requeue=True)` com banco fora — rejeitado: a mensagem volta à fila e pode ser
reentregue depois de eventos posteriores do mesmo job; gravar `attempt_started` a cada tentativa nova da fila —
rejeitado: exige saber, de forma persistente, quais tentativas já foram vistas.

## Decision 5: Configuração por variáveis de ambiente (pontos 3 e 4 da onda-001)

**Decision** (implementada em `api/settings.py` e `stack/compose.yaml`):

| Variável | Onde é definida | Uso |
|----------|-----------------|-----|
| `ARTIGO2MD_DATA_DIR` | `stack/.env` (obrigatória, caminho absoluto já resolvido no host) | origem do bind mount e valor passado à API |
| `ARTIGO2MD_DATA_DIR_HOST` | `environment` do serviço `api`, `= ${ARTIGO2MD_DATA_DIR}` | lado host do par da regra de conversão (FR-006, FR-025) |
| `ARTIGO2MD_DATA_DIR_MOUNT` | `environment` do serviço `api`, valor fixo `/data` | ponto de montagem no container (lado container do par) |
| `ARTIGO2MD_API_ALLOW_DEV_ORIGIN` | `stack/.env` (opcional, vazia = desligada) → `environment` do `api` | `1` ou `true` acrescenta a origem de desenvolvimento `http://localhost:1420` à lista efetiva (Decision 9) |
| `ARTIGO2MD_RABBITMQ_HOST` | `environment` do `api`, valor `rabbitmq` | endereço do broker para a API; aceitos `127.0.0.1`, `::1`, `localhost`, `rabbitmq` (mesma regra de `ARTIGO2MD_POSTGRES_HOST`, F13) |
| `ARTIGO2MD_POSTGRES_HOST` / `ARTIGO2MD_POSTGRES_PORT` | `environment` do `api`: `postgres` / `5432` | já existentes (F13); dentro da rede do compose a porta é a do container, não a publicada |
| `ARTIGO2MD_RABBITMQ_PORT` | `environment` do `api`: `5672` | já existente (F22); idem |
| `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`, `RABBITMQ_DEFAULT_USER`, `RABBITMQ_DEFAULT_PASS` | `stack/.env` → `environment` do `api` | já existentes (F13, F14, F21) |

O compose monta só `${ARTIGO2MD_DATA_DIR}:/data` (bind, leitura e escrita — a exclusão de projeto remove arquivos,
FR-004) e mantém o resto da configuração de segurança (F03). `stack/up.sh` passa a recusar `ARTIGO2MD_DATA_DIR`
ausente, relativo, inexistente ou diferente do seu caminho resolvido (`cd "$dir" && pwd -P`), com mensagem em
português. Na API, ausência ou valor inválido de `ARTIGO2MD_DATA_DIR_HOST`/`_MOUNT` **não derruba** o processo: o
`/health` continua e as rotas que leem arquivos respondem 503 com a mensagem de configuração (FR-032).

**Rationale**: a decisão Q2 do clarify fixou "par vindo de variável de ambiente"; o valor do host precisa ser o
caminho resolvido porque o worker grava `markdown_path` a partir de `--data-dir` resolvido (F17). A porta interna
literal evita o engano entre porta publicada e porta do container.

**Alternatives considered**: um padrão de caminho em `~/Library/...` — rejeitado: nenhuma fonte do projeto define
onde fica o diretório de dados (F21); fica a cargo de `desktop-stack-control`, que escreve `stack/.env`.

## Decision 6: Extensões da camada `store` (ponto 1 da onda-001)

**Decision** (implementada em `store/repository.py`, `store/models.py`, `store/migrations/0002_project_api.sql`): acrescentar ao job-store (detalhe em [contracts/store-extensions.md](contracts/store-extensions.md)):
`rename_project`, `get_document`, `get_source`, `list_sources`, `get_job`, `list_project_job_ids`,
`mark_job_published`, `list_unpublished_jobs`, a transição `pending → failed` em `fail_job` e a migração
`0002_project_api.sql` (coluna `jobs.published_at` e tipo de evento `project_renamed`).

**Rationale**: FR-002 manda acrescentar o renomear à camada; as leituras por id são necessárias para enfileirar
(caminho do PDF) e para atribuir eventos ao projeto certo (SSE). `published_at` fecha a janela "job gravado,
publicação nunca tentada" (queda da API entre o `COMMIT` e a publicação): na partida, a API republica os jobs
`pending` sem `published_at` (FR-011, SC-006). O CHECK de timestamps já aceita `failed` sem `started_at` (F12), mas o trigger `jobs_guard_transition` (`0001_initial.sql`) só permitia `pending → running|cancelled`; a migração `0002` substitui a função do trigger para aceitar também `pending → failed`.

**Alternatives considered**: republicar todos os `pending` na partida — rejeitado: SC-005 (reinício no meio de uma
fila de 20) geraria conversões em dobro de jobs já publicados; renomear sem evento de histórico — rejeitado: o
histórico perderia uma mudança visível ao usuário, e a migração é barata (o migrador já tem versionamento e
checksum, F20).

## Decision 7: Fluxo ao vivo com `StreamingResponse`

**Decision** (implementada em `api/live.py` e `api/routes/events.py`): SSE com `fastapi.responses.StreamingResponse` (`media_type="text/event-stream"`), sem
dependência nova. Um difusor em memória (`asyncio`) mantém, por projeto, a lista de assinantes, cada um com fila
limitada (1000 itens); fila cheia = cliente lento → a conexão dele é encerrada (ele reconecta e relê o status,
FR-020). Comentário de keep-alive a cada 15 s detecta cliente que saiu; ao sair, a assinatura é removida no
`finally` do gerador. Sem `Last-Event-ID`: a recuperação é pela consulta de status (FR-020).

**Rationale**: `StreamingResponse` já é usado no projeto (`src/artigo2md/web.py:20`); a meta de 2 s em 95% (SC-004,
decisão do operador, block-001/dec-023) foi medida no teste de integração (mediana 4 ms, p95 18 ms; quickstart,
"Medições (11.4)").

## Decision 8: Publicação de jobs e falha do broker (FR-011, US2-10)

**Decision** (implementada em `api/routes/jobs.py` e `api/broker.py`): por documento: (1) `create_job` (commit); (2) publicar `schema.build_job(str(job.id),
pdf_path, engine, 1)` em `artigo2md.jobs`/`convert` com confirmação e `mandatory`; (3) confirmado →
`mark_job_published`; não confirmado ou broker fora → `fail_job(job.id, error_summary="Não foi possível enviar o job para a
fila. ...", message="Falha ao enviar o job para a fila.")`. Lote: documentos processados em sequência; a resposta separa `enqueued`, `skipped` (job ativo) e
`failed` (publicação). Na partida e a cada reconexão da publicadora, `list_unpublished_jobs` é republicado.

**Rationale**: o job nunca fica `pending` sem mensagem confirmada por mais que a janela de uma queda da própria
API, e essa janela é fechada na partida seguinte.

## Decision 9: Origem do app desktop e proteção contra páginas web (FR-028)

**Decision**: lista de origens **fixa** no código (`api/settings.py`): `tauri://localhost`
— origem de produção **confirmada pelo operador** (block-001/dec-023; F19). A origem de desenvolvimento `http://localhost:1420`
fica **desligada por padrão** e só entra na lista efetiva quando `ARTIGO2MD_API_ALLOW_DEV_ORIGIN` for definida de forma
explícita. A variável `ARTIGO2MD_API_ALLOWED_ORIGINS`, que trocava a lista por outra qualquer, constava do desenho
original e foi **removida por decisão do operador** (tarefa 13.9, dec-052): a spec não pede lista arbitrária.
Regras (implementadas em `api/security.py` e `api/settings.py`):

- pedido **com** cabeçalho `Origin` fora da lista (inclui `Origin: null`) → 403 em qualquer rota de negócio e no
  SSE, antes de tocar banco ou fila;
- pedido sem `Origin` (cliente nativo do lado Rust, `curl`, testes) é aceito — não é navegador;
- `CORSMiddleware` com `allow_origins` = a lista, `allow_credentials=False`, métodos e cabeçalhos explícitos;
- rotas com corpo exigem `Content-Type: application/json` (415 caso contrário), o que impede o envio "simples"
  de formulário entre origens.

**Rationale**: o navegador sempre envia `Origin` em pedidos entre origens com método que altera dados e em
`fetch`/`EventSource`; recusar no servidor cobre também o que o CORS sozinho não bloqueia (o efeito colateral do
pedido). DNS rebinding continua coberto pelo `TrustedHostMiddleware` (F01).

**Alternatives considered**: token de sessão local — rejeitado nesta entrega: a spec fixa "sem login" e a
proteção exigida é contra páginas web, não contra processos locais (risco aceito no briefing: monousuário local).

## Decision 10: Leitura confinada de arquivos (FR-025, SC-003)

**Decision** (implementada em `api/paths.py`): regra única `to_container_path(host_path)` em `src/artigo2md/api/paths.py`:
recusa vazio, caractere nulo, não absoluto, componente `..` ou `.`; exige prefixo `ARTIGO2MD_DATA_DIR_HOST` com
fronteira de componente; troca o prefixo por `ARTIGO2MD_DATA_DIR_MOUNT`. A leitura segue a sequência de plan.md S-07: abre primeiro com
`os.open(..., O_RDONLY | O_NOFOLLOW)`, confere `S_ISREG` por `fstat` e confere que o **caminho real do descritor aberto**
está sob o `realpath` do ponto de montagem; qualquer falha → recusa em português, sem ler. O caminho real do
descritor vem de `/proc/self/fd/<fd>` (Linux, container) ou, onde esse caminho não existe (macOS, `uv run pytest`
sem Docker), de `fcntl.fcntl(fd, fcntl.F_GETPATH, ...)` (macOS; `fcntl.F_GETPATH` existe no Python 3.9 e 3.12 usados
aqui, conferido em 2026-11-25); além disso, `(st_dev, st_ino)` do descritor MUST ser igual ao de `os.stat` do caminho
resolvido. Se nenhum mecanismo estiver disponível, a leitura é **recusada** (fail-closed), nunca liberada sem a
conferência. A comparação de prefixo
é exata (sensível a maiúsculas), porque `ARTIGO2MD_DATA_DIR` e o `--data-dir` do worker são o mesmo caminho
resolvido (Decision 5).

**Descompasso `--data-dir` do worker x `ARTIGO2MD_DATA_DIR` da API** (implementado em `api/consumer.py`): sintoma = evento `result` cujo
`markdown_path` não está sob `ARTIGO2MD_DATA_DIR_HOST`. A API falha o job (`failed`) e grava/loga, em português:
"O Markdown foi gravado fora da pasta de dados da API. Confira se `--data-dir` do worker é o mesmo caminho de
`ARTIGO2MD_DATA_DIR` em `stack/.env`." O texto não ecoa o caminho completo do host na resposta HTTP; o log pode citar
o `job_id`.

## Decision 11: Exportação em zip (FR-021)

**Decision** (implementada em `api/export.py` e `api/routes/results.py`): `zipfile` da biblioteca padrão, gravado num arquivo temporário em `/tmp` (tmpfs do
container, F03) já removido do diretório ao ser aberto e entregue por `StreamingResponse` sobre o descritor; um `.md` por documento com `last_result`. Nome:
`<stem>.md` para a primeira ocorrência (ordem de `document_id`) e `<stem>-<document_id>.md` nas seguintes;
`<stem>` = nome do arquivo do Markdown do resultado, sem diretórios. Arquivo que falhar na leitura confinada é
omitido e listado num `LEIA-ME.txt` dentro do zip, em português. Projeto **sem nenhum documento concluído** →
404 `no_result` (nenhum zip vazio). O teto de 512 MiB de Markdown somado cabe no `tmpfs /tmp` de
`size=768m` (configuration.md).

## Decision 12: Migrações aplicadas pela API

**Decision** (implementada em `api/deps.py` e `api/background.py`): ao conectar ao banco (na partida ou na reconexão), a API chama
`apply_migrations(settings)` (F20) antes de servir rotas de banco; falha de migração mantém o `/health` e faz as
rotas de banco responderem 503 com a mensagem traduzida.

**Rationale**: o job-store não tem CLI de migração (F20) e a API é a única usuária da camada (roadmap, item 3).

## Verificado na implementação (onda-014; resultados em quickstart.md, "Resultados observados")

- Bind mount do macOS (Docker Desktop 29.9.2, contexto `desktop-linux`, block-001/dec-023): o usuário `10001` do
  container leu o Markdown gravado pelo worker nativo e o `DELETE` do projeto removeu `<mount>/<job_id>/`; nenhuma mudança
  de `user` foi necessária (quickstart, cenário 8). Se outro runtime falhar, a API recusa com `data_dir_permission_denied`,
  sem root e sem `chmod` automático.
- Latência do SSE (SC-004: mediana 4 ms, p95 18 ms) e `list_documents` com 1.000 documentos pela API (mediana 4 ms): quickstart,
  "Medições (11.4)".
- Origem de produção do app: `tauri://localhost` (block-001/dec-023, F19); só rever se a versão do Tauri mudar.
