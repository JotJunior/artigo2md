"""Relatório de medição de RAM e padrões derivados (tarefas 7.2.6, 7.3.4 e SC-004 da `conversion-queue`).

O relatório (`ram-measurement.md`) é GERADO da saída bruta versionada ao lado (`ram-measurement.raw.json`) por
`tools/measure_worker_ram.py`; estes testes regeneram o texto e exigem que seja idêntico ao arquivo (nenhum número
digitado à mão) e que os padrões do código coincidam com os derivados da medição. Sem Docker.
"""
from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SPEC = ROOT / "docs" / "specs" / "conversion-queue"
REPORT = SPEC / "ram-measurement.md"
RAW = SPEC / "ram-measurement.raw.json"


@pytest.fixture(scope="module")
def tool():
    spec = importlib.util.spec_from_file_location("measure_worker_ram", ROOT / "tools" / "measure_worker_ram.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def raw():
    return json.loads(RAW.read_text(encoding="utf-8"))


def test_report_and_raw_output_exist():
    assert REPORT.is_file() and RAW.is_file()


def test_report_is_exactly_what_the_tool_generates_from_the_raw_output(tool, raw):
    expected = tool.render_report(raw) + "\n" + tool.render_derivation(raw)
    assert REPORT.read_text(encoding="utf-8") == expected


def test_report_has_every_section_and_item_required_by_fr_015(raw):
    text = REPORT.read_text(encoding="utf-8")
    for heading in ("## Máquina", "## PDFs do lote", "## Tamanhos de pool testados",
                    "## Consumo por processo de conversão", "## Comportamento da proteção de RAM (gate)",
                    "## Memória disponível ao longo do lote", "## Derivação dos padrões"):
        assert heading in text
    assert "`sysctl -n hw.model`" in text and "`sysctl -n hw.memsize`" in text and "`sw_vers`" in text
    assert f"`{raw['system']['hw_model']}`" in text
    for item in raw["batch"]:  # nome, páginas, perfil e SHA-256 de cada PDF
        assert item["name"] in text and item["sha256"] in text and item["profile"] in text
    assert {"texto nativo", "escaneado (só imagem)", "com tabelas"} <= {i["profile"] for i in raw["batch"]}
    for column in ("Ocioso (1ª amostra)", "Carregado (após o aquecimento)", "Pico", "Adiamentos do gate"):
        assert column in text


def test_at_least_three_distinct_pool_sizes_were_measured_successfully(raw):
    pools = {r["workers"] for r in raw["runs"] if r.get("kind") != "gate" and not r["failure"]}
    assert len(pools) >= 3 and {1, 2, 3} <= pools
    for run in raw["runs"]:
        assert run["failure"] is None and run["worker_exit_code"] == 0
        expected_jobs = run["jobs"]["warmup"] + run["jobs"]["batch"]
        assert run["events_summary"]["result"] == expected_jobs and run["events_summary"]["error"] == 0


def test_gate_run_deferred_at_least_once_and_still_finished_every_job(raw):
    gate = [r for r in raw["runs"] if r.get("kind") == "gate"]
    assert len(gate) == 1 and gate[0]["gate_deferrals"] >= 1 and gate[0]["min_free_memory_mb"] > 0
    assert gate[0]["events_summary"]["error"] == 0


def test_every_conversion_process_of_every_run_has_samples_idle_loaded_and_peak(tool, raw):
    for run in raw["runs"]:
        agg = tool.aggregate_run(run)
        assert agg["children_count"] >= 1
        for child in agg["children"].values():
            assert child["idle_mb"] < child["peak_mb"] and child["loaded_mb"] is not None


# ── padrões derivados (7.3.4, SC-004) ──────────────────────────────────────────────────────────
def test_code_defaults_coincide_with_the_values_derived_from_the_report(tool, raw):
    from artigo2md.jobqueue import settings

    derived = tool.derive_defaults(raw)
    assert settings.DEFAULT_WORKERS == derived["default_workers"]
    assert settings.DEFAULT_MIN_FREE_MEMORY_MB == derived["min_free_memory_mb"]
    # o mesmo número consta no texto do relatório, no bloco legível por máquina
    block = json.loads(re.search(r"```json\n(.*?)```", REPORT.read_text(encoding="utf-8"), re.S).group(1))
    assert block["default_workers"] == settings.DEFAULT_WORKERS
    assert block["min_free_memory_mb"] == settings.DEFAULT_MIN_FREE_MEMORY_MB


def test_default_workers_never_exceeds_the_largest_measured_pool(tool, raw):
    derived = tool.derive_defaults(raw)
    assert derived["default_workers"] <= max(derived["pool_sizes_measured"])
    assert derived["default_workers"] >= 1


def test_default_workers_and_memory_are_documented_with_the_report_citation():
    from artigo2md.jobqueue import settings

    for name in ("contracts/worker-cli.md", "spec.md", "research.md"):
        text = (SPEC / name).read_text(encoding="utf-8")
        assert "ram-measurement.md" in text, name
    cli_doc = (SPEC / "contracts" / "worker-cli.md").read_text(encoding="utf-8")
    assert f"| `--workers` | int | {settings.DEFAULT_WORKERS} " in cli_doc
    assert f"| `--min-free-memory` | int (MB) | {settings.DEFAULT_MIN_FREE_MEMORY_MB} " in cli_doc
