"""`ApiSettings` (`api/settings.py`): validação de cada variável, falha sem derrubar o processo e sem vazar senha (3.3)."""
from __future__ import annotations

import logging
import subprocess
import sys

import pytest

from artigo2md.api import settings as api_settings
from artigo2md.api.settings import (
    ALLOW_DEV_ORIGIN_VAR,
    APP_ORIGIN,
    DATA_DIR_HOST_VAR,
    DATA_DIR_MOUNT_VAR,
    DEV_ORIGIN,
    RABBITMQ_HOST_VAR,
    ApiSettings,
    BrokerSettings,
)
from artigo2md.jobqueue.settings import Credentials

SECRET_DB = "pg-segredo-XYZ"
SECRET_MQ = "mq-segredo-XYZ"


@pytest.fixture
def env(tmp_path):
    mount = tmp_path / "mount"
    mount.mkdir()
    return {
        DATA_DIR_HOST_VAR: "/Users/alguem/dados",
        DATA_DIR_MOUNT_VAR: str(mount),
        "ARTIGO2MD_RABBITMQ_HOST": "rabbitmq",
        "ARTIGO2MD_RABBITMQ_PORT": "5672",
        "RABBITMQ_DEFAULT_USER": "mq-user",
        "RABBITMQ_DEFAULT_PASS": SECRET_MQ,
        "ARTIGO2MD_POSTGRES_HOST": "postgres",
        "ARTIGO2MD_POSTGRES_PORT": "5432",
        "POSTGRES_USER": "pg-user",
        "POSTGRES_PASSWORD": SECRET_DB,
        "POSTGRES_DB": "artigo2md",
    }


def load(env, tmp_path):
    return ApiSettings.from_env(env, tmp_path)


def test_full_valid_configuration(env, tmp_path):
    s = load(env, tmp_path)
    assert s.data_dir_host == "/Users/alguem/dados"
    assert s.data_dir_mount == env[DATA_DIR_MOUNT_VAR]
    assert s.allowed_origins == ("tauri://localhost",)
    assert s.allow_dev_origin is False
    assert (s.broker.host, s.broker.port) == ("rabbitmq", 5672)
    assert (s.broker.credentials.user, s.broker.credentials.password) == ("mq-user", SECRET_MQ)
    assert (s.db.host, s.db.port, s.db.user, s.db.database) == ("postgres", 5432, "pg-user", "artigo2md")
    assert (s.data_dir_error, s.broker_error, s.db_error, s.warnings) == (None, None, None, ())


# ── pasta de dados ──────────────────────────────────────────────────────────────
@pytest.mark.parametrize("value", [
    "relativo/dados", "/dados/../etc", "/dados/./x", "/dados/", "/", "/dados\0x", "/dados//x", "   ",
])
def test_invalid_data_dir_host_is_reported_by_variable_name_only(env, tmp_path, value):
    env[DATA_DIR_HOST_VAR] = value
    s = load(env, tmp_path)
    assert (s.data_dir_host, s.data_dir_mount) == (None, None)
    assert DATA_DIR_HOST_VAR in s.data_dir_error
    assert len(value.strip()) < 4 or value not in s.data_dir_error


@pytest.mark.parametrize("value", ["relativo", "/data/../x", "/data/"])
def test_invalid_data_dir_mount_is_reported(env, tmp_path, value):
    env[DATA_DIR_MOUNT_VAR] = value
    s = load(env, tmp_path)
    assert s.data_dir_mount is None and DATA_DIR_MOUNT_VAR in s.data_dir_error


def test_mount_must_be_an_existing_directory(env, tmp_path):
    env[DATA_DIR_MOUNT_VAR] = str(tmp_path / "nao-existe")
    s = load(env, tmp_path)
    assert s.data_dir_mount is None and DATA_DIR_MOUNT_VAR in s.data_dir_error
    (tmp_path / "arquivo").write_text("x")
    env[DATA_DIR_MOUNT_VAR] = str(tmp_path / "arquivo")
    assert load(env, tmp_path).data_dir_error


@pytest.mark.parametrize("missing", [DATA_DIR_HOST_VAR, DATA_DIR_MOUNT_VAR])
def test_missing_data_dir_variable(env, tmp_path, missing):
    del env[missing]
    s = load(env, tmp_path)
    assert (s.data_dir_host, s.data_dir_mount) == (None, None)
    assert missing in s.data_dir_error


