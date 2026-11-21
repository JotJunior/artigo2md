import os

import pytest

from artigo2md import engines, router
from artigo2md.pdfscan import Analysis, analyze


def test_analyze_detects_text_and_tables(article_pdf):
    a = analyze(article_pdf)
    assert a.pages == 2
    assert a.text_pages == 2 and a.scanned_pages == 0
    assert a.table_hints == 1
    assert a.article_score > 0


def test_analyze_blank_is_scanned(blank_pdf):
    a = analyze(blank_pdf)
    assert a.fully_scanned and a.needs_ocr


def test_plan_skips_non_ocr_engines_for_scanned(monkeypatch):
    monkeypatch.setattr(engines, "mineru_available", lambda: False)
    monkeypatch.setattr(engines, "pymupdf_available", lambda: True)
    scanned = Analysis(pages=3, scanned_pages=3)
    assert router.plan(scanned) == ["docling"]
    text = Analysis(pages=3, text_pages=3)
    assert router.plan(text) == ["docling", "pymupdf", "markitdown"]
    assert router.plan(text, "markitdown") == ["markitdown"]


def test_plan_full_order_with_all_engines(monkeypatch):
    monkeypatch.setattr(engines, "mineru_available", lambda: True)
    monkeypatch.setattr(engines, "pymupdf_available", lambda: True)
    text = Analysis(pages=3, text_pages=3)
    assert router.plan(text) == ["docling", "pymupdf", "mineru", "markitdown"]


def test_plan_without_optional_engines(monkeypatch):
    monkeypatch.setattr(engines, "mineru_available", lambda: False)
    monkeypatch.setattr(engines, "pymupdf_available", lambda: False)
    assert router.plan(Analysis(pages=1, text_pages=1)) == ["docling", "markitdown"]


def test_engine_table_lists_four_engines():
    table = engines.engine_table()
    assert [e["id"] for e in table] == ["docling", "markitdown", "pymupdf", "mineru"]
    assert all(e["install"] for e in table if not e["required"])
    assert all(e["license"] == "MIT" for e in table if e["required"])


@pytest.mark.skipif(not engines.pymupdf_available(), reason="pymupdf4llm não instalado")
def test_pymupdf_end_to_end(article_pdf):
    result = router.convert(article_pdf, engine="pymupdf")
    assert result.engine == "pymupdf"
    assert "randomized trial" in result.markdown


def test_plan_formula_heavy_prefers_mineru(monkeypatch):
    monkeypatch.setattr(engines, "mineru_available", lambda: True)
    heavy = Analysis(pages=2, text_pages=2, formula_score=30)
    assert router.plan(heavy)[0] == "mineru"


def test_count_markdown_tables():
    md = "| a | b |\n|---|---|\n| 1 | 2 |\n\ntexto\n\n| x |\n| :---: |\n| y |\n"
    assert router.count_markdown_tables(md) == 2
    html = "texto\n<table><tr><td>a</td></tr></table>\n<TABLE border=1></TABLE>"
    assert router.count_markdown_tables(html) == 2
    assert router.count_markdown_tables("---\n\ntexto\n\n***") == 0


def test_evaluate_penalizes_missing_tables():
    a = Analysis(pages=1, text_pages=1, total_chars=400, table_hints=1)
    bad = router.evaluate(a, engines.EngineOutput("x", "texto " * 100, tables=0))
    good = router.evaluate(a, engines.EngineOutput("x", "texto " * 100, tables=1))
    assert not bad.accepted and good.accepted


def test_markitdown_end_to_end(article_pdf):
    result = router.convert(article_pdf, engine="markitdown")
    assert result.engine == "markitdown"
    assert "randomized trial" in result.markdown


def test_auto_falls_back_when_engine_errors(article_pdf, monkeypatch):
    monkeypatch.setattr(engines, "mineru_available", lambda: False)
    monkeypatch.setattr(engines, "pymupdf_available", lambda: False)

    def boom(*_a, **_k):
        raise RuntimeError("modelo indisponível")

    monkeypatch.setattr(engines, "run_docling", boom)
    result = router.convert(article_pdf)
    assert result.engine == "markitdown"
    assert result.attempts[0].error


@pytest.mark.skipif(not os.environ.get("ARTIGO2MD_SLOW"), reason="lento: defina ARTIGO2MD_SLOW=1")
def test_docling_end_to_end(article_pdf):
    result = router.convert(article_pdf, engine="docling")
    assert result.engine.startswith("docling")
    assert "randomized trial" in result.markdown
    assert router.count_markdown_tables(result.markdown) >= 1


def test_is_gpu_oom():
    assert engines.is_gpu_oom("torch.OutOfMemoryError: CUDA out of memory. Tried to allocate 564.00 MiB")
    assert engines.is_gpu_oom(RuntimeError("MPS backend out of memory"))
    assert not engines.is_gpu_oom("MemoryError")
    assert not engines.is_gpu_oom("arquivo corrompido")


def test_mineru_retries_on_cpu_when_gpu_is_full(article_pdf, monkeypatch, tmp_path):
    """Placa cheia (outro programa usando a GPU) → refaz com MINERU_DEVICE_MODE=cpu."""
    import subprocess
    import types
    from pathlib import Path

    monkeypatch.delenv("MINERU_DEVICE_MODE", raising=False)
    monkeypatch.setattr(engines, "mineru_status", lambda: (True, "versão 3.4.5"))
    monkeypatch.setattr(engines, "_mineru_exe", lambda: "mineru")
    calls = []

    def fake_run(cmd, env, **kw):
        calls.append(env.get("MINERU_DEVICE_MODE"))
        assert env["MINERU_TELEMETRY_DISABLED"] == "1"
        if env.get("MINERU_DEVICE_MODE") != "cpu":
            return types.SimpleNamespace(returncode=1, stdout="", stderr="CUDA out of memory.")
        out = Path(cmd[cmd.index("-o") + 1]) / "doc" / "auto"
        out.mkdir(parents=True)
        (out / "doc.md").write_text("# ok", encoding="utf-8")
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = engines.run_mineru(article_pdf)
    assert calls == [None, "cpu"]
    assert result.engine == "mineru+cpu" and result.markdown == "# ok"


def test_mineru_other_errors_do_not_retry(article_pdf, monkeypatch):
    import subprocess
    import types

    monkeypatch.setattr(engines, "mineru_status", lambda: (True, "versão 3.4.5"))
    monkeypatch.setattr(engines, "_mineru_exe", lambda: "mineru")
    calls = []

    def fake_run(cmd, env, **kw):
        calls.append(1)
        return types.SimpleNamespace(returncode=1, stdout="", stderr="PDF inválido")

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match="PDF inválido"):
        engines.run_mineru(article_pdf)
    assert calls == [1]
