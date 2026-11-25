"""Corretor AMQP falso, em memória, para testar o worker da fila sem Docker (FR-019).

Imita, com as MESMAS assinaturas do pika 1.5.0 (conferidas em `test_fake_amqp.py` contra o pika real,
quando o extra `queue` está instalado), só o que o worker usa: exchanges `direct`/`topic`, filas duráveis,
prefetch, confirmações do publicador, `nack`/requeue, `cancel`, `redelivered`, expiração por mensagem com
dead-letter (relógio injetável) e perda de conexão. Não abre nenhuma conexão de rede (conftest garante).

Fidelidade assumida (pontos que o teste de integração com o RabbitMQ real, `ARTIGO2MD_STACK=1`, comprova):
mensagem expirada só sai da fila ao chegar à cabeça (como na documentação do broker citada em research.md).
"""
from __future__ import annotations

import itertools
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Callable

try:  # com o extra `queue`, usa as exceções reais do pika; sem ele, equivalentes locais
    from pika import exceptions as amqp_exc
except ImportError:  # pragma: no cover - ramo sem o extra
    class AMQPError(Exception):
        pass

    class AMQPConnectionError(AMQPError):
        pass

    class StreamLostError(AMQPConnectionError):
        pass

    class AMQPChannelError(AMQPError):
        pass

    class ChannelClosedByBroker(AMQPChannelError):
        def __init__(self, reply_code: int, reply_text: str) -> None:
            super().__init__(int(reply_code), str(reply_text))
            self.reply_code, self.reply_text = int(reply_code), str(reply_text)

    class UnroutableError(AMQPChannelError):
        def __init__(self, messages) -> None:
            super().__init__(f"{len(messages)} unroutable message(s) returned")
            self.messages = messages

    class NackError(AMQPChannelError):
        def __init__(self, messages) -> None:
            super().__init__(f"{len(messages)} message(s) NACKed")
            self.messages = messages

    amqp_exc = SimpleNamespace(
        AMQPError=AMQPError, AMQPConnectionError=AMQPConnectionError, StreamLostError=StreamLostError,
        AMQPChannelError=AMQPChannelError, ChannelClosedByBroker=ChannelClosedByBroker,
        UnroutableError=UnroutableError, NackError=NackError)


