"""Configuração do worker: porta, credenciais, pasta de dados e limites (FR-016, FR-022, FR-023, FR-024).

Só biblioteca padrão mais `infra` (que só usa biblioteca padrão). O host do broker é a constante
`infra.HOST`: não existe opção nem variável para mudá-lo (FR-016). As credenciais ficam só em
`WorkerSettings` (com `repr` mascarado) e as variáveis `RABBITMQ_DEFAULT_USER`/`RABBITMQ_DEFAULT_PASS`
saem de `os.environ` assim que são lidas, antes de qualquer processo filho `spawn` (FR-024).
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, MutableMapping

from .. import infra

USER_VAR = "RABBITMQ_DEFAULT_USER"
PASS_VAR = "RABBITMQ_DEFAULT_PASS"
CREDENTIAL_VARS = (USER_VAR, PASS_VAR)

ORIGIN_MIXED = "ambiente e arquivo"

# Padrões de `contracts/worker-cli.md`.
# `DEFAULT_WORKERS` e `DEFAULT_MIN_FREE_MEMORY_MB` vêm da medição de RAM: `docs/specs/conversion-queue/ram-measurement.md`
# (seção "Derivação dos padrões", regra de `research.md` Decision 8). Não os altere sem refazer a medição:
# `tests/test_ram_measurement_report.py` recalcula os dois a partir da saída bruta e exige que coincidam.
DEFAULT_WORKERS = 4
DEFAULT_MIN_FREE_MEMORY_MB = 4822
DEFAULT_RETRY_DELAY = 30.0
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_IDLE_TIMEOUT = 300.0
DEFAULT_SHUTDOWN_TIMEOUT = 30.0
DEFAULT_CONVERSION_TIMEOUT = 0.0
# Prazo do broker para o `ack`: `consumer_timeout` padrão do RabbitMQ 4.4.1 da stack = 1800000 ms (30 min), lido
# da imagem local (`docs/specs/conversion-queue/research.md`, Decision 11). FR-025: o limite por conversão, quando
# usado, tem de ser menor que este prazo.
BROKER_ACK_TIMEOUT_SECONDS = 1800.0

EXIT_OK = 0
EXIT_FAILURE = 1  # defeito inesperado do worker
EXIT_USAGE = 2
EXIT_EXTRA_MISSING = 3
EXIT_CREDENTIALS = 4
EXIT_TOPOLOGY = 5


class ConfigError(Exception):
    """Configuração inválida. `message` está em português e nunca contém valor de credencial."""

    def __init__(self, message: str, exit_code: int = EXIT_USAGE):
        super().__init__(message)
        self.message = message
        self.exit_code = exit_code


@dataclass(frozen=True)
class Credentials:
    user: str
    password: str
    origin: str  # `ambiente`, `arquivo` ou `ambiente e arquivo`; nunca o valor

    def __repr__(self) -> str:
        return f"Credentials(user={self.user!r}, password='***', origin={self.origin!r})"

    __str__ = __repr__


@dataclass(frozen=True)
class WorkerSettings:
    data_dir: Path
    workers: int
    min_free_memory_mb: int
    port: int
    credentials: Credentials
    retry_delay: float = DEFAULT_RETRY_DELAY
    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    idle_timeout: float = DEFAULT_IDLE_TIMEOUT
    shutdown_timeout: float = DEFAULT_SHUTDOWN_TIMEOUT
    conversion_timeout: float = DEFAULT_CONVERSION_TIMEOUT

    @property
    def host(self) -> str:
        """Sempre o loopback da stack (FR-016); sem opção para alterar."""
        return infra.HOST


# ── credenciais ─────────────────────────────────────────────────────────────────
def resolve_credentials(
    environ: Mapping[str, str] | None = None, cwd: Path | None = None
) -> Credentials:
    """Ambiente primeiro; na ausência, `ARTIGO2MD_STACK_ENV` e depois `./stack/.env` (variável a variável)."""
    env = os.environ if environ is None else environ
    base = Path.cwd() if cwd is None else Path(cwd)
    stack_env_path = env.get(infra.STACK_ENV_VAR, "").strip()
    sources = (
        ("ambiente", env),
        ("arquivo", infra.parse_env_file(Path(stack_env_path)) if stack_env_path else {}),
        ("arquivo", infra.parse_env_file(base / "stack" / ".env")),
    )
    values: dict[str, str] = {}
    origins: dict[str, str] = {}
    for var in CREDENTIAL_VARS:
        for origin, source in sources:
            raw = source.get(var, "")
            if raw.strip() != "":
                values[var], origins[var] = raw, origin
                break
    if len(values) != len(CREDENTIAL_VARS):
        raise ConfigError(
            f"Credenciais do RabbitMQ não encontradas. Defina {USER_VAR} e {PASS_VAR} "
            "no ambiente ou em stack/.env.",
            EXIT_CREDENTIALS,
        )
    origin = origins[USER_VAR] if origins[USER_VAR] == origins[PASS_VAR] else ORIGIN_MIXED
    return Credentials(values[USER_VAR], values[PASS_VAR], origin)


def scrub_environment(*environs: MutableMapping[str, str] | None) -> None:
    """Remove as variáveis de credencial de `os.environ` (e dos mapeamentos dados) para os filhos `spawn`."""
    for target in (os.environ, *(e for e in environs if e is not None)):
        for var in CREDENTIAL_VARS:
            target.pop(var, None)


# ── porta ───────────────────────────────────────────────────────────────────────
def resolve_port(environ: Mapping[str, str] | None = None, cwd: Path | None = None) -> int:
    """Porta do RabbitMQ pela mesma ordem do `doctor` (`infra.resolve_ports`)."""
    resolution = infra.resolve_ports(environ, cwd)["rabbitmq"]
    if resolution.invalid or resolution.port is None:
        raise ConfigError(f"Porta inválida em {resolution.var}: use um número de 1 a 65535.")
    return resolution.port


# ── validações ──────────────────────────────────────────────────────────────────
def validate_data_dir(value: str | Path) -> Path:
    """Pasta de dados: existe, é diretório e é gravável; devolve o caminho resolvido."""
    try:
        path = Path(value).expanduser().resolve(strict=True)
    except (OSError, RuntimeError):
        raise ConfigError(f"A pasta de dados não existe: {value}") from None
    if not path.is_dir():
        raise ConfigError(f"O caminho de --data-dir não é uma pasta: {value}")
    if not os.access(path, os.W_OK | os.X_OK):
        raise ConfigError(f"Sem permissão de escrita na pasta de dados: {value}")
    return path


def _check(value: float, name: str, ok: bool, rule: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not ok:
        raise ConfigError(f"{name} inválido ({value!r}): {rule}.")


def validate_limits(
    workers: int,
    min_free_memory_mb: int,
    retry_delay: float,
    max_attempts: int,
    idle_timeout: float,
    shutdown_timeout: float,
    conversion_timeout: float,
) -> None:
    """Intervalos de `contracts/worker-cli.md`."""
    for name, value in (("--workers", workers), ("--min-free-memory", min_free_memory_mb),
                        ("--max-attempts", max_attempts)):
        if isinstance(value, bool) or not isinstance(value, int):
            raise ConfigError(f"{name} inválido ({value!r}): deve ser um número inteiro.")
    _check(workers, "--workers", workers >= 1, "use 1 ou mais")
    _check(min_free_memory_mb, "--min-free-memory", min_free_memory_mb >= 0, "use 0 ou mais (MB)")
    _check(retry_delay, "--retry-delay", retry_delay > 0, "use um valor maior que zero (segundos)")
    _check(max_attempts, "--max-attempts", max_attempts >= 1, "use 1 ou mais (1 = sem nova tentativa)")
    _check(idle_timeout, "--idle-timeout", idle_timeout >= 0, "use 0 ou mais (0 = nunca descarrega)")
    _check(shutdown_timeout, "--shutdown-timeout", shutdown_timeout >= 0, "use 0 ou mais (segundos)")
    _check(conversion_timeout, "--conversion-timeout",
           conversion_timeout == 0 or 0 < conversion_timeout < BROKER_ACK_TIMEOUT_SECONDS,
           f"use 0 (sem limite) ou um valor menor que {BROKER_ACK_TIMEOUT_SECONDS:g} s, o prazo do broker "
           "para o ack (30 min, consumer_timeout do RabbitMQ 4.4.1 da stack)")


def load_settings(
    *,
    data_dir: str | Path,
    workers: int = DEFAULT_WORKERS,
    min_free_memory_mb: int = DEFAULT_MIN_FREE_MEMORY_MB,
    retry_delay: float = DEFAULT_RETRY_DELAY,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    idle_timeout: float = DEFAULT_IDLE_TIMEOUT,
    shutdown_timeout: float = DEFAULT_SHUTDOWN_TIMEOUT,
    conversion_timeout: float = DEFAULT_CONVERSION_TIMEOUT,
    environ: Mapping[str, str] | None = None,
    cwd: Path | None = None,
) -> WorkerSettings:
    """Valida tudo, resolve porta e credenciais e remove as credenciais do ambiente do processo."""
    validate_limits(workers, min_free_memory_mb, retry_delay, max_attempts, idle_timeout,
                    shutdown_timeout, conversion_timeout)
    resolved_dir = validate_data_dir(data_dir)
    port = resolve_port(environ, cwd)
    try:
        credentials = resolve_credentials(environ, cwd)
    finally:
        mutable = environ if isinstance(environ, MutableMapping) else None
        scrub_environment(mutable)
    return WorkerSettings(
        data_dir=resolved_dir,
        workers=workers,
        min_free_memory_mb=min_free_memory_mb,
        port=port,
        credentials=credentials,
        retry_delay=retry_delay,
        max_attempts=max_attempts,
        idle_timeout=idle_timeout,
        shutdown_timeout=shutdown_timeout,
        conversion_timeout=conversion_timeout,
    )
