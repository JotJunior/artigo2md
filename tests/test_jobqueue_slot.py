"""Slot da fila (tarefa 5.1 da `conversion-queue`): máquina de estados, retry, mensagens mortas e gate de RAM.

Usa o corretor AMQP falso (`tests/fake_amqp.py`), um conversor falso e relógio injetável; roda sem Docker e
sem o extra `queue`. Cada `slot.step()` é uma iteração limitada da máquina de estados.
"""
from __future__ import annotations

import json
import threading
import time
from types import SimpleNamespace

import pytest

import fake_amqp
from fake_amqp import FakeBroker, FakeClock, amqp_exc
from queue_support import (SECRET_PASS, SECRET_USER, FakeConverter, events, fake_properties, job_body,
                           make_settings, memory_reader, put_job, report)
from artigo2md.jobqueue import contract, slot as slot_mod, topology
from artigo2md.jobqueue.memory import MemoryGate
from artigo2md.jobqueue.slot import CredentialsRefused, Slot, classify_error
from artigo2md.jobqueue.topology import TopologyMismatch
from artigo2md.worker import ConversionError, ConversionTimeout, WorkerCrashed


class Rig:
    def __init__(self, tmp_path, *, available=10_000, settings=None, converter=None, broker=None, **overrides):
        self.tmp = tmp_path
        self.pdf = tmp_path / "artigo.pdf"
        self.pdf.write_bytes(b"%PDF-1.4 falso")
        self.clock = FakeClock()
        self.broker = broker or FakeBroker(clock=self.clock)
        self.settings = settings or make_settings(tmp_path / "dados", **overrides)
        self.settings.data_dir.mkdir(exist_ok=True)
        self.reader = memory_reader(available)
        self.gate = MemoryGate(self.settings.min_free_memory_mb, self.reader)
        self.converter = converter or FakeConverter()
        self.logs: list[str] = []
        self.waits: list[float] = []
        self.slot = self.new_slot(self.converter)

    def new_slot(self, converter, index=1, **kwargs) -> Slot:
        return Slot(index, self.settings, self.gate, converter, connect=lambda _s: self.broker.connection(),
                    properties=fake_properties, log=self.logs.append, clock=self.clock,
                    wait=self._wait, **kwargs)

    def _wait(self, seconds):
        self.waits.append(seconds)
        self.clock.advance(seconds)

    def job(self, job_id="job-1", **extra) -> bytes:
        return job_body(job_id, str(self.pdf), **extra)

    def submit(self, job_id="job-1", **extra) -> bytes:
        body = self.job(job_id, **extra)
        put_job(self.broker, body)
        return body

    def steps(self, n=1, slot=None):
        for _ in range(n):
            (slot or self.slot).step()

    def run_until(self, predicate, limit=60, slot=None):
        for _ in range(limit):
            if predicate():
                return
            (slot or self.slot).step()
        assert predicate(), f"condição não atingida em {limit} passos (estado: {(slot or self.slot).state})"

    def ready(self):
        self.run_until(lambda: self.slot.state == slot_mod.READY, limit=5)

    def drain(self, limit=60):
        """Ao menos um passo e depois até a fila de jobs ficar vazia e o slot ocioso e sem entrega em andamento."""
        self.slot.step()
        self.run_until(lambda: self.broker.depth(topology.QUEUE_JOBS) == 0 and not self._unacked()
                       and self.slot.state in (slot_mod.READY, slot_mod.COLD), limit)

    def _unacked(self):
        return sum(len(ch.unacked) for conn in self.broker.connections for ch in conn.channels)

    def codes(self):
        return [(e["type"], e.get("code")) for e in events(self.broker)]


@pytest.fixture
def rig(tmp_path):
    return Rig(tmp_path)


# ── 5.1.1 estados e canal ──────────────────────────────────────────────────────────────────────
def test_state_machine_goes_reconnecting_cold_ready_and_configures_the_channel(rig):
    assert rig.slot.state == slot_mod.RECONNECTING
    rig.slot.step()
    assert rig.slot.state == slot_mod.COLD
    channel = rig.broker.connections[0].channels[0]
    assert channel.prefetch_count == 1 and channel.confirm is True
    assert rig.broker.queues[topology.QUEUE_JOBS].consumers == []  # `cold` não consome
    rig.slot.step()
    assert rig.slot.state == slot_mod.READY
    assert len(rig.broker.queues[topology.QUEUE_JOBS].consumers) == 1
    assert len(rig.broker.queues) == 4 and len(rig.broker.exchanges) == 4  # topologia declarada na conexão


