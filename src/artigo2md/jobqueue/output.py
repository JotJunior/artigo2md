"""Destino confinado e gravação atômica do Markdown (FR-007, FR-012, FR-022).

O Markdown de um job vai para `<data_dir>/<job_id>/<stem>.md`, onde `stem` é o nome do PDF sem extensão.
`job_id` e `stem` vêm de uma mensagem de fila, ou seja, de fora do processo: ambos são validados e o
diretório criado é conferido depois de resolvido, para que nada seja gravado fora de `data_dir` (nem por
`..`, nem por symlink plantado). Só biblioteca padrão.
"""
from __future__ import annotations

import errno
import os
import stat
import tempfile
from pathlib import Path

from .schema import JOB_ID_RE

SUFFIX = ".md"
_DEFAULT_NAME_MAX = 255  # NAME_MAX usual (APFS/ext4) quando `pathconf` não responde


class OutputError(Exception):
    """Falha ao localizar o PDF ou gravar a saída. `code` é um código de erro do contrato."""

    def __init__(self, code: str, reason: str):
        super().__init__(reason)
        self.code = code
        self.reason = reason


def _invalid_path(reason: str) -> OutputError:
    return OutputError("invalid_path", reason)


# ── entrada: o PDF ──────────────────────────────────────────────────────────────
def check_pdf_path(pdf_path: str) -> Path:
    """Aceita só caminho absoluto que resolva (seguindo symlink) para arquivo regular.

    Usa `os.stat`, nunca abre o arquivo: abrir uma FIFO ou um dispositivo bloquearia o slot. Symlink
    pendente, diretório, FIFO, dispositivo e socket resultam em `pdf_not_found`.
    """
    if not isinstance(pdf_path, str) or "\x00" in pdf_path or not pdf_path:
        raise OutputError("pdf_not_found", "O caminho do PDF é inválido.")
    if not os.path.isabs(pdf_path):
        raise OutputError("pdf_not_found", f"O caminho do PDF deve ser absoluto: {pdf_path}")
    try:
        mode = os.stat(pdf_path).st_mode
    except (OSError, ValueError):
        raise OutputError("pdf_not_found", f"PDF não encontrado: {pdf_path}") from None
    if not stat.S_ISREG(mode):
        raise OutputError("pdf_not_found", f"O caminho do PDF não é um arquivo comum: {pdf_path}")
    return Path(pdf_path)


# ── saída: nome e destino ───────────────────────────────────────────────────────
def _name_max(directory: Path) -> int:
    try:
        return os.pathconf(directory, "PC_NAME_MAX")
    except (OSError, ValueError, AttributeError):
        return _DEFAULT_NAME_MAX


def validate_job_id(job_id: str) -> str:
    if not isinstance(job_id, str) or not JOB_ID_RE.match(job_id):
        raise _invalid_path("job_id inválido para nome de pasta.")
    return job_id


def derive_stem(pdf_path: str, name_max: int = _DEFAULT_NAME_MAX) -> str:
    """Nome do PDF sem extensão, recusando o que não é nome simples de arquivo."""
    if not isinstance(pdf_path, str) or "\x00" in pdf_path:
        raise _invalid_path("O nome do PDF contém caractere nulo.")
    stem = Path(pdf_path).stem
    if not stem:
        raise _invalid_path("O nome do PDF está vazio.")
    if stem.startswith("."):
        raise _invalid_path("O nome do PDF não pode começar com ponto.")
    if "/" in stem or "\\" in stem:
        raise _invalid_path("O nome do PDF não pode conter barras.")
    if len((stem + SUFFIX).encode("utf-8", "surrogateescape")) > name_max:
        raise _invalid_path("O nome do PDF é longo demais para gravar o Markdown.")
    return stem


def destination(data_dir: str | Path, job_id: str, pdf_path: str) -> Path:
    """Cria (se preciso) `<data_dir>/<job_id>/` e devolve o destino, confirmando o confinamento."""
    root = Path(data_dir).resolve(strict=True)
    validate_job_id(job_id)
    stem = derive_stem(pdf_path, _name_max(root))
    target_dir = root / job_id
    try:
        target_dir.mkdir(exist_ok=True)
        resolved = target_dir.resolve(strict=True)
    except OSError as exc:
        if exc.errno in (errno.EEXIST, errno.ELOOP, errno.ENOTDIR):
            raise _invalid_path("A pasta do job colide com um item que não é diretório.") from None
        raise _write_error(exc) from None
    if resolved != root / job_id:
        raise _invalid_path("A pasta do job resolve para fora do diretório de dados.")
    return resolved / (stem + SUFFIX)


def _write_error(exc: OSError) -> OutputError:
    if exc.errno == errno.ENAMETOOLONG:
        return _invalid_path("O nome do arquivo de saída é longo demais.")
    return OutputError("write_failed", f"Falha ao gravar o Markdown: {exc.strerror or exc}")


def write_markdown(data_dir: str | Path, job_id: str, pdf_path: str, markdown: str) -> Path:
    """Grava o Markdown de forma atômica e devolve o caminho absoluto gravado.

    Temporário oculto no mesmo diretório, `flush` + `fsync`, `os.replace`; o temporário some em qualquer
    erro. Regravar o mesmo destino é idempotente (o último vence).
    """
    target = destination(data_dir, job_id, pdf_path)
    tmp_name: str | None = None
    try:
        fd, tmp_name = tempfile.mkstemp(dir=target.parent, prefix=".artigo2md-", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(markdown)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, target)
        tmp_name = None
    except OSError as exc:
        raise _write_error(exc) from None
    finally:
        if tmp_name is not None:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
    _sync_directory(target.parent)
    return target


def _sync_directory(directory: Path) -> None:
    """Persiste o `replace` no diretório (melhor esforço: alguns sistemas não permitem)."""
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)
