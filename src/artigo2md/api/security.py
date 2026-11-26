"""Política de origem, CORS, tipo de conteúdo e limite de corpo (FR-027, FR-028; research Decision 9).

Quatro camadas, da mais externa para a mais interna (a ordem importa e é montada em `install_security`):

1. `HostGuard`: `Host` só `127.0.0.1` ou `localhost` (400 caso contrário, status da API base, FR-027), com o corpo de
   erro JSON em português (FR-031) no lugar do texto puro em inglês do `TrustedHostMiddleware`.
2. `OriginGuard`: pedido de `/v1` com cabeçalho `Origin` fora da lista efetiva (inclui `null`) → 403
   `origin_not_allowed` ANTES de qualquer efeito (banco, fila, rota). Sem `Origin` (cliente nativo, `curl`, testes) é aceito.
3. CORS (`CORSMiddleware`) SÓ para `/v1`: `allow_credentials=False`, métodos e cabeçalhos enumerados em
   `contracts/http-api.md`; o preflight de origem autorizada termina aqui (200), sem tocar banco nem fila. Preflight
   com método ou cabeçalho fora da lista volta 400 com o corpo de erro em português (FR-031). `OPTIONS /health` não
   passa por aqui e segue o contrato da API base (405, FR-033).
4. `BodyGuard`: pedido COM corpo precisa de `Content-Type: application/json` (415) e o corpo tem no máximo
   `MAX_BODY_BYTES` (413), contado enquanto chega — nunca lido por inteiro para decidir. Rotas sem corpo (`archive`,
   `cancel`, `DELETE`, o SSE) não exigem tipo de conteúdo.

O `/health` fica fora das camadas 2, 3 e 4 (FR-033: mesmo caminho, campos e significado do contrato da API base); o
`Host` continua valendo para ele. Implementado como ASGI puro (não `BaseHTTPMiddleware`) para não bufferizar o SSE.

Importa só `starlette`/`fastapi` e `artigo2md.api.errors`.
"""
from __future__ import annotations

import logging
import re
from collections.abc import Iterable, MutableMapping
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from starlette.datastructures import Headers
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .errors import error_body, error_response

logger = logging.getLogger("artigo2md.api")

API_PREFIX = "/v1"
_HOST_RE = re.compile(r"^(?:127\.0\.0\.1|localhost)(?::[0-9]{1,5})?$", re.IGNORECASE)
HOST_REFUSED_MESSAGE = "O cabeçalho Host não é aceito; acesse a API por 127.0.0.1 ou localhost."
CORS_REFUSED_MESSAGE = "O método ou os cabeçalhos pedidos no preflight CORS não são permitidos."
MAX_BODY_BYTES = 16 * 1024 * 1024  # 16 MiB (contracts/http-api.md, "Corpo")

# Enumerados em `contracts/http-api.md`, "CORS" (tarefa 2.2.1).
CORS_METHODS = ["GET", "POST", "PATCH", "DELETE", "OPTIONS"]
CORS_REQUEST_HEADERS = ["Content-Type"]
CORS_EXPOSED_HEADERS = ["Content-Disposition"]

_BODY_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_JSON_MEDIA_TYPE = "application/json"


def _is_business_path(scope: Scope) -> bool:
    path = str(scope.get("path", ""))
    return path == API_PREFIX or path.startswith(API_PREFIX + "/")


def _header_values(scope: Scope, name: bytes) -> list[str]:
    return [value.decode("latin-1") for key, value in scope.get("headers", ()) if key == name]


