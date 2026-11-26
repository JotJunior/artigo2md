"""Testes de integração da camada `artigo2md.store` contra o Postgres da stack local.

Só rodam com `ARTIGO2MD_DB_TESTS=1` e a stack no ar; cada teste usa um banco temporário
`artigo2md_test_<16 hex>` (tests/store_db.py), nunca o banco real (FR-019, SC-006).
Cada teste assíncrono roda no próprio laço de eventos (`asyncio.run`), sem `pytest-asyncio` (tarefa 2.1.3).
"""
from __future__ import annotations

import asyncio
import contextlib
import platform
import sys
import time

import asyncpg
import pytest
from store_db import requires_db

from artigo2md.store import errors, migrator

# Versões conhecidas do pacote (cresce com cada migração nova): os testes não fixam "só a 0001".
ALL_VERSIONS = tuple(m.version for m in migrator.known_migrations())
LATEST = ALL_VERSIONS[-1]
BROKEN_VERSION = LATEST + 1

pytestmark = requires_db


async def _open(settings) -> asyncpg.Connection:
    return await asyncpg.connect(
        host=settings.resolve_connect_host(),
        port=settings.port,
        user=settings.user,
        password=settings.password,
        database=settings.database,
        timeout=10,
    )


async def _history(connection) -> list[tuple[int, str, str]]:
    rows = await connection.fetch("SELECT version, name, checksum FROM schema_migrations ORDER BY version")
    return [(r["version"], r["name"], r["checksum"]) for r in rows]


async def _schema_snapshot(connection) -> dict[str, list]:
    """Retrato do schema `public` lido do catálogo: colunas, índices, constraints, gatilhos e funções."""
    columns = await connection.fetch(
        "SELECT table_name, column_name, data_type, is_nullable, column_default "
        "FROM information_schema.columns WHERE table_schema = 'public' ORDER BY table_name, ordinal_position"
    )
    indexes = await connection.fetch(
        "SELECT tablename, indexname, indexdef FROM pg_indexes WHERE schemaname = 'public' ORDER BY indexname"
    )
    constraints = await connection.fetch(
        "SELECT c.conrelid::regclass::text AS table_name, c.conname, pg_get_constraintdef(c.oid) AS definition "
        "FROM pg_constraint c JOIN pg_namespace n ON n.oid = c.connamespace "
        "WHERE n.nspname = 'public' ORDER BY c.conname"
    )
    triggers = await connection.fetch(
        "SELECT t.tgrelid::regclass::text AS table_name, t.tgname, pg_get_triggerdef(t.oid) AS definition "
        "FROM pg_trigger t JOIN pg_class r ON r.oid = t.tgrelid JOIN pg_namespace n ON n.oid = r.relnamespace "
        "WHERE n.nspname = 'public' AND NOT t.tgisinternal ORDER BY t.tgname"
    )
    functions = await connection.fetch(
        "SELECT p.proname, pg_get_functiondef(p.oid) AS definition "
        "FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
        "WHERE n.nspname = 'public' ORDER BY p.proname, p.oid"
    )
    return {
        name: [tuple(row.values()) for row in rows]
        for name, rows in (
            ("columns", columns),
            ("indexes", indexes),
            ("constraints", constraints),
            ("triggers", triggers),
            ("functions", functions),
        )
    }


# --- Migrações (US3, SC-001) ---------------------------------------------------------------------------


