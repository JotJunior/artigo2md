# Implementation Plan: API da versão desktop (project-api)

**Feature**: `project-api` | **Date**: 2026-11-25 | **Spec**: [spec.md](spec.md)

## Summary

Estender a API base (`src/artigo2md/api/app.py`, só `GET /health`) para ser o contrato único entre o app
desktop, a fila e o banco: CRUD de projetos, registro de fontes e documentos (a lista de PDFs vem do app),
enfileiramento no RabbitMQ no contrato `schema_version` 1, consumo dos eventos dos workers com gravação pelo
job-store, fluxo SSE por projeto, consulta de resultados/histórico, entrega do Markdown e exportação em zip.
Abordagem: o contrato da fila sai de `jobqueue/contract.py` para um módulo sem dependências pesadas
(`jobqueue/schema.py`) usado pelo worker e pela API (research D1); consumidor e publicador `pika` em threads
dentro do processo da API (D2, D3); extensões pequenas no job-store com a migração `0002` (D6); um diretório de
dados único montado em `/data`, com o par host/container vindo de variáveis de ambiente (D5, D10). Rotas e corpos
estão publicados e verificados em [contracts/](contracts/) (implementação concluída; ver `tasks.md`).

## Technical Context

**Language/Version**: Python >= 3.10 no projeto (`pyproject.toml`); Python 3.13 na imagem da API (`stack/api/Dockerfile`)
**Primary Dependencies**: FastAPI + uvicorn (grupo `api`, já presentes); `asyncpg` via `artigo2md.store` (já presente); `pika==1.5.0` acrescentado ao grupo `api` (licença verificada em `conversion-queue/research.md` L15/L16). Nenhuma dependência nova no projeto
**Storage**: Postgres da stack via `artigo2md.store` (migração nova `0002_project_api.sql`); arquivos no diretório de dados montado em `/data` (leitura do Markdown, remoção na exclusão de projeto, zip temporário em `/tmp`)
**Testing**: pytest; sem Docker com `tests/fake_amqp.py` (existente) e um repositório falso novo (`tests/fake_store.py`); integração com a stack atrás de `ARTIGO2MD_STACK=1` + `ARTIGO2MD_DB_TESTS=1` (flags existentes, `tests/conftest.py`, `tests/store_db.py`)
**Target Platform**: container `artigo2md-api:local` (Linux) no compose local do macOS; worker nativo no host (fonte: `docs/briefing.md`, versão desktop macOS; `stack/compose.yaml`; `docs/roadmap.md`, item 6)
**Project Type**: web-service local (API HTTP + consumidor AMQP no mesmo processo)
**Performance Goals**: SSE em até 2 s em 95% (SC-004; meta aceita pelo operador em block-001/dec-023 e medida no cenário 6: p95 de 18 ms, quickstart); listagem de 1.000 documentos numa consulta (herdado do job-store)
**Constraints**: só `127.0.0.1` no host; sem importar motores de conversão nem `pypdfium2`; leitura de arquivo só sob `/data`; mensagens em português sem segredo; `/health` inalterado; documentação automática desligada
**Scale/Scope**: instância única, monousuário; ~12 módulos novos em `src/artigo2md/api/`, 1 módulo novo em `jobqueue/`, extensões em `store/`, ~10 arquivos de teste, compose/.env/up.sh, README

Nenhum `NEEDS CLARIFICATION` restante; nenhum eixo estrutural novo é fixado (linguagem, framework, persistência,
arquitetura e ambiente já vêm do roadmap e do briefing; D2 registra por que o consumidor no mesmo processo não é
um componente novo).

## Constitution Check

*GATE: antes do Phase 0 e re-checado após o Phase 1.*

