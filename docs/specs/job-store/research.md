# Research: Persistência da versão desktop (job-store)

**Feature**: `job-store` | **Date**: 2026-11-25 | **Spec**: [spec.md](spec.md)

Cada decisão cita a fonte. Fatos externos (licenças, assinaturas de biblioteca) vêm das fontes S01–S10,
lidas em 2026-11-25. Escolhas de desenho próprias desta feature (nomes de tabelas, colunas, módulos,
variáveis de ambiente novas) são marcadas **[PROPOSTA]**: não afirmam nada sobre sistema externo e são
validadas na implementação.

## Fontes consultadas

| ID | Fonte | O que foi lido |
|----|-------|----------------|
| S01 | https://pypi.org/pypi/asyncpg/0.32.0/json | `license_expression: "Apache-2.0"`, `license: null`, `requires_python: ">=3.9.0"`, `requires_dist` (3 itens), wheels cp313 |
| S02 | https://pypi.org/pypi/asyncpg/json | `info.version: "0.32.0"` (última versão publicada em 2026-11-25) |
| S03 | METADATA do wheel `asyncpg-0.32.0` baixado com `pip download` no container `artigo2md-api:local` (dec-021) | `License-Expression: Apache-2.0`, `Requires-Python: >=3.9.0` |
| S04 | https://pypi.org/pypi/async-timeout/json | `version: "5.0.1"`, `license: "Apache 2"`, classifier `License :: OSI Approved :: Apache Software License` |
| S05 | https://magicstack.github.io/asyncpg/current/api/index.html | assinaturas de `connect`, `create_pool`, `Connection.transaction`, `Connection.execute` |
| S06 | https://magicstack.github.io/asyncpg/current/usage.html | placeholders `$n`, transações com `async with`, tabela de conversão de tipos |
| S07 | https://raw.githubusercontent.com/MagicStack/asyncpg/v0.32.0/asyncpg/exceptions/__init__.py | classes de erro por SQLSTATE |
| S08 | https://raw.githubusercontent.com/MagicStack/asyncpg/v0.32.0/asyncpg/exceptions/_base.py | `PostgresError`, atributos `sqlstate`, `constraint_name`, `table_name`, `message`, `detail` |
| S09 | Estado da execução: dec-021 (opções e evidências), dec-022 (resposta do operador ao block-005), dec-024 (decisão com consentimento) | escolha do driver e do migrador |
| S10 | Código do repositório: `src/artigo2md/router.py`, `src/artigo2md/infra.py`, `stack/compose.yaml`, `stack/.env.example`, `stack/api/Dockerfile`, `pyproject.toml`, `.github/workflows/tests.yml`, `tests/test_stack_integration.py` | contratos internos existentes |
| S11 | Código-fonte do pacote `asyncpg` 0.32.0 (`asyncpg/_version.py` confirma `__version__ = '0.32.0'`), lido em 2026-11-25 numa instalação local da mesma versão: `pool.py` (`create_pool(dsn=None, *, init_size=None, min_size=10, max_size=10, max_queries=50000, max_inactive_connection_lifetime=300.0, ..., **connect_kwargs)`; `Pool.acquire(*, timeout=None)` com `compat.wait_for` quando há prazo), `connection.py` (`connect(..., timeout=60, ..., command_timeout=None, ...)`; `command_timeout`: "the default timeout for operations on this connection (the default is None: no timeout)"), `exceptions/__init__.py` (`ForeignKeyViolationError` `sqlstate='23503'`, `CheckViolationError` `'23514'`, `UniqueViolationError` `'23505'`, `ConnectionDoesNotExistError`, `InvalidPasswordError` `'28P01'`, `CannotConnectNowError` `'57P03'`, `TooManyConnectionsError`), `exceptions/_base.py` (atributos `sqlstate`, `detail`, `table_name`, `constraint_name`). A instalação local é de outro projeto da máquina e serve só de leitura; a tarefa 2.1 reconfere os mesmos nomes no ambiente deste projeto depois de adicionar a dependência |

## Decision 1: Driver do Postgres — asyncpg 0.32.0

**Decision**: `asyncpg` 0.32.0, faixa `asyncpg>=0.32.0,<0.33` (eixo estrutural `stack-frameworks`, decidido
pelo operador no block-005, registrado em dec-024).

