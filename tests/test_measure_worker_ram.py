"""Ferramenta de medição de RAM (tarefa 7.1 da `conversion-queue`): só as funções puras e o lote, sem Docker.

O script vive em `tools/` (fora do pacote instalado) e é carregado por caminho; o pacote nunca o importa.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TOOL = ROOT / "tools" / "measure_worker_ram.py"


@pytest.fixture(scope="module")
def tool():
    spec = importlib.util.spec_from_file_location("measure_worker_ram", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _proc(pid, role, rss, uss=None, footprint=None):
    return {"pid": pid, "role": role, "rss_mb": rss, "uss_mb": uss, "uss_error": None if uss else "AccessDenied",
            "footprint_mb": footprint}


def _sample(t, available, procs):
    return {"t": t, "available_mb": available, "total_mb": 16000.0, "procs": procs}


def _raw_run(metric="footprint_mb"):
    def child(pid, rss, value):
        return _proc(pid, "child", rss, value if metric == "uss_mb" else None,
                     value if metric == "footprint_mb" else None)

    samples = [
        _sample(0.5, 9000, [_proc(1, "main", 80, 60, 60), child(11, 100, 90), child(12, 110, 95)]),
        _sample(1.0, 8800, [_proc(1, "main", 80, 60, 60), child(11, 900, 800), child(12, 700, 600)]),
        _sample(1.5, 7000, [_proc(1, "main", 80, 60, 60), child(11, 1500, 1400), child(12, 1200, 1100)]),
        _sample(2.0, 6500, [_proc(1, "main", 80, 60, 60), child(11, 1400, 1300), child(12, 1300, 1250)]),
    ]
    return {"label": "pool2", "workers": 2, "min_free_memory_mb": 0, "samples": samples, "warmup_done_t": 1.2,
            "gate_deferrals": 3, "failure": None, "worker_exit_code": 0,
            "events_summary": {"result": 5, "error": 0, "progress": 9}}


# ── derivação (regra fixada na Decision 8) ─────────────────────────────────────────────────────
@pytest.mark.parametrize("peak,margin,expected", [(1000, 1.0, 1000), (1000.2, 1.0, 1001), (1500, 1.25, 1875),
                                                  (1234.5, 1.5, 1852)])
def test_min_free_memory_is_the_ceiling_of_peak_times_margin(tool, peak, margin, expected):
    assert tool.derive_min_free_memory(peak, margin) == expected


@pytest.mark.parametrize("total,reserve,peak,expected", [(16000, 4000, 2000, 6), (16000, 4000, 2500, 4),
                                                         (4000, 3500, 2000, 1), (1000, 0, 5000, 1)])
def test_default_workers_is_the_floor_and_never_below_one(tool, total, reserve, peak, expected):
    assert tool.derive_default_workers(total, reserve, peak) == expected


@pytest.mark.parametrize("args", [(0, 1.2), (-5, 1.2), (100, 0.9)])
def test_min_free_memory_rejects_invalid_input(tool, args):
    with pytest.raises(ValueError):
        tool.derive_min_free_memory(*args)


@pytest.mark.parametrize("args", [(0, 100, 10), (1000, -1, 10), (1000, 100, 0)])
def test_default_workers_rejects_invalid_input(tool, args):
    with pytest.raises(ValueError):
        tool.derive_default_workers(*args)


# ── agregação ──────────────────────────────────────────────────────────────────────────────────
def test_sample_metric_prefers_uss_then_footprint_then_rss(tool):
    assert tool.sample_metric(_proc(1, "child", 10, uss=5, footprint=7)) == ("uss_mb", 5.0)
    assert tool.sample_metric(_proc(1, "child", 10, footprint=7)) == ("footprint_mb", 7.0)
    assert tool.sample_metric(_proc(1, "child", 10)) == ("rss_mb", 10.0)


def test_aggregate_run_reports_idle_loaded_and_peak_per_child(tool):
    agg = tool.aggregate_run(_raw_run())
    assert agg["metric"] == "footprint_mb" and agg["children_count"] == 2
    first = agg["children"][11]
    assert (first["idle_mb"], first["loaded_mb"], first["peak_mb"]) == (90, 1400, 1400)
    assert (first["idle_rss_mb"], first["loaded_rss_mb"], first["peak_rss_mb"]) == (100, 1500, 1500)
    assert agg["children"][12]["peak_mb"] == 1250 and agg["children"][12]["loaded_mb"] == 1100
    assert agg["peak_per_child_mb"] == 1400
    assert agg["peak_children_total_mb"] == 2550  # 1300 + 1250 (maior soma simultânea)
    assert agg["min_available_mb"] == 6500 and agg["gate_deferrals"] == 3


def test_loaded_is_none_without_a_sample_after_warmup(tool):
    run = _raw_run()
    run["warmup_done_t"] = 99
    assert tool.aggregate_run(run)["children"][11]["loaded_mb"] is None


def test_uss_is_used_when_it_is_available_for_every_child_sample(tool):
    assert tool.aggregate_run(_raw_run("uss_mb"))["metric"] == "uss_mb"


def test_classify_child_separates_converters_from_helpers(tool):
    assert tool.classify_child(["python", "-c", "from multiprocessing.spawn import spawn_main; spawn_main()"]) == "child"
    assert tool.classify_child(["python", "-c", "from multiprocessing.resource_tracker import main"]) == "helper"


def test_helpers_are_counted_but_never_summed_with_the_converters(tool):
    run = _raw_run()
    for sample in run["samples"]:
        sample["procs"].append(_proc(99, "helper", 16, None, 9))
    agg = tool.aggregate_run(run)
    assert agg["helpers_count"] == 1 and agg["children_count"] == 2 and 99 not in agg["children"]
    assert agg["peak_children_total_mb"] == 2550


def test_children_are_never_mixed_with_the_main_process(tool):
    agg = tool.aggregate_run(_raw_run())
    assert 1 not in agg["children"]


def test_run_without_a_common_metric_is_an_error(tool):
    run = _raw_run()
    for sample in run["samples"]:
        for proc in sample["procs"]:
            proc["footprint_mb"] = proc["uss_mb"] = proc["rss_mb"] = None
    with pytest.raises(ValueError):
        tool.aggregate_run(run)


# ── utilidades ─────────────────────────────────────────────────────────────────────────────────
def test_count_gate_deferrals(tool):
    log = ("Memória livre insuficiente para iniciar mais um conversor (10 MB livres, mínimo 100 MB). Nova ...\n"
           "outra linha\nMemória livre insuficiente para iniciar mais um conversor (11 MB livres...\n")
    assert tool.count_gate_deferrals(log) == 2 and tool.count_gate_deferrals("") == 0


def test_parse_pools(tool):
    assert tool.parse_pools("3,1,2,2") == [1, 2, 3]
    for bad in ("", "0", "a", "1,-2"):
        with pytest.raises(ValueError):
            tool.parse_pools(bad)


def test_parse_footprint_json(tool):
    text = json.dumps({"processes": [{"pid": 5, "footprint": 3 * 1048576}]})
    assert tool.parse_footprint_json(text) == 3.0


def test_sha256_of(tool, tmp_path):
    f = tmp_path / "a.bin"
    f.write_bytes(b"abc")
    assert tool.sha256_of(f) == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


# ── lote sintético (7.1.4) ─────────────────────────────────────────────────────────────────────
def test_batch_has_the_three_profiles_with_manifest_and_stable_sha256(tool, tmp_path):
    from artigo2md import pdfscan

    first = tool.make_batch(tmp_path / "a")
    second = tool.make_batch(tmp_path / "b")
    assert [(i["name"], i["sha256"]) for i in first] == [(i["name"], i["sha256"]) for i in second]
    by_name = {i["name"]: i for i in first}
    assert set(by_name) == {"texto-nativo.pdf", "escaneado.pdf", "tabelas.pdf"}
    assert all(i["pages"] >= 1 and len(i["sha256"]) == 64 for i in first)
    assert not (tmp_path / "a" / "_origem-do-escaneado.pdf").exists()
    assert json.loads((tmp_path / "a" / "manifesto.json").read_text(encoding="utf-8")) == first
    scanned = pdfscan.analyze(tmp_path / "a" / "escaneado.pdf")
    assert scanned.fully_scanned and scanned.total_chars == 0
    native = pdfscan.analyze(tmp_path / "a" / "texto-nativo.pdf")
    assert not native.fully_scanned and native.total_chars > 1000
    assert pdfscan.analyze(tmp_path / "a" / "tabelas.pdf").table_hints >= 1


# ── relatório gerado da saída bruta ────────────────────────────────────────────────────────────
def _full_raw(tool):
    return {"schema": 1, "repeat": 2, "sample_interval_s": 0.5,
            "system": {"hw_model": "Mac0,0", "hw_memsize_bytes": str(16 << 30), "sw_vers": "ProductName: macOS\nProductVersion: 1",
                       "platform": "macOS-x", "python": "3.12", "psutil": "7.2.2", "total_mb": 16384.0, "machine": "arm64"},
            "batch": [{"name": "a.pdf", "pages": 3, "profile": "texto nativo", "sha256": "0" * 64, "bytes": 10}],
            "runs": [{**_raw_run(), "label": f"pool{n}", "workers": n} for n in (1, 2, 3)]}


def test_report_has_the_sections_required_by_fr_015(tool):
    text = tool.render_report(_full_raw(tool))
    for heading in ("## Máquina", "## PDFs do lote", "## Tamanhos de pool testados",
                    "## Consumo por processo de conversão", "## Comportamento da proteção de RAM (gate)",
                    "## Memória disponível ao longo do lote"):
        assert heading in text
    assert "USS indisponível" in text and "`footprint_mb`" in text
    assert text.count("### Pool ") == 3


def test_merge_raw_joins_runs_of_the_same_batch_and_machine_only(tool):
    one, two = _full_raw(tool), _full_raw(tool)
    two["runs"] = [{**_raw_run(), "label": "gate3", "workers": 3, "kind": "gate"}]
    merged = tool.merge_raw([one, two])
    assert [r["label"] for r in merged["runs"]] == ["pool1", "pool2", "pool3", "gate3"]
    assert len(one["runs"]) == 3  # o original não é alterado
    other_batch = _full_raw(tool)
    other_batch["batch"][0]["sha256"] = "1" * 64
    with pytest.raises(ValueError):
        tool.merge_raw([one, other_batch])
    other_machine = _full_raw(tool)
    other_machine["system"]["hw_model"] = "Outro"
    with pytest.raises(ValueError):
        tool.merge_raw([one, other_machine])


def test_the_package_never_imports_the_measurement_tool():
    for path in (ROOT / "src").rglob("*.py"):
        assert "measure_worker_ram" not in path.read_text(encoding="utf-8"), path
    assert "measure_worker_ram" not in (ROOT / "pyproject.toml").read_text(encoding="utf-8")