def test_idle_ready_slot_blocks_in_the_event_loop_without_spinning(rig):
    rig.ready()
    conn = rig.broker.connections[0]
    before = conn.process_calls
    rig.steps(5)
    assert conn.process_calls - before == 5  # exatamente uma espera bloqueante por iteração
    assert conn.last_time_limit == slot_mod.POLL_SECONDS > 0


# ── 5.1.2 fluxo feliz e ordem gravar -> publicar -> ack ────────────────────────────────────────
def test_happy_path_writes_publishes_result_then_acks(rig, monkeypatch):
    sequence: list[tuple] = []
    real_publish, real_ack = fake_amqp.FakeChannel.basic_publish, fake_amqp.FakeChannel.basic_ack

    def publish(self, exchange, routing_key, body, properties=None, mandatory=False):
        if routing_key == "job.result":
            sequence.append(("publish-result", (rig.settings.data_dir / "job-1" / "artigo.md").exists()))
        else:
            sequence.append(("publish", routing_key))
        return real_publish(self, exchange, routing_key, body, properties, mandatory)

    def ack(self, delivery_tag=0, multiple=False):
        sequence.append(("ack",))
        return real_ack(self, delivery_tag, multiple)

    monkeypatch.setattr(fake_amqp.FakeChannel, "basic_publish", publish)
    monkeypatch.setattr(fake_amqp.FakeChannel, "basic_ack", ack)
    rig.submit()
    sequence.clear()
    rig.ready()
    rig.drain()
    assert (rig.settings.data_dir / "job-1" / "artigo.md").read_text(encoding="utf-8") == "# ok"
    assert sequence[-2:] == [("publish-result", True), ("ack",)]  # arquivo já gravado ao publicar; ack por último
    types = [e["type"] for e in events(rig.broker)]
    assert types[-1] == "result" and set(types[:-1]) == {"progress"}
    result = events(rig.broker)[-1]
    assert result["markdown_path"] == str(rig.settings.data_dir / "job-1" / "artigo.md")
    assert rig.converter.calls == [(str(rig.pdf), "auto", 0.0)]
    assert rig.broker.depth(topology.QUEUE_JOBS) == 0 and rig.slot.state == slot_mod.READY


def test_progress_events_are_the_messages_of_the_converter(rig):
    rig.submit()
    rig.ready()
    rig.drain()
    messages = [e["message"] for e in events(rig.broker) if e["type"] == "progress"]
    assert messages == ["Carregando o conversor", "Convertendo"]


def test_conversion_timeout_option_is_passed_to_the_converter(tmp_path):
    rig = Rig(tmp_path, conversion_timeout=12.5)
    rig.submit()
    rig.ready()
    rig.drain()
    assert rig.converter.calls[0][2] == 12.5


# ── 5.1.3 falhas definitivas ───────────────────────────────────────────────────────────────────
def _bad_bodies(rig):
    good = json.loads(rig.job())
    return {
        "nao-json": (b"isto nao e json", "invalid_message", False),
        "versao-futura": (json.dumps({**good, "schema_version": 2}).encode(), "unsupported_schema_version", True),
        "job-id-invalido": (json.dumps({**good, "job_id": "../x"}).encode(), "invalid_message", False),
        "motor-invalido": (json.dumps({**good, "engine": "magico"}).encode(), "invalid_engine", True),
        "pdf-inexistente": (json.dumps({**good, "pdf_path": "/nao/existe.pdf"}).encode(), "pdf_not_found", True),
        "pdf-relativo": (json.dumps({**good, "pdf_path": "relativo.pdf"}).encode(), "pdf_not_found", True),
        "nome-oculto": (json.dumps({**good, "pdf_path": str(rig.tmp / ".oculto.pdf")}).encode(),
                        "pdf_not_found", True),
        "campo-extra": (json.dumps({**good, "extra": 1}).encode(), "invalid_message", True),
    }


