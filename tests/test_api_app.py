"""Esqueleto da aplicação: `create_app`, `deps.py` e `/health` intacto (tarefa 5.3; FR-032, FR-033)."""
from __future__ import annotations

import asyncio
import importlib
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient

from artigo2md import __version__
from artigo2md.api import app as app_module
from artigo2md.api.app import create_app
from artigo2md.api.deps import Services, get_broker, get_store
from artigo2md.api.errors import ApiError
from artigo2md.api.settings import ApiSettings
from artigo2md.store import errors as store_errors
from fake_store import FakeStore

ROOT = Path(__file__).resolve().parent.parent
HEALTH = {"status": "ok", "service": "artigo2md-api", "version": __version__}
UP_HINT = "sh stack/up.sh"


def bare_settings() -> ApiSettings:
    """Nada configurado: sem pasta de dados, sem broker, sem banco (`from_env` nunca levanta)."""
    return ApiSettings.from_env({}, Path("/nonexistent-cwd-for-tests"))


def add_probe_routes(app):
    @app.get("/v1/_probe/store")
    async def probe_store(store=Depends(get_store)):
        return {"store": type(store).__name__}

    @app.post("/v1/_probe/broker")
    async def probe_broker(broker=Depends(get_broker)):
        return {"broker": type(broker).__name__}

    return app


@pytest.fixture()
def client():
    app = add_probe_routes(create_app(bare_settings()))
    with TestClient(app, base_url="http://127.0.0.1") as test_client:
        yield test_client


# ── /health intacto ─────────────────────────────────────────────────────────────
def test_health_is_identical_to_the_base_contract_with_database_and_broker_unavailable(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert response.json() == HEALTH


def test_health_does_not_touch_the_store_or_the_broker():
    calls = []

    async def factory(settings):
        calls.append("store")
        raise AssertionError("o /health não pode abrir o banco")

    app = create_app(bare_settings(), store_factory=factory)
    with TestClient(app, base_url="http://127.0.0.1") as test_client:
        assert test_client.get("/health").json() == HEALTH
    assert calls == []


@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "PATCH"])
def test_health_rejects_other_methods_with_405_and_allow(client, method):
    response = client.request(method, "/health")
    assert response.status_code == 405
    assert "GET" in response.headers["allow"]


@pytest.mark.parametrize("host", ["evil.com", "192.168.0.10:8778", "0.0.0.0"])
def test_health_still_refuses_untrusted_hosts(client, host):
    assert client.get("/health", headers={"Host": host}).status_code == 400


# ── documentação automática desligada ───────────────────────────────────────────
@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect", "/v1/docs"])
def test_automatic_docs_are_off(client, path):
    response = client.get(path)
    assert response.status_code == 404


def test_unknown_v1_route_is_404_with_the_error_body(client):
    response = client.get("/v1/nao-existe")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


# ── 503 em português com a dica da stack ────────────────────────────────────────
def test_routes_that_need_the_database_answer_503_when_it_is_not_configured(client):
    response = client.get("/v1/_probe/store")
    assert response.status_code == 503
    body = response.json()["error"]
    assert body["code"] == "database_unavailable"
    assert UP_HINT in body["message"]


def test_routes_that_need_the_broker_answer_503_when_there_is_no_publisher(client):
    response = client.post("/v1/_probe/broker")
    assert response.status_code == 503
    body = response.json()["error"]
    assert body["code"] == "broker_unavailable"
    assert UP_HINT in body["message"]


def test_database_failure_message_has_no_driver_text_or_host():
    async def factory(settings):
        raise store_errors.DatabaseUnavailableError("Não foi possível conectar ao Postgres em segredo-host:5432.")

    app = add_probe_routes(create_app(bare_settings(), store_factory=factory))
    with TestClient(app, base_url="http://127.0.0.1") as test_client:
        response = test_client.get("/v1/_probe/store")
    assert response.status_code == 503
    assert "segredo-host" not in response.text
    assert UP_HINT in response.json()["error"]["message"]


def test_the_api_recovers_by_itself_when_the_database_comes_back():
    attempts = []

    async def factory(settings):
        attempts.append(1)
        if len(attempts) < 3:
            raise store_errors.DatabaseUnavailableError("fora")
        return FakeStore()

    app = add_probe_routes(create_app(bare_settings(), store_factory=factory))
    with TestClient(app, base_url="http://127.0.0.1") as test_client:
        assert test_client.get("/v1/_probe/store").status_code == 503
        assert test_client.get("/v1/_probe/store").status_code == 503
        ok = test_client.get("/v1/_probe/store")
        assert ok.status_code == 200 and ok.json() == {"store": "FakeStore"}
        assert test_client.get("/v1/_probe/store").status_code == 200
        assert test_client.get("/health").json() == HEALTH
    assert len(attempts) == 3  # depois de aberto, o Store é reaproveitado


