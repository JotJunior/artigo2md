"""Integração das extensões do store para a project-api e da migração 0002 (tarefa 4.2.5).

Só roda com `ARTIGO2MD_DB_TESTS=1` e a stack local no ar; usa bancos temporários (tests/conftest.py). Cobre cada
operação nova, cada recusa do contrato (docs/specs/project-api/contracts/store-extensions.md), a transição
`pending -> failed`, o gatilho do banco (defesa em profundidade) e a premissa dos jobs `pending` preexistentes.
"""
from __future__ import annotations

import asyncio
import uuid

import asyncpg
import pytest
from store_db import requires_db

from artigo2md.store import errors, migrator
from artigo2md.store.models import QualityRecord
from artigo2md.store.repository import Store

pytestmark = requires_db

QUALITY = QualityRecord(90.0, True, 1, 1, 1200.0, ())


def uniq(prefix: str = "Projeto") -> str:
    return f"{prefix} {uuid.uuid4().hex[:10]}"


def run_store(settings, scenario):
    async def main():
        async with await Store.open(settings) as store:
            return await scenario(store)

    return asyncio.run(main())


async def make_document(store, path="/pdfs/a.pdf"):
    project = await store.create_project(uniq("ext"))
    source = await store.add_source(project.id, "folder", "in_place", "/pdfs")
    document, _ = await store.add_document(project.id, source.id, path)
    return project, source, document


async def raw(settings):
    return await asyncpg.connect(
        host=settings.resolve_connect_host(), port=settings.port, user=settings.user,
        password=settings.password, database=settings.database, timeout=10,
    )


# --- rename_project (FR-002) -----------------------------------------------------------------------------------


def test_rename_project_trims_updates_timestamp_and_emits_event(migrated_database):
    async def scenario(store):
        project = await store.create_project(uniq("antes"))
        new_name = uniq("Depois")
        renamed = await store.rename_project(project.id, f"   {new_name}  ")
        events = [e.event_type for e in await store.list_events(project_id=project.id)]
        again = await store.create_project(project.name)  # o nome antigo ficou livre
        return project, renamed, events, again, new_name

    project, renamed, events, again, new_name = run_store(migrated_database, scenario)
    assert renamed.name == new_name and renamed.id == project.id and renamed.status == "active"
    assert renamed.created_at == project.created_at and renamed.updated_at > project.updated_at
    assert events == ["project_created", "project_renamed"]
    assert again.name == project.name


def test_rename_project_refusals_and_same_key_spelling_change(migrated_database):
    async def scenario(store):
        first = await store.create_project(uniq("Primeiro"))
        second = await store.create_project(uniq("Segundo"))
        out = {}
        for label, name in (("vazio", "  "), ("tab", "\t")):
            with pytest.raises(errors.StoreValidationError):
                await store.rename_project(second.id, name)
            out[label] = True
        with pytest.raises(errors.ProjectNameTakenError):
            await store.rename_project(second.id, first.name.upper())
        with pytest.raises(errors.ProjectNotFoundError):
            await store.rename_project(2**62, "qualquer")
        respelled = await store.rename_project(first.id, first.name.upper())  # mesma name_key: só a grafia
        out["respelled"] = respelled.name == first.name.upper()
        out["unchanged_second"] = (await store.get_project(second.id)).name == second.name
        out["events"] = [e.event_type for e in await store.list_events(project_id=second.id)]
        return out

    out = run_store(migrated_database, scenario)
    assert out["respelled"] and out["unchanged_second"]
    assert out["events"] == ["project_created"]  # recusas não emitem evento


def test_rename_to_a_name_used_by_an_active_project_loses_the_race_cleanly(migrated_database):
    async def scenario(store):
        a, b = await store.create_project(uniq("A")), await store.create_project(uniq("B"))
        target = uniq("Alvo")
        results = await asyncio.gather(
            store.rename_project(a.id, target), store.rename_project(b.id, target.upper()), return_exceptions=True
        )
        return results

    results = run_store(migrated_database, scenario)
    assert sum(isinstance(r, errors.ProjectNameTakenError) for r in results) == 1
    assert sum(not isinstance(r, BaseException) for r in results) == 1


# --- leituras novas -----------------------------------------------------------------------------------------


