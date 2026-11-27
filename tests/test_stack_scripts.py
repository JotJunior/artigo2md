"""Testes de `stack/up.sh` e `stack/down.sh` sem Docker real.

`docker` e `nc` são falsos, colocados à frente no PATH; cada chamada ao `docker` fica registrada
num arquivo de log. As senhas dos testes são geradas em tempo de execução (nunca constantes
versionadas) e os testes conferem que nenhum valor de credencial aparece nas mensagens.
"""
from __future__ import annotations

import os
import pty
import secrets
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
STACK_SRC = ROOT / "stack"
BASE_PATH = "/usr/bin:/bin"

FAKE_DOCKER = r"""#!/bin/sh
echo "docker $*" >> "$FAKE_LOG"
if [ "${1:-}" = "info" ]; then
    [ "${FAKE_INFO_RC:-0}" = 0 ] || echo "ORIGINAL-DOCKER-ERROR" >&2
    exit "${FAKE_INFO_RC:-0}"
fi
if [ "${1:-}" = "image" ]; then
    exit "${FAKE_IMAGE_RC:-1}"
fi
if [ "${1:-}" = "compose" ]; then
    shift
    while [ $# -gt 0 ]; do
        case $1 in
            --project-directory | --env-file | -f) shift 2 ;;
            *) break ;;
        esac
    done
    sub=${1:-}
    case $sub in
        version)
            [ "${FAKE_COMPOSE_RC:-0}" = 0 ] || echo "ORIGINAL-COMPOSE-ERROR" >&2
            exit "${FAKE_COMPOSE_RC:-0}" ;;
        port)
            # `compose port rabbitmq 15672`: com FAKE_PANEL_ADDR o painel está publicado; sem ele, falha
            # (a forma real da saída sem publicação é validada contra o Docker real em 11.4)
            [ -n "${FAKE_PANEL_ADDR:-}" ] || exit 1
            printf '%s\n' "$FAKE_PANEL_ADDR"
            exit 0 ;;
        ps)
            case " $* " in
                *" --services "*) [ -z "${FAKE_RUNNING:-}" ] || printf '%s\n' $FAKE_RUNNING ;;
                *" --format "*) [ -z "${FAKE_PS_TABLE:-}" ] || printf '%b\n' "$FAKE_PS_TABLE" ;;
            esac
            exit 0 ;;
        up)
            echo "saida-original-do-compose: criando containers"
            exit "${FAKE_UP_RC:-0}" ;;
        down)
            echo "env ARTIGO2MD_DATA_DIR=${ARTIGO2MD_DATA_DIR:-}" >> "$FAKE_LOG"
            exit "${FAKE_DOWN_RC:-0}" ;;
    esac
fi
exit 0
"""

FAKE_NC = r"""#!/bin/sh
echo "nc $*" >> "$FAKE_LOG"
for last; do :; done
for p in ${FAKE_BUSY_PORTS:-}; do
    [ "$p" = "$last" ] && exit 0
done
exit 1
"""