class FakeClock:
    """Relógio injetável: o teste avança o tempo sem esperar."""

    def __init__(self, start: float = 1000.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


def topic_matches(pattern: str, key: str) -> bool:
    """Casamento de routing key de exchange `topic`: `*` = uma palavra, `#` = zero ou mais."""
    def match(p: list[str], k: list[str]) -> bool:
        if not p:
            return not k
        if p[0] == "#":
            return any(match(p[1:], k[i:]) for i in range(len(k) + 1))
        return bool(k) and (p[0] == "*" or p[0] == k[0]) and match(p[1:], k[1:])

    return match(pattern.split("."), key.split("."))


@dataclass
class Message:
    body: bytes
    properties: Any
    routing_key: str
    redelivered: bool = False
    expires_at: float | None = None


@dataclass
class _Queue:
    name: str
    durable: bool
    arguments: dict
    messages: deque = field(default_factory=deque)
    consumers: list = field(default_factory=list)


@dataclass(eq=False)
class _Consumer:
    tag: str
    queue: str
    callback: Callable
    channel: "FakeChannel"
    unacked: int = 0


class FakeBroker:
    def __init__(self, clock: Callable[[], float] | None = None) -> None:
        self.clock = clock or time.monotonic
        self.lock = threading.RLock()
        self.exchanges: dict[str, dict] = {}
        self.queues: dict[str, _Queue] = {}
        self.bindings: list[tuple[str, str, str]] = []  # (exchange, queue, routing_key)
        self.connections: list[FakeConnection] = []
        self.up = True
        self.nack_next_publishes = 0  # próximas N publicações com confirmação recebem Nack
        self.publish_log: list[tuple[str, str, bytes, Any]] = []
        self._tags = itertools.count(1)

    # ── inspeção para os testes ──
    def depth(self, queue: str) -> int:
        with self.lock:
            self._expire(self.queues[queue])
            return len(self.queues[queue].messages)

    def peek(self, queue: str) -> list[Message]:
        with self.lock:
            self._expire(self.queues[queue])
            return list(self.queues[queue].messages)

    # ── controle de falhas ──
    def set_down(self) -> None:
        """Derruba o broker: conexões abertas morrem e novas conexões falham."""
        with self.lock:
            self.up = False
            for conn in list(self.connections):
                conn.lose()

    def set_up(self) -> None:
        with self.lock:
            self.up = True

    # ── operações internas ──
    def _route(self, exchange: str, routing_key: str) -> list[str]:
        ex = self.exchanges.get(exchange)
        if ex is None:
            raise amqp_exc.ChannelClosedByBroker(404, f"NOT_FOUND - no exchange '{exchange}'")
        names = []
        for ex_name, queue, key in self.bindings:
            if ex_name != exchange:
                continue
            ok = topic_matches(key, routing_key) if ex["type"] == "topic" else key == routing_key
            if ok and queue not in names:
                names.append(queue)
        return names

    def _enqueue(self, exchange: str, routing_key: str, body: bytes, properties: Any) -> bool:
        targets = self._route(exchange, routing_key)
        for name in targets:
            exp = getattr(properties, "expiration", None)
            expires_at = self.clock() + int(exp) / 1000.0 if exp not in (None, "") else None
            self.queues[name].messages.append(Message(body, properties, routing_key, False, expires_at))
        return bool(targets)

    def _expire(self, q: _Queue) -> None:
        """Remove mensagens expiradas da CABEÇA da fila e as dead-letter (se a fila tiver DLX)."""
        while q.messages and q.messages[0].expires_at is not None and q.messages[0].expires_at <= self.clock():
            msg = q.messages.popleft()
            dlx = q.arguments.get("x-dead-letter-exchange")
            if dlx is not None:
                key = q.arguments.get("x-dead-letter-routing-key", msg.routing_key)
                props = SimpleNamespace(**{**vars(msg.properties), "expiration": None}) \
                    if hasattr(msg.properties, "__dict__") else msg.properties
                self._enqueue(dlx, key, msg.body, props)

    def dispatch(self) -> None:
        """Entrega às consumers respeitando o prefetch de cada canal."""
        with self.lock:
            for q in self.queues.values():
                self._expire(q)
                for cons in list(q.consumers):
                    ch = cons.channel
                    while q.messages and (ch.prefetch_count == 0 or cons.unacked < ch.prefetch_count) \
                            and not ch.closed and not ch.connection.closed:
                        msg = q.messages.popleft()
                        tag = next(self._tags)
                        cons.unacked += 1
                        ch.unacked[tag] = (cons, q.name, msg)
                        method = SimpleNamespace(delivery_tag=tag, redelivered=msg.redelivered,
                                                 exchange="", routing_key=msg.routing_key,
                                                 consumer_tag=cons.tag)
                        ch.pending.append((cons, method, msg.properties, msg.body))

    def connection(self, parameters: Any = None) -> "FakeConnection":
        with self.lock:
            if not self.up:
                raise amqp_exc.AMQPConnectionError("fake: broker indisponível")
            conn = FakeConnection(self, parameters)
            self.connections.append(conn)
            return conn


class FakeConnection:
    """Imita `pika.BlockingConnection`."""

    def __init__(self, broker: FakeBroker, parameters: Any = None) -> None:
        self.broker = broker
        self.parameters = parameters
        self.closed = False
        self.channels: list[FakeChannel] = []
        self.process_calls = 0  # iterações do laço de eventos (critério de ociosidade, tarefa 1.8)
        self.last_time_limit: float | None = None

    @property
    def is_open(self) -> bool:
        return not self.closed

    def _check(self) -> None:
        if self.closed:
            raise amqp_exc.StreamLostError("fake: conexão perdida")

    def channel(self, channel_number: int | None = None) -> "FakeChannel":
        with self.broker.lock:
            self._check()
            ch = FakeChannel(self)
            self.channels.append(ch)
            return ch

    def process_data_events(self, time_limit: float | None = 0) -> None:
        """Despacha as entregas pendentes aos callbacks (como o pika faz dentro desta chamada)."""
        self._check()
        self.process_calls += 1
        self.last_time_limit = time_limit
        self.broker.dispatch()
        for ch in list(self.channels):
            while ch.pending and not self.closed and not ch.closed:
                cons, method, props, body = ch.pending.popleft()
                if cons in ch.cancelled:
                    continue
                cons.callback(ch, method, props, body)

    def lose(self) -> None:
        """Perda abrupta de conexão: o que estava sem `ack` volta à fila como `redelivered`."""
        with self.broker.lock:
            if self.closed:
                return
            self.closed = True
            for ch in self.channels:
                ch._release_unacked()
                ch.closed = True
            for q in self.broker.queues.values():
                q.consumers = [c for c in q.consumers if c.channel.connection is not self]
            if self in self.broker.connections:
                self.broker.connections.remove(self)

    def close(self, reply_code: int = 200, reply_text: str = "Normal shutdown") -> None:
        self.lose()


class FakeChannel:
    """Imita `pika.adapters.blocking_connection.BlockingChannel`."""

    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection
        self.broker = connection.broker
        self.closed = False
        self.prefetch_count = 0
        self.confirm = False
        self.unacked: dict[int, tuple[_Consumer, str, Message]] = {}
        self.pending: deque = deque()
        self.cancelled: set = set()
        self._ctags = itertools.count(1)

    def _check(self) -> None:
        self.connection._check()
        if self.closed:
            raise amqp_exc.ChannelClosedByBroker(504, "fake: canal fechado")

    def _close_with(self, code: int, text: str):
        self.closed = True
        raise amqp_exc.ChannelClosedByBroker(code, text)

    def _release_unacked(self) -> None:
        for tag, (cons, qname, msg) in sorted(self.unacked.items(), reverse=True):
            msg.redelivered = True
            self.broker.queues[qname].messages.appendleft(msg)
        self.unacked.clear()
        self.pending.clear()

    # ── topologia ──
    def exchange_declare(self, exchange, exchange_type="direct", passive=False, durable=False,
                         auto_delete=False, internal=False, arguments=None):
        with self.broker.lock:
            self._check()
            kind = getattr(exchange_type, "value", exchange_type)
            cur = self.broker.exchanges.get(exchange)
            want = {"type": kind, "durable": durable, "arguments": dict(arguments or {})}
            if cur is None:
                if passive:
                    self._close_with(404, f"NOT_FOUND - no exchange '{exchange}'")
                self.broker.exchanges[exchange] = want
            elif cur != want:
                self._close_with(406, f"PRECONDITION_FAILED - inequivalent arg for exchange '{exchange}'")

    def queue_declare(self, queue, passive=False, durable=False, exclusive=False, auto_delete=False,
                      arguments=None):
        with self.broker.lock:
            self._check()
            args = dict(arguments or {})
            cur = self.broker.queues.get(queue)
            if cur is None:
                if passive:
                    self._close_with(404, f"NOT_FOUND - no queue '{queue}'")
                self.broker.queues[queue] = _Queue(queue, durable, args)
            elif cur.durable != durable or cur.arguments != args:
                self._close_with(406, f"PRECONDITION_FAILED - inequivalent arg for queue '{queue}'")

    def queue_bind(self, queue, exchange, routing_key=None, arguments=None):
        with self.broker.lock:
            self._check()
            if exchange not in self.broker.exchanges:
                self._close_with(404, f"NOT_FOUND - no exchange '{exchange}'")
            if queue not in self.broker.queues:
                self._close_with(404, f"NOT_FOUND - no queue '{queue}'")
            binding = (exchange, queue, routing_key or "")
            if binding not in self.broker.bindings:
                self.broker.bindings.append(binding)

    # ── QoS e confirmações ──
    def basic_qos(self, prefetch_size=0, prefetch_count=0, global_qos=False):
        self._check()
        self.prefetch_count = int(prefetch_count)

    def confirm_delivery(self):
        self._check()
        self.confirm = True

    def basic_publish(self, exchange, routing_key, body, properties=None, mandatory=False):
        with self.broker.lock:
            self._check()
            self.broker.publish_log.append((exchange, routing_key, body, properties))
            if self.confirm and self.broker.nack_next_publishes > 0:
                self.broker.nack_next_publishes -= 1
                raise amqp_exc.NackError([SimpleNamespace(body=body)])
            try:
                routed = self.broker._enqueue(exchange, routing_key, body, properties)
            except amqp_exc.ChannelClosedByBroker as exc:
                self.closed = True
                raise exc
            if not routed and mandatory and self.confirm:
                raise amqp_exc.UnroutableError([SimpleNamespace(body=body)])

    # ── consumo ──
    def basic_consume(self, queue, on_message_callback, auto_ack=False, exclusive=False,
                      consumer_tag=None, arguments=None):
        with self.broker.lock:
            self._check()
            if queue not in self.broker.queues:
                self._close_with(404, f"NOT_FOUND - no queue '{queue}'")
            tag = consumer_tag or f"ctag{id(self)}.{next(self._ctags)}"
            cons = _Consumer(tag, queue, on_message_callback, self)
            self.broker.queues[queue].consumers.append(cons)
            return tag

    def basic_cancel(self, consumer_tag):
        """Como no pika: devolve as entregas já despachadas ao canal e ainda não processadas."""
        with self.broker.lock:
            self._check()
            returned = []
            for q in self.broker.queues.values():
                for cons in list(q.consumers):
                    if cons.tag == consumer_tag and cons.channel is self:
                        q.consumers.remove(cons)
                        self.cancelled.add(cons)
            for item in list(self.pending):
                if item[0] in self.cancelled:
                    self.pending.remove(item)
                    returned.append((item[1], item[2], item[3]))
            return returned

    def basic_ack(self, delivery_tag=0, multiple=False):
        with self.broker.lock:
            self._check()
            entry = self.unacked.pop(delivery_tag, None)
            if entry is None:
                self._close_with(406, f"PRECONDITION_FAILED - unknown delivery tag {delivery_tag}")
            entry[0].unacked -= 1

    def basic_nack(self, delivery_tag=0, multiple=False, requeue=True):
        with self.broker.lock:
            self._check()
            entry = self.unacked.pop(delivery_tag, None)
            if entry is None:
                self._close_with(406, f"PRECONDITION_FAILED - unknown delivery tag {delivery_tag}")
            cons, qname, msg = entry
            cons.unacked -= 1
            if requeue:
                msg.redelivered = True
                self.broker.queues[qname].messages.appendleft(msg)

    def close(self, reply_code=0, reply_text="Normal shutdown"):
        with self.broker.lock:
            if not self.closed:
                self._release_unacked()
                self.closed = True
