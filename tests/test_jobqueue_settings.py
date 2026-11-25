"""Configuração, porta e credenciais do worker (tarefa 3.3 da `conversion-queue`)."""
from __future__ import annotations

import dataclasses
import inspect
import os
import subprocess
import sys
from pathlib import Path

import pytest

from artigo2md import infra
from artigo2md.jobqueue import settings
from artigo2md.jobqueue.settings import ConfigError

SECRET = "senha-so-de-teste-9f3"
USER = "usuario-teste"


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    path = tmp_path / "dados"
    path.mkdir()
    return path


@pytest.fixture
def clean_env(monkeypatch):
    for var in (*settings.CREDENTIAL_VARS, infra.STACK_ENV_VAR, "ARTIGO2MD_RABBITMQ_PORT"):
        monkeypatch.delenv(var, raising=False)


def _stack_env(directory: Path, **values: str) -> Path:
    (directory / "stack").mkdir(exist_ok=True)
    path = directory / "stack" / ".env"
    path.write_text("".join(f"{k}={v}\n" for k, v in values.items()), encoding="utf-8")
    return path


def _load(data_dir, **kwargs):
    base = dict(data_dir=data_dir, workers=2, min_free_memory_mb=1024)
    base.update(kwargs)
    return settings.load_settings(**base)


# ── ordem de resolução das credenciais ──────────────────────────────────────────
def test_environment_wins_over_files(tmp_path, data_dir, clean_env):
    _stack_env(tmp_path, RABBITMQ_DEFAULT_USER="do-arquivo", RABBITMQ_DEFAULT_PASS="do-arquivo")
    env = {"RABBITMQ_DEFAULT_USER": USER, "RABBITMQ_DEFAULT_PASS": SECRET}
    cred = settings.resolve_credentials(env, tmp_path)
    assert (cred.user, cred.password, cred.origin) == (USER, SECRET, "ambiente")


def test_stack_env_var_file_wins_over_stack_dot_env(tmp_path, clean_env):
    _stack_env(tmp_path, RABBITMQ_DEFAULT_USER="u-stack", RABBITMQ_DEFAULT_PASS="p-stack")
    other = tmp_path / "outro.env"
    other.write_text(f"RABBITMQ_DEFAULT_USER=u-outro\nRABBITMQ_DEFAULT_PASS={SECRET}\n", encoding="utf-8")
    cred = settings.resolve_credentials({infra.STACK_ENV_VAR: str(other)}, tmp_path)
    assert (cred.user, cred.password, cred.origin) == ("u-outro", SECRET, "arquivo")


def test_falls_back_to_stack_dot_env(tmp_path, clean_env):
    _stack_env(tmp_path, RABBITMQ_DEFAULT_USER=USER, RABBITMQ_DEFAULT_PASS=SECRET)
    cred = settings.resolve_credentials({}, tmp_path)
    assert (cred.user, cred.password, cred.origin) == (USER, SECRET, "arquivo")


def test_empty_value_counts_as_unset_and_mixed_origin_is_reported(tmp_path, clean_env):
    _stack_env(tmp_path, RABBITMQ_DEFAULT_USER="u-arquivo", RABBITMQ_DEFAULT_PASS="p-arquivo")
    cred = settings.resolve_credentials({"RABBITMQ_DEFAULT_USER": "", "RABBITMQ_DEFAULT_PASS": SECRET}, tmp_path)
    assert (cred.user, cred.password, cred.origin) == ("u-arquivo", SECRET, "ambiente e arquivo")


def test_missing_credentials_name_only_the_variables(tmp_path, clean_env):
    _stack_env(tmp_path, RABBITMQ_DEFAULT_USER=USER)  # só a metade
    with pytest.raises(ConfigError) as info:
        settings.resolve_credentials({}, tmp_path)
    err = info.value
    assert err.exit_code == settings.EXIT_CREDENTIALS
    assert "RABBITMQ_DEFAULT_USER" in err.message and "RABBITMQ_DEFAULT_PASS" in err.message
    assert USER not in err.message


# ── segredo nunca aparece ───────────────────────────────────────────────────────
def test_repr_and_str_never_show_the_password(tmp_path, data_dir, clean_env):
    env = {"RABBITMQ_DEFAULT_USER": USER, "RABBITMQ_DEFAULT_PASS": SECRET}
    loaded = _load(data_dir, environ=env, cwd=tmp_path)
    for text in (repr(loaded), str(loaded), repr(loaded.credentials), str(loaded.credentials),
                 f"{loaded}", f"{loaded.credentials}"):
        assert SECRET not in text
    assert "***" in repr(loaded.credentials)