class Stack:
    """Cópia isolada de stack/ numa pasta temporária, com docker/nc falsos."""

    def __init__(self, tmp: Path):
        self.root = tmp / "repo"
        self.stack = self.root / "stack"
        self.stack.mkdir(parents=True)
        for name in ("up.sh", "down.sh", "compose.yaml", "compose.management.yaml", ".env.example"):
            shutil.copy(STACK_SRC / name, self.stack / name)
        self.bin = tmp / "bin"
        self.bin.mkdir()
        self.log = tmp / "calls.log"
        self.log.write_text("")
        self.add_fake("docker", FAKE_DOCKER)
        self.add_fake("nc", FAKE_NC)
        self.secret = "S3nha-" + secrets.token_hex(8)  # 18 caracteres, gerada em tempo de execução
        self.secret2 = "R4bbit-" + secrets.token_hex(8)
        self.data_dir = (tmp / "dados").resolve()  # forma canônica (no macOS, /var -> /private/var)
        self.data_dir.mkdir()

    def add_fake(self, name: str, body: str) -> None:
        path = self.bin / name
        path.write_text(body)
        path.chmod(path.stat().st_mode | stat.S_IEXEC)

    def write_env(self, **overrides: str | None) -> dict[str, str]:
        values = {
            "ARTIGO2MD_POSTGRES_PORT": "5432",
            "ARTIGO2MD_RABBITMQ_PORT": "5672",
            "ARTIGO2MD_RABBITMQ_MANAGEMENT_PORT": "15672",
            "ARTIGO2MD_API_PORT": "8778",
            "POSTGRES_USER": "artigo2md",
            "POSTGRES_PASSWORD": self.secret,
            "POSTGRES_DB": "artigo2md",
            "RABBITMQ_DEFAULT_USER": "artigo2md",
            "RABBITMQ_DEFAULT_PASS": self.secret2,
            "ARTIGO2MD_DATA_DIR": str(self.data_dir),
        }
        for k, v in overrides.items():
            if v is None:
                values.pop(k, None)
            else:
                values[k] = v
        (self.stack / ".env").write_text("".join(f"{k}={v}\n" for k, v in values.items()))
        return values

    def calls(self) -> list[str]:
        return [line for line in self.log.read_text().splitlines() if line.startswith("docker ")]

    def run(self, script: str, *args: str, env: dict | None = None, path: str | None = None,
            stdin=subprocess.DEVNULL, cwd: Path | None = None):
        full_env = {"PATH": path if path is not None else f"{self.bin}:{BASE_PATH}",
                    "HOME": str(self.root), "FAKE_LOG": str(self.log)}
        full_env.update(env or {})
        return subprocess.run(
            ["/bin/sh", str(self.stack / script), *args], capture_output=True, text=True, env=full_env,
            stdin=stdin, cwd=cwd or self.root, timeout=60,
        )


@pytest.fixture
def stack(tmp_path) -> Stack:
    return Stack(tmp_path)


def no_secret_leak(stack: Stack, proc) -> None:
    for value in (stack.secret, stack.secret2):
        assert value not in proc.stdout and value not in proc.stderr


# ── up.sh: validações que nunca chamam o Docker (exit 2) ─────────────────────────
def test_up_without_env_file_explains_how_to_create_it(stack):
    proc = stack.run("up.sh")

    assert proc.returncode == 2
    assert "cp stack/.env.example stack/.env" in proc.stderr
    assert stack.calls() == []


def test_up_rejects_unknown_option_and_lists_the_valid_one(stack):
    stack.write_env()

    proc = stack.run("up.sh", "--forcar")

    assert proc.returncode == 2
    assert "--atualizar-imagens" in proc.stderr
    assert stack.calls() == []


@pytest.mark.parametrize(
    ("overrides", "extra_lines", "variable"),
    [
        ({"POSTGRES_PASSWORD": "troque-esta-senha"}, "", "POSTGRES_PASSWORD"),
        ({"RABBITMQ_DEFAULT_PASS": "troque-esta-senha"}, "", "RABBITMQ_DEFAULT_PASS"),
        ({"POSTGRES_PASSWORD": "curta123"}, "", "POSTGRES_PASSWORD"),
        ({"RABBITMQ_DEFAULT_PASS": "curta123"}, "", "RABBITMQ_DEFAULT_PASS"),
        ({"POSTGRES_PASSWORD": ""}, "", "POSTGRES_PASSWORD"),
        ({"POSTGRES_PASSWORD": None}, "", "POSTGRES_PASSWORD"),
        ({"POSTGRES_USER": ""}, "", "POSTGRES_USER"),
        ({"POSTGRES_DB": None}, "", "POSTGRES_DB"),
        ({"RABBITMQ_DEFAULT_USER": ""}, "", "RABBITMQ_DEFAULT_USER"),
        ({"POSTGRES_PASSWORD": "abc$def12345678"}, "", "POSTGRES_PASSWORD"),
        ({}, "POSTGRES_HOST_AUTH_METHOD=trust\n", "POSTGRES_HOST_AUTH_METHOD"),
        ({}, "POSTGRES_DB=outro\n", "POSTGRES_DB"),  # chave duplicada
        ({"ARTIGO2MD_API_PORT": "abc"}, "", "ARTIGO2MD_API_PORT"),
        ({"ARTIGO2MD_POSTGRES_PORT": "0"}, "", "ARTIGO2MD_POSTGRES_PORT"),
        ({"ARTIGO2MD_RABBITMQ_PORT": "65536"}, "", "ARTIGO2MD_RABBITMQ_PORT"),
        ({"ARTIGO2MD_API_PORT": None}, "", "ARTIGO2MD_API_PORT"),
    ],
    ids=lambda v: v if isinstance(v, str) and v.isupper() else None,
)
def test_up_invalid_env_exits_2_naming_only_the_variable(stack, overrides, extra_lines, variable):
    stack.write_env(**overrides)
    with (stack.stack / ".env").open("a") as f:
        f.write(extra_lines)

    proc = stack.run("up.sh")

    assert proc.returncode == 2
    assert variable in proc.stderr
    no_secret_leak(stack, proc)
    assert "abc$def12345678" not in proc.stderr and "curta123" not in proc.stderr
    assert stack.calls() == []