| Princípio | Status | Notas |
|-----------|--------|-------|
| I. Processamento 100% Local | PASS | Porta só `127.0.0.1` (compose inalterado); API fala só com `postgres`/`rabbitmq` da rede do compose, com o mesmo filtro de endereço loopback/privado do job-store; nenhum serviço remoto nem telemetria; o PDF nunca passa pela API (Q1). |
| II. Licenças | PASS | Única mudança de dependência: `pika==1.5.0` no grupo `api`, já verificado (BSD-3-Clause). Zip, SSE e confinamento usam biblioteca padrão e FastAPI/Starlette já presentes. Nenhuma imagem nova. |
| III. Português | PASS | Corpo de erro único com `message` em português; handler próprio para validação (substitui o 422 em inglês do FastAPI); logs do consumidor em português. |
| IV. Testes | PASS | `uv run pytest` sem Docker com fakes de fila e banco; integração atrás de `ARTIGO2MD_STACK=1`/`ARTIGO2MD_DB_TESTS=1`, incluindo ciclo real com worker (FR-034). |
| V. Veracidade | PASS | Campos das respostas copiados de `store/models.py`; contrato da fila citado de `queue-messages.md`; rotas/corpos/variáveis novas publicados em `contracts/` e conferidos contra o código (`tests/test_api_contract_coverage.py`); origem do app confirmada pelo operador (block-001/dec-023) com a fonte tauri 2.12.7 citada em research F19; comportamento do bind mount do macOS declarado "a verificar". |

**Re-check pós-design (Etapa 7)**: PASS. O design não acrescenta serviço, container, porta nem dependência nova;
a migração `0002` acrescenta coluna, índice e um tipo de evento e só amplia a regra de transição (`pending → failed`); a refatoração do contrato elimina uma duplicação em vez de criar uma.

## Project Structure

### Documentation (this feature)

```text
docs/specs/project-api/
├── spec.md
├── plan.md                      # este arquivo
├── research.md                  # D1–D12
├── data-model.md
├── quickstart.md
└── contracts/
    ├── http-api.md              # rotas /v1
    ├── live-events.md           # SSE
    ├── store-extensions.md      # job-store + migração 0002
    └── configuration.md         # variáveis, compose, .env, up.sh
```

### Source Code (repository root)

```text
src/artigo2md/
├── api/
│   ├── __init__.py              # existente
│   ├── app.py                   # existente → create_app(); /health inalterado; lifespan inicia serviços de fundo
│   ├── settings.py              # novo: ApiSettings (contracts/configuration.md)
│   ├── errors.py                # novo: corpo de erro, tradução das exceções do store, handler de validação, 405
│   ├── security.py              # novo: checagem de Origin, CORS, Content-Type, limite de corpo
│   ├── paths.py                 # novo: to_container_path + abertura confinada + regras de caminho cadastrado (D10)
│   ├── deps.py                  # novo: Services; acesso ao Store/broker com 503 quando indisponíveis
│   ├── broker.py                # novo: threads publicadora e consumidora (pika), reconexão (D3, D4)
│   ├── consumer.py              # novo: tratamento de eventos → store + live (D4)
│   ├── background.py            # novo: ciclo de vida dos serviços de fundo (migração, republicação, reconexão)
│   ├── live.py                  # novo: difusor SSE por projeto (D7)
│   ├── files.py                 # novo: limpeza segura de arquivos na exclusão de projeto (S-01)
│   ├── export.py                # novo: exportação zip (D11) e leitura confinada do Markdown
│   └── routes/
│       ├── __init__.py          # build_v1_router
│       ├── projects.py          # US1
│       ├── documents.py         # US1 (fontes e documentos)
│       ├── jobs.py              # US2, US5
│       ├── events.py            # US3
│       ├── results.py           # US4 (resultado, Markdown, histórico, exportação)
│       └── status.py            # GET /v1/status
├── jobqueue/
│   ├── schema.py                # novo: contrato v1 sem router/pdfscan (D1)
│   ├── contract.py              # existente → reexporta schema + introspecção p/ teste
│   ├── amqp.py, topology.py, output.py   # existentes → importam de schema
└── store/
    ├── models.py                # existente → Job.published_at, EVENT_TYPES + project_renamed
    ├── repository.py            # existente → operações de contracts/store-extensions.md
    └── migrations/0002_project_api.sql   # novo

stack/
├── compose.yaml                 # serviço api: volume /data + environment
├── up.sh                        # valida ARTIGO2MD_DATA_DIR
└── .env.example                 # ARTIGO2MD_DATA_DIR, ARTIGO2MD_API_ALLOW_DEV_ORIGIN

pyproject.toml                   # grupo api + pika==1.5.0
uv.lock                          # regenerado; diff restrito ao grupo api (decisão do operador, block-001/dec-023)

tests/
├── fake_store.py                # novo: Store em memória com as mesmas recusas
├── test_jobqueue_schema.py      # novo: igualdade com introspecção; import leve
├── test_api_projects.py, test_api_documents.py, test_api_jobs.py, test_api_results.py, test_api_status.py
├── test_api_consumer.py, test_api_broker.py, test_api_live.py, test_api_security.py, test_api_paths.py
├── test_api_files.py, test_api_errors.py, test_api_settings.py, test_api_app.py, test_api_lifespan.py
├── test_api_contract_coverage.py, test_api_isolation.py, test_api_logging_hygiene.py, test_fake_store_parity.py
├── test_store_extensions_integration.py   # ARTIGO2MD_DB_TESTS=1
└── test_api_integration.py      # ARTIGO2MD_STACK=1: ciclo completo com worker real
```

