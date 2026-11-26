"""Serviço da fila (tarefa 5.2 da `conversion-queue`): pool, sinais, prazo de encerramento e saída sem órfãos.

Corretor AMQP falso e conversores falsos; filhos `spawn` reais só onde o teste precisa provar que o processo
de conversão não sobrevive (`tests/fake_child.py`). Sem Docker e sem o extra `queue`.
"""
from __future__ import annotations

import json
import multiprocessing as mp
import os
import signal
import threading
import time

import pytest

import fake_child
from queue_support import (SECRET_PASS, SECRET_USER, FakeConverter, PacedBroker, events, fake_properties, job_body,
                           make_settings, memory_reader, put_job, report, wait_for)
from artigo2md import worker as worker_mod
from artigo2md.jobqueue import settings as settings_mod, slot as slot_mod, topology
from artigo2md.jobqueue.memory import MemoryGate
from artigo2md.jobqueue.service import Service
from artigo2md.worker import ConversionWorker


class Harness:
    def __init__(self, tmp_path, workers=1, converters=None, **overrides):
        self.tmp = tmp_path
        self.pdf = tmp_path / "artigo.pdf"
        self.pdf.write_bytes(b"%PDF-1.4 falso")
        self.broker = PacedBroker()
        self.logs: list[str] = []
        self.settings = make_settings(tmp_path / "dados", workers=workers, **overrides)
        self.settings.data_dir.mkdir(exist_ok=True)
        self.converters = converters or [FakeConverter() for _ in range(workers)]
        queue = list(self.converters)
        self.service = Service(
            self.settings,
            gate=MemoryGate(self.settings.min_free_memory_mb, memory_reader()),
            converter_factory=lambda _s: queue.pop(0),
            connect=lambda _s: self.broker.connection(),
            properties=fake_properties,
            log=self.logs.append,
            poll_interval=0.01,
        )
        self.exit_code: int | None = None
        self.thread: threading.Thread | None = None

    def submit(self, job_id="job-1"):
        put_job(self.broker, job_body(job_id, str(self.pdf)))

    def start(self):
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        self.exit_code = self.service.run(install_signals=False)

    def stop(self, timeout=15):
        self.service.request_shutdown()
        self.thread.join(timeout)
        assert not self.thread.is_alive(), "o serviço não terminou"
        return self.exit_code

    def results(self):
        return [e for e in events(self.broker) if e["type"] == "result"]


def test_service_with_a_single_slot_converts_and_exits_zero(tmp_path):
    h = Harness(tmp_path, workers=1)
    h.start()
    h.submit()
    assert wait_for(lambda: len(h.results()) == 1)
    assert h.stop() == 0 and len(h.service.slots) == 1
    assert h.broker.connections == [] and h.broker.depth(topology.QUEUE_JOBS) == 0
    assert h.converters[0].shutdowns == 1


def test_pool_of_n_slots_has_n_independent_connections_and_converters(tmp_path):
    h = Harness(tmp_path, workers=3)
    h.start()
    assert wait_for(lambda: len(h.broker.connections) == 3
                    and all(s.state == slot_mod.READY for s in h.service.slots))
    assert len({id(c) for c in h.converters}) == 3
    assert all(len(c.channels) == 1 for c in h.broker.connections)
    assert h.stop() == 0 and all(c.shutdowns == 1 for c in h.converters)
    assert h.broker.connections == []


def test_sigterm_with_a_job_that_finishes_inside_the_deadline_lets_it_complete(tmp_path):
    h = Harness(tmp_path, shutdown_timeout=5.0)
    release = threading.Event()
    started = threading.Event()

    def slow(conv):
        started.set()
        release.wait(5)
        return {"markdown": "# ok", "engine": "docling", "report": report()}

    h.converters[0].script = [slow]
    h.start()
    h.submit()
    assert started.wait(5)
    h.service.request_shutdown()  # equivalente a SIGTERM
    assert wait_for(lambda: any("Encerrando: aguardando o job em andamento por até 5 s..." in l for l in h.logs))
    release.set()
    h.thread.join(10)
    assert h.exit_code == 0 and len(h.results()) == 1
    assert h.broker.depth(topology.QUEUE_JOBS) == 0 and h.broker.connections == []


