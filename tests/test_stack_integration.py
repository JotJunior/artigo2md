"""Integração com a infraestrutura REAL (Docker), só com `ARTIGO2MD_STACK=1`.

Rode com:  ARTIGO2MD_STACK=1 uv run pytest tests/test_stack_integration.py

Como funciona (research Decision 10 e Decision 11, itens 8 e 14):

- `stack/` e o mínimo do contexto de build são COPIADOS para uma pasta temporária; o `stack/.env`
  de teste vive só nessa cópia (nunca no repositório). As senhas são geradas em tempo de execução.
- `COMPOSE_PROJECT_NAME` único isola containers, rede e volumes de teste de qualquer stack real do
  usuário (o Compose dá a essa variável precedência sobre o `name:` do arquivo).
- As portas são livres e escolhidas na hora, para não colidir com um Postgres/RabbitMQ existente.
- Nenhum valor de credencial é impresso: toda saída capturada passa por `_scrub` antes de entrar numa
  mensagem de asserção, e a saída de `docker compose config` nunca é exibida.
- Ao final, `down --volumes` apaga containers, rede e volumes DE TESTE e o módulo confere que nada
  sobrou. As imagens baixadas/construídas ficam (são cache e fazem parte do pré-requisito de SC-001).

Os testes rodam na ordem do arquivo e compartilham a mesma infraestrutura.
"""
from __future__ import annotations

import base64
import http.client
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("ARTIGO2MD_STACK"),
    reason="usa Docker e baixa imagens: defina ARTIGO2MD_STACK=1",
)

