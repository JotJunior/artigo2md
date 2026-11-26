"""Testes de integração do repositório `artigo2md.store` contra o Postgres da stack local.

Só rodam com `ARTIGO2MD_DB_TESTS=1`; usam o banco temporário `migrated_database` (tests/conftest.py) e cada
cenário cria projetos de nome único, sem depender de ordem. Nenhum teste lê ou altera o banco real: o guarda
`real_database_guard` compara só contagens e o checksum do histórico de migrações antes e depois (SC-006).
"""
from __future__ import annotations

import asyncio
import dataclasses
import platform
import socket
import sys
import time
import uuid

import asyncpg
import pytest
from store_db import requires_db

from artigo2md.store import errors, repository
from artigo2md.store.models import QualityRecord
from artigo2md.store.repository import Store

pytestmark = requires_db

QUALITY = QualityRecord(82.5, True, 2, 2, 1500.0, ("ação", 'tabela "x"'))


def uniq(prefix: str = "Projeto") -> str:
    return f"{prefix} {uuid.uuid4().hex[:10]}"


def run_store(settings, scenario, **open_kwargs):
    """Abre um Store no laço do teste, roda o cenário e fecha o pool."""

    async def main():
        async with await Store.open(settings, **open_kwargs) as store:
            return await scenario(store)

    return asyncio.run(main())


async def make_document(store, path="/pdfs/a.pdf", tag="doc"):
    project = await store.create_project(uniq(tag))
    source = await store.add_source(project.id, "folder", "in_place", "/pdfs")
    document, _ = await store.add_document(project.id, source.id, path)
    return project, source, document


async def run_job(store, document, engine="auto"):
    """Job criado e iniciado (`running`)."""
    job = await store.create_job(document.id, engine)
    return await store.start_job(job.id)


async def events_of(store, project_id):
    return [e.event_type for e in await store.list_events(project_id=project_id, limit=500)]


# --- Guarda: o banco real não muda (SC-006, tarefas 7.2.2 e 8.1.3) -----------------------------------------


async def _snapshot(settings):
    connection = await asyncpg.connect(
        host=settings.resolve_connect_host(), port=settings.port, user=settings.user,
        password=settings.password, database=settings.database, timeout=10,
    )
    try:
        tables = await connection.fetchval(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_schema NOT IN ('pg_catalog', 'information_schema')"
        )
        migrations = None
        if await connection.fetchval("SELECT to_regclass('public.schema_migrations')") is not None:
            migrations = await connection.fetchval(
                "SELECT md5(coalesce(string_agg(version::text || checksum, ',' ORDER BY version), '')) "
                "FROM schema_migrations"
            )
        leftovers = await connection.fetchval(
            "SELECT count(*) FROM pg_database WHERE datname LIKE 'artigo2md\\_test\\_%'"
        )
        return tables, migrations, leftovers
    finally:
        await connection.close()


@pytest.fixture(scope="session", autouse=True)
def real_database_guard(db_base_settings):
    before = asyncio.run(_snapshot(db_base_settings))
    yield before
    after = asyncio.run(_snapshot(db_base_settings))
    assert after == before, "o banco real mudou, ou sobrou banco temporário, depois da suíte de integração"


# --- Ciclo de vida do Store (4.1.5) ------------------------------------------------------------------------


def test_data_is_identical_after_closing_and_reopening(migrated_database):
    async def create(store):
        project, source, document = await make_document(store)
        return project, source, document

    created = run_store(migrated_database, create)

    async def read(store):
        return (
            await store.get_project(created[0].id),
            [d.document_id for d in await store.list_documents(created[0].id)],
        )

    project, documents = run_store(migrated_database, read)
    assert project == created[0] and documents == [created[2].id]