def test_get_and_list_operations_and_their_not_found_errors(migrated_database):
    async def scenario(store):
        project, source, document = await make_document(store)
        second = await store.add_source(project.id, "file", "copied", "/data/x.pdf")
        job = await store.create_job(document.id, "docling")
        empty = await store.create_project(uniq("vazio"))
        out = {
            "source": await store.get_source(source.id) == source,
            "sources": [s.id for s in await store.list_sources(project.id)] == [source.id, second.id],
            "no_sources": await store.list_sources(empty.id) == [],
            "document": await store.get_document(document.id) == document,
            "job": (await store.get_job(job.id)).id == job.id,
            "job_ids": await store.list_project_job_ids(project.id) == [job.id],
            "no_job_ids": await store.list_project_job_ids(empty.id) == [],
        }
        with pytest.raises(errors.StoreNotFoundError) as source_missing:
            await store.get_source(2**62)
        assert type(source_missing.value) is errors.StoreNotFoundError
        assert str(source_missing.value) == "Fonte não encontrada."
        with pytest.raises(errors.ProjectNotFoundError):
            await store.list_sources(2**62)
        with pytest.raises(errors.DocumentNotFoundError):
            await store.get_document(2**62)
        with pytest.raises(errors.JobNotFoundError):
            await store.get_job(2**62)
        with pytest.raises(errors.ProjectNotFoundError):
            await store.list_project_job_ids(2**62)
        for bad in (0, -1, True, "1"):
            with pytest.raises(errors.StoreValidationError):
                await store.get_job(bad)
        return out

    assert all(run_store(migrated_database, scenario).values())


def test_list_project_job_ids_returns_jobs_of_every_state(migrated_database):
    async def scenario(store):
        project = await store.create_project(uniq("estados"))
        source = await store.add_source(project.id, "folder", "in_place", "/pdfs")
        ids = []
        for n in range(4):
            document, _ = await store.add_document(project.id, source.id, f"/pdfs/{n}.pdf")
            ids.append((await store.create_job(document.id, "auto")).id)
        await store.start_job(ids[0])
        await store.complete_job(ids[0], "/o.md", "docling", QUALITY, "ok")
        await store.cancel_job(ids[1], "cancelado")
        await store.fail_job(ids[2], "não publicado", "falhou")
        return ids, await store.list_project_job_ids(project.id)

    ids, listed = run_store(migrated_database, scenario)
    assert listed == sorted(ids) and len(ids) == 4


def test_list_open_jobs_returns_only_pending_and_running_in_id_order(migrated_database):
    async def scenario(store):
        project = await store.create_project(uniq("abertos"))
        empty = await store.create_project(uniq("vazio"))
        source = await store.add_source(project.id, "folder", "in_place", "/pdfs")
        ids = []
        for n in range(4):
            document, _ = await store.add_document(project.id, source.id, f"/pdfs/{n}.pdf")
            ids.append((await store.create_job(document.id, "auto")).id)
        await store.start_job(ids[0])  # running
        await store.cancel_job(ids[1], "cancelado")
        await store.fail_job(ids[2], "não publicado", "falhou")  # ids[3] segue pending
        listed = await store.list_open_jobs(project.id)
        with pytest.raises(errors.ProjectNotFoundError):
            await store.list_open_jobs(2**62)
        return ids, listed, await store.list_open_jobs(empty.id)

    ids, listed, none = run_store(migrated_database, scenario)
    assert [(j.id, j.status) for j in listed] == [(ids[0], "running"), (ids[3], "pending")] and none == []


# --- publicação (FR-011) ------------------------------------------------------------------------------------


