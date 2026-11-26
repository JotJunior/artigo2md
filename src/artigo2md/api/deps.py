"""Acesso ao `Store` e ao broker, com 503 em português quando indisponíveis (FR-032).

`Services` vive em `app.state.services`, criado por `create_app`. O `Store` é aberto SOB DEMANDA, na primeira rota
que precisa dele, e a abertura que falha NÃO é guardada: a próxima requisição tenta de novo, então a API volta a
operar sozinha quando o banco retorna, sem reinício (FR-032, SC-006). O broker é fornecido por quem o inicia (a
publicadora de `api/broker.py`, fase 7) via `Services.broker`; enquanto não houver publicadora conectada, as rotas que
publicam respondem `503 broker_unavailable`.

Nada aqui importa `asyncpg`: o `Store` real só é carregado dentro de `Services.get_store`.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from fastapi import Request

from .errors import ApiError
from .live import LiveHub
from .settings import ApiSettings

logger = logging.getLogger("artigo2md.api")


class Publisher(Protocol):
    """O que as rotas esperam da publicadora AMQP (implementada em `api/broker.py`)."""

    async def publish(self, message: dict[str, Any]) -> None: ...


StoreFactory = Callable[[ApiSettings], Awaitable[Any]]


async def _open_real_store(settings: ApiSettings) -> Any:
    from ..store import Store  # carregado aqui para a API subir e responder /health sem o driver
    from ..store.migrator import apply_migrations

    if settings.db is None:
        raise ApiError("database_unavailable")
    # Migra ANTES de abrir o pool (7.6.1). Falha (banco fora ou migração recusada) levanta `StoreError`, que vira
    # `503 database_unavailable`; nada fica guardado, então a próxima requisição tenta de novo (recuperação sozinha).
    await apply_migrations(settings.db)
    return await Store.open(settings.db)


class Services:
    """Dependências externas da API: banco (sob demanda) e broker (fornecido)."""

    def __init__(
        self,
        settings: ApiSettings,
        *,
        store: Any | None = None,
        broker: Publisher | None = None,
        store_factory: StoreFactory | None = None,
        consumer: Any | None = None,
    ) -> None:
        self.settings = settings
        self.broker = broker
        self.inflight: set[int] = set()  # jobs que uma requisição desta API está publicando agora (não republicar)
        self.export_active = False  # uma exportação em zip por vez (S-04)
        self.live = LiveHub()  # fluxo ao vivo por projeto (SSE, `api/live.py`)
        self.consumer = consumer  # `EventConsumer` (fase 7.6); só o estado em memória é lido, por `GET /v1/status`
        self._store = store
        self._injected = store is not None
        self._factory = store_factory or _open_real_store
        self._lock = asyncio.Lock()

    async def get_store(self) -> Any:
        """O `Store` aberto. Falha de abertura vira `ApiError`/`StoreError` (503 `database_unavailable`) e é refeita
        na próxima chamada."""
        if self._store is not None:
            return self._store
        async with self._lock:
            if self._store is None:
                self._store = await self._factory(self.settings)
            return self._store

    def get_broker(self) -> Publisher:
        if self.broker is None:
            raise ApiError("broker_unavailable")
        return self.broker

    async def close(self) -> None:
        self.live.close_all()  # os fluxos SSE terminam com a API
        store, self._store = self._store, None
        if store is not None and not self._injected:
            try:
                await store.close()
            except Exception as exc:  # o encerramento nunca falha por causa do banco
                logger.warning("Falha ao fechar o banco no encerramento (%s).", type(exc).__name__)


def get_services(request: Request) -> Services:
    return request.app.state.services


async def get_store(request: Request) -> Any:
    """Dependência do FastAPI: `Depends(get_store)`."""
    return await get_services(request).get_store()


def get_broker(request: Request) -> Publisher:
    """Dependência do FastAPI: `Depends(get_broker)`."""
    return get_services(request).get_broker()
