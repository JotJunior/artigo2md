"""Seção de infraestrutura do `artigo2md doctor` (versão desktop, opcional).

Só biblioteca padrão. Cada serviço é testado por handshake de protocolo, sem credenciais
(contracts/doctor-infra.md): o critério de "acessível" é só "o handshake responde conforme o
contrato". Quem mede saúde de verdade é o healthcheck do Compose.

Este módulo NUNCA é importado por `artigo2md convert`.
"""
from __future__ import annotations

import http.client
import json
import os
import socket
import struct
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

HOST = "127.0.0.1"
CONNECT_TIMEOUT = 1.0  # segundos para conectar
READ_TIMEOUT = 1.0  # segundos por leitura
CHECK_DEADLINE = 2.0  # teto por serviço (conectar + ler)
TOTAL_DEADLINE = 4.5  # teto da seção inteira (SC-005: < 5 s)

# serviço -> (variável de porta, porta padrão do .env.example)
SERVICES: dict[str, tuple[str, int]] = {
    "postgres": ("ARTIGO2MD_POSTGRES_PORT", 5432),
    "rabbitmq": ("ARTIGO2MD_RABBITMQ_PORT", 5672),
    "api": ("ARTIGO2MD_API_PORT", 8778),
}

STACK_ENV_VAR = "ARTIGO2MD_STACK_ENV"

# SSLRequest do Postgres: Int32(8) + Int32(80877103), network byte order (research S05, S35).
_POSTGRES_SSL_REQUEST = struct.pack("!II", 8, 80877103)
# Cabeçalho AMQP 0-9-1 (research S15 §4.2.2).
_AMQP_HEADER = b"AMQP\x00\x00\x09\x01"

REACHABLE = "reachable"
UNREACHABLE = "unreachable"
PROTOCOL_MISMATCH = "protocol_mismatch"
INVALID_PORT = "invalid_port"

ORIGIN_ENV = "ambiente"
ORIGIN_STACK_ENV_VAR = STACK_ENV_VAR
ORIGIN_STACK_FILE = "stack/.env"
ORIGIN_DEFAULT = "padrão"


@dataclass(frozen=True)
class PortResolution:
    """Porta resolvida para um serviço, com a origem do valor."""

    service: str
    var: str
    port: int | None  # None quando o valor é inválido
    origin: str
    invalid: bool = False


@dataclass(frozen=True)
class InfraReport:
    service: str
    host: str
    port: int | None
    state: str
    message: str  # texto em português: `acessível`, `inacessível`, ...
    var: str = ""  # variável de porta (usada na mensagem de porta inválida)
    origin: str = ORIGIN_DEFAULT


# ── leitura segura do .env (só leitura, nada é executado) ───────────────────────
def parse_env_file(path: Path) -> dict[str, str]:
    """Lê `CHAVE=valor` linha a linha. Remove `export `, espaços e aspas que envolvem o valor.

    Ignora comentários e linhas vazias. Nunca executa nada (sem `eval`/`source`). Comentário no
    fim da linha não é interpretado: um valor assim é tratado como está (e a porta, se não for
    numérica, vira `invalid_port`). Arquivo ausente ou ilegível devolve `{}`.
    """
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export ") or line.startswith("export\t"):
            line = line[len("export"):].strip()
        key, sep, value = line.partition("=")
        key = key.strip()
        if not sep or not key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        values[key] = value
    return values


def _valid_port(text: str) -> int | None:
    if not (text.isascii() and text.isdecimal()):
        return None
    n = int(text)
    return n if 1 <= n <= 65535 else None


def resolve_ports(
    environ: Mapping[str, str] | None = None, cwd: Path | None = None
) -> dict[str, PortResolution]:
    """Resolve a porta de cada serviço: ambiente → ARTIGO2MD_STACK_ENV → ./stack/.env → padrão.

    Valor vazio conta como "não definido". A primeira origem que define a variável vale: se o
    valor for inválido, o serviço é reportado como `invalid_port` (sem exceção e sem cair para
    a origem seguinte).
    """
    env = os.environ if environ is None else environ
    base = Path.cwd() if cwd is None else Path(cwd)

    stack_env_path = env.get(STACK_ENV_VAR, "").strip()
    from_var_file = parse_env_file(Path(stack_env_path)) if stack_env_path else {}
    from_stack_file = parse_env_file(base / "stack" / ".env")

    sources = (
        (ORIGIN_ENV, env),
        (ORIGIN_STACK_ENV_VAR, from_var_file),
        (ORIGIN_STACK_FILE, from_stack_file),
    )
    result: dict[str, PortResolution] = {}
    for service, (var, default) in SERVICES.items():
        resolution = PortResolution(service, var, default, ORIGIN_DEFAULT)
        for origin, source in sources:
            raw = source.get(var, "")
            if raw.strip() == "":
                continue
            port = _valid_port(raw.strip())
            resolution = PortResolution(service, var, port, origin, invalid=port is None)
            break
        result[service] = resolution
    return result


