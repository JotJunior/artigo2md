"""Exportação em zip e leitura de Markdown do diretório de dados (`research.md`, Decision 11; FR-021 a FR-025; `plan`
S-04, S-07).

Toda leitura de arquivo passa pela abertura confinada de `api/paths.py` (`open_host_file`): nunca por `open()` direto
num caminho vindo do banco. As funções daqui são SÍNCRONAS e bloqueiam em disco: as rotas as chamam por
`asyncio.to_thread`.

Zip: gravado num arquivo temporário (em `/tmp`, o tmpfs do container, `size=768m`) que é REMOVIDO DO DIRETÓRIO assim que
é aberto para leitura (`unlink` com o descritor aberto): nenhum arquivo temporário sobra, mesmo se a API cair no meio da
entrega; o espaço volta quando a resposta termina e o descritor fecha. Um `.md` por documento com resultado vigente,
nome `<stem>.md` (primeira ocorrência, em ordem de `document_id`) ou `<stem>-<document_id>.md` nas colisões; nenhuma
entrada tem diretório. Arquivo que não pôde ser lido é omitido e listado em `LEIA-ME.txt` (em português). O teto de
Markdown somado (`MAX_EXPORT_BYTES`) recusa a exportação com 413.
"""
from __future__ import annotations

import logging
import os
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import IO, Any

from .errors import ApiError
from .paths import open_host_file

logger = logging.getLogger("artigo2md.api")

MAX_EXPORT_BYTES = 512 * 1024 * 1024  # S-04 (aceito pelo operador, block-001/dec-023)
CHUNK_BYTES = 64 * 1024
README_NAME = "LEIA-ME.txt"
EXPORT_TMP_DIR: str | None = None  # `None` = diretório temporário do sistema (`/tmp` no container)

TOO_LARGE_MESSAGE = "Os resultados do projeto somam mais de 512 MiB de Markdown; exporte menos documentos por vez."


@dataclass(frozen=True)
class Exportable:
    """Um documento com resultado vigente, na ordem de `document_id`."""

    document_id: int
    pdf_path: str
    markdown_path: str
    created_at: Any  # `datetime`


def read_markdown(markdown_path: str, data_dir_host: str | None, data_dir_mount: str | None) -> bytes:
    """Bytes exatos do arquivo de Markdown, lidos pela abertura confinada. Erros: `ApiError` (`path_not_allowed` 403,
    `not_found` 404, `data_dir_unavailable`/`data_dir_permission_denied` 503)."""
    fd = open_host_file(markdown_path, data_dir_host, data_dir_mount)
    with os.fdopen(fd, "rb") as handle:  # `fdopen` assume o descritor: fecha mesmo se a leitura falhar
        try:
            return handle.read()
        except PermissionError:
            raise ApiError("data_dir_permission_denied") from None


def _safe_stem(markdown_path: str) -> str:
    stem = PurePosixPath(markdown_path).stem
    stem = "".join(ch for ch in stem if ch.isprintable() and ch not in "/\\")
    return stem or "documento"


def _key(name: str) -> str:
    """Chave de comparação de nomes: o APFS padrão do macOS não diferencia maiúsculas (`Artigo.md` == `artigo.md`)."""
    return name.casefold()


def entry_names(items: list[Exportable]) -> list[str]:
    """Nome da entrada de cada item, único (sem diferenciar maiúsculas) e sem diretório (colisão:
    `<stem>-<document_id>.md`), para o zip extrair sem sobrescrever arquivos (FR-021)."""
    used: set[str] = set()
    names: list[str] = []
    for item in items:
        stem = _safe_stem(item.markdown_path)
        candidate = f"{stem}.md"
        if _key(candidate) in used:
            candidate = f"{stem}-{item.document_id}.md"
        counter = 2
        while _key(candidate) in used:  # `<stem>-<id>.md` já era o `<stem>` de outro documento
            candidate = f"{stem}-{item.document_id}-{counter}.md"
            counter += 1
        used.add(_key(candidate))
        names.append(candidate)
    return names


def _reason(exc: ApiError) -> str:
    if exc.code == "not_found":
        return "o arquivo de Markdown não existe mais na pasta de dados"
    if exc.code == "data_dir_permission_denied":
        return "sem permissão para ler o arquivo de Markdown"
    return "o arquivo de Markdown está fora da pasta de dados permitida ou não é um arquivo regular"


def _date_time(moment: Any) -> tuple[int, int, int, int, int, int]:
    parts = moment.timetuple()[:6]
    return parts if parts[0] >= 1980 else (1980, 1, 1, 0, 0, 0)


def _copy_into(archive: zipfile.ZipFile, name: str, source: IO[bytes], moment: Any, budget: list[int]) -> None:
    info = zipfile.ZipInfo(name, date_time=_date_time(moment))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o644 << 16
    with archive.open(info, "w") as target:
        while True:
            chunk = source.read(CHUNK_BYTES)
            if not chunk:
                return
            budget[0] -= len(chunk)
            if budget[0] < 0:
                raise ApiError("payload_too_large", TOO_LARGE_MESSAGE)
            target.write(chunk)


def build_zip(
    items: list[Exportable], data_dir_host: str | None, data_dir_mount: str | None, *, max_bytes: int | None = None
) -> tuple[IO[bytes], int]:
    """Monta o zip em arquivo temporário e devolve `(arquivo aberto para leitura, tamanho)`. O arquivo já foi removido
    do diretório: quem recebe o devolve fechando. `ApiError`: `payload_too_large` (teto) ou `data_dir_unavailable`."""
    if not data_dir_host or not data_dir_mount:
        raise ApiError("data_dir_unavailable")
    budget = [MAX_EXPORT_BYTES if max_bytes is None else max_bytes]
    names = entry_names(items)
    omitted: list[str] = []
    fd, name = tempfile.mkstemp(prefix="artigo2md-export-", suffix=".zip", dir=EXPORT_TMP_DIR)
    handle = os.fdopen(fd, "w+b")
    try:
        os.unlink(name)  # nenhum arquivo temporário sobra: o descritor aberto basta
        with zipfile.ZipFile(handle, "w", allowZip64=True) as archive:
            for item, entry in zip(items, names):
                try:
                    source_fd = open_host_file(item.markdown_path, data_dir_host, data_dir_mount)
                except ApiError as exc:
                    if exc.code == "data_dir_unavailable":
                        raise
                    omitted.append(f"- {PurePosixPath(item.pdf_path).name} (documento {item.document_id}): {_reason(exc)}.")
                    continue
                with os.fdopen(source_fd, "rb") as source:
                    try:
                        _copy_into(archive, entry, source, item.created_at, budget)
                    except PermissionError:
                        # A abertura funcionou e a leitura foi negada no meio da cópia: não há como omitir a entrada
                        # parcial, então a exportação inteira é recusada com o aviso de permissão (decisão 1.3.1).
                        raise ApiError("data_dir_permission_denied") from None
            if omitted:
                text = (
                    "Estes documentos concluídos não entraram na exportação:\n\n" + "\n".join(omitted) + "\n"
                )
                archive.writestr(zipfile.ZipInfo(README_NAME, date_time=_date_time(items[0].created_at)), text)
        size = handle.tell()
        handle.seek(0)
        return handle, size
    except BaseException:
        handle.close()
        raise