# ── up.sh: pasta de dados do app (ARTIGO2MD_DATA_DIR) ────────────────────────────
def _data_dir_cases(stack: Stack) -> list:
    arquivo = stack.data_dir.parent / "arquivo.txt"
    arquivo.write_text("x")
    link = stack.data_dir.parent / "atalho"
    link.symlink_to(stack.data_dir)
    return [
        ("ausente", None),
        ("vazia", ""),
        ("relativa", "dados"),
        ("relativa-com-ponto", "./dados"),
        ("inexistente", str(stack.data_dir.parent / "nao-existe")),
        ("arquivo", str(arquivo)),
        ("link-simbolico", str(link)),
        ("barra-final", str(stack.data_dir) + "/"),
        ("ponto-ponto", str(stack.data_dir / ".." / stack.data_dir.name)),
    ]


@pytest.mark.parametrize("case", ["ausente", "vazia", "relativa", "relativa-com-ponto", "inexistente",
                                  "arquivo", "link-simbolico", "barra-final", "ponto-ponto"])
def test_up_rejects_an_invalid_data_dir_in_portuguese_without_calling_docker(stack, case):
    value = dict(_data_dir_cases(stack))[case]
    stack.write_env(ARTIGO2MD_DATA_DIR=value)

    proc = stack.run("up.sh")

    assert proc.returncode == 2
    assert "ARTIGO2MD_DATA_DIR" in proc.stderr
    no_secret_leak(stack, proc)
    assert stack.calls() == []


def test_up_data_dir_error_is_checked_also_for_an_already_existing_env_file(stack):
    stack.write_env()
    (stack.stack / ".env").write_text(
        (stack.stack / ".env").read_text().replace(f"ARTIGO2MD_DATA_DIR={stack.data_dir}\n", "")
    )

    proc = stack.run("up.sh")

    assert proc.returncode == 2
    assert "ARTIGO2MD_DATA_DIR" in proc.stderr and "pasta de dados" in proc.stderr
    assert stack.calls() == []


def test_up_accepts_a_canonical_data_dir_and_reaches_docker(stack):
    stack.write_env()

    proc = stack.run("up.sh")

    assert proc.returncode == 0
    assert any(" up " in c for c in stack.calls())


def test_up_noncanonical_data_dir_message_suggests_the_real_path(stack):
    link = stack.data_dir.parent / "atalho"
    link.symlink_to(stack.data_dir)
    stack.write_env(ARTIGO2MD_DATA_DIR=str(link))

    proc = stack.run("up.sh")

    assert proc.returncode == 2
    assert str(stack.data_dir) in proc.stderr


def test_up_normalizes_export_spaces_quotes_and_comments_like_compose(stack):
    stack.write_env()
    (stack.stack / ".env").write_text(
        "# comentário\n\n"
        "export ARTIGO2MD_POSTGRES_PORT = 5432\n"
        'ARTIGO2MD_RABBITMQ_PORT="5672"\n'
        "ARTIGO2MD_RABBITMQ_MANAGEMENT_PORT='15672'\n"
        "ARTIGO2MD_API_PORT=8778\n"
        "POSTGRES_USER=artigo2md\n"
        f'POSTGRES_PASSWORD = "{stack.secret}"\n'
        "POSTGRES_DB=artigo2md\n"
        "RABBITMQ_DEFAULT_USER=artigo2md\n"
        f"export RABBITMQ_DEFAULT_PASS='{stack.secret2}'\n"
        f'ARTIGO2MD_DATA_DIR = "{stack.data_dir}"\n'
    )

    proc = stack.run("up.sh")

    assert proc.returncode == 0, proc.stderr
    no_secret_leak(stack, proc)