@pytest.mark.parametrize("case", ["nao-json", "versao-futura", "job-id-invalido", "motor-invalido",
                                  "pdf-inexistente", "pdf-relativo", "nome-oculto", "campo-extra"])
def test_definitive_failures_go_to_dead_letters_with_the_original_body(rig, case):
    (rig.tmp / ".oculto.pdf").write_bytes(b"x")
    body, code, has_job_id = _bad_bodies(rig)[case]
    put_job(rig.broker, body)
    rig.ready()
    rig.drain()
    dead = rig.broker.peek(topology.QUEUE_DEAD)
    assert len(dead) == 1 and dead[0].body == body  # corpo original, byte a byte
    headers = dead[0].properties.headers
    if case == "nome-oculto":
        code = "invalid_path"
    assert headers["x-artigo2md-code"] == code
    assert headers["x-artigo2md-reason"] and headers["x-artigo2md-attempts"] >= 1
    assert headers["x-artigo2md-failed-at"].endswith("Z")
    assert rig.converter.calls == []  # nada chega ao conversor
    assert rig.broker.depth(topology.QUEUE_RETRY) == 0 and rig._unacked() == 0
    errors = [e for e in events(rig.broker) if e["type"] == "error"]
    if has_job_id:
        assert len(errors) == 1 and errors[0]["code"] == code and errors[0]["retryable"] is False
    else:
        assert errors == []  # sem job_id válido não há a quem atribuir o evento
    assert not any(p.name != "dados" and p.parent == rig.settings.data_dir.parent and p.is_dir()
                   and p.name == "x" for p in rig.tmp.iterdir())


def test_invalid_pdf_is_definitive_and_never_retried(tmp_path):
    rig = Rig(tmp_path, converter=FakeConverter([ConversionError("a.pdf não é um PDF válido.", "invalid_pdf")]))
    body = rig.submit()
    rig.ready()
    rig.drain()
    assert rig.broker.peek(topology.QUEUE_DEAD)[0].body == body
    assert rig.broker.depth(topology.QUEUE_RETRY) == 0
    error = [e for e in events(rig.broker) if e["type"] == "error"][0]
    assert error["code"] == "invalid_pdf" and error["retryable"] is False
    assert "não é um PDF válido" in error["reason"]


# ── 5.1.4 falhas passageiras: retry e esgotamento ──────────────────────────────────────────────
def test_transient_failure_publishes_a_retry_copy_then_the_error_then_acks(tmp_path, monkeypatch):
    rig = Rig(tmp_path, converter=FakeConverter([WorkerCrashed("morreu"), None]))
    order: list[str] = []
    real_publish, real_ack = fake_amqp.FakeChannel.basic_publish, fake_amqp.FakeChannel.basic_ack
    monkeypatch.setattr(fake_amqp.FakeChannel, "basic_publish",
                        lambda self, exchange, routing_key, body, properties=None, mandatory=False: (
                            order.append(f"{exchange}:{routing_key}"),
                            real_publish(self, exchange, routing_key, body, properties, mandatory))[1])
    monkeypatch.setattr(fake_amqp.FakeChannel, "basic_ack",
                        lambda self, delivery_tag=0, multiple=False: (
                            order.append("ack"), real_ack(self, delivery_tag, multiple))[1])
    rig.submit()
    order.clear()
    rig.ready()
    rig.run_until(lambda: rig.broker.depth(topology.QUEUE_RETRY) == 1)
    assert order[-3:] == ["artigo2md.retry:convert", "artigo2md.events:job.error", "ack"]
    copy = rig.broker.peek(topology.QUEUE_RETRY)[0]
    assert json.loads(copy.body)["attempt"] == 2 and json.loads(copy.body)["job_id"] == "job-1"
    assert copy.properties.expiration == "2000"  # retry_delay em ms
    error = [e for e in events(rig.broker) if e["type"] == "error"][0]
    assert (error["code"], error["retryable"], error["max_attempts"], error["attempt"]) == \
        ("worker_crashed", True, 3, 1)
    assert rig._unacked() == 0 and rig.broker.depth(topology.QUEUE_DEAD) == 0
    # só depois do atraso o job volta à fila de jobs (expiração + DLX) e termina com `result`
    rig.steps(3)
    assert rig.broker.depth(topology.QUEUE_JOBS) == 0 and len(rig.converter.calls) == 1
    rig.clock.advance(2.0)
    rig.run_until(lambda: rig.codes()[-1] == ("result", None))
    assert [c[2] for c in rig.converter.calls] == [0.0, 0.0] and len(rig.converter.calls) == 2
    assert [e["attempt"] for e in events(rig.broker) if e["type"] == "result"] == [2]