def test_wrong_password_and_closed_port_give_unavailable_without_the_password(migrated_database, capsys):
    wrong = dataclasses.replace(migrated_database, password="senha-errada-de-teste")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        free_port = probe.getsockname()[1]
    closed = dataclasses.replace(migrated_database, port=free_port)
    for settings in (wrong, closed):
        with pytest.raises(errors.DatabaseUnavailableError) as caught:
            run_store(settings, lambda store: store.list_projects())
        for secret in (settings.password, migrated_database.password):
            assert secret not in str(caught.value)
        assert secret not in repr(settings)
        assert caught.value.__cause__ is None
    captured = capsys.readouterr()
    assert migrated_database.password not in captured.out + captured.err
    assert "senha-errada-de-teste" not in captured.out + captured.err
    assert run_store(migrated_database, lambda store: store.list_projects()) is not None  # reconectar funciona


# --- Projetos (US1, cenário 14) ------------------------------------------------------------------------------


def test_project_roundtrip_name_rules_and_archived_name_reuse(migrated_database):
    async def scenario(store):
        base = uniq("Revisão")
        project = await store.create_project(f"  {base}  ")
        assert project.name == base and project.status == "active"
        assert await store.get_project(project.id) == project
        for repeated in (base.lower(), base.upper(), f" {base} "):
            with pytest.raises(errors.ProjectNameTakenError):
                await store.create_project(repeated)
        accent = await store.create_project(base.replace("ã", "a"))  # 'a' ≠ 'ã': nome diferente
        assert accent.id != project.id
        for empty in ("", "   ", "\t\n"):
            with pytest.raises(errors.StoreValidationError):
                await store.create_project(empty)
        strasse = uniq("Straße")
        await store.create_project(strasse)
        with pytest.raises(errors.ProjectNameTakenError):
            await store.create_project(strasse.replace("ß", "SS").upper())
        # Arquivar libera o nome; desarquivar esbarra nele.
        outcome = await store.archive_project(project.id)
        assert outcome.project.status == "archived" and outcome.cancelled_job_ids == ()
        assert project.id not in [p.id for p in await store.list_projects()]
        assert project.id in [p.id for p in await store.list_projects(include_archived=True)]
        reused = await store.create_project(base)
        with pytest.raises(errors.ProjectNameTakenError):
            await store.unarchive_project(project.id)
        assert (await store.get_project(project.id)).status == "archived" and reused.name == base
        with pytest.raises(errors.StoreValidationError):
            await store.archive_project(project.id)  # já arquivado: recusa simétrica
        with pytest.raises(errors.StoreValidationError):
            await store.unarchive_project(reused.id)  # já ativo
        with pytest.raises(errors.ProjectNotFoundError):
            await store.get_project(2**62)

    run_store(migrated_database, scenario)


def test_duplicate_name_error_does_not_echo_server_text(migrated_database):
    async def scenario(store):
        name = uniq("Segredo")
        await store.create_project(name)
        with pytest.raises(errors.ProjectNameTakenError) as caught:
            await store.create_project(name)
        assert name not in str(caught.value) and "DETAIL" not in str(caught.value)
        assert caught.value.__cause__ is None

    run_store(migrated_database, scenario)


# --- Fontes e documentos (US1-2..4, US1-7, US5-6) -------------------------------------------------------------


def test_sources_documents_and_same_path_rules(migrated_database):
    async def scenario(store):
        project = await store.create_project(uniq())
        folder = await store.add_source(project.id, "folder", "in_place", "/Users/x/Artigos 2024")
        copied = await store.add_source(project.id, "file", "copied", "/inexistente/no/disco.pdf")
        twin = await store.add_source(project.id, "folder", "in_place", "/Users/x/Artigos 2024")
        assert (folder.kind, folder.mode) == ("folder", "in_place") and (copied.kind, copied.mode) == ("file", "copied")
        assert twin.id != folder.id  # mesmo caminho em duas fontes do mesmo projeto é permitido
        first, created = await store.add_document(project.id, folder.id, "/Users/x/Artigos 2024/a.pdf")
        again, created_again = await store.add_document(project.id, copied.id, "/Users/x/Artigos 2024/a.pdf")
        assert (created, created_again) == (True, False)
        assert again.id == first.id and again.source_id == folder.id  # mantém a fonte original
        statuses = await store.list_documents(project.id)
        assert [(s.document_id, s.status) for s in statuses] == [(first.id, "not_processed")]
        other = await store.create_project(uniq())
        other_source = await store.add_source(other.id, "folder", "in_place", "/Users/x/Artigos 2024")
        second, created_other = await store.add_document(other.id, other_source.id, first.path)
        assert created_other and second.id != first.id  # outro projeto pode repetir o caminho
        with pytest.raises(errors.StoreValidationError):
            await store.add_document(project.id, other_source.id, "/z.pdf")  # fonte de outro projeto
        for kind, mode in (("pasta", "in_place"), ("folder", "linked")):
            with pytest.raises(errors.StoreValidationError):
                await store.add_source(project.id, kind, mode, "/x")
        with pytest.raises(errors.ProjectNotFoundError):
            await store.add_source(2**62, "folder", "in_place", "/x")

    run_store(migrated_database, scenario)