# ── checagens por handshake ─────────────────────────────────────────────────────
def _connect(port: int) -> socket.socket:
    sock = socket.create_connection((HOST, port), timeout=CONNECT_TIMEOUT)
    sock.settimeout(READ_TIMEOUT)
    return sock


def _read_at_least(sock: socket.socket, wanted: int, deadline: float) -> bytes:
    """Lê até `wanted` octetos (ou EOF/prazo). Prazo absoluto em `time.monotonic()`."""
    data = b""
    while len(data) < wanted and time.monotonic() < deadline:
        chunk = sock.recv(wanted - len(data))
        if not chunk:
            break
        data += chunk
    return data


def check_postgres(port: int) -> str:
    """SSLRequest. Acessível se o 1º byte da resposta é `S`, `N` ou `E` (ErrorResponse).

    O texto do erro nunca é lido nem exibido.
    """
    deadline = time.monotonic() + CHECK_DEADLINE
    try:
        with _connect(port) as sock:
            sock.sendall(_POSTGRES_SSL_REQUEST)
            first = _read_at_least(sock, 1, deadline)
    except OSError:
        return UNREACHABLE
    return REACHABLE if first in (b"S", b"N", b"E") else UNREACHABLE


def check_rabbitmq(port: int) -> str:
    """Cabeçalho AMQP 0-9-1. Acessível se a resposta é um frame METHOD (tipo 1) no canal 0."""
    deadline = time.monotonic() + CHECK_DEADLINE
    try:
        with _connect(port) as sock:
            sock.sendall(_AMQP_HEADER)
            data = _read_at_least(sock, 4, deadline)
    except OSError:
        return UNREACHABLE
    if data[:4] == b"AMQP":
        return PROTOCOL_MISMATCH
    if len(data) >= 3 and data[0] == 1 and data[1:3] == b"\x00\x00":
        return REACHABLE
    return UNREACHABLE


def check_api(port: int) -> str:
    """`GET /health` direto em 127.0.0.1 (sem proxy): HTTP 200 e JSON com `status == "ok"`."""
    conn = http.client.HTTPConnection(HOST, port, timeout=CONNECT_TIMEOUT)
    try:
        conn.request("GET", "/health")
        resp = conn.getresponse()
        if resp.status != 200:
            return UNREACHABLE
        body = resp.read(65536)
        payload = json.loads(body.decode("utf-8"))
    except (OSError, http.client.HTTPException, ValueError):
        return UNREACHABLE
    finally:
        conn.close()
    return REACHABLE if isinstance(payload, dict) and payload.get("status") == "ok" else UNREACHABLE


_CHECKS = {"postgres": check_postgres, "rabbitmq": check_rabbitmq, "api": check_api}

_STATE_TEXT = {
    REACHABLE: "acessível",
    UNREACHABLE: "inacessível",
    PROTOCOL_MISMATCH: "inacessível (versão de protocolo incompatível)",
}


def _report(res: PortResolution, state: str) -> InfraReport:
    if state == INVALID_PORT:
        message = f"inacessível (porta inválida em {res.var})"
    else:
        message = _STATE_TEXT[state]
    return InfraReport(res.service, HOST, res.port, state, message, res.var, res.origin)


def collect(
    environ: Mapping[str, str] | None = None, cwd: Path | None = None
) -> list[InfraReport]:
    """Executa as três checagens em paralelo (teto total < 5 s) e devolve um relatório por serviço."""
    resolutions = resolve_ports(environ, cwd)
    states: dict[str, str] = {}
    threads: list[threading.Thread] = []

    def run(name: str, port: int) -> None:
        try:
            states[name] = _CHECKS[name](port)
        except Exception:  # defensivo: o doctor nunca quebra por causa da infraestrutura
            states[name] = UNREACHABLE

    for name, res in resolutions.items():
        if res.invalid or res.port is None:
            states[name] = INVALID_PORT
            continue
        t = threading.Thread(target=run, args=(name, res.port), daemon=True)
        t.start()
        threads.append(t)

    deadline = time.monotonic() + TOTAL_DEADLINE
    for t in threads:
        t.join(max(0.0, deadline - time.monotonic()))
    return [_report(res, states.get(name, UNREACHABLE)) for name, res in resolutions.items()]


# ── formatação ──────────────────────────────────────────────────────────────────
def format_section(reports: list[InfraReport]) -> list[str]:
    """Linhas da seção (contracts/doctor-infra.md). A linha de instrução só aparece com falha."""
    lines = ["Infraestrutura (versão desktop, opcional):"]
    for r in reports:
        addr = f"{r.host}:{r.port}" if r.port is not None else ""
        lines.append(f"  {r.service:<12} {addr:<17} {r.message}".rstrip())
    origins = sorted({r.origin for r in reports}, key=[ORIGIN_ENV, ORIGIN_STACK_ENV_VAR,
                                                       ORIGIN_STACK_FILE, ORIGIN_DEFAULT].index)
    lines.append(f"  (portas: {', '.join(origins)})")
    if any(r.state != REACHABLE for r in reports):
        lines.append(
            "Para subir a infraestrutura: sh stack/up.sh (requer Docker; veja stack/.env.example)."
        )
    return lines