def test_exhausted_attempts_go_to_dead_letters(tmp_path):
    rig = Rig(tmp_path, converter=FakeConverter([WorkerCrashed("a")] * 5), max_attempts=3, retry_delay=1.0)
    rig.submit()
    rig.ready()
    for _ in range(3):
        rig.drain()
        rig.clock.advance(1.0)
    rig.drain()
    errors = [e for e in events(rig.broker) if e["type"] == "error"]
    assert [(e["attempt"], e["retryable"]) for e in errors] == [(1, True), (2, True), (3, False)]
    dead = rig.broker.peek(topology.QUEUE_DEAD)
    assert len(dead) == 1 and dead[0].properties.headers["x-artigo2md-attempts"] == 3
    assert dead[0].properties.headers["x-artigo2md-code"] == "worker_crashed"
    assert json.loads(dead[0].body)["attempt"] == 3  # corpo da última entrega, byte a byte
    assert rig.broker.depth(topology.QUEUE_RETRY) == 0 and len(rig.converter.calls) == 3


def test_max_attempts_one_means_no_retry(tmp_path):
    rig = Rig(tmp_path, converter=FakeConverter([WorkerCrashed("a")]), max_attempts=1)
    rig.submit()
    rig.ready()
    rig.drain()
    assert rig.broker.depth(topology.QUEUE_RETRY) == 0 and rig.broker.depth(topology.QUEUE_DEAD) == 1


@pytest.mark.parametrize("exc,code", [
    (ConversionError("nenhum motor", "error"), "conversion_failed"),
    (WorkerCrashed("morreu"), "worker_crashed"),
    (ConversionTimeout(5), "conversion_timeout"),
    (ValueError("qualquer"), "conversion_failed"),
])
def test_error_classification(exc, code):
    assert classify_error(exc)[0] == code


def test_conversion_timeout_reason_names_the_job_and_the_limit(tmp_path):
    rig = Rig(tmp_path, converter=FakeConverter([ConversionTimeout(7)]), conversion_timeout=7.0)
    rig.submit()
    rig.ready()
    rig.run_until(lambda: rig.broker.depth(topology.QUEUE_RETRY) == 1)
    error = [e for e in events(rig.broker) if e["type"] == "error"][0]
    assert error["code"] == "conversion_timeout" and error["retryable"] is True
    assert error["reason"] == "A conversão do job job-1 excedeu o limite de 7 s e foi encerrada."


def test_write_failure_is_transient(tmp_path, monkeypatch):
    from artigo2md.jobqueue import output

    rig = Rig(tmp_path)
    monkeypatch.setattr(output, "write_markdown",
                        lambda *a: (_ for _ in ()).throw(output.OutputError("write_failed", "disco cheio")))
    rig.submit()
    rig.ready()
    rig.run_until(lambda: rig.broker.depth(topology.QUEUE_RETRY) == 1)
    assert [e["code"] for e in events(rig.broker) if e["type"] == "error"] == ["write_failed"]


