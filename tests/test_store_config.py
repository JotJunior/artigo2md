"""Testes de `artigo2md.store.config` (sem banco, sem driver): parâmetros, host permitido e senha oculta."""
from __future__ import annotations

import socket
from pathlib import Path

import pytest

from artigo2md.store.config import DbSettings
from artigo2md.store.errors import StoreConfigError

SECRET = "s3nh4-super-secreta-xyz"
BASE = {
    "ARTIGO2MD_POSTGRES_PORT": "5432",
    "POSTGRES_USER": "app",
    "POSTGRES_PASSWORD": SECRET,
    "POSTGRES_DB": "artigo2md",
}


def env(**overrides: str | None) -> dict[str, str]:
    values = dict(BASE)
    for key, value in overrides.items():
        if value is None:
            values.pop(key, None)
        else:
            values[key] = value
    return values


def test_defaults_and_fields(tmp_path: Path) -> None:
    settings = DbSettings.from_env(env(), cwd=tmp_path)
    assert (settings.host, settings.port, settings.user, settings.database) == (
        "127.0.0.1",
        5432,
        "app",
        "artigo2md",
    )
    assert settings.password == SECRET


@pytest.mark.parametrize("var", ["ARTIGO2MD_POSTGRES_PORT", "POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB"])
@pytest.mark.parametrize("blank", [None, "", "   "])
def test_missing_parameter_cites_only_the_name(tmp_path: Path, var: str, blank: str | None) -> None:
    with pytest.raises(StoreConfigError) as info:
        DbSettings.from_env(env(**{var: blank}), cwd=tmp_path)
    message = str(info.value)
    assert var in message
    assert SECRET not in message
    assert message.startswith("Parâmetro do banco ausente")


@pytest.mark.parametrize("port", ["0", "65536", "-1", "abc", "5432.0", "²", "5 432"])
def test_invalid_port(tmp_path: Path, port: str) -> None:
    with pytest.raises(StoreConfigError) as info:
        DbSettings.from_env(env(ARTIGO2MD_POSTGRES_PORT=port), cwd=tmp_path)
    assert "ARTIGO2MD_POSTGRES_PORT" in str(info.value)
    assert port not in str(info.value)  # o valor inválido não é ecoado


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost", "postgres"])
def test_allowed_hosts(tmp_path: Path, host: str) -> None:
    assert DbSettings.from_env(env(ARTIGO2MD_POSTGRES_HOST=host), cwd=tmp_path).host == host


@pytest.mark.parametrize("host", ["db.example.com", "10.0.0.5", "0.0.0.0", "127.0.0.2", "LOCALHOST", "postgres.local"])
def test_remote_host_refused_without_echo(tmp_path: Path, host: str) -> None:
    with pytest.raises(StoreConfigError) as info:
        DbSettings.from_env(env(ARTIGO2MD_POSTGRES_HOST=host), cwd=tmp_path)
    assert "ARTIGO2MD_POSTGRES_HOST" in str(info.value)
    assert host not in str(info.value)
    assert SECRET not in str(info.value)


def test_resolution_order_env_then_stack_env_var_then_stack_file(tmp_path: Path) -> None:
    (tmp_path / "stack").mkdir()
    (tmp_path / "stack" / ".env").write_text(
        "ARTIGO2MD_POSTGRES_PORT=1111\nPOSTGRES_USER=do_arquivo_stack\nPOSTGRES_PASSWORD=p_stack\nPOSTGRES_DB=db_stack\n",
        encoding="utf-8",
    )
    other = tmp_path / "outro.env"
    other.write_text("ARTIGO2MD_POSTGRES_PORT=2222\nPOSTGRES_USER=do_arquivo_var\n", encoding="utf-8")

    # só o arquivo da stack
    settings = DbSettings.from_env({}, cwd=tmp_path)
    assert (settings.port, settings.user, settings.password, settings.database) == (1111, "do_arquivo_stack", "p_stack", "db_stack")

    # ARTIGO2MD_STACK_ENV vence o arquivo da stack; o que ele não define cai para a origem seguinte
    settings = DbSettings.from_env({"ARTIGO2MD_STACK_ENV": str(other)}, cwd=tmp_path)
    assert (settings.port, settings.user, settings.password) == (2222, "do_arquivo_var", "p_stack")

    # o ambiente vence tudo
    settings = DbSettings.from_env(
        {"ARTIGO2MD_STACK_ENV": str(other), "ARTIGO2MD_POSTGRES_PORT": "3333", "POSTGRES_USER": "do_ambiente"},
        cwd=tmp_path,
    )
    assert (settings.port, settings.user) == (3333, "do_ambiente")


