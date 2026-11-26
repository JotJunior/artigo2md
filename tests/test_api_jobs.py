"""Rotas de enfileiramento e reprocessamento (`api/routes/jobs.py`, tarefa 7.2; FR-010 a FR-012, FR-026, US2, US5)."""
from __future__ import annotations

import asyncio
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from artigo2md.api.app import create_app
from artigo2md.api.broker import BrokerPublisher
from artigo2md.api.settings import ApiSettings
from artigo2md.jobqueue import schema, topology
from fake_amqp import FakeBroker
from fake_store import FakeStore

JOB_FIELDS = {
    "id", "project_id", "document_id", "requested_engine", "status", "requested_at", "started_at", "finished_at",
    "error_summary",
}
MESSAGE_FIELDS = {"schema_version", "type", "job_id", "pdf_path", "engine", "attempt"}


def wait_for(predicate, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture()
def store():
    return FakeStore()


@pytest.fixture()
def broker():
    return FakeBroker()


@pytest.fixture()
def publisher(broker):
    instance = BrokerPublisher(
        SimpleNamespace(), connect=lambda settings: broker.connection(), backoff_start=0.01, backoff_max=0.05,
        poll=0.02, deadline=5.0,
    )
    instance.start()
    assert wait_for(lambda: instance.connected)
    yield instance
    instance.stop()


def settings_for():
    return ApiSettings.from_env({}, Path("/nonexistent-cwd-for-tests"))


@pytest.fixture()
def client(store, publisher):
    with TestClient(create_app(settings_for(), store=store, broker=publisher), base_url="http://127.0.0.1") as c:
        yield c


def code(response):
    return response.json()["error"]["code"]


def new_project_with_documents(client, count=1, name="Estudos"):
    project_id = client.post("/v1/projects", json={"name": name}).json()["id"]
    paths = [f"/Users/a/pdfs/doc-{index:04d}.pdf" for index in range(count)]
    body = {"kind": "folder", "mode": "in_place", "path": "/Users/a/pdfs", "documents": paths}
    created = client.post(f"/v1/projects/{project_id}/sources", json=body).json()["documents"]["created"]
    return project_id, [item["id"] for item in created], paths


def queued_messages(broker):
    return [schema.parse_job(item.body) for item in broker.peek(topology.QUEUE_JOBS)]


# ── POST /v1/documents/{id}/jobs ────────────────────────────────────────────────
def test_enqueue_document_creates_job_and_publishes_exact_message(client, broker, store):
    _, ids, paths = new_project_with_documents(client)
    response = client.post(f"/v1/documents/{ids[0]}/jobs", json={})
    assert response.status_code == 202
    job = response.json()
    assert set(job) == JOB_FIELDS
    assert (job["status"], job["requested_engine"], job["document_id"]) == ("pending", "auto", ids[0])
    queued = broker.peek(topology.QUEUE_JOBS)
    assert len(queued) == 1
    # corpo exatamente igual ao do contrato, sem nenhum campo a mais
    assert queued[0].body == schema.encode(schema.build_job(str(job["id"]), paths[0], "auto", 1))
    import json

    assert set(json.loads(queued[0].body)) == MESSAGE_FIELDS
    published = asyncio.run(store.get_job(job["id"]))
    assert published.published_at is not None  # confirmada -> marcada


def test_enqueue_document_accepts_a_missing_body_and_defaults_to_auto(client, broker):
    _, ids, _ = new_project_with_documents(client)
    response = client.post(f"/v1/documents/{ids[0]}/jobs")
    assert response.status_code == 202
    assert queued_messages(broker)[0].engine == "auto"


def test_enqueue_document_with_explicit_engine(client, broker):
    _, ids, _ = new_project_with_documents(client)
    response = client.post(f"/v1/documents/{ids[0]}/jobs", json={"engine": "docling"})
    assert response.status_code == 202
    assert response.json()["requested_engine"] == "docling"
    assert queued_messages(broker)[0].engine == "docling"


def test_invalid_engine_is_refused_with_the_valid_list_and_creates_nothing(client, broker, store):
    _, ids, _ = new_project_with_documents(client)
    response = client.post(f"/v1/documents/{ids[0]}/jobs", json={"engine": "inexistente"})
    assert response.status_code == 422 and code(response) == "validation_failed"
    for engine in ("auto", "docling", "pymupdf", "mineru", "markitdown"):
        assert engine in response.json()["error"]["message"]
    assert broker.depth(topology.QUEUE_JOBS) == 0
    assert asyncio.run(store.list_unpublished_jobs()) == []
    assert client.get(f"/v1/documents/{ids[0]}").json()["latest_job_id"] is None


def test_enqueue_unknown_document_is_404_and_bad_body_is_400(client):
    assert client.post("/v1/documents/9999/jobs", json={}).status_code == 404
    _, ids, _ = new_project_with_documents(client)
    assert client.post(f"/v1/documents/{ids[0]}/jobs", json={"engine": 5}).status_code == 400
    assert client.post(f"/v1/documents/{ids[0]}/jobs", json={"extra": 1}).status_code == 400


def test_second_request_for_an_active_document_is_409_and_publishes_once(client, broker):
    _, ids, _ = new_project_with_documents(client)
    assert client.post(f"/v1/documents/{ids[0]}/jobs", json={}).status_code == 202
    second = client.post(f"/v1/documents/{ids[0]}/jobs", json={})
    assert second.status_code == 409 and code(second) == "active_job_exists"
    assert broker.depth(topology.QUEUE_JOBS) == 1


def test_reprocessing_with_another_engine_creates_a_new_job_with_attempt_one(client, broker, store):
    _, ids, _ = new_project_with_documents(client)
    first = client.post(f"/v1/documents/{ids[0]}/jobs", json={}).json()

    async def finish():
        async with store.transaction() as session:
            await session.start_job(first["id"])
            await session.fail_job(first["id"], "Falhou.", "Falhou.")

    asyncio.run(finish())
    second = client.post(f"/v1/documents/{ids[0]}/jobs", json={"engine": "mineru"})
    assert second.status_code == 202
    assert second.json()["id"] != first["id"]
    messages = queued_messages(broker)
    assert [(m.job_id, m.engine, m.attempt) for m in messages] == [
        (str(first["id"]), "auto", 1), (str(second.json()["id"]), "mineru", 1),
    ]
    assert asyncio.run(store.get_job(first["id"])).status == "failed"  # o anterior fica preservado


# ── broker fora ─────────────────────────────────────────────────────────────────
def test_broker_down_returns_503_with_the_failed_job_and_allows_a_new_attempt(store, broker, publisher):
    broker.set_down()
    assert wait_for(lambda: not publisher.connected)
    with TestClient(create_app(settings_for(), store=store, broker=publisher), base_url="http://127.0.0.1") as client:
        _, ids, _ = new_project_with_documents(client)
        response = client.post(f"/v1/documents/{ids[0]}/jobs", json={})
        assert response.status_code == 503
        body = response.json()
        assert body["error"]["code"] == "broker_unavailable"
        assert set(body["job"]) == JOB_FIELDS
        assert body["job"]["status"] == "failed" and body["job"]["error_summary"]
        assert "fila" in body["job"]["error_summary"]
        assert asyncio.run(store.list_unpublished_jobs()) == []  # nenhum pending sem mensagem
        broker.set_up()
        assert wait_for(lambda: publisher.connected)
        again = client.post(f"/v1/documents/{ids[0]}/jobs", json={})
        assert again.status_code == 202  # o documento pode ser enfileirado de novo
        assert broker.depth(topology.QUEUE_JOBS) == 1


def test_missing_publisher_behaves_like_a_disconnected_one(store):
    with TestClient(create_app(settings_for(), store=store), base_url="http://127.0.0.1") as client:
        _, ids, _ = new_project_with_documents(client)
        response = client.post(f"/v1/documents/{ids[0]}/jobs", json={})
        assert response.status_code == 503 and response.json()["job"]["status"] == "failed"


def test_nack_from_the_broker_fails_the_job(client, broker):
    _, ids, _ = new_project_with_documents(client)
    broker.nack_next_publishes = 1
    response = client.post(f"/v1/documents/{ids[0]}/jobs", json={})
    assert response.status_code == 503
    assert "recusou" in response.json()["job"]["error_summary"]
    assert broker.depth(topology.QUEUE_JOBS) == 0


# ── concorrência ────────────────────────────────────────────────────────────────
def test_two_concurrent_requests_for_the_same_document_create_exactly_one_job(store, broker, publisher):
    application = create_app(settings_for(), store=store, broker=publisher)

    async def go():
        transport = httpx.ASGITransport(app=application)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as http:
            project_id = (await http.post("/v1/projects", json={"name": "P"})).json()["id"]
            body = {"kind": "folder", "mode": "in_place", "path": "/Users/a", "documents": ["/Users/a/x.pdf"]}
            created = (await http.post(f"/v1/projects/{project_id}/sources", json=body)).json()["documents"]["created"]
            document_id = created[0]["id"]
            return await asyncio.gather(*(http.post(f"/v1/documents/{document_id}/jobs", json={}) for _ in range(2)))

    responses = asyncio.run(go())
    assert sorted(item.status_code for item in responses) == [202, 409]
    assert broker.depth(topology.QUEUE_JOBS) == 1


# ── POST /v1/projects/{id}/jobs ─────────────────────────────────────────────────
def test_batch_not_processed_enqueues_only_documents_without_jobs(client, broker):
    project_id, ids, _ = new_project_with_documents(client, count=3)
    assert client.post(f"/v1/documents/{ids[0]}/jobs", json={}).status_code == 202
    response = client.post(f"/v1/projects/{project_id}/jobs", json={"engine": "auto", "not_processed": True})
    assert response.status_code == 202
    body = response.json()
    assert set(body) == {"enqueued", "skipped", "failed"}
    assert [item["document_id"] for item in body["enqueued"]] == ids[1:]
    # US2-2/FR-012: o documento que já tem job ativo é ignorado COM aviso em português, e contado na resposta
    assert [item["document_id"] for item in body["skipped"]] == [ids[0]]
    assert "pendente ou em andamento" in body["skipped"][0]["message"]
    assert body["failed"] == []
    assert all(set(item) == JOB_FIELDS for item in body["enqueued"])
    assert broker.depth(topology.QUEUE_JOBS) == 3


def test_batch_by_ids_skips_documents_with_an_active_job_with_a_message(client, broker):
    project_id, ids, _ = new_project_with_documents(client, count=3)
    assert client.post(f"/v1/documents/{ids[1]}/jobs", json={}).status_code == 202
    response = client.post(f"/v1/projects/{project_id}/jobs", json={"engine": "pymupdf", "document_ids": ids})
    assert response.status_code == 202
    body = response.json()
    assert [item["document_id"] for item in body["enqueued"]] == [ids[0], ids[2]]
    assert [item["document_id"] for item in body["skipped"]] == [ids[1]]
    assert body["skipped"][0]["message"]
    assert broker.depth(topology.QUEUE_JOBS) == 3  # 1 antes + 2 do lote; o pulado não publicou de novo


def test_batch_defaults_engine_and_ignores_repeated_ids(client, broker):
    project_id, ids, _ = new_project_with_documents(client, count=1)
    response = client.post(f"/v1/projects/{project_id}/jobs", json={"document_ids": [ids[0], ids[0]]})
    assert response.status_code == 202
    assert len(response.json()["enqueued"]) == 1 and response.json()["skipped"] == []


@pytest.mark.parametrize(
    "body",
    [
        {"document_ids": [1], "not_processed": True},
        {},
        {"engine": "auto"},
        {"document_ids": []},
        {"not_processed": False},
        {"not_processed": "true"},
        {"document_ids": ["1"]},
        {"document_ids": [True]},
        {"unknown": 1, "not_processed": True},
    ],
)
def test_batch_refuses_invalid_target_combinations_with_400_and_creates_nothing(client, broker, body):
    project_id, ids, _ = new_project_with_documents(client, count=2)
    response = client.post(f"/v1/projects/{project_id}/jobs", json=body)
    assert response.status_code == 400 and code(response) == "invalid_request"
    assert broker.depth(topology.QUEUE_JOBS) == 0
    assert all(item["status"] == "not_processed" for item in client.get(f"/v1/projects/{project_id}/documents").json()["documents"])


def test_batch_with_an_id_from_another_project_is_404_and_creates_nothing(client, broker):
    project_id, ids, _ = new_project_with_documents(client, count=2)
    _, other_ids, _ = new_project_with_documents(client, count=1, name="Outro")
    response = client.post(f"/v1/projects/{project_id}/jobs", json={"document_ids": [ids[0], other_ids[0]]})
    assert response.status_code == 404 and code(response) == "not_found"
    assert broker.depth(topology.QUEUE_JOBS) == 0
    assert client.get(f"/v1/documents/{ids[0]}").json()["latest_job_id"] is None  # nenhuma criação parcial
    assert client.post("/v1/projects/9999/jobs", json={"not_processed": True}).status_code == 404


def test_batch_with_more_than_the_id_limit_is_413_and_a_bad_engine_is_422(client, broker):
    project_id, ids, _ = new_project_with_documents(client, count=1)
    response = client.post(f"/v1/projects/{project_id}/jobs", json={"document_ids": list(range(1, 10_002))})
    assert response.status_code == 413 and code(response) == "payload_too_large"
    response = client.post(f"/v1/projects/{project_id}/jobs", json={"engine": "xyz", "document_ids": ids})
    assert response.status_code == 422 and code(response) == "validation_failed"
    assert broker.depth(topology.QUEUE_JOBS) == 0


def test_batch_with_broker_down_fails_every_document_fast_and_leaves_no_pending_job(store, broker, publisher):
    broker.set_down()
    assert wait_for(lambda: not publisher.connected)
    count = 1000
    with TestClient(create_app(settings_for(), store=store, broker=publisher), base_url="http://127.0.0.1") as client:
        project_id, ids, _ = new_project_with_documents(client, count=count)
        started = time.monotonic()
        response = client.post(f"/v1/projects/{project_id}/jobs", json={"not_processed": True})
        elapsed = time.monotonic() - started
    assert response.status_code == 202
    body = response.json()
    assert body["enqueued"] == [] and body["skipped"] == []
    assert len(body["failed"]) == count
    assert all(item["status"] == "failed" and item["error_summary"] for item in body["failed"])
    assert all("sh stack/up.sh" in item["error_summary"] for item in body["failed"])  # FR-032: como subir a stack
    assert asyncio.run(store.list_unpublished_jobs()) == []  # nenhum `pending` sem mensagem
    assert elapsed < 30.0  # sem esperar o prazo por documento (S-05)


def test_batch_stops_waiting_after_the_first_unconfirmed_publication(store, broker, publisher):
    """Primeira recusa do broker: o restante do lote falha na hora, sem publicar nada."""
    with TestClient(create_app(settings_for(), store=store, broker=publisher), base_url="http://127.0.0.1") as client:
        project_id, ids, _ = new_project_with_documents(client, count=4)
        broker.nack_next_publishes = 1
        body = client.post(f"/v1/projects/{project_id}/jobs", json={"not_processed": True}).json()
    assert body["enqueued"] == [] and len(body["failed"]) == 4
    assert broker.depth(topology.QUEUE_JOBS) == 0
    assert asyncio.run(store.list_unpublished_jobs()) == []


# ── GET /v1/jobs/{id}, cancelamento e tentativas (tarefa 7.5) ───────────────────
def test_get_job_returns_the_job_and_404_for_unknown(client):
    _, ids, _ = new_project_with_documents(client)
    job = client.post(f"/v1/documents/{ids[0]}/jobs", json={}).json()
    response = client.get(f"/v1/jobs/{job['id']}")
    assert response.status_code == 200 and response.json() == job
    assert client.get("/v1/jobs/9999").status_code == 404
    assert client.get("/v1/jobs/0").status_code == 400


def test_cancel_a_pending_job_then_the_document_can_be_enqueued_again(client, broker):
    _, ids, _ = new_project_with_documents(client)
    job = client.post(f"/v1/documents/{ids[0]}/jobs", json={}).json()
    response = client.post(f"/v1/jobs/{job['id']}/cancel")
    assert response.status_code == 200
    assert response.json()["status"] == "cancelled" and response.json()["finished_at"] is not None
    again = client.post(f"/v1/documents/{ids[0]}/jobs", json={})
    assert again.status_code == 202 and again.json()["id"] != job["id"]


def test_cancel_a_final_job_is_409_and_unknown_is_404(client):
    _, ids, _ = new_project_with_documents(client)
    job = client.post(f"/v1/documents/{ids[0]}/jobs", json={}).json()
    assert client.post(f"/v1/jobs/{job['id']}/cancel").status_code == 200
    second = client.post(f"/v1/jobs/{job['id']}/cancel")
    assert second.status_code == 409 and code(second) == "invalid_transition"
    assert client.post("/v1/jobs/9999/cancel").status_code == 404


def test_cancel_a_running_orphan_job_and_discard_the_late_result(store, broker, publisher):
    """Job `running` sem worker vivo (órfão) pode ser cancelado; o resultado tardio é descartado sem erro."""
    from artigo2md.api.consumer import EventHandler
    from queue_support import report

    application = create_app(settings_for(), store=store, broker=publisher)
    with TestClient(application, base_url="http://127.0.0.1") as client:
        _, ids, _ = new_project_with_documents(client)
        job = client.post(f"/v1/documents/{ids[0]}/jobs", json={}).json()
        asyncio.run(store.start_job(job["id"]))
        assert client.post(f"/v1/jobs/{job['id']}/cancel").json()["status"] == "cancelled"
        late = schema.encode(schema.build_result(str(job["id"]), 1, "/host/data/1/a.md", "docling", report()))
        outcome = asyncio.run(EventHandler(application.state.services).handle(late))
        assert outcome.action == "discarded" and outcome.reason == "job-final"
        assert client.get(f"/v1/jobs/{job['id']}").json()["status"] == "cancelled"
        assert asyncio.run(store.get_result(job["id"])) is None
        assert client.get(f"/v1/jobs/{job['id']}/attempts").json() == {"attempts": []}


def test_attempts_are_listed_by_position_with_quality_and_error(store, broker, publisher):
    from artigo2md.store.models import QualityRecord

    with TestClient(create_app(settings_for(), store=store, broker=publisher), base_url="http://127.0.0.1") as client:
        _, ids, _ = new_project_with_documents(client)
        job = client.post(f"/v1/documents/{ids[0]}/jobs", json={}).json()

        async def record():
            await store.start_job(job["id"])
            await store.record_attempt(job["id"], "pymupdf", 0.5, None, "falhou")
            await store.record_attempt(
                job["id"], "docling", 1.25, QualityRecord(90.0, True, 1, 1, 450.0, ("aviso",)), None
            )

        asyncio.run(record())
        attempts = client.get(f"/v1/jobs/{job['id']}/attempts").json()["attempts"]
    assert [item["position"] for item in attempts] == [1, 2]
    assert set(attempts[0]) == {"id", "job_id", "position", "engine", "seconds", "quality", "error", "created_at"}
    assert attempts[0]["quality"] is None and attempts[0]["error"] == "falhou"
    assert attempts[1]["quality"] == {
        "score": 90.0, "accepted": True, "tables_found": 1, "tables_expected": 1, "chars_per_page": 450.0,
        "issues": ["aviso"],
    }


def test_attempts_of_an_unknown_job_is_404(client):
    assert client.get("/v1/jobs/9999/attempts").status_code == 404