# ── 5.1.5 / 5.1.8 sem confirmação ou conexão perdida: sem ack ──────────────────────────────────
def test_ack_lost_after_the_retry_copy_leaves_one_extra_chain_but_never_exceeds_max_attempts(tmp_path, monkeypatch):
    rig = Rig(tmp_path, converter=FakeConverter([WorkerCrashed("a")]), retry_delay=1.0)
    real_ack = fake_amqp.FakeChannel.basic_ack
    lost = {"done": False}

    def flaky_ack(self, delivery_tag=0, multiple=False):
        if not lost["done"]:
            lost["done"] = True
            self.connection.lose()
            raise amqp_exc.StreamLostError("ack perdido")
        return real_ack(self, delivery_tag, multiple)

    monkeypatch.setattr(fake_amqp.FakeChannel, "basic_ack", flaky_ack)
    rig.submit()
    rig.ready()
    rig.run_until(lambda: lost["done"])
    rig.slot.step()  # percebe a perda
    assert rig.slot.state in (slot_mod.RECONNECTING, slot_mod.COLD)
    # o original voltou com attempt antigo e a cópia attempt+1 também existe
    assert rig.broker.depth(topology.QUEUE_RETRY) == 1
    original = rig.broker.peek(topology.QUEUE_JOBS)
    assert len(original) == 1 and original[0].redelivered and json.loads(original[0].body)["attempt"] == 1
    rig.clock.advance(1.0)
    rig.run_until(lambda: rig.codes().count(("result", None)) >= 2, limit=200)
    attempts = [json.loads(body)["attempt"] for _ex, _rk, body, _p in rig.broker.publish_log
                if json.loads(body)["type"] == "job"]
    assert attempts and max(attempts) <= rig.settings.max_attempts
    results = [e for e in events(rig.broker) if e["type"] == "result"]
    assert {e["job_id"] for e in results} == {"job-1"}  # o consumidor deduplica por (job_id, attempt)


@pytest.mark.parametrize("breakage", ["nack", "unroutable"])
def test_unconfirmed_or_unroutable_publish_leaves_the_job_unacked_and_redelivered(rig, breakage, monkeypatch):
    rig.submit()
    rig.ready()
    if breakage == "nack":
        # o evento `result` (depois dos `progress`) não é confirmado
        real = fake_amqp.FakeChannel.basic_publish

        def refuse_result(self, exchange, routing_key, body, properties=None, mandatory=False):
            if routing_key == "job.result":
                raise amqp_exc.NackError([SimpleNamespace(body=body)])
            return real(self, exchange, routing_key, body, properties, mandatory)

        monkeypatch.setattr(fake_amqp.FakeChannel, "basic_publish", refuse_result)
    else:
        rig.broker.bindings[:] = [b for b in rig.broker.bindings if b[1] != topology.QUEUE_EVENTS]
    rig.run_until(lambda: rig.slot.state == slot_mod.RECONNECTING, limit=10)
    assert rig.broker.depth(topology.QUEUE_JOBS) == 1  # job devolvido, sem ack
    assert rig.broker.peek(topology.QUEUE_JOBS)[0].redelivered
    assert rig.broker.depth(topology.QUEUE_DEAD) == 0 and rig.broker.depth(topology.QUEUE_RETRY) == 0
    assert rig._unacked() == 0


def test_memory_falling_between_the_check_and_the_delivery_requeues_without_incrementing_attempt(rig):
    rig.submit()  # a mensagem fica na fila; o slot só consome depois de passar no gate
    rig.slot.step()  # conecta
    rig.slot.step()  # passa no gate (memória alta) e começa a consumir
    assert rig.slot.state == slot_mod.READY
    rig.reader.state.available = 10  # a memória cai antes da entrega
    rig.slot.step()  # recebe a entrega: reavalia o gate, devolve e cancela o consumidor
    assert rig.slot.state == slot_mod.COLD
    assert rig.converter.calls == [] and rig.broker.queues[topology.QUEUE_JOBS].consumers == []
    message = rig.broker.peek(topology.QUEUE_JOBS)[0]
    assert message.redelivered and json.loads(message.body)["attempt"] == 1
    assert rig.broker.depth(topology.QUEUE_RETRY) == 0 and rig.broker.depth(topology.QUEUE_DEAD) == 0
    assert any("Memória livre insuficiente" in line for line in rig.logs)
    assert rig.gate.reserved_mb == 0  # a reserva foi devolvida


