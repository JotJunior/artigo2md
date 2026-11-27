"""Testes do subcomando `artigo2md models` (contrato: docs/specs/desktop-stack-control/contracts/models-cli.md).

Sem rede e sem baixar modelo: `snapshot_download` é substituído por falsos.
"""
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from artigo2md import cli, modelcache

ROOT = Path(__file__).resolve().parents[1]
GB = 1024**3


@pytest.fixture(autouse=True)
def macos(monkeypatch):
    """O contrato só vale no macOS: os testes não dependem do SO em que rodam."""
    monkeypatch.setattr(modelcache, "platform_supported", lambda: True)


def _lines(text: str) -> list[dict]:
    return [json.loads(line) for line in text.splitlines() if line.strip()]


class FakeHub:
    """`snapshot_download` falso: cache em memória, dry-run com tamanhos e progresso por `tqdm_class`."""

    def __init__(self, present=(), sizes=None, fail_with=None, chunks=1):
        self.present = set(present)
        self.sizes = sizes or {}
        self.fail_with = fail_with
        self.chunks = chunks
        self.downloaded: list[str] = []
        self.dry_runs: list[str] = []

    def __call__(self, repo_id, *, revision, cache_dir, local_files_only=False, dry_run=False, tqdm_class=None):
        if local_files_only:
            if repo_id in self.present:
                return str(Path(cache_dir))
            raise FileNotFoundError(repo_id)
        if dry_run:
            self.dry_runs.append(repo_id)
            size = self.sizes.get(repo_id, 100)
            return [SimpleNamespace(file_size=size, will_download=True)]
        if self.fail_with is not None:
            raise self.fail_with
        self.downloaded.append(repo_id)
        bar = tqdm_class(total=self.sizes.get(repo_id, 100), unit="B", desc="Downloading bytes")
        for _ in range(self.chunks):
            bar.update(1)
        self.present.add(repo_id)
        return str(Path(cache_dir))


@pytest.fixture
def cache(tmp_path):
    (tmp_path / "x").write_text("x")  # a pasta do cache precisa de conteúdo para "presente"
    return str(tmp_path)


def _download(hub, cache, **kw):
    events: list[dict] = []
    code = modelcache.download(events.append, cache=cache, snapshot_download=hub,
                               free_bytes=kw.pop("free_bytes", lambda _c: 100 * GB), **kw)
    return code, events


