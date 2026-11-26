"""Paridade entre `tests/fake_store.FakeStore` e o `Store` real (tarefa 4.3.3, FR-034).

Cada cenário roda contra o fake (sempre) e contra o Postgres temporário (só com `ARTIGO2MD_DB_TESTS=1`) e produz
uma transcrição: o valor devolvido (instantes trocados por um marcador) ou a exceção (classe e mensagem) de cada
passo. As duas transcrições precisam ser idênticas. Os cenários só comparam o que o contrato do repositório
promete; instantes exatos e ids absolutos não entram (cada banco temporário começa com ids a partir de 1).
"""
from __future__ import annotations

import asyncio
import dataclasses
from datetime import datetime

import pytest
import store_db
from fake_store import FakeStore

from artigo2md.store import errors
from artigo2md.store.models import QualityRecord

QUALITY = QualityRecord(82.5, True, 2, 2, 1500.0, ("aviso", "outro"))
MISSING = 2**40


def _norm(value):
    if isinstance(value, datetime):
        return "<instante>"
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: _norm(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, (list, tuple)):
        return [_norm(item) for item in value]
    return value


class Transcript:
    """Registra o resultado normalizado, ou a exceção, de cada chamada."""

    def __init__(self) -> None:
        self.steps: list = []

    async def call(self, label: str, awaitable):
        try:
            value = await awaitable
        except errors.StoreError as exc:
            self.steps.append((label, "raises", type(exc).__name__, str(exc)))
            return None
        except BaseException as exc:  # noqa: BLE001 - erro inesperado entra na transcrição para a comparação falhar
            self.steps.append((label, "unexpected", type(exc).__name__, str(exc)))
            return None
        self.steps.append((label, "ok", _norm(value)))
        return value


# --- Cenários ----------------------------------------------------------------------------------------------


async def scenario_projects(store, t: Transcript) -> None:
    a = await t.call("create a", store.create_project("  Revisão A  "))
    await t.call("create dup case", store.create_project("revisão a"))
    await t.call("create empty", store.create_project("   "))
    await t.call("create nul", store.create_project("a\x00b"))
    b = await t.call("create b", store.create_project("Revisão B"))
    await t.call("get a", store.get_project(a.id))
    await t.call("get missing", store.get_project(MISSING))
    await t.call("get bad id", store.get_project(0))
    await t.call("rename to taken", store.rename_project(b.id, " REVISÃO A "))
    await t.call("rename same key", store.rename_project(a.id, "REVISÃO a"))
    await t.call("rename trimmed", store.rename_project(b.id, "  Revisão C  "))
    await t.call("rename empty", store.rename_project(b.id, "  "))
    await t.call("rename missing", store.rename_project(MISSING, "X"))
    await t.call("archive a", store.archive_project(a.id))
    await t.call("archive a again", store.archive_project(a.id))
    await t.call("rename archived", store.rename_project(a.id, "Revisão C"))  # arquivado não colide
    await t.call("list active", store.list_projects())
    await t.call("list all", store.list_projects(include_archived=True))
    await t.call("unarchive a (name taken by b)", store.unarchive_project(a.id))
    await t.call("rename archived free", store.rename_project(a.id, "Revisão Livre"))
    await t.call("unarchive a", store.unarchive_project(a.id))
    await t.call("unarchive a again", store.unarchive_project(a.id))
    await t.call("events a", store.list_events(project_id=a.id))
    await t.call("delete a", store.delete_project(a.id))
    await t.call("delete a again", store.delete_project(a.id))
    await t.call("events a gone", store.list_events(project_id=a.id))