# ── 5.1.6 ociosidade ───────────────────────────────────────────────────────────────────────────
def test_converter_unloaded_by_idleness_returns_the_slot_to_cold_and_reapplies_the_gate(rig):
    rig.submit()
    rig.ready()
    rig.drain()
    assert rig.converter.alive and rig.slot.state == slot_mod.READY
    rig.converter.alive = False  # descarregado pelo `--idle-timeout`
    rig.slot.step()
    assert rig.slot.state == slot_mod.COLD and rig.broker.queues[topology.QUEUE_JOBS].consumers == []
    rig.slot.step()  # reaplica o gate e volta a consumir
    assert rig.slot.state == slot_mod.READY


# ── 5.1.7 idempotência e concorrência ──────────────────────────────────────────────────────────
def test_processing_the_same_job_twice_leaves_one_file_with_the_second_content(rig):
    rig.submit()
    rig.ready()
    rig.drain()
    rig.converter.markdown = "# segunda"
    rig.submit()
    rig.drain()
    target = rig.settings.data_dir / "job-1"
    assert [p.name for p in target.iterdir()] == ["artigo.md"]  # nenhum temporário restante
    assert (target / "artigo.md").read_text(encoding="utf-8") == "# segunda"
    assert [e["type"] for e in events(rig.broker)].count("result") == 2


def test_pool_runs_at_most_n_conversions_and_one_unacked_per_slot(tmp_path):
    rig = Rig(tmp_path, workers=2)
    started = {"max": 0, "now": 0}
    lock = threading.Lock()
    unacked_seen = []

    def slow(_converter):
        with lock:
            started["now"] += 1
            started["max"] = max(started["max"], started["now"])
            unacked_seen.append(max(len(ch.unacked) for conn in rig.broker.connections for ch in conn.channels))
        time.sleep(0.05)
        with lock:
            started["now"] -= 1
        return {"markdown": "# ok", "engine": "docling", "report": report()}

    conv_a, conv_b = FakeConverter([slow] * 5), FakeConverter([slow] * 5)
    slots = [rig.new_slot(conv_a, 1), rig.new_slot(conv_b, 2)]
    for i in range(5):
        rig.submit(f"job-{i}")
    stop = threading.Event()

    def loop(s):
        while not stop.is_set():
            s.step()
            time.sleep(0.001)

    threads = [threading.Thread(target=loop, args=(s,), daemon=True) for s in slots]
    for t in threads:
        t.start()
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline and [e["type"] for e in events(rig.broker)].count("result") < 5:
        time.sleep(0.02)
    stop.set()
    for t in threads:
        t.join(5)
    assert [e["type"] for e in events(rig.broker)].count("result") == 5
    assert started["max"] <= 2 and started["max"] >= 1
    assert max(unacked_seen) <= 1  # prefetch 1: nunca mais de uma entrega sem ack por canal


# ── 5.1.9 contrato e credenciais ───────────────────────────────────────────────────────────────
def test_every_published_message_passes_the_contract_validator_and_leaks_no_credential(tmp_path):
    rig = Rig(tmp_path, converter=FakeConverter([WorkerCrashed("a"), ConversionError("x", "invalid_pdf"), None]),
              max_attempts=3, retry_delay=1.0)
    rig.submit("ok-1")
    put_job(rig.broker, b"lixo")
    rig.submit("ok-2")
    rig.ready()
    for _ in range(3):
        rig.drain()
        rig.clock.advance(1.0)
    rig.drain()
    evs = events(rig.broker)
    assert {e["type"] for e in evs} == {"progress", "result", "error"}
    for ev in evs:
        contract.validate_event(ev)
    for _ex, _rk, body, props in rig.broker.publish_log:
        assert SECRET_USER.encode() not in body and SECRET_PASS.encode() not in body
        assert SECRET_PASS not in json.dumps(getattr(props, "headers", None) or {})
    for message in rig.broker.peek(topology.QUEUE_DEAD) + rig.broker.peek(topology.QUEUE_RETRY):
        contract.parse_job(message.body) if message.body != b"lixo" else None
    assert not any(SECRET_PASS in line or SECRET_USER in line for line in rig.logs)


def test_real_pika_properties_carry_json_persistence_headers_and_expiration():
    pytest.importorskip("pika")
    from artigo2md.jobqueue import amqp

    props = amqp.make_properties(headers={"x-artigo2md-code": "invalid_pdf"}, expiration="2000")
    assert props.content_type == "application/json" and props.delivery_mode == 2
    assert props.headers == {"x-artigo2md-code": "invalid_pdf"} and props.expiration == "2000"


