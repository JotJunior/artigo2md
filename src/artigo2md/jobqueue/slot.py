"""Slot do worker: máquina de estados de um consumidor com 1 conexão, 1 canal e 1 conversor (FR-003/005/010-012).

Estados (`data-model.md`, §Slot): `cold` (sem consumir; avalia o gate de RAM), `ready` (consumindo, `prefetch_count=1`),
`busy` (processando uma entrega), `reconnecting` (sem conexão; backoff 1 s dobrando até 15 s), `draining`
(encerrando) e `stopped`.

O slot roda numa thread própria (`run`), mas é construído sobre `step()`, que faz **uma** iteração limitada
(no máximo uma espera de `poll_interval` s no laço de eventos do cliente, nunca um laço sem espera), de modo
que os testes dirigem a máquina de estados sem threads nem relógio real. A entrega recebida pelo callback do
consumidor é só guardada; quem a processa é o `step`, fora do callback.

Ordem de garantia de cada job (`contracts/queue-messages.md`, §Garantias de entrega): validar → (gate de RAM)
→ converter → gravar o Markdown de forma atômica → publicar o evento com confirmação do broker → `ack`.
Qualquer falha de publicação ou de conexão antes do `ack` deixa o job sem `ack` e ele é reentregue.
"""
from __future__ import annotations

import json
import sys
import threading
import time
from typing import Any, Callable

from ..worker import ConversionError, ConversionTimeout, WorkerCrashed
from . import amqp, contract, output, topology
from .contract import ERROR_CODES, ContractError, Job
from .memory import RECHECK_SECONDS, MemoryGate, Reservation
from .output import OutputError
from .settings import WorkerSettings
from .topology import TopologyMismatch

# estados
COLD = "cold"
READY = "ready"
BUSY = "busy"
RECONNECTING = "reconnecting"
DRAINING = "draining"
STOPPED = "stopped"

POLL_SECONDS = 1.0  # espera máxima por iteração no laço de eventos (heartbeat sem espera ativa)
BACKOFF_START = 1.0
BACKOFF_MAX = 15.0
MAX_REASON = 1000  # tamanho máximo do `reason` gravado no cabeçalho de mensagens mortas

CODE_HEADER = "x-artigo2md-code"
REASON_HEADER = "x-artigo2md-reason"
ATTEMPTS_HEADER = "x-artigo2md-attempts"
FAILED_AT_HEADER = "x-artigo2md-failed-at"

BROKER_DOWN = (
    "RabbitMQ inacessível em {host}:{port}. Para subir a infraestrutura: sh stack/up.sh "
    "(requer Docker; veja stack/.env.example). Nova tentativa em {delay:g} s."
)
CONNECTION_LOST = "A conexão com o RabbitMQ foi perdida. Nova tentativa em {delay:g} s."


class CredentialsRefused(Exception):
    """O broker recusou as credenciais (código de saída 4). `message` nunca contém valor de credencial."""

    def __init__(self, origin: str):
        self.message = (
            "O RabbitMQ recusou as credenciais. Revise RABBITMQ_DEFAULT_USER e RABBITMQ_DEFAULT_PASS "
            f"(origem: {origin})."
        )
        super().__init__(self.message)


class _Abandoned(Exception):
    """Encerramento por prazo: o job em andamento volta à fila sem `ack`."""

    def __init__(self, job_id: str):
        super().__init__(job_id)
        self.job_id = job_id


def default_log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def classify_error(exc: BaseException) -> tuple[str, str]:
    """Falha da conversão → (código do contrato, motivo em português)."""
    if isinstance(exc, ConversionError):
        return ("invalid_pdf" if exc.kind == "invalid_pdf" else "conversion_failed"), str(exc)
    if isinstance(exc, WorkerCrashed):
        return "worker_crashed", str(exc)
    if isinstance(exc, ConversionTimeout):
        return "conversion_timeout", str(exc)
    detail = str(exc)[:800]
    return "conversion_failed", f"Falha inesperada na conversão: {detail}" if detail else (
        f"Falha inesperada na conversão ({type(exc).__name__}).")