async def scenario_sources_and_documents(store, t: Transcript) -> None:
    p = await t.call("project", store.create_project("Fontes"))
    other = await t.call("other project", store.create_project("Outro"))
    await t.call("list sources empty", store.list_sources(p.id))
    await t.call("list sources missing project", store.list_sources(MISSING))
    s1 = await t.call("source folder", store.add_source(p.id, "folder", "in_place", "/pdfs"))
    s2 = await t.call("source file", store.add_source(p.id, "file", "copied", "/data/x.pdf"))
    foreign = await t.call("source of other", store.add_source(other.id, "folder", "in_place", "/o"))
    await t.call("source bad kind", store.add_source(p.id, "zip", "in_place", "/x"))
    await t.call("source bad mode", store.add_source(p.id, "folder", "link", "/x"))
    await t.call("source empty path", store.add_source(p.id, "folder", "in_place", ""))
    await t.call("source missing project", store.add_source(MISSING, "folder", "in_place", "/x"))
    await t.call("get source", store.get_source(s1.id))
    await t.call("get source missing", store.get_source(MISSING))
    await t.call("list sources", store.list_sources(p.id))
    d1, _ = await t.call("document", store.add_document(p.id, s1.id, "/pdfs/a.pdf"))
    await t.call("document again", store.add_document(p.id, s2.id, "/pdfs/a.pdf"))
    await t.call("document foreign source", store.add_document(p.id, foreign.id, "/pdfs/b.pdf"))
    await t.call("document missing project", store.add_document(MISSING, s1.id, "/pdfs/c.pdf"))
    await t.call("document missing source", store.add_document(p.id, MISSING, "/pdfs/c.pdf"))
    await t.call("get document", store.get_document(d1.id))
    await t.call("get document missing", store.get_document(MISSING))
    await t.call("list documents", store.list_documents(p.id))
    await t.call("list documents empty", store.list_documents(other.id))
    await t.call("list documents missing project", store.list_documents(MISSING))
    await t.call("events", store.list_events(project_id=p.id))
    await t.call("document events", store.list_events(document_id=d1.id))
    await t.call("delete cascades sources", store.delete_project(p.id))
    await t.call("get source after delete", store.get_source(s1.id))
    await t.call("get document after delete", store.get_document(d1.id))


async def scenario_jobs(store, t: Transcript) -> None:
    p = await t.call("project", store.create_project("Jobs"))
    s = await t.call("source", store.add_source(p.id, "folder", "in_place", "/pdfs"))
    d1, _ = await t.call("doc 1", store.add_document(p.id, s.id, "/pdfs/1.pdf"))
    d2, _ = await t.call("doc 2", store.add_document(p.id, s.id, "/pdfs/2.pdf"))
    d3, _ = await t.call("doc 3", store.add_document(p.id, s.id, "/pdfs/3.pdf"))
    await t.call("job bad engine", store.create_job(d1.id, "gpt"))
    await t.call("job missing document", store.create_job(MISSING, "auto"))
    j1 = await t.call("job 1", store.create_job(d1.id, "auto"))
    await t.call("job 1 dup", store.create_job(d1.id, "docling"))
    await t.call("get job", store.get_job(j1.id))
    await t.call("get job missing", store.get_job(MISSING))
    await t.call("attempt on pending", store.record_attempt(j1.id, "docling", 1.0, error="x"))
    await t.call("complete pending", store.complete_job(j1.id, "/o.md", "docling", QUALITY, "ok"))
    await t.call("start 1", store.start_job(j1.id))
    await t.call("start 1 again", store.start_job(j1.id))
    await t.call("attempt without quality or error", store.record_attempt(j1.id, "docling", 1.0))
    await t.call("attempt negative", store.record_attempt(j1.id, "docling", -1.0, error="x"))
    await t.call("attempt error", store.record_attempt(j1.id, "docling", 1.5, error="falhou"))
    await t.call("attempt quality", store.record_attempt(j1.id, "pymupdf", 0.5, quality=QUALITY))
    await t.call("attempts", store.list_attempts(j1.id))
    await t.call("result before", store.get_result(j1.id))
    await t.call("complete bad quality", store.complete_job(j1.id, "/o.md", "docling", None, "ok"))
    await t.call("complete 1", store.complete_job(j1.id, "/o.md", "pymupdf", QUALITY, "Concluído."))
    await t.call("complete 1 again", store.complete_job(j1.id, "/o.md", "pymupdf", QUALITY, "Concluído."))
    await t.call("cancel final", store.cancel_job(j1.id, "tarde"))
    await t.call("result", store.get_result(j1.id))
    await t.call("result missing job", store.get_result(MISSING))
    await t.call("attempts missing job", store.list_attempts(MISSING))
    # pending -> failed (migração 0002) e running -> failed
    j2 = await t.call("job 2", store.create_job(d2.id, "auto"))
    await t.call("fail pending", store.fail_job(j2.id, "publicação não confirmada", "Falhou."))
    await t.call("fail again", store.fail_job(j2.id, "outra", "Falhou."))
    await t.call("fail empty summary", store.fail_job(j2.id, " ", "Falhou."))
    await t.call("start failed", store.start_job(j2.id))
    j3 = await t.call("job 3", store.create_job(d2.id, "mineru"))  # documento livre de novo
    await t.call("start 3", store.start_job(j3.id))
    await t.call("fail running", store.fail_job(j3.id, "motor caiu", "Falhou."))
    j4 = await t.call("job 4", store.create_job(d3.id, "markitdown"))
    await t.call("cancel pending", store.cancel_job(j4.id, "Cancelado."))
    await t.call("documents", store.list_documents(p.id))
    await t.call("job ids", store.list_project_job_ids(p.id))
    await t.call("job ids missing project", store.list_project_job_ids(MISSING))
    empty = await t.call("empty project", store.create_project("Vazio"))
    await t.call("job ids empty", store.list_project_job_ids(empty.id))
    await t.call("events", store.list_events(project_id=p.id, limit=500))
    await t.call("events page", store.list_events(project_id=p.id, limit=3, offset=2))
    await t.call("events document", store.list_events(document_id=d1.id))
    await t.call("events bad filter", store.list_events())
    await t.call("events bad limit", store.list_events(project_id=p.id, limit=0))
    await t.call("events bad offset", store.list_events(project_id=p.id, offset=-1))