# ── 7 / 7b / 7c gate de RAM ────────────────────────────────────────────────────────────────────
def test_low_memory_keeps_the_slot_cold_without_consuming_or_starting_a_converter(tmp_path):
    rig = Rig(tmp_path, available=50)
    rig.submit()
    rig.steps(4)
    assert rig.slot.state == slot_mod.COLD
    assert rig.broker.queues[topology.QUEUE_JOBS].consumers == [] and rig.converter.calls == []
    assert any("Memória livre insuficiente para iniciar mais um conversor" in line for line in rig.logs)
    assert rig.broker.depth(topology.QUEUE_JOBS) == 1  # o job espera na fila
    rig.reader.state.available = 500
    rig.clock.advance(10)  # nova verificação em 10 s
    rig.run_until(lambda: rig.slot.state == slot_mod.READY, limit=5)
    rig.drain()
    assert len(rig.converter.calls) == 1


def test_gate_is_rechecked_only_every_ten_seconds(tmp_path):
    rig = Rig(tmp_path, available=50)
    rig.steps(2)
    calls = rig.reader.state.calls
    rig.steps(5)
    assert rig.reader.state.calls == calls  # sem nova leitura antes de 10 s
    rig.clock.advance(10)
    rig.slot.step()
    assert rig.reader.state.calls > calls


def test_three_cold_slots_with_memory_for_only_one_load_a_single_converter(tmp_path):
    rig = Rig(tmp_path, available=150)  # mínimo 100: cabe exatamente um
    conv = [FakeConverter(), FakeConverter(), FakeConverter()]
    slots = [rig.new_slot(c, i + 1) for i, c in enumerate(conv)]
    for s in slots:
        rig.steps(3, s)
    assert sorted(s.state for s in slots) == [slot_mod.COLD, slot_mod.COLD, slot_mod.READY]
    assert sum("Memória livre insuficiente" in line for line in rig.logs) >= 2
    winner = next(s for s in slots if s.state == slot_mod.READY)
    rig.submit("j1")
    rig.run_until(lambda: any(e["type"] == "result" for e in events(rig.broker)), slot=winner)
    # carga concluída: a reserva foi liberada e os demais reavaliam
    assert rig.gate.reserved_mb == 0
    rig.clock.advance(10)
    for s in slots:
        rig.steps(2, s)
    assert sum(s.state == slot_mod.READY for s in slots) >= 2


def test_reconnecting_slot_does_not_evaluate_the_gate_and_applies_it_after_reconnecting(tmp_path):
    rig = Rig(tmp_path, available=50)
    rig.broker.set_down()
    rig.steps(3)
    assert rig.slot.state == slot_mod.RECONNECTING and rig.reader.state.calls == 0
    rig.broker.set_up()
    rig.run_until(lambda: rig.slot.state == slot_mod.COLD, limit=40)
    assert rig.reader.state.calls == 0  # ainda não avaliou
    rig.slot.step()
    assert rig.reader.state.calls > 0 and rig.slot.state == slot_mod.COLD  # agora aplica o gate (memória baixa)


def test_live_converter_skips_the_gate_after_reconnecting(rig):
    rig.converter.alive = True
    rig.steps(2)
    assert rig.slot.state == slot_mod.READY and rig.reader.state.calls == 0
    assert rig.gate.reserved_mb == 0


# ── reconexão: backoff e erros fatais ──────────────────────────────────────────────────────────
def test_reconnection_backoff_doubles_from_one_to_fifteen_seconds_with_clean_messages(rig):
    rig.broker.set_down()
    rig.run_until(lambda: len(rig.logs) >= 6, limit=400)
    delays = [float(line.rsplit("Nova tentativa em ", 1)[1].split(" s")[0]) for line in rig.logs]
    assert delays[:6] == [1, 2, 4, 8, 15, 15]
    assert all(line.startswith("RabbitMQ inacessível em 127.0.0.1:5672.") and "sh stack/up.sh" in line
               for line in rig.logs)
    assert not any("Traceback" in line or SECRET_PASS in line or SECRET_USER in line for line in rig.logs)
    rig.broker.set_up()
    rig.run_until(lambda: rig.slot.state == slot_mod.COLD, limit=60)
    assert rig.slot._backoff == slot_mod.BACKOFF_START  # a próxima queda recomeça em 1 s


