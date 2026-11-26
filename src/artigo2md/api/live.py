"""Fluxo ao vivo por projeto (SSE): difusor em memória e formatação dos eventos (`contracts/live-events.md`; FR-019,
FR-020, SC-004; `plan` S-03; `data-model.md`, "Entity: Subscriber").

`LiveHub` guarda, por projeto, os `Subscriber` abertos. Cada `Subscriber` tem uma fila limitada (1000 eventos) e a marca
`closed`. Tudo roda no laço `asyncio` da API (as rotas e o `EventHandler`, chamado pela consumidora via
`run_coroutine_threadsafe`, executam nele): `publish` nunca bloqueia nem espera um cliente.

- **cliente lento** (fila cheia): o assinante é marcado `closed` e removido do difusor; o gerador do fluxo termina e a
  conexão dele é encerrada. Os demais clientes não são afetados (FR-020);
- **teto** de 32 assinantes simultâneos (S-03): o 33º recebe `503 too_many_streams`;
- **remoção no `finally`**: o gerador sempre chama `unsubscribe` (desconexão, fila cheia ou encerramento da API), então
  nenhuma fila, nenhum assinante e nenhuma referência ficam retidos;
- **sem `id:`/`Last-Event-ID`**: ao reconectar, o cliente relê o estado pelas rotas de consulta (FR-020); os eventos
  são sinais de interface e a fonte de verdade é o banco.

Este módulo importa só a biblioteca padrão e `artigo2md.api.errors` (nunca motores de conversão, FR-029).
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from .errors import ApiError

logger = logging.getLogger("artigo2md.api")

MAX_SUBSCRIBERS = 32  # S-03 (aceito pelo operador, block-001/dec-023)
QUEUE_SIZE = 1000  # data-model.md, "Entity: Subscriber"
KEEPALIVE_SECONDS = 15.0  # live-events.md: comentário `: keep-alive` a cada 15 s
KEEPALIVE_FRAME = ": keep-alive\n\n"

EVENT_PROGRESS = "progress"
EVENT_JOB_STATE = "job_state"
EVENT_WARNING = "warning"

MSG_JOB_CREATED = "Job criado."
MSG_JOB_STARTED = "Job iniciado."
MSG_JOB_CANCELLED = "Job cancelado pelo usuário."
MSG_JOB_CANCELLED_ARCHIVE = "Job cancelado porque o projeto foi arquivado."
MSG_JOB_CANCELLED_DELETE = "Job cancelado porque o projeto foi excluído."

_CLOSE = object()  # sentinela: acorda o gerador para encerrar o fluxo


@dataclass(eq=False)
class Subscriber:
    """Um cliente SSE: filtro por projeto, fila limitada e a marca `closed`."""

    project_id: int
    queue: asyncio.Queue[Any] = field(default_factory=lambda: asyncio.Queue(maxsize=QUEUE_SIZE))
    closed: bool = False
    overflowed: bool = False  # a fila encheu: o cliente não consome, o fluxo termina sem entregar o que sobrou

    def offer(self, frame: str) -> bool:
        """Enfileira um quadro já formatado. Falso (e `closed`) se a fila está cheia ou o assinante já fechou."""
        if self.closed:
            return False
        try:
            self.queue.put_nowait(frame)
        except asyncio.QueueFull:
            self.closed = True
            self.overflowed = True
            return False
        return True

    def close(self) -> None:
        """Marca como fechado e acorda o gerador (se a fila estiver cheia ele vê `closed` no próximo ciclo)."""
        self.closed = True
        try:
            self.queue.put_nowait(_CLOSE)
        except asyncio.QueueFull:
            pass


def format_event(name: str, data: dict[str, Any]) -> str:
    """Quadro SSE: `event:` e uma linha `data:` com JSON numa linha só; sem `id:`."""
    return f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False, separators=(',', ':'))}\n\n"


# ── cargas dos eventos (live-events.md) ─────────────────────────────────────────
def progress_data(job_id: int, document_id: int, attempt: int, message: str, emitted_at: str) -> dict[str, Any]:
    return {"job_id": job_id, "document_id": document_id, "attempt": attempt, "message": message,
            "emitted_at": emitted_at}


def job_state_data(
    job_id: int, document_id: int, status: str, message: str, attempt: int | None = None
) -> dict[str, Any]:
    return {"job_id": job_id, "document_id": document_id, "attempt": attempt, "status": status, "message": message}


class LiveHub:
    """Difusor por projeto. Não é seguro entre threads: use só no laço `asyncio` da API."""

    def __init__(self, max_subscribers: int = MAX_SUBSCRIBERS, queue_size: int = QUEUE_SIZE) -> None:
        self._max = max_subscribers
        self._queue_size = queue_size
        self._by_project: dict[int, list[Subscriber]] = {}

    # ── assinantes ──────────────────────────────────────────────────────────────
    @property
    def subscriber_count(self) -> int:
        return sum(len(items) for items in self._by_project.values())

    def subscribe(self, project_id: int) -> Subscriber:
        """Novo assinante do projeto; acima do teto, `503 too_many_streams`."""
        if self.subscriber_count >= self._max:
            logger.warning("Fluxo ao vivo recusado: teto de %d conexões simultâneas atingido.", self._max)
            raise ApiError("too_many_streams")
        subscriber = Subscriber(project_id, asyncio.Queue(maxsize=self._queue_size))
        self._by_project.setdefault(project_id, []).append(subscriber)
        return subscriber

    def unsubscribe(self, subscriber: Subscriber) -> None:
        """Remove o assinante (idempotente); o projeto sem assinantes sai do mapa."""
        subscriber.closed = True
        items = self._by_project.get(subscriber.project_id)
        if items is None:
            return
        try:
            items.remove(subscriber)
        except ValueError:
            pass
        if not items:
            del self._by_project[subscriber.project_id]

    # ── emissão ─────────────────────────────────────────────────────────────────
    def publish(self, project_id: int, name: str, data: dict[str, Any]) -> int:
        """Entrega o evento aos assinantes DO projeto. Devolve quantos receberam. Cliente com fila cheia é encerrado
        (removido na hora; o gerador termina no próximo ciclo) sem afetar os demais."""
        items = self._by_project.get(project_id)
        if not items:
            return 0
        frame = format_event(name, data)
        delivered = 0
        for subscriber in list(items):
            if subscriber.offer(frame):
                delivered += 1
            else:
                logger.warning("Cliente do fluxo ao vivo encerrado: não consumiu os eventos a tempo.")
                self.unsubscribe(subscriber)
                subscriber.close()
        return delivered

    def job_state(
        self, project_id: int, job_id: int, document_id: int, status: str, message: str, attempt: int | None = None
    ) -> int:
        return self.publish(project_id, EVENT_JOB_STATE, job_state_data(job_id, document_id, status, message, attempt))

    def close_project(self, project_id: int) -> None:
        """Encerra os fluxos do projeto (exclusão, `live-events.md`): os eventos já enfileirados são entregues antes."""
        for subscriber in list(self._by_project.get(project_id, ())):
            self.unsubscribe(subscriber)
            subscriber.close()

    def close_all(self) -> None:
        """Encerramento da API: todos os fluxos terminam."""
        for project_id in list(self._by_project):
            self.close_project(project_id)

    # ── fluxo ───────────────────────────────────────────────────────────────────
    async def stream(self, subscriber: Subscriber, keepalive: float = KEEPALIVE_SECONDS) -> AsyncIterator[str]:
        """Gerador do corpo SSE. Termina quando o assinante fecha; o `finally` sempre remove o assinante."""
        try:
            while not subscriber.overflowed:
                try:
                    item = await asyncio.wait_for(subscriber.queue.get(), timeout=keepalive)
                except asyncio.TimeoutError:
                    if subscriber.closed:
                        return
                    yield KEEPALIVE_FRAME
                    continue
                if item is _CLOSE:
                    return
                yield item
                if subscriber.closed and (subscriber.overflowed or subscriber.queue.empty()):
                    return
        finally:
            self.unsubscribe(subscriber)