def test_up_does_not_execute_anything_from_the_env_file(stack, tmp_path):
    marker = tmp_path / "executou"
    stack.write_env()
    with (stack.stack / ".env").open("a") as f:
        f.write(f"EXTRA=`touch {marker}`\nOUTRA=x; touch {marker}\n")
    # o valor com crase não tem `$`, então passa na validação e prova que nada foi executado

    stack.run("up.sh")

    assert not marker.exists()


# ── up.sh: Docker indisponível (exit 3) ──────────────────────────────────────────
def test_up_docker_not_installed_exits_3(stack):
    stack.write_env()
    if shutil.which("docker", path=BASE_PATH):
        pytest.skip("docker real encontrado em /usr/bin ou /bin")

    proc = stack.run("up.sh", path=BASE_PATH)

    assert proc.returncode == 3
    assert "Docker não está disponível" in proc.stderr


def test_up_docker_daemon_stopped_exits_3_with_portuguese_message(stack):
    stack.write_env()

    proc = stack.run("up.sh", env={"FAKE_INFO_RC": "1"})

    assert proc.returncode == 3
    assert "Docker não está disponível" in proc.stderr
    assert "ORIGINAL-DOCKER-ERROR" not in proc.stderr + proc.stdout
    assert not any(" up" in c for c in stack.calls())


def test_up_docker_without_compose_plugin_exits_3_with_portuguese_message(stack):
    stack.write_env()

    proc = stack.run("up.sh", env={"FAKE_COMPOSE_RC": "1"})

    assert proc.returncode == 3
    assert "Docker Compose não está disponível" in proc.stderr
    assert "ORIGINAL-COMPOSE-ERROR" not in proc.stderr + proc.stdout


# ── up.sh: porta ocupada (exit 4) ────────────────────────────────────────────────
def test_up_busy_port_exits_4_naming_the_port_and_the_variable(stack):
    stack.write_env(ARTIGO2MD_POSTGRES_PORT="55432")

    proc = stack.run("up.sh", env={"FAKE_BUSY_PORTS": "55432"})

    assert proc.returncode == 4
    assert "55432" in proc.stderr and "ARTIGO2MD_POSTGRES_PORT" in proc.stderr
    assert not any(" up " in c for c in stack.calls())
    assert "-z -w 1 127.0.0.1" in stack.log.read_text()


@pytest.mark.parametrize(
    ("busy", "variable"),
    [("5672", "ARTIGO2MD_RABBITMQ_PORT"), ("8778", "ARTIGO2MD_API_PORT")],
)
def test_up_busy_port_check_covers_every_published_port(stack, busy, variable):
    stack.write_env()

    proc = stack.run("up.sh", env={"FAKE_BUSY_PORTS": busy})

    assert proc.returncode == 4
    assert variable in proc.stderr


def test_up_management_port_is_validated_only_with_the_management_panel_option(stack):
    stack.write_env(ARTIGO2MD_RABBITMQ_MANAGEMENT_PORT="-5")

    with_option = stack.run("up.sh", "--management-panel")
    without_option = stack.run("up.sh")

    assert with_option.returncode == 2
    assert "ARTIGO2MD_RABBITMQ_MANAGEMENT_PORT" in with_option.stderr
    assert without_option.returncode == 0, without_option.stderr  # sem a opção nada dessa porta é validado
    no_secret_leak(stack, with_option)


def test_up_management_port_busy_check_runs_only_with_the_management_panel_option(stack):
    stack.write_env()

    with_option = stack.run("up.sh", "--management-panel", env={"FAKE_BUSY_PORTS": "15672"})
    without_option = stack.run("up.sh", env={"FAKE_BUSY_PORTS": "15672"})

    assert with_option.returncode == 4
    assert "ARTIGO2MD_RABBITMQ_MANAGEMENT_PORT" in with_option.stderr and "15672" in with_option.stderr
    assert without_option.returncode == 0, without_option.stderr
    # sem a opção, `nc` nunca foi chamado para a 15672 (só o primeiro processo, com a opção, a testou)
    nc_calls = [c for c in stack.log.read_text().splitlines() if c.startswith("nc ")]
    assert sum(1 for c in nc_calls if c.endswith(" 15672")) == 1


