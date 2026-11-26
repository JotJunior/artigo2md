"""Repositório falso em memória com a mesma interface pública de `artigo2md.store.Store` (tarefa 4.3, FR-034).

Serve aos testes da API sem Docker nem Postgres. Reproduz as exceções, as transições de job, a unicidade de nome
entre projetos ativos, um job ativo por documento, os eventos de histórico e a atomicidade de `transaction()`
(um erro desfaz tudo o que a transação gravou). A validação de entrada reaproveita as funções do repositório
real (`_text`, `_id`, `_choice`, `name_key`), de modo que mensagens e recusas coincidem; o teste de paridade
(`tests/test_fake_store_parity.py`) roda os mesmos cenários contra este fake e, com `ARTIGO2MD_DB_TESTS=1`,
contra o banco real.

As sequências de id não são desfeitas por rollback nem por INSERT recusado (como as do Postgres), então os ids
coincidem com os do banco real em cenários com recusas. Não acessa disco, rede nem relógio externo: o relógio interno avança 1 µs por leitura, o que dá a ordem estável
de `(occurred_at, id)` que o banco real garante com `clock_timestamp()`.
"""
from __future__ import annotations

import contextlib
import math
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Any

from artigo2md.store import errors
from artigo2md.store.models import (
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
from artigo2md.store.repository import (
    _OPERATIONS,
    _TRANSITIONS,
    MAX_EVENTS_PAGE,
    MAX_UNPUBLISHED_PAGE,
    _choice,
    _id,
    _text,
    name_key,
)

_EPOCH = datetime(2026, 1, 1, tzinfo=timezone.utc)


class _State:
    """Tabelas em memória; `snapshot()` desta classe é o ponto de retorno da transação."""

    def __init__(self) -> None:
        self.clock = 0
        self.ids = {"project": 0, "source": 0, "document": 0, "job": 0, "attempt": 0, "event": 0}
        self.projects: dict[int, Project] = {}
        self.name_keys: dict[int, str] = {}
        self.sources: dict[int, Source] = {}
        self.documents: dict[int, Document] = {}
        self.jobs: dict[int, Job] = {}
        self.attempts: dict[int, Attempt] = {}
        self.results: dict[int, Result] = {}
        self.events: list[HistoryEvent] = []

    def snapshot(self) -> "_State":
        """Ponto de retorno da transação: copia só os contêineres (os registros são `frozen`, logo imutáveis), o que
        mantém a transação O(contêineres) e não O(registros) — necessário para lotes de milhares de documentos."""
        copy_ = _State.__new__(_State)
        for name, value in vars(self).items():
            setattr(copy_, name, value.copy() if isinstance(value, (dict, list)) else value)
        return copy_

    def now(self) -> datetime:
        self.clock += 1
        return _EPOCH + timedelta(microseconds=self.clock)

    def next_id(self, table: str) -> int:
        self.ids[table] += 1
        return self.ids[table]


class FakeStoreSession:
    """Mesmas operações de `StoreSession`, sobre o estado em memória."""

    def __init__(self, state: _State) -> None:
        self._s = state

    # -- Eventos --

    def _emit(
        self, project_id: int, event_type: str, message: str, *, document_id: int | None = None,
        job_id: int | None = None,
    ) -> HistoryEvent:
        event = HistoryEvent(
            id=self._s.next_id("event"), project_id=project_id, document_id=document_id, job_id=job_id,
            event_type=event_type, occurred_at=self._s.now(), message=message,
        )
        self._s.events.append(event)
        return event

    # -- Projetos --

    def _active_key_owner(self, key: str, *, except_id: int | None = None) -> int | None:
        for project_id, project in self._s.projects.items():
            if project.status == "active" and project_id != except_id and self._s.name_keys[project_id] == key:
                return project_id
        return None

    def _project(self, project_id: int) -> Project:
        project = self._s.projects.get(project_id)
        if project is None:
            raise errors.ProjectNotFoundError("Projeto não encontrado.")
        return project

    async def create_project(self, name: str) -> Project:
        clean = _text(name, "name").strip()
        key = name_key(clean)
        if self._active_key_owner(key) is not None:
            self._s.next_id("project")  # INSERT ... ON CONFLICT DO NOTHING consome o valor da sequência
            raise errors.ProjectNameTakenError("Já existe um projeto ativo com esse nome.")
        now = self._s.now()
        project = Project(
            id=self._s.next_id("project"), name=clean, status="active", created_at=now, updated_at=now
        )
        self._s.projects[project.id] = project
        self._s.name_keys[project.id] = key
        self._emit(project.id, "project_created", "Projeto criado.")
        return project

    async def get_project(self, project_id: int) -> Project:
        return self._project(_id(project_id, "project_id"))

    async def list_projects(self, include_archived: bool = False) -> list[Project]:
        projects = [p for p in self._s.projects.values() if include_archived or p.status == "active"]
        return sorted(projects, key=lambda p: (p.created_at, p.id))

    def _cancel_open_jobs(self, project_id: int) -> list[tuple[int, int]]:
        cancelled = []
        for job in list(self._s.jobs.values()):
            if job.project_id == project_id and job.status in ("pending", "running"):
                self._s.jobs[job.id] = replace(job, status="cancelled", finished_at=self._s.now())
                cancelled.append((job.id, job.document_id))
        return sorted(cancelled)

    async def archive_project(self, project_id: int) -> ArchiveOutcome:
        project = self._project(_id(project_id, "project_id"))
        if project.status == "archived":
            raise errors.StoreValidationError("O projeto já está arquivado.")
        project = replace(project, status="archived", updated_at=self._s.now())
        self._s.projects[project.id] = project
        cancelled = self._cancel_open_jobs(project.id)
        for job_id, document_id in cancelled:
            self._emit(
                project.id, "job_cancelled", "Job cancelado porque o projeto foi arquivado.",
                document_id=document_id, job_id=job_id,
            )
        self._emit(project.id, "project_archived", "Projeto arquivado.")
        return ArchiveOutcome(project=project, cancelled_job_ids=tuple(j for j, _ in cancelled))

    async def unarchive_project(self, project_id: int) -> Project:
        project = self._project(_id(project_id, "project_id"))
        if project.status == "active":
            raise errors.StoreValidationError("O projeto já está ativo.")
        if self._active_key_owner(self._s.name_keys[project.id]) is not None:
            raise errors.ProjectNameTakenError("Já existe um projeto ativo com esse nome.")
        project = replace(project, status="active", updated_at=self._s.now())
        self._s.projects[project.id] = project
        self._emit(project.id, "project_unarchived", "Projeto desarquivado.")
        return project

    async def rename_project(self, project_id: int, name: str) -> Project:
        project_id = _id(project_id, "project_id")
        clean = _text(name, "name").strip()
        project = self._project(project_id)
        key = name_key(clean)
        if project.status == "active" and self._active_key_owner(key, except_id=project_id) is not None:
            raise errors.ProjectNameTakenError("Já existe um projeto ativo com esse nome.")
        project = replace(project, name=clean, updated_at=self._s.now())
        self._s.projects[project_id] = project
        self._s.name_keys[project_id] = key
        self._emit(project_id, "project_renamed", "Projeto renomeado.")
        return project

    async def delete_project(self, project_id: int) -> None:
        project = self._project(_id(project_id, "project_id"))
        self._cancel_open_jobs(project.id)
        pid = project.id
        self._s.projects.pop(pid)
        self._s.name_keys.pop(pid)
        job_ids = {j.id for j in self._s.jobs.values() if j.project_id == pid}
        self._s.sources = {k: v for k, v in self._s.sources.items() if v.project_id != pid}
        self._s.documents = {k: v for k, v in self._s.documents.items() if v.project_id != pid}
        self._s.jobs = {k: v for k, v in self._s.jobs.items() if v.project_id != pid}
        self._s.attempts = {k: v for k, v in self._s.attempts.items() if v.job_id not in job_ids}
        self._s.results = {k: v for k, v in self._s.results.items() if k not in job_ids}
        self._s.events = [e for e in self._s.events if e.project_id != pid]

    # -- Fontes e documentos --

    async def add_source(self, project_id: int, kind: str, mode: str, path: str) -> Source:
        project_id = _id(project_id, "project_id")
        _choice(kind, SOURCE_KINDS, "kind")
        _choice(mode, SOURCE_MODES, "mode")
        _text(path, "path")
        if project_id not in self._s.projects:
            self._s.next_id("source")  # o INSERT que viola a chave estrangeira também consome a sequência
        self._project(project_id)
        source = Source(
            id=self._s.next_id("source"), project_id=project_id, kind=kind, mode=mode, path=path,
            created_at=self._s.now(),
        )
        self._s.sources[source.id] = source
        self._emit(project_id, "source_added", "Fonte adicionada.")
        return source

    async def get_source(self, source_id: int) -> Source:
        source = self._s.sources.get(_id(source_id, "source_id"))
        if source is None:
            raise errors.StoreNotFoundError("Fonte não encontrada.")
        return source

    async def list_sources(self, project_id: int) -> list[Source]:
        project_id = _id(project_id, "project_id")
        self._project(project_id)
        return sorted((s for s in self._s.sources.values() if s.project_id == project_id), key=lambda s: s.id)

    async def add_document(self, project_id: int, source_id: int, path: str) -> tuple[Document, bool]:
        project_id = _id(project_id, "project_id")
        source_id = _id(source_id, "source_id")
        _text(path, "path")
        source = self._s.sources.get(source_id)
        if source is None or source.project_id != project_id:
            self._project(project_id)
            raise errors.StoreValidationError("A fonte informada não existe ou não pertence a este projeto.")
        for existing in self._s.documents.values():
            if existing.project_id == project_id and existing.path == path:
                self._s.next_id("document")  # ON CONFLICT DO NOTHING consome o valor da sequência
                return existing, False
        document = Document(
            id=self._s.next_id("document"), project_id=project_id, source_id=source_id, path=path,
            created_at=self._s.now(),
        )
        self._s.documents[document.id] = document
        self._emit(project_id, "document_added", "Documento adicionado.", document_id=document.id)
        return document, True

    async def get_document(self, document_id: int) -> Document:
        document = self._s.documents.get(_id(document_id, "document_id"))
        if document is None:
            raise errors.DocumentNotFoundError("Documento não encontrado.")
        return document

    async def list_documents(self, project_id: int) -> list[DocumentStatus]:
        project_id = _id(project_id, "project_id")
        documents = sorted((d for d in self._s.documents.values() if d.project_id == project_id), key=lambda d: d.id)
        if not documents:
            self._project(project_id)
            return []
        statuses = []
        for document in documents:
            jobs = sorted(
                (j for j in self._s.jobs.values() if j.document_id == document.id),
                key=lambda j: (j.requested_at, j.id), reverse=True,
            )
            latest = jobs[0] if jobs else None
            summary = None
            for job in jobs:
                if job.status == "succeeded" and job.id in self._s.results:
                    result = self._s.results[job.id]
                    summary = ResultSummary(
                        job_id=job.id, markdown_path=result.markdown_path, engine=result.engine,
                        score=result.quality.score, accepted=result.quality.accepted, created_at=result.created_at,
                    )
                    break
            statuses.append(
                DocumentStatus(
                    document_id=document.id, project_id=document.project_id, source_id=document.source_id,
                    path=document.path, status=latest.status if latest else NOT_PROCESSED,
                    latest_job_id=latest.id if latest else None,
                    latest_job_requested_at=latest.requested_at if latest else None, last_result=summary,
                )
            )
        return statuses

    # -- Jobs --

    async def create_job(self, document_id: int, requested_engine: str) -> Job:
        document_id = _id(document_id, "document_id")
        _choice(requested_engine, REQUESTED_ENGINES, "requested_engine")
        document = self._s.documents.get(document_id)
        if document is None:
            raise errors.DocumentNotFoundError("Documento não encontrado.")
        if any(
            j.document_id == document_id and j.status in ("pending", "running") for j in self._s.jobs.values()
        ):
            self._s.next_id("job")  # ON CONFLICT DO NOTHING consome o valor da sequência
            raise errors.ActiveJobExistsError("O documento já tem um job pendente ou em andamento.")
        job = Job(
            id=self._s.next_id("job"), project_id=document.project_id, document_id=document_id,
            requested_engine=requested_engine, status="pending", requested_at=self._s.now(), started_at=None,
            finished_at=None, error_summary=None, published_at=None,
        )
        self._s.jobs[job.id] = job
        self._emit(job.project_id, "job_created", "Job criado.", document_id=job.document_id, job_id=job.id)
        return job

    def _transition(self, job_id: int, new_status: str, error_summary: str | None = None) -> Job:
        job_id = _id(job_id, "job_id")
        allowed, _assignments = _TRANSITIONS[new_status]
        job = self._s.jobs.get(job_id)
        if job is None:
            raise errors.JobNotFoundError("Job não encontrado.")
        if job.status not in allowed:
            raise errors.InvalidJobTransitionError(
                f"Transição de estado recusada: o job está '{job.status}' e não pode passar para '{new_status}'."
            )
        now = self._s.now()
        fields: dict[str, Any] = {"status": new_status}
        if new_status == "running":
            fields["started_at"] = now
        else:
            fields["finished_at"] = now
        if new_status == "failed":
            fields["error_summary"] = error_summary
        job = replace(job, **fields)
        self._s.jobs[job_id] = job
        return job

    async def get_job(self, job_id: int) -> Job:
        job = self._s.jobs.get(_id(job_id, "job_id"))
        if job is None:
            raise errors.JobNotFoundError("Job não encontrado.")
        return job

    async def list_project_job_ids(self, project_id: int) -> list[int]:
        project_id = _id(project_id, "project_id")
        ids = sorted(j.id for j in self._s.jobs.values() if j.project_id == project_id)
        if not ids:
            self._project(project_id)
        return ids

    async def list_open_jobs(self, project_id: int) -> list[Job]:
        project_id = _id(project_id, "project_id")
        self._project(project_id)
        return sorted(
            (j for j in self._s.jobs.values() if j.project_id == project_id and j.status in ("pending", "running")),
            key=lambda j: j.id,
        )

    async def mark_job_published(self, job_id: int) -> Job:
        job_id = _id(job_id, "job_id")
        job = self._s.jobs.get(job_id)
        if job is None:
            raise errors.JobNotFoundError("Job não encontrado.")
        if job.published_at is None and job.status in ("pending", "running"):
            job = replace(job, published_at=self._s.now())
            self._s.jobs[job_id] = job
        return job

    async def list_unpublished_jobs(self, limit: int = 1000) -> list[Job]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_UNPUBLISHED_PAGE:
            raise errors.StoreValidationError(f"O limite precisa estar entre 1 e {MAX_UNPUBLISHED_PAGE}.")
        jobs = [j for j in self._s.jobs.values() if j.status == "pending" and j.published_at is None]
        return sorted(jobs, key=lambda j: j.id)[:limit]

    async def start_job(self, job_id: int) -> Job:
        job = self._transition(job_id, "running")
        self._emit(job.project_id, "job_started", "Job iniciado.", document_id=job.document_id, job_id=job.id)
        return job

    async def record_attempt(
        self, job_id: int, engine: str, seconds: float, quality: QualityRecord | None = None,
        error: str | None = None,
    ) -> Attempt:
        job_id = _id(job_id, "job_id")
        _text(engine, "engine")
        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not math.isfinite(seconds) or seconds < 0:
            raise errors.StoreValidationError("A duração (seconds) da tentativa não pode ser negativa.")
        if quality is not None and not isinstance(quality, QualityRecord):
            raise errors.StoreValidationError("A qualidade da tentativa precisa ser um QualityRecord.")
        if error is not None:
            _text(error, "error", allow_empty=True)
        if quality is None and (error is None or error.strip() == ""):
            raise errors.StoreValidationError("Uma tentativa precisa ter qualidade ou erro.")
        job = self._s.jobs.get(job_id)
        if job is None:
            raise errors.JobNotFoundError("Job não encontrado.")
        if job.status != "running":
            raise errors.InvalidJobTransitionError("Só um job em andamento aceita tentativas.")
        position = 1 + max((a.position for a in self._s.attempts.values() if a.job_id == job_id), default=0)
        attempt = Attempt(
            id=self._s.next_id("attempt"), job_id=job_id, position=position, engine=engine,
            seconds=float(seconds), quality=quality, error=error, created_at=self._s.now(),
        )
        self._s.attempts[attempt.id] = attempt
        self._emit(
            job.project_id, "attempt_recorded", f"Tentativa {position} registrada.",
            document_id=job.document_id, job_id=job_id,
        )
        return attempt

    async def complete_job(
        self, job_id: int, markdown_path: str, engine: str, quality: QualityRecord, message: str
    ) -> tuple[Job, Result, HistoryEvent]:
        _text(markdown_path, "markdown_path")
        _text(engine, "engine")
        _text(message, "message")
        if not isinstance(quality, QualityRecord):
            raise errors.StoreValidationError("A qualidade do resultado precisa ser um QualityRecord.")
        job = self._transition(job_id, "succeeded")
        result = Result(
            job_id=job.id, markdown_path=markdown_path, engine=engine, quality=quality, created_at=self._s.now()
        )
        self._s.results[job.id] = result
        event = self._emit(job.project_id, "job_succeeded", message, document_id=job.document_id, job_id=job.id)
        return job, result, event

    async def fail_job(self, job_id: int, error_summary: str, message: str) -> tuple[Job, HistoryEvent]:
        _text(error_summary, "error_summary")
        _text(message, "message")
        job = self._transition(job_id, "failed", error_summary)
        event = self._emit(job.project_id, "job_failed", message, document_id=job.document_id, job_id=job.id)
        return job, event

    async def cancel_job(self, job_id: int, message: str) -> tuple[Job, HistoryEvent]:
        _text(message, "message")
        job = self._transition(job_id, "cancelled")
        event = self._emit(job.project_id, "job_cancelled", message, document_id=job.document_id, job_id=job.id)
        return job, event

    async def get_result(self, job_id: int) -> Result | None:
        job_id = _id(job_id, "job_id")
        if job_id not in self._s.jobs:
            raise errors.JobNotFoundError("Job não encontrado.")
        return self._s.results.get(job_id)

    async def list_attempts(self, job_id: int) -> list[Attempt]:
        job_id = _id(job_id, "job_id")
        if job_id not in self._s.jobs:
            raise errors.JobNotFoundError("Job não encontrado.")
        return sorted((a for a in self._s.attempts.values() if a.job_id == job_id), key=lambda a: a.position)

    # -- Histórico --

    async def add_event(
        self, project_id: int, event_type: str, message: str, document_id: int | None = None,
        job_id: int | None = None,
    ) -> HistoryEvent:
        project_id = _id(project_id, "project_id")
        _choice(event_type, EVENT_TYPES, "event_type")
        _text(message, "message")
        if document_id is not None:
            document_id = _id(document_id, "document_id")
        if job_id is not None:
            job_id = _id(job_id, "job_id")
        self._project(project_id)
        if document_id is not None:
            document = self._s.documents.get(document_id)
            if document is None or document.project_id != project_id:
                raise errors.StoreValidationError("O documento informado não existe ou não pertence a este projeto.")
        if job_id is not None:
            job = self._s.jobs.get(job_id)
            if job is None or job.project_id != project_id:
                raise errors.StoreValidationError("O job informado não existe ou não pertence a este projeto.")
            if document_id is not None and job.document_id != document_id:
                raise errors.StoreValidationError("O job informado não pertence ao documento informado.")
        return self._emit(project_id, event_type, message, document_id=document_id, job_id=job_id)

    async def list_events(
        self, project_id: int | None = None, document_id: int | None = None, limit: int = 100, offset: int = 0
    ) -> list[HistoryEvent]:
        if (project_id is None) == (document_id is None):
            raise errors.StoreValidationError("Informe exatamente um filtro: project_id ou document_id.")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_EVENTS_PAGE:
            raise errors.StoreValidationError(f"O limite precisa estar entre 1 e {MAX_EVENTS_PAGE}.")
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise errors.StoreValidationError("O deslocamento (offset) não pode ser negativo.")
        if project_id is not None:
            wanted = _id(project_id, "project_id")
            events = [e for e in self._s.events if e.project_id == wanted]
        else:
            wanted = _id(document_id, "document_id")
            events = [e for e in self._s.events if e.document_id == wanted]
        events.sort(key=lambda e: (e.occurred_at, e.id))
        return events[offset : offset + limit]


class FakeStore:
    """Interface pública de `Store` sobre memória: operações avulsas, `transaction()`, `close()`."""

    def __init__(self) -> None:
        self._state = _State()
        self._closed = False
        self._failures: dict[str, list[BaseException]] = {}

    # -- ciclo de vida --

    async def close(self) -> None:
        self._closed = True

    async def __aenter__(self) -> "FakeStore":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    def inject_failure(self, operation: str, error: BaseException, times: int = 1) -> None:
        """Faz as próximas `times` chamadas de `operation` lançarem `error` (ex.: `DatabaseUnavailableError`)."""
        if operation not in _OPERATIONS:
            raise ValueError(f"operação desconhecida: {operation}")
        self._failures.setdefault(operation, []).extend([error] * times)

    @contextlib.asynccontextmanager
    async def transaction(self) -> AsyncIterator[FakeStoreSession]:
        if self._closed:
            raise errors.DatabaseUnavailableError("O Store está fechado; abra-o de novo com Store.open.")
        snapshot = self._state.snapshot()
        try:
            yield _FailingSession(FakeStoreSession(self._state), self._failures)
        except BaseException:
            # A transação inteira é desfeita, mas as sequências do Postgres não voltam atrás (há "buracos" nos ids).
            snapshot.ids = dict(self._state.ids)
            self._state = snapshot
            raise


class _FailingSession:
    """Envolve a sessão para aplicar as falhas injetadas antes de cada operação."""

    def __init__(self, session: FakeStoreSession, failures: dict[str, list[BaseException]]) -> None:
        self._session = session
        self._failures = failures

    def __getattr__(self, name: str) -> Any:
        target = getattr(self._session, name)
        if name not in _OPERATIONS:
            return target

        async def call(*args: Any, **kwargs: Any) -> Any:
            pending = self._failures.get(name)
            if pending:
                raise pending.pop(0)
            return await target(*args, **kwargs)

        return call


def _delegate(name: str) -> Any:
    async def method(self: FakeStore, *args: Any, **kwargs: Any) -> Any:
        async with self.transaction() as session:
            return await getattr(session, name)(*args, **kwargs)

    method.__name__ = name
    return method


for _name in _OPERATIONS:
    setattr(FakeStore, _name, _delegate(_name))
del _name
