"""Testes da API base (contrato: docs/specs/container-stack/contracts/api-health.md). Sem Docker."""
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

from artigo2md import __version__
from artigo2md.api.app import app


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app, base_url="http://127.0.0.1")


def test_health_ok(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/json")
    body = resp.json()
    assert body == {"status": "ok", "service": "artigo2md-api", "version": __version__}


def test_health_contract_fields(client):
    """Compatibilidade (contrato §Versionamento): campos estáveis status/service/version."""
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["service"] == "artigo2md-api"
    assert isinstance(body["version"], str) and body["version"] == __version__


@pytest.mark.parametrize("path", ["/", "/outra", "/docs", "/redoc", "/openapi.json", "/health/x"])
def test_unknown_routes_return_404(client, path):
    assert client.get(path).status_code == 404


@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"])
def test_health_other_methods_not_allowed(client, method):
    assert client.request(method, "/health").status_code == 405


@pytest.mark.parametrize("host", ["127.0.0.1", "127.0.0.1:8778", "127.0.0.1:8000", "localhost", "localhost:1"])
def test_allowed_hosts_with_and_without_port(client, host):
    assert client.get("/health", headers={"Host": host}).status_code == 200


@pytest.mark.parametrize("host", ["evil.com", "evil.com:80", "192.168.0.10:8778", "0.0.0.0"])
def test_untrusted_host_returns_400(client, host):
    assert client.get("/health", headers={"Host": host}).status_code == 400


def test_importing_api_does_not_load_heavy_engines():
    code = (
        "import sys, artigo2md.api.app;"
        "bad=[m for m in ('docling','pymupdf4llm') if m in sys.modules];"
        "print(','.join(bad)); sys.exit(1 if bad else 0)"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert proc.returncode == 0, f"módulos pesados carregados: {proc.stdout.strip()}"