def test_up_ignores_busy_port_of_a_service_that_is_already_running(stack):
    stack.write_env()

    proc = stack.run("up.sh", env={"FAKE_BUSY_PORTS": "5432", "FAKE_RUNNING": "postgres"})

    assert proc.returncode == 0, proc.stderr
    assert any(" up " in c for c in stack.calls())


def test_up_skips_port_check_with_a_warning_when_nc_is_missing(stack, tmp_path):
    stack.write_env()
    (stack.bin / "nc").unlink()
    # PATH isolado só com os utilitários que o script usa, sem `nc` (o do sistema fica de fora)
    tools = tmp_path / "tools"
    tools.mkdir()
    for tool in ("dirname", "sed", "grep", "head", "cat"):
        (tools / tool).symlink_to(shutil.which(tool, path=BASE_PATH))

    proc = stack.run("up.sh", path=f"{stack.bin}:{tools}")

    assert proc.returncode == 0, proc.stderr
    assert "verificação de portas ocupadas foi pulada" in proc.stdout


# ── up.sh: falha ao subir (exit 5) ───────────────────────────────────────────────
def test_up_unhealthy_service_exits_5_with_service_and_logs_command(stack):
    stack.write_env()
    table = "postgres running healthy\\nrabbitmq running healthy\\napi exited "

    proc = stack.run("up.sh", env={"FAKE_UP_RC": "1", "FAKE_PS_TABLE": table})

    assert proc.returncode == 5
    assert "Serviço com problema: api" in proc.stderr
    assert "docker compose -p artigo2md logs api" in proc.stderr
    assert "postgres" not in proc.stderr.replace("docker compose -p artigo2md logs", "")
    no_secret_leak(stack, proc)


def test_up_failure_without_readable_status_points_to_every_service_logs(stack):
    stack.write_env()

    proc = stack.run("up.sh", env={"FAKE_UP_RC": "1"})

    assert proc.returncode == 5
    for svc in ("postgres", "rabbitmq", "api"):
        assert f"docker compose -p artigo2md logs {svc}" in proc.stderr


# ── up.sh: caminho feliz, idempotência, imagens ──────────────────────────────────
def test_up_success_reports_each_service_and_shows_compose_output_without_the_panel_by_default(stack):
    stack.write_env(ARTIGO2MD_RABBITMQ_MANAGEMENT_PORT="15999")

    proc = stack.run("up.sh")

    assert proc.returncode == 0, proc.stderr
    assert "saida-original-do-compose: criando containers" in proc.stdout
    for svc in ("postgres", "rabbitmq", "api"):
        assert f"{svc}: saudável" in proc.stdout
    # sem --management-panel: nenhuma URL do painel e a linha "não publicado"
    assert "http://127.0.0.1:15999" not in proc.stdout
    assert "Painel do RabbitMQ: não publicado (use --management-panel)" in proc.stdout
    assert "compose.management.yaml" not in " ".join(stack.calls())
    no_secret_leak(stack, proc)


def test_up_with_the_management_panel_option_adds_the_extra_file_and_shows_url_and_warning(stack):
    stack.write_env(ARTIGO2MD_RABBITMQ_MANAGEMENT_PORT="15999")

    proc = stack.run("up.sh", "--management-panel")

    assert proc.returncode == 0, proc.stderr
    assert "Painel do RabbitMQ: http://127.0.0.1:15999/" in proc.stdout
    assert "DNS rebinding" in proc.stdout
    assert "não publicado" not in proc.stdout
    up_call = next(c for c in stack.calls() if " up " in c)
    assert f"-f {stack.stack}/compose.yaml -f {stack.stack}/compose.management.yaml" in up_call
    no_secret_leak(stack, proc)


