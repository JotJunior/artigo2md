# Contract: extensões da camada `artigo2md.store` para a project-api

> **Contrato implementado e verificado** (`src/artigo2md/store/repository.py`, `models.py`,
> `migrations/0002_project_api.sql`; testes em `tests/test_store_extensions_integration.py`, `tests/test_store_migrations.py`
> e `tests/test_fake_store_parity.py`). Acrescenta operações à camada definida em
> [`docs/specs/job-store/contracts/repository.md`](../../job-store/contracts/repository.md) sem mudar o
> comportamento das existentes, exceto onde indicado. Nomes em inglês, mensagens em português. Tipos de retorno
> são os dataclasses existentes de `src/artigo2md/store/models.py` (`Project`, `Source`, `Document`, `Job`).

## Operações novas (em `StoreSession`, delegadas por `Store` como as demais)

| Método | Retorno | Recusas (exceção existente) | Req. da project-api |
|--------|---------|-----------------------------|---------------------|
| `rename_project(project_id, name)` | `Project` (nome sem espaços nas pontas, `updated_at = now()`) | nome vazio → `StoreValidationError`; `name_key` em uso por **outro** projeto ativo → `ProjectNameTakenError` (mesmo índice `projects_active_name_key`); projeto inexistente → `ProjectNotFoundError`; mesmo `name_key` do próprio projeto = só atualiza a grafia; emite `project_renamed` | FR-002 |
| `get_source(source_id)` | `Source` | inexistente → `StoreNotFoundError` com `"Fonte não encontrada."` | FR-005, FR-006 |
| `list_sources(project_id)` | `list[Source]` por `id` | `ProjectNotFoundError` | FR-005 |
| `get_document(document_id)` | `Document` | `DocumentNotFoundError` | FR-010, FR-022, FR-024 |
| `get_job(job_id)` | `Job` | `JobNotFoundError` | FR-013, FR-018 |
| `list_project_job_ids(project_id)` | `list[int]` (todos os jobs, de qualquer estado) | `ProjectNotFoundError` | FR-004 (limpeza de `<data_dir>/<job_id>/`) |
| `mark_job_published(job_id)` | `Job` | `JobNotFoundError`; grava `published_at = now()` só se nulo e o job não é final (idempotente); job que já chegou a estado final antes da marcação é devolvido sem alteração (o gatilho recusa qualquer `UPDATE` em job final) | FR-011 |
| `list_open_jobs(project_id)` | `list[Job]` com `status IN ('pending', 'running')`, por `id`; trava a linha do projeto (`FOR UPDATE`) até o fim da transação | `ProjectNotFoundError` | FR-019, FR-020 (`job_state cancelled` ao vivo no `archive`/`DELETE`: lida antes da operação, na mesma transação, é exatamente o conjunto cancelado) |
| `list_unpublished_jobs(limit=1000)` | `list[Job]` com `status = 'pending'` e `published_at IS NULL`, por `id` | `limit` fora de 1..100 000 → `StoreValidationError` | FR-011, SC-006 |

`get_source` reaproveita `StoreNotFoundError` (base já existente) para não criar classe nova.

## Mudança de comportamento em operação existente

| Método | Antes (`_TRANSITIONS` de `repository.py` em `main`) | Depois |
|--------|-------------------------------|--------|
| `fail_job` | só `running → failed` | `pending → failed` **ou** `running → failed`; o CHECK `jobs_timestamps_check` já aceita `failed` com `started_at` nulo, mas o trigger `jobs_guard_transition` (`0001_initial.sql`) recusava `pending → failed`: a migração `0002` troca a função do trigger (abaixo) |

Motivo: o job cuja publicação não foi confirmada nunca chegou a um worker; registrá-lo como iniciado seria falso
(FR-011).

`delete_project` não muda: continua sem tocar em arquivos; a API lista os jobs (`list_project_job_ids`) e as
fontes copiadas na **mesma** transação antes de excluir, e remove os arquivos depois do `COMMIT` (ver
[http-api.md](http-api.md), `DELETE /v1/projects/{project_id}`).

## Migração `0002_project_api.sql`

| Mudança | Detalhe |
|---------|---------|
| `ALTER TABLE jobs ADD COLUMN published_at timestamptz` | nula; preenchida por `mark_job_published` |
| índice parcial `jobs_unpublished_idx ON jobs (id) WHERE status = 'pending' AND published_at IS NULL` | leitura de `list_unpublished_jobs` na partida |
| troca do CHECK `history_events_event_type_check` | mesmos 12 tipos de `0001_initial.sql` **mais** `project_renamed` |
| `CREATE OR REPLACE FUNCTION jobs_guard_transition()` | mesmo corpo da função de `0001_initial.sql`, com `pending` passando a aceitar `running`, `cancelled` **e** `failed`; demais regras (imutabilidade, estado final) sem mudança |
| `EVENT_TYPES` em `models.py` | acrescenta `project_renamed`; o teste `tests/test_store_migrations.py:69`, que compara a lista do CHECK com `models.EVENT_TYPES`, passa a ler o CHECK vigente depois de todas as migrações |

**Premissa para jobs `pending` já existentes**: ao aplicar a `0002`, todo job `pending` tem `published_at` nulo e,
portanto, é tratado como **não publicado**: `list_unpublished_jobs` o devolve na primeira partida e a API o publica
uma vez (at-least-once, R-06). Se a mensagem original já tinha sido publicada por outro meio, o worker pode receber
uma duplicata; o resultado repetido é descartado pela transição do job (FR-016). Nenhum backfill de `published_at` é feito pela
migração.

`Job` ganha o campo `published_at: datetime | None`. A migração segue as regras de `migrations.md` do job-store
(arquivo novo, checksum próprio; `0001_initial.sql` não é alterado).