async def scenario_archive_and_delete_cancel_open_jobs(store, t: Transcript) -> None:
    p = await t.call("project", store.create_project("Em andamento"))
    s = await t.call("source", store.add_source(p.id, "folder", "in_place", "/pdfs"))
    docs = []
    for n in range(3):
        d, _ = await t.call(f"doc {n}", store.add_document(p.id, s.id, f"/pdfs/{n}.pdf"))
        docs.append(d)
    jobs = [await t.call(f"job {n}", store.create_job(d.id, "auto")) for n, d in enumerate(docs)]
    await t.call("start 1", store.start_job(jobs[1].id))
    await t.call("cancel 2 pending", store.cancel_job(jobs[2].id, "Cancelado antes."))
    await t.call("open jobs", store.list_open_jobs(p.id))
    await t.call("open jobs missing project", store.list_open_jobs(MISSING))
    await t.call("archive", store.archive_project(p.id))
    await t.call("open jobs after archive", store.list_open_jobs(p.id))
    await t.call("jobs after archive", store.list_project_job_ids(p.id))
    await t.call("job 0", store.get_job(jobs[0].id))
    await t.call("job 1", store.get_job(jobs[1].id))
    await t.call("events", store.list_events(project_id=p.id, limit=500))
    await t.call("delete", store.delete_project(p.id))
    await t.call("job after delete", store.get_job(jobs[0].id))


async def scenario_publication(store, t: Transcript) -> None:
    p = await t.call("project", store.create_project("Publicação"))
    s = await t.call("source", store.add_source(p.id, "folder", "in_place", "/pdfs"))
    docs = [(await t.call(f"doc {n}", store.add_document(p.id, s.id, f"/pdfs/{n}.pdf")))[0] for n in range(4)]
    jobs = [await t.call(f"job {n}", store.create_job(d.id, "auto")) for n, d in enumerate(docs)]
    await t.call("unpublished all", store.list_unpublished_jobs())
    await t.call("unpublished limit 2", store.list_unpublished_jobs(limit=2))
    await t.call("unpublished bad limit", store.list_unpublished_jobs(limit=0))
    await t.call("unpublished bad limit type", store.list_unpublished_jobs(limit=True))
    await t.call("mark 0", store.mark_job_published(jobs[0].id))
    first = await store.get_job(jobs[0].id)
    await t.call("mark 0 again", store.mark_job_published(jobs[0].id))
    again = await store.get_job(jobs[0].id)
    t.steps.append(("idempotent published_at", first.published_at == again.published_at))
    await t.call("mark missing", store.mark_job_published(MISSING))
    await t.call("unpublished after mark", store.list_unpublished_jobs())
    await t.call("start 1 (still unpublished)", store.start_job(jobs[1].id))
    await t.call("unpublished skips running", store.list_unpublished_jobs())
    await t.call("mark running", store.mark_job_published(jobs[1].id))
    await t.call("cancel 2", store.cancel_job(jobs[2].id, "Cancelado."))
    await t.call("mark cancelled is a no-op", store.mark_job_published(jobs[2].id))
    await t.call("unpublished skips cancelled", store.list_unpublished_jobs())
    await t.call("fail 3 pending", store.fail_job(jobs[3].id, "não publicado", "Falhou."))
    await t.call("unpublished skips failed", store.list_unpublished_jobs())


