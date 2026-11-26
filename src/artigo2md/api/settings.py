"""Configuração da API por variáveis de ambiente (`contracts/configuration.md`, research Decision 5).

Nenhuma falha de configuração derruba o processo (FR-032: `/health` responde sempre). Cada área (pasta de dados,
broker, banco) guarda o seu próprio erro, em português, citando só o NOME da variável — nunca o valor. A senha do
banco e a do broker nunca aparecem em `repr`, mensagem nem log.

Só biblioteca padrão, `artigo2md.infra`, `artigo2md.store.config` e `artigo2md.jobqueue.settings` (que não importam
`pika`, `asyncpg`, `docling` nem `pymupdf4llm`).
"""
from __future__ import annotations

import dataclasses
import ipaddress
import logging
import os
import socket
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from ..jobqueue import settings as queue_settings
from ..jobqueue.settings import ConfigError, Credentials
from ..store.config import DbSettings
from ..store.errors import StoreConfigError

logger = logging.getLogger("artigo2md.api")

DATA_DIR_HOST_VAR = "ARTIGO2MD_DATA_DIR_HOST"
DATA_DIR_MOUNT_VAR = "ARTIGO2MD_DATA_DIR_MOUNT"
ALLOW_DEV_ORIGIN_VAR = "ARTIGO2MD_API_ALLOW_DEV_ORIGIN"
RABBITMQ_HOST_VAR = "ARTIGO2MD_RABBITMQ_HOST"

# Lista de origens FIXA (decisão do operador sobre a tarefa 13.9): a de produção do app desktop (confirmada em
# block-001/dec-023) e, só com `ARTIGO2MD_API_ALLOW_DEV_ORIGIN`, a de desenvolvimento (FR-028). Não há variável
# que troque a lista.
APP_ORIGIN = "tauri://localhost"
DEV_ORIGIN = "http://localhost:1420"

# Lista fechada de destinos do broker (Princípio I): loopback ou o nome do serviço do RabbitMQ na rede da stack.
DEFAULT_BROKER_HOST = "127.0.0.1"
BROKER_LOOPBACK_HOSTS = ("127.0.0.1", "::1", "localhost")
BROKER_SERVICE_HOST = "rabbitmq"
BROKER_ALLOWED_HOSTS = BROKER_LOOPBACK_HOSTS + (BROKER_SERVICE_HOST,)

_TRUE_VALUES = frozenset({"1", "true"})


# ── validações ──────────────────────────────────────────────────────────────────
def is_valid_absolute_path(value: str) -> bool:
    """Caminho POSIX absoluto, sem `\\0`, sem componente `.`/`..`/vazio e sem `/` final."""
    if not value or "\0" in value or not value.startswith("/") or value.endswith("/"):
        return False
    return all(part not in ("", ".", "..") for part in value[1:].split("/"))


def parse_dev_origin_flag(raw: str | None) -> tuple[bool, str | None]:
    """`1`/`true` (sem diferenciar maiúsculas) liga; ausente ou vazio desliga; outro valor desliga com aviso."""
    if raw is None or raw.strip() == "":
        return False, None
    if raw.strip().lower() in _TRUE_VALUES:
        return True, None
    return False, (
        f"Valor inválido em {ALLOW_DEV_ORIGIN_VAR}: use 1 ou true para liberar a origem de desenvolvimento. "
        "A origem de desenvolvimento permanece desligada."
    )


def _parse_port(raw: str) -> int | None:
    text = raw.strip()
    if not (text.isascii() and text.isdigit()):
        return None
    port = int(text)
    return port if 1 <= port <= 65535 else None


# ── broker ──────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class BrokerSettings:
    """Endereço e credenciais do RabbitMQ. Compatível com `jobqueue.amqp.open_connection` (`host`, `port`,
    `credentials.user`, `credentials.password`). `Credentials` mascara a senha no `repr`."""

    host: str
    port: int
    credentials: Credentials = field(repr=True)

    def resolve_connect_host(self) -> str:
        """Endereço a usar ao conectar. Para o nome `rabbitmq`, resolve uma vez e só aceita se TODOS os
        endereços forem de loopback ou privados; a conexão usa o primeiro, sem resolver de novo."""
        if self.host in BROKER_LOOPBACK_HOSTS:
            return self.host
        refused = ConfigError("O nome rabbitmq não resolveu para um endereço local; conexão recusada.")
        try:
            infos = socket.getaddrinfo(self.host, None, type=socket.SOCK_STREAM)
        except OSError:
            raise refused from None
        addresses: list[str] = []
        for info in infos:
            text = str(info[4][0])
            try:
                ip = ipaddress.ip_address(text.split("%", 1)[0])
            except ValueError:
                raise refused from None
            if not (ip.is_loopback or ip.is_private):
                raise refused from None
            addresses.append(text)
        if not addresses:
            raise refused from None
        return addresses[0]

    def with_resolved_host(self) -> "BrokerSettings":
        return dataclasses.replace(self, host=self.resolve_connect_host())


