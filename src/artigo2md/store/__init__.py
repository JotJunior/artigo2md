"""Camada de persistência do artigo2md (Postgres da stack local).

Só a futura `project-api` usa este pacote: o worker, a CLI e os módulos de conversão NÃO o importam (FR-017).
Os símbolos que dependem do driver (`Store`, `apply_migrations`) são carregados sob demanda, para que importar
`artigo2md.store` (por exemplo só para os erros ou `DbSettings`) não exija `asyncpg`.
"""
from __future__ import annotations

from importlib import import_module
from typing import Any

from .config import DbSettings
from .errors import (
    ActiveJobExistsError,
    DatabaseUnavailableError,
    DocumentNotFoundError,
    InvalidJobTransitionError,
    JobNotFoundError,
    MigrationChecksumError,
    MigrationError,
    MigrationFailedError,
    MigrationLockTimeoutError,
    ProjectNameTakenError,
    ProjectNotFoundError,
    SchemaTooNewError,
    StoreConfigError,
    StoreDatabaseError,
    StoreError,
    StoreNotFoundError,
    StoreRuleError,
    StoreValidationError,
)

# Carregados sob demanda (PEP 562): módulo onde cada símbolo vive. Acrescentados à medida que existem.
_LAZY: dict[str, str] = {
    "apply_migrations": ".migrator",
    "MigrationReport": ".migrator",
    "Store": ".repository",
    "StoreSession": ".repository",
}

__all__ = [
    "DbSettings",
    "MigrationReport",
    "apply_migrations",
    "Store",
    "StoreSession",
    "ActiveJobExistsError",
    "DatabaseUnavailableError",
    "DocumentNotFoundError",
    "InvalidJobTransitionError",
    "JobNotFoundError",
    "MigrationChecksumError",
    "MigrationError",
    "MigrationFailedError",
    "MigrationLockTimeoutError",
    "ProjectNameTakenError",
    "ProjectNotFoundError",
    "SchemaTooNewError",
    "StoreConfigError",
    "StoreDatabaseError",
    "StoreError",
    "StoreNotFoundError",
    "StoreRuleError",
    "StoreValidationError",
]


def __getattr__(name: str) -> Any:
    module = _LAZY.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(import_module(module, __name__), name)
