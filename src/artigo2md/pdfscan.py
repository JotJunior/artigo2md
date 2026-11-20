"""Inspeção rápida e local de um PDF para decidir qual motor usar.

Usa só o pypdfium2 (licença Apache/BSD) e não envia nada a lugar nenhum.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

import pypdfium2 as pdfium

ARTICLE_TERMS = re.compile(
    r"\b(abstract|methods?|results?|discussion|references|resumo|métodos?|resultados?|"
    r"discussão|referências?)\b",
    re.I,
)
TABLE_TERMS = re.compile(r"\b(table|tabela|quadro)\s*\d+", re.I)
FORMULA_TERMS = re.compile(r"[∑Σ∫√≤≥≈∂∇]|\b(equation|equação)\b", re.I)

MIN_NATIVE_CHARS = 80  # abaixo disso a página é tratada como escaneada


@dataclass
class PageInfo:
    page: int
    text_chars: int
    has_native_text: bool
    table_hints: int
    formula_score: int


@dataclass
class Analysis:
    pages: int = 0
    text_pages: int = 0
    scanned_pages: int = 0
    total_chars: int = 0
    table_hints: int = 0  # páginas que citam "Tabela N"/"Table N"
    formula_score: int = 0
    article_score: int = 0
    page_info: list[PageInfo] = field(default_factory=list)

    @property
    def fully_scanned(self) -> bool:
        return self.pages > 0 and self.scanned_pages == self.pages

    @property
    def needs_ocr(self) -> bool:
        return self.scanned_pages > 0

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("page_info")
        d["fully_scanned"] = self.fully_scanned
        return d


def analyze(path: str | Path) -> Analysis:
    pdf = pdfium.PdfDocument(str(path))
    result = Analysis(pages=len(pdf))
    try:
        for index in range(len(pdf)):
            page = pdf[index]
            textpage = page.get_textpage()
            text = textpage.get_text_range().strip()
            textpage.close()
            page.close()
            chars = len(text)
            native = chars >= MIN_NATIVE_CHARS
            tables = len(TABLE_TERMS.findall(text))
            formulas = len(FORMULA_TERMS.findall(text))
            result.total_chars += chars
            result.text_pages += native
            result.scanned_pages += not native
            result.table_hints += 1 if tables else 0
            result.formula_score += formulas
            result.article_score += len(ARTICLE_TERMS.findall(text))
            result.page_info.append(PageInfo(index + 1, chars, native, tables, formulas))
    finally:
        pdf.close()
    return result