def test_special_characters_are_stored_and_read_back_identically(migrated_database):
    nasty = [
        "Revisão \"sistemática\" 'v2'",
        "'; DROP TABLE projects; --",
        "C:\\Users\\x\\artigos\\ação.pdf",
        "caminho com espaços/e/barras//duplas/ü.pdf",
        "emoji 📄 e tab\t",
    ]

    async def scenario(store):
        for text in nasty:
            project = await store.create_project(f"{uniq()} {text}".strip())
            source = await store.add_source(project.id, "folder", "in_place", text)
            document, _ = await store.add_document(project.id, source.id, f"{text}/a.pdf")
            assert (await store.get_project(project.id)).name == f"{project.name}"
            assert source.path == text and document.path == f"{text}/a.pdf"
            assert (await store.list_documents(project.id))[0].path == f"{text}/a.pdf"
        assert await store.list_projects() is not None  # a tabela `projects` continua lá

    run_store(migrated_database, scenario)


# --- Jobs, tentativas, resultados (US2, cenários 3, 4, 12, 13) ---------------------------------------------------


def test_job_with_two_attempts_and_result_roundtrips_quality(migrated_database):
    async def scenario(store):
        project, _, document = await make_document(store)
        job = await store.create_job(document.id, "auto")
        assert job.status == "pending" and job.requested_engine == "auto" and job.requested_at.tzinfo is not None
        job = await store.start_job(job.id)
        assert job.status == "running" and job.started_at is not None
        rejected = QualityRecord(40.0, False, 0, 2, 300.0, ("tabelas ausentes",))
        await store.record_attempt(job.id, "markitdown", 1.5, quality=rejected)
        await store.record_attempt(job.id, "docling", 4.25, quality=QUALITY)
        attempts = await store.list_attempts(job.id)
        assert [(a.position, a.engine) for a in attempts] == [(1, "markitdown"), (2, "docling")]
        assert attempts[0].quality == rejected and attempts[1].quality == QUALITY
        done, result, event = await store.complete_job(job.id, "/md/a.md", "docling", QUALITY, "Concluído")
        assert done.status == "succeeded" and done.finished_at is not None and event.event_type == "job_succeeded"
        stored = await store.get_result(job.id)
        assert stored == result and stored.quality == QUALITY  # seis campos idênticos, issues na mesma ordem
        assert stored.warnings == ("ação", 'tabela "x"')
        status = (await store.list_documents(project.id))[0]
        assert status.status == "succeeded" and status.last_result.job_id == job.id

    run_store(migrated_database, scenario)


def test_not_accepted_result_still_finishes_as_succeeded_and_empty_issues_roundtrip(migrated_database):
    async def scenario(store):
        _, _, document = await make_document(store)
        job = await run_job(store, document)
        quality = QualityRecord(0, False, 0, 0, 0.0, ())
        done, result, _ = await store.complete_job(job.id, "/md/b.md", "pymupdf", quality, "Sem aceite")
        assert done.status == "succeeded" and result.quality.accepted is False
        assert (await store.get_result(job.id)).quality.issues == ()
        edge = await run_job(store, (await make_document(store))[2])
        top = QualityRecord(100, True, 5, 5, 99999.5, ())
        await store.complete_job(edge.id, "/md/c.md", "docling", top, "Topo")
        assert (await store.get_result(edge.id)).quality.score == 100

    run_store(migrated_database, scenario)


