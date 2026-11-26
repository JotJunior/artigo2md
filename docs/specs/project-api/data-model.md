# Data Model: API da versão desktop (project-api)

**Feature**: `project-api` | **Date**: 2026-11-25

As entidades persistidas são as do job-store (`docs/specs/job-store/data-model.md`,
`src/artigo2md/store/models.py`, `store/migrations/0001_initial.sql`); esta feature só acrescenta o que está
marcado **(extensão)** — implementado e verificado (`src/artigo2md/store/`, `src/artigo2md/api/`). As entidades em memória
existem só no processo da API.

## Entity: Project (existente + extensão)

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| id | bigint | PK identity | |
| name | text | `btrim(name) <> ''` | grafia exibida |
| name_key | text | único entre `status = 'active'` (`projects_active_name_key`) | NFC + casefold (`repository.name_key`) |
| status | text | `active` \| `archived` | |
| created_at / updated_at | timestamptz | | `rename_project` atualiza `updated_at` (extensão) |

**State transitions**: `active → archived` (cancela jobs não finais, libera o nome); `archived → active` (recusado
se o nome estiver em uso); `rename` (extensão) só muda `name`/`name_key`/`updated_at`, em qualquer estado, com a
mesma recusa de nome em uso entre ativos.

## Entity: Source (existente)

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| id, project_id | bigint | FK projeto, cascata | |
| kind | text | `folder` \| `file` | |
| mode | text | `in_place` \| `copied` | `copied` exige caminho sob `<DATA_DIR_HOST>/sources/<project_id>/` (extensão, regra da API) |
| path | text | não vazio | caminho do host, gravado como informado |

## Entity: Document (existente)

`id`, `project_id`, `source_id`, `path` (único por projeto), `created_at`. Status derivado do job mais recente
(`not_processed` sem job); `last_result` = resultado do job `succeeded` mais recente (`list_documents`).

## Entity: Job (existente + extensão)

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| id | bigint | PK | `str(id)` é o `job_id` da fila |
| project_id, document_id | bigint | FK composta, cascata | |
| requested_engine | text | um de `auto`, `docling`, `pymupdf`, `mineru`, `markitdown` | |
| status | text | `pending` \| `running` \| `succeeded` \| `failed` \| `cancelled`; um não final por documento | |
| requested_at, started_at, finished_at | timestamptz | `jobs_timestamps_check` | |
| error_summary | text | obrigatório em `failed` | motivo em português |
| published_at | timestamptz \| null | (extensão, migração 0002) | instante da confirmação do broker; nulo = mensagem nunca confirmada |

**State transitions** (quem dispara, nesta feature):

```text
pending --(1º evento do worker: start_job)--> running
pending --(publicação não confirmada: fail_job; extensão)--> failed
pending|running --(cancelar / arquivar / excluir projeto)--> cancelled
running --(result válido: complete_job)--> succeeded
running --(error definitivo | caminho de MD fora do data dir: fail_job)--> failed
running --(error passageiro)--> running   (nada gravado; aviso só no fluxo ao vivo)
estado final --(qualquer evento)--> sem mudança (InvalidJobTransitionError = duplicata, descartado)
```

## Entity: Attempt, Result, HistoryEvent (existentes)

- `Attempt` (`job_attempts`): uma linha por item de `report.attempts` do evento `result`, na ordem (`position`).
- `Result` (`job_results`): `markdown_path` (caminho do host informado pelo worker, já validado pela regra
  confinada), `engine`, colunas de `Quality`.
- `HistoryEvent`: tipos de `0001_initial.sql` mais `project_renamed` (extensão, migração 0002).

## Entity: ApiSettings (em memória)

| Field | Type | Origem |
|-------|------|--------|
| data_dir_host | str \| None | `ARTIGO2MD_DATA_DIR_HOST` |
| data_dir_mount | str \| None | `ARTIGO2MD_DATA_DIR_MOUNT` |
| allowed_origins | tuple[str, ...] | fixa no código: `tauri://localhost` (+ `http://localhost:1420` só com `ARTIGO2MD_API_ALLOW_DEV_ORIGIN`); sem variável própria (dec-052) |
| allow_dev_origin | bool | `ARTIGO2MD_API_ALLOW_DEV_ORIGIN` (padrão falso) |
| broker | host, port, credentials | `ARTIGO2MD_RABBITMQ_HOST/PORT`, `RABBITMQ_DEFAULT_USER/PASS` |
| db | `DbSettings` | `DbSettings.from_env` |
| data_dir_error, broker_error, db_error | str \| None | texto em português, sem valor de credencial, da área que falhou (a área com erro fica `None`) |
| warnings | tuple[str, ...] | avisos de configuração, também registrados em log |

`credentials` com `repr` que não mostra a senha (mesmo cuidado de `jobqueue.settings.Credentials`).

## Entity: Subscriber (em memória)

| Field | Type | Description |
|-------|------|-------------|
| project_id | int | filtro do fluxo |
| queue | `asyncio.Queue` (maxsize 1000) | eventos pendentes de envio |
| closed | bool | marcado quando a fila enche ou o cliente sai |
| overflowed | bool | a fila encheu: o cliente não consome e o fluxo termina sem entregar o que sobrou |

Ciclo de vida: criado ao abrir `GET /v1/projects/{id}/events`; removido no `finally` do gerador (desconexão,
fila cheia ou encerramento da API).

## Mapeamento evento da fila → gravação

| Evento (`queue-messages.md`) | Gravação via `store` | Fluxo ao vivo |
|------------------------------|----------------------|---------------|
| `progress` | `start_job` se `pending` | `progress` (+ `job_state running` na transição) |
| `result` | transação: `start_job`?, `record_attempt` × N, `complete_job` | `job_state succeeded` |
| `error` `retryable=true` | `start_job` se `pending` | `warning` |
| `error` `retryable=false` | transação: `start_job`?, `fail_job(job_id, error_summary=reason, message=...)` | `job_state failed` |
| fora do contrato / job inexistente / job final / projeto arquivado / `result` duplicado | nenhuma (log em português; duplicata, job final e projeto arquivado em `INFO`; os demais em `WARNING`) | nada |
