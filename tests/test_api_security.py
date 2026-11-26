"""Origem, CORS, tipo de conteúdo, limite de corpo e `Host` (`api/security.py`, tarefa 5.1; FR-027, FR-028)."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient

from artigo2md.api.errors import install_error_handlers
from artigo2md.api.security import (
    CORS_EXPOSED_HEADERS,
    CORS_METHODS,
    CORS_REQUEST_HEADERS,
    MAX_BODY_BYTES,
    install_security,
)
from artigo2md.api.settings import ALLOW_DEV_ORIGIN_VAR, DEV_ORIGIN, ApiSettings

ROOT = Path(__file__).resolve().parent.parent
TAURI = "tauri://localhost"
EVIL = "https://evil.example"
SMALL_LIMIT = 1024


class Probe:
    """Conta quantas vezes uma rota de verdade foi executada (a recusa tem de vir ANTES de qualquer efeito)."""

    def __init__(self) -> None:
        self.calls = 0


def make_app(origins=(TAURI,), max_body=SMALL_LIMIT) -> tuple[FastAPI, Probe]:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    probe = Probe()
    install_error_handlers(app)
    install_security(app, origins, max_body_bytes=max_body)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/v1/things")
    def list_things():
        probe.calls += 1
        return {"things": []}

    @app.post("/v1/things")
    async def create_thing(request: Request):
        probe.calls += 1
        body = await request.body()
        return {"size": len(body)}

    @app.patch("/v1/things/{thing_id}")
    async def rename_thing(thing_id: int, request: Request):
        probe.calls += 1
        return {"size": len(await request.body())}

    @app.post("/v1/things/{thing_id}/archive")
    def archive(thing_id: int):
        probe.calls += 1
        return {"ok": True}

    @app.post("/v1/jobs/{job_id}/cancel")
    def cancel(job_id: int):
        probe.calls += 1
        return {"ok": True}

    @app.delete("/v1/things/{thing_id}")
    def delete(thing_id: int):
        probe.calls += 1
        return {"ok": True}

    @app.get("/v1/events")
    def events():
        probe.calls += 1

        def stream():
            yield "event: ping\ndata: {}\n\n"

        return StreamingResponse(stream(), media_type="text/event-stream")

    return app, probe


@pytest.fixture()
def env():
    app, probe = make_app()
    return TestClient(app, base_url="http://127.0.0.1"), probe


def assert_origin_refused(response):
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "origin_not_allowed"
    assert "access-control-allow-origin" not in response.headers


# ── Origin ──────────────────────────────────────────────────────────────────────
def test_authorized_origin_is_accepted_and_gets_cors_headers(env):
    client, probe = env
    response = client.get("/v1/things", headers={"Origin": TAURI})
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == TAURI
    assert "access-control-allow-credentials" not in response.headers
    assert "Content-Disposition" in response.headers["access-control-expose-headers"]
    assert probe.calls == 1


@pytest.mark.parametrize("origin", [EVIL, "null", "", "tauri://localhost.evil.com", "TAURI://LOCALHOST",
                                    "http://localhost:1420", "http://127.0.0.1", "*"])
def test_unauthorized_origin_is_refused_before_any_effect(env, origin):
    client, probe = env
    for method, path in (("GET", "/v1/things"), ("POST", "/v1/things"), ("PATCH", "/v1/things/1"),
                         ("POST", "/v1/things/1/archive"), ("POST", "/v1/jobs/1/cancel"),
                         ("DELETE", "/v1/things/1"), ("GET", "/v1/events")):
        response = client.request(method, path, headers={"Origin": origin}, json={"name": "x"} if method in
                                  ("POST", "PATCH") and not path.endswith(("archive", "cancel")) else None)
        assert_origin_refused(response)
    assert probe.calls == 0


def test_request_without_origin_is_accepted(env):
    client, probe = env
    assert client.get("/v1/things").status_code == 200
    assert probe.calls == 1


def test_two_origin_headers_are_refused(env):
    client, probe = env
    response = client.get("/v1/things", headers=[("Origin", TAURI), ("Origin", TAURI)])
    assert_origin_refused(response)
    assert probe.calls == 0


def test_dev_origin_refused_by_default_and_accepted_only_with_the_dev_variable():
    default = ApiSettings.from_env({}, None)
    assert DEV_ORIGIN not in default.allowed_origins
    app, _ = make_app(origins=default.allowed_origins)
    assert_origin_refused(TestClient(app, base_url="http://127.0.0.1").get("/v1/things", headers={"Origin": DEV_ORIGIN}))

    enabled = ApiSettings.from_env({ALLOW_DEV_ORIGIN_VAR: "1"}, None)
    app, probe = make_app(origins=enabled.allowed_origins)
    client = TestClient(app, base_url="http://127.0.0.1")
    assert client.get("/v1/things", headers={"Origin": DEV_ORIGIN}).status_code == 200
    assert client.get("/v1/things", headers={"Origin": TAURI}).status_code == 200
    assert_origin_refused(client.get("/v1/things", headers={"Origin": EVIL}))


def test_removed_origins_variable_cannot_authorize_another_origin():
    """`ARTIGO2MD_API_ALLOWED_ORIGINS` foi removida (13.9): a lista é fixa e a variável não libera nada."""
    settings = ApiSettings.from_env({"ARTIGO2MD_API_ALLOWED_ORIGINS": f"*,{EVIL}"}, None)
    assert settings.allowed_origins == (TAURI,)
    app, probe = make_app(origins=settings.allowed_origins)
    client = TestClient(app, base_url="http://127.0.0.1")
    assert_origin_refused(client.get("/v1/things", headers={"Origin": EVIL}))
    assert client.get("/v1/things", headers={"Origin": TAURI}).status_code == 200
    assert client.get("/v1/things").status_code == 200  # cliente nativo continua funcionando


def test_empty_origin_list_authorizes_no_browser_origin():
    app, probe = make_app(origins=())
    client = TestClient(app, base_url="http://127.0.0.1")
    assert_origin_refused(client.get("/v1/things", headers={"Origin": TAURI}))
    assert client.get("/v1/things").status_code == 200


def test_health_is_not_subject_to_the_origin_policy(env):
    client, _ = env
    assert client.get("/health", headers={"Origin": EVIL}).status_code == 200


# ── preflight / CORS ────────────────────────────────────────────────────────────
def preflight(client, origin, method="POST", headers="Content-Type"):
    request_headers = {"Origin": origin, "Access-Control-Request-Method": method}
    if headers:
        request_headers["Access-Control-Request-Headers"] = headers
    return client.options("/v1/things", headers=request_headers)


@pytest.mark.parametrize("method", ["GET", "POST", "PATCH", "DELETE"])
def test_preflight_from_authorized_origin_answers_200_without_touching_the_route(env, method):
    client, probe = env
    response = preflight(client, TAURI, method)
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == TAURI
    allowed = {item.strip() for item in response.headers["access-control-allow-methods"].split(",")}
    assert allowed == set(CORS_METHODS)
    assert "content-type" in response.headers["access-control-allow-headers"].lower()
    assert "access-control-allow-credentials" not in response.headers
    assert probe.calls == 0


@pytest.mark.parametrize("origin", [EVIL, "null", "http://localhost:1420"])
def test_preflight_from_unauthorized_origin_is_refused_with_403(env, origin):
    client, probe = env
    assert_origin_refused(preflight(client, origin))
    assert probe.calls == 0


def test_preflight_with_unlisted_method_or_header_is_not_authorized(env):
    client, _ = env
    for response in (preflight(client, TAURI, "PUT"), preflight(client, TAURI, "POST", "X-Token")):
        assert response.status_code == 400
        # FR-031: corpo de erro do contrato, em português (e não o texto puro "Disallowed CORS ..." do Starlette)
        assert response.headers["content-type"].startswith("application/json")
        error = response.json()["error"]
        assert error["code"] == "invalid_request" and "preflight CORS" in error["message"]
        assert "Disallowed" not in response.text


@pytest.mark.parametrize("path", ["/health", "/health/"])
def test_cors_does_not_wrap_health_so_options_health_is_never_2xx(env, path):
    """FR-033: `OPTIONS /health` nunca é 2xx (405), mesmo com `Origin` autorizada e `Access-Control-Request-Method`."""
    client, _ = env
    response = client.options(path, headers={"Origin": TAURI, "Access-Control-Request-Method": "GET"})
    assert not 200 <= response.status_code < 300
    assert response.status_code in (404, 405)
    assert "access-control-allow-origin" not in response.headers
    if path == "/health":
        assert response.status_code == 405


def test_cors_constants_match_the_published_contract():
    contract = (ROOT / "docs/specs/project-api/contracts/http-api.md").read_text(encoding="utf-8")
    for method in CORS_METHODS:
        assert f"`{method}`" in contract
    assert CORS_REQUEST_HEADERS == ["Content-Type"] and CORS_EXPOSED_HEADERS == ["Content-Disposition"]
    assert MAX_BODY_BYTES == 16 * 1024 * 1024


# ── Content-Type (415) ──────────────────────────────────────────────────────────
@pytest.mark.parametrize("content_type", ["text/plain", "application/x-www-form-urlencoded", "multipart/form-data",
                                          "application/jsonx", "text/json", ""])
@pytest.mark.parametrize("method,path", [("POST", "/v1/things"), ("PATCH", "/v1/things/1")])
def test_wrong_content_type_with_body_is_415_before_any_effect(env, method, path, content_type):
    client, probe = env
    headers = {"Content-Type": content_type} if content_type else {}
    response = client.request(method, path, content=b'{"name": "x"}', headers=headers)
    assert response.status_code == 415
    assert response.json()["error"]["code"] == "unsupported_media_type"
    assert probe.calls == 0


@pytest.mark.parametrize("content_type", ["application/json", "Application/JSON", "application/json; charset=utf-8"])
def test_json_content_type_is_accepted(env, content_type):
    client, probe = env
    response = client.post("/v1/things", content=b'{"name": "x"}', headers={"Content-Type": content_type})
    assert response.status_code == 200
    assert probe.calls == 1


@pytest.mark.parametrize("method,path", [("POST", "/v1/things/1/archive"), ("POST", "/v1/jobs/1/cancel"),
                                         ("DELETE", "/v1/things/1"), ("GET", "/v1/things"), ("GET", "/v1/events")])
def test_routes_without_body_do_not_require_content_type(env, method, path):
    client, probe = env
    response = client.request(method, path)
    assert response.status_code == 200
    assert probe.calls == 1


def test_empty_body_with_content_length_zero_is_not_a_body(env):
    client, _ = env
    assert client.post("/v1/things/1/archive", headers={"Content-Length": "0"}).status_code == 200


# ── limite de corpo (413) ───────────────────────────────────────────────────────
def test_declared_oversize_body_is_413_without_reading_it(env):
    client, probe = env
    response = client.post("/v1/things", content=b"x" * (SMALL_LIMIT + 1), headers={"Content-Type": "application/json"})
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "payload_too_large"
    assert probe.calls == 0


def test_body_at_exactly_the_limit_is_accepted(env):
    client, _ = env
    response = client.post("/v1/things", content=b"x" * SMALL_LIMIT, headers={"Content-Type": "application/json"})
    assert response.status_code == 200
    assert response.json() == {"size": SMALL_LIMIT}


def test_streamed_oversize_body_without_content_length_is_413(env):
    client, _ = env

    def chunks():
        for _ in range(40):  # 40 x 100 B = 4000 B > 1024
            yield b"y" * 100

    response = client.post("/v1/things", content=chunks(), headers={"Content-Type": "application/json"})
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "payload_too_large"


def test_streamed_body_within_the_limit_is_accepted(env):
    client, _ = env

    def chunks():
        for _ in range(5):
            yield b"z" * 100

    response = client.post("/v1/things", content=chunks(), headers={"Content-Type": "application/json"})
    assert response.status_code == 200
    assert response.json() == {"size": 500}


def test_invalid_content_length_is_400(env):
    client, _ = env
    response = client.post("/v1/things", content=b"{}", headers={"Content-Type": "application/json",
                                                                 "Content-Length": "abc"})
    assert response.status_code in (400, 422)  # o httpx pode recusar antes; quando chega, é invalid_request


def test_415_takes_precedence_over_413(env):
    client, _ = env
    response = client.post("/v1/things", content=b"x" * (SMALL_LIMIT + 1), headers={"Content-Type": "text/plain"})
    assert response.status_code == 415


def test_oversize_error_from_an_authorized_origin_is_readable_by_the_browser(env):
    client, _ = env
    response = client.post("/v1/things", content=b"x" * (SMALL_LIMIT + 1),
                           headers={"Content-Type": "application/json", "Origin": TAURI})
    assert response.status_code == 413
    assert response.headers["access-control-allow-origin"] == TAURI


# ── SSE ─────────────────────────────────────────────────────────────────────────
def test_sse_with_authorized_origin_streams_and_with_another_is_refused(env):
    client, probe = env
    ok = client.get("/v1/events", headers={"Origin": TAURI})
    assert ok.status_code == 200
    assert ok.headers["content-type"].startswith("text/event-stream")
    assert ok.headers["access-control-allow-origin"] == TAURI
    assert "event: ping" in ok.text
    refused = client.get("/v1/events", headers={"Origin": EVIL})
    assert_origin_refused(refused)
    assert probe.calls == 1


# ── Host ────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("host", ["evil.com", "evil.com:80", "192.168.0.10:8778", "0.0.0.0"])
def test_untrusted_host_is_400_on_every_route(host):
    app, probe = make_app()
    client = TestClient(app)
    for path in ("/health", "/v1/things", "/v1/events"):
        assert client.get(path, headers={"Host": host}).status_code == 400
    assert probe.calls == 0


@pytest.mark.parametrize("host", ["127.0.0.1", "127.0.0.1:8778", "localhost", "localhost:1"])
def test_allowed_hosts_are_accepted(host):
    app, _ = make_app()
    client = TestClient(app, base_url="http://127.0.0.1")
    assert client.get("/v1/things", headers={"Host": host}).status_code == 200


def test_untrusted_host_gets_the_portuguese_error_body_with_status_400():
    app, _ = make_app()
    client = TestClient(app)
    for path in ("/health", "/v1/things"):
        response = client.get(path, headers={"Host": "evil.com"})
        assert response.status_code == 400
        assert response.headers["content-type"].startswith("application/json")
        error = response.json()["error"]
        assert error["code"] == "invalid_request" and "Host" in error["message"]
        assert "Invalid host header" not in response.text and "evil.com" not in response.text  # sem eco do valor


def test_host_check_runs_before_the_origin_check():
    app, probe = make_app()
    response = TestClient(app, base_url="http://127.0.0.1").get("/v1/things", headers={"Host": "evil.com", "Origin": EVIL})
    assert response.status_code == 400
    assert probe.calls == 0


def test_security_module_does_not_load_conversion_engines():
    code = (
        "import sys, artigo2md.api.security;"
        "bad=[m for m in ('docling','pymupdf4llm','pypdfium2','artigo2md.router','asyncpg') if m in sys.modules];"
        "print(','.join(bad)); sys.exit(1 if bad else 0)"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=ROOT)
    assert proc.returncode == 0, proc.stdout