def test_concurrent_first_requests_open_the_store_only_once():
    opened = []

    async def factory(settings):
        opened.append(1)
        await asyncio.sleep(0.05)
        return FakeStore()

    services = Services(bare_settings(), store_factory=factory)

    async def go():
        return await asyncio.gather(*(services.get_store() for _ in range(10)))

    stores = asyncio.run(go())
    assert len(opened) == 1 and all(item is stores[0] for item in stores)


def test_injected_store_and_broker_are_used_and_the_injected_store_is_not_closed():
    store = FakeStore()
    closed = []

    async def close():
        closed.append(1)

    store.close = close  # type: ignore[attr-defined]

    class Publisher:
        async def publish(self, message):
            return None

    app = add_probe_routes(create_app(bare_settings(), store=store, broker=Publisher()))
    with TestClient(app, base_url="http://127.0.0.1") as test_client:
        assert test_client.get("/v1/_probe/store").json() == {"store": "FakeStore"}
        assert test_client.post("/v1/_probe/broker").json() == {"broker": "Publisher"}
    assert closed == []  # quem injetou é dono do ciclo de vida


def test_a_store_opened_by_the_app_is_closed_on_shutdown():
    closed = []

    class Closable(FakeStore):
        async def close(self):
            closed.append(1)

    async def factory(settings):
        return Closable()

    app = add_probe_routes(create_app(bare_settings(), store_factory=factory))
    with TestClient(app, base_url="http://127.0.0.1") as test_client:
        assert test_client.get("/v1/_probe/store").status_code == 200
    assert closed == [1]


def test_a_failure_while_closing_never_breaks_shutdown():
    class Broken(FakeStore):
        async def close(self):
            raise RuntimeError("senha-secreta")

    async def factory(settings):
        return Broken()

    app = add_probe_routes(create_app(bare_settings(), store_factory=factory))
    with TestClient(app, base_url="http://127.0.0.1") as test_client:
        test_client.get("/v1/_probe/store")


def test_settings_without_database_raise_api_error_not_a_crash():
    services = Services(bare_settings())
    with pytest.raises(Exception) as exc:
        asyncio.run(services.get_store())
    assert isinstance(exc.value, (ApiError, store_errors.StoreError))


# ── segurança montada no app real ───────────────────────────────────────────────
def test_create_app_applies_the_origin_policy_to_v1_but_not_to_health(client):
    refused = client.get("/v1/_probe/store", headers={"Origin": "https://evil.example"})
    assert refused.status_code == 403 and refused.json()["error"]["code"] == "origin_not_allowed"
    assert client.get("/health", headers={"Origin": "https://evil.example"}).status_code == 200
    allowed = client.get("/v1/_probe/store", headers={"Origin": "tauri://localhost"})
    assert allowed.status_code == 503  # passou pela origem; falhou depois, por falta de banco
    assert allowed.headers["access-control-allow-origin"] == "tauri://localhost"


def test_create_app_instances_are_independent():
    first, second = create_app(bare_settings()), create_app(bare_settings())
    assert first is not second and first.state.services is not second.state.services


# ── alvo do uvicorn ─────────────────────────────────────────────────────────────
def test_uvicorn_target_is_a_lazily_built_fastapi_app():
    first = app_module.app
    assert first is app_module.app
    with TestClient(first, base_url="http://127.0.0.1") as test_client:
        assert test_client.get("/health").json() == HEALTH


def test_unknown_module_attribute_raises_attribute_error():
    with pytest.raises(AttributeError):
        app_module.nao_existe  # noqa: B018


def test_dockerfile_target_still_points_to_the_same_module_attribute():
    dockerfile = (ROOT / "stack" / "api" / "Dockerfile").read_text(encoding="utf-8")
    assert '"artigo2md.api.app:app"' in dockerfile
    assert hasattr(importlib.import_module("artigo2md.api.app"), "app")


def test_importing_the_app_module_is_lightweight():
    code = (
        "import sys, artigo2md.api.app;"
        "bad=[m for m in sys.modules if m in ('asyncpg','fastapi.testclient') or m.startswith(('asyncpg.','artigo2md.store'))];"
        "print(','.join(bad)); sys.exit(1 if bad else 0)"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=ROOT)
    assert proc.returncode == 0, proc.stdout
