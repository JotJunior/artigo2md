"""Corpo de erro único (`api/errors.py`): tabela do contrato, tradução do store, validação e 500 (3.4, SC-002)."""
from __future__ import annotations

import logging
import re
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict, Field

from artigo2md.api import errors
from artigo2md.api.errors import ERRORS, ApiError, error_body, install_error_handlers, translate_store_error
from artigo2md.store import errors as se

ROOT = Path(__file__).resolve().parent.parent
CONTRACT = ROOT / "docs" / "specs" / "project-api" / "contracts" / "http-api.md"
REPOSITORY = ROOT / "src" / "artigo2md" / "store" / "repository.py"

SECRET = "senha-super-secreta-123"
RAW_DRIVER = 'invalid input syntax for type integer: "abc" DETAIL: Key (name)=(valor-sensivel) conflicts'
FORBIDDEN = (SECRET, "asyncpg", "Traceback", "sqlstate", "DETAIL", "valor-sensivel", 'File "')
_PT = re.compile(r"[áàâãéêíóôõúç]| não | de | do | da | o | a | para | em | com | ou |^[A-ZÁ]", re.IGNORECASE)


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1)
    count: int = 1


def make_app(raiser=None) -> FastAPI:
    app = FastAPI()
    install_error_handlers(app)

    @app.post("/v1/items")
    def create(body: Body):
        return {"ok": body.name}

    @app.get("/v1/page")
    def page(limit: int = Query(10, ge=1, le=100)):
        return {"limit": limit}

    @app.get("/v1/boom/{kind}")
    def boom(kind: str):
        raiser(kind)

    return app


@pytest.fixture()
def client():
    def raiser(kind):
        raise ApiError(kind) if kind in ERRORS else RuntimeError(f"{SECRET} {RAW_DRIVER}")

    return TestClient(make_app(raiser), raise_server_exceptions=False)


# ── tabela do contrato ──────────────────────────────────────────────────────────
def contract_rows() -> dict[str, int]:
    text = CONTRACT.read_text(encoding="utf-8")
    section = text.split("### Corpo de erro", 1)[1].split("## Representações", 1)[0]
    return {code: int(status) for status, code in re.findall(r"^\| (\d{3}) \| `([a-z_]+)` \|", section, re.M)}


def test_error_table_equals_the_contract_table():
    assert {code: spec.status for code, spec in ERRORS.items()} == contract_rows()


def test_data_dir_permission_denied_is_in_the_table():
    assert ERRORS["data_dir_permission_denied"].status == 503


@pytest.mark.parametrize("code", sorted(ERRORS))
def test_every_code_has_unique_body_shape_status_and_portuguese_message(client, code):
    resp = client.get(f"/v1/boom/{code}")
    assert resp.status_code == ERRORS[code].status
    assert resp.headers["content-type"].startswith("application/json")
    body = resp.json()
    assert list(body) == ["error"] and set(body["error"]) == {"code", "message"}
    assert body["error"]["code"] == code
    message = body["error"]["message"]
    assert message and message == ERRORS[code].message and _PT.search(message)
    assert not any(bad.lower() in message.lower() for bad in FORBIDDEN)


def test_api_error_accepts_custom_message_and_rejects_unknown_code():
    err = ApiError("not_found", "Projeto não encontrado.")
    assert (err.status, err.code, err.message) == (404, "not_found", "Projeto não encontrado.")
    with pytest.raises(ValueError):
        ApiError("inventado")


def test_long_messages_are_clipped():
    assert len(ApiError("not_found", "x" * 5000).message) <= errors.MAX_MESSAGE_CHARS
    assert len(error_body("not_found", "y " * 5000)["error"]["message"]) <= errors.MAX_MESSAGE_CHARS


# ── tradução das exceções do store ──────────────────────────────────────────────
@pytest.mark.parametrize("exc, status, code", [
    (se.ProjectNameTakenError("Já existe um projeto ativo com esse nome."), 409, "project_name_taken"),
    (se.ActiveJobExistsError("O documento já tem um job pendente ou em andamento."), 409, "active_job_exists"),
    (se.InvalidJobTransitionError("Transição de estado recusada."), 409, "invalid_transition"),
    (se.StoreValidationError("O projeto já está arquivado."), 409, "invalid_transition"),
    (se.StoreValidationError("O projeto já está ativo."), 409, "invalid_transition"),
    (se.StoreValidationError("O campo kind precisa ser um destes valores: folder, file."), 422, "validation_failed"),
    (se.ProjectNotFoundError("Projeto não encontrado."), 404, "not_found"),
    (se.DocumentNotFoundError("Documento não encontrado."), 404, "not_found"),
    (se.JobNotFoundError("Job não encontrado."), 404, "not_found"),
    (se.StoreNotFoundError("Registro inexistente."), 404, "not_found"),
    (se.DatabaseUnavailableError("O banco não respondeu."), 503, "database_unavailable"),
    (se.StoreConfigError("Parâmetro do banco ausente: defina POSTGRES_USER em stack/.env."), 503,
     "database_unavailable"),
    (se.SchemaTooNewError("x"), 503, "database_unavailable"),
    (se.MigrationFailedError("x"), 503, "database_unavailable"),
    (se.StoreDatabaseError(f"O banco recusou o comando (SQLSTATE 42P01). {RAW_DRIVER}"), 500, "internal_error"),
    (se.StoreRuleError("regra"), 500, "internal_error"),
])
def test_store_exceptions_are_translated(exc, status, code):
    def raiser(_):
        raise exc

    resp = TestClient(make_app(raiser), raise_server_exceptions=False).get("/v1/boom/x")
    assert (resp.status_code, resp.json()["error"]["code"]) == (status, code)
    assert translate_store_error(exc).code == code


