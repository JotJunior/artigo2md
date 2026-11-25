# Contract: camada de repositório `artigo2md.store`

> **[PROPOSTA — a validar na implementação]**. Interface Python interna, consumida só pela futura `project-api`
> (FR-014, FR-017). Não há rota HTTP nesta feature. Nomes em inglês; mensagens das exceções em português.
> Fatos sobre o driver citam a research (S05–S08).

## Configuração — `artigo2md.store.config`

```python
@dataclass(frozen=True)
class DbSettings:
    host: str            # padrão "127.0.0.1"; aceitos: "127.0.0.1", "::1", "localhost", "postgres"
    port: int            # 1..65535
    user: str
    database: str
    password: str = field(repr=False)

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None, cwd: Path | None = None) -> "DbSettings": ...
    def resolve_connect_host(self) -> str: ...   # endereço já validado, usado em `host=` ao conectar
```

| Parâmetro | Variável | Origem (nesta ordem) | Obrigatório |
|-----------|----------|----------------------|-------------|
| host | `ARTIGO2MD_POSTGRES_HOST` | ambiente → `ARTIGO2MD_STACK_ENV` → `./stack/.env` | não (padrão `127.0.0.1`) |
| port | `ARTIGO2MD_POSTGRES_PORT` | idem | sim |
| user | `POSTGRES_USER` | idem | sim |
| password | `POSTGRES_PASSWORD` | idem | sim |
| database | `POSTGRES_DB` | idem | sim |

Falhas → `StoreConfigError`, mensagem cita só a variável. Exemplos de texto:
- `"Parâmetro do banco ausente: defina POSTGRES_PASSWORD em stack/.env."`
- `"Parâmetro do banco inválido: ARTIGO2MD_POSTGRES_PORT precisa ser um número de 1 a 65535."`
- `"Endereço do banco não permitido em ARTIGO2MD_POSTGRES_HOST: use 127.0.0.1, ::1, localhost ou postgres."`
  (o valor recusado não é ecoado).
- Com `host = "postgres"`, o nome é resolvido uma única vez (`socket.getaddrinfo`) por `resolve_connect_host()` e só
  é aceito se TODOS os endereços forem de loopback ou privados (`ipaddress`); caso contrário:
  `"O nome postgres não resolveu para um endereço local; conexão recusada."` (research, Decision 4). A conexão usa o
  primeiro endereço validado em `host=`, sem resolver o nome de novo (fecha a janela entre validação e conexão).
  Para `127.0.0.1`, `::1` e `localhost`, `resolve_connect_host()` devolve o próprio valor. A fonte não declara
  sub-rede (`stack/compose.yaml` não tem `networks:`), então "privado" é o critério mais estreito citável; o
  residual está em plan, Riscos (L-04).
- Mensagens traduzidas de `PostgresError` usam texto próprio em português; o campo `detail` do servidor (que pode
  conter valores de linhas) não é copiado para a mensagem.

## Ciclo de vida — `artigo2md.store.Store`

```python
class Store:
    @classmethod
    async def open(cls, settings: DbSettings, *, min_size: int = 1, max_size: int = 5,
                   connect_timeout: float = 10.0, command_timeout: float = 30.0,
                   acquire_timeout: float = 10.0) -> "Store": ...
    async def close(self) -> None: ...
    async def __aenter__(self) -> "Store": ...
    async def __aexit__(self, *exc) -> None: ...

    def transaction(self) -> AsyncContextManager["StoreSession"]: ...
```

- `open` cria um pool com `asyncpg.create_pool(min_size=..., max_size=..., host=settings.resolve_connect_host(),
  port=..., user=..., password=..., database=..., timeout=connect_timeout, command_timeout=command_timeout)`
  (S05 e S11: `create_pool(dsn=None, *, init_size, min_size=10, max_size=10, max_queries, ..., **connect_kwargs)` e
  `connect(..., host, port, user, password, database, timeout=60, command_timeout=None, ...)`). Falha de conexão →
  `DatabaseUnavailableError`
  (`"Não foi possível conectar ao Postgres em 127.0.0.1:5432. Confira se a stack está no ar (sh stack/up.sh)."`),
  lançada `from None`.
- **Prazos [PROPOSTA, configuráveis]**: `connect_timeout` (10 s; `timeout` de `connect`, padrão do driver 60 s) limita
  cada conexão nova; `command_timeout` (30 s; padrão do driver `None`, "sem limite", S11) limita cada comando e
  cancela a operação na transação em curso; `acquire_timeout` (10 s) é passado a `pool.acquire(timeout=...)`
  (S11: `timeout=None` espera sem limite). Estourar qualquer um deles levanta `asyncio.TimeoutError` (sinônimo de
  `TimeoutError` em Python 3.11+) no driver e a camada traduz para `DatabaseUnavailableError` (`from None`):
  `"O banco não respondeu em N s."` ou, no pool, `"Todas as conexões com o banco estão ocupadas; tente de novo."`
  Repetir a chamada depois funciona (sem gravação parcial: a transação é desfeita).