async def scenario_events_and_validation(store, t: Transcript) -> None:
    p = await t.call("project", store.create_project("Eventos"))
    q = await t.call("other", store.create_project("Eventos 2"))
    s = await t.call("source", store.add_source(p.id, "folder", "in_place", "/pdfs"))
    d, _ = await t.call("doc", store.add_document(p.id, s.id, "/pdfs/a.pdf"))
    j = await t.call("job", store.create_job(d.id, "auto"))
    await t.call("event renamed", store.add_event(p.id, "project_renamed", "Renomeado."))
    await t.call("event with doc and job", store.add_event(p.id, "attempt_started", "Início", d.id, j.id))
    await t.call("event bad type", store.add_event(p.id, "inventado", "x"))
    await t.call("event empty message", store.add_event(p.id, "job_started", " "))
    await t.call("event missing project", store.add_event(MISSING, "job_started", "x"))
    await t.call("event foreign document", store.add_event(q.id, "job_started", "x", d.id))
    await t.call("event foreign job", store.add_event(q.id, "job_started", "x", None, j.id))
    await t.call("events", store.list_events(project_id=p.id, limit=500))
    await t.call("bool id", store.get_project(True))
    await t.call("str id", store.get_job("1"))


async def scenario_transaction(store, t: Transcript) -> None:
    keep = await t.call("keep", store.create_project("Mantido"))
    try:
        async with store.transaction() as session:
            await session.create_project("Desfeito")
            await session.rename_project(keep.id, "Renomeado na transação")
            raise RuntimeError("falha no meio da transação")
    except RuntimeError as exc:
        t.steps.append(("tx raised", str(exc)))
    await t.call("list after rollback", store.list_projects())
    await t.call("events after rollback", store.list_events(project_id=keep.id))
    async with store.transaction() as session:
        created = await session.create_project("Confirmado")
        await session.rename_project(created.id, "Confirmado 2")
    await t.call("list after commit", store.list_projects())
    try:
        async with store.transaction() as session:
            await session.create_project("Mantido")  # colide: recusa vira exceção da camada
    except errors.ProjectNameTakenError as exc:
        t.steps.append(("tx name taken", str(exc)))


SCENARIOS = [
    scenario_projects,
    scenario_sources_and_documents,
    scenario_jobs,
    scenario_archive_and_delete_cancel_open_jobs,
    scenario_publication,
    scenario_events_and_validation,
    scenario_transaction,
]


def _ids(scenarios):
    return [s.__name__.removeprefix("scenario_") for s in scenarios]


def _transcript_of_fake(scenario) -> list:
    async def main():
        transcript = Transcript()
        async with FakeStore() as store:
            await scenario(store, transcript)
        return transcript.steps

    return asyncio.run(main())


@pytest.mark.parametrize("scenario", SCENARIOS, ids=_ids(SCENARIOS))
def test_fake_store_scenarios_have_no_unexpected_errors(scenario):
    steps = _transcript_of_fake(scenario)
    assert steps
    assert not [step for step in steps if "unexpected" in step], steps


@pytest.mark.skipif(not store_db.db_tests_enabled(), reason=store_db.SKIP_REASON)
@pytest.mark.parametrize("scenario", SCENARIOS, ids=_ids(SCENARIOS))
def test_fake_store_matches_the_real_store(scenario, empty_database):
    """Mesmos cenários contra o Postgres temporário (ids reiniciam em 1): transcrições idênticas."""
    from artigo2md.store import apply_migrations
    from artigo2md.store.repository import Store

    asyncio.run(apply_migrations(empty_database))

    async def main():
        transcript = Transcript()
        async with await Store.open(empty_database) as store:
            await scenario(store, transcript)
        return transcript.steps

    real = asyncio.run(main())
    fake = _transcript_of_fake(scenario)
    assert len(real) == len(fake)
    for index, (expected, got) in enumerate(zip(real, fake, strict=True)):
        assert got == expected, f"passo {index} ({expected[0]}): fake {got!r} != real {expected!r}"


# --- Comportamento próprio do fake ---------------------------------------------------------------------------


def test_closed_fake_store_refuses_transactions_like_the_real_one():
    async def main():
        store = FakeStore()
        await store.close()
        await store.close()
        with pytest.raises(errors.DatabaseUnavailableError):
            await store.list_projects()

    asyncio.run(main())


def test_fake_store_exposes_every_public_operation_and_injects_failures():
    from artigo2md.store.repository import _OPERATIONS

    async def main():
        store = FakeStore()
        for name in _OPERATIONS:
            assert callable(getattr(store, name))
        store.inject_failure("create_project", errors.DatabaseUnavailableError("banco fora"), times=1)
        with pytest.raises(errors.DatabaseUnavailableError):
            await store.create_project("X")
        assert (await store.create_project("X")).name == "X"  # a falha injetada vale uma vez
        with pytest.raises(ValueError):
            store.inject_failure("nao_existe", RuntimeError())

    asyncio.run(main())