def test_secret_never_in_repr_or_str(tmp_path: Path) -> None:
    settings = DbSettings.from_env(env(), cwd=tmp_path)
    assert SECRET not in repr(settings)
    assert SECRET not in str(settings)
    with pytest.raises(StoreConfigError) as info:
        DbSettings.from_env(env(ARTIGO2MD_POSTGRES_PORT="abc"), cwd=tmp_path)
    assert SECRET not in str(info.value)
    assert SECRET not in repr(info.value)


def _fake_getaddrinfo(addresses: list[str]):
    def fake(host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (a, 0)) for a in addresses]

    return fake


def test_loopback_hosts_are_returned_as_is_without_resolution(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*args, **kwargs):
        raise AssertionError("não deveria resolver nomes")

    monkeypatch.setattr(socket, "getaddrinfo", boom)
    for host in ("127.0.0.1", "::1", "localhost"):
        settings = DbSettings.from_env(env(ARTIGO2MD_POSTGRES_HOST=host), cwd=tmp_path)
        assert settings.resolve_connect_host() == host


def test_postgres_name_resolves_once_and_returns_validated_address(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    fake = _fake_getaddrinfo(["172.18.0.2", "172.18.0.3"])

    def counting(host, *args, **kwargs):
        calls.append(host)
        return fake(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", counting)
    settings = DbSettings.from_env(env(ARTIGO2MD_POSTGRES_HOST="postgres"), cwd=tmp_path)
    assert settings.resolve_connect_host() == "172.18.0.2"
    assert calls == ["postgres"]


@pytest.mark.parametrize("addresses", [["8.8.8.8"], ["172.18.0.2", "93.184.216.34"], []])
def test_postgres_name_resolving_to_public_address_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, addresses: list[str]
) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo(addresses))
    settings = DbSettings.from_env(env(ARTIGO2MD_POSTGRES_HOST="postgres"), cwd=tmp_path)
    with pytest.raises(StoreConfigError) as info:
        settings.resolve_connect_host()
    assert "postgres" in str(info.value)
    for address in addresses:
        assert address not in str(info.value)


def test_postgres_name_not_found_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*args, **kwargs):
        raise socket.gaierror("sem resolução")

    monkeypatch.setattr(socket, "getaddrinfo", fail)
    settings = DbSettings.from_env(env(ARTIGO2MD_POSTGRES_HOST="postgres"), cwd=tmp_path)
    with pytest.raises(StoreConfigError):
        settings.resolve_connect_host()


def test_remote_host_is_refused_before_any_socket_is_opened(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Nenhum socket nem resolução de nome: a recusa acontece só pela lista fechada (Princípio I)."""

    def boom(*args, **kwargs):
        raise AssertionError("nenhuma rede deveria ser tocada")

    monkeypatch.setattr(socket, "getaddrinfo", boom)
    monkeypatch.setattr(socket, "create_connection", boom)
    monkeypatch.setattr(socket.socket, "connect", boom)
    with pytest.raises(StoreConfigError):
        DbSettings.from_env(env(ARTIGO2MD_POSTGRES_HOST="db.example.com"), cwd=tmp_path)