**Structure Decision**: as rotas ficam em `api/routes/` por grupo de recurso e a infraestrutura (broker, consumidor,
SSE, arquivos) em módulos separados, para que cada um seja testável com fakes; `app.py` continua sendo o alvo do
uvicorn (`artigo2md.api.app:app`, `stack/api/Dockerfile`), então o `CMD` da imagem não muda.

## Convencoes de Borda

| Camada | Case style | Validacao | Fonte da verdade |
|--------|------------|-----------|------------------|
| Colunas do Postgres | snake_case | CHECK/FK + migrações | `src/artigo2md/store/migrations/*.sql` |
| Registros Python (store) | snake_case (dataclasses) | `StoreValidationError` | `src/artigo2md/store/models.py` |
| Payload HTTP (request/response) | snake_case, mesmos nomes dos dataclasses | modelos Pydantic de entrada + serialização explícita | `contracts/http-api.md` |
| Eventos SSE | snake_case | serialização explícita | `contracts/live-events.md` |
| Mensagens da fila | snake_case | `jobqueue.schema.validate_event` / `build_job` | `conversion-queue/contracts/queue-messages.md` |
| Caminhos | host absoluto no banco e na fila; container só dentro da API | `api.paths.to_container_path` | `research.md` D10 |
| Path params | inteiros (`{project_id}`, `{document_id}`, `{job_id}`) | FastAPI (`int`, > 0) | `contracts/http-api.md` |

**Mapper layer**: `store` já converte linha → dataclass (`_row_to_*`, sem ORM). A API converte dataclass → JSON em
`api/routes/*` com funções `to_json(record)` explícitas (sem `dataclasses.asdict` genérico, para `published_at`
não vazar e `Result.warnings` entrar).

**Validação**: entrada com modelos Pydantic do FastAPI (`extra="forbid"`); saída sem validação extra, coberta pelo
roundtrip do quickstart (cenário 1) que compara o payload real com o contrato.

## Fluxos principais

**Enfileirar (US2, FR-010 a FR-012)**: rota → `create_job` (commit) → `live` emite `job_state pending` →
publicadora (thread) publica com confirmação → `mark_job_published` | `fail_job` + `job_state failed`. A chamada à
publicadora é feita com `asyncio.wrap_future`, prazo de 10 s (aceito pelo operador, block-001/dec-023); estourar o prazo = não confirmado.

