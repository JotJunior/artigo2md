"""Fronteira da camada `artigo2md.store` e registro de licenças do driver (tarefas 7.1 e 7.3; FR-017, FR-018,
FR-021, SC-004, SC-008). Tudo sem banco e sem Docker.
"""
from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "src" / "artigo2md"
STORE = PACKAGE / "store"
LICENSES = ROOT / "stack" / "THIRD_PARTY_LICENSES.md"

# Módulos do produto atual que NUNCA podem puxar a camada de persistência nem o driver (FR-017, FR-021).
PRODUCT_MODULES = (
    "artigo2md.cli", "artigo2md.web", "artigo2md.worker", "artigo2md.router", "artigo2md.engines",
    "artigo2md.pdfscan", "artigo2md.infra", "artigo2md.api.app",
)


def _run_python(code: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=120, cwd=ROOT, check=False
    )


def test_importing_the_product_does_not_load_asyncpg_or_the_store():
    imports = "; ".join(f"import {name}" for name in PRODUCT_MODULES)
    code = (
        f"import sys; {imports}\n"
        "bad = sorted(m for m in sys.modules if m == 'asyncpg' or m.startswith('asyncpg.') "
        "or m == 'artigo2md.store' or m.startswith('artigo2md.store.'))\n"
        "print('BAD:' + ','.join(bad)) if bad else print('OK')"
    )
    result = _run_python(code)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "OK", result.stdout


def test_product_still_works_when_the_driver_is_not_installed():
    """Paridade com FR-021: `convert`, `doctor` e `serve` não exigem o driver (importação bloqueada)."""
    imports = "; ".join(f"import {name}" for name in PRODUCT_MODULES)
    code = (
        "import sys; sys.modules['asyncpg'] = None\n"  # `import asyncpg` passa a falhar com ImportError
        f"{imports}\n"
        "from artigo2md import cli\n"
        "parser_ok = callable(cli.main)\n"
        "import artigo2md.store as store\n"  # o pacote em si importa sem o driver (carregamento sob demanda)
        "print('OK' if parser_ok and store.DbSettings and store.StoreError else 'FAIL')"
    )
    result = _run_python(code)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "OK"
    doctor = _run_python(
        "import sys; sys.modules['asyncpg'] = None\n"
        "from artigo2md import cli\n"
        "sys.exit(0 if cli.main(['doctor']) in (0, 1) else 2)"  # roda até o fim; 0/1 = pronto/faltam motores
    )
    assert doctor.returncode == 0, doctor.stderr


def _imported_modules(path: Path) -> set[str]:
    """Nomes absolutos importados por um arquivo (relativos resolvidos contra `artigo2md.<pacote>`)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    package = ["artigo2md"] + list(path.relative_to(PACKAGE).parts[:-1])
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                base = node.module or ""
            else:
                prefix = package[: len(package) - (node.level - 1)]
                base = ".".join(prefix + ([node.module] if node.module else []))
            found.add(base)
            found.update(f"{base}.{alias.name}" for alias in node.names)
    return found


def test_only_the_store_package_imports_the_driver_or_the_store():
    offenders = []
    for path in sorted(PACKAGE.rglob("*.py")):
        if STORE in path.parents:
            continue
        for name in _imported_modules(path):
            if name == "asyncpg" or name.startswith("asyncpg.") or name == "artigo2md.store" or name.startswith(
                "artigo2md.store."
            ):
                offenders.append(f"{path.relative_to(ROOT)} importa {name}")
    assert offenders == []


def test_the_store_never_imports_conversion_modules_or_the_cli():
    forbidden = {"router", "engines", "pdfscan", "worker", "web", "cli"}
    offenders = []
    for path in sorted(STORE.rglob("*.py")):
        for name in _imported_modules(path):
            parts = name.split(".")
            if parts[0] == "artigo2md" and len(parts) > 1 and parts[1] in forbidden:
                offenders.append(f"{path.relative_to(ROOT)} importa {name}")
            if parts[0] in ("docling", "pymupdf4llm", "pypdfium2", "markitdown"):
                offenders.append(f"{path.relative_to(ROOT)} importa {name}")
    assert offenders == []


def test_cli_and_web_have_no_database_code_paths():
    for module in ("cli.py", "web.py", "worker.py"):
        text = (PACKAGE / module).read_text(encoding="utf-8")
        assert "asyncpg" not in text and "artigo2md.store" not in text and "from .store" not in text, module


# --- Registro de licenças e imagem da API (7.3) ---------------------------------------------------------------


def _asyncpg_row() -> list[str]:
    for line in LICENSES.read_text(encoding="utf-8").splitlines():
        if line.startswith("| asyncpg "):
            return [cell.strip() for cell in line.strip().strip("|").split("|")]
    pytest.fail("asyncpg não está no registro de licenças (stack/THIRD_PARTY_LICENSES.md)")


def test_asyncpg_is_registered_with_a_permissive_license_and_an_official_source():
    name, image, license_text, principle, source, checked = _asyncpg_row()
    assert name == "asyncpg 0.32.0" and image == "`artigo2md-api:local`"
    assert license_text == "Apache-2.0" and principle == "permissiva"
    assert source == "https://pypi.org/pypi/asyncpg/0.32.0/json"
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", checked)
    assert not re.search(r"presum|desconhecid|tbd|todo", " ".join(_asyncpg_row()), re.IGNORECASE)


def test_locked_asyncpg_matches_the_registered_version_and_api_group_stays_light():
    tomllib = pytest.importorskip("tomllib")  # Python >= 3.11
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    by_name: dict[str, list[dict]] = {}
    for pkg in lock["package"]:
        by_name.setdefault(pkg["name"], []).append(pkg)
    assert {p["version"] for p in by_name["asyncpg"]} == {"0.32.0"}
    todo = [d["name"] for d in by_name["artigo2md"][0]["dev-dependencies"]["api"]]
    assert "asyncpg" in todo
    closure: set[str] = set()
    while todo:
        name = todo.pop()
        if name in closure:
            continue
        closure.add(name)
        for pkg in by_name[name]:
            for dep in pkg.get("dependencies", []):
                todo.append(dep["name"])
    assert not closure & {"docling", "pymupdf4llm", "pymupdf", "pypdfium2", "markitdown", "torch"}
    runtime = by_name["artigo2md"][0].get("dependencies", [])
    assert "asyncpg" not in [d["name"] for d in runtime]  # fora das dependências obrigatórias (FR-021)
