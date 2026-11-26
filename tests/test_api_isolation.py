"""Isolamento dos módulos de conversão no processo da API (tarefa 5.4; FR-029, US6-4, SC-007).

Cada verificação roda em processo novo: `sys.modules` do pytest já pode ter carregado qualquer coisa.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FORBIDDEN = ("docling", "pymupdf4llm", "pypdfium2", "markitdown", "artigo2md.router", "artigo2md.engines",
             "artigo2md.pdfscan", "artigo2md.worker", "artigo2md.cli")
CONVERSION_PACKAGES = ("docling", "pymupdf4llm", "pymupdf", "pypdfium2", "markitdown", "mineru", "torch",
                       "torchvision", "transformers", "psutil")

PRELUDE = f"""
import sys
FORBIDDEN = {FORBIDDEN!r}
def loaded():
    return sorted(m for m in sys.modules if any(m == f or m.startswith(f + '.') for f in FORBIDDEN))
"""


def run_isolated(body: str) -> subprocess.CompletedProcess:
    code = PRELUDE + body + "\nbad = loaded()\nprint('BAD:' + ','.join(bad) if bad else 'OK')\nsys.exit(1 if bad else 0)\n"
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=ROOT, timeout=120)


def test_importing_the_app_module_loads_no_conversion_module():
    proc = run_isolated("import artigo2md.api.app")
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_the_uvicorn_target_and_create_app_load_no_conversion_module():
    proc = run_isolated(
        "from artigo2md.api.app import create_app\n"
        "import artigo2md.api.app as m\n"
        "app = m.app\n"          # o que o uvicorn faz na partida
        "other = create_app()\n"
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_serving_requests_loads_no_conversion_module():
    proc = run_isolated(
        "from fastapi.testclient import TestClient\n"
        "from artigo2md.api.app import create_app\n"
        "app = create_app()\n"
        "with TestClient(app, base_url='http://127.0.0.1') as c:\n"
        "    assert c.get('/health').status_code == 200\n"
        "    assert c.get('/v1/nada').status_code == 404\n"
        "    assert c.get('/v1/nada', headers={'Origin': 'https://x.example'}).status_code == 403\n"
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_starting_the_background_services_with_fakes_loads_no_conversion_module():
    """5.4.2: publicadora, consumidora e supervisor rodando (broker e banco falsos) e atendendo pedidos."""
    proc = run_isolated(
        "import sys, time, asyncio, dataclasses\n"
        "sys.path.insert(0, 'tests')\n"
        "from fastapi.testclient import TestClient\n"
        "from artigo2md.api.app import create_app\n"
        "from artigo2md.api.settings import ApiSettings, BrokerSettings\n"
        "from artigo2md.jobqueue.settings import Credentials\n"
        "from fake_amqp import FakeBroker\n"
        "from fake_store import FakeStore\n"
        "broker, store = FakeBroker(), FakeStore()\n"
        "base = ApiSettings.from_env({}, None)\n"
        "settings = dataclasses.replace(base, broker=BrokerSettings('127.0.0.1', 5672, Credentials('u', 'p', 'ambiente')))\n"
        "opts = {'connect': lambda s: broker.connection(), 'backoff_start': 0.01, 'backoff_max': 0.05, 'poll': 0.02}\n"
        "app = create_app(settings, store=store, background=True, background_options=opts)\n"
        "with TestClient(app, base_url='http://127.0.0.1') as c:\n"
        "    end = time.monotonic() + 5\n"
        "    while time.monotonic() < end and not (c.get('/v1/status').json()['consumer']['connected']\n"
        "            and c.get('/v1/status').json()['publisher']['connected']):\n"
        "        time.sleep(0.01)\n"
        "    assert c.get('/v1/status').json()['consumer']['connected'] is True\n"
        "    pid = c.post('/v1/projects', json={'name': 'P'}).json()['id']\n"
        "    body = {'kind': 'folder', 'mode': 'in_place', 'path': '/Users/a', 'documents': ['/Users/a/x.pdf']}\n"
        "    did = c.post(f'/v1/projects/{pid}/sources', json=body).json()['documents']['created'][0]['id']\n"
        "    assert c.post(f'/v1/documents/{did}/jobs', json={}).status_code == 202\n"
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_every_api_module_imports_without_loading_conversion_modules():
    modules = sorted(p.stem for p in (ROOT / "src" / "artigo2md" / "api").glob("*.py") if p.stem != "__init__")
    imports = "\n".join(f"import artigo2md.api.{name}" for name in modules)
    proc = run_isolated(imports)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_the_api_package_never_imports_conversion_modules_statically():
    offenders = []
    for path in sorted((ROOT / "src" / "artigo2md" / "api").rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r"^\s*(?:from\s+(\S+)\s+import|import\s+(\S+))", text, re.MULTILINE):
            target = match.group(1) or match.group(2)
            if target.startswith(".."):
                name = target.lstrip(".").split(".")[0]
                bad = name in ("router", "engines", "pdfscan", "worker", "web", "cli")
            else:
                head = target.split(".")[0]
                bad = head in CONVERSION_PACKAGES or target.startswith(
                    ("artigo2md.router", "artigo2md.engines", "artigo2md.pdfscan", "artigo2md.worker", "artigo2md.cli",
                     "artigo2md.web"))
            if bad:
                offenders.append(f"{path.relative_to(ROOT)}: {target}")
    assert offenders == []


def _api_group() -> list[str]:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    section = text.split("[dependency-groups]", 1)[1]
    match = re.search(r"^api\s*=\s*\[(.*?)\]", section, re.MULTILINE | re.DOTALL)
    assert match, "grupo `api` não encontrado em pyproject.toml"
    return re.findall(r'"([^"]+)"', match.group(1))


def test_no_conversion_engine_is_a_dependency_of_the_api_group():
    names = {re.split(r"[<>=!~ \[;]", item, maxsplit=1)[0].lower().replace("_", "-") for item in _api_group()}
    assert names, "grupo `api` vazio"
    assert names.isdisjoint(CONVERSION_PACKAGES), names & set(CONVERSION_PACKAGES)
    assert {"fastapi", "uvicorn", "asyncpg", "pika"} <= names


def test_the_api_image_installs_only_the_api_group():
    dockerfile = (ROOT / "stack" / "api" / "Dockerfile").read_text(encoding="utf-8")
    assert "uv sync --frozen --only-group api" in dockerfile
    assert "--extra" not in dockerfile and "--all-extras" not in dockerfile and "--group" not in dockerfile.replace(
        "--only-group", "")
