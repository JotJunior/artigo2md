"""Infraestrutura dos testes de integração da camada `artigo2md.store` (research, Decision 9).

Os testes de integração só rodam com `ARTIGO2MD_DB_TESTS=1` e a stack local no ar. Cada banco temporário tem
nome `artigo2md_test_<16 hex>`; o banco real (`POSTGRES_DB`) é usado só para executar `CREATE DATABASE` e
nunca é lido nem alterado (FR-019, SC-006). O teardown só apaga bancos cujo nome passa pela guarda abaixo.
"""
from __future__ import annotations

import dataclasses
import os
import re
import secrets

import pytest

ENV_FLAG = "ARTIGO2MD_DB_TESTS"
SKIP_REASON = "defina ARTIGO2MD_DB_TESTS=1 com a stack no ar"
TEST_DB_PATTERN = re.compile(r"^artigo2md_test_[0-9a-f]{16}$")

# Marca para os testes de integração: sem a variável, ficam SKIPPED com motivo em português.
requires_db = pytest.mark.skipif(os.environ.get(ENV_FLAG) != "1", reason=SKIP_REASON)


class UnsafeDropError(RuntimeError):
    """Recusa de apagar um banco que não é um banco temporário de teste."""


def db_tests_enabled() -> bool:
    return os.environ.get(ENV_FLAG) == "1"


def new_test_db_name() -> str:
    return f"artigo2md_test_{secrets.token_hex(8)}"


def assert_droppable(name: str, real_database: str) -> None:
    """Guarda do teardown: só nomes `artigo2md_test_<16 hex>` e diferentes de `POSTGRES_DB` podem ser apagados."""
    if not isinstance(name, str) or TEST_DB_PATTERN.fullmatch(name) is None:
        raise UnsafeDropError("Recusado: o nome não é de um banco temporário de teste; nada foi apagado.")
    if name == real_database:
        raise UnsafeDropError("Recusado: o banco a apagar é o banco real da stack; nada foi apagado.")


def quote_ident(name: str) -> str:
    """Identificador citado (aspas duplicadas por dentro), para `CREATE`/`DROP DATABASE`."""
    return '"' + name.replace('"', '""') + '"'


async def _connect(settings, database: str):
    import asyncpg

    return await asyncpg.connect(
        host=settings.resolve_connect_host(),
        port=settings.port,
        user=settings.user,
        password=settings.password,
        database=database,
        timeout=10,
    )


async def create_database(settings, name: str) -> None:
    """Cria o banco temporário `name`; a conexão administrativa usa o banco real só para isso."""
    import asyncpg

    assert_droppable(name, settings.database)  # mesma guarda: só se cria o que depois se pode apagar
    try:
        connection = await _connect(settings, settings.database)
    except (OSError, asyncpg.PostgresError, asyncpg.InterfaceError, TimeoutError):
        pytest.fail(
            "Não foi possível conectar ao banco da stack para criar o banco de teste: "
            "suba a stack e confira os parâmetros em stack/.env.",
            pytrace=False,
        )
    try:
        await connection.execute(f"CREATE DATABASE {quote_ident(name)}")
    except asyncpg.InsufficientPrivilegeError:
        pytest.fail(
            "O usuário do banco (POSTGRES_USER) não tem permissão para criar bancos; "
            "os testes de integração precisam dela para usar um banco temporário.",
            pytrace=False,
        )
    finally:
        await connection.close()


async def drop_database(settings, name: str) -> None:
    """Apaga o banco temporário `name`, depois de passar pela guarda; aborta sem apagar nada se ela falhar."""
    assert_droppable(name, settings.database)
    connection = await _connect(settings, settings.database)
    try:
        await connection.execute(f"DROP DATABASE IF EXISTS {quote_ident(name)} WITH (FORCE)")
    finally:
        await connection.close()


def settings_for(settings, database: str):
    """Cópia dos parâmetros apontando para outro banco (a senha segue fora do `repr`)."""
    return dataclasses.replace(settings, database=database)