def test_up_uses_the_documented_compose_invocation_from_any_directory(stack, tmp_path):
    stack.write_env()
    elsewhere = tmp_path / "outro-lugar"
    elsewhere.mkdir()

    proc = stack.run("up.sh", cwd=elsewhere)

    assert proc.returncode == 0, proc.stderr
    up_call = next(c for c in stack.calls() if " up " in c)
    assert f"--project-directory {stack.stack}" in up_call
    assert f"--env-file {stack.stack}/.env" in up_call
    assert f"-f {stack.stack}/compose.yaml" in up_call
    assert "--detach --wait --wait-timeout 120 --pull missing" in up_call


@pytest.mark.parametrize(
    ("option", "panel_addr", "calls_up"),
    [
        ((), None, False),                              # sem a opção, painel fechado: atalho
        ((), "127.0.0.1:15672", True),                  # sem a opção, painel aberto: `up` o recria sem ele
        (("--management-panel",), "127.0.0.1:15672", False),  # com a opção, painel aberto: atalho
        (("--management-panel",), None, True),          # com a opção, painel fechado: `up` o publica
    ],
    ids=["default-closed", "default-open", "optin-open", "optin-closed"],
)
def test_up_already_running_shortcut_only_when_panel_publication_matches_the_mode(stack, option, panel_addr, calls_up):
    stack.write_env()
    env = {"FAKE_RUNNING": "postgres rabbitmq api"}
    if panel_addr:
        env["FAKE_PANEL_ADDR"] = panel_addr

    proc = stack.run("up.sh", *option, env=env)

    assert proc.returncode == 0, proc.stderr
    assert any(" up " in c for c in stack.calls()) is calls_up
    assert ("A infraestrutura já estava no ar." in proc.stdout) is (not calls_up)
    no_secret_leak(stack, proc)


def test_up_when_everything_is_running_does_not_call_up_again(stack):
    stack.write_env()

    proc = stack.run("up.sh", env={"FAKE_RUNNING": "postgres rabbitmq api"})

    assert proc.returncode == 0
    assert "A infraestrutura já estava no ar." in proc.stdout
    assert not any(" up " in c for c in stack.calls())


def test_up_with_a_partial_stack_calls_up(stack):
    stack.write_env()

    proc = stack.run("up.sh", env={"FAKE_RUNNING": "postgres"})

    assert proc.returncode == 0
    assert any(" up " in c for c in stack.calls())


def test_up_builds_the_api_image_only_when_it_is_missing(stack):
    stack.write_env()

    stack.run("up.sh", env={"FAKE_IMAGE_RC": "1"})  # imagem ausente
    stack.run("up.sh", env={"FAKE_IMAGE_RC": "0"})  # imagem presente

    ups = [c for c in stack.calls() if " up " in c]
    assert len(ups) == 2
    assert ups[0].endswith("--pull missing --build")
    assert ups[1].endswith("--pull missing --no-build")
    assert any(c == "docker image inspect artigo2md-api:local" for c in stack.calls())


def test_up_update_images_pulls_always_and_forces_build(stack):
    stack.write_env()

    proc = stack.run("up.sh", "--atualizar-imagens", env={"FAKE_IMAGE_RC": "0"})

    assert proc.returncode == 0, proc.stderr
    up_call = next(c for c in stack.calls() if " up " in c)
    assert up_call.endswith("--pull always --build")


def test_up_update_images_with_a_running_service_exits_2_without_changing_anything(stack):
    stack.write_env()

    proc = stack.run("up.sh", "--atualizar-imagens", env={"FAKE_RUNNING": "rabbitmq"})

    assert proc.returncode == 2
    assert "sh stack/down.sh" in proc.stderr
    assert not any(" up " in c or " down" in c for c in stack.calls())


# ── down.sh ──────────────────────────────────────────────────────────────────────
def test_down_without_options_keeps_the_volumes(stack):
    stack.write_env()

    proc = stack.run("down.sh")

    assert proc.returncode == 0
    down_call = next(c for c in stack.calls() if " down" in c)
    assert down_call.endswith(" down")
    assert "--volumes" not in down_call
    assert "mantidos" in proc.stdout


def test_down_uses_env_example_for_interpolation_when_env_is_missing(stack):
    proc = stack.run("down.sh")

    assert proc.returncode == 0
    down_call = next(c for c in stack.calls() if " down" in c)
    assert f"--env-file {stack.stack}/.env.example" in down_call