def test_failed_job_keeps_attempts_without_result_and_unknown_jobs_are_distinguished(migrated_database):
    async def scenario(store):
        project, _, document = await make_document(store)
        job = await run_job(store, document)
        await store.record_attempt(job.id, "docling", 2.0, error="memória insuficiente")
        failed, event = await store.fail_job(job.id, "Todos os motores falharam", "Falha total")
        assert failed.status == "failed" and failed.error_summary == "Todos os motores falharam"
        assert event.event_type == "job_failed"
        assert await store.get_result(job.id) is None  # existe, sem resultado
        attempts = await store.list_attempts(job.id)
        assert len(attempts) == 1 and attempts[0].quality is None and attempts[0].error == "memória insuficiente"
        empty = await store.create_job(document.id, "docling")  # novo job depois de estado final
        assert await store.list_attempts(empty.id) == []  # existe, sem tentativas
        for call in (store.get_result, store.list_attempts):
            with pytest.raises(errors.JobNotFoundError):
                await call(2**62)

    run_store(migrated_database, scenario)


def test_rules_refused_without_changing_the_job(migrated_database):
    async def scenario(store):
        _, _, document = await make_document(store)
        with pytest.raises(errors.StoreValidationError):
            await store.create_job(document.id, "pdfbox")
        with pytest.raises(errors.DocumentNotFoundError):
            await store.create_job(2**62, "auto")
        job = await store.create_job(document.id, "auto")
        with pytest.raises(errors.ActiveJobExistsError):
            await store.create_job(document.id, "docling")
        with pytest.raises(errors.InvalidJobTransitionError):
            await store.record_attempt(job.id, "docling", 1.0, error="x")  # ainda pending
        with pytest.raises(errors.InvalidJobTransitionError):
            await store.complete_job(job.id, "/m.md", "docling", QUALITY, "x")  # pending não conclui
        await store.start_job(job.id)
        with pytest.raises(errors.InvalidJobTransitionError):
            await store.start_job(job.id)
        with pytest.raises(errors.StoreValidationError):
            await store.record_attempt(job.id, "docling", 1.0)  # sem qualidade e sem erro
        with pytest.raises(errors.StoreValidationError):
            QualityRecord(101, True, 1, 1, 1.0)
        await store.complete_job(job.id, "/m.md", "docling", QUALITY, "ok")
        before = (await store.list_attempts(job.id), await store.get_result(job.id), await events_of(store, job.project_id))
        for call in (
            lambda: store.complete_job(job.id, "/m2.md", "docling", QUALITY, "de novo"),
            lambda: store.fail_job(job.id, "x", "x"),
            lambda: store.cancel_job(job.id, "x"),
            lambda: store.start_job(job.id),
            lambda: store.record_attempt(job.id, "docling", 1.0, error="tardia"),
        ):
            with pytest.raises(errors.InvalidJobTransitionError):
                await call()
        after = (await store.list_attempts(job.id), await store.get_result(job.id), await events_of(store, job.project_id))
        assert after == before  # sem segundo resultado, sem tentativa nem evento novo
        assert (await store.create_job(document.id, "auto")).status == "pending"  # novo job; o anterior intacto

    run_store(migrated_database, scenario)


def test_concurrency_one_active_job_and_unique_attempt_positions(migrated_database):
    async def scenario(store):
        _, _, document = await make_document(store)
        outcomes = await asyncio.gather(
            store.create_job(document.id, "auto"), store.create_job(document.id, "auto"), return_exceptions=True
        )
        assert sum(isinstance(o, errors.ActiveJobExistsError) for o in outcomes) == 1
        job = next(o for o in outcomes if not isinstance(o, Exception))
        await store.start_job(job.id)
        await asyncio.gather(*(store.record_attempt(job.id, f"motor{i}", 0.1, error="x") for i in range(4)))
        assert sorted(a.position for a in await store.list_attempts(job.id)) == [1, 2, 3, 4]

    run_store(migrated_database, scenario)