ROOT = Path(__file__).resolve().parent.parent
API_IMAGE = "artigo2md-api:local"
SERVICES = ("postgres", "rabbitmq", "api")
PORT_VARS = (
    "ARTIGO2MD_POSTGRES_PORT",
    "ARTIGO2MD_RABBITMQ_PORT",
    "ARTIGO2MD_RABBITMQ_MANAGEMENT_PORT",
    "ARTIGO2MD_API_PORT",
)
UP_DEADLINE = 120  # SC-001: 2 minutos, com as imagens já baixadas e a da API já construída


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@dataclass
class Stack:
    work: Path
    project: str
    env: dict[str, str]
    ports: dict[str, int]
    user: str
    password: str
    db: str

    # ── execução ────────────────────────────────────────────────────────────────
    def _scrub(self, text: str) -> str:
        return text.replace(self.password, "***") if self.password else text

    def run(self, *cmd: str, timeout: int = 600, env: dict[str, str] | None = None,
            cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            list(cmd), env=env or self.env, cwd=cwd or self.work, capture_output=True, text=True,
            stdin=subprocess.DEVNULL, timeout=timeout,
        )

    def script(self, name: str, *args: str) -> subprocess.CompletedProcess[str]:
        return self.run("sh", str(self.work / "stack" / name), *args)

    def dc(self, *args: str, timeout: int = 600) -> subprocess.CompletedProcess[str]:
        return self.run(
            "docker", "compose", "--project-directory", str(self.work / "stack"),
            "--env-file", str(self.work / "stack" / ".env"),
            "-f", str(self.work / "stack" / "compose.yaml"), *args, timeout=timeout,
        )

    def show(self, result: subprocess.CompletedProcess[str]) -> str:
        """Texto seguro (sem a senha) para mensagens de asserção."""
        return self._scrub(f"rc={result.returncode}\n{result.stdout[-1500:]}\n{result.stderr[-1500:]}")

    # ── consultas ───────────────────────────────────────────────────────────────
    def service_table(self) -> dict[str, tuple[str, str]]:
        """`docker compose ps --all --format '{{.Service}} {{.State}} {{.Health}}'` -> {serviço: (estado, saúde)}."""
        res = self.dc("ps", "--all", "--format", "{{.Service}} {{.State}} {{.Health}}")
        table: dict[str, tuple[str, str]] = {}
        for line in res.stdout.splitlines():
            parts = line.split()
            if parts:
                table[parts[0]] = (parts[1] if len(parts) > 1 else "", parts[2] if len(parts) > 2 else "")
        return table

    def container_ids(self) -> dict[str, str]:
        ids: dict[str, str] = {}
        for svc in SERVICES:
            out = self.dc("ps", "--all", "-q", svc).stdout.split()
            if out:
                ids[svc] = out[0]
        return ids

    def inspect(self, ident: str, fmt: str) -> str:
        return self.run("docker", "inspect", "--format", fmt, ident).stdout.strip()

    def project_volumes(self) -> list[str]:
        out = self.run("docker", "volume", "ls", "--quiet", "--filter",
                       f"label=com.docker.compose.project={self.project}").stdout
        return sorted(out.split())

    def project_containers(self) -> list[str]:
        out = self.run("docker", "ps", "--all", "--quiet", "--filter",
                       f"label=com.docker.compose.project={self.project}").stdout
        return out.split()

    def psql(self, sql: str) -> subprocess.CompletedProcess[str]:
        # Conexão pelo socket Unix de dentro do container (autenticação "trust" local da imagem):
        # a senha não trafega nem aparece na linha de comando.
        return self.dc("exec", "-T", "postgres", "psql", "-U", self.user, "-d", self.db,
                       "-v", "ON_ERROR_STOP=1", "--tuples-only", "--no-align", "-c", sql)

    def management(self, method: str, path: str, body: dict | None = None,
                   credentials: tuple[str, str] | None = None) -> tuple[int, str]:
        user, password = credentials or (self.user, self.password)
        auth = base64.b64encode(f"{user}:{password}".encode()).decode()
        headers = {"Authorization": f"Basic {auth}", "Content-Type": "application/json"}
        conn = http.client.HTTPConnection("127.0.0.1", self.ports["ARTIGO2MD_RABBITMQ_MANAGEMENT_PORT"], timeout=10)
        try:
            conn.request(method, path, body=json.dumps(body) if body is not None else None, headers=headers)
            resp = conn.getresponse()
            return resp.status, resp.read().decode()
        finally:
            conn.close()

    def wait_management(self, timeout: float = 60.0) -> None:
        """O painel pode demorar um instante a mais que o healthcheck do broker após um restart."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                if self.management("GET", "/api/overview")[0] == 200:
                    return
            except OSError:
                pass
            time.sleep(1)
        pytest.fail("o painel do RabbitMQ não respondeu a tempo")

    def api_get(self, path: str) -> tuple[int, str]:
        conn = http.client.HTTPConnection("127.0.0.1", self.ports["ARTIGO2MD_API_PORT"], timeout=10)
        try:
            conn.request("GET", path)
            resp = conn.getresponse()
            return resp.status, resp.read().decode()
        finally:
            conn.close()


@pytest.fixture(scope="module")
def stack(tmp_path_factory: pytest.TempPathFactory):
    if shutil.which("docker") is None or subprocess.run(
        ["docker", "info"], capture_output=True, stdin=subprocess.DEVNULL
    ).returncode != 0:
        pytest.fail("ARTIGO2MD_STACK=1 exige o Docker instalado e em execução")

    work = tmp_path_factory.mktemp("stack-it")
    shutil.copytree(ROOT / "stack", work / "stack", ignore=shutil.ignore_patterns(".env"))
    for name in ("pyproject.toml", "uv.lock", ".dockerignore"):
        shutil.copy2(ROOT / name, work / name)
    (work / "src").mkdir()
    shutil.copytree(ROOT / "src" / "artigo2md", work / "src" / "artigo2md",
                    ignore=shutil.ignore_patterns("__pycache__"))

    ports = {var: _free_port() for var in PORT_VARS}
    password = secrets.token_urlsafe(24)
    user, db = "it_user", "it_db"
    env_text = "".join(f"{var}={port}\n" for var, port in ports.items()) + (
        f"POSTGRES_USER={user}\nPOSTGRES_PASSWORD={password}\nPOSTGRES_DB={db}\n"
        f"RABBITMQ_DEFAULT_USER={user}\nRABBITMQ_DEFAULT_PASS={password}\n"
    )
    env_file = work / "stack" / ".env"
    env_file.write_text(env_text)
    env_file.chmod(0o600)

    project = f"artigo2md-it-{secrets.token_hex(4)}"
    env = {**os.environ, "COMPOSE_PROJECT_NAME": project}
    for key in list(env):  # o ambiente do desenvolvedor não pode mudar portas nem credenciais do teste
        if key.startswith("ARTIGO2MD_") and key != "ARTIGO2MD_STACK":
            del env[key]
    st = Stack(work, project, env, ports, user, password, db)

    # Pré-requisito de SC-001: imagens oficiais baixadas e imagem da API construída ANTES da medição.
    pulled = st.dc("pull", "postgres", "rabbitmq")
    assert pulled.returncode == 0, st.show(pulled)
    if st.run("docker", "image", "inspect", API_IMAGE).returncode != 0:
        built = st.dc("build", "api")
        assert built.returncode == 0, st.show(built)

    try:
        yield st
    finally:
        st.dc("down", "--volumes", "--remove-orphans")
        left_c, left_v = st.project_containers(), st.project_volumes()
        shutil.rmtree(work, ignore_errors=True)
        assert not left_c, f"containers de teste órfãos: {left_c}"
        assert not left_v, f"volumes de teste órfãos: {left_v}"


# ── 6.1.2 ────────────────────────────────────────────────────────────────────────
def test_up_from_scratch_all_services_healthy(stack: Stack):
    assert stack.project_containers() == [] and stack.project_volumes() == []

    started = time.monotonic()
    res = stack.script("up.sh")
    elapsed = time.monotonic() - started

    assert res.returncode == 0, stack.show(res)
    assert elapsed <= UP_DEADLINE, f"subiu em {elapsed:.0f} s (limite {UP_DEADLINE} s)"
    assert res.stdout.count("saudável") == 3, stack.show(res)
    mgmt = stack.ports["ARTIGO2MD_RABBITMQ_MANAGEMENT_PORT"]
    assert f"http://127.0.0.1:{mgmt}/" in res.stdout

    table = stack.service_table()
    assert table == {svc: ("running", "healthy") for svc in SERVICES}, table

    # Hipótese de compatibilidade (research Decision 11 item 11): `no-new-privileges` realmente aplicado.
    for svc, ident in stack.container_ids().items():
        opts = stack.inspect(ident, "{{json .HostConfig.SecurityOpt}}")
        assert "no-new-privileges:true" in opts, f"{svc}: {opts}"


# ── 6.1.3 ────────────────────────────────────────────────────────────────────────
def test_ports_published_on_loopback_only(stack: Stack):
    expected = {
        ("postgres", 5432): stack.ports["ARTIGO2MD_POSTGRES_PORT"],
        ("rabbitmq", 5672): stack.ports["ARTIGO2MD_RABBITMQ_PORT"],
        ("rabbitmq", 15672): stack.ports["ARTIGO2MD_RABBITMQ_MANAGEMENT_PORT"],
        ("api", 8000): stack.ports["ARTIGO2MD_API_PORT"],
    }
    for (svc, inner), host_port in expected.items():
        res = stack.dc("port", svc, str(inner))
        assert res.returncode == 0, f"{svc}:{inner} {stack.show(res)}"
        assert res.stdout.strip() == f"127.0.0.1:{host_port}", f"{svc}:{inner} -> {res.stdout.strip()!r}"

    # `docker compose config` traz credenciais interpoladas: só se lê o JSON em memória, e as
    # mensagens de falha citam serviço e porta, nunca o conteúdo.
    cfg = stack.dc("config", "--format", "json")
    assert cfg.returncode == 0, "docker compose config falhou"
    published = []
    for svc, definition in json.loads(cfg.stdout)["services"].items():
        for entry in definition.get("ports", []):
            assert entry.get("host_ip") == "127.0.0.1", f"{svc}: porta {entry.get('target')} sem host_ip loopback"
            published.append((svc, int(entry["target"]), int(entry["published"])))
    assert sorted((s, t) for s, t, _ in published) == sorted(expected), "conjunto de portas publicadas inesperado"
    # nem epmd (4369) nem a distribuição Erlang (25672) são publicadas
    assert not {4369, 25672} & {t for _, t, _ in published}


# ── 6.1.6 ────────────────────────────────────────────────────────────────────────
def test_health_endpoint_and_doctor_see_the_stack(stack: Stack):
    status, body = stack.api_get("/health")
    assert status == 200
    payload = json.loads(body)
    assert set(payload) == {"status", "service", "version"}
    assert all(isinstance(v, str) for v in payload.values())
    assert payload["status"] == "ok" and payload["service"] == "artigo2md-api"
    assert stack.api_get("/docs")[0] == 404

    doctor_cmd = [sys.executable, "-c", "import sys; from artigo2md.cli import main; sys.exit(main(['doctor']))"]
    # (a) portas pelas variáveis de ambiente
    env_ports = {**stack.env, **{var: str(port) for var, port in stack.ports.items()}}
    res = stack.run(*doctor_cmd, env=env_ports, cwd=stack.work, timeout=300)
    lines = {ln.split()[0]: ln for ln in res.stdout.splitlines() if ln.split() and ln.split()[0] in SERVICES}
    assert set(lines) == set(SERVICES), stack.show(res)
    for svc in SERVICES:
        assert lines[svc].rstrip().endswith("acessível") and "inacessível" not in lines[svc], lines[svc]
    assert "(portas: ambiente)" in res.stdout

    # (b) portas lidas de ./stack/.env no diretório corrente
    res = stack.run(*doctor_cmd, cwd=stack.work, timeout=300)
    assert "(portas: stack/.env)" in res.stdout, stack.show(res)
    assert res.stdout.count("acessível") - res.stdout.count("inacessível") == 3, stack.show(res)


# ── 8.1 (research Decision 11 item 13, CHK013) ───────────────────────────────────
def test_guest_account_cannot_authenticate_over_network(stack: Stack):
    """A conta padrão `guest` não pode autenticar pela porta publicada.

    A listagem dos usuários do broker é feita dentro do container e só os NOMES são lidos (a saída
    JSON de `rabbitmqctl list_users` traz `user` e `tags`, nada de senha). O resultado observado
    fica registrado na research (Observações da integração real); se o `guest` existir E autenticar
    por rede, a regra é bloqueio humano, não correção automática.
    """
    listed = stack.dc("exec", "-T", "rabbitmq", "rabbitmqctl", "list_users", "--formatter", "json")
    assert listed.returncode == 0, stack.show(listed)
    users = {entry["user"] for entry in json.loads(listed.stdout)}
    assert stack.user in users, f"usuários do broker: {sorted(users)}"

    stack.wait_management()
    status, _ = stack.management("GET", "/api/whoami", credentials=("guest", "guest"))
    assert status == 401, (
        f"o painel aceitou guest/guest pela porta publicada (HTTP {status}; guest listado: "
        f"{'guest' in users}): bloqueio humano (research Decision 11 item 13)"
    )


# ── 6.1.4 ────────────────────────────────────────────────────────────────────────
def test_up_is_idempotent(stack: Stack):
    ids = stack.container_ids()
    started = {svc: stack.inspect(i, "{{.State.StartedAt}}") for svc, i in ids.items()}
    volumes = {v: stack.run("docker", "volume", "inspect", "--format", "{{.CreatedAt}}", v).stdout.strip()
               for v in stack.project_volumes()}

    res = stack.script("up.sh")
    assert res.returncode == 0, stack.show(res)
    assert "já estava no ar" in res.stdout, stack.show(res)
    assert stack.container_ids() == ids
    assert {svc: stack.inspect(i, "{{.State.StartedAt}}") for svc, i in ids.items()} == started

    # Caminho que o up.sh percorre com a stack parcialmente no ar: `up` sobre containers inalterados
    # não deve recriá-los (hipótese do plan, §Riscos).
    direct = stack.dc("up", "--detach", "--wait", "--wait-timeout", "120", "--pull", "missing", "--no-build")
    assert direct.returncode == 0, stack.show(direct)
    assert stack.container_ids() == ids
    assert {svc: stack.inspect(i, "{{.State.StartedAt}}") for svc, i in ids.items()} == started
    assert {v: stack.run("docker", "volume", "inspect", "--format", "{{.CreatedAt}}", v).stdout.strip()
            for v in stack.project_volumes()} == volumes


# ── 6.2.4 ────────────────────────────────────────────────────────────────────────
def test_volume_names_follow_project_prefix(stack: Stack):
    assert stack.project_volumes() == sorted([f"{stack.project}_pgdata", f"{stack.project}_rabbitmqdata"])


# ── 6.1.5 ────────────────────────────────────────────────────────────────────────
def test_data_survives_three_down_up_cycles(stack: Stack):
    marker = f"marcador-{secrets.token_hex(6)}"
    res = stack.psql(f"CREATE TABLE IF NOT EXISTS it_persist (v text); INSERT INTO it_persist VALUES ('{marker}');")
    assert res.returncode == 0, stack.show(res)

    stack.wait_management()
    assert stack.management("PUT", "/api/queues/%2F/it-persist", {"durable": True, "auto_delete": False})[0] in (201, 204)
    status, _ = stack.management("POST", "/api/exchanges/%2F/amq.default/publish", {
        "properties": {"delivery_mode": 2}, "routing_key": "it-persist", "payload": marker,
        "payload_encoding": "string",
    })
    assert status == 200

    for cycle in range(1, 4):
        down = stack.script("down.sh")
        assert down.returncode == 0, stack.show(down)
        assert stack.project_containers() == [], f"ciclo {cycle}: containers sobraram após o down"
        assert len(stack.project_volumes()) == 2, f"ciclo {cycle}: o down apagou volumes"

        up = stack.script("up.sh")
        assert up.returncode == 0, f"ciclo {cycle}\n{stack.show(up)}"

        rows = stack.psql("SELECT v FROM it_persist;")
        assert rows.returncode == 0 and marker in rows.stdout.split(), f"ciclo {cycle}: linha do Postgres ausente"

        stack.wait_management()
        status, body = stack.management("POST", "/api/queues/%2F/it-persist/get",
                                        {"count": 1, "ackmode": "ack_requeue_true", "encoding": "auto"})
        assert status == 200, f"ciclo {cycle}: fila durável ausente"
        assert marker in [m["payload"] for m in json.loads(body)], f"ciclo {cycle}: mensagem persistente ausente"


# ── fechamento: o --apagar-dados apaga de verdade ────────────────────────────────
def test_wipe_removes_volumes(stack: Stack):
    res = stack.script("down.sh", "--apagar-dados", "--sim")
    assert res.returncode == 0, stack.show(res)
    assert stack.project_containers() == []
    assert stack.project_volumes() == []
