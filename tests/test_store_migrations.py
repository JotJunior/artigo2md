"""Testes SEM banco do SQL, do runner de migrações e da guarda do banco temporário (tarefas 3.1.6, 3.2.6, 3.3.4).

O comportamento contra um Postgres de verdade está em `test_store_integration.py` (ARTIGO2MD_DB_TESTS=1).
Aqui o runner roda sobre uma conexão de mentira, só para conferir a ordem das chamadas e que nenhuma
mensagem de erro carrega texto bruto do servidor.
"""
from __future__ import annotations

import ast
import asyncio
import hashlib
import re
from pathlib import Path

import asyncpg
import pytest
import store_db

from artigo2md import router
from artigo2md.store import errors, migrator, models

MIGRATIONS_DIR = Path(migrator.__file__).parent / "migrations"
SQL_0001 = (MIGRATIONS_DIR / "0001_initial.sql").read_text(encoding="utf-8")


# --- known_migrations (3.2.6) -------------------------------------------------------------------------


def test_known_migrations_are_contiguous_from_0001():
    migrations = migrator.known_migrations()
    assert [m.version for m in migrations] == list(range(1, len(migrations) + 1))
    assert migrations[0].name == "0001_initial.sql"


def test_known_migrations_checksum_is_sha256_of_file_bytes_and_stable():
    first = migrator.known_migrations()
    second = migrator.known_migrations()
    assert first == second
    for migration in first:
        raw = (MIGRATIONS_DIR / migration.name).read_bytes()
        assert migration.checksum == hashlib.sha256(raw).hexdigest()
        assert migration.sql == raw.decode("utf-8")


def test_migration_file_names_follow_the_pattern():
    names = sorted(path.name for path in MIGRATIONS_DIR.glob("*.sql"))
    assert names, "nenhum arquivo de migração empacotado"
    for name in names:
        assert re.fullmatch(r"\d{4}_[a-z0-9_]+\.sql", name), name


# --- SQL x constantes (3.1.6) -------------------------------------------------------------------------


def _check_lists(sql: str) -> dict[str, tuple[str, ...]]:
    """Mapeia `CONSTRAINT <nome> CHECK (<col> IN (...))` para a tupla de valores literais."""
    found: dict[str, tuple[str, ...]] = {}
    for match in re.finditer(r"CONSTRAINT (\w+)\s+CHECK \((\w+) IN \(([^)]*)\)\)", sql):
        found[match.group(1)] = tuple(re.findall(r"'([^']*)'", match.group(3)))
    return found


def test_sql_enumerated_values_match_constants_and_router_engines():
    lists = _check_lists(SQL_0001)
    assert lists["projects_status_check"] == models.PROJECT_STATUSES
    assert lists["sources_kind_check"] == models.SOURCE_KINDS
    assert lists["sources_mode_check"] == models.SOURCE_MODES
    assert lists["jobs_status_check"] == models.JOB_STATUSES
    assert lists["history_events_event_type_check"] == models.EVENT_TYPES
    assert lists["jobs_requested_engine_check"] == tuple(router.ENGINES)
    assert tuple(router.ENGINES) == models.REQUESTED_ENGINES


def test_sql_final_states_and_active_index_agree_with_constants():
    assert set(models.FINAL_JOB_STATUSES) == {"succeeded", "failed", "cancelled"}
    assert "WHERE status IN ('pending', 'running')" in SQL_0001  # índice de job ativo por documento
    for status in models.FINAL_JOB_STATUSES:
        assert f"'{status}'" in SQL_0001


def test_sql_declares_the_documented_objects():
    for name in (
        "projects_active_name_key",
        "jobs_one_active_per_document",
        "jobs_document_recent_idx",
        "jobs_project_idx",
        "sources_project_idx",
        "documents_source_idx",
        "history_events_job_idx",
        "jobs_guard_transition",
        "job_results_require_succeeded_job",
        "jobs_succeeded_requires_result",
        "history_events_append_only",
    ):
        assert name in SQL_0001, name
    assert "DEFERRABLE INITIALLY DEFERRED" in SQL_0001
    assert "DEFAULT clock_timestamp()" in SQL_0001
    assert "lower(name)" not in SQL_0001  # a regra de nome não depende da localidade do banco


def test_sql_identifiers_are_ascii_snake_case_english_shape():
    """Quickstart, cenário 14.4 (FR-020): todo identificador declarado é ASCII `snake_case`."""
    names = re.findall(
        r"CREATE (?:UNIQUE )?(?:TABLE|INDEX|FUNCTION|TRIGGER|CONSTRAINT TRIGGER) (\w+)", SQL_0001
    )
    names += re.findall(r"CONSTRAINT (?!TRIGGER)(\w+)", SQL_0001)
    assert names
    for name in names:
        assert re.fullmatch(r"[a-z][a-z0-9_]*", name), name


