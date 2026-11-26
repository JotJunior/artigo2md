"""Partida, reconexão e encerramento dos serviços de fundo (`api/background.py`, tarefa 7.6; FR-011, FR-017, FR-032)."""
from __future__ import annotations

import asyncio
import dataclasses
import threading
import time
from pathlib import Path

from fastapi.testclient import TestClient

from artigo2md.api.app import create_app
from artigo2md.api.background import republish_unpublished
from artigo2md.api.deps import Services
from artigo2md.api.settings import ApiSettings, BrokerSettings
from artigo2md.jobqueue import schema, topology
from artigo2md.jobqueue.settings import Credentials
from artigo2md.store import errors as se
from fake_amqp import FakeBroker
from fake_store import FakeStore


def wait_for(predicate, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def settings_with_broker():
    base = ApiSettings.from_env({}, Path("/nonexistent-cwd-for-tests"))
    return dataclasses.replace(
        base, broker=BrokerSettings(host="127.0.0.1", port=5672, credentials=Credentials("u", "p", "ambiente"))
    )


def queued(broker):
    """Mensagens na fila de jobs (0 enquanto nenhuma conexão declarou a topologia)."""
    return broker.depth(topology.QUEUE_JOBS) if topology.QUEUE_JOBS in broker.queues else 0


def options(broker):
    return {"connect": lambda settings: broker.connection(), "backoff_start": 0.01, "backoff_max": 0.05, "poll": 0.02}


class FlakyFactory:
    """`store_factory` que falha (banco fora ou migração recusada) até `recover()`."""

    def __init__(self, store, error):
        self.store, self.error, self.down, self.calls = store, error, True, 0

    async def __call__(self, _settings):
        self.calls += 1
        if self.down:
            raise self.error
        return self.store

    def recover(self):
        self.down = False


def seed_pending_job(store, path="/Users/a/x.pdf", engine="auto"):
    async def go():
        project = await store.create_project("P")
        source = await store.add_source(project.id, "folder", "in_place", "/Users/a")
        document, _ = await store.add_document(project.id, source.id, path)
        return await store.create_job(document.id, engine)

    return asyncio.run(go())


def make_app(store_or_factory, broker, *, settings=None):
    kwargs = {"store_factory": store_or_factory} if callable(store_or_factory) else {"store": store_or_factory}
    return create_app(
        settings or settings_with_broker(), background=True, background_options=options(broker), **kwargs
    )


def background_threads():
    return [t for t in threading.enumerate() if t.name in ("artigo2md-publisher", "artigo2md-consumer") and t.is_alive()]


# ── partida ─────────────────────────────────────────────────────────────────────
def test_start_republishes_pending_unpublished_jobs_and_marks_them(tmp_path):
    store, broker = FakeStore(), FakeBroker()
    first = seed_pending_job(store, "/Users/a/x.pdf", "docling")
    with TestClient(make_app(store, broker), base_url="http://127.0.0.1") as client:
        assert wait_for(lambda: queued(broker) == 1)
        message = schema.parse_job(broker.peek(topology.QUEUE_JOBS)[0].body)
        assert (message.job_id, message.pdf_path, message.engine, message.attempt) == (
            str(first.id), "/Users/a/x.pdf", "docling", 1,
        )
        assert wait_for(lambda: asyncio.run(store.get_job(first.id)).published_at is not None)
        assert wait_for(lambda: client.get("/v1/status").json()["consumer"]["connected"])
        assert client.get("/v1/status").json()["publisher"]["connected"] is True


def test_republish_on_every_reconnection_never_repeats_confirmed_jobs():
    store, broker = FakeStore(), FakeBroker()
    seed_pending_job(store)
    with TestClient(make_app(store, broker), base_url="http://127.0.0.1") as client:
        assert wait_for(lambda: queued(broker) == 1)
        assert wait_for(lambda: client.get("/v1/status").json()["publisher"]["connected"])
        broker.set_down()
        assert wait_for(lambda: not client.get("/v1/status").json()["publisher"]["connected"])
        broker.set_up()
        assert wait_for(lambda: client.get("/v1/status").json()["publisher"]["connected"])
        time.sleep(0.3)
        assert queued(broker) == 1  # o job confirmado não foi publicado de novo


def test_job_created_while_the_broker_was_down_is_published_when_it_returns():
    store, broker = FakeStore(), FakeBroker()
    broker.set_down()
    job = seed_pending_job(store)
    with TestClient(make_app(store, broker), base_url="http://127.0.0.1") as client:
        time.sleep(0.2)
        assert queued(broker) == 0
        assert client.get("/v1/status").json()["publisher"]["connected"] is False
        broker.set_up()
        assert wait_for(lambda: queued(broker) == 1)
        assert wait_for(lambda: asyncio.run(store.get_job(job.id)).published_at is not None)


def test_unconfirmed_republication_leaves_the_job_pending_and_unpublished():
    store, broker = FakeStore(), FakeBroker()
    job = seed_pending_job(store)
    broker.nack_next_publishes = 1
    with TestClient(make_app(store, broker), base_url="http://127.0.0.1"):
        # a primeira rodada é recusada; o supervisor tenta de novo com recuo e a segunda passa
        assert wait_for(lambda: queued(broker) == 1)
        assert wait_for(lambda: asyncio.run(store.get_job(job.id)).published_at is not None)
    assert asyncio.run(store.get_job(job.id)).status == "pending"  # nunca foi falhado por republicação


def test_inflight_jobs_are_skipped_by_the_republication():
    store, broker = FakeStore(), FakeBroker()
    job = seed_pending_job(store)

    class Recorder:
        def __init__(self):
            self.messages = []

        async def publish(self, message):
            self.messages.append(message)

    services = Services(ApiSettings.from_env({}, Path("/nonexistent-cwd-for-tests")), store=store)
    services.inflight.add(job.id)
    recorder = Recorder()
    assert asyncio.run(republish_unpublished(services, recorder)) == 0
    assert recorder.messages == []
    services.inflight.clear()
    assert asyncio.run(republish_unpublished(services, recorder)) == 1
    assert recorder.messages[0]["job_id"] == str(job.id)


# ── banco fora / migração recusada (7.6.2) ──────────────────────────────────────
def test_database_down_keeps_health_and_returns_503_until_it_recovers():
    store, broker = FakeStore(), FakeBroker()
    factory = FlakyFactory(store, se.DatabaseUnavailableError("fora"))
    with TestClient(make_app(factory, broker), base_url="http://127.0.0.1") as client:
        assert client.get("/health").json()["status"] == "ok"
        response = client.get("/v1/projects")
        assert response.status_code == 503 and response.json()["error"]["code"] == "database_unavailable"
        assert "stack/up.sh" in response.json()["error"]["message"]
        assert wait_for(lambda: client.get("/v1/status").json()["consumer"]["connected"])  # AMQP independe do banco
        factory.recover()
        assert client.get("/v1/projects").status_code == 200  # sem reinício


def test_migration_failure_keeps_health_and_recovers_when_the_database_returns():
    store, broker = FakeStore(), FakeBroker()
    factory = FlakyFactory(store, se.MigrationError("migração recusada"))
    with TestClient(make_app(factory, broker), base_url="http://127.0.0.1") as client:
        assert client.get("/health").status_code == 200
        response = client.get("/v1/projects")
        assert response.status_code == 503 and response.json()["error"]["code"] == "database_unavailable"
        assert "recusada" not in response.text  # nada do texto do migrador chega ao cliente
        calls_before = factory.calls
        assert wait_for(lambda: factory.calls > calls_before)  # o supervisor também insiste, com recuo
        factory.recover()
        assert client.get("/v1/projects").status_code == 200


def test_pending_jobs_are_republished_after_the_database_comes_back():
    store, broker = FakeStore(), FakeBroker()
    seed_pending_job(store)
    factory = FlakyFactory(store, se.DatabaseUnavailableError("fora"))
    with TestClient(make_app(factory, broker), base_url="http://127.0.0.1") as client:
        assert wait_for(lambda: client.get("/v1/status").json()["publisher"]["connected"])
        time.sleep(0.2)
        assert queued(broker) == 0
        factory.recover()
        assert wait_for(lambda: queued(broker) == 1)


# ── broker e banco fora ao mesmo tempo ──────────────────────────────────────────
def test_start_with_database_and_broker_down_then_each_recovers_independently():
    store, broker = FakeStore(), FakeBroker()
    broker.set_down()
    factory = FlakyFactory(store, se.DatabaseUnavailableError("fora"))
    with TestClient(make_app(factory, broker), base_url="http://127.0.0.1") as client:
        assert client.get("/health").status_code == 200
        status = client.get("/v1/status").json()
        assert status["consumer"]["connected"] is False and status["publisher"]["connected"] is False
        broker.set_up()
        assert wait_for(lambda: client.get("/v1/status").json()["publisher"]["connected"])
        assert client.get("/v1/projects").status_code == 503  # o banco segue fora
        factory.recover()
        assert client.get("/v1/projects").status_code == 200


def test_start_without_broker_configuration_still_serves_and_reports_disconnected():
    store = FakeStore()
    base = ApiSettings.from_env({}, Path("/nonexistent-cwd-for-tests"))  # sem credenciais: broker None
    assert base.broker is None
    app = create_app(base, store=store, background=True)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        assert client.get("/health").status_code == 200
        assert client.get("/v1/status").json()["publisher"]["connected"] is False
        assert client.get("/v1/projects").status_code == 200


# ── encerramento ────────────────────────────────────────────────────────────────
def test_shutdown_releases_threads_and_connections():
    store, broker = FakeStore(), FakeBroker()
    with TestClient(make_app(store, broker), base_url="http://127.0.0.1") as client:
        assert wait_for(lambda: client.get("/v1/status").json()["consumer"]["connected"])
        assert wait_for(lambda: client.get("/v1/status").json()["publisher"]["connected"])
        assert background_threads()
    assert wait_for(lambda: not background_threads())
    assert all(connection.closed for connection in broker.connections) or not broker.connections