**Rationale**:
- Licença `Apache-2.0` confirmada em fonte oficial (S01, campo `license_expression`) e no METADATA do wheel (S03).
- `requires_python: ">=3.9.0"` (S01) é compatível com `requires-python = ">=3.10"` do projeto (`pyproject.toml:7`).
- Há wheels cp313 `manylinux2014_aarch64` e `manylinux2014_x86_64` (S01): a imagem `python:3.13-slim-trixie`
  (`stack/api/Dockerfile:4`) instala sem compilador, em Mac Intel ou Apple Silicon.
- API assíncrona nativa (FR-014): `connect`, `create_pool`, `Connection.transaction()` com `async with` (S05, S06).
- Argumentos sempre como parâmetros `$n` do protocolo nativo (S06: "asyncpg uses the native PostgreSQL syntax for
  query arguments: `$n`"), o que atende FR-015 sem concatenação.

**Dependências transitivas** (S01, `requires_dist`):
- `async_timeout>=4.0.3; python_version < "3.11.0"` — só entra em Python 3.10. A imagem da API usa Python 3.13
  e o CI usa 3.12 (`.github/workflows/tests.yml`), então não entra em nenhum dos dois; entra só num ambiente de
  desenvolvimento local em 3.10. Licença `Apache 2` (S04) — permissiva.
- `gssapi` / `sspilib` — só com o extra `gssauth`, que esta feature não usa.

**Alternatives considered** (S09, dec-021):
- psycopg 3.3.9 / psycopg_pool 3.3.3 — `LGPL-3.0-only`: rejeitado (FR-018 exige MIT, Apache ou BSD).
- asyncpg + SQLAlchemy 2.x async + Alembic — SQLAlchemy 2.1.9 exige Python >=3.11 (conflito com `>=3.10`),
  Alembic não detecta migração aplicada alterada nem recusa banco mais novo por padrão, e a licença do Mako não
  foi confirmada. Rejeitado pelo operador (block-005, opção B).

## Decision 2: Onde o driver entra no `pyproject.toml`

**Decision** [PROPOSTA]: `asyncpg>=0.32.0,<0.33` entra no dependency group `api` (imagem da API) e no extra `dev`
(testes no host). NÃO entra em `[project] dependencies`.

**Rationale**: FR-021 exige que `convert`, `doctor` e `serve` funcionem sem o driver; o grupo `api` é o único
instalado na imagem (`stack/api/Dockerfile`, `uv sync --frozen --only-group api`), o que mantém a imagem sem
Docling e pymupdf4llm (FR-018); o CI instala `--extra dev` (`.github/workflows/tests.yml`), então os testes de
contrato da camada rodam lá. O `uv.lock` é regenerado na implementação.

**Alternatives considered**: dependência obrigatória do projeto (rejeitada: obrigaria o driver em quem só usa a
CLI); extra próprio `store` (rejeitado: mais uma combinação de instalação sem ganho, pois só a API e os testes usam).

## Decision 3: Migrações — runner próprio em SQL puro

**Decision**: runner próprio (dec-024), com arquivos `NNNN_<slug>.sql` empacotados em
`src/artigo2md/store/migrations/` [PROPOSTA de caminho] e tabela `schema_migrations` (versão, nome, checksum
SHA-256, instante de aplicação).

**Algoritmo** (atende FR-012 item a item):
1. Abrir uma conexão e tomar o advisory lock com uma chave fixa de 64 bits definida no código [PROPOSTA:
   constante derivada dos 8 primeiros bytes do SHA-256 de `artigo2md.schema_migrations`], por
   `pg_try_advisory_lock` repetido a cada 0,2 s até 60 s [PROPOSTA]; estourado o prazo, `MigrationLockTimeoutError`.
   Liberar com `pg_advisory_unlock` no `finally`. Dois processos concorrentes: o segundo espera e depois encontra
   tudo aplicado → nenhum erro de concorrência (US3, cenário 6). SC-001 mede só a execução depois do lock.
2. `CREATE TABLE IF NOT EXISTS schema_migrations (...)` sob o lock.
3. Ler os registros aplicados e comparar com os arquivos conhecidos:
   - versão aplicada maior que a maior conhecida → `SchemaTooNewError` ("o banco foi criado por uma versão mais
     nova do artigo2md");
   - versão aplicada conhecida com checksum diferente → `MigrationChecksumError` citando o arquivo;
   - versão aplicada que não existe mais no código (abaixo da maior conhecida) → `MigrationChecksumError`
     (histórico divergente).
4. Para cada pendente, em ordem crescente, numa transação própria: executar o SQL do arquivo e inserir a linha em
   `schema_migrations`. Falha → rollback total da migração e `MigrationFailedError` com o nome do arquivo e a
   mensagem em português (US3, cenário 4). Postgres executa DDL dentro de transação, então nada parcial fica.
5. Devolver `MigrationReport(applied=[...], current_version=N)`; lista vazia = "banco já atualizado".

**Execução de arquivo com vários comandos**: `Connection.execute` "can execute many SQL commands at once, when no
arguments are provided" (S05) — o arquivo é passado sem argumentos.

**Checksum**: SHA-256 dos bytes do arquivo como empacotado (`importlib.resources`), em hexadecimal minúsculo.

**Alternatives considered**: Alembic e yoyo-migrations (dec-021) — rejeitados pelo operador.

## Decision 4: Parâmetros de conexão e endereço

**Decision** [PROPOSTA de nomes novos]:
- Usuário, senha, banco e porta vêm de `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` e
  `ARTIGO2MD_POSTGRES_PORT` — os mesmos nomes de `stack/.env.example` e `stack/compose.yaml` (FR-016).
- Ordem de resolução igual à do `doctor` (`src/artigo2md/infra.py`, `resolve_ports`): variável de ambiente →
  arquivo em `ARTIGO2MD_STACK_ENV` → `./stack/.env`. A leitura do arquivo reusa `infra.parse_env_file` (só
  biblioteca padrão, nada é executado).
- Endereço: nova variável `ARTIGO2MD_POSTGRES_HOST`, padrão `127.0.0.1`. Valores aceitos: `127.0.0.1`, `::1`,
  `localhost` (host) e `postgres` (nome do serviço em `stack/compose.yaml`, para quando a `project-api` rodar no
  container). Qualquer outro valor é recusado antes de conectar (Princípio I). Como `postgres` é resolvido por DNS
  (e um resolvedor com domínio de busca poderia levá-lo a uma máquina remota, entregando a senha), a camada
  resolve o nome com `socket.getaddrinfo` antes de conectar e só aceita se TODOS os endereços forem de loopback ou
  privados (`ipaddress.ip_address(...).is_loopback or .is_private`); senão recusa sem conectar (gate
  `owasp-security`, finding M-01). A conexão usa então o **endereço já validado** (primeiro da lista) em `host=`,
  sem resolver o nome de novo, o que fecha a janela entre validação e conexão (checklist security CHK005). Sobre a
  faixa: `stack/compose.yaml` (S10) não declara `networks:` nem sub-rede, então não há faixa mais estreita que
  "loopback ou privado" que se possa citar sem presumir; o residual (host de rede local privada que responda por
  `postgres`, p.ex. por domínio de busca do resolvedor) é o risco L-04 do plan, não bloqueante. Quem liga a variável no container
  é a feature `project-api`; esta feature só define e testa a regra.
- Todos os quatro parâmetros são obrigatórios: ausente, vazio ou porta fora de 1–65535 → `StoreConfigError` com
  mensagem que cita só o nome da variável.
- A senha vive num campo sem `repr` (`dataclasses.field(repr=False)`) e nunca entra em mensagem, log ou exceção.
  A conexão usa argumentos nomeados de `asyncpg.connect` (`host`, `port`, `user`, `password`, `database`, S05),
  nunca uma DSN em texto. Erros de conexão viram `DatabaseUnavailableError` com host e porta, lançados com
  `from None` para não encadear texto do driver.

**Rationale**: FR-016 pede os mesmos parâmetros da stack, padrão `127.0.0.1`, restrição a loopback ou ao serviço
interno, falha citando só o nome do parâmetro. O achado histórico de que a porta 5432 pode estar ocupada por
outro Postgres local é atendido pela porta configurável já existente (`ARTIGO2MD_POSTGRES_PORT`).

**Alternatives considered**: DSN única em variável (rejeitada: a senha ficaria embutida num texto que costuma
aparecer em erro); aceitar qualquer endereço de loopback `127.0.0.0/8` (rejeitada: a lista fechada é mais
simples de testar e suficiente).

## Decision 5: Regras de negócio garantidas pelo banco, sem depender de captura de exceção no caminho comum

**Decision** [PROPOSTA]:
- **No máximo um job não final por documento** (FR-004, FR-013): índice único parcial
  `jobs (document_id) WHERE status IN ('pending','running')`. A criação usa
  `INSERT ... ON CONFLICT (document_id) WHERE status IN ('pending','running') DO NOTHING RETURNING id`;
  zero linhas → `ActiveJobExistsError`. Funciona sob concorrência porque quem decide é o índice.
- **Nome único entre projetos ativos, sem diferenciar maiúsculas** (FR-001): índice único parcial em
  `name_key WHERE status = 'active'`, onde `name_key = NFC(NFC(name.strip()).casefold())` é calculado pela camada
  (stdlib `unicodedata` e `str.casefold`); a criação usa `ON CONFLICT (name_key) WHERE status = 'active' DO NOTHING`.
  `lower(name)` no banco foi descartado porque depende da localidade do cluster; `casefold` torna a regra igual em
  qualquer máquina (`Á`=`á`; `ß`=`ss` por decisão explícita; `a`≠`á`).
- **Transições de job** (FR-010): `UPDATE jobs SET status = $new ... WHERE id = $1 AND status = ANY($allowed)
  RETURNING ...`; zero linhas → leitura do estado atual para distinguir `JobNotFoundError` de
  `InvalidJobTransitionError`. Um gatilho `BEFORE UPDATE` em `jobs` repete a validação como defesa em
  profundidade.
- **Desarquivar com nome já ativo** e corridas raras em que um `UPDATE` bate num índice único: o erro do driver é
  `asyncpg.exceptions.UniqueViolationError` (`sqlstate = '23505'`, S07), que expõe `constraint_name` (S08). A
  camada traduz para `ProjectNameTakenError` quando `constraint_name` é o índice de nome; qualquer outro
  `PostgresError` (S08) vira `StoreDatabaseError` genérico — assim recusa de regra e erro de banco são
  distinguíveis por tipo (FR-004, FR-010).

**Rationale**: o caminho comum não depende de nomes de exceção; quando depende, os nomes vêm do código-fonte da
versão fixada (S07, S08).

## Decision 6: Estados, tipos e valores enumerados

**Decision** [PROPOSTA]: colunas `text` com `CHECK (... IN (...))`, em vez de tipos `ENUM` do Postgres — mudar a
lista vira uma migração simples (`DROP/ADD CONSTRAINT`) e o valor volta como `str` (S06: "All other types are
encoded and decoded as text by default"; `text` é tipo nativo).

| Domínio | Valores |
|---------|---------|
| `projects.status` | `active`, `archived` |
| `sources.kind` | `folder`, `file` |
| `sources.mode` | `in_place`, `copied` |
| `jobs.status` | `pending`, `running`, `succeeded`, `failed`, `cancelled` |
| `jobs.requested_engine` | `auto`, `docling`, `pymupdf`, `mineru`, `markitdown` (= `router.ENGINES`, `src/artigo2md/router.py:13`) |
| `history_events.event_type` | `project_created`, `project_archived`, `project_unarchived`, `source_added`, `document_added`, `job_created`, `job_started`, `attempt_started`, `attempt_recorded`, `job_succeeded`, `job_failed`, `job_cancelled` |
| status derivado do documento | `not_processed` + os cinco estados de job |

`jobs.requested_engine` é validado contra `router.ENGINES` por um teste (o pacote `store` não importa `router`,
ver Decision 8). Motores de tentativas e resultados são texto livre (FR-005).

## Decision 7: Representação de `Quality`

**Decision** [PROPOSTA]: seis colunas tipadas, espelho de `router.Quality` (`src/artigo2md/router.py:22-29`):
`score double precision`, `accepted boolean`, `tables_found integer`, `tables_expected integer`,
`chars_per_page double precision`, `issues text[]`. Tipos Python devolvidos: `float`, `bool`, `int`, `list`
(S06: `double precision` → `float`, `bool` → `bool`, `integer` → `int`, `anyarray` → `list`). Arrays preservam a
ordem dos elementos; `issues` é `NOT NULL DEFAULT '{}'` no resultado, então lista vazia volta como lista vazia.

**Avisos** (FR-006, FR-007): são as próprias `issues` do motor vencedor (spec, Assumptions). Não há coluna
duplicada: o registro de resultado expõe `warnings` como a mesma lista.

**Alternatives considered**: `jsonb` (rejeitado: S06 indica que `jsonb` volta como `str` sem codec registrado, e
perderia as `CHECK` por campo).

## Decision 8: Fronteira da camada (FR-017, FR-021)

**Decision** [PROPOSTA]: pacote novo `artigo2md.store`. Ele pode importar `artigo2md.infra` (só biblioteca padrão)
e `asyncpg`; NÃO importa `router`, `engines`, `pdfscan`, `worker`, `web` nem `cli` — `router` importa `pdfscan`
(pypdfium2), que não existe na imagem da API. Nenhum módulo fora de `store/` importa `store` ou `asyncpg`.

**Verificação**: teste com subprocesso que importa `artigo2md.cli`, `artigo2md.web`, `artigo2md.worker`,
`artigo2md.router`, `artigo2md.engines`, `artigo2md.pdfscan`, `artigo2md.infra` e `artigo2md.api.app` e confere que
`asyncpg` e `artigo2md.store` não estão em `sys.modules` (mesmo padrão de
`tests/test_api_base.py::test_importing_api_does_not_load_heavy_engines`), mais uma varredura AST dos arquivos
`src/artigo2md/**/*.py` fora de `store/`.

## Decision 9: Testes de integração

**Decision** [PROPOSTA]: variável `ARTIGO2MD_DB_TESTS=1`, separada de `ARTIGO2MD_STACK=1` (que sobe uma stack
própria por projeto Compose, `tests/test_stack_integration.py`); aqui a stack do usuário já está no ar (spec,
US5). Sem a variável: `pytest.mark.skipif` com motivo em português.

Fixture de sessão:
1. Resolve os parâmetros com a mesma regra da Decision 4.
2. Conecta no banco `POSTGRES_DB` só para executar `CREATE DATABASE artigo2md_test_<hex aleatório>` (nenhuma
   tabela do usuário é lida ou alterada; FR-019, SC-006). O nome é gerado pelo teste, só `[a-z0-9_]`, e entra
   como identificador citado.
3. Aplica as migrações no banco temporário e roda os cenários.
4. No teardown (inclusive após falha): `DROP DATABASE ... WITH (FORCE)`, protegido por guarda: o nome tem de
   casar `^artigo2md_test_[0-9a-f]{16}$` e ser diferente de `POSTGRES_DB`, senão o teardown aborta sem apagar nada
   (gate `owasp-security`, finding M-02; SC-006).
5. Se o usuário do `.env` não puder criar banco, o teste falha com mensagem em português explicando a causa
   (a permissão do `POSTGRES_USER` da imagem oficial é verificada na primeira execução, não presumida aqui).

## Decision 10: Instantes e ordenação

**Decision** [PROPOSTA]: todas as colunas de instante são `timestamptz` (FR-022) com `DEFAULT now()` — exceto
`history_events.occurred_at`, que usa `DEFAULT clock_timestamp()`: `now()` devolve o início da transação, e eventos
de transações concorrentes ou de uma mesma transação longa ficariam com instantes que não refletem a ordem em que
foram gravados; `clock_timestamp()` dá o instante da gravação da linha. Ordem entre transações concorrentes = a do
instante de gravação (não a do commit). asyncpg devolve `datetime` com fuso (S06: "timestamp with time zone |
offset-aware datetime.datetime"). A linha do tempo ordena por `(occurred_at, id)`, com `id` `bigint GENERATED
ALWAYS AS IDENTITY` como desempate estável para eventos com o mesmo instante (US4, cenário 2). Paginação por `LIMIT`/`OFFSET`, com limite máximo de 500 por
página [PROPOSTA].

## Registro de licenças (FR-018)

| Componente | Licença (como na fonte) | Princípio II | Fonte oficial | Consultado em |
|---|---|---|---|---|
| asyncpg 0.32.0 | Apache-2.0 (`license_expression`) | permissiva | https://pypi.org/pypi/asyncpg/0.32.0/json | 2026-11-25 |
| async-timeout (só Python < 3.11; fora da imagem e do CI) | Apache 2 (`license`; classifier "Apache Software License") | permissiva | https://pypi.org/pypi/async-timeout/json | 2026-11-25 |

Registrado em `stack/THIRD_PARTY_LICENSES.md` nesta etapa.

## Pendências verificáveis na implementação (não bloqueiam o plan)

| ID | Item | Como verificar |
|----|------|----------------|
| V-01 | Versão de `async-timeout` resolvida no `uv.lock` para Python 3.10 (a licença lida é a da versão 5.0.1) | conferir o lock após `uv lock`; se outra versão, conferir a licença dela no PyPI |
| V-02 | Permissão de `CREATE DATABASE` do `POSTGRES_USER` da stack | fixture de integração falha com mensagem clara se faltar |
| V-03 | `pg_try_advisory_lock($1)` repetido até o prazo serializa duas chamadas concorrentes de `apply_migrations` e libera ao fechar a conexão; `clock_timestamp()` como `DEFAULT` de coluna; `SHOW server_version` para registrar o ambiente | testes de integração das tarefas 3.4 e 6.2 contra o Postgres da stack; falha = ajustar o contrato, não presumir |