def test_logging_of_settings_has_no_password(tmp_path, data_dir, clean_env, caplog):
    import logging

    env = {"RABBITMQ_DEFAULT_USER": USER, "RABBITMQ_DEFAULT_PASS": SECRET}
    loaded = _load(data_dir, environ=env, cwd=tmp_path)
    with caplog.at_level(logging.DEBUG):
        logging.getLogger("t").info("config %s %r", loaded, loaded)
    assert SECRET not in caplog.text


def test_environment_is_scrubbed_after_loading(tmp_path, data_dir, clean_env, monkeypatch):
    monkeypatch.setenv("RABBITMQ_DEFAULT_USER", USER)
    monkeypatch.setenv("RABBITMQ_DEFAULT_PASS", SECRET)
    loaded = _load(data_dir, cwd=tmp_path)
    assert "RABBITMQ_DEFAULT_USER" not in os.environ and "RABBITMQ_DEFAULT_PASS" not in os.environ
    assert loaded.credentials.password == SECRET


def test_environment_is_scrubbed_even_when_credentials_come_from_a_file(tmp_path, data_dir, clean_env, monkeypatch):
    _stack_env(tmp_path, RABBITMQ_DEFAULT_USER=USER, RABBITMQ_DEFAULT_PASS=SECRET)
    monkeypatch.setenv("RABBITMQ_DEFAULT_USER", "")  # definida mas vazia
    _load(data_dir, cwd=tmp_path)
    assert not any(v in os.environ for v in settings.CREDENTIAL_VARS)


def test_environment_is_scrubbed_when_loading_fails(tmp_path, data_dir, clean_env, monkeypatch):
    monkeypatch.setenv("RABBITMQ_DEFAULT_USER", USER)  # sem a senha
    with pytest.raises(ConfigError):
        _load(data_dir, cwd=tmp_path)
    assert "RABBITMQ_DEFAULT_USER" not in os.environ


