"""Testes SEM banco do repositório: validação antes do SQL, regras de ramificação, tradução de erros e
inspeção estática do código (tarefas 4.1.3, 4.1.4, 5.4.5 e a parte offline de 4.2-6.2).

O SQL em si só pode ser verificado contra um Postgres (`test_store_integration.py`); aqui o banco é um
dublê roteirizado que devolve linhas prontas e registra as chamadas.
"""
from __future__ import annotations

import ast
import asyncio
from datetime import datetime, timezone
from pathlib import Path

import asyncpg
import pytest

from artigo2md.store import DbSettings, errors, models, repository
from artigo2md.store.repository import Store, StoreSession, _translate, name_key

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
SOURCE_FILE = Path(repository.__file__)


class _Boom:
    """Dublê que falha se qualquer SQL for enviado: prova que a validação ocorre antes do banco."""

    def __getattr__(self, name):
        async def _fail(*_a, **_k):
            raise AssertionError(f"SQL enviado antes da validação: {name}")

        return _fail


class _Script:
    """Dublê roteirizado: cada chamada consome a próxima resposta `(método, valor)` e a registra."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls: list[tuple[str, str, tuple]] = []

    async def _call(self, method, sql, args):
        self.calls.append((method, " ".join(sql.split()), args))
        assert self.responses, f"chamada inesperada: {method} {sql[:60]}"
        expected, value = self.responses.pop(0)
        assert expected == method, f"esperado {expected}, chamado {method}: {sql[:60]}"
        if isinstance(value, BaseException):
            raise value
        return value

    async def fetch(self, sql, *args, fk_error=None):
        return await self._call("fetch", sql, args)

    async def fetchrow(self, sql, *args, fk_error=None):
        return await self._call("fetchrow", sql, args)

    async def fetchval(self, sql, *args, fk_error=None):
        return await self._call("fetchval", sql, args)

    async def execute(self, sql, *args, fk_error=None):
        return await self._call("execute", sql, args)


def _session(*responses) -> tuple[StoreSession, _Script]:
    db = _Script(*responses)
    return StoreSession(db), db


def _run(coro):
    return asyncio.run(coro)


def _project_row(status="active", name="P"):
    return {"id": 1, "name": name, "status": status, "created_at": NOW, "updated_at": NOW}


def _event_row(event_type="project_created"):
    return {"id": 9, "project_id": 1, "document_id": None, "job_id": None, "event_type": event_type,
            "occurred_at": NOW, "message": "m"}


def _job_row(status="pending"):
    return {"id": 5, "project_id": 1, "document_id": 3, "requested_engine": "auto", "status": status,
            "requested_at": NOW, "started_at": None, "finished_at": None, "error_summary": None, "published_at": None}


# --- name_key -------------------------------------------------------------------------------------------


def test_name_key_rules_from_the_spec():
    assert name_key("Á") == name_key("á")
    assert name_key("Straße") == name_key("STRASSE")
    assert name_key("a") != name_key("á")
    assert name_key("é") == name_key("é") == name_key("É")  # NFD = NFC


# --- Validação ocorre antes de qualquer SQL ---------------------------------------------------------------

_GOOD_QUALITY = models.QualityRecord(80.0, True, 1, 1, 1500.0, ("ok",))

_INVALID_CALLS = {
    "name vazio": lambda s: s.create_project("   "),
    "name nao texto": lambda s: s.create_project(None),
    "name com nulo": lambda s: s.create_project("a\x00b"),
    "kind invalido": lambda s: s.add_source(1, "pasta", "in_place", "/x"),
    "mode invalido": lambda s: s.add_source(1, "folder", "linked", "/x"),
    "path vazio": lambda s: s.add_source(1, "folder", "in_place", ""),
    "id negativo": lambda s: s.get_project(-1),
    "id bool": lambda s: s.get_project(True),
    "motor invalido": lambda s: s.create_job(1, "pdfbox"),
    "tentativa sem qualidade e sem erro": lambda s: s.record_attempt(1, "docling", 1.0),
    "tentativa com erro vazio": lambda s: s.record_attempt(1, "docling", 1.0, error="  "),
    "duracao negativa": lambda s: s.record_attempt(1, "docling", -1.0, error="x"),
    "duracao nan": lambda s: s.record_attempt(1, "docling", float("nan"), error="x"),
    "qualidade de outro tipo": lambda s: s.record_attempt(1, "docling", 1.0, quality=object()),
    "complete sem quality": lambda s: s.complete_job(1, "/x.md", "docling", None, "ok"),
    "complete path vazio": lambda s: s.complete_job(1, "", "docling", _GOOD_QUALITY, "ok"),
    "fail sem resumo": lambda s: s.fail_job(1, "", "x"),
    "tipo de evento fora da lista": lambda s: s.add_event(1, "inventado", "x"),
    "evento sem mensagem": lambda s: s.add_event(1, "job_created", 5),
    "sem filtro": lambda s: s.list_events(),
    "dois filtros": lambda s: s.list_events(project_id=1, document_id=1),
    "limit zero": lambda s: s.list_events(project_id=1, limit=0),
    "limit 501": lambda s: s.list_events(project_id=1, limit=501),
    "offset negativo": lambda s: s.list_events(project_id=1, offset=-1),
    "rename nome vazio": lambda s: s.rename_project(1, "  "),
    "rename nome nao texto": lambda s: s.rename_project(1, 7),
    "rename id invalido": lambda s: s.rename_project(0, "Novo"),
    "get_source id invalido": lambda s: s.get_source(-3),
    "list_sources id invalido": lambda s: s.list_sources(True),
    "get_document id invalido": lambda s: s.get_document(0),
    "get_job id invalido": lambda s: s.get_job("1"),
    "list_project_job_ids id invalido": lambda s: s.list_project_job_ids(None),
    "list_open_jobs id invalido": lambda s: s.list_open_jobs(0),
    "mark_job_published id invalido": lambda s: s.mark_job_published(False),
    "list_unpublished limit zero": lambda s: s.list_unpublished_jobs(limit=0),
    "list_unpublished limit bool": lambda s: s.list_unpublished_jobs(limit=True),
    "list_unpublished limit enorme": lambda s: s.list_unpublished_jobs(limit=repository.MAX_UNPUBLISHED_PAGE + 1),
}


@pytest.mark.parametrize("label", list(_INVALID_CALLS))
def test_invalid_input_is_refused_before_any_sql(label):
    session = StoreSession(_Boom())
    with pytest.raises(errors.StoreValidationError):
        _run(_INVALID_CALLS[label](session))


def test_limit_boundaries_are_accepted():
    for limit in (1, 500):
        session, db = _session(("fetch", []))
        assert _run(session.list_events(project_id=1, limit=limit)) == []
        assert db.calls[0][2][1] == limit


# --- Ramificações de regra -----------------------------------------------------------------------------


def test_create_project_stores_stripped_name_and_key_and_emits_event():
    session, db = _session(("fetchrow", _project_row(name="Revisão")), ("fetchrow", _event_row()))
    project = _run(session.create_project("  Revisão  "))
    assert project.name == "Revisão"
    assert db.calls[0][2] == ("Revisão", name_key("Revisão"))
    assert "ON CONFLICT (name_key) WHERE status = 'active' DO NOTHING" in db.calls[0][1]
    assert db.calls[1][2][3] == "project_created"


def test_create_project_conflict_raises_name_taken_without_event():
    session, db = _session(("fetchrow", None))
    with pytest.raises(errors.ProjectNameTakenError):
        _run(session.create_project("P"))
    assert len(db.calls) == 1


def test_get_project_not_found():
    session, _ = _session(("fetchrow", None))
    with pytest.raises(errors.ProjectNotFoundError):
        _run(session.get_project(1))


def test_archive_twice_and_unarchive_active_are_refused_symmetrically():
    session, db = _session(("fetchrow", _project_row("archived")))
    with pytest.raises(errors.StoreValidationError, match="já está arquivado"):
        _run(session.archive_project(1))
    assert len(db.calls) == 1  # nada foi alterado
    session, db = _session(("fetchrow", _project_row("active")))
    with pytest.raises(errors.StoreValidationError, match="já está ativo"):
        _run(session.unarchive_project(1))
    assert len(db.calls) == 1


def test_archive_cancels_jobs_and_emits_one_event_per_job_plus_project_event():
    session, db = _session(
        ("fetchrow", _project_row("active")),
        ("fetchrow", _project_row("archived")),
        ("fetch", [{"id": 8, "document_id": 2}, {"id": 5, "document_id": 3}]),
        ("fetchrow", _event_row("job_cancelled")),
        ("fetchrow", _event_row("job_cancelled")),
        ("fetchrow", _event_row("project_archived")),
    )
    outcome = _run(session.archive_project(1))
    assert outcome.project.status == "archived"
    assert outcome.cancelled_job_ids == (5, 8)  # em ordem de id
    types = [call[2][3] for call in db.calls[3:]]
    assert types == ["job_cancelled", "job_cancelled", "project_archived"]


def test_delete_project_cancels_open_jobs_then_deletes_without_events():
    session, db = _session(("fetchrow", _project_row()), ("fetch", []), ("execute", "DELETE 1"))
    assert _run(session.delete_project(1)) is None
    assert "DELETE FROM projects" in db.calls[-1][1]
    assert not any("history_events" in call[1] for call in db.calls)


def test_add_document_with_source_of_another_project_is_refused():
    session, _ = _session(("fetchval", None), ("fetchval", 1))
    with pytest.raises(errors.StoreValidationError, match="não pertence"):
        _run(session.add_document(1, 99, "/a.pdf"))
    session, _ = _session(("fetchval", None), ("fetchval", None))
    with pytest.raises(errors.ProjectNotFoundError):
        _run(session.add_document(1, 99, "/a.pdf"))


def test_add_document_repeat_returns_existing_with_original_source_and_no_event():
    existing = {"id": 3, "project_id": 1, "source_id": 7, "path": "/a.pdf", "created_at": NOW}
    session, db = _session(("fetchval", 1), ("fetchrow", None), ("fetchrow", existing))
    document, created = _run(session.add_document(1, 8, "/a.pdf"))
    assert created is False and document.source_id == 7
    assert len(db.calls) == 3  # nenhum INSERT de evento


def test_create_job_unknown_document_and_active_job():
    session, _ = _session(("fetchrow", None))
    with pytest.raises(errors.DocumentNotFoundError):
        _run(session.create_job(1, "auto"))
    session, _ = _session(("fetchrow", {"id": 3, "project_id": 1}), ("fetchrow", None))
    with pytest.raises(errors.ActiveJobExistsError):
        _run(session.create_job(3, "auto"))


def test_transition_distinguishes_missing_job_from_invalid_transition_and_changes_nothing():
    session, db = _session(("fetchrow", None), ("fetchval", None))
    with pytest.raises(errors.JobNotFoundError):
        _run(session.start_job(5))
    session, db = _session(("fetchrow", None), ("fetchval", "succeeded"))
    with pytest.raises(errors.InvalidJobTransitionError, match="succeeded"):
        _run(session.start_job(5))
    assert len(db.calls) == 2 and all(c[1].startswith(("UPDATE", "SELECT")) for c in db.calls)


def test_transitions_use_one_conditional_update_with_the_allowed_origin_states():
    session, db = _session(("fetchrow", _job_row("cancelled")), ("fetchrow", _event_row("job_cancelled")))
    _run(session.cancel_job(5, "cancelado"))
    method, sql, args = db.calls[0]
    assert "status = ANY($2::text[])" in sql and args[1] == ["pending", "running"]
    session, db = _session(("fetchrow", _job_row("failed")), ("fetchrow", _event_row("job_failed")))
    _run(session.fail_job(5, "motor falhou", "falhou"))
    assert db.calls[0][2] == (5, ["pending", "running"], "motor falhou")  # pending -> failed (0002)


def test_complete_job_writes_state_result_and_event_in_that_order():
    result_row = {
        "job_id": 5, "markdown_path": "/x.md", "engine": "docling", "score": 80.0, "accepted": True,
        "tables_found": 1, "tables_expected": 1, "chars_per_page": 1500.0, "issues": ["ok"], "created_at": NOW,
    }
    session, db = _session(
        ("fetchrow", _job_row("succeeded")), ("fetchrow", result_row), ("fetchrow", _event_row("job_succeeded"))
    )
    job, result, event = _run(session.complete_job(5, "/x.md", "docling", _GOOD_QUALITY, "concluído"))
    assert result.quality == _GOOD_QUALITY and result.warnings == ("ok",)
    assert [c[1].split()[0] for c in db.calls] == ["UPDATE", "INSERT", "INSERT"]
    assert "job_results" in db.calls[1][1] and "history_events" in db.calls[2][1]
    assert db.calls[1][2][-1] == ["ok"]  # issues gravadas como lista, na mesma ordem


def test_failure_after_state_change_propagates_so_the_transaction_rolls_back():
    boom = errors.StoreDatabaseError("falha simulada ao gravar o resultado")
    session, _ = _session(("fetchrow", _job_row("succeeded")), ("fetchrow", boom))
    with pytest.raises(errors.StoreDatabaseError):
        _run(session.complete_job(5, "/x.md", "docling", _GOOD_QUALITY, "ok"))


def test_record_attempt_requires_running_job_and_assigns_next_position():
    running = {"id": 5, "project_id": 1, "document_id": 3, "status": "running"}
    session, _ = _session(("fetchrow", None))
    with pytest.raises(errors.JobNotFoundError):
        _run(session.record_attempt(5, "docling", 1.0, error="x"))
    session, _ = _session(("fetchrow", {**running, "status": "succeeded"}))
    with pytest.raises(errors.InvalidJobTransitionError):
        _run(session.record_attempt(5, "docling", 1.0, error="x"))
    attempt_row = {
        "id": 1, "job_id": 5, "position": 3, "engine": "docling", "seconds": 1.0, "score": None, "accepted": None,
        "tables_found": None, "tables_expected": None, "chars_per_page": None, "issues": None, "error": "x",
        "created_at": NOW,
    }
    session, db = _session(
        ("fetchrow", running), ("fetchval", 3), ("fetchrow", attempt_row), ("fetchrow", _event_row("attempt_recorded"))
    )
    attempt = _run(session.record_attempt(5, "docling", 1.0, error="x"))
    assert attempt.position == 3 and attempt.quality is None and attempt.error == "x"
    assert "FOR UPDATE" in db.calls[0][1]  # serializa tentativas concorrentes no mesmo job


def test_get_result_and_list_attempts_distinguish_unknown_job_from_empty():
    session, _ = _session(("fetchrow", None))
    with pytest.raises(errors.JobNotFoundError):
        _run(session.get_result(5))
    row = {k: None for k in ("job_id", "markdown_path", "engine", "score", "accepted", "tables_found",
                             "tables_expected", "chars_per_page", "issues", "created_at")}
    session, _ = _session(("fetchrow", {"existing_job_id": 5, **row}))
    assert _run(session.get_result(5)) is None
    session, _ = _session(("fetchval", None))
    with pytest.raises(errors.JobNotFoundError):
        _run(session.list_attempts(5))
    session, _ = _session(("fetchval", 1), ("fetch", []))
    assert _run(session.list_attempts(5)) == []


def test_list_documents_is_one_query_and_derives_status_and_last_result():
    base = {"document_id": 1, "project_id": 1, "source_id": 1, "path": "/a.pdf"}
    none_job = {"latest_job_id": None, "latest_status": None, "latest_requested_at": None}
    no_result = {k: None for k in ("result_job_id", "result_markdown_path", "result_engine", "result_score",
                                   "result_accepted", "result_created_at")}
    with_result = {"result_job_id": 4, "result_markdown_path": "/a.md", "result_engine": "docling",
                   "result_score": 70.0, "result_accepted": False, "result_created_at": NOW}
    rows = [
        {**base, **none_job, **no_result},
        {**base, "document_id": 2, "latest_job_id": 6, "latest_status": "cancelled", "latest_requested_at": NOW,
         **with_result},
    ]
    session, db = _session(("fetch", rows))
    statuses = _run(session.list_documents(1))
    assert len(db.calls) == 1
    assert statuses[0].status == "not_processed" and statuses[0].last_result is None
    assert statuses[1].status == "cancelled" and statuses[1].last_result.job_id == 4  # status ≠ origem do resultado
    assert statuses[1].last_result.accepted is False
    session, _ = _session(("fetch", []), ("fetchval", None))
    with pytest.raises(errors.ProjectNotFoundError):
        _run(session.list_documents(1))
    session, _ = _session(("fetch", []), ("fetchval", 1))
    assert _run(session.list_documents(1)) == []


def test_add_event_checks_project_document_and_job_ownership():
    session, _ = _session(("fetchval", None))
    with pytest.raises(errors.ProjectNotFoundError):
        _run(session.add_event(1, "job_created", "x"))
    session, _ = _session(("fetchval", 1), ("fetchval", None))
    with pytest.raises(errors.StoreValidationError, match="documento"):
        _run(session.add_event(1, "job_created", "x", document_id=2))
    session, _ = _session(("fetchval", 1), ("fetchrow", None))
    with pytest.raises(errors.StoreValidationError, match="job"):
        _run(session.add_event(1, "job_created", "x", job_id=2))
    session, _ = _session(("fetchval", 1), ("fetchval", 1), ("fetchrow", {"document_id": 9}))
    with pytest.raises(errors.StoreValidationError, match="não pertence ao documento"):
        _run(session.add_event(1, "job_created", "x", document_id=2, job_id=2))
    session, db = _session(("fetchval", 1), ("fetchrow", _event_row("attempt_started")))
    assert _run(session.add_event(1, "attempt_started", "x")).event_type == "attempt_started"
    assert "FOR SHARE" in db.calls[0][1]


# --- Tradução de erros do driver ------------------------------------------------------------------------

_SERVER_TEXT = "DETAIL: Key (name_key)=(segredo-do-usuario) already exists; password=hunter2"


def _pg(cls, constraint=None):
    exc = cls(_SERVER_TEXT)
    if constraint:
        exc.constraint_name = constraint
    return exc


@pytest.mark.parametrize(
    "exc, expected",
    [
        (_pg(asyncpg.UniqueViolationError, "projects_active_name_key"), errors.ProjectNameTakenError),
        (_pg(asyncpg.UniqueViolationError, "jobs_one_active_per_document"), errors.ActiveJobExistsError),
        (_pg(asyncpg.UniqueViolationError, "outra"), errors.StoreDatabaseError),
        (_pg(asyncpg.ForeignKeyViolationError), errors.StoreValidationError),
        (_pg(asyncpg.CheckViolationError), errors.StoreValidationError),
        (_pg(asyncpg.DataError), errors.StoreValidationError),
        (_pg(asyncpg.CharacterNotInRepertoireError), errors.StoreValidationError),
        (_pg(asyncpg.PostgresConnectionError), errors.DatabaseUnavailableError),
        (_pg(asyncpg.AdminShutdownError), errors.DatabaseUnavailableError),
        (_pg(asyncpg.QueryCanceledError), errors.DatabaseUnavailableError),
        (asyncpg.InterfaceError("connection is closed"), errors.DatabaseUnavailableError),
        (ConnectionRefusedError("host=10.1.1.1"), errors.DatabaseUnavailableError),
        (asyncio.TimeoutError(), errors.DatabaseUnavailableError),
        (_pg(asyncpg.PostgresError), errors.StoreDatabaseError),
    ],
)
def test_driver_errors_are_translated_without_server_text(exc, expected):
    translated = _translate(exc, command_timeout=30.0)
    assert type(translated) is expected
    text = str(translated)
    for leaked in ("segredo-do-usuario", "hunter2", "10.1.1.1", "DETAIL"):
        assert leaked not in text


def test_foreign_key_violation_uses_the_operation_specific_error_when_given():
    custom = errors.ProjectNotFoundError("Projeto não encontrado.")
    assert _translate(_pg(asyncpg.ForeignKeyViolationError), fk_error=custom) is custom


def test_non_driver_errors_are_not_translated():
    assert _translate(ValueError("x")) is None
    assert _translate(errors.StoreValidationError("já traduzido")) is None


def test_executor_raises_translated_error_from_none_and_counts_queries():
    class _Conn:
        async def fetchrow(self, sql, *args):
            raise _pg(asyncpg.UniqueViolationError, "projects_active_name_key")

    store = Store(pool=None, acquire_timeout=1.0, command_timeout=30.0)
    executor = repository._Executor(_Conn(), store)
    with pytest.raises(errors.ProjectNameTakenError) as caught:
        _run(executor.fetchrow("SELECT 1"))
    assert caught.value.__cause__ is None and caught.value.__suppress_context__
    assert _SERVER_TEXT not in str(caught.value)
    assert store.query_count == 1
    store.reset_query_count()
    assert store.query_count == 0


# --- Ciclo de vida do Store ----------------------------------------------------------------------------

_SETTINGS = DbSettings(host="127.0.0.1", port=5432, user="u", database="d", password="senha-super-secreta")


def test_open_failure_is_database_unavailable_without_the_password(monkeypatch):
    async def _refuse(*_a, **kwargs):
        raise OSError(f"falhou com password={kwargs.get('password')}")

    monkeypatch.setattr(asyncpg, "create_pool", _refuse)
    with pytest.raises(errors.DatabaseUnavailableError) as caught:
        _run(Store.open(_SETTINGS))
    assert "senha-super-secreta" not in str(caught.value)
    assert "127.0.0.1:5432" in str(caught.value)
    assert caught.value.__cause__ is None and caught.value.__suppress_context__


def test_open_passes_named_arguments_and_the_validated_host_never_a_dsn(monkeypatch):
    seen = {}

    async def _fake_pool(*args, **kwargs):
        seen["args"], seen["kwargs"] = args, kwargs
        return object()

    monkeypatch.setattr(asyncpg, "create_pool", _fake_pool)
    store = _run(Store.open(_SETTINGS, min_size=2, max_size=3))
    assert seen["args"] == ()  # nenhum DSN posicional
    assert seen["kwargs"]["host"] == "127.0.0.1" and seen["kwargs"]["min_size"] == 2
    assert seen["kwargs"]["timeout"] == 10.0 and seen["kwargs"]["command_timeout"] == 30.0
    assert "dsn" not in seen["kwargs"]
    assert isinstance(store, Store)


def test_open_refuses_remote_host_before_any_pool_is_created(monkeypatch):
    async def _boom(*_a, **_k):
        raise AssertionError("tentou criar o pool")

    monkeypatch.setattr(asyncpg, "create_pool", _boom)
    remote = DbSettings(host="db.exemplo.com", port=5432, user="u", database="d", password="x")
    with pytest.raises(errors.StoreConfigError):
        _run(Store.open(remote))


def test_closed_store_refuses_transactions_and_close_is_idempotent():
    store = Store(pool=None, acquire_timeout=1.0, command_timeout=30.0)

    async def scenario():
        await store.close()
        await store.close()
        async with store.transaction():
            pass

    with pytest.raises(errors.DatabaseUnavailableError):
        _run(scenario())


def test_every_public_operation_is_exposed_on_store_and_session():
    for name in repository._OPERATIONS:
        assert callable(getattr(Store, name)) and callable(getattr(StoreSession, name))
    forbidden = ("update_event", "delete_event", "update_result", "delete_result", "update_attempt",
                 "delete_attempt", "remove_event")
    assert not [n for n in dir(StoreSession) if n in forbidden]
    assert not [n for n in dir(Store) if n in forbidden]


# --- Inspeção estática (FR-002, FR-015, FR-016) -----------------------------------------------------------

_TREE = ast.parse(SOURCE_FILE.read_text(encoding="utf-8"))


def test_every_sql_string_is_constant_or_uses_only_fixed_fragments():
    """4.1.4: nenhum valor do usuário é concatenado em SQL; só `_transition` monta SQL, com constantes."""
    checked = 0
    for node in ast.walk(_TREE):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr not in {"fetch", "fetchrow", "fetchval", "execute"}:
            continue
        if not (isinstance(node.func.value, ast.Attribute) and node.func.value.attr == "_db"):
            continue
        sql = node.args[0]
        checked += 1
        if isinstance(sql, ast.Constant):
            continue
        assert isinstance(sql, ast.JoinedStr), ast.dump(sql)
        names = {v.value.id for v in sql.values if isinstance(v, ast.FormattedValue)}
        assert names == {"new_status", "assignments"}, names  # ambos derivados de _TRANSITIONS
    assert checked >= 20


def test_status_fragment_in_transition_comes_only_from_the_fixed_table():
    assert set(repository._TRANSITIONS) == {"running", "succeeded", "failed", "cancelled"}
    for _allowed, assignments in repository._TRANSITIONS.values():
        assert "'" not in assignments and ";" not in assignments


def test_repository_never_touches_the_filesystem():
    """5.4.5: nenhum import de os/pathlib/shutil/io/tempfile/glob e nenhuma chamada de `open`."""
    imported: set[str] = set()
    for node in ast.walk(_TREE):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            imported.add(node.module.split(".")[0])
    assert not imported & {"os", "pathlib", "shutil", "io", "tempfile", "glob", "subprocess"}
    calls = {n.func.id for n in ast.walk(_TREE) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert "open" not in calls


def test_no_exception_in_the_package_is_raised_with_raw_driver_text():
    """Todo `raise` dentro de um `except` do repositório e do migrador usa `from None`."""
    for module in ("repository", "migrator"):
        tree = ast.parse((SOURCE_FILE.parent / f"{module}.py").read_text(encoding="utf-8"))
        for handler in (n for n in ast.walk(tree) if isinstance(n, ast.ExceptHandler)):
            for node in ast.walk(handler):
                if isinstance(node, ast.Raise) and node.exc is not None:
                    assert node.cause is not None or isinstance(node.exc, ast.Name), (
                        f"{module}:{node.lineno}: raise dentro de except sem `from None`"
                    )


# --- Operações da project-api (migração 0002) -------------------------------------------------------------


def test_rename_project_locks_trims_updates_and_emits_the_event():
    session, db = _session(
        ("fetchrow", _project_row()), ("fetchrow", _project_row(name="Novo nome")),
        ("fetchrow", _event_row("project_renamed")),
    )
    project = _run(session.rename_project(1, "  Novo nome  "))
    assert project.name == "Novo nome"
    assert "FOR UPDATE" in db.calls[0][1]
    assert db.calls[1][2] == (1, "Novo nome", name_key("Novo nome"))
    assert db.calls[2][2][3:5] == ("project_renamed", "Projeto renomeado.")


def test_rename_project_unknown_project_is_not_found_and_emits_nothing():
    session, db = _session(("fetchrow", None))
    with pytest.raises(errors.ProjectNotFoundError):
        _run(session.rename_project(1, "Novo"))
    assert len(db.calls) == 1


def test_get_source_not_found_uses_the_base_error_with_the_contract_message():
    session, _ = _session(("fetchrow", None))
    with pytest.raises(errors.StoreNotFoundError) as caught:
        _run(session.get_source(4))
    assert type(caught.value) is errors.StoreNotFoundError and str(caught.value) == "Fonte não encontrada."


def test_empty_lists_check_whether_the_project_exists():
    for method in ("list_sources", "list_project_job_ids"):
        session, db = _session(("fetch", []), ("fetchval", None))
        with pytest.raises(errors.ProjectNotFoundError):
            _run(getattr(session, method)(1))
        session, db = _session(("fetch", []), ("fetchval", 1))
        assert _run(getattr(session, method)(1)) == []
        assert "FROM projects" in db.calls[1][1]


def test_mark_job_published_updates_only_when_null_and_otherwise_reads_the_job():
    session, db = _session(("fetchrow", _job_row()))
    _run(session.mark_job_published(5))
    assert "published_at IS NULL" in db.calls[0][1] and len(db.calls) == 1
    session, db = _session(("fetchrow", None), ("fetchrow", _job_row("cancelled")))
    assert _run(session.mark_job_published(5)).status == "cancelled"
    session, _ = _session(("fetchrow", None), ("fetchrow", None))
    with pytest.raises(errors.JobNotFoundError):
        _run(session.mark_job_published(5))


def test_list_unpublished_jobs_filters_pending_without_published_at_ordered_by_id():
    session, db = _session(("fetch", [_job_row()]))
    jobs = _run(session.list_unpublished_jobs())
    assert [j.id for j in jobs] == [5] and db.calls[0][2] == (1000,)
    assert "status = 'pending'" in db.calls[0][1] and "published_at IS NULL" in db.calls[0][1]
    assert "ORDER BY id" in db.calls[0][1]


def test_fail_job_accepts_pending_and_running_origins():
    assert repository._TRANSITIONS["failed"][0] == ("pending", "running")


def test_job_exposes_published_at():
    session, _ = _session(("fetchrow", {**_job_row(), "published_at": NOW}))
    assert _run(session.mark_job_published(5)).published_at == NOW