**Consumir (FR-013 a FR-017)**: thread consumidora → `run_coroutine_threadsafe(consumer.handle(body))` → espera o
resultado → `ack`; banco fora = repete a mesma mensagem com recuo (D4). Partida: conectar ao banco
(`apply_migrations`), republicar `list_unpublished_jobs`, iniciar consumo; cada dependência tem seu laço de
reconexão independente e o `/health` nunca depende delas.

**Ler Markdown (FR-024, FR-025)**: `get_document` → `list_documents`/`last_result` → sem resultado = 404 sem disco →
`to_container_path` → abertura confinada → resposta com os bytes.

## Cobertura de requisitos

| Requisito | Onde |
|-----------|------|
| FR-001–FR-004 | `routes/projects.py`, `store.rename_project`, `files.py` (limpeza) |
| FR-005–FR-009 | `routes/documents.py`, `paths.py`, regras de caminho em `http-api.md` |
| FR-010–FR-012, FR-026 | `routes/jobs.py`, `broker.py` (publicadora), D8 |
| FR-013–FR-017 | `broker.py` (consumidora), `consumer.py`, `background.py`, `routes/status.py`, D4 |
| FR-018 | `POST /v1/jobs/{id}/cancel` |
| FR-019, FR-020 | `live.py`, `routes/events.py`, `live-events.md` |
| FR-021–FR-025 | `routes/results.py`, `export.py`, `paths.py` |
| FR-027–FR-031 | `security.py`, `errors.py`, compose inalterado na porta, teste de `sys.modules` |
| FR-032 | `deps.py` (503), laços de reconexão, `/health` sem dependência |
| FR-033 | `contracts/*.md`; `/health` intacto; docs automáticas desligadas |
| FR-034 | `tests/fake_store.py`, `tests/fake_amqp.py`, `test_api_integration.py` |

## Riscos e mitigação

| Id | Risco | Mitigação |
|----|-------|-----------|
| R-01 | Origem de produção do app (`tauri://localhost`, **confirmada** pelo operador em block-001/dec-023; fonte em research F19) diferir em outra versão do Tauri | lista de origens fixa no código (`tauri://localhost`); se a origem real mudar, a troca é feita no código e na spec, não por variável (`ARTIGO2MD_API_ALLOWED_ORIGINS` removida por decisão do operador, tarefa 13.9/dec-052); origem de desenvolvimento `http://localhost:1420` só com `ARTIGO2MD_API_ALLOW_DEV_ORIGIN` explícita, desligada por padrão; cliente nativo (sem `Origin`) não depende disso |
| R-02 | Bind mount do macOS não deixar o usuário `10001` ler/apagar arquivos do worker (runtime do operador: Docker Desktop 29.9.2, contexto `desktop-linux`, observado em `docker info`; block-001/dec-023) | quickstart cenário 8 e tarefa 11.3 verificam o comportamento real; se falhar, a API recusa a operação com mensagem clara em português (`data_dir_permission_denied`), sem executar como root e sem `chmod` automático; mudar `user` do container segue exigindo nova decisão do operador |
| R-03 | Qualquer processo local pode chamar a API (sem login) | aceito pela spec (monousuário, loopback); `Origin` bloqueia páginas web; documentado no README |
| R-04 | `BlockingConnection` obsoleta no pika 1.5.0 | mesmo risco aceito no worker; acoplamento concentrado em `jobqueue/amqp.py` |
| R-05 | Consumo sequencial limitar a vazão de eventos | volume local pequeno (workers padrão 4, `jobqueue/settings.py:28`); medir no cenário 4, sem limiar de vazão (decisão do operador, block-001/dec-023; premissa P-07 da `conversion-queue`) |
| R-06 | Republicação na partida gerar conversão em dobro se a confirmação se perdeu depois de publicada | aceito (at-least-once); resultado duplicado é descartado pela transição do job |
| R-07 | `stack/.env` existente sem `ARTIGO2MD_DATA_DIR` impedir `up.sh` | mensagem em português explicando o valor; README e `.env.example` atualizados |