STORE_DIR = Path(migrator.__file__).parent
_SNAKE_CASE = re.compile(r"_{0,2}[a-z][a-z0-9]*(?:_[a-z0-9]+)*_{0,2}")  # aceita prefixo `_` e dunders (`__init__`)
_PASCAL_CASE = re.compile(r"_?[A-Z][A-Za-z0-9]*")


def test_python_files_modules_classes_and_functions_have_the_expected_shape():
    """FR-020 / US5-8 (quickstart 14.4): arquivos e módulos `snake_case`, classes `PascalCase`, funções `snake_case`, tudo ASCII.

    Só confere a forma; que os nomes estejam em inglês segue na revisão humana.
    """
    sources = sorted(STORE_DIR.rglob("*.py"))
    assert sources, "nenhum módulo Python encontrado em src/artigo2md/store/"
    problems: list[str] = []
    for path in sources:
        relative = path.relative_to(STORE_DIR)
        for part in relative.parts[:-1]:  # diretórios do pacote
            if part != "__pycache__" and not re.fullmatch(r"[a-z][a-z0-9_]*", part):
                problems.append(f"diretório {relative.parent}")
        if not path.stem.isascii() or not _SNAKE_CASE.fullmatch(path.stem):
            problems.append(f"arquivo {relative}")
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), filename=str(path))):
            if isinstance(node, ast.ClassDef) and not (node.name.isascii() and _PASCAL_CASE.fullmatch(node.name)):
                problems.append(f"classe {node.name} em {relative}")
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not (
                node.name.isascii() and _SNAKE_CASE.fullmatch(node.name)
            ):
                problems.append(f"função {node.name} em {relative}")
    assert not problems, "nomes fora da forma esperada: " + "; ".join(problems)


# --- guarda de nome do banco temporário (3.3.4) --------------------------------------------------------


def test_valid_temporary_names_pass_the_guard():
    for _ in range(5):
        name = store_db.new_test_db_name()
        store_db.assert_droppable(name, "artigo2md")
    store_db.assert_droppable("artigo2md_test_0123456789abcdef", "artigo2md")


@pytest.mark.parametrize(
    "name",
    [
        "artigo2md",  # banco real típico
        "postgres",
        "artigo2md_test_",
        "artigo2md_test_0123456789abcde",  # 15 hex
        "artigo2md_test_0123456789abcdef0",  # 17 hex
        "artigo2md_test_0123456789ABCDEF",  # maiúsculas
        "artigo2md_test_0123456789abcdeg",  # não-hex
        'artigo2md_test_0123456789abcdef"; DROP DATABASE artigo2md; --',
        "artigo2md_test_0123456789abcdef\n",
        " artigo2md_test_0123456789abcdef",
        "",
    ],
)
def test_guard_refuses_unsafe_names_without_touching_anything(name):
    with pytest.raises(store_db.UnsafeDropError):
        store_db.assert_droppable(name, "artigo2md")


def test_guard_refuses_the_real_database_even_if_it_matches_the_pattern():
    name = "artigo2md_test_0123456789abcdef"
    with pytest.raises(store_db.UnsafeDropError):
        store_db.assert_droppable(name, name)


def test_drop_and_create_run_the_guard_before_any_connection(monkeypatch):
    """Nome fora do padrão aborta antes de qualquer tentativa de conexão (nada é executado)."""

    async def _boom(*_args, **_kwargs):
        raise AssertionError("tentou conectar")

    monkeypatch.setattr(store_db, "_connect", _boom)

    class _Settings:
        database = "artigo2md"

    with pytest.raises(store_db.UnsafeDropError):
        asyncio.run(store_db.drop_database(_Settings(), "artigo2md"))
    with pytest.raises(store_db.UnsafeDropError):
        asyncio.run(store_db.create_database(_Settings(), 'x"; DROP DATABASE artigo2md; --'))


def test_quote_ident_doubles_quotes():
    assert store_db.quote_ident("abc") == '"abc"'
    assert store_db.quote_ident('a"b') == '"a""b"'


# --- runner sobre conexão de mentira ------------------------------------------------------------------


class _Tx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False


class FakeConnection:
    """Registra as chamadas; `history` simula as linhas de `schema_migrations`."""

    def __init__(self, history=(), lock=True, fail_on: str | None = None, fail_with=None):
        self.history = [{"version": v, "checksum": c} for v, c in history]
        self.lock = lock
        self.fail_on = fail_on
        self.fail_with = fail_with
        self.statements: list[str] = []

    async def fetchval(self, query, *args):
        return self.lock

    async def fetch(self, query, *args):
        return self.history

    def transaction(self):
        return _Tx()

    async def execute(self, query, *args):
        self.statements.append(query)
        if self.fail_on is not None and self.fail_on in query:
            raise self.fail_with


def _migration(version: int, sql: str = "SELECT 1") -> migrator.Migration:
    name = f"{version:04d}_test.sql"
    return migrator.Migration(version, name, hashlib.sha256(sql.encode()).hexdigest(), sql)