def test_spawned_child_does_not_see_the_credentials(tmp_path, data_dir, clean_env, monkeypatch):
    monkeypatch.setenv("RABBITMQ_DEFAULT_USER", USER)
    monkeypatch.setenv("RABBITMQ_DEFAULT_PASS", SECRET)
    _load(data_dir, cwd=tmp_path)
    out = subprocess.run(
        [sys.executable, "-I", "-c",
         "import os; print(sorted(k for k in os.environ if k.startswith('RABBITMQ_DEFAULT')))"],
        capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "[]"


# ── porta e host ────────────────────────────────────────────────────────────────
def test_port_follows_the_doctor_resolution(tmp_path, clean_env):
    assert settings.resolve_port({}, tmp_path) == 5672
    _stack_env(tmp_path, ARTIGO2MD_RABBITMQ_PORT="15673")
    assert settings.resolve_port({}, tmp_path) == 15673
    assert settings.resolve_port({"ARTIGO2MD_RABBITMQ_PORT": "25672"}, tmp_path) == 25672
    assert settings.resolve_port({}, tmp_path) == infra.resolve_ports({}, tmp_path)["rabbitmq"].port


def test_invalid_port_is_a_usage_error_naming_the_variable(tmp_path, clean_env):
    with pytest.raises(ConfigError) as info:
        settings.resolve_port({"ARTIGO2MD_RABBITMQ_PORT": "abc"}, tmp_path)
    assert "ARTIGO2MD_RABBITMQ_PORT" in info.value.message and info.value.exit_code == settings.EXIT_USAGE


def test_host_is_fixed_and_not_configurable(tmp_path, data_dir, clean_env):
    env = {"RABBITMQ_DEFAULT_USER": USER, "RABBITMQ_DEFAULT_PASS": SECRET,
           "ARTIGO2MD_RABBITMQ_HOST": "10.0.0.5", "RABBITMQ_HOST": "10.0.0.5"}
    loaded = _load(data_dir, environ=env, cwd=tmp_path)
    assert loaded.host == infra.HOST == "127.0.0.1"
    assert "host" not in {f.name for f in dataclasses.fields(settings.WorkerSettings)}
    assert "host" not in inspect.signature(settings.load_settings).parameters
    with pytest.raises(dataclasses.FrozenInstanceError):
        loaded.host = "x"  # type: ignore[misc]


# ── pasta de dados e intervalos ─────────────────────────────────────────────────
def test_data_dir_is_resolved(tmp_path, data_dir):
    link = tmp_path / "atalho"
    link.symlink_to(data_dir, target_is_directory=True)
    assert settings.validate_data_dir(link) == data_dir.resolve()


def test_invalid_data_dirs_are_refused_in_portuguese(tmp_path, data_dir):
    a_file = tmp_path / "arquivo"
    a_file.write_text("x")
    readonly = tmp_path / "somente-leitura"
    readonly.mkdir()
    readonly.chmod(0o500)
    try:
        cases = [tmp_path / "nao-existe", a_file] + ([readonly] if os.geteuid() != 0 else [])
        for bad in cases:
            with pytest.raises(ConfigError) as info:
                settings.validate_data_dir(bad)
            assert info.value.exit_code == settings.EXIT_USAGE
            assert any(w in info.value.message for w in ("pasta", "permissão"))
    finally:
        readonly.chmod(0o700)


@pytest.mark.parametrize("override", [
    {"workers": 0}, {"workers": -1}, {"workers": 1.5}, {"workers": True},
    {"min_free_memory_mb": -1}, {"retry_delay": 0}, {"retry_delay": -1}, {"max_attempts": 0},
    {"idle_timeout": -1}, {"shutdown_timeout": -0.1}, {"conversion_timeout": -1},
    {"retry_delay": float("nan")}, {"shutdown_timeout": float("inf")},
])
def test_out_of_range_values_are_refused(tmp_path, data_dir, clean_env, override):
    env = {"RABBITMQ_DEFAULT_USER": USER, "RABBITMQ_DEFAULT_PASS": SECRET}
    with pytest.raises(ConfigError):
        _load(data_dir, environ=env, cwd=tmp_path, **override)


def test_boundary_values_are_accepted(tmp_path, data_dir, clean_env):
    env = {"RABBITMQ_DEFAULT_USER": USER, "RABBITMQ_DEFAULT_PASS": SECRET}
    loaded = _load(data_dir, environ=env, cwd=tmp_path, workers=1, min_free_memory_mb=0, max_attempts=1,
                   idle_timeout=0, shutdown_timeout=0, conversion_timeout=0, retry_delay=0.1)
    assert (loaded.workers, loaded.min_free_memory_mb, loaded.max_attempts) == (1, 0, 1)


@pytest.mark.parametrize("value", [1800, 1800.0, 3600, 86400])
def test_conversion_timeout_at_or_above_the_broker_ack_deadline_is_refused_with_exit_2(
        tmp_path, data_dir, clean_env, value):
    """FR-025: o prazo do broker é 1800 s (`consumer_timeout` do RabbitMQ 4.4.1, research.md Decision 11)."""
    env = {"RABBITMQ_DEFAULT_USER": USER, "RABBITMQ_DEFAULT_PASS": SECRET}
    with pytest.raises(ConfigError) as info:
        _load(data_dir, environ=env, cwd=tmp_path, conversion_timeout=value)
    assert info.value.exit_code == settings.EXIT_USAGE
    assert "--conversion-timeout" in info.value.message and "1800" in info.value.message
    assert "prazo do broker" in info.value.message


@pytest.mark.parametrize("value", [0, 0.5, 60, 1799, 1799.9])
def test_conversion_timeout_zero_or_below_the_broker_ack_deadline_is_accepted(
        tmp_path, data_dir, clean_env, value):
    env = {"RABBITMQ_DEFAULT_USER": USER, "RABBITMQ_DEFAULT_PASS": SECRET}
    assert _load(data_dir, environ=env, cwd=tmp_path, conversion_timeout=value).conversion_timeout == value


def test_defaults_come_from_the_cli_contract(tmp_path, data_dir, clean_env):
    env = {"RABBITMQ_DEFAULT_USER": USER, "RABBITMQ_DEFAULT_PASS": SECRET}
    loaded = _load(data_dir, environ=env, cwd=tmp_path)
    assert (loaded.retry_delay, loaded.max_attempts, loaded.idle_timeout,
            loaded.shutdown_timeout, loaded.conversion_timeout) == (30, 3, 300, 30, 0)


# ── isolamento de imports ───────────────────────────────────────────────────────
def test_module_imports_infra_and_no_engines():
    code = (
        "import sys, artigo2md.jobqueue.settings\n"
        "bad = [m for m in ('pika', 'psutil', 'docling', 'pymupdf4llm', 'artigo2md.router', 'artigo2md.engines') "
        "if m in sys.modules]\n"
        "print(','.join(bad), 'artigo2md.infra' in sys.modules)\n"
    )
    out = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "True"
