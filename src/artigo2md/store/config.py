"""Parâmetros de conexão com o Postgres da stack local.

Só biblioteca padrão e `artigo2md.infra`: este módulo NÃO importa o driver, para poder ser testado sem ele.
A senha nunca aparece em `repr`, mensagem, log ou exceção (FR-016, SC-007).
"""
from __future__ import annotations

import ipaddress
import os
import socket
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from ..infra import STACK_ENV_VAR, parse_env_file
from .errors import StoreConfigError

HOST_VAR = "ARTIGO2MD_POSTGRES_HOST"
PORT_VAR = "ARTIGO2MD_POSTGRES_PORT"
USER_VAR = "POSTGRES_USER"
PASSWORD_VAR = "POSTGRES_PASSWORD"
DATABASE_VAR = "POSTGRES_DB"

DEFAULT_HOST = "127.0.0.1"
# Lista fechada de destinos (Princípio I): loopback ou o nome do serviço do Postgres na rede da stack.
LOOPBACK_HOSTS = ("127.0.0.1", "::1", "localhost")
SERVICE_HOST = "postgres"
ALLOWED_HOSTS = LOOPBACK_HOSTS + (SERVICE_HOST,)


def _missing(var: str) -> StoreConfigError:
    return StoreConfigError(f"Parâmetro do banco ausente: defina {var} em stack/.env.")


def _parse_port(raw: str) -> int | None:
    text = raw.strip()
    if not (text.isascii() and text.isdigit()):
        return None
    port = int(text)
    return port if 1 <= port <= 65535 else None


@dataclass(frozen=True)
class DbSettings:
    """Parâmetros de conexão; `password` fica fora do `repr`."""

    host: str
    port: int
    user: str
    database: str
    password: str = field(repr=False)

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None, cwd: Path | None = None) -> "DbSettings":
        """Resolve nesta ordem: ambiente → arquivo de `ARTIGO2MD_STACK_ENV` → `./stack/.env`.

        Valor vazio conta como não definido. Falhas citam só o nome da variável, nunca o valor.
        """
        env = os.environ if environ is None else environ
        base = Path.cwd() if cwd is None else Path(cwd)
        stack_env_path = env.get(STACK_ENV_VAR, "").strip()
        from_var_file = parse_env_file(Path(stack_env_path)) if stack_env_path else {}
        from_stack_file = parse_env_file(base / "stack" / ".env")
        sources = (env, from_var_file, from_stack_file)

        def lookup(var: str) -> str | None:
            for source in sources:
                value = source.get(var, "")
                if value.strip() != "":
                    return value
            return None

        raw_port = lookup(PORT_VAR)
        if raw_port is None:
            raise _missing(PORT_VAR)
        port = _parse_port(raw_port)
        if port is None:
            raise StoreConfigError(
                f"Parâmetro do banco inválido: {PORT_VAR} precisa ser um número de 1 a 65535."
            )

        values: dict[str, str] = {}
        for var in (USER_VAR, PASSWORD_VAR, DATABASE_VAR):
            value = lookup(var)
            if value is None:
                raise _missing(var)
            values[var] = value

        raw_host = lookup(HOST_VAR)
        host = DEFAULT_HOST if raw_host is None else raw_host.strip()
        if host not in ALLOWED_HOSTS:
            # O valor recusado não é ecoado (pode ser um endereço remoto ou conter texto sensível).
            raise StoreConfigError(
                f"Endereço do banco não permitido em {HOST_VAR}: use 127.0.0.1, ::1, localhost ou postgres."
            )
        return cls(
            host=host,
            port=port,
            user=values[USER_VAR],
            database=values[DATABASE_VAR],
            password=values[PASSWORD_VAR],
        )

    def resolve_connect_host(self) -> str:
        """Devolve o endereço a usar em `host=` ao conectar.

        Para `127.0.0.1`, `::1` e `localhost`, o próprio valor. Para `postgres`, o nome é resolvido uma só
        vez e só é aceito se TODOS os endereços forem de loopback ou privados; a conexão usa o primeiro
        endereço validado, sem resolver o nome de novo (fecha a janela entre validação e conexão).
        """
        if self.host in LOOPBACK_HOSTS:
            return self.host
        refused = StoreConfigError("O nome postgres não resolveu para um endereço local; conexão recusada.")
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