def test_atomicity_state_result_and_event_all_or_nothing(migrated_database, monkeypatch):
    async def scenario(store):
        project, _, document = await make_document(store)
        job = await run_job(store, document)
        events_before = await events_of(store, project.id)
        with pytest.raises(RuntimeError):  # falha depois do resultado, antes do commit
            async with store.transaction() as session:
                await session.complete_job(job.id, "/m.md", "docling", QUALITY, "ok")
                raise RuntimeError("falha simulada")
        assert await store.get_result(job.id) is None
        assert (await events_of(store, project.id)) == events_before
        assert (await store.list_documents(project.id))[0].status == "running"

        def _boom(row):
            raise RuntimeError("falha simulada ao mapear o resultado")

        monkeypatch.setattr(repository, "_row_to_result", _boom)
        with pytest.raises(RuntimeError):
            await store.complete_job(job.id, "/m.md", "docling", QUALITY, "ok")
        monkeypatch.undo()
        assert (await store.list_documents(project.id))[0].status == "running"
        assert await store.get_result(job.id) is None
        assert (await events_of(store, project.id)) == events_before

    run_store(migrated_database, scenario)


# --- Arquivar, desarquivar, excluir (US1-5..9, cenário 5) ---------------------------------------------------


def test_archive_cancels_jobs_and_late_result_is_refused_and_unarchive_restores(migrated_database):
    async def scenario(store):
        project, source, document = await make_document(store)
        second, _ = await store.add_document(project.id, source.id, "/pdfs/b.pdf")
        running = await run_job(store, document)
        pending = await store.create_job(second.id, "auto")
        outcome = await store.archive_project(project.id)
        assert set(outcome.cancelled_job_ids) == {running.id, pending.id}
        types = await events_of(store, project.id)
        assert types.count("job_cancelled") == 2 and types[-1] == "project_archived"
        with pytest.raises(errors.InvalidJobTransitionError):  # resultado tardio de job cancelado
            await store.complete_job(running.id, "/m.md", "docling", QUALITY, "tarde")
        assert await store.get_result(running.id) is None
        # tudo permanece consultável
        assert len(await store.list_documents(project.id)) == 2
        restored = await store.unarchive_project(project.id)
        assert restored.status == "active"
        assert (await events_of(store, project.id))[-1] == "project_unarchived"

    run_store(migrated_database, scenario)


def test_delete_project_removes_everything_and_keeps_other_projects(migrated_database):
    async def scenario(store):
        gone, _, doc_gone = await make_document(store, tag="excluir")
        kept, _, doc_kept = await make_document(store, tag="manter")
        job_gone, job_kept = await run_job(store, doc_gone), await run_job(store, doc_kept)
        await store.record_attempt(job_gone.id, "docling", 1.0, error="x")
        await store.complete_job(job_kept.id, "/k.md", "docling", QUALITY, "ok")
        await store.delete_project(gone.id)
        with pytest.raises(errors.ProjectNotFoundError):
            await store.get_project(gone.id)
        with pytest.raises(errors.ProjectNotFoundError):
            await store.delete_project(gone.id)
        with pytest.raises(errors.JobNotFoundError):
            await store.list_attempts(job_gone.id)
        assert await store.list_events(project_id=gone.id) == []
        assert (await store.get_project(kept.id)).id == kept.id
        assert (await store.get_result(job_kept.id)).quality == QUALITY
        assert len(await events_of(store, kept.id)) >= 4

    run_store(migrated_database, scenario)


# --- Status de documento e escala (US1-3, SC-003, cenário 9) --------------------------------------------------


def test_document_status_follows_latest_job_and_keeps_last_successful_result(migrated_database):
    async def scenario(store):
        project, _, document = await make_document(store)
        assert (await store.list_documents(project.id))[0].status == "not_processed"
        first = await run_job(store, document)
        await store.complete_job(first.id, "/v1.md", "docling", QUALITY, "ok")
        second = await store.create_job(document.id, "pymupdf")
        status = (await store.list_documents(project.id))[0]
        assert status.status == "pending" and status.latest_job_id == second.id
        assert status.last_result.job_id == first.id  # status do mais recente, resultado do último bem-sucedido
        await store.cancel_job(second.id, "cancelado")
        status = (await store.list_documents(project.id))[0]
        assert status.status == "cancelled" and status.last_result.job_id == first.id
        third = await run_job(store, document)
        await store.fail_job(third.id, "falhou", "falha")
        status = (await store.list_documents(project.id))[0]
        assert status.status == "failed" and status.last_result.markdown_path == "/v1.md"

    run_store(migrated_database, scenario)


