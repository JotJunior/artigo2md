"""Publicadora AMQP da API (`research.md`, Decision 3 e 8; FR-011, FR-032; plan S-05).

Uma `BlockingConnection` do pika confinada a UMA thread (o pika não é thread-safe), com `confirm_delivery()` e
`mandatory=True`, como o worker. A thread só publica na exchange de jobs (`artigo2md.jobs`, routing key `convert`) e
mantém a própria reconexão com recuo exponencial (1 s a 30 s), independente do banco e da consumidora.

`publish(message)` é assíncrona (`asyncio.wrap_future`) e só retorna quando o broker CONFIRMA a mensagem. Qualquer outra
coisa — recusa (`Nack`), mensagem não roteável, conexão caída, prazo estourado (padrão 10 s, decisão 1.1.2) — vira
`PublishFailed` com motivo em português: a mensagem NÃO é considerada publicada e quem chamou grava o job como falho.
Não há retentativa aqui: o documento pode ser enfileirado de novo pelo cliente.

Regra S-05 (lote grande com broker fora não pode esperar o prazo por documento): publicadora desconectada (ou
travada num envio que passou do prazo) recusa NA HORA, sem esperar; e `PublishBatch` faz o restante do lote falhar na
hora depois da primeira falha.

Consumidora (`EventConsumer`, tarefa 7.3; FR-013, FR-017; Decision 4): OUTRA `BlockingConnection` em OUTRA thread, que
consome `artigo2md.events.all` com `prefetch_count` 16. O callback do pika só ENFILEIRA a entrega num buffer local; o laço
da thread trata UMA mensagem por vez, na ordem de chegada (preserva `progress -> result/error`), entregando o corpo ao
laço `asyncio` da API (`run_coroutine_threadsafe`) e esperando o resultado ANTES do `ack`. Banco indisponível: a MESMA
mensagem é repetida depois de um recuo exponencial (1 s a 30 s) feito com `connection.sleep()` (mantém o heartbeat; as
entregas que chegam nesse intervalo só entram no buffer, nunca são tratadas fora de ordem); sem `ack` nem `nack`. A
conexão AMQP caída é refeita com o mesmo recuo, independente do banco e da publicadora (o broker devolve o que ficou
sem `ack`).

Nada aqui importa `pika` no topo: `jobqueue.amqp.open_connection` o carrega sob demanda, e os testes injetam uma
conexão falsa (`connect=`).
"""
from __future__ import annotations

import asyncio
import collections
import concurrent.futures
import logging
import queue
import threading
from collections.abc import Callable, Coroutine
from datetime import datetime, timezone
from typing import Any

from ..jobqueue import amqp, schema, topology

logger = logging.getLogger("artigo2md.api")

PUBLISH_DEADLINE_SECONDS = 10.0  # decisão 1.1.2: prazo de confirmação da publicação
BACKOFF_START_SECONDS = 1.0
BACKOFF_MAX_SECONDS = 30.0
_POLL_SECONDS = 1.0  # intervalo do laço ocioso: mantém o heartbeat e reage ao encerramento
_STOP = object()


