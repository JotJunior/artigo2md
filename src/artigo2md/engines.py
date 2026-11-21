"""Motores de conversão PDF → Markdown.

- docling    (MIT)  — principal; layout, 2 colunas, tabelas, OCR quando preciso.
- markitdown (MIT)  — simples; último recurso para PDFs de texto corrido.
- pymupdf    (AGPL) — opcional (`uv sync --extra pymupdf`): pymupdf4llm, reserva muito rápida.
- mineru     (AGPL) — opcional, instalado à parte: usado se o comando `mineru` existir
                      (ou se ARTIGO2MD_MINERU apontar para ele). Bom para fórmulas.

Os motores AGPL nunca são dependência obrigatória: assim o projeto continua MIT.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path

# Nada sai da máquina: desliga a telemetria do Hugging Face Hub, que o Docling usa para
# baixar modelos. O hub lê a variável ao ser importado, por isso ela é definida aqui,
# antes de qualquer import do Docling.
os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"


@dataclass
class EngineOutput:
    engine: str
    markdown: str
    tables: int | None = None  # tabelas estruturadas pelo motor (None = não informado)
    extra: dict = field(default_factory=dict)


class EngineUnavailable(RuntimeError):
    pass


def install_hint(engine_id: str) -> str:
    """Comando que instala o motor: o da ficha, para os opcionais; `uv sync`, para os incluídos."""
    for info in ENGINE_INFO:
        if info["id"] == engine_id:
            return info["install"] or "uv sync"
    raise ValueError(f"Motor desconhecido: {engine_id}")


# ── Docling ──────────────────────────────────────────────────────────────────
_docling_lock = threading.Lock()
_docling_cache: dict[bool, object] = {}


def _docling_pipeline_options(ocr: bool):
    """Opções do pipeline de PDF: tabelas no modo preciso, OCR sob demanda, nada remoto."""
    from docling.datamodel.pipeline_options import PdfPipelineOptions, TableFormerMode

    opts = PdfPipelineOptions()
    opts.do_table_structure = True
    opts.table_structure_options.mode = TableFormerMode.ACCURATE
    opts.table_structure_options.do_cell_matching = True
    opts.do_ocr = ocr
    if ocr:
        opts.ocr_options = _ocr_options()
    # Serviços remotos desligados de forma explícita, sem depender do padrão do Docling.
    # Versões antigas do Docling não têm o campo.
    if hasattr(opts, "enable_remote_services"):
        opts.enable_remote_services = False
    return opts


def _docling_converter(ocr: bool):
    """Cria (uma vez) o conversor; carregar os modelos é a parte cara."""
    with _docling_lock:
        if ocr in _docling_cache:
            return _docling_cache[ocr]
        from docling.datamodel.base_models import InputFormat
        from docling.document_converter import DocumentConverter, PdfFormatOption

        converter = DocumentConverter(
            format_options={
                InputFormat.PDF: PdfFormatOption(pipeline_options=_docling_pipeline_options(ocr))
            }
        )
        _docling_cache[ocr] = converter
        return converter


def _ocr_options():
    """No Mac usa o OCR nativo da Apple (rápido, sem GPU); nos demais, o automático do Docling."""
    from docling.datamodel import pipeline_options as po

    if sys.platform == "darwin" and hasattr(po, "OcrMacOptions"):
        return po.OcrMacOptions(lang=["pt-BR", "en-US"])
    if hasattr(po, "OcrAutoOptions"):
        return po.OcrAutoOptions()
    return po.EasyOcrOptions(lang=["pt", "en"])


def run_docling(path: Path, ocr: bool = False) -> EngineOutput:
    try:
        converter = _docling_converter(ocr)
    except ImportError as exc:
        raise EngineUnavailable(
            f"Docling não está instalado. Rode: {install_hint('docling')}"
        ) from exc
    result = converter.convert(str(path))
    doc = result.document
    return EngineOutput(
        engine="docling" + ("+ocr" if ocr else ""),
        markdown=doc.export_to_markdown(),
        tables=len(doc.tables),
        extra={"status": str(result.status)},
    )


# ── MarkItDown ───────────────────────────────────────────────────────────────
def run_markitdown(path: Path) -> EngineOutput:
    try:
        from markitdown import MarkItDown
    except ImportError as exc:
        raise EngineUnavailable(
            f"MarkItDown não está instalado. Rode: {install_hint('markitdown')}"
        ) from exc

    text = MarkItDown(enable_plugins=False).convert(str(path)).text_content
    return EngineOutput(engine="markitdown", markdown=text or "")


# ── pymupdf4llm (opcional, AGPL) ─────────────────────────────────────────────
def pymupdf_available() -> bool:
    try:
        import pymupdf4llm  # noqa: F401
        return True
    except Exception:
        return False


def run_pymupdf(path: Path) -> EngineOutput:
    try:
        import pymupdf4llm
    except ImportError as exc:
        raise EngineUnavailable(
            f"pymupdf4llm não está instalado (opcional). Rode: {install_hint('pymupdf')}"
        ) from exc
    return EngineOutput(engine="pymupdf", markdown=pymupdf4llm.to_markdown(str(path)))


# ── MinerU (opcional, processo externo) ──────────────────────────────────────
def _mineru_exe() -> str | None:
    custom = os.environ.get("ARTIGO2MD_MINERU")
    if custom:
        return custom if os.access(custom, os.X_OK) else None
    return shutil.which("mineru")


_mineru_cache: dict[str, tuple[bool, str]] = {}


def mineru_status() -> tuple[bool, str]:
    """(utilizável, detalhe). Só a linha 3.x serve: a 4.x mudou a CLI para um servidor
    em segundo plano, vem com o processamento local desligado e telemetria ligada."""
    exe = _mineru_exe()
    if not exe:
        return False, "não instalado"
    if exe in _mineru_cache:
        return _mineru_cache[exe]
    try:
        out = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=60)
        text = (out.stdout + out.stderr).strip()
    except Exception as exc:
        return False, f"não respondeu: {exc}"
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", text)
    if not match:
        result = (False, f"versão não reconhecida: {text[:80]}")
    elif int(match.group(1)) != 3:
        result = (False, f"versão {match.group(0)} incompatível; instale a 3.x: "
                         'uv tool install --force "mineru[core]>=3.4,<4" --with six')
    else:
        result = (True, f"versão {match.group(0)}")
    _mineru_cache[exe] = result
    return result


def mineru_available() -> bool:
    return mineru_status()[0]


def run_mineru(path: Path, timeout: int = 1800) -> EngineOutput:
    ok, detail = mineru_status()
    if not ok:
        # O detalhe de versão incompatível já traz o comando; nos outros casos, vem da ficha.
        hint = "" if "instale" in detail else f" Instale com: {install_hint('mineru')}."
        raise EngineUnavailable(f"MinerU indisponível ({detail}).{hint} Veja o README.")
    env = dict(os.environ, MINERU_TELEMETRY_DISABLED="1")  # nada sai da máquina
    with tempfile.TemporaryDirectory(prefix="artigo2md-mineru-") as tmp:
        proc = subprocess.run(
            [_mineru_exe(), "-p", str(path), "-o", tmp],
            capture_output=True, text=True, timeout=timeout, env=env,
        )
        mds = sorted(Path(tmp).rglob("*.md"), key=lambda p: p.stat().st_size, reverse=True)
        if proc.returncode != 0 or not mds:
            raise RuntimeError("MinerU falhou: " + (proc.stderr or proc.stdout)[-600:])
        return EngineOutput(engine="mineru", markdown=mds[0].read_text(encoding="utf-8"))


def available_engines() -> dict[str, bool]:
    def importable(mod: str) -> bool:
        try:
            __import__(mod)
            return True
        except Exception:
            return False

    return {
        "docling": importable("docling"),
        "markitdown": importable("markitdown"),
        "pymupdf": pymupdf_available(),
        "mineru": mineru_available(),
    }


# Ficha de cada motor (mostrada na interface e no `doctor`).
ENGINE_INFO = [
    {"id": "docling", "name": "Docling", "license": "MIT", "required": True,
     "role": "Principal: 2 colunas, tabelas estruturadas, OCR em páginas escaneadas",
     "weight": "pesado (~1–2 GB de modelos)", "install": None},
    {"id": "markitdown", "name": "MarkItDown", "license": "MIT", "required": True,
     "role": "Último recurso para PDFs de texto corrido",
     "weight": "leve", "install": None},
    {"id": "pymupdf", "name": "pymupdf4llm", "license": "AGPL-3.0", "required": False,
     "role": "Reserva rápida: menos de 1 s por artigo, bom em 2 colunas",
     "weight": "leve (~30 MB)", "install": "uv sync --extra pymupdf"},
    {"id": "mineru", "name": "MinerU", "license": "AGPL-3.0", "required": False,
     "role": "Fórmulas e layouts complexos",
     "weight": "pesado (vários GB)", "install": 'uv tool install "mineru[core]>=3.4,<4" --with six'},
]


def engine_table() -> list[dict]:
    status = available_engines()
    rows = [dict(info, installed=status[info["id"]], detail=None) for info in ENGINE_INFO]
    for row in rows:
        if row["id"] == "mineru":
            ok, detail = mineru_status()
            if not ok and detail != "não instalado":
                row["detail"] = detail  # instalado, mas versão errada
    return rows