def test_list_documents_scale_with_1000_documents_one_query_and_index_plan(migrated_database):
    async def scenario(store):
        project = await store.create_project(uniq("Escala"))
        source = await store.add_source(project.id, "folder", "in_place", "/grande")
        documents = []
        for number in range(1000):
            document, _ = await store.add_document(project.id, source.id, f"/grande/{number:04d}.pdf")
            documents.append(document)
        for document in documents[::2]:  # metade com job concluído
            job = await run_job(store, document)
            await store.complete_job(job.id, "/g.md", "docling", QUALITY, "ok")
        await store.list_documents(project.id)  # aquecimento (cache quente)
        store.reset_query_count()
        started = time.monotonic()
        statuses = await store.list_documents(project.id)
        elapsed = time.monotonic() - started
        assert store.query_count == 1, "list_documents precisa ser uma única consulta (SC-003)"
        assert len(statuses) == 1000 and sum(s.status == "succeeded" for s in statuses) == 500
        connection = await asyncpg.connect(
            host=migrated_database.resolve_connect_host(), port=migrated_database.port,
            user=migrated_database.user, password=migrated_database.password, database=migrated_database.database,
        )
        try:
            version = await connection.fetchval("SHOW server_version")
            await connection.execute("SET enable_seqscan = off")  # só para provar que o índice serve à consulta
            plan = "\n".join(
                r[0]
                for r in await connection.fetch(
                    f"EXPLAIN SELECT id, status FROM jobs WHERE document_id = {int(documents[0].id)} "
                    "ORDER BY requested_at DESC, id DESC LIMIT 1"
                )
            )
        finally:
            await connection.close()
        print(
            f"SC-003: list_documents de 1000 documentos em {elapsed:.3f} s "
            f"(máquina de desenvolvimento, Python {sys.version.split()[0]} em {platform.platform()}, "
            f"Postgres {version}, cache quente)"
        )
        assert elapsed <= 2.0, f"list_documents levou {elapsed:.2f} s (SC-003: até 2 s)"
        assert "jobs_document_recent_idx" in plan, plan

    run_store(migrated_database, scenario)


# --- Histórico (US4, cenário 10) ---------------------------------------------------------------------------------


def test_add_event_ownership_isolation_order_pagination_and_timezones(migrated_database):
    async def scenario(store):
        project, source, doc_a = await make_document(store, "/p/a.pdf")
        doc_b, _ = await store.add_document(project.id, source.id, "/p/b.pdf")
        job_a = await store.create_job(doc_a.id, "auto")
        other, _, other_doc = await make_document(store, "/p/o.pdf", tag="outro")
        other_job = await store.create_job(other_doc.id, "auto")
        event = await store.add_event(project.id, "attempt_started", "Tentativa iniciada", doc_a.id, job_a.id)
        assert (event.project_id, event.document_id, event.job_id) == (project.id, doc_a.id, job_a.id)
        for kwargs in (
            {"document_id": other_doc.id},
            {"job_id": other_job.id},
            {"document_id": doc_b.id, "job_id": job_a.id},
            {"document_id": 2**62},
        ):
            with pytest.raises(errors.StoreValidationError):
                await store.add_event(project.id, "attempt_started", "x", **kwargs)
        with pytest.raises(errors.StoreValidationError):
            await store.add_event(project.id, "tipo_inventado", "x")
        with pytest.raises(errors.ProjectNotFoundError):
            await store.add_event(2**62, "attempt_started", "x")
        for number in range(250):  # intercala eventos dos dois documentos
            await store.add_event(project.id, "attempt_started", f"e{number}", (doc_a, doc_b)[number % 2].id)
        only_b = await store.list_events(document_id=doc_b.id, limit=500)
        assert only_b and all(e.document_id == doc_b.id for e in only_b)
        keys = [(e.occurred_at, e.id) for e in only_b]
        assert keys == sorted(keys)
        pages, offset = [], 0
        while True:
            page = await store.list_events(project_id=project.id, limit=100, offset=offset)
            pages += page
            if len(page) < 100:
                break
            offset += 100
        full = await store.list_events(project_id=project.id, limit=500)
        assert [e.id for e in pages] == [e.id for e in full] and len({e.id for e in pages}) == len(pages)
        assert all(e.occurred_at.tzinfo is not None for e in full)
        for bad in (0, 501):
            with pytest.raises(errors.StoreValidationError):
                await store.list_events(project_id=project.id, limit=bad)

    run_store(migrated_database, scenario)


