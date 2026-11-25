"""Teste estático das dependências do extra `queue` (FR-017, FR-020; tarefa 2.1 da `conversion-queue`).

Roda sem Docker e sem rede: lê só `pyproject.toml`, `uv.lock` e o registro de licenças.
"""
from __future__ import annotations

from pathlib import Path

import pytest

tomllib = pytest.importorskip("tomllib")  # Python >= 3.11

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
LOCK = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
LICENSES = (ROOT / "stack" / "THIRD_PARTY_LICENSES.md").read_text(encoding="utf-8")


def _names(requirements: list[str]) -> set[str]:
    out = set()
    for req in requirements:
        name = req.split(";")[0]
        for sep in ("[", "=", "<", ">", "~", "!", " "):
            name = name.split(sep)[0]
        out.add(name.strip().lower())
    return out


def test_queue_extra_pins_pika_to_the_verified_version():
    extra = PYPROJECT["project"]["optional-dependencies"]["queue"]
    assert "pika==1.5.0" in extra
    assert "psutil" in _names(extra)


def test_queue_extra_does_not_leak_into_base_dependencies():
    base = _names(PYPROJECT["project"]["dependencies"])
    assert "pika" not in base
    # o psutil só entra no pacote base por transitividade (accelerate); o projeto não o declara
    assert "psutil" not in base


def test_lock_resolves_exactly_pika_1_5_0_without_runtime_dependencies():
    pikas = [p for p in LOCK["package"] if p["name"] == "pika"]
    assert len(pikas) == 1
    assert pikas[0]["version"] == "1.5.0"
    assert not pikas[0].get("dependencies")  # nenhuma dependência de runtime para auditar


def test_license_registry_lists_the_queue_dependencies():
    section = LICENSES.split("## Dependências Python do worker nativo (extra `queue`)", 1)
    assert len(section) == 2, "seção do extra `queue` ausente do registro de licenças"
    assert "| pika 1.5.0 |" in section[1]
    assert "| psutil 7.2.2 |" in section[1]
    assert "BSD-3-Clause" in section[1]