# ── status ──────────────────────────────────────────────────────────────────────────────────────
def test_status_with_empty_cache_reports_absent_and_exits_zero(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(modelcache, "cache_dir", lambda: str(tmp_path / "vazio"))
    monkeypatch.setattr(modelcache, "is_present", lambda *a, **k: False)
    assert cli.main(["models", "status", "--json"]) == 0
    (event,) = _lines(capsys.readouterr().out)
    assert event["event"] == "status"
    assert event["schema_version"] == 1
    assert event["cache_dir"] == str(tmp_path / "vazio")
    assert event["models"] == [{"id": "layout", "present": False}, {"id": "tableformer", "present": False}]


def test_status_json_keys_are_snake_case(cache):
    event = modelcache.status(cache, snapshot_download=FakeHub(present={m.repo_id for m in modelcache.MODELS}))
    assert set(event) == {"event", "cache_dir", "models"}
    assert all(set(m) == {"id", "present"} and m["present"] for m in event["models"])


def test_incomplete_snapshot_counts_as_absent(cache):
    from huggingface_hub.errors import IncompleteSnapshotError

    def incomplete(*a, **k):
        raise IncompleteSnapshotError("faltam arquivos", snapshot_path=cache)

    assert modelcache.is_present(modelcache.MODELS[0], cache, snapshot_download=incomplete) is False


def test_status_uses_no_network_and_disables_telemetry(cache, monkeypatch):
    seen = {}

    def probe(repo_id, **kw):
        seen.update(kw)
        raise FileNotFoundError(repo_id)

    modelcache.status(cache, snapshot_download=probe)
    assert seen["local_files_only"] is True
    assert modelcache.cache_dir()  # força o import do hub, que liga a variável antes
    import os

    assert os.environ["HF_HUB_DISABLE_TELEMETRY"] == "1"


def test_cache_dir_follows_huggingface_constant():
    from huggingface_hub import constants

    assert modelcache.cache_dir() == str(constants.HF_HUB_CACHE)


# ── drift e isolamento ──────────────────────────────────────────────────────────────────────────
def test_models_match_docling():
    pytest.importorskip("docling", reason="Docling não está instalado: deriva não verificável")
    code = (
        "import sys\n"
        "from docling.datamodel import stage_model_specs as s\n"
        "from docling.models.stages.table_structure.table_structure_model import TableStructureModel as T\n"
        "import inspect\n"
        "spec = s.OBJECT_DETECTION_LAYOUT_HERON.model_spec\n"
        "src = inspect.getsource(T.download_models)\n"
        "print(spec.repo_id, spec.revision)\n"
        "print('docling-project/docling-models' in src and 'v2.3.0' in src)\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    if proc.returncode != 0:
        pytest.skip(f"não foi possível ler as specs do Docling instalado: {proc.stderr.strip()[-200:]}")
    heron, tableformer_ok = proc.stdout.splitlines()
    layout = modelcache.MODELS[0]
    assert heron == f"{layout.repo_id} {layout.revision}"
    assert tableformer_ok == "True"
    table = modelcache.MODELS[1]
    assert (table.repo_id, table.revision) == ("docling-project/docling-models", "v2.3.0")


def _heavy_modules_after(code: str) -> str:
    proc = subprocess.run(
        [sys.executable, "-c", code + "\nbad=[m for m in ('docling','pymupdf4llm') if m in sys.modules]\n"
         "print(','.join(bad)); sys.exit(1 if bad else 0)"],
        capture_output=True, text=True)
    assert proc.returncode == 0, f"módulos pesados carregados: {proc.stdout.strip()} {proc.stderr[-300:]}"
    return proc.stdout


def test_importing_modelcache_does_not_import_docling():
    _heavy_modules_after("import sys\nimport artigo2md.modelcache")


def test_status_path_does_not_import_docling():
    _heavy_modules_after(
        "import sys\nfrom artigo2md import modelcache\nmodelcache.status('/nonexistent-cache-dir')")


def test_convert_and_serve_do_not_import_modelcache():
    _heavy_modules_after("import sys\nimport artigo2md.cli as c\nassert 'artigo2md.modelcache' not in sys.modules")


# ── download ────────────────────────────────────────────────────────────────────────────────────
def test_download_event_order_and_content(cache):
    hub = FakeHub(sizes={"docling-project/docling-layout-heron": 300, "docling-project/docling-models": 200})
    code, events = _download(hub, cache)
    assert code == 0
    kinds = [e["event"] for e in events]
    assert kinds == ["plan", "disk", "progress", "model_done", "progress", "model_done", "result"]
    assert events[0] == {"event": "plan", "total_bytes": 500, "missing": ["layout", "tableformer"]}
    assert events[1] == {"event": "disk", "free_bytes": 100 * GB, "required_bytes": 500, "ok": True}
    assert events[2]["id"] == "layout" and events[2]["total_bytes"] == 300
    assert events[-1]["status"] == "done" and events[-1]["reason"] is None
    assert hub.downloaded == ["docling-project/docling-layout-heron", "docling-project/docling-models"]


def test_download_only_fetches_missing_models(cache):
    hub = FakeHub(present={"docling-project/docling-layout-heron"})
    code, events = _download(hub, cache)
    assert code == 0 and hub.downloaded == ["docling-project/docling-models"]
    assert events[0]["missing"] == ["tableformer"]


def test_download_with_nothing_missing_is_done_without_fetching(cache):
    hub = FakeHub(present={m.repo_id for m in modelcache.MODELS})
    code, events = _download(hub, cache)
    assert code == 0 and hub.downloaded == [] and hub.dry_runs == []
    assert [e["event"] for e in events] == ["plan", "result"]
    assert events[-1]["status"] == "done"


def test_progress_is_limited_to_one_event_per_second_per_model(cache):
    now = {"t": 0.0}

    class TickingHub(FakeHub):
        def __call__(self, repo_id, *, tqdm_class=None, **kw):
            if tqdm_class is None:
                return super().__call__(repo_id, **kw)
            bar = tqdm_class(total=100, unit="B")
            for _ in range(10):  # 10 atualizações em 5 s (0,5 s cada)
                now["t"] += 0.5
                bar.update(10)
            self.present.add(repo_id)
            return cache

    events: list[dict] = []
    modelcache.download(events.append, cache=cache, snapshot_download=TickingHub(), clock=lambda: now["t"],
                        free_bytes=lambda _c: 100 * GB)
    for model in ("layout", "tableformer"):
        n = len([e for e in events if e["event"] == "progress" and e["id"] == model])
        assert 1 <= n <= 5, (model, n)  # 5 s por modelo: no máximo 1 por segundo (cada modelo tem a sua conta)


def test_progress_values_never_decrease_and_no_bar_is_drawn(cache, capsys):
    hub = FakeHub(chunks=3)
    code, events = _download(hub, cache, clock=iter(range(0, 1000, 2)).__next__)
    progress = [e["downloaded_bytes"] for e in events if e["event"] == "progress" and e["id"] == "layout"]
    assert progress == sorted(progress) and progress[-1] == 3
    out = capsys.readouterr()
    assert out.out == "" and out.err == ""  # a barra do tqdm não desenha nada


@pytest.mark.parametrize("error, reason, code", [
    (ConnectionError("sem rede"), "no_network", 1),
    (TimeoutError("lento"), "no_network", 1),
    (OSError(28, "No space left on device"), "disk_full", 1),
    (RuntimeError("algo"), "other", 1),
])
def test_download_failure_reasons(cache, error, reason, code):
    got, events = _download(FakeHub(fail_with=error), cache)
    assert got == code
    result = events[-1]
    assert result["event"] == "result" and result["status"] == "failed" and result["reason"] == reason
    assert result["message"] == modelcache.REASON_MESSAGES[reason]
    assert "model_done" not in [e["event"] for e in events]


def test_dry_run_failure_without_network_is_reported(cache):
    from huggingface_hub.errors import DryRunError

    class Offline(FakeHub):
        def __call__(self, repo_id, *, dry_run=False, **kw):
            if dry_run:
                raise DryRunError("sem acesso")
            return super().__call__(repo_id, dry_run=dry_run, **kw)

    got, events = _download(Offline(), cache)
    assert got == 1 and events[-1]["reason"] == "no_network"
    assert [e["event"] for e in events] == ["result"]


def test_insufficient_disk_exits_7_without_downloading_any_byte(cache):
    hub = FakeHub(sizes={"docling-project/docling-layout-heron": 3 * GB, "docling-project/docling-models": GB})
    code, events = _download(hub, cache, free_bytes=lambda _c: GB)
    assert code == 7 and hub.downloaded == []
    disk = next(e for e in events if e["event"] == "disk")
    assert disk == {"event": "disk", "free_bytes": GB, "required_bytes": 4 * GB, "ok": False}
    assert events[-1]["reason"] == "disk_full"
    assert "progress" not in [e["event"] for e in events]


def test_keyboard_interrupt_exits_130_without_result(cache):
    code, events = _download(FakeHub(fail_with=KeyboardInterrupt()), cache)
    assert code == 130
    assert "result" not in [e["event"] for e in events]


# ── saída (stdout) e segredos ───────────────────────────────────────────────────────────────────
def test_stdout_is_only_json_lines_and_has_no_credentials(monkeypatch, capsys, cache):
    secret = "hf_SEGREDO_NAO_PODE_VAZAR"
    monkeypatch.setenv("HF_TOKEN", secret)
    monkeypatch.setattr(modelcache, "cache_dir", lambda: cache)
    monkeypatch.setattr(modelcache, "_hub", lambda: SimpleNamespace(
        snapshot_download=FakeHub(fail_with=RuntimeError(f"401 token {secret}")),
        utils=SimpleNamespace(tqdm=object)))
    monkeypatch.setattr(modelcache, "_free_bytes", lambda _c: 100 * GB)
    assert cli.main(["models", "download", "--json"]) == 1
    out = capsys.readouterr().out
    events = _lines(out)  # toda linha é JSON válido
    assert events and all(e["schema_version"] == 1 for e in events)
    assert secret not in out and "token" not in out.lower().replace("schema", "")


def test_human_output_without_json(monkeypatch, capsys, cache):
    monkeypatch.setattr(modelcache, "cache_dir", lambda: cache)
    monkeypatch.setattr(modelcache, "is_present", lambda spec, *a, **k: spec.id == "layout")
    assert cli.main(["models", "status"]) == 0
    assert capsys.readouterr().out.splitlines() == ["layout: presente", "tableformer: ausente"]


# ── plataforma e uso ────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("verb", ["status", "download"])
def test_outside_macos_exits_6(monkeypatch, capsys, verb):
    monkeypatch.setattr(modelcache, "platform_supported", lambda: False)
    assert cli.main(["models", verb, "--json"]) == 6
    captured = capsys.readouterr()
    assert captured.out == "" and "macOS" in captured.err


def test_invalid_usage_exits_2(capsys):
    for argv in (["models"], ["models", "apagar"], ["models", "status", "--nao-existe"]):
        with pytest.raises(SystemExit) as exc:
            cli.main(argv)
        assert exc.value.code == 2
    capsys.readouterr()


def test_help_lists_models(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--help"])
    assert exc.value.code == 0
    assert "models" in capsys.readouterr().out


# ── dependência direta ──────────────────────────────────────────────────────────────────────────
def test_huggingface_hub_is_a_direct_dependency():
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    block = text.split("dependencies = [", 1)[1].split("\n]", 1)[0]  # só a lista obrigatória do projeto
    assert '"huggingface-hub>=1.33,<2"' in block
