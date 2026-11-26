"""Tratamento dos eventos da fila (`api/consumer.py`, tarefa 7.4; FR-013 a FR-017, FR-025, S-02)."""
from __future__ import annotations

import asyncio
import copy
import logging
from pathlib import Path

import pytest

from artigo2md.api.consumer import MAX_TEXT_CHARS, OUTSIDE_DATA_DIR_SUMMARY, EventHandler, TransientStoreError
from artigo2md.api.deps import Services
from artigo2md.api.settings import ApiSettings
from artigo2md.jobqueue import schema
from artigo2md.store import errors as se
from fake_store import FakeStore
from queue_support import report

HOST = "/host/data"


def run(coro):
    return asyncio.run(coro)


class World:
    def __init__(self, tmp_path: Path):
        env = {"ARTIGO2MD_DATA_DIR_HOST": HOST, "ARTIGO2MD_DATA_DIR_MOUNT": str(tmp_path)}
        self.settings = ApiSettings.from_env(env, Path("/nonexistent-cwd-for-tests"))
        self.store = FakeStore()
        self.services = Services(self.settings, store=self.store)
        self.handler = EventHandler(self.services)

    def handle(self, event):
        body = event if isinstance(event, (bytes, bytearray)) else schema.encode(event)
        return run(self.handler.handle(body))

    def make_job(self, *, start=False, name="P", path="/Users/a/x.pdf"):
        async def go():
            project = await self.store.create_project(name)
            source = await self.store.add_source(project.id, "folder", "in_place", "/Users/a")
            document, _ = await self.store.add_document(project.id, source.id, path)
            job = await self.store.create_job(document.id, "auto")
            if start:
                job = await self.store.start_job(job.id)
            return job

        return run(go())

    def job(self, job_id):
        return run(self.store.get_job(job_id))

    def events(self, project_id):
        return run(self.store.list_events(project_id=project_id, limit=500))


@pytest.fixture()
def world(tmp_path):
    return World(tmp_path)


def result_event(job, *, path=f"{HOST}/1/a.md", attempts=None, engine="docling"):
    data = copy.deepcopy(report())
    if attempts is not None:
        data["attempts"] = attempts
    data["engine"] = engine
    return schema.build_result(str(job.id), 1, path, engine, data)


QUALITY = {"score": 80.0, "accepted": True, "tables_found": 1, "tables_expected": 1, "chars_per_page": 450.0,
           "issues": ["aviso"]}
LOW = {**QUALITY, "score": 30.0, "accepted": False, "issues": ["ruim"]}


# ── progress ────────────────────────────────────────────────────────────────────
def test_progress_starts_a_pending_job_and_is_idempotent_afterwards(world):
    job = world.make_job()
    first = world.handle(schema.build_progress(str(job.id), 1, "Convertendo"))
    assert first.applied and first.job_started is True
    assert world.job(job.id).status == "running"
    before = len(world.events(job.project_id))
    second = world.handle(schema.build_progress(str(job.id), 1, "Convertendo de novo"))
    assert second.applied and second.job_started is False
    assert len(world.events(job.project_id)) == before  # nada novo no histórico


# ── result ──────────────────────────────────────────────────────────────────────
def test_result_records_attempts_in_order_and_completes_with_the_winner_quality(world):
    job = world.make_job(start=True)
    attempts = [
        {"engine": "docling", "seconds": 1.5, "quality": LOW, "error": None},
        {"engine": "pymupdf", "seconds": 0.5, "quality": None, "error": "falhou"},
        {"engine": "markitdown", "seconds": 2.0, "quality": QUALITY, "error": None},
    ]
    outcome = world.handle(result_event(job, attempts=attempts, engine="markitdown"))
    assert outcome.applied
    assert world.job(job.id).status == "succeeded"
    stored = run(world.store.list_attempts(job.id))
    assert [(a.position, a.engine) for a in stored] == [(1, "docling"), (2, "pymupdf"), (3, "markitdown")]
    result = run(world.store.get_result(job.id))
    assert result.engine == "markitdown" and result.quality.score == 80.0 and result.markdown_path == f"{HOST}/1/a.md"


def test_result_on_a_pending_job_starts_it_in_the_same_transaction(world):
    job = world.make_job()
    assert world.handle(result_event(job)).applied
    final = world.job(job.id)
    assert final.status == "succeeded" and final.started_at is not None


def test_duplicate_result_is_discarded_without_error_and_changes_nothing(world, caplog):
    job = world.make_job(start=True)
    assert world.handle(result_event(job)).applied
    attempts = len(run(world.store.list_attempts(job.id)))
    with caplog.at_level(logging.INFO, logger="artigo2md.api"):
        again = world.handle(result_event(job))
    assert again.action == "discarded" and again.reason == "job-final"
    assert len(run(world.store.list_attempts(job.id))) == attempts
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]  # descarte não é falha da API


