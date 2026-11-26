"""Confinamento de caminhos ao diretório de dados (FR-025, SC-003; research Decision 10; plan S-01, S-07).

Regra única e testável:

- `to_container_path`: converte um caminho do HOST para o caminho dentro do container trocando o prefixo
  `ARTIGO2MD_DATA_DIR_HOST` por `ARTIGO2MD_DATA_DIR_MOUNT`. Recusa vazio, `\\0`, não absoluto e qualquer componente
  `..`, `.` ou vazio; o prefixo tem fronteira de componente e a comparação é exata (sensível a maiúsculas).
- `open_confined`: abre o arquivo primeiro (`O_RDONLY | O_NOFOLLOW | O_NONBLOCK`), confere `S_ISREG` por `fstat` e confere que o
  caminho REAL do descritor aberto está sob o `realpath` do ponto de montagem (fecha a corrida de CWE-367, S-07). O
  caminho real do descritor vem de `/proc/self/fd/<fd>` (Linux, o container) ou de `fcntl.F_GETPATH` (macOS, os
  testes sem Docker); `(st_dev, st_ino)` do descritor MUST igualar o do caminho resolvido. Sem mecanismo disponível a
  leitura é RECUSADA (fail-closed).
- `check_document_path` / `check_copied_path`: regras de FR-008 e FR-006 para o que o cliente cadastra.

Nenhuma função lê o conteúdo do arquivo nem lista diretórios. Mensagens em português, sem eco do caminho do host
completo nos erros de confinamento (só nas recusas de cadastro, que precisam citar o caminho — US1-8).
"""
from __future__ import annotations

import errno
import logging
import os
import stat
from pathlib import PurePosixPath

from .errors import ApiError

logger = logging.getLogger("artigo2md.api")

MAX_PATH_BYTES = 1024  # `PC_PATH_MAX` (queue-messages.md, "Limites de entrada")
MAX_PATHS_PER_REQUEST = 10_000  # decisão 1.1.2 (spec.md, limites): mais que isso num pedido = 413
SOURCES_DIRNAME = "sources"
_SHOWN_CHARS = 120

_O_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_O_NONBLOCK = getattr(os, "O_NONBLOCK", 0)  # um FIFO no diretório de dados não pode travar a abertura


# ── fronteira de componente ─────────────────────────────────────────────────────
def has_unsafe_component(path: str) -> bool:
    """`..`, `.` ou componente vazio (`//`, `/` final) num caminho POSIX absoluto."""
    if not path.startswith("/"):
        return True
    return any(part in ("", ".", "..") for part in path[1:].split("/"))


def is_under(path: str, root: str) -> bool:
    """`path` é o próprio `root` ou está dentro dele, com fronteira de componente e comparação exata."""
    return path == root or path.startswith(root.rstrip("/") + "/")


# ── caminho do host -> caminho do container ─────────────────────────────────────
def _require_data_dir(data_dir_host: str | None, data_dir_mount: str | None) -> tuple[str, str]:
    if not data_dir_host or not data_dir_mount:
        raise ApiError("data_dir_unavailable")
    return data_dir_host, data_dir_mount


def to_container_path(host_path: str, data_dir_host: str | None, data_dir_mount: str | None) -> str:
    """Caminho do host -> caminho no container. `ApiError`: `data_dir_unavailable` (par não configurado) ou
    `path_not_allowed` (vazio, nulo, relativo, `..`/`.`, fora do diretório de dados)."""
    host, mount = _require_data_dir(data_dir_host, data_dir_mount)
    refused = ApiError("path_not_allowed")
    if not isinstance(host_path, str) or host_path == "" or "\0" in host_path:
        raise refused
    if has_unsafe_component(host_path) or not is_under(host_path, host):
        raise refused
    return mount + host_path[len(host):]


# ── abertura confinada ──────────────────────────────────────────────────────────
def _descriptor_real_path(fd: int) -> str | None:
    """Caminho real do arquivo aberto, ou `None` se não houver mecanismo (Linux `/proc`, macOS `F_GETPATH`)."""
    proc = f"/proc/self/fd/{fd}"
    if os.path.lexists(proc):
        try:
            return os.readlink(proc)
        except OSError:
            return None
    try:
        import fcntl

        getpath = getattr(fcntl, "F_GETPATH", None)
        if getpath is None:
            return None
        raw = fcntl.fcntl(fd, getpath, b"\0" * 1024)
        return os.fsdecode(raw.split(b"\0", 1)[0]) or None
    except (ImportError, OSError):
        return None


def _refuse(message: str, code: str = "path_not_allowed") -> ApiError:
    return ApiError(code, message)