class HostGuard:
    """`Host` só `127.0.0.1` ou `localhost` (com porta opcional): 400 com corpo de erro em português (FR-027, FR-031)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in ("http", "websocket"):
            host = Headers(scope=scope).get("host", "")
            if not _HOST_RE.match(host.strip()):
                if scope["type"] == "websocket":
                    await send({"type": "websocket.close", "code": 1008})
                    return
                logger.warning("Pedido recusado: cabeçalho Host não autorizado.")  # o valor não é registrado
                await error_response("invalid_request", HOST_REFUSED_MESSAGE)(scope, receive, send)
                return
        await self.app(scope, receive, send)


class ApiCORSMiddleware(CORSMiddleware):
    """`CORSMiddleware` com a recusa de preflight (método/cabeçalho fora da lista) no corpo de erro em português."""

    def preflight_response(self, request_headers: Headers) -> Response:
        response = super().preflight_response(request_headers)
        if response.status_code == 200:
            return response
        kept = {name: value for name, value in response.headers.items() if name.lower().startswith("access-control-")}
        return JSONResponse(error_body("invalid_request", CORS_REFUSED_MESSAGE), status_code=400, headers=kept)


class BusinessCORS:
    """Aplica o CORS só a `/v1`: o `/health` não tem preflight (`OPTIONS /health` nunca é 2xx, FR-033)."""

    def __init__(self, app: ASGIApp, **cors_options: Any) -> None:
        self.app = app
        self.cors = ApiCORSMiddleware(app, **cors_options)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in ("http", "websocket") and _is_business_path(scope):
            await self.cors(scope, receive, send)
            return
        await self.app(scope, receive, send)


class OriginGuard:
    """Recusa `Origin` fora da lista efetiva nas rotas de `/v1` (403 `origin_not_allowed`)."""

    def __init__(self, app: ASGIApp, allowed_origins: Iterable[str]) -> None:
        self.app = app
        self.allowed = frozenset(allowed_origins)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in ("http", "websocket") and _is_business_path(scope):
            origins = _header_values(scope, b"origin")
            # Mais de um `Origin` é recusado: um navegador nunca o envia; só um cliente que tenta confundir a checagem.
            if origins and (len(origins) > 1 or origins[0] not in self.allowed):
                if scope["type"] == "websocket":
                    await send({"type": "websocket.close", "code": 1008})
                    return
                logger.warning("Pedido recusado: origem não autorizada.")  # o valor não é registrado (pode ser texto livre)
                await error_response("origin_not_allowed")(scope, receive, send)
                return
        await self.app(scope, receive, send)


class BodyGuard:
    """Exige `application/json` e limita o tamanho do corpo dos pedidos de `/v1` que trazem corpo."""

    def __init__(self, app: ASGIApp, max_body_bytes: int = MAX_BODY_BYTES) -> None:
        self.app = app
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not _is_business_path(scope):
            await self.app(scope, receive, send)
            return

        declared = _header_values(scope, b"content-length")
        chunked = bool(_header_values(scope, b"transfer-encoding"))
        length: int | None = None
        if declared:
            text = declared[0].strip()
            if len(declared) > 1 or not (text.isascii() and text.isdigit()):
                await error_response("invalid_request", "O cabeçalho Content-Length é inválido.")(scope, receive, send)
                return
            length = int(text)
        has_body = chunked or (length is not None and length > 0)

        if has_body and scope["method"] in _BODY_METHODS:
            content_type = (_header_values(scope, b"content-type") or [""])[0]
            media_type = content_type.split(";", 1)[0].strip().lower()
            if media_type != _JSON_MEDIA_TYPE:
                await error_response("unsupported_media_type")(scope, receive, send)
                return
        if length is not None and length > self.max_body_bytes:
            await error_response("payload_too_large")(scope, receive, send)  # sem ler uma linha do corpo
            return
        if not has_body:
            await self.app(scope, receive, send)
            return
        await self._run_counting(scope, receive, send)

    async def _run_counting(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Conta os bytes à medida que chegam. Estourou o limite: responde 413, entrega `http.disconnect` à rota (que
        para de ler) e descarta o que ela ainda tentar enviar."""
        state: MutableMapping[str, Any] = {"received": 0, "rejected": False, "started": False}

        async def counting_receive() -> Message:
            if state["rejected"]:
                return {"type": "http.disconnect"}
            message = await receive()
            if message["type"] == "http.request":
                state["received"] += len(message.get("body", b""))
                if state["received"] > self.max_body_bytes:
                    state["rejected"] = True
                    if not state["started"]:
                        state["started"] = True
                        await error_response("payload_too_large")(scope, receive, send)
                    return {"type": "http.disconnect"}
            return message

        async def guarded_send(message: Message) -> None:
            if state["rejected"]:
                return
            if message["type"] == "http.response.start":
                state["started"] = True
            await send(message)

        try:
            await self.app(scope, counting_receive, guarded_send)
        except Exception:
            # A rota viu o `http.disconnect` que entregamos e abortou a leitura (ClientDisconnect ou erro de
            # leitura do corpo): a resposta 413 já foi enviada, não há mais nada a fazer.
            if not state["rejected"]:
                raise


def install_security(app: FastAPI, allowed_origins: Iterable[str], *, max_body_bytes: int = MAX_BODY_BYTES) -> None:
    """Monta as camadas na ordem certa. `add_middleware` põe o último adicionado por fora; por isso a ordem de
    chamada abaixo é a inversa da execução."""
    origins = list(allowed_origins)
    app.add_middleware(BodyGuard, max_body_bytes=max_body_bytes)
    app.add_middleware(
        BusinessCORS,
        allow_origins=origins,
        allow_credentials=False,
        allow_methods=CORS_METHODS,
        allow_headers=CORS_REQUEST_HEADERS,
        expose_headers=CORS_EXPOSED_HEADERS,
    )
    app.add_middleware(OriginGuard, allowed_origins=origins)
    app.add_middleware(HostGuard)