def test_down_works_with_a_broken_env_file(stack):
    (stack.stack / ".env").write_text("POSTGRES_PASSWORD=curta\nlixo\n")

    proc = stack.run("down.sh")

    assert proc.returncode == 0


def test_down_does_not_need_the_data_dir_to_interpolate_the_compose_file(stack):
    # o compose exige ARTIGO2MD_DATA_DIR (':?'); o down fornece um valor de preenchimento, sem exigir o .env
    for env_text in (None, "POSTGRES_PASSWORD=curta\n"):
        if env_text is not None:
            (stack.stack / ".env").write_text(env_text)
        proc = stack.run("down.sh", env={"ARTIGO2MD_DATA_DIR": ""})
        assert proc.returncode == 0
    envs = [line for line in stack.log.read_text().splitlines() if line.startswith("env ")]
    assert envs and all(line != "env ARTIGO2MD_DATA_DIR=" for line in envs)


def test_down_wipe_without_a_terminal_fails_closed(stack):
    stack.write_env()

    proc = stack.run("down.sh", "--apagar-dados")

    assert proc.returncode == 1
    assert "Sem terminal interativo" in proc.stderr
    assert not any(" down" in c for c in stack.calls())


def test_down_wipe_with_explicit_yes_removes_volumes_without_asking(stack):
    stack.write_env()

    proc = stack.run("down.sh", "--apagar-dados", "--sim")

    assert proc.returncode == 0
    assert next(c for c in stack.calls() if " down" in c).endswith(" down --volumes")
    assert "apagados" in proc.stdout


def _run_with_terminal(stack: Stack, answer: bytes, *args: str):
    master, slave = pty.openpty()
    try:
        env = {"PATH": f"{stack.bin}:{BASE_PATH}", "HOME": str(stack.root), "FAKE_LOG": str(stack.log)}
        proc = subprocess.Popen(
            ["/bin/sh", str(stack.stack / "down.sh"), *args], stdin=slave, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, env=env, cwd=stack.root,
        )
        os.close(slave)
        os.write(master, answer)
        out, err = proc.communicate(timeout=60)
        return proc.returncode, out, err
    finally:
        os.close(master)


def test_down_wipe_confirmed_by_typing_apagar(stack):
    stack.write_env()

    code, out, _err = _run_with_terminal(stack, b"apagar\n", "--apagar-dados")

    assert code == 0
    assert "digite apagar" in out
    assert next(c for c in stack.calls() if " down" in c).endswith(" down --volumes")


@pytest.mark.parametrize("answer", [b"nao\n", b"\n", b"APAGAR\n", b"apagar tudo\n"])
def test_down_wipe_refused_when_the_confirmation_does_not_match(stack, answer):
    stack.write_env()

    code, _out, err = _run_with_terminal(stack, answer, "--apagar-dados")

    assert code == 1
    assert "nada foi apagado" in err
    assert not any(" down" in c for c in stack.calls())


@pytest.mark.parametrize("args", [("--sim",), ("--forcar",), ("--apagar-dados", "--forcar"), ("apagar",)])
def test_down_invalid_option_combinations_exit_2_without_touching_docker(stack, args):
    stack.write_env()

    proc = stack.run("down.sh", *args)

    assert proc.returncode == 2
    assert "Uso: sh stack/down.sh" in proc.stderr
    assert stack.calls() == []


def test_down_docker_unavailable_exits_3(stack):
    proc = stack.run("down.sh", env={"FAKE_INFO_RC": "1"})

    assert proc.returncode == 3
    assert "Docker não está disponível" in proc.stderr
    assert "ORIGINAL-DOCKER-ERROR" not in proc.stderr


def test_down_docker_without_compose_exits_3(stack):
    proc = stack.run("down.sh", env={"FAKE_COMPOSE_RC": "1"})

    assert proc.returncode == 3
    assert "Docker Compose não está disponível" in proc.stderr


def test_scripts_are_executable_and_start_with_a_shebang():
    for name in ("up.sh", "down.sh"):
        path = STACK_SRC / name
        assert path.read_text().startswith("#!/bin/sh\n")
        assert os.access(path, os.X_OK)
