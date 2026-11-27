"""Topologia do RabbitMQ: nomes, argumentos e declaração idempotente (FR-002; research.md, Decision 5).

Os 8 objetos (4 exchanges e 4 filas) e os bindings vivem só aqui, como constantes. A declaração usa sempre
os mesmos argumentos; se o broker já tem um objeto com configuração diferente (erro de pré-condição, 406),
`declare` levanta `TopologyMismatch` com a mensagem em português e **não** apaga nem sobrescreve nada.
Não importa `pika`: reconhece o erro do broker pelo `reply_code` da exceção.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .schema import ROUTING_KEYS, TYPE_ERROR, TYPE_PROGRESS, TYPE_RESULT

PRECONDITION_FAILED = 406  # código AMQP de pré-condição (`pika.spec.PRECONDITION_FAILED`)

# exchanges
EXCHANGE_JOBS = "artigo2md.jobs"
EXCHANGE_RETRY = "artigo2md.retry"
EXCHANGE_DEAD = "artigo2md.dead"
EXCHANGE_EVENTS = "artigo2md.events"

# filas
QUEUE_JOBS = "artigo2md.jobs.convert"
QUEUE_RETRY = "artigo2md.jobs.retry"
QUEUE_DEAD = "artigo2md.jobs.dead"
QUEUE_EVENTS = "artigo2md.events.all"

# routing keys (as dos eventos vêm do contrato)
RK_CONVERT = "convert"
RK_DEAD = "dead"
RK_EVENTS_ALL = "job.#"
RK_PROGRESS = ROUTING_KEYS[TYPE_PROGRESS]
RK_RESULT = ROUTING_KEYS[TYPE_RESULT]
RK_ERROR = ROUTING_KEYS[TYPE_ERROR]

CLASSIC = {"x-queue-type": "classic"}


@dataclass(frozen=True)
class ExchangeSpec:
    name: str
    kind: str  # `direct` ou `topic`


@dataclass(frozen=True)
class QueueSpec:
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BindingSpec:
    queue: str
    exchange: str
    routing_key: str


EXCHANGES: tuple[ExchangeSpec, ...] = (
    ExchangeSpec(EXCHANGE_JOBS, "direct"),
    ExchangeSpec(EXCHANGE_RETRY, "direct"),
    ExchangeSpec(EXCHANGE_DEAD, "direct"),
    ExchangeSpec(EXCHANGE_EVENTS, "topic"),
)

# Sem `x-message-ttl` na fila de retry: a expiração é por mensagem (`expiration`), para que mudar
# `--retry-delay` não gere topologia incompatível.
QUEUES: tuple[QueueSpec, ...] = (
    QueueSpec(QUEUE_JOBS, dict(CLASSIC)),
    QueueSpec(QUEUE_RETRY, {**CLASSIC, "x-dead-letter-exchange": EXCHANGE_JOBS,
                            "x-dead-letter-routing-key": RK_CONVERT}),
    QueueSpec(QUEUE_DEAD, dict(CLASSIC)),
    QueueSpec(QUEUE_EVENTS, dict(CLASSIC)),
)

BINDINGS: tuple[BindingSpec, ...] = (
    BindingSpec(QUEUE_JOBS, EXCHANGE_JOBS, RK_CONVERT),
    BindingSpec(QUEUE_RETRY, EXCHANGE_RETRY, RK_CONVERT),
    BindingSpec(QUEUE_DEAD, EXCHANGE_DEAD, RK_DEAD),
    BindingSpec(QUEUE_EVENTS, EXCHANGE_EVENTS, RK_EVENTS_ALL),
)


class TopologyMismatch(Exception):
    """Objeto já existente no broker com configuração diferente. `message` está em português."""

    def __init__(self, object_name: str):
        self.object_name = object_name
        self.message = (
            f"A fila/exchange {object_name} já existe no RabbitMQ com configuração diferente. "
            "Nada foi alterado. Remova-a pelo painel (abra-o com 'sh stack/up.sh --management-panel'; "
            "ele fica sem proteção contra DNS rebinding até um 'sh stack/up.sh' sem a opção ou um 'sh stack/down.sh') ou pela "
            "linha de comando do broker, ou alinhe a configuração."
        )
        super().__init__(self.message)


def _is_precondition_failed(exc: BaseException) -> bool:
    return getattr(exc, "reply_code", None) == PRECONDITION_FAILED


def declare(channel) -> None:
    """Declara exchanges, filas e bindings. Idempotente; nunca apaga nem altera objeto existente.

    Depois de um `TopologyMismatch` o canal foi fechado pelo broker: quem chama deve abrir outro.
    """
    current = ""
    try:
        for exchange in EXCHANGES:
            current = exchange.name
            channel.exchange_declare(exchange=exchange.name, exchange_type=exchange.kind, durable=True)
        for queue in QUEUES:
            current = queue.name
            channel.queue_declare(queue=queue.name, durable=True, arguments=dict(queue.arguments) or None)
        for binding in BINDINGS:
            current = binding.queue
            channel.queue_bind(queue=binding.queue, exchange=binding.exchange, routing_key=binding.routing_key)
    except Exception as exc:
        if _is_precondition_failed(exc):
            raise TopologyMismatch(current) from None
        raise