- **Pool [PROPOSTA]**: `min_size=1`, `max_size=5`; conexões esgotadas → a chamada espera até `acquire_timeout` e então
  falha como acima (nunca bloqueia sem prazo).
- `transaction()` adquire uma conexão (`async with pool.acquire()`) e abre `connection.transaction()` (S05, S06);
  a `StoreSession` devolvida tem os mesmos métodos de operação abaixo, todos na mesma transação. Fora de
  `transaction()`, cada método do `Store` roda na sua própria transação curta.
- Todas as operações são `async` (FR-014). Toda consulta usa parâmetros `$n` (S06); nenhum valor do usuário é
  concatenado em SQL (FR-015).

## Registros devolvidos — `artigo2md.store.models`

Dataclasses congeladas, campos = colunas do [data-model](../data-model.md):
`Project`, `Source`, `Document`, `Job`, `Attempt`, `Result`, `HistoryEvent`, `DocumentStatus`, e o valor
`QualityRecord(score, accepted, tables_found, tables_expected, chars_per_page, issues: tuple[str, ...])`.
`Result.warnings` é propriedade que devolve `quality.issues` (FR-007).
`DocumentStatus(document_id, project_id, source_id, path, status, latest_job_id, latest_job_requested_at,
last_result)` com `status` em `not_processed` + os cinco estados de job e `last_result: ResultSummary | None`;
`ResultSummary(job_id, markdown_path, engine, score, accepted, created_at)` (data-model, "Status derivado"). `QualityRecord` espelha `router.Quality`
sem importar `router` (research, Decision 8); um teste confere que os nomes dos campos coincidem.

## Operações

| Método | Retorno | Recusas (exceção) | Req |
|--------|---------|-------------------|-----|
| `create_project(name)` | `Project` (nome gravado sem espaços nas pontas) | nome vazio ou só espaços → `StoreValidationError`; `name_key` já ativo (`casefold`, ver data-model) → `ProjectNameTakenError`; emite `project_created` | FR-001 |
| `get_project(project_id)` | `Project` | `ProjectNotFoundError` | FR-001 |
| `list_projects(include_archived=False)` | `list[Project]` | — | FR-011 |
| `archive_project(project_id)` | `ArchiveOutcome(project, cancelled_job_ids)` | `ProjectNotFoundError`; já arquivado → `StoreValidationError` (`"O projeto já está arquivado."`); emite `job_cancelled` por job e `project_archived` | FR-011 |
| `unarchive_project(project_id)` | `Project` | `ProjectNotFoundError`; já ativo → `StoreValidationError` (`"O projeto já está ativo."`, simétrico); nome em uso por projeto ativo → `ProjectNameTakenError`; emite `project_unarchived` | FR-001, FR-011 |
| `delete_project(project_id)` | `None` | `ProjectNotFoundError`; não grava evento (o histórico é removido junto) | FR-011 |
| `add_source(project_id, kind, mode, path)` | `Source` | `kind`/`mode` fora da lista → `StoreValidationError`; mesmo caminho no mesmo projeto é permitido; emite `source_added` | FR-002 |
| `add_document(project_id, source_id, path)` | `(Document, created: bool)` (documento existente mantém a fonte original) | fonte de outro projeto → `StoreValidationError`; emite `document_added` só se `created` | FR-003 |
| `list_documents(project_id)` | `list[DocumentStatus]` (ver "Registros"), uma única consulta, sem paginação (referência: 1.000 documentos) | `ProjectNotFoundError` | FR-009 |
| `create_job(document_id, requested_engine)` | `Job` | `DocumentNotFoundError`; motor fora de `ENGINES` → `StoreValidationError`; outro job não final → `ActiveJobExistsError`; emite `job_created` | FR-004 |
| `start_job(job_id)` | `Job` | `InvalidJobTransitionError`, `JobNotFoundError`; emite `job_started` | FR-010 |
| `record_attempt(job_id, engine, seconds, quality=None, error=None)` | `Attempt` | `JobNotFoundError`; job não `running` → `InvalidJobTransitionError`; sem `quality` e sem `error` ou valores fora de faixa → `StoreValidationError`; emite `attempt_recorded` | FR-005, FR-007 |
| `complete_job(job_id, markdown_path, engine, quality, message)` | `(Job, Result, HistoryEvent)` | job não `running` → `InvalidJobTransitionError`; valores fora de faixa → `StoreValidationError` | FR-006, FR-010, FR-014 |
| `fail_job(job_id, error_summary, message)` | `(Job, HistoryEvent)` | idem | FR-006, FR-010 |
| `cancel_job(job_id, message)` | `(Job, HistoryEvent)` | job final → `InvalidJobTransitionError` | FR-010 |
| `get_result(job_id)` | `Result \| None` (`None` = job existe e não tem resultado) | `JobNotFoundError` para `job_id` inexistente | FR-006 |
| `list_attempts(job_id)` | `list[Attempt]` em ordem de `position` (lista vazia = job sem tentativas) | `JobNotFoundError` para `job_id` inexistente | FR-005 |
| `add_event(project_id, event_type, message, document_id=None, job_id=None)` | `HistoryEvent` | `ProjectNotFoundError`; tipo fora da lista, documento/job de outro projeto ou inexistente → `StoreValidationError` (validação prévia na mesma transação, mais a tradução de 23503/23514 abaixo) | FR-008 |
| `list_events(project_id=None, document_id=None, limit=100, offset=0)` | `list[HistoryEvent]` ordenados por `(occurred_at, id)` | exatamente um dos dois filtros; `limit` 1..500 | FR-008 |

