"""Ciclo de vida da infraestrutura como o app desktop a usa, com Docker REAL (tarefa 11.6; quickstart 2 a 5 e 8).

Só roda com `ARTIGO2MD_STACK=1`:  ARTIGO2MD_STACK=1 uv run pytest tests/test_desktop_stack_integration.py

Reaproveita o arranjo isolado de `tests/test_stack_integration.py` (`launch_stack`): pasta temporária, `.env` de
teste só na cópia, `COMPOSE_PROJECT_NAME` único, portas e senhas próprias, `down --volumes` ao final. O painel de gestão
do RabbitMQ NÃO é publicado (o app desktop nunca o usa). O "pool de workers" do app é representado por um processo
`artigo2md worker` real, iniciado em grupo de processos próprio (como o app faz) e parado por SIGTERM no grupo.

Ciclo coberto: subir, roundtrip de `GET /v1/status`, iniciar worker, parar worker, parar a stack e subir de novo com os
dados preservados. A soma "parar workers" + `down.sh` tem o teto de 60 s da regra de 1.11 (SC-005).
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from test_stack_integration import Stack, launch_stack

pytestmark = pytest.mark.skipif(
    not os.environ.get("ARTIGO2MD_STACK"),
    reason="usa Docker: defina ARTIGO2MD_STACK=1",
)

ROOT = Path(__file__).resolve().parent.parent
STOP_DEADLINE = 60  # SC-005 / regra de 1.11: parar workers + down.sh
JOBS_QUEUE = "artigo2md.jobs.convert"


def _consumers(stack: Stack, queue: str) -> int:
    """Consumidores da fila, via `rabbitmqctl` dentro do container (sem painel de gestão e sem senha na linha)."""
    res = stack.dc("exec", "-T", "rabbitmq", "rabbitmqctl", "list_queues", "name", "consumers", "--formatter", "json")
    assert res.returncode == 0, stack.show(res)
    for row in json.loads(res.stdout):
        if row["name"] == queue:
            return int(row["consumers"])
    return 0


def _group_pids(pgid: int) -> list[str]:
    """PIDs vivos do grupo de processos (`pgrep -g`); vazio quando o grupo acabou."""
    res = subprocess.run(["pgrep", "-g", str(pgid)], capture_output=True, text=True, stdin=subprocess.DEVNULL)
    return res.stdout.split()


def _worker_processes(marker: str) -> list[str]:
    """Linhas de `ps` que contêm `artigo2md.cli worker` e o marcador (pasta de dados de teste)."""
    out = subprocess.run(["ps", "-axo", "pid=,command="], capture_output=True, text=True,
                         stdin=subprocess.DEVNULL).stdout
    return [line for line in out.splitlines() if "artigo2md.cli worker" in line and marker in line]


@pytest.fixture(scope="module")
def stack(tmp_path_factory: pytest.TempPathFactory):
    with launch_stack(tmp_path_factory) as st:  # sem painel de gestão
        yield st


def test_desktop_lifecycle_up_workers_down_up_preserves_data(stack: Stack, tmp_path: Path):
    pytest.importorskip("pika")  # o worker só roda com o extra `queue`
    pytest.importorskip("psutil")

    # ── subir ──────────────────────────────────────────────────────────────────
    res = stack.script("up.sh")
    assert res.returncode == 0, stack.show(res)
    assert stack.service_table() == {svc: ("running", "healthy") for svc in ("postgres", "rabbitmq", "api")}
    assert "Painel do RabbitMQ: não publicado" in res.stdout

    # ── roundtrip de GET /v1/status (cenário 8): consumer.connected booleano, a consumidora da API ligada ─────────
    code, body = stack.api_get("/v1/status")
    assert code == 200, body[:300]
    status = json.loads(body)
    assert isinstance(status["consumer"]["connected"], bool) and status["consumer"]["connected"] is True
    assert isinstance(status["publisher"]["connected"], bool)

    # dado a preservar entre as paradas (volume pgdata)
    made = stack.psql("CREATE TABLE IF NOT EXISTS lifecycle_probe (v text); INSERT INTO lifecycle_probe VALUES ('kept');")
    assert made.returncode == 0, stack.show(made)

    # ── iniciar workers (grupo de processos próprio, como o pool do app) ───────────────────────────────────────
    data_dir = stack.data_dir
    assert data_dir is not None
    log = open(tmp_path / "worker.log", "wb")
    worker = subprocess.Popen(
        [sys.executable, "-m", "artigo2md.cli", "worker", "--data-dir", str(data_dir), "--workers", "1",
         "--min-free-memory", "0"],
        cwd=stack.work,  # o worker lê porta e credenciais do stack/.env da cópia de teste
        env={k: v for k, v in os.environ.items()
             if not k.startswith(("RABBITMQ_", "ARTIGO2MD_")) and k != "COMPOSE_PROJECT_NAME"} | {"PYTHONUNBUFFERED": "1"},
        stdout=log, stderr=log, stdin=subprocess.DEVNULL, start_new_session=True,
    )
    log.close()
    try:
        deadline = time.monotonic() + 120
        while _consumers(stack, JOBS_QUEUE) < 1:
            assert worker.poll() is None, f"o worker terminou (código {worker.returncode})"
            assert time.monotonic() < deadline, "o worker não começou a consumir a fila de jobs em 120 s"
            time.sleep(0.5)

        # ── parar workers + parar a stack: a soma tem teto de 60 s ───────────────────────────────────────────
        started = time.monotonic()
        os.killpg(worker.pid, signal.SIGTERM)
        worker.wait(timeout=STOP_DEADLINE)
        worker_stopped = time.monotonic() - started
        down = stack.script("down.sh")
        total = time.monotonic() - started
    finally:
        if worker.poll() is None:  # só chega aqui numa falha: não deixa processo para trás
            try:
                os.killpg(worker.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            worker.wait(30)
    print(f"\n[11.6] parar worker: {worker_stopped:.1f} s; parar worker + down.sh: {total:.1f} s")
    assert down.returncode == 0, stack.show(down)
    assert total <= STOP_DEADLINE, f"parar workers + down.sh levou {total:.0f} s (limite {STOP_DEADLINE} s)"

    # ── nada sobrou: nem processo do worker, nem container do projeto; volumes mantidos ────────────────────────
    assert _group_pids(worker.pid) == [], "restou processo no grupo do worker"
    assert _worker_processes(str(data_dir)) == [], "restou `artigo2md worker` desta execução"
    assert stack.project_containers() == []
    assert len(stack.project_volumes()) == 2, stack.project_volumes()

    # ── subir de novo: dados preservados ───────────────────────────────────────────────────────────────────────
    res = stack.script("up.sh")
    assert res.returncode == 0, stack.show(res)
    kept = stack.psql("SELECT v FROM lifecycle_probe;")
    assert kept.returncode == 0 and kept.stdout.strip() == "kept", stack.show(kept)
    deadline = time.monotonic() + 30  # a consumidora da API reconecta ao broker logo após o `healthy`
    while True:
        code, body = stack.api_get("/v1/status")
        if code == 200 and json.loads(body)["consumer"]["connected"] is True:
            break
        assert time.monotonic() < deadline, f"a API não voltou a ligar a consumidora: {code} {body[:200]}"
        time.sleep(1)


def _cargo() -> str | None:
    return shutil.which("cargo") or (str(c) if (c := Path.home() / ".cargo" / "bin" / "cargo").exists() else None)


def test_rust_status_roundtrip_against_the_real_api(stack: Stack):
    """11.7 (quickstart, cenário 8): o teste Rust `real_stack_status_roundtrip_*` roda contra a stack de teste."""
    cargo = _cargo()
    crate = ROOT / "desktop" / "src-tauri"
    if cargo is None or not (crate / "Cargo.toml").exists():
        pytest.skip("cargo ou desktop/src-tauri indisponível")

    res = stack.script("up.sh")  # idempotente: o teste anterior deixou a stack no ar de novo
    assert res.returncode == 0, stack.show(res)

    env = {**os.environ,
           "ARTIGO2MD_STACK": "1",
           "ARTIGO2MD_STACK_POSTGRES_PORT": str(stack.ports["ARTIGO2MD_POSTGRES_PORT"]),
           "ARTIGO2MD_STACK_RABBITMQ_PORT": str(stack.ports["ARTIGO2MD_RABBITMQ_PORT"]),
           "ARTIGO2MD_STACK_API_PORT": str(stack.ports["ARTIGO2MD_API_PORT"])}
    context = subprocess.run(["docker", "context", "show"], capture_output=True, text=True,
                             stdin=subprocess.DEVNULL).stdout.strip()
    if context == "desktop-linux":  # contexto padrão do Docker Desktop: habilita a checagem do dono do socket (11.5)
        env["ARTIGO2MD_STACK_RUNTIME"] = "dockerDesktop"
    out = subprocess.run([cargo, "test", "--offline", "real_stack_status_roundtrip", "--", "--nocapture"],
                         cwd=crate, env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=900)
    text = stack._scrub(out.stdout + out.stderr)
    assert out.returncode == 0, text[-2000:]
    assert "1 passed" in text, text[-2000:]