@pytest.mark.parametrize(
    "path", [f"{HOST}/../etc/passwd", "/etc/passwd", f"{HOST}-outro/1/a.md", "relativo/a.md", f"{HOST}/1/\0.md"]
)
def test_result_with_markdown_path_outside_the_data_dir_fails_the_job_without_storing_the_path(world, path, caplog):
    job = world.make_job(start=True)
    with caplog.at_level(logging.DEBUG, logger="artigo2md.api"):
        outcome = world.handle(result_event(job, path=path))
    assert outcome.applied and outcome.reason == "resultado-recusado"
    final = world.job(job.id)
    assert final.status == "failed"
    assert final.error_summary == OUTSIDE_DATA_DIR_SUMMARY  # diagnóstico do descompasso (2.3.1), texto fixo
    assert "--data-dir" in final.error_summary and "ARTIGO2MD_DATA_DIR" in final.error_summary
    assert "--data-dir do worker" in caplog.text and f"Job {job.id}" in caplog.text
    assert run(world.store.get_result(job.id)) is None
    assert run(world.store.list_attempts(job.id)) == []
    for text in [final.error_summary, caplog.text, *[e.message for e in world.events(job.project_id)]]:
        assert "passwd" not in text and "relativo" not in text


def test_result_without_data_dir_configuration_fails_the_job_with_a_clear_reason(tmp_path):
    settings = ApiSettings.from_env({}, Path("/nonexistent-cwd-for-tests"))
    store = FakeStore()
    handler = EventHandler(Services(settings, store=store))

    async def go():
        project = await store.create_project("P")
        source = await store.add_source(project.id, "folder", "in_place", "/Users/a")
        document, _ = await store.add_document(project.id, source.id, "/Users/a/x.pdf")
        job = await store.start_job((await store.create_job(document.id, "auto")).id)
        await handler.handle(schema.encode(result_event(job)))
        return await store.get_job(job.id)

    final = run(go())
    assert final.status == "failed" and "ARTIGO2MD_DATA_DIR" in final.error_summary
    assert "não está configurada" in final.error_summary  # outra causa, outro texto: não é o descompasso


def test_result_without_quality_for_the_winning_engine_is_discarded_and_leaves_the_job_running(world):
    job = world.make_job(start=True)
    attempts = [{"engine": "docling", "seconds": 1.0, "quality": None, "error": "falhou"}]
    outcome = world.handle(result_event(job, attempts=attempts))
    assert outcome.reason == "resultado-sem-qualidade"
    assert world.job(job.id).status == "running"
    assert run(world.store.list_attempts(job.id)) == []


def test_result_with_out_of_domain_values_rolls_back_everything(world):
    job = world.make_job()  # pending: o start_job e as tentativas gravadas antes do erro também voltam
    bad = {**QUALITY, "score": 1000.0}
    attempts = [
        {"engine": "pymupdf", "seconds": 1.0, "quality": None, "error": "falhou"},
        {"engine": "docling", "seconds": 1.0, "quality": bad, "error": None},
    ]
    outcome = world.handle(result_event(job, attempts=attempts))
    assert outcome.action == "discarded" and outcome.reason == "fora-do-contrato"
    assert world.job(job.id).status == "pending"
    assert run(world.store.list_attempts(job.id)) == []


# ── error ───────────────────────────────────────────────────────────────────────
def test_transient_error_keeps_the_job_running_and_writes_no_history(world):
    job = world.make_job(start=True)
    before = len(world.events(job.project_id))
    event = schema.build_error(str(job.id), 1, "conversion_failed", "Motor falhou", True, 3)
    outcome = world.handle(event)
    assert outcome.applied and outcome.reason == "error-passageiro"
    assert world.job(job.id).status == "running"
    assert len(world.events(job.project_id)) == before


def test_definitive_error_fails_the_job_with_the_reason_as_summary(world):
    job = world.make_job()
    event = schema.build_error(str(job.id), 3, "invalid_pdf", "O PDF está protegido por senha.", False, 3)
    outcome = world.handle(event)
    assert outcome.applied and outcome.reason == "error-definitivo"
    final = world.job(job.id)
    assert final.status == "failed" and final.error_summary == "O PDF está protegido por senha."
    assert any("Conversão falhou" in e.message for e in world.events(job.project_id))


def test_free_text_is_truncated_to_2000_characters(world):
    job = world.make_job(start=True)
    event = schema.build_error(str(job.id), 3, "invalid_pdf", "x" * 5000, False, 3)
    world.handle(event)
    assert len(world.job(job.id).error_summary) == MAX_TEXT_CHARS


