"""Camada de repositório assíncrona sobre o Postgres da stack local (contracts/repository.md).

`Store` abre um pool e expõe as operações; `Store.transaction()` devolve uma `StoreSession` com as MESMAS
operações todas na mesma transação. Fora de `transaction()`, cada operação roda na sua transação curta.

Regras de segurança aplicadas aqui (FR-015, FR-016, SC-007):
- toda consulta usa parâmetros `$n`; nenhum valor do usuário é concatenado em SQL (os únicos fragmentos de SQL
  montados em código são constantes fixas de `_TRANSITIONS`);
- erros do driver são traduzidos para a hierarquia própria com `raise ... from None`; da exceção do driver só se
  leem `sqlstate` e `constraint_name`; `message`, `detail` e `hint` do servidor nunca entram na mensagem;
- nenhuma operação lê, grava ou apaga arquivos do disco (FR-002, FR-011).
"""
from __future__ import annotations

import asyncio
import contextlib
import functools
import math
import unicodedata
from collections.abc import AsyncIterator, Sequence
from typing import Any

import asyncpg

from .config import DbSettings
from .errors import (
    ActiveJobExistsError,
    DatabaseUnavailableError,
    DocumentNotFoundError,
    InvalidJobTransitionError,
    JobNotFoundError,
    ProjectNameTakenError,
    ProjectNotFoundError,
    StoreDatabaseError,
    StoreError,
    StoreNotFoundError,
    StoreValidationError,
)
from .models import (
    EVENT_TYPES,
    NOT_PROCESSED,
    REQUESTED_ENGINES,
    SOURCE_KINDS,
    SOURCE_MODES,
    ArchiveOutcome,
    Attempt,
    Document,
    DocumentStatus,
    HistoryEvent,
    Job,
    Project,
    QualityRecord,
    Result,
    ResultSummary,
    Source,
)

MAX_EVENTS_PAGE = 500
MAX_UNPUBLISHED_PAGE = 100_000

_NAME_INDEX = "projects_active_name_key"
_ACTIVE_JOB_INDEX = "jobs_one_active_per_document"

# Transições de job (FR-010): estado novo -> (estados de origem aceitos, fragmento SET fixo).
# Os fragmentos são constantes do código; nenhum valor do usuário entra neles.
_TRANSITIONS: dict[str, tuple[tuple[str, ...], str]] = {
    "running": (("pending",), "started_at = now()"),
    "succeeded": (("running",), "finished_at = now()"),
    # `pending -> failed` (migração 0002): job cuja publicação na fila não foi confirmada nunca chegou a um worker.
    "failed": (("pending", "running"), "finished_at = now(), error_summary = $3"),
    "cancelled": (("pending", "running"), "finished_at = now()"),
}


# --- Validação de entrada -------------------------------------------------------------------------------


def _text(value: Any, label: str, *, allow_empty: bool = False) -> str:
    """Texto válido para o banco: `str`, sem caractere nulo e codificável em UTF-8."""
    if not isinstance(value, str):
        raise StoreValidationError(f"O campo {label} precisa ser um texto.")
    if not allow_empty and value.strip() == "":
        raise StoreValidationError(f"O campo {label} não pode ficar vazio.")
    if "\x00" in value:
        raise StoreValidationError(f"O campo {label} não pode conter o caractere nulo.")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise StoreValidationError(f"O campo {label} contém caracteres inválidos.") from None
    return value