class PublishFailed(Exception):
    """A mensagem não foi confirmada pelo broker. `reason` está em português, sem texto bruto do driver."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _default_connect(settings: Any) -> Any:
    return amqp.open_connection(settings.with_resolved_host())


class BrokerPublisher:
    """Publicadora com thread própria. `start()` inicia a conexão em segundo plano; a API sobe sem broker (FR-032)."""

    def __init__(
        self,
        settings: Any,
        *,
        deadline: float = PUBLISH_DEADLINE_SECONDS,
        backoff_start: float = BACKOFF_START_SECONDS,
        backoff_max: float = BACKOFF_MAX_SECONDS,
        connect: Callable[[Any], Any] | None = None,
        on_connect: Callable[[], None] | None = None,
        poll: float = _POLL_SECONDS,
    ) -> None:
        self._settings = settings
        self._deadline = deadline
        self._backoff_start = backoff_start
        self._backoff_max = backoff_max
        self._connect = connect or _default_connect
        self._on_connect = on_connect
        self._poll = poll
        self._work: queue.Queue[Any] = queue.Queue()
        self._stop = threading.Event()
        self._connected = threading.Event()
        self._stalled = threading.Event()  # um envio passou do prazo e a thread ainda está nele
        self._thread: threading.Thread | None = None

    # ── estado em memória (GET /v1/status) ──────────────────────────────────────
    @property
    def connected(self) -> bool:
        return self._connected.is_set() and not self._stalled.is_set()

    # ── ciclo de vida ───────────────────────────────────────────────────────────
    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="artigo2md-publisher", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        """Encerra a thread, fecha a conexão e falha o que ainda estava na fila."""
        self._stop.set()
        self._work.put(_STOP)
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout)
        self._connected.clear()
        self._fail_pending("A publicadora foi encerrada.")

    # ── API assíncrona ──────────────────────────────────────────────────────────
    async def publish(self, message: dict[str, Any]) -> None:
        """Publica o job e espera a confirmação. `PublishFailed` em qualquer outro desfecho."""
        try:
            body = schema.encode(message)
        except (TypeError, ValueError):
            raise PublishFailed("A mensagem do job não pôde ser montada.") from None
        if len(body) > schema.MAX_BODY_BYTES:
            raise PublishFailed("A mensagem do job excede o tamanho máximo permitido pela fila.")
        if not self.connected:
            raise PublishFailed("A fila de conversão não está disponível no momento.")  # S-05: sem esperar
        future: concurrent.futures.Future[None] = concurrent.futures.Future()
        self._work.put((future, body))
        try:
            await asyncio.wait_for(asyncio.wrap_future(future), timeout=self._deadline)
        except (asyncio.TimeoutError, TimeoutError):
            if not future.cancel():  # já em envio: a thread terminou ou ainda está nele
                if not future.done():
                    self._stalled.set()
            raise PublishFailed("A fila de conversão não confirmou a mensagem a tempo.") from None

    def batch(self) -> "PublishBatch":
        return PublishBatch(self)

    # ── thread ──────────────────────────────────────────────────────────────────
    def _fail_pending(self, reason: str) -> None:
        while True:
            try:
                item = self._work.get_nowait()
            except queue.Empty:
                return
            if item is _STOP:
                continue
            future = item[0]
            if future.set_running_or_notify_cancel():
                future.set_exception(PublishFailed(reason))

    def _run(self) -> None:
        delay = self._backoff_start
        while not self._stop.is_set():
            connection = None
            try:
                connection = self._connect(self._settings)
                channel = connection.channel()
                topology.declare(channel)
                channel.confirm_delivery()
            except Exception as exc:
                self._close(connection)
                logger.warning("A publicadora não conseguiu conectar ao broker (%s); nova tentativa em %.0f s.",
                               type(exc).__name__, delay)
                self._fail_pending("A fila de conversão não está disponível no momento.")
                if self._stop.wait(delay):
                    return
                delay = min(delay * 2, self._backoff_max)
                continue
            delay = self._backoff_start
            self._connected.set()
            logger.info("Publicadora conectada ao broker.")
            if self._on_connect is not None:
                try:
                    self._on_connect()
                except Exception as exc:  # o gancho de republicação nunca derruba a publicadora
                    logger.warning("Falha ao agendar a republicação de jobs pendentes (%s).", type(exc).__name__)
            try:
                self._serve(connection, channel)
            finally:
                self._connected.clear()
                self._stalled.clear()
                self._close(connection)
                self._fail_pending("A fila de conversão não está disponível no momento.")

    def _serve(self, connection: Any, channel: Any) -> None:
        """Atende a fila de trabalho até a conexão cair ou a thread ser encerrada."""
        while not self._stop.is_set():
            self._stalled.clear()  # no topo do laço a thread não está presa num envio: marca velha não vale
            try:
                item = self._work.get(timeout=self._poll)
            except queue.Empty:
                item = None
            try:
                connection.process_data_events(time_limit=0)  # mantém o heartbeat
            except Exception as exc:
                logger.warning("A conexão da publicadora com o broker caiu (%s).", type(exc).__name__)
                if item is not None and item is not _STOP:
                    self._reject(item[0], "A fila de conversão não está disponível no momento.")
                return
            if item is None:
                continue
            if item is _STOP:
                return
            future, body = item
            if not future.set_running_or_notify_cancel():
                continue  # o prazo estourou antes de a thread pegar o item
            try:
                channel.basic_publish(
                    exchange=topology.EXCHANGE_JOBS,
                    routing_key=topology.RK_CONVERT,
                    body=body,
                    properties=amqp.make_properties(),
                    mandatory=True,
                )
            except Exception as exc:
                self._stalled.clear()
                if amqp.is_publish_refused(exc):
                    future.set_exception(PublishFailed("A fila de conversão recusou a mensagem do job."))
                    continue  # o canal segue utilizável
                future.set_exception(PublishFailed("A fila de conversão não está disponível no momento."))
                logger.warning("Falha ao publicar na fila de conversão (%s).", type(exc).__name__)
                return  # reconecta
            self._stalled.clear()
            future.set_result(None)

    @staticmethod
    def _reject(future: concurrent.futures.Future, reason: str) -> None:
        if future.set_running_or_notify_cancel():
            future.set_exception(PublishFailed(reason))

    @staticmethod
    def _close(connection: Any) -> None:
        if connection is None:
            return
        try:
            connection.close()
        except Exception:  # fechar uma conexão já morta não é erro
            pass


class PublishBatch:
    """Regra S-05 para lotes: depois da primeira falha, os documentos restantes falham na hora (sem nova espera)."""

    def __init__(self, publisher: Any) -> None:
        self._publisher = publisher
        self._tripped: PublishFailed | None = None

    @property
    def tripped(self) -> bool:
        return self._tripped is not None

    async def publish(self, message: dict[str, Any]) -> None:
        if self._tripped is not None:
            raise PublishFailed(self._tripped.reason)
        try:
            await self._publisher.publish(message)
        except PublishFailed as exc:
            self._tripped = exc
            raise


# ── consumidora ─────────────────────────────────────────────────────────────────
CONSUMER_PREFETCH = 16


class EventConsumer:
    """Consumidora de `artigo2md.events.all` com thread própria (ver o cabeçalho do módulo).

    `dispatch(body)` é uma corrotina da API (`EventHandler.handle` + repasse ao vivo) executada no laço `loop`; ela
    levanta `consumer_retry` (`retry_on`) quando o banco está fora, e qualquer outra coisa conta como tratada.
    """

    def __init__(
        self,
        settings: Any,
        dispatch: Callable[[bytes], Coroutine[Any, Any, Any]],
        loop: asyncio.AbstractEventLoop,
        *,
        retry_on: type[BaseException] | tuple[type[BaseException], ...],
        prefetch: int = CONSUMER_PREFETCH,
        backoff_start: float = BACKOFF_START_SECONDS,
        backoff_max: float = BACKOFF_MAX_SECONDS,
        connect: Callable[[Any], Any] | None = None,
        poll: float = _POLL_SECONDS,
        handle_timeout: float | None = None,
    ) -> None:
        self._settings = settings
        self._dispatch = dispatch
        self._loop = loop
        self._retry_on = retry_on
        self._prefetch = prefetch
        self._backoff_start = backoff_start
        self._backoff_max = backoff_max
        self._connect = connect or _default_connect
        self._poll = poll
        self._handle_timeout = handle_timeout
        self._stop = threading.Event()
        self._connected = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_event_at: str | None = None
        self._buffer: collections.deque[tuple[Any, Any, Any, bytes]] = collections.deque()

    # ── estado em memória (GET /v1/status) ──────────────────────────────────────
    @property
    def connected(self) -> bool:
        return self._connected.is_set()

    @property
    def last_event_at(self) -> str | None:
        return self._last_event_at

    # ── ciclo de vida ───────────────────────────────────────────────────────────
    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="artigo2md-consumer", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout)
        self._connected.clear()

    # ── thread ──────────────────────────────────────────────────────────────────
    def _run(self) -> None:
        delay = self._backoff_start
        while not self._stop.is_set():
            connection = None
            try:
                connection = self._connect(self._settings)
                channel = connection.channel()
                topology.declare(channel)
                channel.basic_qos(prefetch_count=self._prefetch)
                self._buffer.clear()
                channel.basic_consume(
                    topology.QUEUE_EVENTS,
                    on_message_callback=lambda ch, method, props, body: self._buffer.append((ch, method, props, body)),
                    auto_ack=False,
                )
            except Exception as exc:
                BrokerPublisher._close(connection)
                logger.warning("A consumidora não conseguiu conectar ao broker (%s); nova tentativa em %.0f s.",
                               type(exc).__name__, delay)
                if self._stop.wait(delay):
                    return
                delay = min(delay * 2, self._backoff_max)
                continue
            delay = self._backoff_start
            self._connected.set()
            logger.info("Consumidora conectada ao broker.")
            try:
                self._serve(connection)
            except Exception as exc:  # conexão/canal caiu: reconecta; o broker devolve o que ficou sem ack
                logger.warning("A conexão da consumidora com o broker caiu (%s).", type(exc).__name__)
            finally:
                self._connected.clear()
                self._buffer.clear()
                BrokerPublisher._close(connection)
            if self._stop.wait(delay):
                return
            delay = min(delay * 2, self._backoff_max)

    def _serve(self, connection: Any) -> None:
        """Trata UMA mensagem por vez, na ordem de chegada, até a conexão cair ou a thread ser encerrada."""
        while not self._stop.is_set():
            if not self._buffer:
                connection.process_data_events(time_limit=self._poll)  # recebe entregas e mantém o heartbeat
                continue
            channel, method, _props, body = self._buffer.popleft()
            if not self._handle_until_done(connection, body):
                return  # encerramento no meio do tratamento: sem ack, a mensagem volta à fila
            channel.basic_ack(delivery_tag=method.delivery_tag)
            self._last_event_at = datetime.now(timezone.utc).isoformat()
            connection.process_data_events(time_limit=0)

    def _handle_until_done(self, connection: Any, body: bytes) -> bool:
        """Repete a MESMA mensagem enquanto o banco estiver fora. `False` = encerrando (não tratada)."""
        delay = self._backoff_start
        while not self._stop.is_set():
            future: concurrent.futures.Future | None = None
            try:
                future = asyncio.run_coroutine_threadsafe(self._dispatch(body), self._loop)
                future.result(timeout=self._handle_timeout)
                return True
            except self._retry_on:
                logger.warning("Banco indisponível ao tratar um evento; nova tentativa em %.0f s.", delay)
            except (concurrent.futures.CancelledError, RuntimeError, concurrent.futures.TimeoutError):
                if future is not None:
                    future.cancel()
                return False  # laço da API encerrado (ou travado): não trata nem confirma
            except Exception as exc:  # o tratamento nunca derruba a consumidora nem bloqueia a fila
                logger.error("Evento descartado por falha inesperada no repasse (%s).", type(exc).__name__)
                return True
            self._sleep(connection, delay)
            delay = min(delay * 2, self._backoff_max)
        return False

    def _sleep(self, connection: Any, seconds: float) -> None:
        """Dorme com `connection.sleep()` (heartbeat vivo), em fatias, atento ao encerramento."""
        remaining = seconds
        while remaining > 0 and not self._stop.is_set():
            step = min(remaining, self._poll)
            connection.sleep(step)
            remaining -= step