def _peek_attempt(body: bytes) -> int:
    """`attempt` de um corpo que não passou na validação, só quando é seguro lê-lo; senão 1."""
    try:
        if len(body) <= contract.MAX_BODY_BYTES and contract.json_depth(body) <= contract.MAX_JSON_DEPTH:
            value = json.loads(body.decode("utf-8")).get("attempt", 1)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 1:
                return value
    except (ValueError, AttributeError):
        pass
    return 1


class Slot:
    def __init__(
        self,
        index: int,
        settings: WorkerSettings,
        gate: MemoryGate,
        converter: Any,
        *,
        connect: Callable[[WorkerSettings], Any] | None = None,
        properties: Callable[..., Any] | None = None,
        log: Callable[[str], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
        wait: Callable[[float], Any] | None = None,
        poll_interval: float = POLL_SECONDS,
    ):
        self.index = index
        self.settings = settings
        self.gate = gate
        self.converter = converter  # `ConversionWorker` (ou equivalente: `alive`, `convert`, `kill`, `shutdown`)
        self._connect = connect or amqp.open_connection
        self._properties = properties or amqp.make_properties
        self._log = log or default_log
        self._clock = clock
        self._poll_interval = poll_interval

        self._stop = threading.Event()
        self._abandon = threading.Event()
        self._wait = wait or self._stop.wait

        self.state = RECONNECTING
        self.fatal: BaseException | None = None
        self.current_job_id: str | None = None

        self._conn: Any = None
        self._channel: Any = None
        self._consumer_tag: str | None = None
        self._delivery: tuple[int, Any, bytes] | None = None
        self._reservation: Reservation | None = None
        self._had_converter = False
        self._recheck_at = 0.0
        self._next_attempt_at = 0.0
        self._backoff = BACKOFF_START

    # ── controle externo (thread principal) ─────────────────────────────────────────────────────
    def request_stop(self) -> None:
        """Pede o encerramento: o job em andamento termina; depois o slot para de consumir e fecha."""
        self._stop.set()

    def abandon(self) -> None:
        """Prazo esgotado: encerra o filho de conversão; o job volta à fila sem `ack` (FR-013)."""
        self._abandon.set()
        self.converter.kill()

    # ── laço ────────────────────────────────────────────────────────────────────────────────────
    def run(self) -> None:
        try:
            while self.state != STOPPED:
                self.step()
        except BaseException as exc:  # defeito inesperado: o serviço lê `fatal` e encerra com mensagem
            self.fatal = exc
        finally:
            self._drop_connection()
            self.state = STOPPED

    def step(self) -> None:
        """Uma iteração da máquina de estados (nunca bloqueia além de `poll_interval` s fora de um job)."""
        if self.state == STOPPED:
            return
        try:
            if self._stop.is_set():
                self._finish()
            elif self.state == RECONNECTING:
                self._step_reconnecting()
            elif self.state == COLD:
                self._step_cold()
            elif self.state == READY:
                self._step_ready()
        except _Abandoned as abandoned:
            self._drop_connection()
            self.state = STOPPED
            self._log(f"Job {abandoned.job_id} devolvido à fila.")
        except (CredentialsRefused, TopologyMismatch) as exc:
            self.fatal = exc
            self._drop_connection()
            self.state = STOPPED
        except Exception as exc:
            if not amqp.is_connection_problem(exc):
                raise
            self._lost_connection()

    # ── reconnecting ────────────────────────────────────────────────────────────────────────────
    def _lost_connection(self) -> None:
        self._drop_connection()
        self.state = RECONNECTING
        self._next_attempt_at = self._clock()  # a primeira nova tentativa é imediata

    def _step_reconnecting(self) -> None:
        remaining = self._next_attempt_at - self._clock()
        if remaining > 0:
            self._wait(min(remaining, self._poll_interval))
            return
        try:
            self._open()
        except TopologyMismatch:
            raise
        except Exception as exc:
            if amqp.is_auth_error(exc):
                raise CredentialsRefused(self.settings.credentials.origin) from None
            if not amqp.is_connection_problem(exc):
                raise
            delay = self._backoff
            self._backoff = min(self._backoff * 2, BACKOFF_MAX)
            self._next_attempt_at = self._clock() + delay
            self._log(BROKER_DOWN.format(host=self.settings.host, port=self.settings.port, delay=delay))
            return
        self._backoff = BACKOFF_START
        self.state = COLD

    def _open(self) -> None:
        connection = self._connect(self.settings)
        try:
            channel = connection.channel()
            channel.basic_qos(prefetch_count=1)
            channel.confirm_delivery()
            topology.declare(channel)
        except BaseException:
            _quiet_close(connection)
            raise
        self._conn, self._channel = connection, channel

    def _drop_connection(self) -> None:
        """Fecha a conexão sem `ack`: o broker devolve à fila o que estava em andamento."""
        conn = self._conn
        self._conn = self._channel = None
        self._consumer_tag = None
        self._delivery = None
        self._release_reservation()
        if conn is not None:
            _quiet_close(conn)

    # ── cold ────────────────────────────────────────────────────────────────────────────────────
    def _step_cold(self) -> None:
        if self._conn is None:
            self.state = RECONNECTING
            return
        if self._clock() < self._recheck_at:
            self._poll()
            return
        if not self.converter.alive:  # conversor já vivo não reavalia o gate
            result = self.gate.try_acquire()
            if not result.allowed:
                self._log(result.message)
                self._recheck_at = self._clock() + RECHECK_SECONDS
                self._poll()
                return
            self._reservation = result.reservation
        self._consumer_tag = self._channel.basic_consume(topology.QUEUE_JOBS, self._on_message)
        self.state = READY

    def _poll(self) -> None:
        self._conn.process_data_events(time_limit=self._poll_interval)

    # ── ready / busy ────────────────────────────────────────────────────────────────────────────
    def _on_message(self, _channel, method, properties, body) -> None:
        if self._delivery is not None:  # não ocorre com prefetch 1; devolve por segurança
            self._channel.basic_nack(delivery_tag=method.delivery_tag, requeue=True)
            return
        self._delivery = (method.delivery_tag, properties, body)

    def _step_ready(self) -> None:
        self._poll()
        if self._delivery is not None:
            self._handle()
        elif self._had_converter and not self.converter.alive:
            # conversor descarregado por ociosidade (ou morto): volta a `cold` e reaplica o gate
            self._cancel_consumer()
            self._had_converter = False
            self._release_reservation()
            self.state = COLD

    def _cancel_consumer(self) -> None:
        """Para de consumir; devolve à fila o que já havia chegado e não foi processado."""
        tag, self._consumer_tag = self._consumer_tag, None
        pending = []
        if self._delivery is not None:
            pending.append(self._delivery[0])
            self._delivery = None
        if tag is not None:
            for method, _props, _body in self._channel.basic_cancel(tag) or []:
                pending.append(method.delivery_tag)
        for delivery_tag in pending:
            self._channel.basic_nack(delivery_tag=delivery_tag, requeue=True)

    def _release_reservation(self) -> None:
        reservation, self._reservation = self._reservation, None
        if reservation is not None:
            reservation.release()

    def _finish(self) -> None:
        self.state = DRAINING
        if self._conn is not None:
            try:
                self._cancel_consumer()
            except Exception as exc:
                if not amqp.is_connection_problem(exc):
                    raise
        self._drop_connection()
        self.state = STOPPED

    def _handle(self) -> None:
        delivery_tag, _properties, body = self._delivery
        self._delivery = None
        self.state = BUSY
        try:
            if self._process(delivery_tag, body) == "requeued":
                self._recheck_at = self._clock() + RECHECK_SECONDS
                self.state = COLD
            else:
                self.state = READY
        finally:
            self.current_job_id = None
            if self.state == BUSY:  # saída por exceção: quem trata é `step`
                self.state = READY

    # ── processamento de um job ─────────────────────────────────────────────────────────────────
    def _process(self, delivery_tag: int, body: bytes) -> str:
        try:
            job = contract.parse_job(body)
        except ContractError as exc:
            self._fail(delivery_tag, body, exc.job_id, _peek_attempt(body), exc.code, exc.reason, job=None)
            return "done"
        self.current_job_id = job.job_id
        try:
            output.check_pdf_path(job.pdf_path)
            output.validate_job_id(job.job_id)
            output.derive_stem(job.pdf_path)
        except OutputError as exc:
            self._fail(delivery_tag, body, job.job_id, job.attempt, exc.code, exc.reason, job=job)
            return "done"

        if not self.converter.alive:
            check = self.gate.still_ok(self._reservation)
            if not check.allowed:
                # memória caiu entre a checagem e a entrega: devolve sem incrementar `attempt`
                self._log(check.message)
                self._channel.basic_nack(delivery_tag=delivery_tag, requeue=True)
                self._cancel_consumer()
                self._release_reservation()
                self._had_converter = False
                return "requeued"

        self._had_converter = True
        try:
            payload = self.converter.convert(
                job.pdf_path,
                job.engine,
                progress=lambda message: self._publish_event(
                    contract.build_progress(job.job_id, job.attempt, message)),
                tick=lambda: self._conn.process_data_events(time_limit=0),
                timeout=self.settings.conversion_timeout,
            )
        except Exception as exc:
            if self._abandon.is_set():
                raise _Abandoned(job.job_id) from None
            if amqp.is_amqp_error(exc):
                raise
            code, reason = classify_error(exc)
            if code == "conversion_timeout":
                reason = (f"A conversão do job {job.job_id} excedeu o limite de "
                          f"{self.settings.conversion_timeout:g} s e foi encerrada.")
            self._release_reservation()
            self._fail(delivery_tag, body, job.job_id, job.attempt, code, reason, job=job)
            return "done"
        self._release_reservation()  # o conversor terminou de carregar: libera a reserva do gate

        try:
            path = output.write_markdown(self.settings.data_dir, job.job_id, job.pdf_path, payload["markdown"])
        except OutputError as exc:
            self._fail(delivery_tag, body, job.job_id, job.attempt, exc.code, exc.reason, job=job)
            return "done"
        self._publish_event(contract.build_result(
            job.job_id, job.attempt, str(path), payload["engine"], payload["report"]))
        self._channel.basic_ack(delivery_tag=delivery_tag)
        return "done"

    def _fail(self, delivery_tag: int, body: bytes, job_id: str | None, attempt: int, code: str,
              reason: str, job: Job | None) -> None:
        """Falha passageira com tentativas restantes → retry; caso contrário → mensagens mortas."""
        max_attempts = self.settings.max_attempts
        retryable = job is not None and ERROR_CODES[code] and attempt < max_attempts
        if retryable:
            copy = contract.encode(contract.build_job(job.job_id, job.pdf_path, job.engine, attempt + 1))
            expiration = str(max(1, round(self.settings.retry_delay * 1000)))
            self._publish(topology.EXCHANGE_RETRY, topology.RK_CONVERT, copy,
                          self._properties(expiration=expiration))
        else:
            headers = {
                CODE_HEADER: code,
                REASON_HEADER: reason[:MAX_REASON],
                ATTEMPTS_HEADER: attempt,
                FAILED_AT_HEADER: contract.utc_now_iso(),
            }
            self._publish(topology.EXCHANGE_DEAD, topology.RK_DEAD, body, self._properties(headers=headers))
        if job_id is not None:  # sem `job_id` válido não há a quem atribuir o evento
            self._publish_event(contract.build_error(job_id, attempt, code, reason, retryable, max_attempts))
        self._channel.basic_ack(delivery_tag=delivery_tag)

    # ── publicação ──────────────────────────────────────────────────────────────────────────────
    def _publish(self, exchange: str, routing_key: str, body: bytes, properties: Any) -> None:
        """Publica com confirmação do broker e `mandatory`; recusa ou não roteável levanta (sem `ack`)."""
        self._channel.basic_publish(
            exchange=exchange, routing_key=routing_key, body=body, properties=properties, mandatory=True)

    def _publish_event(self, event: dict) -> None:
        self._publish(topology.EXCHANGE_EVENTS, contract.ROUTING_KEYS[event["type"]],
                      contract.encode(event), self._properties())


def _quiet_close(connection: Any) -> None:
    try:
        connection.close()
    except Exception:
        pass