def test_history_cannot_be_altered_directly_and_order_survives_session_time_zone(migrated_database):
    async def scenario(store):
        project, _, _ = await make_document(store)
        await store.add_event(project.id, "attempt_started", "extra")
        baseline = [e.id for e in await store.list_events(project_id=project.id, limit=500)]
        connection = await asyncpg.connect(
            host=migrated_database.resolve_connect_host(), port=migrated_database.port,
            user=migrated_database.user, password=migrated_database.password, database=migrated_database.database,
        )
        try:
            with pytest.raises(asyncpg.PostgresError):
                await connection.execute("UPDATE history_events SET message = 'x' WHERE project_id = $1", project.id)
            await connection.execute("SET TIME ZONE 'Asia/Tokyo'")
            rows = await connection.fetch(
                "SELECT id FROM history_events WHERE project_id = $1 ORDER BY occurred_at, id", project.id
            )
        finally:
            await connection.close()
        assert [r["id"] for r in rows] == baseline

    run_store(migrated_database, scenario)


def test_automatic_events_follow_the_operations_and_failures_leave_no_orphans(migrated_database):
    async def scenario(store):
        project = await store.create_project(uniq())
        source = await store.add_source(project.id, "folder", "in_place", "/e")
        document, _ = await store.add_document(project.id, source.id, "/e/a.pdf")
        await store.add_document(project.id, source.id, "/e/a.pdf")  # repetido: sem novo evento
        job = await store.create_job(document.id, "auto")
        await store.start_job(job.id)
        await store.record_attempt(job.id, "docling", 1.0, error="x")
        await store.complete_job(job.id, "/e/a.md", "docling", QUALITY, "ok")
        expected = [
            "project_created", "source_added", "document_added", "job_created", "job_started",
            "attempt_recorded", "job_succeeded",
        ]
        assert await events_of(store, project.id) == expected
        with pytest.raises(errors.ActiveJobExistsError):
            await store.create_job(document.id, "auto")  # job anterior já é final: este cria
            await store.create_job(document.id, "auto")  # o segundo falha e não deixa evento órfão
        assert (await events_of(store, project.id)).count("job_created") == 2
        with pytest.raises(errors.StoreValidationError):
            await store.add_source(project.id, "pasta", "in_place", "/x")
        assert (await events_of(store, project.id)).count("source_added") == 1

    run_store(migrated_database, scenario)


def test_translated_errors_never_carry_server_text_or_connection_parameters(migrated_database):
    async def scenario(store):
        _, _, document = await make_document(store)
        job = await run_job(store, document)
        await store.complete_job(job.id, "/m.md", "docling", QUALITY, "ok")
        with pytest.raises(errors.InvalidJobTransitionError) as caught:
            await store.complete_job(job.id, "/m.md", "docling", QUALITY, "ok")
        text = str(caught.value)
        for leaked in (migrated_database.password, migrated_database.user, migrated_database.database, "DETAIL"):
            assert leaked not in text
        assert caught.value.__cause__ is None

    run_store(migrated_database, scenario)


def test_pool_exhaustion_fails_in_bounded_time_instead_of_blocking(migrated_database):
    async def scenario(store):
        async with store.transaction():  # ocupa a única conexão
            started = time.monotonic()
            with pytest.raises(errors.DatabaseUnavailableError) as caught:
                await store.list_projects()
            assert time.monotonic() - started < 5
            assert "ocupadas" in str(caught.value)

    run_store(migrated_database, scenario, min_size=1, max_size=1, acquire_timeout=0.5)
