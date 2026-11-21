"""Motores de conversão PDF → Markdown.

- docling    (MIT)  — principal; layout, 2 colunas, tabelas, OCR quando preciso.
- markitdown (MIT)  — simples; último recurso para PDFs de texto corrido.
- pymupdf    (AGPL) — opcional (`uv sync --extra pymupdf`): pymupdf4llm, reserva muito rápida.
- mineru     (AGPL) — opcional, instalado à parte: usado se o comando `mineru` existir
                      (ou se ARTIGO2MD_MINERU apontar para ele). Bom para fórmulas.

Os motores AGPL nunca são dependência obrigatória: assim o projeto continua MIT.
"""
from __future__ import annotations

import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class EngineOutput:
    engine: str
    markdown: str
    tables: int | None = None  # tabelas estruturadas pelo motor (None = não informado)
    extra: dict = field(default_factory=dict)


class EngineUnavailable(RuntimeError):
    pass


# ── Docling ──────────────────────────────────────────────────────────────────
_docling_lock = threading.Lock()
_docling_cache: dict[tuple[bool, bool], object] = {}


def is_gpu_oom(exc_or_text) -> bool:
    """Falta de memória na placa de vídeo (CUDA/MPS). Comum quando outro programa usa a GPU."""
    text = str(exc_or_text).lower()
    return "out of memory" in text and ("cuda" in text or "mps" in text or "gpu" in text)


def release_gpu_memory() -> None:
    """Descarta os modelos do Docling e devolve a memória da placa (antes do MinerU,
    que roda em outro processo e precisa da GPU livre)."""
    with _docling_lock:
        _docling_cache.clear()
    import gc

    gc.collect()
    torch = sys.modules.get("torch")  # só se já foi carregado; nunca importar aqui
    if torch is not None:
        try:
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass


def _docling_converter(ocr: bool, cpu: bool = False):
    """Cria (uma vez) o conversor; carregar os modelos é a parte cara."""
    key = (ocr, cpu)
    with _docling_lock:
        if key in _docling_cache:
            return _docling_cache[key]
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import (
            PdfPipelineOptions,
            TableFormerMode,
        )
        from docling.document_converter import DocumentConverter, PdfFormatOption

        opts = PdfPipelineOptions()
        opts.do_table_structure = True
        opts.table_structure_options.mode = TableFormerMode.ACCURATE
        opts.table_structure_options.do_cell_matching = True
        opts.do_ocr = ocr
        if ocr:
            opts.ocr_options = _ocr_options()
        if cpu:
            try:
                from docling.datamodel.accelerator_options import AcceleratorDevice, AcceleratorOptions
            except ImportError:  # versões antigas do Docling
                from docling.datamodel.pipeline_options import AcceleratorDevice, AcceleratorOptions
            opts.accelerator_options = AcceleratorOptions(device=AcceleratorDevice.CPU)
        converter = DocumentConverter(
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)}
        )
        _docling_cache[key] = converter
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
    cpu = False
    try:
        result = _docling_converter(ocr).convert(str(path))
    except Exception as exc:
        if not is_gpu_oom(exc):
            raise
        # A placa está cheia (outro programa usando a GPU): refaz no processador.
        release_gpu_memory()
        cpu = True
        result = _docling_converter(ocr, cpu=True).convert(str(path))
    doc = result.document
    return EngineOutput(
        engine="docling" + ("+ocr" if ocr else "") + ("+cpu" if cpu else ""),
        markdown=doc.export_to_markdown(),
        tables=len(doc.tables),
        extra={"status": str(result.status), "gpu_fallback": cpu},
    )


# ── MarkItDown ───────────────────────────────────────────────────────────────
def run_markitdown(path: Path) -> EngineOutput:
    from markitdown import MarkItDown

    text = MarkItDown(enable_plugins=False).convert(str(path)).text_content
    return EngineOutput(engine="markitdown", markdown=text or "")


# ── pymupdf4llm (opcional, AGPL) ─────────────────────────────────────────────
def _installed(module: str) -> bool:
    """Verifica se o pacote existe SEM importá-lo (importar carrega centenas de MB)."""
    try:
        return importlib.util.find_spec(module) is not None
    except Exception:
        return False


def pymupdf_available() -> bool:
    return _installed("pymupdf4llm")


def run_pymupdf(path: Path) -> EngineOutput:
    try:
        import pymupdf4llm
    except ImportError as exc:
        raise EngineUnavailable(
            "pymupdf4llm não está instalado (opcional). Rode: uv sync --extra pymupdf"
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
        raise EngineUnavailable(f"MinerU indisponível ({detail}). Veja o README.")
    release_gpu_memory()  # o MinerU roda em outro processo e precisa da placa livre
    env = dict(os.environ, MINERU_TELEMETRY_DISABLED="1")  # nada sai da máquina

    def attempt(extra_env: dict) -> tuple[str | None, str]:
        with tempfile.TemporaryDirectory(prefix="artigo2md-mineru-") as tmp:
            proc = subprocess.run(
                [_mineru_exe(), "-p", str(path), "-o", tmp],
                capture_output=True, text=True, timeout=timeout, env=dict(env, **extra_env),
            )
            mds = sorted(Path(tmp).rglob("*.md"), key=lambda p: p.stat().st_size, reverse=True)
            if proc.returncode == 0 and mds:
                return mds[0].read_text(encoding="utf-8"), ""
            return None, proc.stderr + proc.stdout

    markdown, log = attempt({})
    cpu = False
    if markdown is None and is_gpu_oom(log) and "MINERU_DEVICE_MODE" not in os.environ:
        # A placa está cheia (outro programa usando a GPU): refaz no processador.
        cpu = True
        markdown, log = attempt({"MINERU_DEVICE_MODE": "cpu"})
    if markdown is None:
        raise RuntimeError("MinerU falhou: " + log[-600:])
    return EngineOutput(engine="mineru" + ("+cpu" if cpu else ""), markdown=markdown,
                        extra={"gpu_fallback": cpu})


def available_engines() -> dict[str, bool]:
    return {
        "docling": _installed("docling"),
        "markitdown": _installed("markitdown"),
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
