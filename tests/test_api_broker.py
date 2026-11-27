"""Publicadora AMQP da API com o corretor falso (`api/broker.py`, tarefa 7.1; FR-011, FR-032; plan S-05)."""
from __future__ import annotations

import asyncio
import threading
import time
from types import SimpleNamespace

import pytest

from artigo2md.api.broker import BrokerPublisher, PublishBatch, PublishFailed
from artigo2md.jobqueue import schema, topology
from fake_amqp import FakeBroker, FakeChannel


def message(n=1):
    return schema.build_job(str(n), f"/data/{n}.pdf", "auto", 1)


def wait_for(predicate, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture()
def broker():
    return FakeBroker()


@pytest.fixture()
def make(broker):
    created = []

    def factory(**kwargs):
        kwargs.setdefault("connect", lambda settings: broker.connection())
        kwargs.setdefault("backoff_start", 0.01)
        kwargs.setdefault("backoff_max", 0.05)
        kwargs.setdefault("poll", 0.02)
        publisher = BrokerPublisher(SimpleNamespace(), **kwargs)
        created.append(publisher)
        return publisher

    yield factory
    for publisher in created:
        publisher.stop()


def started(make, **kwargs):
    publisher = make(**kwargs)
    publisher.start()
    assert wait_for(lambda: publisher.connected)
    return publisher


# ── confirmação ─────────────────────────────────────────────────────────────────
def test_confirmed_publish_lands_in_the_jobs_queue_with_the_exact_message(broker, make):
    publisher = started(make)
    asyncio.run(publisher.publish(message(7)))
    queued = broker.peek(topology.QUEUE_JOBS)
    assert len(queued) == 1
    assert queued[0].body == schema.encode(message(7))
    parsed = schema.parse_job(queued[0].body)
    assert (parsed.job_id, parsed.pdf_path, parsed.engine, parsed.attempt) == ("7", "/data/7.pdf", "auto", 1)
    exchange, routing_key, _, props = broker.publish_log[-1]
    assert (exchange, routing_key) == (topology.EXCHANGE_JOBS, topology.RK_CONVERT)
    assert props.delivery_mode == 2 and props.content_type == "application/json"


def test_publisher_uses_confirm_delivery_and_declares_the_topology(broker, make):
    publisher = started(make)
    channel = broker.connections[0].channels[0]
    assert channel.confirm is True
    assert set(broker.queues) >= {topology.QUEUE_JOBS, topology.QUEUE_EVENTS}
    assert publisher.connected


def test_many_publishes_keep_the_order(broker, make):
    publisher = started(make)

    async def go():
        for n in range(20):
            await publisher.publish(message(n))

    asyncio.run(go())
    ids = [schema.parse_job(item.body).job_id for item in broker.peek(topology.QUEUE_JOBS)]
    assert ids == [str(n) for n in range(20)]


# ── recusa (nack e não roteável) ────────────────────────────────────────────────
def test_nack_fails_the_message_and_keeps_the_connection(broker, make):
    publisher = started(make)
    broker.nack_next_publishes = 1
    with pytest.raises(PublishFailed) as caught:
        asyncio.run(publisher.publish(message(1)))
    assert "recusou" in caught.value.reason
    assert publisher.connected
    asyncio.run(publisher.publish(message(2)))  # a seguinte passa, sem reconectar
    assert len(broker.connections) == 1
    assert [schema.parse_job(i.body).job_id for i in broker.peek(topology.QUEUE_JOBS)] == ["2"]


def test_unroutable_message_is_refused(broker, make):
    publisher = started(make)
    broker.bindings.clear()  # a exchange existe, mas nenhuma fila recebe `convert`
    with pytest.raises(PublishFailed) as caught:
        asyncio.run(publisher.publish(message(1)))
    assert "recusou" in caught.value.reason
    assert publisher.connected


# ── broker fora, S-05 ───────────────────────────────────────────────────────────
def test_broker_down_fails_immediately_without_waiting_the_deadline(broker, make):
    broker.set_down()
    publisher = make(deadline=30.0)
    publisher.start()
    time.sleep(0.1)
    assert publisher.connected is False
    started_at = time.monotonic()
    with pytest.raises(PublishFailed) as caught:
        asyncio.run(publisher.publish(message(1)))
    assert time.monotonic() - started_at < 1.0
    assert "não está disponível" in caught.value.reason


def test_batch_with_broker_down_fails_every_document_fast(broker, make):
    broker.set_down()
    publisher = make(deadline=30.0)
    publisher.start()

    async def go():
        batch = publisher.batch()
        failures = 0
        for n in range(1000):
            try:
                await batch.publish(message(n))
            except PublishFailed:
                failures += 1
        return failures

    started_at = time.monotonic()
    assert asyncio.run(go()) == 1000
    assert time.monotonic() - started_at < 2.0


def test_batch_trips_after_the_first_failure_without_touching_the_publisher():
    calls = []

    class Flaky:
        async def publish(self, msg):
            calls.append(msg)
            if len(calls) == 2:
                raise PublishFailed("Falhou.")

    async def go():
        batch = PublishBatch(Flaky())
        outcomes = []
        for n in range(5):
            try:
                await batch.publish(message(n))
                outcomes.append("ok")
            except PublishFailed as exc:
                outcomes.append(exc.reason)
        return batch, outcomes

    batch, outcomes = asyncio.run(go())
    assert outcomes == ["ok", "Falhou.", "Falhou.", "Falhou.", "Falhou."]
    assert len(calls) == 2 and batch.tripped  # só as duas primeiras chegaram à publicadora


# ── prazo ───────────────────────────────────────────────────────────────────────
def test_deadline_overrun_is_not_confirmed_and_marks_the_publisher_stalled(broker, make, monkeypatch):
    release = threading.Event()
    real = FakeChannel.basic_publish

    def slow(self, *args, **kwargs):
        release.wait(5)
        return real(self, *args, **kwargs)

    monkeypatch.setattr(FakeChannel, "basic_publish", slow)
    publisher = started(make, deadline=0.1)
    started_at = time.monotonic()
    with pytest.raises(PublishFailed) as caught:
        asyncio.run(publisher.publish(message(1)))
    assert 0.09 <= time.monotonic() - started_at < 2.0
    assert "a tempo" in caught.value.reason
    assert publisher.connected is False  # travada no envio: novos pedidos falham na hora
    with pytest.raises(PublishFailed):
        asyncio.run(publisher.publish(message(2)))
    release.set()
    assert wait_for(lambda: publisher.connected)  # destravou: volta a aceitar


def test_item_cancelled_by_the_deadline_before_pickup_is_never_published(broker, make, monkeypatch):
    release = threading.Event()
    real = FakeChannel.basic_publish

    def slow(self, exchange, routing_key, body, **kwargs):
        if schema.parse_job(body).job_id == "1":
            release.wait(5)
        return real(self, exchange, routing_key, body, **kwargs)

    monkeypatch.setattr(FakeChannel, "basic_publish", slow)
    publisher = started(make, deadline=0.1)

    async def go():
        first = asyncio.create_task(publisher.publish(message(1)))
        await asyncio.sleep(0.03)
        second = asyncio.create_task(publisher.publish(message(2)))  # fica na fila atrás do envio travado
        results = await asyncio.gather(first, second, return_exceptions=True)
        return results

    results = asyncio.run(go())
    assert all(isinstance(item, PublishFailed) for item in results)
    release.set()
    time.sleep(0.2)
    published = [schema.parse_job(i.body).job_id for i in broker.peek(topology.QUEUE_JOBS)]
    assert "2" not in published  # o job 2 foi dado como não confirmado e jamais é enviado depois


# ── reconexão sem reiniciar ─────────────────────────────────────────────────────
def test_reconnects_by_itself_after_the_broker_returns(broker, make):
    publisher = started(make)
    asyncio.run(publisher.publish(message(1)))
    broker.set_down()
    assert wait_for(lambda: not publisher.connected)
    with pytest.raises(PublishFailed):
        asyncio.run(publisher.publish(message(2)))
    broker.set_up()
    assert wait_for(lambda: publisher.connected)
    asyncio.run(publisher.publish(message(3)))
    ids = [schema.parse_job(i.body).job_id for i in broker.peek(topology.QUEUE_JOBS)]
    assert ids == ["1", "3"]


def test_starts_without_broker_and_connects_when_it_appears(broker, make):
    broker.set_down()
    publisher = make()
    publisher.start()
    time.sleep(0.15)
    assert publisher.connected is False
    broker.set_up()
    assert wait_for(lambda: publisher.connected)
    asyncio.run(publisher.publish(message(1)))


def test_on_connect_hook_runs_on_every_connection(broker, make):
    calls = []
    publisher = started(make, on_connect=lambda: calls.append(1))
    assert wait_for(lambda: len(calls) == 1)
    broker.set_down()
    assert wait_for(lambda: not publisher.connected)
    broker.set_up()
    assert wait_for(lambda: len(calls) == 2)


def test_failing_on_connect_hook_does_not_break_the_publisher(broker, make):
    def boom():
        raise RuntimeError("falha do gancho")

    publisher = started(make, on_connect=boom)
    asyncio.run(publisher.publish(message(1)))


def test_connect_failures_are_retried_with_growing_backoff(broker, make):
    attempts = []

    def connect(settings):
        attempts.append(time.monotonic())
        if len(attempts) < 4:
            raise OSError("fake")
        return broker.connection()

    publisher = make(connect=connect, backoff_start=0.05, backoff_max=0.5)
    # O recuo é conferido pela espera que a publicadora pede, não pela razão entre intervalos medidos no relógio:
    # em máquina carregada cada espera atrasa dezenas de milissegundos e essa razão deixa de valer.
    waits = []
    real_wait = publisher._stop.wait

    def recording_wait(timeout=None):
        waits.append(timeout)
        return real_wait(timeout)

    publisher._stop.wait = recording_wait
    publisher.start()
    assert wait_for(lambda: publisher.connected)
    assert len(attempts) == 4
    assert waits[:3] == [0.05, 0.1, 0.2]  # recuo exponencial
    gaps = [b - a for a, b in zip(attempts, attempts[1:])]
    assert all(gap >= wait * 0.9 for gap, wait in zip(gaps, waits))  # a espera pedida é de fato cumprida


# ── entrada e encerramento ──────────────────────────────────────────────────────
def test_message_over_the_contract_limit_is_refused_without_publishing(broker, make):
    publisher = started(make)
    huge = schema.build_job("1", "/" + "a" * 20000 + ".pdf", "auto", 1)
    with pytest.raises(PublishFailed):
        asyncio.run(publisher.publish(huge))
    assert broker.depth(topology.QUEUE_JOBS) == 0


def test_stop_releases_thread_and_connection(broker, make):
    publisher = started(make)
    thread = publisher._thread
    publisher.stop()
    assert not thread.is_alive()
    assert publisher.connected is False
    assert all(conn.closed for conn in broker.connections) or not broker.connections
    with pytest.raises(PublishFailed):
        asyncio.run(publisher.publish(message(1)))


def test_failure_messages_are_portuguese_and_leak_nothing(broker, make):
    broker.set_down()
    publisher = make()
    publisher.start()
    time.sleep(0.05)
    with pytest.raises(PublishFailed) as caught:
        asyncio.run(publisher.publish(message(1)))
    text = caught.value.reason
    assert "fake" not in text and "Traceback" not in text and "amqp" not in text.lower()


# ── consumidora (tarefa 7.3) ────────────────────────────────────────────────────
import threading  # noqa: E402  (agrupado com os testes da consumidora)
from pathlib import Path  # noqa: E402

from artigo2md.api.broker import EventConsumer  # noqa: E402
from artigo2md.api.consumer import EventHandler, TransientStoreError  # noqa: E402
from artigo2md.api.deps import Services  # noqa: E402
from artigo2md.api.settings import ApiSettings  # noqa: E402
from artigo2md.store import errors as store_errors  # noqa: E402
from fake_store import FakeStore  # noqa: E402


class LoopThread:
    """Laço `asyncio` da \"API\" numa thread própria; os testes (síncronos) usam `call` para tocar o store."""

    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self._thread.start()

    def call(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(10)

    def close(self):
        self.loop.call_soon_threadsafe(self.loop.stop)
        self._thread.join(5)
        self.loop.close()


@pytest.fixture()
def api_loop():
    loop_thread = LoopThread()
    yield loop_thread
    loop_thread.close()


@pytest.fixture()
def store():
    return FakeStore()


@pytest.fixture()
def handler(store):
    settings = ApiSettings.from_env(
        {"ARTIGO2MD_DATA_DIR_HOST": "/host/data", "ARTIGO2MD_DATA_DIR_MOUNT": "/tmp"}, Path("/nonexistent-cwd")
    )
    return EventHandler(Services(settings, store=store))


@pytest.fixture()
def make_consumer(broker, api_loop, handler):
    created = []

    def factory(**kwargs):
        kwargs.setdefault("connect", lambda settings: broker.connection())
        kwargs.setdefault("backoff_start", 0.01)
        kwargs.setdefault("backoff_max", 0.05)
        kwargs.setdefault("poll", 0.02)
        consumer = EventConsumer(
            SimpleNamespace(), handler.handle, api_loop.loop, retry_on=TransientStoreError, **kwargs
        )
        created.append(consumer)
        return consumer

    yield factory
    for consumer in created:
        consumer.stop()


def declare_topology(broker):
    connection = broker.connection()
    topology.declare(connection.channel())
    return connection


def publish_event(broker, event):
    channel = declare_topology(broker).channel()
    channel.basic_publish(
        exchange=topology.EXCHANGE_EVENTS, routing_key=schema.ROUTING_KEYS[event["type"]], body=schema.encode(event)
    )


def make_pending_job(api_loop, store):
    async def go():
        project = await store.create_project("P")
        source = await store.add_source(project.id, "folder", "in_place", "/Users/a")
        document, _ = await store.add_document(project.id, source.id, "/Users/a/x.pdf")
        return await store.create_job(document.id, "auto")

    return api_loop.call(go())


def job_status(api_loop, store, job_id):
    """Status lido por `list_documents`: nunca consome as falhas injetadas em `get_job` (usado pelo tratamento)."""
    async def read():
        for project in await store.list_projects():
            for item in await store.list_documents(project.id):
                if item.latest_job_id == job_id:
                    return item.status
        return None

    return api_loop.call(read())


def test_consumer_applies_events_in_order_and_acks_only_after_handling(broker, api_loop, store, make_consumer):
    job = make_pending_job(api_loop, store)
    publish_event(broker, schema.build_progress(str(job.id), 1, "Convertendo"))
    consumer = make_consumer()
    assert consumer.last_event_at is None and consumer.connected is False
    consumer.start()
    assert wait_for(lambda: job_status(api_loop, store, job.id) == "running")
    assert wait_for(lambda: broker.depth(topology.QUEUE_EVENTS) == 0 and consumer.last_event_at is not None)
    assert consumer.connected is True
    channel = broker.connections[-1].channels[0]
    assert wait_for(lambda: not channel.unacked)  # tudo confirmado depois do tratamento


def test_consumer_uses_prefetch_16_and_manual_ack(broker, make_consumer):
    consumer = make_consumer()
    consumer.start()
    assert wait_for(lambda: consumer.connected)
    channel = broker.connections[-1].channels[0]
    assert channel.prefetch_count == 16


def test_consumer_keeps_the_same_message_while_the_database_is_down_and_preserves_order(
    broker, api_loop, store, make_consumer
):
    job = make_pending_job(api_loop, store)
    store.inject_failure("get_job", store_errors.DatabaseUnavailableError("fora"), times=4)
    publish_event(broker, schema.build_progress(str(job.id), 1, "Convertendo"))
    publish_event(broker, schema.build_progress(str(job.id), 1, "Convertendo 2"))
    connects = []
    consumer = make_consumer(connect=lambda settings: (connects.append(1), broker.connection())[1])
    consumer.start()
    assert wait_for(lambda: job_status(api_loop, store, job.id) == "running")
    assert wait_for(lambda: broker.depth(topology.QUEUE_EVENTS) == 0)
    assert consumer.connected is True  # a queda do banco não derruba a conexão AMQP
    assert len(connects) == 1  # nem exige reconexão


def test_progress_is_handled_before_the_result_of_the_same_job_even_with_the_database_flapping(
    broker, api_loop, store, make_consumer
):
    from queue_support import report

    job = make_pending_job(api_loop, store)
    store.inject_failure("get_job", store_errors.DatabaseUnavailableError("fora"), times=3)
    publish_event(broker, schema.build_progress(str(job.id), 1, "Convertendo"))
    publish_event(broker, schema.build_result(str(job.id), 1, "/host/data/1/a.md", "docling", report()))
    consumer = make_consumer()
    consumer.start()
    assert wait_for(lambda: job_status(api_loop, store, job.id) == "succeeded")
    events = api_loop.call(store.list_events(project_id=job.project_id, limit=100))
    types = [e.event_type for e in events]
    assert types.index("job_started") < types.index("job_succeeded")


def test_consumer_reconnects_after_the_broker_returns_without_restart(broker, api_loop, store, make_consumer):
    job = make_pending_job(api_loop, store)
    consumer = make_consumer()
    consumer.start()
    assert wait_for(lambda: consumer.connected)
    broker.set_down()
    assert wait_for(lambda: not consumer.connected)
    broker.set_up()
    assert wait_for(lambda: consumer.connected)
    publish_event(broker, schema.build_progress(str(job.id), 1, "Voltou"))
    assert wait_for(lambda: job_status(api_loop, store, job.id) == "running")


def test_status_signals_a_stopped_consumer_and_its_recovery_with_the_real_consumer(
    broker, api_loop, store, make_consumer
):
    """10.2.4 / FR-017: `GET /v1/status` reflete a consumidora real (sem tocar banco nem fila); `/health` não muda."""
    from fastapi.testclient import TestClient

    from artigo2md.api.app import create_app

    async def exploding_factory(_settings):
        raise AssertionError("GET /v1/status não pode tocar o banco")

    consumer = make_consumer()
    settings = ApiSettings.from_env({}, Path("/nonexistent-cwd"))
    app = create_app(settings, store_factory=exploding_factory, consumer=consumer)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        assert client.get("/v1/status").json()["consumer"] == {"connected": False, "last_event_at": None}
        consumer.start()
        assert wait_for(lambda: client.get("/v1/status").json()["consumer"]["connected"])
        broker.set_down()
        assert wait_for(lambda: not client.get("/v1/status").json()["consumer"]["connected"])
        assert client.get("/health").status_code == 200  # parada da consumidora não muda o significado de /health
        broker.set_up()
        assert wait_for(lambda: client.get("/v1/status").json()["consumer"]["connected"])


def test_event_in_flight_when_the_connection_drops_is_redelivered_and_handled_once(
    broker, api_loop, store, make_consumer
):
    job = make_pending_job(api_loop, store)
    store.inject_failure("get_job", store_errors.DatabaseUnavailableError("fora"), times=3)
    publish_event(broker, schema.build_progress(str(job.id), 1, "Convertendo"))
    consumer = make_consumer(backoff_start=0.2, backoff_max=0.2)  # a mensagem fica retida esperando o banco
    consumer.start()
    assert wait_for(lambda: consumer.connected)
    broker.set_down()
    broker.set_up()  # a conexão morreu com a mensagem sem ack: o broker a devolve
    assert wait_for(lambda: job_status(api_loop, store, job.id) == "running", timeout=10)
    assert wait_for(lambda: broker.depth(topology.QUEUE_EVENTS) == 0)
    events = api_loop.call(store.list_events(project_id=job.project_id, limit=100))
    assert [e.event_type for e in events].count("job_started") == 1


def test_poison_message_is_acked_and_does_not_block_the_next_one(broker, api_loop, store, make_consumer):
    job = make_pending_job(api_loop, store)
    channel = declare_topology(broker).channel()
    channel.basic_publish(exchange=topology.EXCHANGE_EVENTS, routing_key="job.progress", body=b"lixo")
    publish_event(broker, schema.build_progress(str(job.id), 1, "ok"))
    consumer = make_consumer()
    consumer.start()
    assert wait_for(lambda: job_status(api_loop, store, job.id) == "running")
    assert wait_for(lambda: broker.depth(topology.QUEUE_EVENTS) == 0)


def test_stop_releases_the_thread_and_the_connection(broker, make_consumer):
    consumer = make_consumer()
    consumer.start()
    assert wait_for(lambda: consumer.connected)
    connection = broker.connections[-1]
    consumer.stop()
    assert consumer.connected is False
    assert connection.closed is True
    assert not any(thread.name == "artigo2md-consumer" and thread.is_alive() for thread in threading.enumerate())


def test_consumer_starts_without_broker_and_connects_when_it_appears(broker, make_consumer):
    broker.set_down()
    consumer = make_consumer()
    consumer.start()
    time.sleep(0.1)
    assert consumer.connected is False
    broker.set_up()
    assert wait_for(lambda: consumer.connected)
