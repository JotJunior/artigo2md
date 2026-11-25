"""Apoio aos testes do slot e do serviço: conversor falso, publicação de jobs e leitura do que o worker publicou."""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import fake_amqp
from artigo2md.jobqueue import contract, topology
from artigo2md.jobqueue.memory import MemoryReading
from artigo2md.jobqueue.settings import Credentials, WorkerSettings
from artigo2md.router import Attempt, ConversionResult, Quality

SECRET_USER = "usr-nunca-vazar"
SECRET_PASS = "senha-nunca-vazar"


def make_settings(data_dir: Path, **overrides) -> WorkerSettings:
    values = dict(data_dir=data_dir, workers=1, min_free_memory_mb=100, port=5672,
                  credentials=Credentials(SECRET_USER, SECRET_PASS, "ambiente"),
                  retry_delay=2.0, max_attempts=3, idle_timeout=0.0, shutdown_timeout=1.0)
    values.update(overrides)
    return WorkerSettings(**values)


def fake_properties(headers=None, expiration=None):
    """Substituto de `pika.BasicProperties` para rodar sem o extra `queue`."""
    return SimpleNamespace(headers=headers, expiration=expiration, delivery_mode=2,
                           content_type=contract.CONTENT_TYPE)


def report() -> dict:
    analysis = {"pages": 2, "text_pages": 2, "scanned_pages": 0, "total_chars": 900, "table_hints": 1,
                "formula_score": 0, "article_score": 4, "fully_scanned": False}
    quality = Quality(score=100.0, accepted=True, tables_found=1, tables_expected=1,
                      chars_per_page=450.0, issues=[])
    return ConversionResult(source="/tmp/a.pdf", markdown="# t", engine="docling", analysis=analysis,
                            attempts=[Attempt("docling", 1.2, quality)]).report()


class FakeConverter:
    """Imita a interface de `ConversionWorker` usada pelo slot. `script`: um item por chamada
    (exceção = levanta; função = devolve o payload; `None` = sucesso padrão)."""

    def __init__(self, script=None, markdown="# ok"):
        self.alive = False
        self.script = list(script or [])
        self.markdown = markdown
        self.calls: list[tuple] = []
        self.kills = 0
        self.shutdowns = 0
        self.released = threading.Event()  # `kill()` libera as chamadas bloqueadas em `block`
        self.active = 0
        self.max_active = 0
        self._lock = threading.Lock()

    def convert(self, path, engine, progress, tick=None, timeout=0):
        self.calls.append((path, engine, timeout))
        with self._lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            if not self.alive:
                progress("Carregando o conversor")
                self.alive = True
            progress("Convertendo")
            if tick is not None:
                tick()
            step = self.script.pop(0) if self.script else None
            if isinstance(step, BaseException):
                raise step
            if callable(step):
                return step(self)
            return {"markdown": self.markdown, "engine": "docling", "report": report()}
        finally:
            with self._lock:
                self.active -= 1

    def block(self, _converter=None):
        """Item de `script`: espera `kill()` e então morre como o filho encerrado (`WorkerCrashed`)."""
        from artigo2md.worker import WorkerCrashed

        self.released.wait(10)
        raise WorkerCrashed("O processo de conversão foi encerrado.")

    def kill(self):
        self.kills += 1
        self.alive = False
        self.released.set()

    def shutdown(self):
        self.shutdowns += 1
        self.alive = False


def memory_reader(available_mb: float = 10_000, total_mb: float = 16_000):
    state = SimpleNamespace(available=available_mb, total=total_mb, calls=0)

    def read() -> MemoryReading:
        state.calls += 1
        return MemoryReading(state.available, state.total)

    read.state = state
    return read


def job_body(job_id="job-1", pdf_path="/tmp/x.pdf", **extra) -> bytes:
    msg = contract.build_job(job_id, pdf_path)
    msg.update(extra)
    return json.dumps(msg).encode("utf-8")


def put_job(broker: fake_amqp.FakeBroker, body: bytes) -> None:
    """Publica um job em `artigo2md.jobs` como um produtor externo."""
    connection = broker.connection()
    channel = connection.channel()
    topology.declare(channel)
    channel.basic_publish(topology.EXCHANGE_JOBS, topology.RK_CONVERT, body, fake_properties())
    connection.close()


def events(broker: fake_amqp.FakeBroker) -> list[dict]:
    return [json.loads(body) for ex, _rk, body, _p in broker.publish_log if ex == topology.EXCHANGE_EVENTS]


def published(broker: fake_amqp.FakeBroker, exchange: str) -> list[tuple[str, bytes, object]]:
    return [(rk, body, props) for ex, rk, body, props in broker.publish_log if ex == exchange]


class PacedBroker(fake_amqp.FakeBroker):
    """Corretor falso cujas conexões bloqueiam um pouco em `process_data_events`, como o cliente real: slots em
    threads não giram em laço sem espera durante o teste."""

    def connection(self, parameters=None):
        conn = super().connection(parameters)
        original = conn.process_data_events

        def paced(time_limit=0):
            time.sleep(min(time_limit or 0, 0.005))
            return original(time_limit)

        conn.process_data_events = paced
        return conn


def wait_for(predicate, timeout=10.0, interval=0.01) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()