# ── descartes ───────────────────────────────────────────────────────────────────
def test_unknown_fields_in_a_valid_event_are_ignored(world):
    job = world.make_job()
    event = schema.build_progress(str(job.id), 1, "ok")
    event["campo_novo"] = {"qualquer": 1}
    assert world.handle(event).applied
    assert world.job(job.id).status == "running"


@pytest.mark.parametrize(
    "body, reason",
    [
        (b"nao e json", "fora-do-contrato"),
        (b"[]", "fora-do-contrato"),
        (b'{"type": "job"}', "fora-do-contrato"),
        (b"x" * (schema.MAX_BODY_BYTES + 1), "corpo-grande"),
        (b'{"a":' * (schema.MAX_JSON_DEPTH + 2) + b"1" + b"}" * (schema.MAX_JSON_DEPTH + 2), "json-profundo"),
    ],
)
def test_malformed_bodies_are_discarded_without_logging_the_body(world, body, reason, caplog):
    with caplog.at_level(logging.DEBUG, logger="artigo2md.api"):
        outcome = world.handle(body)
    assert outcome.action == "discarded" and outcome.reason == reason
    assert "nao e json" not in caplog.text and "xxxx" not in caplog.text
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


def test_unsupported_schema_version_is_discarded(world):
    job = world.make_job()
    event = schema.build_progress(str(job.id), 1, "ok")
    event["schema_version"] = 2
    assert world.handle(event).reason == "versao-nao-suportada"
    assert world.job(job.id).status == "pending"


@pytest.mark.parametrize("job_id", ["999", "0", "007", "1.5", "abc", "-1", "٣", "9" * 30])
def test_event_for_a_nonexistent_or_invalid_job_id_is_discarded(world, job_id):
    outcome = world.handle(schema.build_progress(job_id, 1, "ok"))
    assert outcome.action == "discarded" and outcome.reason in ("job-inexistente", "fora-do-contrato")


def test_events_of_final_jobs_are_discarded_and_change_nothing(world):
    job = world.make_job(start=True)
    run(world.store.cancel_job(job.id, "Cancelado."))
    before = len(world.events(job.project_id))
    for event in (
        schema.build_progress(str(job.id), 1, "tarde"),
        result_event(job),
        schema.build_error(str(job.id), 1, "invalid_pdf", "tarde", False, 3),
    ):
        assert world.handle(event).reason == "job-final"
    assert world.job(job.id).status == "cancelled"
    assert len(world.events(job.project_id)) == before


@pytest.mark.parametrize("operation", ["start_job", "fail_job"])
def test_job_that_became_final_during_handling_is_discarded_as_repeated_notification(world, caplog, operation):
    """FR-016: cancelamento entre `_open` e a transição do job = notificação repetida, descarte informativo sem erro."""
    job = world.make_job()
    world.store.inject_failure(operation, se.InvalidJobTransitionError("O job já está final."))
    event = (
        schema.build_progress(str(job.id), 1, "ok")
        if operation == "start_job"
        else schema.build_error(str(job.id), 1, "invalid_pdf", "falhou", False, 3)
    )
    with caplog.at_level(logging.INFO, logger="artigo2md.api"):
        outcome = world.handle(event)
    assert outcome.action == "discarded" and outcome.reason == "job-final"
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert world.job(job.id).status == "pending"  # a transação foi desfeita por inteiro


def test_event_of_a_deleted_project_is_discarded(world):
    job = world.make_job()
    run(world.store.delete_project(job.project_id))
    assert world.handle(schema.build_progress(str(job.id), 1, "ok")).reason == "job-inexistente"


# ── banco fora ──────────────────────────────────────────────────────────────────
def test_database_down_raises_transient_error_and_the_same_event_works_after_recovery(world):
    job = world.make_job()
    world.store.inject_failure("get_job", se.DatabaseUnavailableError("fora"), times=2)
    event = schema.build_progress(str(job.id), 1, "ok")
    for _ in range(2):
        with pytest.raises(TransientStoreError):
            world.handle(event)
    assert world.job(job.id).status == "pending"
    assert world.handle(event).applied
    assert world.job(job.id).status == "running"


def test_unexpected_failure_is_logged_without_details_and_discarded(world, caplog):
    job = world.make_job()
    world.store.inject_failure("get_job", RuntimeError("senha=segredo"), times=1)
    with caplog.at_level(logging.DEBUG, logger="artigo2md.api"):
        outcome = world.handle(schema.build_progress(str(job.id), 1, "ok"))
    assert outcome.reason == "erro-inesperado"
    assert "segredo" not in caplog.text