# ── origens ─────────────────────────────────────────────────────────────────────
def test_origin_list_is_fixed_to_the_app_origin_by_default(env, tmp_path):
    s = load(env, tmp_path)
    assert s.allowed_origins == (APP_ORIGIN,) == ("tauri://localhost",) and s.warnings == ()


@pytest.mark.parametrize("raw", ["*", "https://app.exemplo.com", "http://localhost:1420", ""])
def test_removed_origins_variable_is_ignored(env, tmp_path, raw):
    """`ARTIGO2MD_API_ALLOWED_ORIGINS` foi removida (decisão do operador, 13.9): defini-la não muda a lista."""
    env["ARTIGO2MD_API_ALLOWED_ORIGINS"] = raw
    s = load(env, tmp_path)
    assert s.allowed_origins == (APP_ORIGIN,) and s.warnings == ()
    assert not hasattr(api_settings, "ALLOWED_ORIGINS_VAR") and not hasattr(api_settings, "parse_origins")


@pytest.mark.parametrize("raw", ["1", "true", "TRUE", "True", " 1 "])
def test_dev_origin_only_with_explicit_variable(env, tmp_path, raw):
    assert DEV_ORIGIN not in load(env, tmp_path).allowed_origins
    env[ALLOW_DEV_ORIGIN_VAR] = raw
    s = load(env, tmp_path)
    assert s.allow_dev_origin is True
    assert s.allowed_origins == ("tauri://localhost", DEV_ORIGIN)


@pytest.mark.parametrize("raw", ["0", "false", "yes", "sim", "2"])
def test_dev_origin_stays_off_for_any_other_value(env, tmp_path, raw):
    env[ALLOW_DEV_ORIGIN_VAR] = raw
    s = load(env, tmp_path)
    assert s.allow_dev_origin is False and DEV_ORIGIN not in s.allowed_origins
    assert (raw == "0" or raw == "false" or any(ALLOW_DEV_ORIGIN_VAR in w for w in s.warnings))


@pytest.mark.parametrize("raw", [None, ""])
def test_dev_origin_absent_or_empty_is_off_without_warning(env, tmp_path, raw):
    if raw is not None:
        env[ALLOW_DEV_ORIGIN_VAR] = raw
    s = load(env, tmp_path)
    assert s.allow_dev_origin is False and s.warnings == ()


# ── broker ──────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost", "rabbitmq"])
def test_allowed_broker_hosts(env, tmp_path, host):
    env[RABBITMQ_HOST_VAR] = host
    assert load(env, tmp_path).broker.host == host


def test_broker_host_defaults_to_loopback(env, tmp_path):
    del env[RABBITMQ_HOST_VAR]
    assert load(env, tmp_path).broker.host == "127.0.0.1"


@pytest.mark.parametrize("host", ["10.0.0.5", "broker.exemplo.com", "rabbitmq.exemplo.com", "0.0.0.0"])
def test_disallowed_broker_host_is_refused_without_echo(env, tmp_path, host):
    env[RABBITMQ_HOST_VAR] = host
    s = load(env, tmp_path)
    assert s.broker is None and RABBITMQ_HOST_VAR in s.broker_error and host not in s.broker_error


@pytest.mark.parametrize("port", ["0", "65536", "abc", "-1"])
def test_invalid_broker_port(env, tmp_path, port):
    env["ARTIGO2MD_RABBITMQ_PORT"] = port
    s = load(env, tmp_path)
    assert s.broker is None and "ARTIGO2MD_RABBITMQ_PORT" in s.broker_error


@pytest.mark.parametrize("missing", ["RABBITMQ_DEFAULT_USER", "RABBITMQ_DEFAULT_PASS"])
def test_missing_broker_credentials_cite_variable_names(env, tmp_path, missing):
    del env[missing]
    s = load(env, tmp_path)
    assert s.broker is None and "RABBITMQ_DEFAULT_USER" in s.broker_error
    assert "mq-user" not in s.broker_error and SECRET_MQ not in s.broker_error


