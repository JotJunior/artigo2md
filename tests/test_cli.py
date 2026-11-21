"""Testes da linha de comando: `artigo2md convert`, `serve` e `doctor`.

A função de entrada é chamada em processo, com a lista de argumentos, e devolve o
código de saída (ver docs/specs/command-line-interface/research.md, Decision 4).
"""
from pathlib import Path

import pytest

from artigo2md import cli, router


@pytest.fixture
def run_cli(capsys):
    """Chama a linha de comando e devolve (código de saída, stdout, stderr)."""

    def _run(*args) -> tuple[int, str, str]:
        code = cli.main([str(arg) for arg in args])
        captured = capsys.readouterr()
        return code, captured.out, captured.err

    return _run


@pytest.fixture
def fake_convert(monkeypatch):
    """Troca a conversão real por uma simulação rápida e registra cada chamada.

    Serve aos testes que exercitam a lógica da linha de comando, não a conversão.
    """
    calls: list[dict] = []

    def _convert(path, engine="auto", progress=None):
        path = Path(path)
        calls.append({"path": path, "engine": engine})
        if progress:
            progress("Convertendo (simulado)")
        quality = router.Quality(
            score=100.0, accepted=True, tables_found=0, tables_expected=0, chars_per_page=0.0
        )
        return router.ConversionResult(
            source=str(path),
            markdown=f"# {path.stem}\n\nconteúdo simulado\n",
            engine="simulado",
            analysis={"pages": 1},
            attempts=[router.Attempt("simulado", 0.0, quality)],
        )

    monkeypatch.setattr(router, "convert", _convert)
    return calls


@pytest.mark.parametrize("input_kind", ["file", "folder"])
def test_convert_end_to_end_with_markitdown(run_cli, article_pdf, tmp_path, input_kind):
    source = article_pdf if input_kind == "file" else article_pdf.parent
    out_dir = tmp_path / "saida"

    code, out, _err = run_cli("convert", source, "-o", out_dir, "-e", "markitdown")

    assert code == 0
    markdown = out_dir / "artigo.md"
    assert markdown.exists()
    assert "randomized trial" in markdown.read_text(encoding="utf-8")
    assert "motor: markitdown" in out
    assert "Concluído: 1 ok, 0 com erro." in out


def test_fake_convert_replaces_real_conversion(run_cli, fake_convert, article_pdf, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    code, out, _err = run_cli("convert", article_pdf, "-e", "docling")

    assert code == 0
    assert fake_convert == [{"path": article_pdf, "engine": "docling"}]
    # sem -o, a saída vai para a pasta "markdown" do diretório atual
    assert (tmp_path / "markdown" / "artigo.md").read_text(encoding="utf-8").startswith("# artigo")
    assert "motor: simulado" in out


def test_convert_defaults_to_auto_engine(run_cli, fake_convert, article_pdf, tmp_path):
    code, _out, _err = run_cli("convert", article_pdf, "-o", tmp_path / "saida")

    assert code == 0
    assert fake_convert[0]["engine"] == "auto"