## Segurança — achados do gate owasp-security (incorporados ao design)

Revisão de arquitetura (OWASP Top 10:2025, API Security Top 10:2023) em 2026-11-25. Nenhum achado `critical` ou
`high`; os achados abaixo foram corrigidos neste plano.

| Id | Sev. | Achado | Correção adotada |
|----|------|--------|------------------|
| S-01 | medium | Exclusão de projeto removendo caminhos vindos do cliente (A01, CWE-22) | só caminhos derivados (`<mount>/<job_id>/`, `<mount>/sources/<project_id>/`), `lstat` + `realpath` + `shutil.rmtree` ([http-api.md](contracts/http-api.md), `DELETE`) |
| S-02 | medium | Evento da fila analisado sem limite de tamanho/profundidade (API4, A10) — `validate_event` chama `json.loads` direto (em `jobqueue/contract.py`, antes de `schema.py`) | `consumer.py` aplica `MAX_BODY_BYTES` e `json_depth <= MAX_JSON_DEPTH` (mesmos limites do job, `queue-messages.md`) **antes** de `validate_event`; acima → descarte com log |
| S-03 | medium | Conexões SSE sem teto (API4) | no máximo 32 assinantes simultâneos (aceito pelo operador, block-001/dec-023); acima → 503 `too_many_streams` em português |
| S-04 | medium | Exportação em zip no tmpfs sem teto (API4) | uma exportação por vez (outra → 409 `export_in_progress`) e teto de 512 MiB de Markdown somado (aceito pelo operador, block-001/dec-023); acima → 413 com mensagem; `tmpfs /tmp` com `size=768m` no compose (configuration.md) |
| S-05 | medium | Lote grande com broker fora esperando o prazo de 10 s por documento (API4, disponibilidade) | publicadora desconectada ou primeira falha do lote → os documentos restantes falham na hora, sem nova espera (FR-011 mantido: todos terminam `failed` com motivo) |
| S-06 | low | Texto livre dos eventos (`reason`, `issues`, `message`) sem limite gravado no banco | truncar em 2000 caracteres por campo antes de gravar (aceito pelo operador, block-001/dec-023) |
| S-07 | low | Corrida entre conferir `realpath` e abrir o arquivo (CWE-367) | abrir primeiro (`O_NOFOLLOW`) e conferir o caminho real do descritor aberto (`/proc/self/fd/<fd>` no container Linux; `fcntl.F_GETPATH` + igualdade de `(st_dev, st_ino)` onde não há `/proc`, como no macOS dos testes; sem mecanismo → recusa, fail-closed; research Decision 10) |
| S-08 | info | Sem login: qualquer processo local chama a API (A07); sem limitação de taxa (por exemplo, repetição de `POST .../jobs` ou abertura de SSE por processo local) | risco aceito pela spec (monousuário, loopback); **ausência de rate limiting aceita como decisão explícita do operador (block-001/dec-023)**, mitigada só pelos tetos de S-03/S-04; páginas web bloqueadas por `Origin` + `Content-Type`; R-03 |
| S-09 | info | Credenciais no `environment` do container (A02) | mesmo risco já aceito em `stack/.env.example` (visíveis em `docker inspect`) |
| S-10 | info | Cliente local pode mandar o worker ler qualquer PDF do host; publicador local com as credenciais do broker pode forjar um `result`/`error` bem formado para um `job_id` real em `artigo2md.events` | risco já aceito em `conversion-queue/plan.md` (P-02); credenciais do broker = confiança total, **risco de forja aceito pelo operador (block-001/dec-023)**; a API não amplia: só repassa o caminho registrado e o caminho do Markdown recebido ainda é confinado ao diretório de dados (FR-025) |

## Complexity Tracking

Sem violações de constitution a justificar.