def test_sigterm_after_the_deadline_kills_the_child_and_returns_the_job_without_ack(tmp_path):
    h = Harness(tmp_path, shutdown_timeout=0.2)
    h.converters[0].script = [h.converters[0].block]
    h.start()
    h.submit()
    assert wait_for(lambda: len(h.converters[0].calls) == 1)
    assert h.stop() == 0
    assert h.converters[0].kills >= 1 and h.converters[0].shutdowns == 1
    assert h.broker.depth(topology.QUEUE_JOBS) == 1 and h.broker.peek(topology.QUEUE_JOBS)[0].redelivered
    assert not [e for e in events(h.broker) if e["type"] in ("error", "result")]
    assert "Job job-1 devolvido à fila." in h.logs
    assert any(l.startswith("Encerrando: aguardando o job em andamento por até 0.2 s") for l in h.logs)
    assert h.broker.connections == []


def test_no_conversion_child_survives_the_exit(tmp_path, monkeypatch):
    """SC-008 com filhos `spawn` reais: o filho que nunca responde é encerrado no prazo."""
    monkeypatch.setattr(worker_mod, "_child_main", fake_child.hang_main)
    workers = [ConversionWorker(idle_timeout=0) for _ in range(2)]
    h = Harness(tmp_path, workers=2, converters=workers, shutdown_timeout=0.3)
    h.start()
    h.submit("job-a")
    h.submit("job-b")
    assert wait_for(lambda: len([p for p in mp.active_children() if p.name == "artigo2md-worker"]) == 2, 30)
    assert h.stop(30) == 0
    assert [p for p in mp.active_children() if p.name == "artigo2md-worker"] == []
    assert all(not w.alive for w in workers)
    assert h.broker.depth(topology.QUEUE_JOBS) == 2  # os dois jobs voltaram à fila


@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGINT])
def test_signal_handlers_stop_the_service_with_zero_and_are_restored(tmp_path, sig):
    h = Harness(tmp_path)
    before = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM)}
    timer = threading.Timer(0.3, lambda: signal.raise_signal(sig))
    timer.start()
    try:
        assert h.service.run() == 0  # instala os manipuladores no thread principal
    finally:
        timer.cancel()
    assert {s: signal.getsignal(s) for s in before} == before
    assert h.broker.connections == []


@pytest.mark.parametrize("error_name,expected_code", [("ProbableAuthenticationError", 4), ("AuthenticationError", 4)])
def test_refused_credentials_exit_4_without_a_retry_loop_and_without_the_secret(tmp_path, error_name, expected_code):
    attempts = []
    exc_class = type(error_name, (Exception,), {})

    def refuse(_settings):
        attempts.append(1)
        raise exc_class("recusado")

    h = Harness(tmp_path, workers=2)
    h.service = Service(h.settings, gate=MemoryGate(0, memory_reader()), converter_factory=lambda _s: FakeConverter(),
                        connect=refuse, properties=fake_properties, log=h.logs.append, poll_interval=0.01)
    assert h.service.run(install_signals=False) == expected_code
    assert len(attempts) == 2  # uma tentativa por slot, sem laço agressivo
    assert h.logs == ["O RabbitMQ recusou as credenciais. Revise RABBITMQ_DEFAULT_USER e "
                      "RABBITMQ_DEFAULT_PASS (origem: ambiente)."]
    assert not any(SECRET_PASS in l or SECRET_USER in l for l in h.logs)