def test_runner_applies_pending_in_order_and_reports():
    connection = FakeConnection()
    report = asyncio.run(migrator.apply_migrations_on(connection, [_migration(1), _migration(2)]))
    assert report.applied == (1, 2)
    assert report.current_version == 2
    inserts = [s for s in connection.statements if s.startswith("INSERT INTO schema_migrations")]
    assert len(inserts) == 2
    assert connection.statements[-1].startswith("SELECT pg_advisory_unlock")


def test_runner_is_a_noop_when_everything_is_applied():
    migrations = [_migration(1), _migration(2)]
    connection = FakeConnection(history=[(m.version, m.checksum) for m in migrations])
    report = asyncio.run(migrator.apply_migrations_on(connection, migrations))
    assert report.applied == ()
    assert report.current_version == 2
    assert "já está atualizado (versão 2)" in report.summary
    assert not any(s.startswith("INSERT") for s in connection.statements)


def test_runner_applies_only_pending_when_partially_applied():
    migrations = [_migration(1), _migration(2), _migration(3)]
    connection = FakeConnection(history=[(1, migrations[0].checksum)])
    report = asyncio.run(migrator.apply_migrations_on(connection, migrations))
    assert report.applied == (2, 3)
    assert report.current_version == 3


def test_runner_refuses_newer_schema_before_applying_anything():
    migrations = [_migration(1)]
    connection = FakeConnection(history=[(1, migrations[0].checksum), (9999, "0" * 64)])
    with pytest.raises(errors.SchemaTooNewError) as caught:
        asyncio.run(migrator.apply_migrations_on(connection, migrations))
    assert "versão 9999" in str(caught.value)
    assert not any("SELECT 1" == s for s in connection.statements)


def test_runner_refuses_changed_checksum_before_applying_anything():
    migrations = [_migration(1), _migration(2)]
    connection = FakeConnection(history=[(1, "f" * 64)])
    with pytest.raises(errors.MigrationChecksumError) as caught:
        asyncio.run(migrator.apply_migrations_on(connection, migrations))
    assert "0001_test.sql foi alterada depois de aplicada" in str(caught.value)
    assert not any(s.startswith("INSERT") for s in connection.statements)


def test_runner_refuses_diverging_history():
    """Versão aplicada abaixo da maior conhecida, mas sem arquivo: histórico divergente."""
    migrations = [_migration(2)]  # simula o arquivo 0001 que sumiu do código (a verificação é por versão)
    connection = FakeConnection(history=[(1, "a" * 64)])
    with pytest.raises(errors.MigrationChecksumError) as caught:
        asyncio.run(migrator.apply_migrations_on(connection, migrations))
    assert "a migração 0001 foi aplicada mas não existe mais" in str(caught.value)


def test_runner_times_out_waiting_for_the_lock_and_never_runs_anything():
    connection = FakeConnection(lock=False)
    with pytest.raises(errors.MigrationLockTimeoutError) as caught:
        asyncio.run(
            migrator.apply_migrations_on(
                connection, [_migration(1)], lock_wait_seconds=0.05, lock_poll_seconds=0.01
            )
        )
    assert "há mais de" in str(caught.value)
    assert connection.statements == []  # nem criou a tabela de controle, nem tentou liberar o lock


def test_failed_migration_names_the_file_and_never_leaks_server_text():
    secret = "detalhe-bruto-do-servidor password=segredo-123"
    connection = FakeConnection(fail_on="BAD SQL", fail_with=asyncpg.PostgresError(secret))
    migrations = [_migration(1), _migration(2, "BAD SQL")]
    with pytest.raises(errors.MigrationFailedError) as caught:
        asyncio.run(migrator.apply_migrations_on(connection, migrations))
    text = str(caught.value)
    assert "0002_test.sql" in text and "foi desfeita" in text
    assert secret not in text and "segredo-123" not in text
    assert caught.value.__cause__ is None and caught.value.__suppress_context__


def test_connection_loss_becomes_database_unavailable_without_raw_text():
    connection = FakeConnection(fail_on="BAD SQL", fail_with=OSError("host=10.0.0.9 senha=xyz"))
    with pytest.raises(errors.DatabaseUnavailableError) as caught:
        asyncio.run(migrator.apply_migrations_on(connection, [_migration(1, "BAD SQL")]))
    assert "10.0.0.9" not in str(caught.value) and "xyz" not in str(caught.value)
    assert caught.value.__cause__ is None


def test_apply_migrations_connect_failure_is_generic_and_hides_the_password(monkeypatch):
    from artigo2md.store import DbSettings

    settings = DbSettings(host="127.0.0.1", port=5432, user="u", database="d", password="senha-super-secreta")

    async def _refuse(*_args, **kwargs):
        raise OSError(f"falha com password={kwargs.get('password')}")

    monkeypatch.setattr(asyncpg, "connect", _refuse)
    with pytest.raises(errors.DatabaseUnavailableError) as caught:
        asyncio.run(migrator.apply_migrations(settings))
    assert "senha-super-secreta" not in str(caught.value)
    assert caught.value.__cause__ is None and caught.value.__suppress_context__