def test_translate_store_error_ignores_foreign_exceptions():
    assert translate_store_error(RuntimeError("x")) is None


def test_store_driver_text_and_config_detail_never_reach_the_client():
    for exc in (se.StoreDatabaseError(RAW_DRIVER), se.DatabaseUnavailableError(RAW_DRIVER),
                se.StoreConfigError(f"POSTGRES_PASSWORD={SECRET}"), se.SchemaTooNewError(RAW_DRIVER)):
        def raiser(_, exc=exc):
            raise exc

        text = TestClient(make_app(raiser), raise_server_exceptions=False).get("/v1/boom/x").text
        assert not any(bad in text for bad in FORBIDDEN), text


def test_state_validation_messages_still_exist_in_the_repository():
    source = REPOSITORY.read_text(encoding="utf-8")
    for message in errors.STATE_VALIDATION_MESSAGES:
        assert f'StoreValidationError("{message}")' in source, message


# ── validação do FastAPI -> 400 invalid_request ─────────────────────────────────
def post(client, **kwargs):
    return client.post("/v1/items", **kwargs)


def test_missing_field_is_400_invalid_request_naming_the_field(client):
    resp = post(client, json={})
    assert resp.status_code == 400
    assert resp.json() == {"error": {"code": "invalid_request", "message": "O campo name é obrigatório."}}


def test_unknown_field_is_named_when_safe_without_echoing_the_value(client):
    resp = post(client, json={"name": "a", "extra": "valor-sensivel"})
    assert resp.status_code == 400
    message = resp.json()["error"]["message"]
    assert message == "O campo extra não é um campo conhecido." and "valor-sensivel" not in message


def test_unsafe_field_name_from_client_is_not_reflected(client):
    resp = post(client, json={"name": "a", "<script>alert(1)</script>": 1})
    message = resp.json()["error"]["message"]
    assert resp.status_code == 400 and "script" not in message and "alert" not in message


def test_wrong_type_never_echoes_the_value(client):
    resp = post(client, json={"name": "a", "count": f"{SECRET}"})
    assert resp.status_code == 400
    message = resp.json()["error"]["message"]
    assert "count" in message and SECRET not in message


def test_value_out_of_range_never_echoes_the_value(client):
    resp = post(client, json={"name": ""})
    assert resp.status_code == 400 and "name" in resp.json()["error"]["message"]


@pytest.mark.parametrize("query", ["limit=0", "limit=101", "limit=abc"])
def test_query_out_of_range_or_wrong_type_is_400_citing_the_field_not_the_value(client, query):
    resp = client.get(f"/v1/page?{query}")
    assert resp.status_code == 400
    message = resp.json()["error"]["message"]
    assert "limit" in message and query.split("=")[1] not in message.replace("limit", "")


def test_malformed_json_is_400_without_the_raw_body(client):
    resp = post(client, content=f'{{"name": "{SECRET}', headers={"content-type": "application/json"})
    assert resp.status_code == 400
    body = resp.json()
    assert body["error"]["code"] == "invalid_request" and SECRET not in resp.text
    assert "JSON" in body["error"]["message"]


def test_fastapi_default_422_is_never_returned(client):
    for kwargs in ({"json": {}}, {"json": [1]}, {"json": "texto"}, {"content": b"", "headers": {}}):
        assert post(client, **kwargs).status_code == 400


def test_no_validation_response_has_the_default_detail_key(client):
    assert "detail" not in post(client, json={}).json()


# ── 404/405/500 ─────────────────────────────────────────────────────────────────
def test_unknown_route_uses_the_single_error_body(client):
    resp = client.get("/v1/nada")
    assert resp.status_code == 404
    assert resp.json() == error_body("not_found")


def test_method_not_allowed_keeps_405_allow_header_and_error_body(client):
    resp = client.delete("/v1/items")
    assert resp.status_code == 405
    assert resp.json()["error"]["code"] == "method_not_allowed" and "POST" in resp.headers["allow"]


def test_unexpected_exception_is_500_internal_error_with_fixed_message(client):
    resp = client.get("/v1/boom/qualquer-coisa")
    assert resp.status_code == 500
    assert resp.json() == error_body("internal_error")
    assert not any(bad in resp.text for bad in FORBIDDEN)


def test_unexpected_exception_log_has_only_the_exception_type(client, caplog):
    with caplog.at_level(logging.DEBUG):
        client.get("/v1/boom/qualquer-coisa")
    logged = " ".join(r.getMessage() for r in caplog.records) + " ".join(
        str(r.exc_info) for r in caplog.records if r.exc_info)
    assert "RuntimeError" in logged
    assert SECRET not in logged and "valor-sensivel" not in logged


def test_no_response_body_in_any_error_path_leaks_secrets(client):
    paths = [("get", "/v1/boom/qualquer"), ("get", "/v1/zzz"), ("delete", "/v1/items"), ("post", "/v1/items")]
    for method, path in paths:
        text = getattr(client, method)(path).text
        assert not any(bad in text for bad in FORBIDDEN), (method, path, text)


# ── isolamento ──────────────────────────────────────────────────────────────────
def test_importing_errors_does_not_load_conversion_or_driver_modules():
    code = (
        "import sys, artigo2md.api.errors\n"
        "bad=[m for m in ('docling','pymupdf4llm','pypdfium2','artigo2md.router','asyncpg','pika')"
        " if m in sys.modules]\nprint(','.join(bad))\n")
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == ""
