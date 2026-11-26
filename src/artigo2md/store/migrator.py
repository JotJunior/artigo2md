"""Runner próprio de migrações em SQL puro (contracts/migrations.md; research, Decision 3).

Só avanço, sem reversão. Cada migração pendente roda em transação própria, registrada em `schema_migrations`.
O driver (`asyncpg`) só é importado dentro das funções que falam com o banco, para que `known_migrations()`
possa ser usado e testado sem ele. Nenhuma mensagem de erro carrega texto bruto, `detail` ou parâmetros do
servidor (FR-016, SC-007): todas as exceções do driver são convertidas com `raise ... from None`.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import re
import time
from collections.abc import Sequence
from dataclasses import dataclass
from importlib import resources
from typing import TYPE_CHECKING

from .config import DbSettings
from .errors import (
    DatabaseUnavailableError,
    MigrationChecksumError,
    MigrationError,
    MigrationFailedError,
    MigrationLockTimeoutError,
    SchemaTooNewError,
    StoreDatabaseError,
    StoreError,
)

if TYPE_CHECKING:  # só para anotações; o driver não é importado em tempo de execução aqui
    import asyncpg

# Chave constante (bigint) do lock consultivo de migração [PROPOSTA]; arbitrária, mas fixa no código.
LOCK_KEY = 0x6172_7469_676F_3200
# Espera pelo lock de outro processo: repete a cada 0,2 s até 60 s [PROPOSTA] (contracts/migrations.md).
LOCK_POLL_SECONDS = 0.2
LOCK_WAIT_SECONDS = 60.0
# Prazo para conectar do runner; sem `command_timeout`, para uma migração longa não ser cortada.
CONNECT_TIMEOUT_SECONDS = 10.0

_FILE_PATTERN = re.compile(r"^(\d{4})_([a-z0-9_]+)\.sql$")

_CREATE_CONTROL_TABLE = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version     integer PRIMARY KEY CHECK (version > 0),
    name        text NOT NULL,
    checksum    text NOT NULL CHECK (checksum ~ '^[0-9a-f]{64}$'),
    applied_at  timestamptz NOT NULL DEFAULT now()
)
"""


@dataclass(frozen=True)
class Migration:
    """Um arquivo `NNNN_<slug>.sql` empacotado com o código."""

    version: int
    name: str
    checksum: str
    sql: str


@dataclass(frozen=True)
class MigrationReport:
    """Resultado de uma chamada: versões aplicadas agora (vazio = já atualizado) e a versão corrente."""

    applied: tuple[int, ...]
    current_version: int

    @property
    def summary(self) -> str:
        if not self.applied:
            return f"banco já está atualizado (versão {self.current_version})"
        versions = ", ".join(f"{version:04d}" for version in self.applied)
        return f"migrações aplicadas: {versions} (versão {self.current_version})"


def known_migrations() -> tuple[Migration, ...]:
    """Lê as migrações empacotadas (via `importlib.resources`), ordenadas por versão, com SHA-256 dos bytes."""
    folder = resources.files(__package__) / "migrations"
    found: list[Migration] = []
    for entry in folder.iterdir():
        match = _FILE_PATTERN.match(entry.name)
        if match is None or not entry.is_file():
            continue
        raw = entry.read_bytes()
        found.append(
            Migration(
                version=int(match.group(1)),
                name=entry.name,
                checksum=hashlib.sha256(raw).hexdigest(),
                sql=raw.decode("utf-8"),
            )
        )
    found.sort(key=lambda migration: migration.version)
    versions = [migration.version for migration in found]
    if versions != list(range(1, len(versions) + 1)):
        raise MigrationError("Os arquivos de migração do pacote não são contíguos a partir de 0001.")
    return tuple(found)


def _check_history(migrations: Sequence[Migration], history: dict[int, str]) -> None:
    """Recusa, antes de aplicar qualquer coisa, banco mais novo ou histórico divergente do código."""
    newest = migrations[-1].version if migrations else 0
    too_new = [version for version in history if version > newest]
    if too_new:
        raise SchemaTooNewError(
            f"O banco foi criado por uma versão mais nova do artigo2md (versão {max(too_new)}); atualize o app."
        ) from None
    by_version = {migration.version: migration for migration in migrations}
    for version in sorted(history):
        known = by_version.get(version)
        if known is None:
            raise MigrationChecksumError(
                "O histórico de migrações do banco diverge do código: "
                f"a migração {version:04d} foi aplicada mas não existe mais."
            ) from None
        if known.checksum != history[version]:
            raise MigrationChecksumError(
                f"A migração {known.name} foi alterada depois de aplicada."
            ) from None


