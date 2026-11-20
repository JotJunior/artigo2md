"""Roteador: inspeciona → escolhe motor → converte → avalia → tenta outro se ficou ruim."""
from __future__ import annotations

import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from . import engines
from .pdfscan import Analysis, analyze

ENGINES = ("auto", "docling", "pymupdf", "mineru", "markitdown")
ACCEPT_SCORE = 65.0
FORMULA_HEAVY = 12

# Linha separadora de tabela Markdown: tem "|", tem "---" e só contém | - : e espaços.
# Exigir o "|" evita contar uma linha horizontal "---" como tabela.
TABLE_SEPARATOR = re.compile(r"^(?=[^\n]*\|)(?=[^\n]*-{3})[ \t|:-]+$", re.M)


@dataclass
class Quality:
    score: float
    accepted: bool
    tables_found: int
    tables_expected: int
    chars_per_page: float
    issues: list[str] = field(default_factory=list)


@dataclass
class Attempt:
    engine: str
    seconds: float
    quality: Quality | None = None
    error: str | None = None


@dataclass
class ConversionResult:
    source: str
    markdown: str
    engine: str
    analysis: dict
    attempts: list[Attempt]

    def report(self) -> dict:
        d = asdict(self)
        d.pop("markdown")
        return d


HTML_TABLE = re.compile(r"<table\b", re.I)


def count_markdown_tables(md: str) -> int:
    """Tabelas em Markdown (|---|) e em HTML (<table>, como o MinerU produz)."""
    return len(TABLE_SEPARATOR.findall(md)) + len(HTML_TABLE.findall(md))


def evaluate(analysis: Analysis, out: engines.EngineOutput) -> Quality:
    tables = out.tables if out.tables is not None else count_markdown_tables(out.markdown)
    expected = analysis.table_hints
    pages = max(1, analysis.pages)
    density = len(out.markdown.strip()) / pages
    score, issues = 100.0, []
    if expected and not tables:
        score -= 45
        issues.append(f"O PDF cita tabelas em {expected} página(s), mas nenhuma foi estruturada.")
    if density < 100:
        score -= 35
        issues.append("Markdown muito curto para a quantidade de páginas.")
    if analysis.total_chars and len(out.markdown) < 0.5 * analysis.total_chars:
        score -= 20
        issues.append("Saída tem menos da metade do texto nativo do PDF.")
    score = max(0.0, min(100.0, score))
    return Quality(
        score=score,
        accepted=score >= ACCEPT_SCORE,
        tables_found=tables,
        tables_expected=expected,
        chars_per_page=round(density, 1),
        issues=issues,
    )


def plan(analysis: Analysis, requested: str = "auto") -> list[str]:
    """Ordem de motores a tentar."""
    if requested != "auto":
        return [requested]
    has_text = not analysis.fully_scanned  # pymupdf e markitdown não fazem OCR
    order: list[str] = []
    if analysis.formula_score >= FORMULA_HEAVY and engines.mineru_available():
        order.append("mineru")
    order.append("docling")
    if has_text and engines.pymupdf_available():
        order.append("pymupdf")  # reserva rápida: custa menos de 1 s tentar
    if engines.mineru_available() and "mineru" not in order:
        order.append("mineru")
    if has_text:
        order.append("markitdown")
    return order


def _run(name: str, path: Path, analysis: Analysis) -> engines.EngineOutput:
    if name == "docling":
        return engines.run_docling(path, ocr=analysis.needs_ocr)
    if name == "markitdown":
        return engines.run_markitdown(path)
    if name == "pymupdf":
        return engines.run_pymupdf(path)
    if name == "mineru":
        return engines.run_mineru(path)
    raise ValueError(f"Motor desconhecido: {name}")


def convert(
    path: str | Path,
    engine: str = "auto",
    progress: Callable[[str], None] | None = None,
) -> ConversionResult:
    if engine not in ENGINES:
        raise ValueError(f"Motor inválido: {engine}. Opções: {', '.join(ENGINES)}")
    path = Path(path)
    say = progress or (lambda _msg: None)
    say("Inspecionando o PDF")
    analysis = analyze(path)
    attempts: list[Attempt] = []
    best: tuple[float, engines.EngineOutput] | None = None

    for name in plan(analysis, engine):
        say(f"Convertendo com {name}")
        start = time.monotonic()
        try:
            out = _run(name, path, analysis)
        except Exception as exc:  # um motor falhar não derruba os outros
            attempts.append(Attempt(name, round(time.monotonic() - start, 1), error=str(exc)[:500]))
            continue
        quality = evaluate(analysis, out)
        attempts.append(Attempt(out.engine, round(time.monotonic() - start, 1), quality))
        if best is None or quality.score > best[0]:
            best = (quality.score, out)
        if quality.accepted:
            break
        say(f"{name} não passou na avaliação ({quality.score:.0f}/100); tentando outro motor")

    if best is None:
        errors = "; ".join(f"{a.engine}: {a.error}" for a in attempts)
        raise RuntimeError(f"Nenhum motor conseguiu converter {path.name}. {errors}")
    return ConversionResult(
        source=str(path),
        markdown=best[1].markdown,
        engine=best[1].engine,
        analysis=analysis.to_dict(),
        attempts=attempts,
    )
