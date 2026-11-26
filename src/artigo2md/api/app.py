"""Aplicação FastAPI: `GET /health` da API base (inalterado) e as rotas de negócio sob `/v1`.

Contrato de `/health` em `docs/specs/container-stack/contracts/api-health.md`; contrato das rotas em
`docs/specs/project-api/contracts/http-api.md`.

Este módulo importa só `fastapi` e `artigo2md` no topo — nunca Docling, pymupdf4llm, `artigo2md.store` nem
`asyncpg` (FR-008 da API base, FR-017/FR-029 da `project-api`). A construção da aplicação (`create_app`) importa
configuração, erros, segurança e `deps` de forma preguiçosa, e o alvo do uvicorn (`artigo2md.api.app:app`, também usado
pelo `Dockerfile`) é criado no primeiro acesso ao atributo `app` (PEP 562): importar este módulo é leve.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI

from artigo2md import __version__

if TYPE_CHECKING:
    from .settings import ApiSettings


def create_app(
    settings: "ApiSettings | None" = None,
    *,
    store: Any | None = None,
    broker: Any | None = None,
    store_factory: Any | None = None,
    consumer: Any | None = None,
    background: bool | None = None,
    background_options: dict[str, Any] | None = None,
) -> FastAPI:
    """Monta a aplicação. `settings=None` lê o ambiente (nunca levanta: erro de configuração só afeta a área
    correspondente, FR-032). `store`/`broker` injetam o repositório e a publicadora (testes com fakes);
    `store_factory` (`async (settings) -> Store`) substitui a abertura real do banco, ainda sob demanda; `consumer`
    injeta a consumidora de eventos (só o estado em memória dela é exposto em `GET /v1/status`).

    `background` liga os serviços de fundo no `lifespan` (publicadora, consumidora, migração/reconexão do banco e
    republicação de jobs, `api/background.py`). `None` = liga só quando NADA foi injetado (execução real, `app`); os
    testes que injetam fakes controlam o resto. `background_options` repassa `connect`/`backoff_*`/`poll` às threads."""
    from .deps import Services
    from .errors import install_error_handlers
    from .routes import build_v1_router
    from .security import install_security
    from .settings import ApiSettings

    effective = settings if settings is not None else ApiSettings.from_env()
    services = Services(effective, store=store, broker=broker, store_factory=store_factory, consumer=consumer)
    if background is None:
        background = all(item is None for item in (store, broker, store_factory, consumer))

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        runner = None
        if background:
            from .background import Background

            runner = Background(services, background_options)
            await runner.start()
        try:
            yield
        finally:
            if runner is not None:
                await runner.stop()
            await services.close()

    # Documentação automática desligada: /docs, /redoc e /openapi.json respondem 404 (FR-033).
    application = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    application.state.services = services
    application.state.settings = effective
    install_error_handlers(application)
    install_security(application, effective.allowed_origins)

    @application.get("/health")
    def health() -> dict[str, str]:
        """Indica que o processo da API está vivo. Não toca banco nem broker (FR-032)."""
        return {"status": "ok", "service": "artigo2md-api", "version": __version__}

    router = build_v1_router()
    application.include_router(router)
    return application


_app: FastAPI | None = None


def __getattr__(name: str) -> Any:
    """`artigo2md.api.app:app` é criado sob demanda (uvicorn faz `getattr` na partida)."""
    global _app
    if name == "app":
        if _app is None:
            _app = create_app()
        return _app
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