def test_mark_job_published_is_idempotent_and_list_unpublished_follows_the_contract(migrated_database):
    async def scenario(store):
        project, source, first_doc = await make_document(store, "/pdfs/0.pdf")
        docs = [first_doc] + [
            (await store.add_document(project.id, source.id, f"/pdfs/{n}.pdf"))[0] for n in range(1, 4)
        ]
        jobs = [await store.create_job(d.id, "auto") for d in docs]
        mine = {j.id for j in jobs}
        assert all(j.published_at is None for j in jobs)
        unpublished = [j.id for j in await store.list_unpublished_jobs() if j.id in mine]
        first = await store.mark_job_published(jobs[0].id)
        second = await store.mark_job_published(jobs[0].id)
        await store.start_job(jobs[1].id)
        await store.cancel_job(jobs[2].id, "cancelado")
        after_cancel = await store.mark_job_published(jobs[2].id)  # final: devolvido sem alteração
        await store.fail_job(jobs[3].id, "publicação não confirmada", "falhou")
        remaining = [j.id for j in await store.list_unpublished_jobs() if j.id in mine]
        limited = await store.list_unpublished_jobs(limit=1)
        for bad in (0, -5, True, 10**9, "2"):
            with pytest.raises(errors.StoreValidationError):
                await store.list_unpublished_jobs(limit=bad)
        with pytest.raises(errors.JobNotFoundError):
            await store.mark_job_published(2**62)
        return jobs, unpublished, first, second, after_cancel, remaining, limited

    jobs, unpublished, first, second, after_cancel, remaining, limited = run_store(migrated_database, scenario)
    assert unpublished == [j.id for j in jobs]  # por id, todos pendentes
    assert first.published_at is not None and second.published_at == first.published_at  # idempotente
    assert after_cancel.status == "cancelled" and after_cancel.published_at is None
    assert remaining == []  # publicado, em andamento, cancelado e falho saem da lista
    assert len(limited) <= 1


# --- fail_job pending -> failed ------------------------------------------------------------------------------


def test_fail_job_accepts_pending_and_running_and_keeps_the_other_refusals(migrated_database):
    async def scenario(store):
        project, source, doc_a = await make_document(store, "/pdfs/a.pdf")
        doc_b, _ = await store.add_document(project.id, source.id, "/pdfs/b.pdf")
        pending = await store.create_job(doc_a.id, "auto")
        failed_pending, event = await store.fail_job(pending.id, "publicação não confirmada", "Falhou.")
        running = await store.start_job((await store.create_job(doc_b.id, "auto")).id)
        failed_running, _ = await store.fail_job(running.id, "motor caiu", "Falhou.")
        out = {
            "pending": failed_pending,
            "running": failed_running,
            "event": event.event_type,
            "events_of_pending": [
                e.event_type for e in await store.list_events(document_id=doc_a.id)
            ],
        }
        for job in (failed_pending, failed_running):
            with pytest.raises(errors.InvalidJobTransitionError):
                await store.fail_job(job.id, "de novo", "x")
            with pytest.raises(errors.InvalidJobTransitionError):
                await store.start_job(job.id)
            with pytest.raises(errors.InvalidJobTransitionError):
                await store.cancel_job(job.id, "x")
        with pytest.raises(errors.StoreValidationError):
            await store.fail_job((await store.create_job(doc_a.id, "auto")).id, "   ", "x")
        with pytest.raises(errors.JobNotFoundError):
            await store.fail_job(2**62, "x", "x")
        return out

    out = run_store(migrated_database, scenario)
    assert out["pending"].status == "failed" and out["pending"].started_at is None
    assert out["pending"].finished_at is not None and out["pending"].error_summary == "publicação não confirmada"
    assert out["running"].status == "failed" and out["running"].started_at is not None
    assert out["event"] == "job_failed"
    assert out["events_of_pending"] == ["document_added", "job_created", "job_failed"]


# --- esquema e gatilho da migração 0002 -------------------------------------------------------------------------


