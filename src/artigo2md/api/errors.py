"""Corpo de erro único da API (`contracts/http-api.md`, "Corpo de erro"; FR-031).

Toda resposta de erro de `/v1` tem a forma `{"error": {"code": ..., "message": ...}}`, com mensagem em português que
nunca contém senha, credencial, texto bruto do driver, pilha nem eco do valor enviado pelo cliente. As exceções do
`store` são traduzidas para o código da tabela; o 422 padrão do FastAPI vira `400 invalid_request`; qualquer outra
exceção vira `500 internal_error` com mensagem fixa.

Importa só `fastapi`/`starlette` e `artigo2md.store.errors` (que não carrega o driver): a API nunca importa Docling
nem pymupdf4llm (FR-029).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from ..store import errors as store_errors

logger = logging.getLogger("artigo2md.api")

MAX_MESSAGE_CHARS = 300


@dataclass(frozen=True)
class ErrorSpec:
    status: int
    message: str  # mensagem padrão, em português


# Tabela "Corpo de erro" de contracts/http-api.md (código -> status e mensagem padrão).
ERRORS: dict[str, ErrorSpec] = {
    "invalid_request": ErrorSpec(400, "A requisição é inválida."),
    "origin_not_allowed": ErrorSpec(403, "Origem não autorizada para acessar esta API."),
    "path_not_allowed": ErrorSpec(403, "O caminho informado está fora da pasta de dados permitida."),
    "not_found": ErrorSpec(404, "Recurso não encontrado."),
    "no_result": ErrorSpec(404, "O documento ainda não tem resultado de conversão."),
    "project_name_taken": ErrorSpec(409, "Já existe um projeto ativo com esse nome."),
    "active_job_exists": ErrorSpec(409, "O documento já tem um job pendente ou em andamento."),
    "invalid_transition": ErrorSpec(409, "A operação não é permitida no estado atual."),
    "export_in_progress": ErrorSpec(409, "Outra exportação está em andamento; tente de novo em instantes."),
    "payload_too_large": ErrorSpec(413, "O conteúdo enviado ou solicitado excede o limite permitido."),
    "unsupported_media_type": ErrorSpec(415, "O corpo da requisição precisa ser enviado como application/json."),
    "validation_failed": ErrorSpec(422, "Algum valor está fora do domínio aceito."),
    "too_many_streams": ErrorSpec(503, "Há conexões de acompanhamento demais abertas; feche alguma e tente de novo."),
    "database_unavailable": ErrorSpec(
        503, "O banco de dados não está disponível ou não foi migrado. Suba a stack com: sh stack/up.sh"
    ),
    "broker_unavailable": ErrorSpec(
        503, "A fila de conversão não está disponível. Suba a stack com: sh stack/up.sh"
    ),
    "data_dir_permission_denied": ErrorSpec(
        503,
        "A API não tem permissão para acessar arquivos da pasta de dados. Ajuste as permissões da pasta no seu "
        "computador; a API não usa root nem altera permissões.",
    ),
    "data_dir_unavailable": ErrorSpec(
        503, "A pasta de dados não está configurada ou é inválida. Confira ARTIGO2MD_DATA_DIR em stack/.env."
    ),
    "internal_error": ErrorSpec(500, "Erro interno da API. Tente de novo; se persistir, consulte o log."),
}

# Fora da tabela do contrato: método HTTP não permitido numa rota existente (resposta padrão do Starlette).
METHOD_NOT_ALLOWED = "method_not_allowed"
_METHOD_NOT_ALLOWED_MESSAGE = "Método HTTP não permitido para esta rota."

# `StoreValidationError` de ESTADO (e não de domínio): vira 409 `invalid_transition` (http-api.md). Os textos são os do
# `repository.py`; `tests/test_api_errors.py` confere que continuam lá.
STATE_VALIDATION_MESSAGES = frozenset({"O projeto já está arquivado.", "O projeto já está ativo."})


class ApiError(Exception):
    """Erro de API com código da tabela. `message` opcional substitui o padrão (sempre em português)."""

    def __init__(self, code: str, message: str | None = None):
        if code not in ERRORS:
            raise ValueError(f"Código de erro desconhecido: {code}")
        super().__init__(code)
        self.code = code
        self.status = ERRORS[code].status
        self.message = _clip(message) if message else ERRORS[code].message


def _clip(message: str) -> str:
    text = " ".join(message.split())
    return text if len(text) <= MAX_MESSAGE_CHARS else text[: MAX_MESSAGE_CHARS - 1] + "…"


def error_body(code: str, message: str | None = None) -> dict[str, Any]:
    spec = ERRORS[code]
    return {"error": {"code": code, "message": _clip(message) if message else spec.message}}


def error_response(code: str, message: str | None = None) -> JSONResponse:
    return JSONResponse(error_body(code, message), status_code=ERRORS[code].status)


# ── tradução das exceções do store ──────────────────────────────────────────────
def translate_store_error(exc: BaseException) -> ApiError | None:
    """Código da tabela para uma exceção do `store`; `None` se não for uma exceção conhecida do store."""
    if isinstance(exc, store_errors.ProjectNameTakenError):
        return ApiError("project_name_taken", str(exc))
    if isinstance(exc, store_errors.ActiveJobExistsError):
        return ApiError("active_job_exists", str(exc))
    if isinstance(exc, store_errors.InvalidJobTransitionError):
        return ApiError("invalid_transition", str(exc))
    if isinstance(exc, store_errors.StoreNotFoundError):
        return ApiError("not_found", str(exc))
    if isinstance(exc, store_errors.StoreValidationError):
        if str(exc) in STATE_VALIDATION_MESSAGES:
            return ApiError("invalid_transition", str(exc))
        return ApiError("validation_failed", str(exc))
    if isinstance(exc, (store_errors.DatabaseUnavailableError, store_errors.StoreConfigError)):
        return ApiError("database_unavailable")  # mensagem padrão, sem texto do store (indica `sh stack/up.sh`)
    if isinstance(exc, store_errors.MigrationError):
        return ApiError("database_unavailable")
    if isinstance(exc, store_errors.StoreError):
        return ApiError("internal_error")  # StoreDatabaseError e demais: sem detalhe do banco
    return None


# ── validação do FastAPI -> 400 invalid_request ─────────────────────────────────
_SAFE_FIELD_RE = re.compile(r"^[A-Za-z0-9_\-\[\]]{1,64}$")
_LOCATION_ROOTS = ("body", "query", "path", "header", "cookie")

_PROBLEM_TEXT = {
    "missing": "é obrigatório",
    "extra_forbidden": "não é um campo conhecido",
}


def _field_name(location: tuple[Any, ...]) -> str | None:
    """Nome do campo a partir de `loc`, sem o prefixo (`body`, `query`...). Nomes vindos do cliente só entram se
    forem seguros; caso contrário o campo não é citado (evita refletir texto arbitrário)."""
    parts = list(location)
    if parts and isinstance(parts[0], str) and parts[0] in _LOCATION_ROOTS:
        parts = parts[1:]
    names: list[str] = []
    for part in parts:
        if isinstance(part, int):
            names.append(f"[{part}]")
        elif isinstance(part, str) and _SAFE_FIELD_RE.match(part):
            names.append(part if not names else f".{part}")
        else:
            return None
    return "".join(names) or None


def validation_message(errors: list[dict[str, Any]]) -> str:
    """Mensagem em português que cita o primeiro campo problemático e nunca o valor enviado."""
    if not errors:
        return ERRORS["invalid_request"].message
    first = errors[0]
    kind = str(first.get("type", ""))
    if kind in ("json_invalid", "json_type"):
        return "O corpo da requisição não é um JSON válido."
    field = _field_name(tuple(first.get("loc", ())))
    where = f"O campo {field}" if field else "Um campo da requisição"
    if kind in _PROBLEM_TEXT:
        return f"{where} {_PROBLEM_TEXT[kind]}."
    if kind.endswith(("_type", "_parsing")) or kind in ("string_type", "int_type", "bool_type", "dict_type"):
        return f"{where} tem o tipo errado."
    if kind.startswith(("greater_than", "less_than", "too_short", "too_long", "string_too", "int_")):
        return f"{where} está fora da faixa permitida."
    return f"{where} tem formato inválido."


# ── handlers ────────────────────────────────────────────────────────────────────
async def _api_error_handler(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, ApiError)
    return error_response(exc.code, exc.message)


async def _store_error_handler(_: Request, exc: Exception) -> JSONResponse:
    api_error = translate_store_error(exc) or ApiError("internal_error")
    if api_error.code == "internal_error":
        logger.error("Erro inesperado na camada de persistência (%s).", type(exc).__name__)
    return error_response(api_error.code, api_error.message)


async def _validation_handler(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    return error_response("invalid_request", validation_message(list(exc.errors())))


async def _http_exception_handler(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    if exc.status_code == 404:
        return error_response("not_found")
    if exc.status_code == 405:
        response = JSONResponse(
            {"error": {"code": METHOD_NOT_ALLOWED, "message": _METHOD_NOT_ALLOWED_MESSAGE}}, status_code=405
        )
        allow = (exc.headers or {}).get("Allow")
        if allow:
            response.headers["Allow"] = allow
        return response
    if 400 <= exc.status_code < 500:
        return error_response("invalid_request")
    return error_response("internal_error")


async def _unhandled_handler(_: Request, exc: Exception) -> JSONResponse:
    # Só o tipo da exceção vai ao log: a mensagem e a pilha podem trazer texto do driver ou credenciais.
    logger.error("Erro interno não tratado (%s).", type(exc).__name__)
    return error_response("internal_error")


def install_error_handlers(app: FastAPI) -> None:
    """Registra os handlers do corpo de erro único na aplicação."""
    app.add_exception_handler(ApiError, _api_error_handler)
    app.add_exception_handler(store_errors.StoreError, _store_error_handler)
    app.add_exception_handler(RequestValidationError, _validation_handler)
    app.add_exception_handler(StarletteHTTPException, _http_exception_handler)
    app.add_exception_handler(Exception, _unhandled_handler)