def _load_broker(env: Mapping[str, str], cwd: Path | None) -> tuple[BrokerSettings | None, str | None]:
    raw_host = env.get(RABBITMQ_HOST_VAR, "")
    host = DEFAULT_BROKER_HOST if raw_host.strip() == "" else raw_host.strip()
    if host not in BROKER_ALLOWED_HOSTS:
        return None, (
            f"Endereço do broker não permitido em {RABBITMQ_HOST_VAR}: use 127.0.0.1, ::1, localhost ou rabbitmq."
        )
    try:
        port = queue_settings.resolve_port(env, cwd)
        credentials = queue_settings.resolve_credentials(env, cwd)
    except ConfigError as exc:
        return None, exc.message  # já em português e sem valor de credencial
    return BrokerSettings(host=host, port=port, credentials=credentials), None


def _load_db(env: Mapping[str, str], cwd: Path | None) -> tuple[DbSettings | None, str | None]:
    try:
        return DbSettings.from_env(env, cwd), None
    except StoreConfigError as exc:
        return None, str(exc)


# ── pasta de dados ──────────────────────────────────────────────────────────────
def _load_data_dir(env: Mapping[str, str]) -> tuple[str | None, str | None, str | None]:
    """Devolve `(host, mount, erro)`. Qualquer problema deixa os dois caminhos em `None`."""
    raw_host = env.get(DATA_DIR_HOST_VAR, "").strip()
    raw_mount = env.get(DATA_DIR_MOUNT_VAR, "").strip()
    for var, raw in ((DATA_DIR_HOST_VAR, raw_host), (DATA_DIR_MOUNT_VAR, raw_mount)):
        if raw == "":
            return None, None, f"Pasta de dados não configurada: defina {var} (ARTIGO2MD_DATA_DIR em stack/.env)."
        if not is_valid_absolute_path(raw):
            return None, None, (
                f"Valor inválido em {var}: use um caminho absoluto, sem '..', sem '.', sem '/' final."
            )
    if not os.path.isdir(raw_mount):
        return None, None, f"A pasta indicada em {DATA_DIR_MOUNT_VAR} não existe no container."
    return raw_host, raw_mount, None


# ── configuração agregada ───────────────────────────────────────────────────────
@dataclass(frozen=True)
class ApiSettings:
    """Configuração em memória da API. Cada área com erro fica `None` e o erro vai no campo `*_error`."""

    data_dir_host: str | None
    data_dir_mount: str | None
    allowed_origins: tuple[str, ...]
    allow_dev_origin: bool
    broker: BrokerSettings | None
    db: DbSettings | None
    data_dir_error: str | None = None
    broker_error: str | None = None
    db_error: str | None = None
    warnings: tuple[str, ...] = ()

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None, cwd: Path | None = None) -> "ApiSettings":
        """Lê o ambiente (e, para banco e broker, `ARTIGO2MD_STACK_ENV`/`./stack/.env`). Nunca levanta exceção."""
        env = os.environ if environ is None else environ
        warnings: list[str] = []

        allow_dev, dev_warning = parse_dev_origin_flag(env.get(ALLOW_DEV_ORIGIN_VAR))
        if dev_warning:
            warnings.append(dev_warning)
        origins = (APP_ORIGIN, DEV_ORIGIN) if allow_dev else (APP_ORIGIN,)

        data_host, data_mount, data_error = _load_data_dir(env)
        broker, broker_error = _load_broker(env, cwd)
        db, db_error = _load_db(env, cwd)
        for message in (data_error, broker_error, db_error):
            if message:
                warnings.append(message)
        for message in warnings:
            logger.warning(message)
        return cls(
            data_dir_host=data_host,
            data_dir_mount=data_mount,
            allowed_origins=origins,
            allow_dev_origin=allow_dev,
            broker=broker,
            db=db,
            data_dir_error=data_error,
            broker_error=broker_error,
            db_error=db_error,
            warnings=tuple(warnings),
        )