async def _acquire_lock(connection: "asyncpg.Connection", wait_seconds: float, poll_seconds: float) -> None:
    """Espera pelo lock com `pg_try_advisory_lock` repetido (não depende de `lock_timeout` do servidor)."""
    deadline = time.monotonic() + wait_seconds
    while True:
        if await connection.fetchval("SELECT pg_try_advisory_lock($1)", LOCK_KEY):
            return
        if time.monotonic() >= deadline:
            raise MigrationLockTimeoutError(
                f"Outro processo está aplicando migrações há mais de {wait_seconds:g} s; "
                "tente de novo depois que ele terminar."
            ) from None
        await asyncio.sleep(poll_seconds)


async def _apply_locked(connection: "asyncpg.Connection", migrations: Sequence[Migration]) -> MigrationReport:
    import asyncpg

    await connection.execute(_CREATE_CONTROL_TABLE)
    rows = await connection.fetch("SELECT version, checksum FROM schema_migrations ORDER BY version")
    history = {int(row["version"]): str(row["checksum"]) for row in rows}
    _check_history(migrations, history)

    applied: list[int] = []
    for migration in migrations:
        if migration.version in history:
            continue
        try:
            async with connection.transaction():
                # Sem argumentos, `execute` aceita vários comandos de uma vez (arquivo inteiro).
                await connection.execute(migration.sql)
                await connection.execute(
                    "INSERT INTO schema_migrations (version, name, checksum) VALUES ($1, $2, $3)",
                    migration.version,
                    migration.name,
                    migration.checksum,
                )
        except asyncpg.PostgresError as exc:
            # Só o SQLSTATE (código padronizado de 5 caracteres) entra na mensagem; nunca o texto do servidor.
            code = getattr(exc, "sqlstate", None) or "desconhecido"
            raise MigrationFailedError(
                f"A migração {migration.name} falhou e foi desfeita: o banco recusou o comando (SQLSTATE {code})."
            ) from None
        applied.append(migration.version)
    current = max([*history, *applied], default=0)
    return MigrationReport(applied=tuple(applied), current_version=current)


async def apply_migrations_on(
    connection: "asyncpg.Connection",
    migrations: Sequence[Migration] | None = None,
    *,
    lock_wait_seconds: float = LOCK_WAIT_SECONDS,
    lock_poll_seconds: float = LOCK_POLL_SECONDS,
) -> MigrationReport:
    """Aplica as migrações pendentes numa conexão já aberta (usado pelos testes e por `apply_migrations`).

    `migrations` e os prazos existem para os testes injetarem migrações próprias e esperas curtas; o padrão é o
    que está empacotado e o limite de 60 s do contrato. Cada chamada concorrente precisa de conexão própria:
    o lock consultivo é da sessão, logo reentrante dentro dela.
    """
    import asyncpg

    pending_source = known_migrations() if migrations is None else tuple(migrations)
    try:
        await _acquire_lock(connection, lock_wait_seconds, lock_poll_seconds)
        try:
            return await _apply_locked(connection, pending_source)
        finally:
            # O lock é da sessão: se a conexão caiu, o servidor já o liberou; não mascara o erro original.
            with contextlib.suppress(Exception):
                await connection.execute("SELECT pg_advisory_unlock($1)", LOCK_KEY)
    except StoreError:
        raise
    except asyncpg.PostgresError:
        raise StoreDatabaseError("O banco recusou um comando durante a migração.") from None
    except (OSError, asyncpg.InterfaceError, asyncio.TimeoutError):
        raise DatabaseUnavailableError("A conexão com o banco foi perdida durante a migração.") from None


async def apply_migrations(settings: DbSettings) -> MigrationReport:
    """Abre uma conexão própria de migração (sem `command_timeout`) e aplica o que estiver pendente."""
    import asyncpg

    host = settings.resolve_connect_host()
    try:
        connection = await asyncpg.connect(
            host=host,
            port=settings.port,
            user=settings.user,
            password=settings.password,
            database=settings.database,
            timeout=CONNECT_TIMEOUT_SECONDS,
            command_timeout=None,
        )
    except (OSError, asyncpg.PostgresError, asyncpg.InterfaceError, asyncio.TimeoutError):
        # Mensagem fixa: nada do erro do driver (que pode ecoar usuário, banco ou endereço) é repassado.
        raise DatabaseUnavailableError(
            "Não foi possível conectar ao banco de dados; confira se a stack está no ar e os parâmetros em stack/.env."
        ) from None
    try:
        return await apply_migrations_on(connection)
    finally:
        with contextlib.suppress(Exception):
            await connection.close()