def test_empty_database_gets_all_migrations_then_second_call_changes_nothing(empty_database):
    async def scenario():
        connection = await _open(empty_database)
        try:
            server_version = await connection.fetchval("SHOW server_version")
            started = time.monotonic()
            first = await migrator.apply_migrations_on(connection)
            elapsed = time.monotonic() - started
            before = await _history(connection)
            schema_before = await _schema_snapshot(connection)
            second = await migrator.apply_migrations_on(connection)
            after = await _history(connection)
            schema_after = await _schema_snapshot(connection)
            tables = {
                r["tablename"]
                for r in await connection.fetch("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
            }
            return first, second, before, after, schema_before, schema_after, elapsed, tables, server_version
        finally:
            await connection.close()

    first, second, before, after, schema_before, schema_after, elapsed, tables, server_version = asyncio.run(
        scenario()
    )
    assert first.applied == ALL_VERSIONS and first.current_version == LATEST
    assert elapsed <= 10, f"migração em banco vazio levou {elapsed:.2f} s (SC-001: até 10 s)"
    # SC-001: o teste registra o ambiente medido (sistema, versão do Python e versão do servidor Postgres).
    print(
        f"SC-001: migração em banco vazio em {elapsed:.2f} s "
        f"(sistema {platform.platform()}, Python {sys.version.split()[0]}, Postgres {server_version})"
    )
    assert second.applied == () and second.current_version == LATEST
    assert before == after and len(before) == LATEST  # nenhum registro de histórico alterado
    assert schema_before["columns"] and schema_before["constraints"] and schema_before["triggers"]
    assert schema_before == schema_after  # a segunda aplicação não altera o schema
    assert {
        "projects", "sources", "documents", "jobs", "job_attempts", "job_results", "history_events",
        "schema_migrations",
    } <= tables


def test_apply_migrations_with_settings_works_on_an_empty_database(empty_database):
    report = asyncio.run(migrator.apply_migrations(empty_database))
    assert report.applied == ALL_VERSIONS


def test_concurrent_calls_both_succeed_and_each_version_is_recorded_once(empty_database):
    async def scenario():
        first, second = await _open(empty_database), await _open(empty_database)  # lock é da sessão: 1 conexão cada
        checker = await _open(empty_database)
        try:
            reports = await asyncio.gather(
                migrator.apply_migrations_on(first), migrator.apply_migrations_on(second)
            )
            return reports, await _history(checker)
        finally:
            for connection in (first, second, checker):
                await connection.close()

    reports, history = asyncio.run(scenario())
    assert sorted(v for report in reports for v in report.applied) == list(ALL_VERSIONS)  # cada versão aplicada uma só vez
    assert [row[0] for row in history] == list(ALL_VERSIONS)


def test_lock_held_by_another_session_times_out_without_applying(empty_database):
    async def scenario():
        holder, waiter, checker = await _open(empty_database), await _open(empty_database), await _open(empty_database)
        try:
            assert await holder.fetchval("SELECT pg_try_advisory_lock($1)", migrator.LOCK_KEY)
            with pytest.raises(errors.MigrationLockTimeoutError) as caught:
                await migrator.apply_migrations_on(waiter, lock_wait_seconds=0.5, lock_poll_seconds=0.1)
            await holder.execute("SELECT pg_advisory_unlock($1)", migrator.LOCK_KEY)
            applied = await migrator.apply_migrations_on(waiter)  # liberado: agora aplica
            return caught.value, applied, await _history(checker)
        finally:
            for connection in (holder, waiter, checker):
                await connection.close()

    error, applied, history = asyncio.run(scenario())
    assert "há mais de" in str(error)
    assert applied.applied == ALL_VERSIONS and len(history) == LATEST


def test_changed_checksum_newer_schema_and_diverging_history_are_refused(empty_database):
    async def scenario():
        connection = await _open(empty_database)
        try:
            await migrator.apply_migrations_on(connection)
            outcomes = {}

            await connection.execute("UPDATE schema_migrations SET checksum = repeat('0', 64) WHERE version = 1")
            with pytest.raises(errors.MigrationChecksumError) as changed:
                await migrator.apply_migrations_on(connection)
            outcomes["changed"] = str(changed.value)
            await connection.execute(
                "UPDATE schema_migrations SET checksum = $1 WHERE version = 1", migrator.known_migrations()[0].checksum
            )

            await connection.execute(
                "INSERT INTO schema_migrations (version, name, checksum) VALUES (9999, '9999_future.sql', repeat('a', 64))"
            )
            with pytest.raises(errors.SchemaTooNewError) as newer:
                await migrator.apply_migrations_on(connection)
            outcomes["newer"] = str(newer.value)
            await connection.execute("DELETE FROM schema_migrations WHERE version = 9999")

            # Histórico divergente: a versão 1 está aplicada, mas o "código" só conhece a 2.
            only_two = [migrator.Migration(LATEST, f"{LATEST:04d}_other.sql", "b" * 64, "SELECT 1")]
            with pytest.raises(errors.MigrationChecksumError) as diverged:
                await migrator.apply_migrations_on(connection, only_two)
            outcomes["diverged"] = str(diverged.value)
            outcomes["history"] = await _history(connection)
            return outcomes
        finally:
            await connection.close()

    outcomes = asyncio.run(scenario())
    assert "0001_initial.sql foi alterada depois de aplicada" in outcomes["changed"]
    assert "versão 9999" in outcomes["newer"]
    assert "a migração 0001 foi aplicada mas não existe mais" in outcomes["diverged"]
    assert [row[0] for row in outcomes["history"]] == list(ALL_VERSIONS)  # nada foi aplicado nem removido pelas recusas


def test_failing_migration_in_the_middle_is_rolled_back_and_not_recorded(empty_database):
    broken = migrator.Migration(
        version=BROKEN_VERSION,
        name=f"{BROKEN_VERSION:04d}_broken.sql",
        checksum="c" * 64,
        sql="CREATE TABLE partial_table (id integer); SELECT * FROM table_that_does_not_exist_zz;",
    )

    async def scenario():
        connection = await _open(empty_database)
        try:
            with pytest.raises(errors.MigrationFailedError) as caught:
                await migrator.apply_migrations_on(connection, [*migrator.known_migrations(), broken])
            partial = await connection.fetchval("SELECT to_regclass('public.partial_table')")
            return caught.value, partial, await _history(connection)
        finally:
            await connection.close()

    error, partial, history = asyncio.run(scenario())
    assert f"{BROKEN_VERSION:04d}_broken.sql" in str(error) and "foi desfeita" in str(error)
    assert "table_that_does_not_exist_zz" not in str(error)  # nada do texto bruto do servidor
    assert error.__cause__ is None
    assert partial is None  # nenhuma tabela parcial
    assert [row[0] for row in history] == list(ALL_VERSIONS)  # as migrações válidas permanecem; a quebrada não


# --- Esquema: regras que o próprio banco garante (data-model.md) ---------------------------------------


class _Rollback(Exception):
    """Sentinela para desfazer tudo ao fim de um cenário sem persistir nada no banco temporário."""


@contextlib.asynccontextmanager
async def _scratch(connection):
    try:
        async with connection.transaction():
            yield
            raise _Rollback
    except _Rollback:
        pass


async def _refused(connection, sql: str, *args) -> asyncpg.PostgresError:
    """Executa dentro de um savepoint e devolve o erro do servidor (falha se o comando for aceito)."""
    try:
        async with connection.transaction():
            await connection.execute(sql, *args)
    except asyncpg.PostgresError as exc:
        return exc
    raise AssertionError(f"o banco aceitou um comando que deveria recusar: {sql[:60]}")


async def _seed(connection, name: str = "p") -> dict[str, int]:
    project = await connection.fetchval(
        "INSERT INTO projects (name, name_key) VALUES ($1, $1) RETURNING id", name
    )
    source = await connection.fetchval(
        "INSERT INTO sources (project_id, kind, mode, path) VALUES ($1, 'folder', 'in_place', '/x') RETURNING id",
        project,
    )
    document = await connection.fetchval(
        "INSERT INTO documents (project_id, source_id, path) VALUES ($1, $2, '/x/a.pdf') RETURNING id",
        project,
        source,
    )
    job = await connection.fetchval(
        "INSERT INTO jobs (project_id, document_id, requested_engine) VALUES ($1, $2, 'auto') RETURNING id",
        project,
        document,
    )
    return {"project": project, "source": source, "document": document, "job": job}


def _run_schema(migrated_database, scenario):
    async def runner():
        connection = await _open(migrated_database)
        try:
            async with _scratch(connection):
                await scenario(connection)
        finally:
            await connection.close()

    asyncio.run(runner())


def test_schema_project_name_key_is_unique_only_among_active(migrated_database):
    async def scenario(c):
        await c.execute("INSERT INTO projects (name, name_key) VALUES ('A', 'a')")
        error = await _refused(c, "INSERT INTO projects (name, name_key) VALUES ('a', 'a')")
        assert isinstance(error, asyncpg.UniqueViolationError)
        assert error.constraint_name == "projects_active_name_key"
        await c.execute("UPDATE projects SET status = 'archived' WHERE name_key = 'a'")
        await c.execute("INSERT INTO projects (name, name_key) VALUES ('a', 'a')")  # nome liberado pelo arquivamento
        assert isinstance(await _refused(c, "INSERT INTO projects (name, name_key) VALUES (' ', ' ')"), asyncpg.CheckViolationError)

    _run_schema(migrated_database, scenario)


def test_schema_composite_foreign_keys_keep_everything_inside_one_project(migrated_database):
    async def scenario(c):
        one, other = await _seed(c, "um"), await _seed(c, "outro")
        error = await _refused(
            c,
            "INSERT INTO documents (project_id, source_id, path) VALUES ($1, $2, '/y.pdf')",
            one["project"],
            other["source"],
        )
        assert isinstance(error, asyncpg.ForeignKeyViolationError)
        error = await _refused(
            c,
            "INSERT INTO history_events (project_id, document_id, event_type, message) VALUES ($1, $2, 'job_created', 'x')",
            one["project"],
            other["document"],
        )
        assert isinstance(error, asyncpg.ForeignKeyViolationError)
        error = await _refused(
            c,
            "INSERT INTO history_events (project_id, document_id, job_id, event_type, message) "
            "VALUES ($1, $2, $3, 'job_created', 'x')",
            one["project"],
            one["document"],
            other["job"],
        )
        assert isinstance(error, asyncpg.ForeignKeyViolationError)
        await c.execute(  # evento só do projeto continua válido
            "INSERT INTO history_events (project_id, event_type, message) VALUES ($1, 'project_created', 'x')",
            one["project"],
        )

    _run_schema(migrated_database, scenario)


def test_schema_one_active_job_per_document_and_new_job_after_final(migrated_database):
    async def scenario(c):
        ids = await _seed(c)
        error = await _refused(
            c,
            "INSERT INTO jobs (project_id, document_id, requested_engine) VALUES ($1, $2, 'docling')",
            ids["project"],
            ids["document"],
        )
        assert isinstance(error, asyncpg.UniqueViolationError)
        assert error.constraint_name == "jobs_one_active_per_document"
        await c.execute("UPDATE jobs SET status = 'cancelled', finished_at = now() WHERE id = $1", ids["job"])
        await c.execute(
            "INSERT INTO jobs (project_id, document_id, requested_engine) VALUES ($1, $2, 'docling')",
            ids["project"],
            ids["document"],
        )
        assert isinstance(
            await _refused(
                c,
                "INSERT INTO jobs (project_id, document_id, requested_engine) VALUES ($1, $2, 'inexistente')",
                ids["project"],
                ids["document"],
            ),
            asyncpg.CheckViolationError,
        )

    _run_schema(migrated_database, scenario)


def test_schema_job_transitions_are_guarded(migrated_database):
    async def scenario(c):
        ids = await _seed(c)
        job = ids["job"]
        assert isinstance(
            await _refused(c, "UPDATE jobs SET status = 'succeeded', finished_at = now() WHERE id = $1", job),
            asyncpg.PostgresError,
        )  # pending -> succeeded não existe
        await c.execute("UPDATE jobs SET status = 'running', started_at = now() WHERE id = $1", job)
        assert isinstance(  # não existe running -> pending
            await _refused(c, "UPDATE jobs SET status = 'pending', started_at = NULL WHERE id = $1", job),
            asyncpg.PostgresError,
        )
        await c.execute(
            "UPDATE jobs SET status = 'failed', finished_at = now(), error_summary = 'falhou' WHERE id = $1", job
        )
        for sql in (
            "UPDATE jobs SET status = 'running' WHERE id = $1",
            "UPDATE jobs SET error_summary = 'outro' WHERE id = $1",
            "UPDATE jobs SET status = 'cancelled' WHERE id = $1",
        ):
            assert isinstance(await _refused(c, sql, job), asyncpg.CheckViolationError), sql

    _run_schema(migrated_database, scenario)


def test_schema_job_timestamp_and_error_consistency(migrated_database):
    async def scenario(c):
        ids = await _seed(c)
        job = ids["job"]
        assert isinstance(
            await _refused(c, "UPDATE jobs SET started_at = now() WHERE id = $1", job), asyncpg.CheckViolationError
        )  # pending com started_at
        await c.execute("UPDATE jobs SET status = 'running', started_at = now() WHERE id = $1", job)
        assert isinstance(
            await _refused(c, "UPDATE jobs SET status = 'failed', finished_at = now() WHERE id = $1", job),
            asyncpg.CheckViolationError,
        )  # failed sem error_summary

    _run_schema(migrated_database, scenario)


def test_schema_result_requires_succeeded_job_and_succeeded_job_requires_result(migrated_database):
    insert = (
        "INSERT INTO job_results (job_id, markdown_path, engine, score, accepted, tables_found, tables_expected, "
        "chars_per_page, issues) VALUES ($1, '/x/a.md', 'docling', 0, false, 0, 0, 0, '{}')"
    )

    async def scenario(c):
        ids = await _seed(c)
        job = ids["job"]
        assert isinstance(await _refused(c, insert, job), asyncpg.CheckViolationError)  # job ainda pending
        await c.execute("UPDATE jobs SET status = 'running', started_at = now() WHERE id = $1", job)
        await c.execute("UPDATE jobs SET status = 'succeeded', finished_at = now() WHERE id = $1", job)
        # Gatilho diferido: sem resultado, a verificação do commit (forçada aqui) recusa.
        error = None
        try:
            async with c.transaction():
                await c.execute("SET CONSTRAINTS jobs_succeeded_requires_result IMMEDIATE")
        except asyncpg.PostgresError as exc:
            error = exc
        assert isinstance(error, asyncpg.CheckViolationError)
        await c.execute(insert, job)
        await c.execute("SET CONSTRAINTS jobs_succeeded_requires_result IMMEDIATE")  # com resultado, passa
        assert isinstance(await _refused(c, insert, job), asyncpg.UniqueViolationError)  # um resultado por job

    _run_schema(migrated_database, scenario)


def test_schema_attempt_quality_rules(migrated_database):
    base = "INSERT INTO job_attempts (job_id, position, engine, seconds{cols}) VALUES ($1, {pos}, 'docling', 1.5{vals})"

    async def scenario(c):
        job = (await _seed(c))["job"]
        full_cols = ", score, accepted, tables_found, tables_expected, chars_per_page, issues"
        full_vals = ", 80, true, 1, 1, 1500, '{}'"
        await c.execute(base.format(cols=full_cols, vals=full_vals, pos=1), job)
        await c.execute(base.format(cols=", error", vals=", 'falhou'", pos=2), job)
        for cols, vals, pos in (
            ("", "", 3),  # sem qualidade e sem erro
            (", score", ", 50", 3),  # qualidade pela metade
            (full_cols, ", 101, true, 1, 1, 1500, '{}'", 3),  # score fora da faixa
            (full_cols, ", 80, true, -1, 1, 1500, '{}'", 3),
        ):
            assert isinstance(
                await _refused(c, base.format(cols=cols, vals=vals, pos=pos), job), asyncpg.CheckViolationError
            ), (cols, vals)
        error = await _refused(c, base.format(cols=", error", vals=", 'outra'", pos=2), job)
        assert isinstance(error, asyncpg.UniqueViolationError)  # posição repetida no job

    _run_schema(migrated_database, scenario)


def test_schema_history_is_append_only_and_uses_write_time(migrated_database):
    async def scenario(c):
        ids = await _seed(c)
        first = await c.fetchval(
            "INSERT INTO history_events (project_id, event_type, message) VALUES ($1, 'project_created', 'a') RETURNING id",
            ids["project"],
        )
        second = await c.fetchval(
            "INSERT INTO history_events (project_id, event_type, message) VALUES ($1, 'source_added', 'b') RETURNING id",
            ids["project"],
        )
        # Filtra pelo projeto do cenário: o banco da sessão é compartilhado, e outros módulos gravam eventos nele.
        rows = await c.fetch(
            "SELECT id, occurred_at FROM history_events WHERE project_id = $1 ORDER BY occurred_at, id", ids["project"]
        )
        assert [r["id"] for r in rows] == [first, second]
        assert rows[0]["occurred_at"].tzinfo is not None
        assert rows[0]["occurred_at"] < rows[1]["occurred_at"]  # clock_timestamp: instantes crescentes na transação
        assert isinstance(
            await _refused(c, "UPDATE history_events SET message = 'x' WHERE id = $1", first),
            asyncpg.CheckViolationError,
        )
        assert isinstance(
            await _refused(
                c, "INSERT INTO history_events (project_id, event_type, message) VALUES ($1, 'tipo_novo', 'x')", ids["project"]
            ),
            asyncpg.CheckViolationError,
        )

    _run_schema(migrated_database, scenario)


def test_schema_deleting_a_project_cascades_without_touching_another(migrated_database):
    async def scenario(c):
        one, other = await _seed(c, "excluir"), await _seed(c, "ficar")
        for ids in (one, other):
            await c.execute(
                "INSERT INTO history_events (project_id, document_id, job_id, event_type, message) "
                "VALUES ($1, $2, $3, 'job_created', 'x')",
                ids["project"],
                ids["document"],
                ids["job"],
            )
        await c.execute("DELETE FROM projects WHERE id = $1", one["project"])
        for table in ("sources", "documents", "jobs", "history_events"):
            assert await c.fetchval(f"SELECT count(*) FROM {table} WHERE project_id = $1", one["project"]) == 0
            assert await c.fetchval(f"SELECT count(*) FROM {table} WHERE project_id = $1", other["project"]) == 1

    _run_schema(migrated_database, scenario)