def _id(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 < value < 2**63:
        raise StoreValidationError(f"O identificador {label} precisa ser um inteiro positivo.")
    return value


def _choice(value: Any, allowed: Sequence[str], label: str) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise StoreValidationError(f"O campo {label} precisa ser um destes valores: {', '.join(allowed)}.")
    return value


def name_key(name: str) -> str:
    """Chave de unicidade do nome (FR-001): NFC + `casefold` + NFC, independente da localidade do banco."""
    return unicodedata.normalize("NFC", unicodedata.normalize("NFC", name).casefold())


# --- Tradução de erros do driver ------------------------------------------------------------------------


def _translate(
    exc: BaseException, *, fk_error: StoreError | None = None, command_timeout: float | None = None
) -> StoreError | None:
    """Converte um erro do driver em erro da camada; `None` quando não é erro do driver (propaga o original).

    Lê só `sqlstate` e `constraint_name`; o texto do servidor nunca é copiado (FR-016).
    """
    if isinstance(exc, StoreError):
        return None
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        waited = f"em {command_timeout:g} s" if command_timeout else "no prazo"
        return DatabaseUnavailableError(f"O banco não respondeu {waited}; tente de novo.")
    if isinstance(exc, asyncpg.PostgresError):
        sqlstate = getattr(exc, "sqlstate", None) or ""
        if isinstance(exc, asyncpg.UniqueViolationError):
            constraint = getattr(exc, "constraint_name", None)
            if constraint == _NAME_INDEX:
                return ProjectNameTakenError("Já existe um projeto ativo com esse nome.")
            if constraint == _ACTIVE_JOB_INDEX:
                return ActiveJobExistsError("O documento já tem um job pendente ou em andamento.")
            return StoreDatabaseError("O banco recusou a gravação por duplicidade.")
        if isinstance(exc, asyncpg.ForeignKeyViolationError):
            return fk_error or StoreValidationError(
                "Algum registro citado não existe ou não pertence a este projeto."
            )
        if isinstance(exc, asyncpg.CheckViolationError):
            return StoreValidationError("Algum valor está fora do domínio aceito pelo banco.")
        if isinstance(exc, asyncpg.PostgresConnectionError) or sqlstate.startswith("08") or isinstance(
            exc,
            (asyncpg.CannotConnectNowError, asyncpg.AdminShutdownError, asyncpg.QueryCanceledError),
        ):
            return DatabaseUnavailableError("A conexão com o banco foi perdida ou recusada; tente de novo.")
        if isinstance(exc, asyncpg.DataError) or sqlstate.startswith("22"):
            return StoreValidationError("Algum valor informado não é aceito pelo banco.")
        code = sqlstate or "desconhecido"
        return StoreDatabaseError(f"O banco recusou o comando (SQLSTATE {code}).")
    if isinstance(exc, (asyncpg.InterfaceError, OSError)):
        return DatabaseUnavailableError("A conexão com o banco foi perdida ou recusada; tente de novo.")
    return None


# --- Mapeamento de linhas -------------------------------------------------------------------------------


def _row_to_project(row: Any) -> Project:
    return Project(
        id=row["id"], name=row["name"], status=row["status"], created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_source(row: Any) -> Source:
    return Source(
        id=row["id"], project_id=row["project_id"], kind=row["kind"], mode=row["mode"], path=row["path"],
        created_at=row["created_at"],
    )


def _row_to_document(row: Any) -> Document:
    return Document(
        id=row["id"], project_id=row["project_id"], source_id=row["source_id"], path=row["path"],
        created_at=row["created_at"],
    )


def _row_to_job(row: Any) -> Job:
    return Job(
        id=row["id"], project_id=row["project_id"], document_id=row["document_id"],
        requested_engine=row["requested_engine"], status=row["status"], requested_at=row["requested_at"],
        started_at=row["started_at"], finished_at=row["finished_at"], error_summary=row["error_summary"],
        published_at=row["published_at"],
    )


def _quality_from_row(row: Any) -> QualityRecord:
    return QualityRecord(
        score=row["score"], accepted=row["accepted"], tables_found=row["tables_found"],
        tables_expected=row["tables_expected"], chars_per_page=row["chars_per_page"],
        issues=tuple(row["issues"]),
    )


def _row_to_attempt(row: Any) -> Attempt:
    return Attempt(
        id=row["id"], job_id=row["job_id"], position=row["position"], engine=row["engine"],
        seconds=row["seconds"], quality=_quality_from_row(row) if row["score"] is not None else None,
        error=row["error"], created_at=row["created_at"],
    )


def _row_to_result(row: Any) -> Result:
    return Result(
        job_id=row["job_id"], markdown_path=row["markdown_path"], engine=row["engine"],
        quality=_quality_from_row(row), created_at=row["created_at"],
    )


def _row_to_event(row: Any) -> HistoryEvent:
    return HistoryEvent(
        id=row["id"], project_id=row["project_id"], document_id=row["document_id"], job_id=row["job_id"],
        event_type=row["event_type"], occurred_at=row["occurred_at"], message=row["message"],
    )


# --- Execução de SQL (ponto único) ----------------------------------------------------------------------


class _Executor:
    """Único ponto por onde o SQL do repositório passa: conta consultas e traduz erros do driver."""

    def __init__(self, connection: Any, store: "Store") -> None:
        self._connection = connection
        self._store = store

    async def _run(self, method: str, sql: str, args: tuple[Any, ...], fk_error: StoreError | None) -> Any:
        self._store._queries += 1
        try:
            return await getattr(self._connection, method)(sql, *args)
        except Exception as exc:
            translated = _translate(exc, fk_error=fk_error, command_timeout=self._store._command_timeout)
            if translated is None:
                raise
            raise translated from None

    async def fetch(self, sql: str, *args: Any, fk_error: StoreError | None = None) -> list[Any]:
        return await self._run("fetch", sql, args, fk_error)

    async def fetchrow(self, sql: str, *args: Any, fk_error: StoreError | None = None) -> Any:
        return await self._run("fetchrow", sql, args, fk_error)

    async def fetchval(self, sql: str, *args: Any, fk_error: StoreError | None = None) -> Any:
        return await self._run("fetchval", sql, args, fk_error)

    async def execute(self, sql: str, *args: Any, fk_error: StoreError | None = None) -> str:
        return await self._run("execute", sql, args, fk_error)


# --- Operações ------------------------------------------------------------------------------------------


class StoreSession:
    """Operações do repositório sobre uma conexão e uma transação já abertas por `Store`.

    Não é construída diretamente: use `Store.transaction()` ou chame as operações em `Store`.
    """

    def __init__(self, executor: _Executor) -> None:
        self._db = executor

    # -- Eventos (uso interno) --

    async def _emit(
        self, project_id: int, event_type: str, message: str, *, document_id: int | None = None,
        job_id: int | None = None,
    ) -> HistoryEvent:
        row = await self._db.fetchrow(
            "INSERT INTO history_events (project_id, document_id, job_id, event_type, message) "
            "VALUES ($1, $2, $3, $4, $5) RETURNING *",
            project_id, document_id, job_id, event_type, message,
        )
        return _row_to_event(row)

    # -- Projetos --

    async def create_project(self, name: str) -> Project:
        """Cria projeto ativo; o nome é gravado sem espaços nas pontas (FR-001)."""
        clean = _text(name, "name").strip()
        row = await self._db.fetchrow(
            "INSERT INTO projects (name, name_key) VALUES ($1, $2) "
            "ON CONFLICT (name_key) WHERE status = 'active' DO NOTHING RETURNING *",
            clean, name_key(clean),
        )
        if row is None:
            raise ProjectNameTakenError("Já existe um projeto ativo com esse nome.")
        project = _row_to_project(row)
        await self._emit(project.id, "project_created", "Projeto criado.")
        return project

    async def get_project(self, project_id: int) -> Project:
        row = await self._db.fetchrow("SELECT * FROM projects WHERE id = $1", _id(project_id, "project_id"))
        if row is None:
            raise ProjectNotFoundError("Projeto não encontrado.")
        return _row_to_project(row)

    async def list_projects(self, include_archived: bool = False) -> list[Project]:
        rows = await self._db.fetch(
            "SELECT * FROM projects WHERE ($1::boolean OR status = 'active') ORDER BY created_at, id",
            bool(include_archived),
        )
        return [_row_to_project(row) for row in rows]

    async def _lock_project(self, project_id: int) -> Any:
        row = await self._db.fetchrow("SELECT * FROM projects WHERE id = $1 FOR UPDATE", _id(project_id, "project_id"))
        if row is None:
            raise ProjectNotFoundError("Projeto não encontrado.")
        return row

    async def _require_project(self, project_id: int) -> None:
        """Só no caso de resultado vazio: distingue "sem registros" de "projeto inexistente"."""
        if await self._db.fetchval("SELECT 1 FROM projects WHERE id = $1", project_id) is None:
            raise ProjectNotFoundError("Projeto não encontrado.")

    async def _cancel_open_jobs(self, project_id: int) -> list[tuple[int, int]]:
        """Cancela jobs pendentes/em andamento do projeto; devolve `(job_id, document_id)` em ordem de id."""
        rows = await self._db.fetch(
            "UPDATE jobs SET status = 'cancelled', finished_at = now() "
            "WHERE project_id = $1 AND status IN ('pending', 'running') RETURNING id, document_id",
            project_id,
        )
        return sorted((row["id"], row["document_id"]) for row in rows)

    async def archive_project(self, project_id: int) -> ArchiveOutcome:
        """Arquiva, cancela os jobs não finais e grava um evento por job e um `project_archived` (FR-011)."""
        current = await self._lock_project(project_id)
        if current["status"] == "archived":
            raise StoreValidationError("O projeto já está arquivado.")
        row = await self._db.fetchrow(
            "UPDATE projects SET status = 'archived', updated_at = now() WHERE id = $1 RETURNING *", project_id
        )
        cancelled = await self._cancel_open_jobs(project_id)
        for job_id, document_id in cancelled:
            await self._emit(
                project_id, "job_cancelled", "Job cancelado porque o projeto foi arquivado.",
                document_id=document_id, job_id=job_id,
            )
        await self._emit(project_id, "project_archived", "Projeto arquivado.")
        return ArchiveOutcome(project=_row_to_project(row), cancelled_job_ids=tuple(j for j, _ in cancelled))

    async def unarchive_project(self, project_id: int) -> Project:
        """Reativa o projeto; recusa se outro projeto ativo já usa o nome (sem renomear nada)."""
        current = await self._lock_project(project_id)
        if current["status"] == "active":
            raise StoreValidationError("O projeto já está ativo.")
        row = await self._db.fetchrow(
            "UPDATE projects SET status = 'active', updated_at = now() WHERE id = $1 RETURNING *", project_id
        )
        await self._emit(project_id, "project_unarchived", "Projeto desarquivado.")
        return _row_to_project(row)

    async def rename_project(self, project_id: int, name: str) -> Project:
        """Renomeia o projeto (nome sem espaços nas pontas); emite `project_renamed` (FR-002).

        Mesmo `name_key` do próprio projeto só atualiza a grafia; chave em uso por OUTRO projeto ativo é
        recusada pelo índice `projects_active_name_key` (`ProjectNameTakenError`).
        """
        project_id = _id(project_id, "project_id")
        clean = _text(name, "name").strip()
        await self._lock_project(project_id)
        row = await self._db.fetchrow(
            "UPDATE projects SET name = $2, name_key = $3, updated_at = now() WHERE id = $1 RETURNING *",
            project_id, clean, name_key(clean),
        )
        await self._emit(project_id, "project_renamed", "Projeto renomeado.")
        return _row_to_project(row)

    async def delete_project(self, project_id: int) -> None:
        """Cancela jobs não finais e remove o projeto; a cascata remove o resto. Nenhum arquivo é tocado."""
        await self._lock_project(project_id)
        await self._cancel_open_jobs(project_id)
        await self._db.execute("DELETE FROM projects WHERE id = $1", project_id)

    # -- Fontes e documentos --

    async def add_source(self, project_id: int, kind: str, mode: str, path: str) -> Source:
        """Registra a fonte sem verificar o caminho no disco (FR-002)."""
        project_id = _id(project_id, "project_id")
        _choice(kind, SOURCE_KINDS, "kind")
        _choice(mode, SOURCE_MODES, "mode")
        _text(path, "path")
        row = await self._db.fetchrow(
            "INSERT INTO sources (project_id, kind, mode, path) VALUES ($1, $2, $3, $4) RETURNING *",
            project_id, kind, mode, path,
            fk_error=ProjectNotFoundError("Projeto não encontrado."),
        )
        source = _row_to_source(row)
        await self._emit(project_id, "source_added", "Fonte adicionada.")
        return source

    async def get_source(self, source_id: int) -> Source:
        row = await self._db.fetchrow("SELECT * FROM sources WHERE id = $1", _id(source_id, "source_id"))
        if row is None:
            raise StoreNotFoundError("Fonte não encontrada.")
        return _row_to_source(row)

    async def list_sources(self, project_id: int) -> list[Source]:
        """Fontes do projeto por `id`; projeto inexistente é `ProjectNotFoundError`."""
        project_id = _id(project_id, "project_id")
        rows = await self._db.fetch("SELECT * FROM sources WHERE project_id = $1 ORDER BY id", project_id)
        if not rows:
            await self._require_project(project_id)
        return [_row_to_source(row) for row in rows]

    async def get_document(self, document_id: int) -> Document:
        row = await self._db.fetchrow("SELECT * FROM documents WHERE id = $1", _id(document_id, "document_id"))
        if row is None:
            raise DocumentNotFoundError("Documento não encontrado.")
        return _row_to_document(row)

    async def add_document(self, project_id: int, source_id: int, path: str) -> tuple[Document, bool]:
        """Registra o documento; se o caminho já existe no projeto, devolve o existente (fonte original)."""
        project_id = _id(project_id, "project_id")
        source_id = _id(source_id, "source_id")
        _text(path, "path")
        owned = await self._db.fetchval(
            "SELECT 1 FROM sources WHERE id = $1 AND project_id = $2", source_id, project_id
        )
        if owned is None:
            exists = await self._db.fetchval("SELECT 1 FROM projects WHERE id = $1", project_id)
            if exists is None:
                raise ProjectNotFoundError("Projeto não encontrado.")
            raise StoreValidationError("A fonte informada não existe ou não pertence a este projeto.")
        row = await self._db.fetchrow(
            "INSERT INTO documents (project_id, source_id, path) VALUES ($1, $2, $3) "
            "ON CONFLICT (project_id, path) DO NOTHING RETURNING *",
            project_id, source_id, path,
        )
        if row is None:
            existing = await self._db.fetchrow(
                "SELECT * FROM documents WHERE project_id = $1 AND path = $2", project_id, path
            )
            return _row_to_document(existing), False
        document = _row_to_document(row)
        await self._emit(project_id, "document_added", "Documento adicionado.", document_id=document.id)
        return document, True

    async def list_documents(self, project_id: int) -> list[DocumentStatus]:
        """Documentos do projeto com status derivado e resumo do último resultado, em UMA consulta (FR-009)."""
        project_id = _id(project_id, "project_id")
        rows = await self._db.fetch(
            """
            SELECT d.id AS document_id, d.project_id, d.source_id, d.path,
                   lj.id AS latest_job_id, lj.status AS latest_status, lj.requested_at AS latest_requested_at,
                   lr.job_id AS result_job_id, lr.markdown_path AS result_markdown_path,
                   lr.engine AS result_engine, lr.score AS result_score, lr.accepted AS result_accepted,
                   lr.created_at AS result_created_at
              FROM documents d
              LEFT JOIN LATERAL (
                    SELECT j.id, j.status, j.requested_at FROM jobs j
                     WHERE j.document_id = d.id
                     ORDER BY j.requested_at DESC, j.id DESC LIMIT 1
              ) lj ON true
              LEFT JOIN LATERAL (
                    SELECT r.job_id, r.markdown_path, r.engine, r.score, r.accepted, r.created_at
                      FROM jobs j2 JOIN job_results r ON r.job_id = j2.id
                     WHERE j2.document_id = d.id AND j2.status = 'succeeded'
                     ORDER BY j2.requested_at DESC, j2.id DESC LIMIT 1
              ) lr ON true
             WHERE d.project_id = $1
             ORDER BY d.id
            """,
            project_id,
        )
        if not rows:
            # Só no caso vazio: distingue "projeto sem documentos" de "projeto inexistente".
            if await self._db.fetchval("SELECT 1 FROM projects WHERE id = $1", project_id) is None:
                raise ProjectNotFoundError("Projeto não encontrado.")
            return []
        statuses = []
        for row in rows:
            summary = None
            if row["result_job_id"] is not None:
                summary = ResultSummary(
                    job_id=row["result_job_id"], markdown_path=row["result_markdown_path"],
                    engine=row["result_engine"], score=row["result_score"], accepted=row["result_accepted"],
                    created_at=row["result_created_at"],
                )
            statuses.append(
                DocumentStatus(
                    document_id=row["document_id"], project_id=row["project_id"], source_id=row["source_id"],
                    path=row["path"], status=row["latest_status"] or NOT_PROCESSED,
                    latest_job_id=row["latest_job_id"], latest_job_requested_at=row["latest_requested_at"],
                    last_result=summary,
                )
            )
        return statuses

    # -- Jobs --

    async def create_job(self, document_id: int, requested_engine: str) -> Job:
        """Cria job `pending`; recusa se o documento já tem job pendente/em andamento (FR-004)."""
        document_id = _id(document_id, "document_id")
        _choice(requested_engine, REQUESTED_ENGINES, "requested_engine")
        document = await self._db.fetchrow("SELECT id, project_id FROM documents WHERE id = $1", document_id)
        if document is None:
            raise DocumentNotFoundError("Documento não encontrado.")
        row = await self._db.fetchrow(
            "INSERT INTO jobs (project_id, document_id, requested_engine) VALUES ($1, $2, $3) "
            "ON CONFLICT (document_id) WHERE status IN ('pending', 'running') DO NOTHING RETURNING *",
            document["project_id"], document_id, requested_engine,
            fk_error=DocumentNotFoundError("Documento não encontrado."),
        )
        if row is None:
            raise ActiveJobExistsError("O documento já tem um job pendente ou em andamento.")
        job = _row_to_job(row)
        await self._emit(
            job.project_id, "job_created", "Job criado.", document_id=job.document_id, job_id=job.id
        )
        return job

    async def get_job(self, job_id: int) -> Job:
        row = await self._db.fetchrow("SELECT * FROM jobs WHERE id = $1", _id(job_id, "job_id"))
        if row is None:
            raise JobNotFoundError("Job não encontrado.")
        return _row_to_job(row)

    async def list_project_job_ids(self, project_id: int) -> list[int]:
        """Ids de todos os jobs do projeto, de qualquer estado, em ordem (limpeza de `<data_dir>/<job_id>/`)."""
        project_id = _id(project_id, "project_id")
        rows = await self._db.fetch("SELECT id FROM jobs WHERE project_id = $1 ORDER BY id", project_id)
        if not rows:
            await self._require_project(project_id)
        return [row["id"] for row in rows]

    async def list_open_jobs(self, project_id: int) -> list[Job]:
        """Jobs `pending|running` do projeto, em ordem de id; `ProjectNotFoundError` se o projeto não existe.

        Trava a linha do projeto (`FOR UPDATE`) até o fim da transação: lida antes de `archive_project`/
        `delete_project` na MESMA transação, a lista é exatamente o conjunto que essas operações cancelam, sem
        corrida com um worker concluindo um job (fluxo ao vivo `job_state cancelled`, `live-events.md`).
        """
        project_id = _id(project_id, "project_id")
        await self._lock_project(project_id)
        rows = await self._db.fetch(
            "SELECT * FROM jobs WHERE project_id = $1 AND status IN ('pending', 'running') ORDER BY id", project_id
        )
        return [_row_to_job(row) for row in rows]

    async def mark_job_published(self, job_id: int) -> Job:
        """Grava `published_at = now()` só se nulo (idempotente); devolve o job (FR-011).

        Job que já chegou a estado final antes da marcação (worker mais rápido que a API) é devolvido sem
        alteração: o gatilho do banco recusa qualquer UPDATE em job final, e só jobs `pending` são
        candidatos a republicação (`list_unpublished_jobs`).
        """
        job_id = _id(job_id, "job_id")
        row = await self._db.fetchrow(
            "UPDATE jobs SET published_at = now() "
            "WHERE id = $1 AND published_at IS NULL AND status IN ('pending', 'running') RETURNING *",
            job_id,
        )
        if row is not None:
            return _row_to_job(row)
        return await self.get_job(job_id)

    async def list_unpublished_jobs(self, limit: int = 1000) -> list[Job]:
        """Jobs `pending` sem `published_at`, por `id` (republicação na partida da API, FR-011)."""
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_UNPUBLISHED_PAGE:
            raise StoreValidationError(f"O limite precisa estar entre 1 e {MAX_UNPUBLISHED_PAGE}.")
        rows = await self._db.fetch(
            "SELECT * FROM jobs WHERE status = 'pending' AND published_at IS NULL ORDER BY id LIMIT $1", limit
        )
        return [_row_to_job(row) for row in rows]

    async def _transition(self, job_id: int, new_status: str, error_summary: str | None = None) -> Job:
        """Transição atômica `UPDATE ... WHERE status = ANY(...)`; zero linhas distingue inexistente de inválido."""
        job_id = _id(job_id, "job_id")
        allowed, assignments = _TRANSITIONS[new_status]
        params: list[Any] = [job_id, list(allowed)]
        if new_status == "failed":
            params.append(error_summary)
        row = await self._db.fetchrow(
            f"UPDATE jobs SET status = '{new_status}', {assignments} "  # fragmentos fixos de _TRANSITIONS
            "WHERE id = $1 AND status = ANY($2::text[]) RETURNING *",
            *params,
        )
        if row is not None:
            return _row_to_job(row)
        current = await self._db.fetchval("SELECT status FROM jobs WHERE id = $1", job_id)
        if current is None:
            raise JobNotFoundError("Job não encontrado.")
        raise InvalidJobTransitionError(
            f"Transição de estado recusada: o job está '{current}' e não pode passar para '{new_status}'."
        )

    async def start_job(self, job_id: int) -> Job:
        job = await self._transition(job_id, "running")
        await self._emit(job.project_id, "job_started", "Job iniciado.", document_id=job.document_id, job_id=job.id)
        return job

    async def record_attempt(
        self, job_id: int, engine: str, seconds: float, quality: QualityRecord | None = None,
        error: str | None = None,
    ) -> Attempt:
        """Registra uma tentativa de motor num job `running`, na ordem em que ocorreu (FR-005)."""
        job_id = _id(job_id, "job_id")
        _text(engine, "engine")
        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not math.isfinite(seconds) or seconds < 0:
            raise StoreValidationError("A duração (seconds) da tentativa não pode ser negativa.")
        if quality is not None and not isinstance(quality, QualityRecord):
            raise StoreValidationError("A qualidade da tentativa precisa ser um QualityRecord.")
        if error is not None:
            _text(error, "error", allow_empty=True)
        if quality is None and (error is None or error.strip() == ""):
            raise StoreValidationError("Uma tentativa precisa ter qualidade ou erro.")
        job = await self._db.fetchrow(
            "SELECT id, project_id, document_id, status FROM jobs WHERE id = $1 FOR UPDATE", job_id
        )
        if job is None:
            raise JobNotFoundError("Job não encontrado.")
        if job["status"] != "running":
            raise InvalidJobTransitionError("Só um job em andamento aceita tentativas.")
        position = await self._db.fetchval(
            "SELECT COALESCE(MAX(position), 0) + 1 FROM job_attempts WHERE job_id = $1", job_id
        )
        q = quality
        row = await self._db.fetchrow(
            "INSERT INTO job_attempts (job_id, position, engine, seconds, score, accepted, tables_found, "
            "tables_expected, chars_per_page, issues, error) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11) RETURNING *",
            job_id, position, engine, float(seconds),
            None if q is None else q.score, None if q is None else q.accepted,
            None if q is None else q.tables_found, None if q is None else q.tables_expected,
            None if q is None else q.chars_per_page, None if q is None else list(q.issues),
            error,
        )
        attempt = _row_to_attempt(row)
        await self._emit(
            job["project_id"], "attempt_recorded", f"Tentativa {position} registrada.",
            document_id=job["document_id"], job_id=job_id,
        )
        return attempt

    async def complete_job(
        self, job_id: int, markdown_path: str, engine: str, quality: QualityRecord, message: str
    ) -> tuple[Job, Result, HistoryEvent]:
        """`running -> succeeded` + resultado + evento, numa única transação (FR-006, FR-014)."""
        _text(markdown_path, "markdown_path")
        _text(engine, "engine")
        _text(message, "message")
        if not isinstance(quality, QualityRecord):
            raise StoreValidationError("A qualidade do resultado precisa ser um QualityRecord.")
        job = await self._transition(job_id, "succeeded")
        row = await self._db.fetchrow(
            "INSERT INTO job_results (job_id, markdown_path, engine, score, accepted, tables_found, "
            "tables_expected, chars_per_page, issues) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9) RETURNING *",
            job.id, markdown_path, engine, quality.score, quality.accepted, quality.tables_found,
            quality.tables_expected, quality.chars_per_page, list(quality.issues),
        )
        event = await self._emit(
            job.project_id, "job_succeeded", message, document_id=job.document_id, job_id=job.id
        )
        return job, _row_to_result(row), event

    async def fail_job(self, job_id: int, error_summary: str, message: str) -> tuple[Job, HistoryEvent]:
        """`pending|running -> failed` sem resultado; as tentativas já gravadas permanecem (FR-006, FR-011)."""
        _text(error_summary, "error_summary")
        _text(message, "message")
        job = await self._transition(job_id, "failed", error_summary)
        event = await self._emit(
            job.project_id, "job_failed", message, document_id=job.document_id, job_id=job.id
        )
        return job, event

    async def cancel_job(self, job_id: int, message: str) -> tuple[Job, HistoryEvent]:
        """`pending|running -> cancelled`; job já final é recusado (FR-010)."""
        _text(message, "message")
        job = await self._transition(job_id, "cancelled")
        event = await self._emit(
            job.project_id, "job_cancelled", message, document_id=job.document_id, job_id=job.id
        )
        return job, event

    async def get_result(self, job_id: int) -> Result | None:
        """`None` = job existe e não tem resultado; `JobNotFoundError` = job inexistente."""
        job_id = _id(job_id, "job_id")
        row = await self._db.fetchrow(
            "SELECT j.id AS existing_job_id, r.job_id, r.markdown_path, r.engine, r.score, r.accepted, "
            "r.tables_found, r.tables_expected, r.chars_per_page, r.issues, r.created_at "
            "FROM jobs j LEFT JOIN job_results r ON r.job_id = j.id WHERE j.id = $1",
            job_id,
        )
        if row is None:
            raise JobNotFoundError("Job não encontrado.")
        if row["job_id"] is None:
            return None
        return _row_to_result(row)

    async def list_attempts(self, job_id: int) -> list[Attempt]:
        """Tentativas em ordem de `position`; lista vazia = job sem tentativas."""
        job_id = _id(job_id, "job_id")
        if await self._db.fetchval("SELECT 1 FROM jobs WHERE id = $1", job_id) is None:
            raise JobNotFoundError("Job não encontrado.")
        rows = await self._db.fetch("SELECT * FROM job_attempts WHERE job_id = $1 ORDER BY position", job_id)
        return [_row_to_attempt(row) for row in rows]

    # -- Histórico --

    async def add_event(
        self, project_id: int, event_type: str, message: str, document_id: int | None = None,
        job_id: int | None = None,
    ) -> HistoryEvent:
        """Acrescenta um evento; documento e job precisam ser do projeto (FR-008).

        A linha do projeto é travada com `FOR SHARE` para a verificação e o `INSERT` não correrem contra uma
        exclusão concorrente do projeto: a exclusão espera esta transação terminar.
        """
        project_id = _id(project_id, "project_id")
        _choice(event_type, EVENT_TYPES, "event_type")
        _text(message, "message")
        if document_id is not None:
            document_id = _id(document_id, "document_id")
        if job_id is not None:
            job_id = _id(job_id, "job_id")
        if await self._db.fetchval("SELECT 1 FROM projects WHERE id = $1 FOR SHARE", project_id) is None:
            raise ProjectNotFoundError("Projeto não encontrado.")
        if document_id is not None:
            found = await self._db.fetchval(
                "SELECT 1 FROM documents WHERE id = $1 AND project_id = $2", document_id, project_id
            )
            if found is None:
                raise StoreValidationError("O documento informado não existe ou não pertence a este projeto.")
        if job_id is not None:
            job = await self._db.fetchrow(
                "SELECT document_id FROM jobs WHERE id = $1 AND project_id = $2", job_id, project_id
            )
            if job is None:
                raise StoreValidationError("O job informado não existe ou não pertence a este projeto.")
            if document_id is not None and job["document_id"] != document_id:
                raise StoreValidationError("O job informado não pertence ao documento informado.")
        return await self._emit(
            project_id, event_type, message, document_id=document_id, job_id=job_id
        )

    async def list_events(
        self, project_id: int | None = None, document_id: int | None = None, limit: int = 100, offset: int = 0
    ) -> list[HistoryEvent]:
        """Linha do tempo ordenada por `(occurred_at, id)`; exige exatamente um filtro (FR-008)."""
        if (project_id is None) == (document_id is None):
            raise StoreValidationError("Informe exatamente um filtro: project_id ou document_id.")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_EVENTS_PAGE:
            raise StoreValidationError(f"O limite precisa estar entre 1 e {MAX_EVENTS_PAGE}.")
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise StoreValidationError("O deslocamento (offset) não pode ser negativo.")
        if project_id is not None:
            rows = await self._db.fetch(
                "SELECT * FROM history_events WHERE project_id = $1 ORDER BY occurred_at, id LIMIT $2 OFFSET $3",
                _id(project_id, "project_id"), limit, offset,
            )
        else:
            rows = await self._db.fetch(
                "SELECT * FROM history_events WHERE document_id = $1 ORDER BY occurred_at, id LIMIT $2 OFFSET $3",
                _id(document_id, "document_id"), limit, offset,
            )
        return [_row_to_event(row) for row in rows]


# Operações públicas replicadas em `Store` (cada uma numa transação curta).
_OPERATIONS = (
    "create_project", "get_project", "list_projects", "archive_project", "unarchive_project", "delete_project",
    "add_source", "add_document", "list_documents", "create_job", "start_job", "record_attempt", "complete_job",
    "fail_job", "cancel_job", "get_result", "list_attempts", "add_event", "list_events",
    # project-api (migração 0002)
    "rename_project", "get_source", "list_sources", "get_document", "get_job", "list_project_job_ids",
    "mark_job_published", "list_unpublished_jobs",
    # project-api (fluxo ao vivo)
    "list_open_jobs",
)


# --- Ciclo de vida --------------------------------------------------------------------------------------


class Store:
    """Pool de conexões e operações do repositório (todas `async`, FR-014)."""

    def __init__(self, pool: Any, *, acquire_timeout: float, command_timeout: float | None) -> None:
        self._pool = pool
        self._acquire_timeout = acquire_timeout
        self._command_timeout = command_timeout
        self._queries = 0

    @classmethod
    async def open(
        cls,
        settings: DbSettings,
        *,
        min_size: int = 1,
        max_size: int = 5,
        connect_timeout: float = 10.0,
        command_timeout: float = 30.0,
        acquire_timeout: float = 10.0,
    ) -> "Store":
        """Abre o pool com argumentos nomeados (nunca um DSN em texto); falha vira `DatabaseUnavailableError`."""
        host = settings.resolve_connect_host()  # recusa host remoto antes de qualquer conexão (StoreConfigError)
        try:
            pool = await asyncpg.create_pool(
                min_size=min_size,
                max_size=max_size,
                host=host,
                port=settings.port,
                user=settings.user,
                password=settings.password,
                database=settings.database,
                timeout=connect_timeout,
                command_timeout=command_timeout,
            )
        except (OSError, asyncpg.PostgresError, asyncpg.InterfaceError, asyncio.TimeoutError):
            # Mensagem fixa: nada do erro do driver (usuário, banco, senha) é repassado.
            raise DatabaseUnavailableError(
                f"Não foi possível conectar ao Postgres em {settings.host}:{settings.port}. "
                "Confira se a stack está no ar (sh stack/up.sh)."
            ) from None
        return cls(pool, acquire_timeout=acquire_timeout, command_timeout=command_timeout)

    async def close(self) -> None:
        pool, self._pool = self._pool, None
        if pool is not None:
            with contextlib.suppress(Exception):
                await pool.close()

    async def __aenter__(self) -> "Store":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    @property
    def query_count(self) -> int:
        """Quantidade de comandos SQL de operações enviados por este `Store` (para o teste de SC-003)."""
        return self._queries

    def reset_query_count(self) -> None:
        self._queries = 0

    @contextlib.asynccontextmanager
    async def transaction(self) -> AsyncIterator[StoreSession]:
        """Adquire uma conexão e abre uma transação; as operações da sessão compartilham a mesma transação."""
        if self._pool is None:
            raise DatabaseUnavailableError("O Store está fechado; abra-o de novo com Store.open.")
        pool = self._pool
        try:
            connection = await pool.acquire(timeout=self._acquire_timeout)
        except (asyncio.TimeoutError, TimeoutError):
            raise DatabaseUnavailableError(
                "Todas as conexões com o banco estão ocupadas; tente de novo."
            ) from None
        except (OSError, asyncpg.PostgresError, asyncpg.InterfaceError):
            raise DatabaseUnavailableError(
                "Não foi possível obter uma conexão com o banco; tente de novo."
            ) from None
        try:
            try:
                async with connection.transaction():
                    yield StoreSession(_Executor(connection, self))
            except StoreError:
                raise
            except Exception as exc:
                # Erros do driver no BEGIN/COMMIT (inclui o gatilho diferido de "job concluído sem resultado").
                translated = _translate(exc, command_timeout=self._command_timeout)
                if translated is None:
                    raise
                raise translated from None
        finally:
            with contextlib.suppress(Exception):
                await pool.release(connection)


def _delegate(name: str) -> Any:
    session_method = getattr(StoreSession, name)

    @functools.wraps(session_method)
    async def method(self: Store, *args: Any, **kwargs: Any) -> Any:
        async with self.transaction() as session:
            return await getattr(session, name)(*args, **kwargs)

    return method


for _name in _OPERATIONS:
    setattr(Store, _name, _delegate(_name))
del _name