def test_trigger_and_check_after_0002_accept_the_new_paths_and_refuse_the_rest(migrated_database):
    async def scenario():
        connection = await raw(migrated_database)
        try:
            project = await connection.fetchval(
                "INSERT INTO projects (name, name_key) VALUES ($1, $1) RETURNING id", uniq("raw")
            )
            source = await connection.fetchval(
                "INSERT INTO sources (project_id, kind, mode, path) VALUES ($1, 'folder', 'in_place', '/p') "
                "RETURNING id", project,
            )
            doc = await connection.fetchval(
                "INSERT INTO documents (project_id, source_id, path) VALUES ($1, $2, '/p/a.pdf') RETURNING id",
                project, source,
            )
            job = await connection.fetchval(
                "INSERT INTO jobs (project_id, document_id, requested_engine) VALUES ($1, $2, 'auto') RETURNING id",
                project, doc,
            )
            out = {}
            out["pending_to_failed"] = await connection.fetchval(
                "UPDATE jobs SET status = 'failed', finished_at = now(), error_summary = 'x' "
                "WHERE id = $1 RETURNING status", job,
            )
            with pytest.raises(asyncpg.CheckViolationError):  # estado final continua imutável
                await connection.execute("UPDATE jobs SET status = 'running', started_at = now() WHERE id = $1", job)
            with pytest.raises(asyncpg.CheckViolationError):
                await connection.execute("UPDATE jobs SET published_at = now() WHERE id = $1", job)
            doc2 = await connection.fetchval(
                "INSERT INTO documents (project_id, source_id, path) VALUES ($1, $2, '/p/b.pdf') RETURNING id",
                project, source,
            )
            job2 = await connection.fetchval(
                "INSERT INTO jobs (project_id, document_id, requested_engine) VALUES ($1, $2, 'auto') RETURNING id",
                project, doc2,
            )
            with pytest.raises(asyncpg.CheckViolationError):  # pending -> succeeded segue proibido
                await connection.execute(
                    "UPDATE jobs SET status = 'succeeded', finished_at = now() WHERE id = $1", job2
                )
            with pytest.raises(asyncpg.CheckViolationError):  # dados de origem imutáveis
                await connection.execute("UPDATE jobs SET requested_engine = 'docling' WHERE id = $1", job2)
            out["event_type"] = await connection.fetchval(
                "INSERT INTO history_events (project_id, event_type, message) VALUES ($1, 'project_renamed', 'x') "
                "RETURNING event_type", project,
            )
            with pytest.raises(asyncpg.CheckViolationError):
                await connection.execute(
                    "INSERT INTO history_events (project_id, event_type, message) VALUES ($1, 'inventado', 'x')",
                    project,
                )
            out["index"] = await connection.fetchval(
                "SELECT indexdef FROM pg_indexes WHERE schemaname = 'public' AND indexname = 'jobs_unpublished_idx'"
            )
            out["column"] = await connection.fetchrow(
                "SELECT data_type, is_nullable, column_default FROM information_schema.columns "
                "WHERE table_name = 'jobs' AND column_name = 'published_at'"
            )
            return out
        finally:
            await connection.close()

    out = asyncio.run(scenario())
    assert out["pending_to_failed"] == "failed" and out["event_type"] == "project_renamed"
    assert "WHERE ((status = 'pending'::text) AND (published_at IS NULL))" in out["index"]
    assert out["column"]["data_type"] == "timestamp with time zone"
    assert out["column"]["is_nullable"] == "YES" and out["column"]["column_default"] is None


def test_0002_on_a_populated_0001_database_keeps_data_and_treats_pending_jobs_as_unpublished(empty_database):
    """4.1.4: nenhum backfill; o `pending` preexistente é devolvido por `list_unpublished_jobs` uma vez."""

    async def prepare():
        connection = await raw(empty_database)
        try:
            only_first = migrator.known_migrations()[:1]
            first = await migrator.apply_migrations_on(connection, only_first)
            project = await connection.fetchval("INSERT INTO projects (name, name_key) VALUES ('Antigo', 'antigo') RETURNING id")
            source = await connection.fetchval(
                "INSERT INTO sources (project_id, kind, mode, path) VALUES ($1, 'folder', 'in_place', '/p') RETURNING id",
                project,
            )
            doc = await connection.fetchval(
                "INSERT INTO documents (project_id, source_id, path) VALUES ($1, $2, '/p/a.pdf') RETURNING id",
                project, source,
            )
            job = await connection.fetchval(
                "INSERT INTO jobs (project_id, document_id, requested_engine) VALUES ($1, $2, 'auto') RETURNING id",
                project, doc,
            )
            second = await migrator.apply_migrations_on(connection)
            published_at = await connection.fetchval("SELECT published_at FROM jobs WHERE id = $1", job)
            return first, second, job, published_at
        finally:
            await connection.close()

    first, second, job, published_at = asyncio.run(prepare())
    assert first.applied == (1,) and second.applied == tuple(range(2, second.current_version + 1))
    assert published_at is None  # sem backfill

    async def scenario(store):
        listed = [j.id for j in await store.list_unpublished_jobs()]
        await store.mark_job_published(job)
        return listed, [j.id for j in await store.list_unpublished_jobs()]

    listed, after = run_store(empty_database, scenario)
    assert listed == [job] and after == []


def test_second_application_of_0002_changes_nothing(empty_database):
    async def scenario():
        connection = await raw(empty_database)
        try:
            first = await migrator.apply_migrations_on(connection)
            second = await migrator.apply_migrations_on(connection)
            return first, second
        finally:
            await connection.close()

    first, second = asyncio.run(scenario())
    assert 2 in first.applied and second.applied == ()