def test_incompatible_topology_exits_5(tmp_path):
    h = Harness(tmp_path)
    h.broker.connection().channel().queue_declare(topology.QUEUE_JOBS, durable=True,
                                                  arguments={"x-queue-type": "quorum"})
    assert h.service.run(install_signals=False) == 5
    assert h.logs[-1].startswith("A fila/exchange artigo2md.jobs.convert já existe no RabbitMQ")


def test_broker_down_at_startup_is_not_an_exit_error(tmp_path):
    h = Harness(tmp_path)
    h.broker.set_down()
    h.start()
    assert wait_for(lambda: any(l.startswith("RabbitMQ inacessível em 127.0.0.1:5672.") for l in h.logs))
    assert h.thread.is_alive()
    h.broker.set_up()
    h.submit()
    assert wait_for(lambda: len(h.results()) == 1, 20)  # volta sozinho (1 s de backoff)
    assert h.stop() == 0
    assert not any("Traceback" in l or SECRET_PASS in l for l in h.logs)


def test_pool_warning_is_logged_at_startup_when_the_pool_exceeds_total_memory(tmp_path):
    h = Harness(tmp_path, workers=2, min_free_memory_mb=9000)  # 2 x 9000 > 16000 (total do leitor falso)
    h.start()
    assert wait_for(lambda: h.logs and h.logs[0].startswith("Aviso: 2 conversores podem esgotar a memória"))
    h.stop()


# ── 5.2.6 credenciais não chegam ao filho ──────────────────────────────────────────────────────
def test_spawned_child_does_not_see_the_rabbitmq_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("RABBITMQ_DEFAULT_USER", SECRET_USER)
    monkeypatch.setenv("RABBITMQ_DEFAULT_PASS", SECRET_PASS)
    (tmp_path / "dados").mkdir()
    monkeypatch.chdir(tmp_path)
    settings_mod.load_settings(data_dir=tmp_path / "dados", workers=1, min_free_memory_mb=0)
    assert "RABBITMQ_DEFAULT_USER" not in os.environ and "RABBITMQ_DEFAULT_PASS" not in os.environ
    monkeypatch.setattr(worker_mod, "_child_main", fake_child.env_main)
    conv = ConversionWorker(idle_timeout=0)
    try:
        seen = json.loads(conv.convert("/x/a.pdf", "auto", lambda _m: None)["markdown"])
    finally:
        conv.kill()
        conv.shutdown()
    assert seen == {"RABBITMQ_DEFAULT_USER": None, "RABBITMQ_DEFAULT_PASS": None}


# ── 5.2.7 reconnecting + gate + ociosidade ─────────────────────────────────────────────────────
def test_reconnecting_slot_with_low_memory_follows_the_gate_rule_and_idle_converter_is_unloaded(tmp_path, monkeypatch):
    monkeypatch.setattr(worker_mod, "_child_main", fake_child.echo_main)
    converter = ConversionWorker(idle_timeout=0.3)
    try:
        converter.convert("/x/a.pdf", "auto", lambda _m: None)
        assert converter.alive
        reader = memory_reader(available_mb=10)  # baixa: abaixo do mínimo de 100 MB
        broker = PacedBroker()
        broker.set_down()
        slot = slot_mod.Slot(1, make_settings(tmp_path), MemoryGate(100, reader), converter,
                             connect=lambda _s: broker.connection(), properties=fake_properties,
                             log=lambda _m: None, poll_interval=0.01)
        slot.step()
        assert slot.state == slot_mod.RECONNECTING and reader.state.calls == 0  # não avalia o gate
        assert wait_for(lambda: not converter.alive, 10)  # descarregado após o limite de ociosidade
        broker.set_up()
        slot._next_attempt_at = 0
        slot.step()
        assert slot.state == slot_mod.COLD
        slot.step()  # agora aplica o gate: memória baixa, conversor frio -> não consome
        assert reader.state.calls > 0 and slot.state == slot_mod.COLD
        assert broker.queues[topology.QUEUE_JOBS].consumers == []
    finally:
        converter.kill()
        converter.shutdown()