def test_lost_connection_while_ready_goes_to_reconnecting_and_resumes(rig):
    rig.ready()
    rig.broker.set_down()
    rig.slot.step()
    assert rig.slot.state == slot_mod.RECONNECTING
    rig.broker.set_up()
    rig.run_until(lambda: rig.slot.state == slot_mod.READY, limit=40)


def test_refused_credentials_are_fatal_without_a_retry_loop_and_without_the_secret(tmp_path):
    class ProbableAuthenticationError(Exception):
        pass

    rig = Rig(tmp_path)
    attempts = []

    def refuse(_settings):
        attempts.append(1)
        raise ProbableAuthenticationError("auth")

    rig.slot._connect = refuse
    rig.slot.step()
    assert rig.slot.state == slot_mod.STOPPED and len(attempts) == 1
    assert isinstance(rig.slot.fatal, CredentialsRefused)
    assert rig.slot.fatal.message == ("O RabbitMQ recusou as credenciais. Revise RABBITMQ_DEFAULT_USER e "
                                      "RABBITMQ_DEFAULT_PASS (origem: ambiente).")
    assert SECRET_PASS not in rig.slot.fatal.message and rig.logs == []


def test_incompatible_topology_is_fatal_and_changes_nothing(tmp_path):
    rig = Rig(tmp_path)
    ch = rig.broker.connection().channel()
    ch.exchange_declare(topology.EXCHANGE_JOBS, "topic", durable=True)  # difere do esperado (`direct`)
    before = dict(rig.broker.exchanges)
    rig.slot.step()
    assert rig.slot.state == slot_mod.STOPPED and isinstance(rig.slot.fatal, TopologyMismatch)
    assert rig.slot.fatal.message.startswith("A fila/exchange artigo2md.jobs já existe no RabbitMQ")
    assert rig.broker.exchanges == before


# ── encerramento ───────────────────────────────────────────────────────────────────────────────
def test_stop_request_cancels_consumption_and_closes_the_connection(rig):
    rig.ready()
    rig.slot.request_stop()
    rig.slot.step()
    assert rig.slot.state == slot_mod.STOPPED and rig.broker.connections == []
    assert rig.broker.queues[topology.QUEUE_JOBS].consumers == []


def test_stop_request_during_a_job_lets_it_finish_then_stops(tmp_path):
    rig = Rig(tmp_path)

    def stop_midway(_conv):
        rig.slot.request_stop()
        return {"markdown": "# meio", "engine": "docling", "report": report()}

    rig.converter.script = [stop_midway]
    rig.submit()
    rig.ready()
    rig.run_until(lambda: rig.slot.state == slot_mod.STOPPED, limit=10)
    assert (rig.settings.data_dir / "job-1" / "artigo.md").exists()
    assert [e["type"] for e in events(rig.broker)][-1] == "result" and rig.broker.depth(topology.QUEUE_JOBS) == 0


def test_abandon_kills_the_child_and_returns_the_job_without_ack(tmp_path):
    rig = Rig(tmp_path)
    rig.converter.script = [rig.converter.block]
    rig.submit()
    rig.ready()
    thread = threading.Thread(target=rig.slot.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not rig.converter.calls and time.monotonic() < deadline:
        time.sleep(0.01)
    assert rig.converter.calls
    rig.slot.request_stop()
    rig.slot.abandon()
    thread.join(5)
    assert not thread.is_alive() and rig.slot.state == slot_mod.STOPPED and rig.converter.kills == 1
    assert rig.broker.depth(topology.QUEUE_JOBS) == 1 and rig.broker.peek(topology.QUEUE_JOBS)[0].redelivered
    assert rig.broker.depth(topology.QUEUE_RETRY) == 0 and rig.broker.depth(topology.QUEUE_DEAD) == 0
    assert [e["type"] for e in events(rig.broker)].count("error") == 0
    assert any(line == "Job job-1 devolvido à fila." for line in rig.logs)