`complete_job`, `fail_job`, `cancel_job`, `archive_project` e `delete_project` gravam estado e eventos numa única
transação: tudo ou nada (US2, cenário 10). Não há métodos de alteração/remoção de eventos, tentativas ou resultados.

**Quem emite cada evento** (FR-008): as próprias operações da tabela gravam, na mesma transação, `project_created`,
`source_added`, `document_added`, `job_created`, `job_started`, `attempt_recorded`, `job_succeeded` (`complete_job`),
`job_failed` (`fail_job`), `job_cancelled` (`cancel_job` e `archive_project`), `project_archived` e
`project_unarchived`. `attempt_started` não tem operação da camada que o emita: é registrado pela `project-api` via
`add_event`, assim como qualquer evento de outra origem. `delete_project` não grava evento. Falha de uma operação
desfaz seus eventos (nenhum evento órfão).

**Recuperação de job órfão** (worker caiu, job `running` parado; FR-010): a camada não expira jobs. A `project-api` é
quem decide e executa a sequência: `fail_job` (ou `cancel_job`) sobre o job antigo e só depois `create_job` para o
mesmo documento; enquanto o antigo não chega a estado final, `create_job` levanta `ActiveJobExistsError`.

## Hierarquia de exceções — `artigo2md.store.errors`

```text
StoreError                      # base; mensagem sempre em português
├── StoreConfigError            # parâmetro ausente/inválido (cita só o nome da variável)
├── StoreValidationError        # valor fora do domínio
├── StoreRuleError              # recusa de regra de negócio (distinguível de erro de banco)
│   ├── ProjectNameTakenError
│   ├── ActiveJobExistsError
│   └── InvalidJobTransitionError   # inclui gravação tardia em job final (notificação repetida)
├── StoreNotFoundError
│   ├── ProjectNotFoundError
│   ├── DocumentNotFoundError
│   └── JobNotFoundError
├── DatabaseUnavailableError    # conexão recusada/caiu; repetir após reconectar funciona
├── StoreDatabaseError          # qualquer outro asyncpg.exceptions.PostgresError (S08)
└── MigrationError              # ver contracts/migrations.md
```

Tradução de erros do driver: `asyncpg.exceptions.UniqueViolationError` (`sqlstate = '23505'`, S07) com
`constraint_name = 'projects_active_name_key'` (S08) → `ProjectNameTakenError`; com
`'jobs_one_active_per_document'` → `ActiveJobExistsError`; `ForeignKeyViolationError` (`sqlstate = '23503'`) e
`CheckViolationError` (`sqlstate = '23514'`, S07/S11) → `StoreValidationError` com texto próprio por operação
(por exemplo, `add_event`: `"O documento ou o job informado não pertence a este projeto."`); na corrida com a
exclusão concorrente do projeto, a camada refaz a leitura do projeto depois de desfeita a transação e, se ele sumiu,
levanta `ProjectNotFoundError` em vez de `StoreValidationError`; demais `PostgresError` → `StoreDatabaseError`.
Os atributos lidos do erro do driver são só `sqlstate` e `constraint_name` (S08); `message`, `detail` e `hint` do
servidor NUNCA entram na mensagem traduzida (o `detail` pode conter valores de linhas).

**Encadeamento de exceções (FR-016, SC-007)**: toda exceção da camada é lançada com `raise ... from None` (inclui
`DatabaseUnavailableError`, `StoreDatabaseError` e `MigrationFailedError`); a mensagem usa só texto próprio em
português e a cadeia `__cause__`/`__context__` não carrega o erro bruto do driver. Nenhuma mensagem traduzida
inclui a senha nem o texto bruto de parâmetros de conexão (FR-016, SC-007).

**Texto livre** (FR-005, FR-007): `error`, `error_summary`, `issues` e `message` podem conter trechos derivados de
documento; ficam só no banco local e nada deles é enviado a fora (Princípio I).