def test_broker_rabbitmq_name_must_resolve_to_local_addresses(monkeypatch):
    broker = BrokerSettings("rabbitmq", 5672, Credentials("u", "p", "ambiente"))

    def fake(addresses):
        return lambda *a, **k: [(2, 1, 6, "", (address, 0)) for address in addresses]

    monkeypatch.setattr(api_settings.socket, "getaddrinfo", fake(["172.18.0.3"]))
    assert broker.resolve_connect_host() == "172.18.0.3"
    assert broker.with_resolved_host().host == "172.18.0.3"
    monkeypatch.setattr(api_settings.socket, "getaddrinfo", fake(["172.18.0.3", "8.8.8.8"]))
    with pytest.raises(api_settings.ConfigError):
        broker.resolve_connect_host()
    monkeypatch.setattr(api_settings.socket, "getaddrinfo", fake([]))
    with pytest.raises(api_settings.ConfigError):
        broker.resolve_connect_host()

    def boom(*a, **k):
        raise OSError("sem dns")

    monkeypatch.setattr(api_settings.socket, "getaddrinfo", boom)
    with pytest.raises(api_settings.ConfigError):
        broker.resolve_connect_host()


def test_loopback_broker_host_is_not_resolved(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("não deve resolver nome")

    monkeypatch.setattr(api_settings.socket, "getaddrinfo", boom)
    assert BrokerSettings("localhost", 5672, Credentials("u", "p", "ambiente")).resolve_connect_host() == "localhost"


# ── banco ───────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("missing", ["POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB", "ARTIGO2MD_POSTGRES_PORT"])
def test_missing_db_variable_is_reported_by_name(env, tmp_path, missing):
    del env[missing]
    s = load(env, tmp_path)
    assert s.db is None and missing in s.db_error and SECRET_DB not in s.db_error


def test_disallowed_db_host(env, tmp_path):
    env["ARTIGO2MD_POSTGRES_HOST"] = "db.exemplo.com"
    s = load(env, tmp_path)
    assert s.db is None and "db.exemplo.com" not in s.db_error


# ── falha nunca derruba; senha nunca vaza ───────────────────────────────────────
def test_empty_environment_never_raises_and_reports_every_area(tmp_path):
    s = ApiSettings.from_env({}, tmp_path)
    assert (s.data_dir_host, s.broker, s.db) == (None, None, None)
    assert s.data_dir_error and s.broker_error and s.db_error
    assert s.allowed_origins == ("tauri://localhost",)


def test_garbage_environment_never_raises(tmp_path):
    garbage = {k: "\0\x01lixo" for k in (
        DATA_DIR_HOST_VAR, DATA_DIR_MOUNT_VAR, ALLOW_DEV_ORIGIN_VAR, RABBITMQ_HOST_VAR,
        "ARTIGO2MD_RABBITMQ_PORT", "RABBITMQ_DEFAULT_USER", "ARTIGO2MD_POSTGRES_PORT", "POSTGRES_USER")}
    s = ApiSettings.from_env(garbage, tmp_path)
    assert s.allowed_origins == (APP_ORIGIN,) and s.broker is None and s.db is None


def test_passwords_never_appear_in_repr_str_warnings_or_log(env, tmp_path, caplog):
    env[ALLOW_DEV_ORIGIN_VAR] = "talvez"
    del env[DATA_DIR_HOST_VAR]
    with caplog.at_level(logging.DEBUG):
        s = load(env, tmp_path)
    blob = " ".join([repr(s), str(s), repr(s.broker), str(s.broker), repr(s.db), str(s.db), *s.warnings,
                     *(r.getMessage() for r in caplog.records)])
    assert SECRET_MQ not in blob and SECRET_DB not in blob
    assert "mq-user" in repr(s.broker)  # só a senha é mascarada


def test_stack_env_file_is_a_source_for_broker_and_db(tmp_path):
    stack = tmp_path / "stack"
    stack.mkdir()
    (stack / ".env").write_text(
        f"RABBITMQ_DEFAULT_USER=u\nRABBITMQ_DEFAULT_PASS={SECRET_MQ}\nARTIGO2MD_RABBITMQ_PORT=5673\n"
        f"ARTIGO2MD_POSTGRES_PORT=5433\nPOSTGRES_USER=pu\nPOSTGRES_PASSWORD={SECRET_DB}\nPOSTGRES_DB=d\n",
        encoding="utf-8")
    s = ApiSettings.from_env({}, tmp_path)
    assert s.broker.port == 5673 and s.db.port == 5433
    assert SECRET_MQ not in repr(s) and SECRET_DB not in repr(s)


def test_importing_settings_does_not_load_conversion_or_driver_modules():
    code = (
        "import sys, artigo2md.api.settings\n"
        "bad=[m for m in ('docling','pymupdf4llm','pypdfium2','artigo2md.router','artigo2md.pdfscan','pika','asyncpg')"
        " if m in sys.modules]\nprint(','.join(bad))\n")
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == ""