def open_confined(container_path: str, data_dir_mount: str) -> int:
    """Abre um arquivo regular dentro do ponto de montagem e devolve o descritor (quem chama fecha). Qualquer falha de
    confinamento levanta `ApiError` e fecha o descritor; nada é lido."""
    try:
        root = os.path.realpath(data_dir_mount, strict=True)
    except OSError:
        raise ApiError("data_dir_unavailable") from None
    if not os.path.isdir(root):
        raise ApiError("data_dir_unavailable")

    try:
        fd = os.open(container_path, os.O_RDONLY | _O_NOFOLLOW | _O_CLOEXEC | _O_NONBLOCK)
    except FileNotFoundError:
        raise ApiError("not_found", "O arquivo não existe na pasta de dados.") from None
    except PermissionError:
        raise ApiError("data_dir_permission_denied") from None
    except OSError as exc:
        # ELOOP: o último componente é link simbólico (O_NOFOLLOW). ENOTDIR/ENAMETOOLONG etc.: caminho inválido.
        reason = "é um link simbólico" if exc.errno == errno.ELOOP else "não pode ser aberto"
        raise _refuse(f"O arquivo {reason}; a leitura fora de arquivo regular da pasta de dados foi recusada.") from None

    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise _refuse("O caminho não é um arquivo regular; a leitura foi recusada.")
        real = _descriptor_real_path(fd)
        if real is None:
            # Fail-closed: sem como conferir onde o descritor realmente está, nada é liberado.
            raise _refuse("Não foi possível confirmar a localização do arquivo; a leitura foi recusada.")
        if not is_under(real, root):
            raise _refuse("O arquivo está fora da pasta de dados; a leitura foi recusada.")
        try:
            resolved = os.stat(real)
        except OSError:
            raise _refuse("O arquivo mudou durante a abertura; a leitura foi recusada.") from None
        if (resolved.st_dev, resolved.st_ino) != (info.st_dev, info.st_ino):
            raise _refuse("O arquivo mudou durante a abertura; a leitura foi recusada.")
        return fd
    except BaseException:
        os.close(fd)
        raise


def open_host_file(host_path: str, data_dir_host: str | None, data_dir_mount: str | None) -> int:
    """`to_container_path` + `open_confined`: a porta de entrada para ler um arquivo dado o caminho do host."""
    container = to_container_path(host_path, data_dir_host, data_dir_mount)
    assert data_dir_mount is not None  # garantido por to_container_path
    return open_confined(container, data_dir_mount)


# ── caminhos cadastrados pelo cliente (FR-008, FR-006) ──────────────────────────
def _shown(path: str) -> str:
    """Forma segura de citar o caminho numa mensagem: controles escapados e tamanho limitado."""
    text = "".join(ch if ch.isprintable() else f"\\x{ord(ch):02x}" for ch in path)
    return text if len(text) <= _SHOWN_CHARS else text[: _SHOWN_CHARS - 1] + "…"


def check_document_path(path: str) -> str | None:
    """`None` se o caminho de documento é válido; senão, a mensagem em português que o cita (FR-008)."""
    if path == "":
        return "O caminho do documento está vazio."
    shown = _shown(path)
    if "\0" in path:
        return f"O caminho {shown} contém caractere nulo."
    if not path.startswith("/"):
        return f"O caminho {shown} não é absoluto."
    if len(path.encode("utf-8", "surrogatepass")) > MAX_PATH_BYTES:
        return f"O caminho {shown} excede {MAX_PATH_BYTES} bytes."
    if not path.lower().endswith(".pdf"):
        return f"O caminho {shown} não termina em .pdf."
    return None


def check_source_path(path: str) -> str | None:
    """`None` se o caminho de uma fonte (pasta ou arquivo) é aceitável; senão, a mensagem em português que o cita.
    A API não verifica o disco: exige só não vazio, absoluto, sem `\\0` e até `PC_PATH_MAX` bytes."""
    if path == "":
        return "O caminho da fonte está vazio."
    shown = _shown(path)
    if "\0" in path:
        return f"O caminho {shown} contém caractere nulo."
    if not path.startswith("/"):
        return f"O caminho {shown} não é absoluto."
    if len(path.encode("utf-8", "surrogatepass")) > MAX_PATH_BYTES:
        return f"O caminho {shown} excede {MAX_PATH_BYTES} bytes."
    return None


def copied_sources_root(data_dir_host: str | None, project_id: int) -> str:
    """Pasta onde o app desktop copia os arquivos de fontes `copied`: `<DATA_DIR_HOST>/sources/<project_id>`."""
    if not data_dir_host:
        raise ApiError("data_dir_unavailable")
    return str(PurePosixPath(data_dir_host) / SOURCES_DIRNAME / str(project_id))


def check_copied_path(path: str, data_dir_host: str | None, project_id: int) -> str | None:
    """`None` se `path` está sob `<DATA_DIR_HOST>/sources/<project_id>/` (fronteira de componente, sem `..`); senão, a
    mensagem em português que o cita. Sem a pasta de dados configurada levanta `data_dir_unavailable`."""
    root = copied_sources_root(data_dir_host, project_id)
    if "\0" in path or path == "" or has_unsafe_component(path) or not is_under(path, root):
        return f"O caminho {_shown(path)} está fora da pasta permitida para fontes copiadas ({root})."
    return None
