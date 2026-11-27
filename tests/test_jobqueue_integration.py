"""Integração do worker com o RabbitMQ REAL (Docker), só com `ARTIGO2MD_STACK=1` (tarefas 6.1 e 6.2).

Rode com:  ARTIGO2MD_STACK=1 uv run pytest tests/test_jobqueue_integration.py

Como funciona (mesmo padrão de `tests/test_stack_integration.py`):

- `stack/` é COPIADO para uma pasta temporária e só o serviço `rabbitmq` sobe (imagem já local; nada é baixado
  nem construído). O `stack/.env` de teste vive só nessa cópia, com senha gerada na hora e portas livres; o
  worker é iniciado com essa pasta como diretório de trabalho e lê porta e credenciais do arquivo (origem `arquivo`).
- `COMPOSE_PROJECT_NAME` único isola containers, rede e volume de teste de qualquer stack real do usuário.
- O worker roda como processo real (`python -m artigo2md.cli worker`), com o conversor real do roteador (motor
  `markitdown`, sem baixar modelos). Nenhum valor de credencial é impresso: tudo o que vai para uma asserção passa
  por `Rabbit.scrub`.
- Ao final, `down --volumes` apaga o que o teste criou e o módulo confere que nada sobrou.
"""
from __future__ import annotations

import http.client
import json
import os
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("ARTIGO2MD_STACK"),
    reason="usa Docker: defina ARTIGO2MD_STACK=1",
)

pika = pytest.importorskip("pika")
psutil = pytest.importorskip("psutil")

from fpdf import FPDF  # noqa: E402

from artigo2md.jobqueue import amqp, contract, topology  # noqa: E402
from artigo2md.jobqueue.topology import TopologyMismatch  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PORT_VARS = ("ARTIGO2MD_POSTGRES_PORT", "ARTIGO2MD_RABBITMQ_PORT", "ARTIGO2MD_RABBITMQ_MANAGEMENT_PORT",
             "ARTIGO2MD_API_PORT")
BIG_PAGES = 300  # ~7 s de conversão com o motor markitdown nesta máquina (medido na tarefa 6.2)
TIMEOUT = 90


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _pdf(path: Path, pages: int) -> Path:
    pdf = FPDF()
    pdf.set_font("Helvetica", size=11)
    text = ("Background. Attention-deficit/hyperactivity disorder is a common neurodevelopmental condition. "
            "Methods. We conducted a randomized trial with 120 participants. Results. The intervention reduced "
            "symptoms compared with placebo. Discussion. ")
    for i in range(pages):
        pdf.add_page()
        pdf.multi_cell(0, 6, (f"Page {i}. " + text) * 6)
    pdf.output(str(path))
    return path


class Rabbit:
    def __init__(self, work: Path, project: str, env: dict, ports: dict, user: str, password: str):
        self.work, self.project, self.env, self.ports = work, project, env, ports
        self.user, self.password = user, password
        self.port = ports["ARTIGO2MD_RABBITMQ_PORT"]
        self.mgmt_port = ports["ARTIGO2MD_RABBITMQ_MANAGEMENT_PORT"]
        self.events: dict[str, list[dict]] = {}

    def scrub(self, text: str) -> str:
        return text.replace(self.password, "***")

    def dc(self, *args: str, timeout: int = 300) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["docker", "compose", "--project-directory", str(self.work / "stack"),
             "--env-file", str(self.work / "stack" / ".env"), "-f", str(self.work / "stack" / "compose.yaml"),
             # opt-in do painel: estes testes usam a API HTTP do painel; fora de ARTIGO2MD_STACK=1 não é publicado
             "-f", str(self.work / "stack" / "compose.management.yaml"), *args],
            env=self.env, cwd=self.work, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=timeout)

    # ── AMQP ────────────────────────────────────────────────────────────────────
    def connect(self, password: str | None = None):
        params = pika.ConnectionParameters(
            host="127.0.0.1", port=self.port, virtual_host="/", heartbeat=30, connection_attempts=1,
            credentials=pika.PlainCredentials(self.user, password if password is not None else self.password))
        return pika.BlockingConnection(params)

    def publish(self, body: bytes) -> None:
        conn = self.connect()
        try:
            ch = conn.channel()
            ch.confirm_delivery()
            ch.basic_publish(topology.EXCHANGE_JOBS, topology.RK_CONVERT, body, amqp.make_properties(), mandatory=True)
        finally:
            conn.close()

    def publish_job(self, job_id: str, pdf: Path, engine: str = "markitdown", attempt: int = 1) -> bytes:
        body = contract.encode(contract.build_job(job_id, str(pdf), engine, attempt))
        self.publish(body)
        return body

    def queue_state(self, name: str) -> tuple[int, int]:
        """(mensagens prontas, consumidores) — leitura imediata por declaração passiva."""
        conn = self.connect()
        try:
            ok = conn.channel().queue_declare(name, passive=True)
            return ok.method.message_count, ok.method.consumer_count
        finally:
            conn.close()

    def get_all(self, queue: str) -> list[tuple[object, bytes]]:
        conn = self.connect()
        try:
            ch = conn.channel()
            out = []
            while True:
                method, props, body = ch.basic_get(queue, auto_ack=True)
                if method is None:
                    return out
                out.append((props, body))
        finally:
            conn.close()

    def purge_all(self) -> None:
        conn = self.connect()
        try:
            ch = conn.channel()
            for queue in (topology.QUEUE_JOBS, topology.QUEUE_RETRY, topology.QUEUE_DEAD, topology.QUEUE_EVENTS):
                ch.queue_purge(queue)
        finally:
            conn.close()
        self.events.clear()

    def drain_events(self) -> None:
        for _props, body in self.get_all(topology.QUEUE_EVENTS):
            event = json.loads(body)
            self.events.setdefault(event["job_id"], []).append(event)

    def wait_events(self, job_id: str, predicate, timeout: float = TIMEOUT) -> list[dict]:
        deadline = time.monotonic() + timeout
        while True:
            self.drain_events()
            evs = self.events.get(job_id, [])
            if predicate(evs):
                return evs
            if time.monotonic() > deadline:
                pytest.fail(f"eventos esperados não chegaram em {timeout:.0f} s: "
                            f"{[(e['type'], e.get('code'), e.get('message')) for e in evs]}")
            time.sleep(0.3)

    # ── painel de gestão ────────────────────────────────────────────────────────
    def management(self, method: str, path: str, body: dict | None = None) -> tuple[int, str]:
        import base64
        auth = base64.b64encode(f"{self.user}:{self.password}".encode()).decode()
        conn = http.client.HTTPConnection("127.0.0.1", self.mgmt_port, timeout=10)
        try:
            conn.request(method, path, body=json.dumps(body) if body is not None else None,
                         headers={"Authorization": f"Basic {auth}", "Content-Type": "application/json"})
            resp = conn.getresponse()
            return resp.status, resp.read().decode()
        finally:
            conn.close()

    def wait_ready(self, timeout: float = 120.0) -> float:
        """Espera o broker aceitar conexão AMQP com a topologia; devolve os segundos esperados."""
        started, deadline = time.monotonic(), time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                conn = self.connect()
                conn.close()
                return time.monotonic() - started
            except Exception:
                time.sleep(1)
        pytest.fail("o RabbitMQ não voltou a aceitar conexões a tempo")


class WorkerProc:
    """`artigo2md worker` como processo real, com `stack/.env` da cópia de teste (credenciais só no arquivo)."""

    def __init__(self, rabbit: Rabbit, data_dir: Path, *extra: str, workers: int = 1, env: dict | None = None):
        self.rabbit, self.data_dir = rabbit, data_dir
        data_dir.mkdir(exist_ok=True)
        base = {k: v for k, v in os.environ.items()
                if not k.startswith(("RABBITMQ_", "ARTIGO2MD_")) and k != "COMPOSE_PROJECT_NAME"}
        self.env = {**base, "PYTHONUNBUFFERED": "1", **(env or {})}
        self.args = [sys.executable, "-m", "artigo2md.cli", "worker", "--data-dir", str(data_dir),
                     "--workers", str(workers), "--min-free-memory", "0", *extra]
        self.log_path = data_dir.parent / f"worker-{uuid.uuid4().hex[:6]}.log"
        self.proc: subprocess.Popen | None = None

    def start(self) -> "WorkerProc":
        log = open(self.log_path, "wb")
        self.proc = subprocess.Popen(self.args, cwd=self.rabbit.work, env=self.env, stdout=log, stderr=log,
                                     stdin=subprocess.DEVNULL, start_new_session=True)
        log.close()
        return self

    @property
    def pid(self) -> int:
        return self.proc.pid

    def log(self) -> str:
        return self.log_path.read_text(encoding="utf-8", errors="replace")

    def wait_consuming(self, consumers: int = 1, timeout: float = TIMEOUT) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                pytest.fail(f"o worker terminou (código {self.proc.returncode}): {self.rabbit.scrub(self.log())}")
            try:
                if self.rabbit.queue_state(topology.QUEUE_JOBS)[1] >= consumers:
                    return
            except Exception:
                pass
            time.sleep(0.3)
        pytest.fail(f"o worker não começou a consumir: {self.rabbit.scrub(self.log())}")

    def descendants(self) -> list[psutil.Process]:
        try:
            return psutil.Process(self.pid).children(recursive=True)
        except psutil.NoSuchProcess:
            return []

    def stop(self, sig=signal.SIGTERM, timeout: float = 60) -> int:
        if self.proc.poll() is None:
            self.proc.send_signal(sig)
        try:
            return self.proc.wait(timeout)
        except subprocess.TimeoutExpired:
            self.kill_group()
            pytest.fail(f"o worker não encerrou em {timeout:.0f} s: {self.rabbit.scrub(self.log())}")

    def kill_group(self) -> None:
        try:
            os.killpg(self.proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        self.proc.wait(30)


@pytest.fixture(scope="module")
def rabbit(tmp_path_factory):
    if shutil.which("docker") is None or subprocess.run(
            ["docker", "info"], capture_output=True, stdin=subprocess.DEVNULL).returncode != 0:
        pytest.fail("ARTIGO2MD_STACK=1 exige o Docker instalado e em execução")
    work = tmp_path_factory.mktemp("queue-it")
    shutil.copytree(ROOT / "stack", work / "stack", ignore=shutil.ignore_patterns(".env"))
    ports = {var: _free_port() for var in PORT_VARS}
    password, user = secrets.token_urlsafe(24), "it_user"
    # O compose exige ARTIGO2MD_DATA_DIR (volume da API) mesmo quando só o RabbitMQ sobe: a interpolação vale
    # para o arquivo inteiro.
    data_dir = work / "data"
    data_dir.mkdir()
    env_text = "".join(f"{v}={p}\n" for v, p in ports.items()) + (
        f"POSTGRES_USER={user}\nPOSTGRES_PASSWORD={password}\nPOSTGRES_DB=it_db\n"
        f"RABBITMQ_DEFAULT_USER={user}\nRABBITMQ_DEFAULT_PASS={password}\n"
        f"ARTIGO2MD_DATA_DIR={data_dir}\n")
    env_file = work / "stack" / ".env"
    env_file.write_text(env_text)
    env_file.chmod(0o600)
    project = f"artigo2md-qit-{secrets.token_hex(4)}"
    env = {**os.environ, "COMPOSE_PROJECT_NAME": project}
    for key in list(env):
        if key.startswith(("ARTIGO2MD_", "RABBITMQ_")) and key != "ARTIGO2MD_STACK":
            del env[key]
    rb = Rabbit(work, project, env, ports, user, password)
    try:
        up = rb.dc("up", "-d", "--wait", "rabbitmq")
        assert up.returncode == 0, rb.scrub(f"{up.stdout[-1200:]}\n{up.stderr[-1200:]}")
        rb.wait_ready()
        conn = rb.connect()
        topology.declare(conn.channel())
        conn.close()
        yield rb
    finally:
        rb.dc("down", "--volumes", "--remove-orphans")
        left = subprocess.run(["docker", "ps", "--all", "--quiet", "--filter",
                               f"label=com.docker.compose.project={project}"],
                              capture_output=True, text=True).stdout.split()
        shutil.rmtree(work, ignore_errors=True)
        assert not left, f"containers de teste órfãos: {left}"


@pytest.fixture(autouse=True)
def clean_queues(rabbit):
    rabbit.purge_all()
    yield


@pytest.fixture
def workers():
    """Garante que nenhum worker (nem filho) sobra de um teste que falhou."""
    started: list[WorkerProc] = []
    yield started
    for w in started:
        if w.proc is not None and w.proc.poll() is None:
            w.kill_group()


@pytest.fixture
def make_worker(rabbit, workers, tmp_path):
    def make(*extra, **kw) -> WorkerProc:
        w = WorkerProc(rabbit, tmp_path / "dados", *extra, **kw)
        workers.append(w)
        return w.start()

    return make


def _result(evs):
    return any(e["type"] == "result" for e in evs)


# ══ 6.1 — fluxo feliz, retry e verificação do broker ═════════════════════════════════════════════
def test_scenario_1_happy_path_with_real_events_validated_against_the_contract(rabbit, make_worker, tmp_path, article_pdf):
    worker = make_worker("--idle-timeout", "0")
    worker.wait_consuming()
    job_id = f"it-{uuid.uuid4().hex[:8]}"
    rabbit.publish_job(job_id, article_pdf)
    evs = rabbit.wait_events(job_id, _result)
    out = tmp_path / "dados" / job_id / "artigo.md"
    assert out.read_text(encoding="utf-8").strip()
    types = [e["type"] for e in evs]
    assert types[-1] == "result" and set(types[:-1]) == {"progress"} and "progress" in types
    for event in evs:  # roundtrip real: o que o broker entregou passa no validador do contrato (SC-006)
        contract.validate_event(event)
    result = evs[-1]
    assert result["markdown_path"] == str(out.resolve()) and result["engine"] == "markitdown"
    assert [e["message"] for e in evs if e["type"] == "progress"][0] == "Carregando o conversor"
    assert rabbit.queue_state(topology.QUEUE_JOBS)[0] == 0
    assert worker.stop() == 0
    assert rabbit.password not in worker.log()


def test_scenario_3_transient_failure_returns_after_the_delay_and_ends_with_result(rabbit, make_worker, tmp_path, article_pdf):
    """Expiração por mensagem + DLX no RabbitMQ 4.4: a falha de gravação é real (pasta do job sem permissão)."""
    worker = make_worker("--retry-delay", "3", "--max-attempts", "3")
    worker.wait_consuming()
    job_id = f"it-{uuid.uuid4().hex[:8]}"
    job_dir = tmp_path / "dados" / job_id
    job_dir.mkdir()
    job_dir.chmod(0o500)  # o Markdown não pode ser gravado: `write_failed` (passageira)
    try:
        rabbit.publish_job(job_id, article_pdf)
        first = rabbit.wait_events(job_id, lambda evs: any(e["type"] == "error" for e in evs))
        error = next(e for e in first if e["type"] == "error")
        assert (error["code"], error["retryable"], error["attempt"], error["max_attempts"]) == ("write_failed", True, 1, 3)
    finally:
        job_dir.chmod(0o700)
    # a cópia espera em `artigo2md.jobs.retry` e só volta à fila de jobs depois do atraso
    assert rabbit.queue_state(topology.QUEUE_RETRY)[0] == 1
    evs = rabbit.wait_events(job_id, _result)
    result = next(e for e in evs if e["type"] == "result")
    assert result["attempt"] == 2
    waited = (datetime.fromisoformat(result["emitted_at"].replace("Z", "+00:00"))
              - datetime.fromisoformat(error["emitted_at"].replace("Z", "+00:00"))).total_seconds()
    assert waited >= 2.5, f"a nova tentativa veio só {waited:.2f} s depois (atraso configurado: 3 s)"
    assert (job_dir / "artigo.md").exists() and rabbit.queue_state(topology.QUEUE_RETRY)[0] == 0
    assert worker.stop() == 0


def test_vhost_default_and_precondition_error_with_a_divergent_topology(rabbit):
    status, body = rabbit.management("GET", "/api/vhosts")
    assert status == 200 and "/" in [v["name"] for v in json.loads(body)]  # vhost padrão `/`
    status, body = rabbit.management("GET", "/api/queues/%2F")
    assert status == 200 and {topology.QUEUE_JOBS, topology.QUEUE_RETRY, topology.QUEUE_DEAD,
                              topology.QUEUE_EVENTS} <= {q["name"] for q in json.loads(body)}
    # fila existente declarada com argumentos diferentes -> 406 PRECONDITION_FAILED, objeto citado, nada alterado
    conn = rabbit.connect()
    try:
        with pytest.raises(pika.exceptions.ChannelClosedByBroker) as info:
            conn.channel().queue_declare(topology.QUEUE_JOBS, durable=True, arguments={"x-queue-type": "quorum"})
        assert info.value.reply_code == 406 and "PRECONDITION_FAILED" in info.value.reply_text
        assert topology.QUEUE_JOBS in info.value.reply_text
        # o declarador da feature converte o erro real em `TopologyMismatch` e o canal precisa ser reaberto
        status, _ = rabbit.management("DELETE", f"/api/queues/%2F/{topology.QUEUE_EVENTS}")
        assert status == 204
        conn.channel().queue_declare(topology.QUEUE_EVENTS, durable=True, arguments={"x-queue-type": "quorum"})
        with pytest.raises(TopologyMismatch) as mismatch:
            topology.declare(conn.channel())
        assert mismatch.value.object_name == topology.QUEUE_EVENTS
        assert amqp.is_amqp_error(info.value) and not amqp.is_auth_error(info.value)
    finally:
        rabbit.management("DELETE", f"/api/queues/%2F/{topology.QUEUE_EVENTS}")
        topology.declare(conn.channel())  # restaura a topologia correta para os testes seguintes
        conn.close()


def test_worker_exits_5_on_a_divergent_topology_and_changes_nothing(rabbit, make_worker, tmp_path):
    rabbit.management("DELETE", f"/api/queues/%2F/{topology.QUEUE_EVENTS}")
    conn = rabbit.connect()
    try:
        conn.channel().queue_declare(topology.QUEUE_EVENTS, durable=True, arguments={"x-queue-type": "quorum"})
        worker = make_worker()
        assert worker.proc.wait(60) == 5
        log = rabbit.scrub(worker.log())
        assert f"A fila/exchange {topology.QUEUE_EVENTS} já existe no RabbitMQ com configuração diferente." in log
        assert "Traceback" not in log and rabbit.password not in log
        _status, body = rabbit.management("GET", f"/api/queues/%2F/{topology.QUEUE_EVENTS}")
        assert json.loads(body)["arguments"] == {"x-queue-type": "quorum"}  # nada foi alterado
    finally:
        rabbit.management("DELETE", f"/api/queues/%2F/{topology.QUEUE_EVENTS}")
        topology.declare(conn.channel())
        conn.close()


def test_refused_credentials_exit_4_and_the_real_exception_is_recognized(rabbit, make_worker):
    with pytest.raises(Exception) as info:
        rabbit.connect(password="senha-errada-do-teste")
    names = [c.__name__ for c in type(info.value).__mro__]
    assert amqp.is_auth_error(info.value), f"exceção real não reconhecida como credencial recusada: {names}"
    worker = make_worker(env={"RABBITMQ_DEFAULT_USER": rabbit.user, "RABBITMQ_DEFAULT_PASS": "senha-errada-do-teste"})
    assert worker.proc.wait(60) == 4
    log = worker.log()
    assert "O RabbitMQ recusou as credenciais." in log and "origem: ambiente" in log
    assert "senha-errada-do-teste" not in log and "Traceback" not in log


def test_scenario_4_definitive_failures_go_to_the_dead_queue_without_retry(rabbit, make_worker, tmp_path, article_pdf, not_a_pdf):
    worker = make_worker()
    worker.wait_consuming()
    ids = {name: f"it-{name}-{uuid.uuid4().hex[:6]}" for name in ("pdfinvalido", "inexistente", "motor")}
    bodies = {
        "pdfinvalido": rabbit.publish_job(ids["pdfinvalido"], not_a_pdf),
        "inexistente": rabbit.publish_job(ids["inexistente"], tmp_path / "nao-existe.pdf"),
        "motor": contract.encode({**contract.build_job(ids["motor"], str(article_pdf)), "engine": "magico"}),
    }
    rabbit.publish(bodies["motor"])
    junk = b"isto nao e json"
    rabbit.publish(junk)
    for name in ids:
        rabbit.wait_events(ids[name], lambda evs: any(e["type"] == "error" for e in evs))
    deadline = time.monotonic() + TIMEOUT
    dead: list = []
    while len(dead) < 4 and time.monotonic() < deadline:
        dead += rabbit.get_all(topology.QUEUE_DEAD)
        time.sleep(0.3)
    assert len(dead) == 4
    by_body = {body: props.headers for props, body in dead}
    assert set(by_body) == set(bodies.values()) | {junk}  # corpo original, byte a byte
    expected = {bodies["pdfinvalido"]: "invalid_pdf", bodies["inexistente"]: "pdf_not_found",
                bodies["motor"]: "invalid_engine", junk: "invalid_message"}
    for body, code in expected.items():
        headers = by_body[body]
        assert headers["x-artigo2md-code"] == code and headers["x-artigo2md-reason"]
        assert headers["x-artigo2md-failed-at"].endswith("Z") and isinstance(headers["x-artigo2md-attempts"], int)
    for name, code in (("pdfinvalido", "invalid_pdf"), ("inexistente", "pdf_not_found"), ("motor", "invalid_engine")):
        errors = [e for e in rabbit.events[ids[name]] if e["type"] == "error"]
        assert len(errors) == 1 and errors[0]["code"] == code and errors[0]["retryable"] is False
    assert rabbit.queue_state(topology.QUEUE_RETRY)[0] == 0 and rabbit.queue_state(topology.QUEUE_JOBS)[0] == 0
    assert worker.stop() == 0


def test_attempts_exhausted_go_to_the_dead_queue(rabbit, make_worker, article_pdf):
    """Motor `mineru` não instalado: falha passageira real (`conversion_failed`) em todas as tentativas."""
    worker = make_worker("--retry-delay", "1", "--max-attempts", "2")
    worker.wait_consuming()
    job_id = f"it-{uuid.uuid4().hex[:8]}"
    body = rabbit.publish_job(job_id, article_pdf, engine="mineru")
    evs = rabbit.wait_events(job_id, lambda es: sum(e["type"] == "error" for e in es) >= 2)
    errors = [e for e in evs if e["type"] == "error"]
    assert [(e["attempt"], e["retryable"], e["code"]) for e in errors] == [
        (1, True, "conversion_failed"), (2, False, "conversion_failed")]
    dead = rabbit.get_all(topology.QUEUE_DEAD)
    assert len(dead) == 1 and json.loads(dead[0][1])["attempt"] == 2 and json.loads(dead[0][1])["job_id"] == job_id
    assert dead[0][0].headers["x-artigo2md-attempts"] == 2 and body != dead[0][1]
    assert rabbit.queue_state(topology.QUEUE_RETRY)[0] == 0
    assert worker.stop() == 0


# ══ 6.2 — resiliência, encerramento e isolamento ═════════════════════════════════════════════════
@pytest.fixture
def big_pdf(tmp_path_factory):
    return _pdf(tmp_path_factory.mktemp("big") / "grande.pdf", BIG_PAGES)


def _wait_converting(rabbit, job_id):
    rabbit.wait_events(job_id, lambda evs: any(e.get("message") == "Convertendo com markitdown" for e in evs))


def test_scenario_8_sigterm_in_the_middle_of_a_conversion_without_deadline_returns_the_job(rabbit, make_worker, big_pdf):
    worker = make_worker("--shutdown-timeout", "0.5", "--idle-timeout", "0")
    worker.wait_consuming()
    job_id = f"it-{uuid.uuid4().hex[:8]}"
    rabbit.publish_job(job_id, big_pdf)
    _wait_converting(rabbit, job_id)
    children = worker.descendants()
    assert children, "o filho de conversão deveria estar vivo durante a conversão"
    assert worker.stop(timeout=30) == 0
    assert not any(p.is_running() and p.status() != psutil.STATUS_ZOMBIE for p in children)  # SC-008
    log = worker.log()
    assert "Encerrando: aguardando o job em andamento por até 0.5 s..." in log
    assert f"Job {job_id} devolvido à fila." in log and "Traceback" not in log
    assert rabbit.queue_state(topology.QUEUE_JOBS)[0] == 1  # voltou à fila, sem `ack`
    assert not any(e["type"] in ("result", "error") for e in rabbit.events.get(job_id, []))


def test_scenario_8_sigterm_with_a_generous_deadline_lets_the_job_finish(rabbit, make_worker, big_pdf, tmp_path):
    worker = make_worker("--shutdown-timeout", "120", "--idle-timeout", "0")
    worker.wait_consuming()
    job_id = f"it-{uuid.uuid4().hex[:8]}"
    rabbit.publish_job(job_id, big_pdf)
    _wait_converting(rabbit, job_id)
    children = worker.descendants()
    assert worker.stop(timeout=120) == 0
    evs = rabbit.wait_events(job_id, _result, timeout=10)
    assert (tmp_path / "dados" / job_id / "grande.md").stat().st_size > 0 and _result(evs)
    assert rabbit.queue_state(topology.QUEUE_JOBS)[0] == 0
    assert not any(p.is_running() and p.status() != psutil.STATUS_ZOMBIE for p in children)


def test_scenario_9_idle_worker_resumes_after_the_broker_restarts(rabbit, make_worker, article_pdf):
    worker = make_worker("--idle-timeout", "0")
    worker.wait_consuming()
    restart = rabbit.dc("restart", "rabbitmq", timeout=180)
    assert restart.returncode == 0, rabbit.scrub(restart.stderr[-800:])
    rabbit.wait_ready()
    started = time.monotonic()
    worker.wait_consuming(timeout=60)
    resumed = time.monotonic() - started
    assert resumed <= 60, f"consumo retomado só {resumed:.0f} s depois de o broker voltar (SC-007)"
    job_id = f"it-{uuid.uuid4().hex[:8]}"
    rabbit.publish_job(job_id, article_pdf)
    rabbit.wait_events(job_id, _result)
    log = rabbit.scrub(worker.log())
    assert "RabbitMQ inacessível em 127.0.0.1:" in log and "Nova tentativa em" in log  # aviso em português
    assert "Traceback" not in log and rabbit.password not in log
    assert worker.stop() == 0


def test_scenario_9_broker_restart_with_a_job_in_progress_loses_no_confirmed_message(rabbit, make_worker, big_pdf, tmp_path):
    worker = make_worker("--idle-timeout", "0", "--shutdown-timeout", "120")
    worker.wait_consuming()
    job_id = f"it-{uuid.uuid4().hex[:8]}"
    rabbit.publish_job(job_id, big_pdf)
    _wait_converting(rabbit, job_id)
    restart = rabbit.dc("restart", "rabbitmq", timeout=180)
    assert restart.returncode == 0, rabbit.scrub(restart.stderr[-800:])
    rabbit.wait_ready()
    evs = rabbit.wait_events(job_id, _result, timeout=180)  # o job volta e termina
    assert any(e["type"] == "result" for e in evs)
    assert (tmp_path / "dados" / job_id / "grande.md").stat().st_size > 0
    log = rabbit.scrub(worker.log())
    assert "Traceback" not in log and rabbit.password not in log
    assert worker.stop(timeout=120) == 0


def test_sc002_ten_forced_kills_in_the_middle_of_a_conversion_reprocess_every_job(rabbit, workers, tmp_path, big_pdf):
    from artigo2md.router import convert

    data = tmp_path / "dados"
    expected = convert(big_pdf, engine="markitdown").markdown.encode("utf-8")  # conversão limpa, sem o worker
    for round_ in range(10):
        job_id = f"it-kill-{round_}-{uuid.uuid4().hex[:6]}"
        victim = WorkerProc(rabbit, data, "--idle-timeout", "0")
        workers.append(victim)
        victim.start().wait_consuming()
        rabbit.publish_job(job_id, big_pdf)
        _wait_converting(rabbit, job_id)
        victim.kill_group()  # `kill -9` do worker e do filho (mesmo grupo de processos)
        assert not (data / job_id / "grande.md").exists()  # nada parcial: o arquivo final só nasce inteiro
        survivor = WorkerProc(rabbit, data, "--idle-timeout", "0")
        workers.append(survivor)
        survivor.start().wait_consuming(timeout=60)
        rabbit.wait_events(job_id, _result, timeout=180)
        out = data / job_id / "grande.md"
        assert out.read_bytes() == expected  # completo e idêntico ao de uma conversão limpa
        assert [p.name for p in (data / job_id).glob("*.md")] == ["grande.md"]
        assert survivor.stop(timeout=60) == 0
        rabbit.purge_all()


def test_scenario_11_only_loopback_connections_and_no_credential_leak(rabbit, make_worker, big_pdf, tmp_path):
    if shutil.which("lsof") is None:
        pytest.skip("lsof indisponível")
    worker = make_worker("--idle-timeout", "0")
    worker.wait_consuming()
    job_id = f"it-{uuid.uuid4().hex[:8]}"
    rabbit.publish_job(job_id, big_pdf)
    _wait_converting(rabbit, job_id)
    pids = [worker.pid] + [p.pid for p in worker.descendants()]
    out = subprocess.run(["lsof", "-a", "-i", "-n", "-P", "-p", ",".join(map(str, pids))],
                         capture_output=True, text=True).stdout
    names = [line.split()[-2] if line.rstrip().endswith(")") else line.split()[-1]
             for line in out.splitlines()[1:] if line.strip()]
    assert names, f"nenhuma conexão encontrada:\n{out}"
    for name in names:
        for endpoint in name.split("->"):
            assert endpoint.startswith("127.0.0.1:"), f"conexão fora do loopback: {name}"
    assert any(n.endswith(f"->127.0.0.1:{rabbit.port}") for n in names)
    evs = rabbit.wait_events(job_id, _result, timeout=180)
    assert worker.stop() == 0
    haystack = worker.log() + json.dumps(evs)
    assert rabbit.password not in haystack and rabbit.user not in worker.log().replace(rabbit.password, "")


def test_idle_worker_cpu_is_close_to_zero(rabbit, make_worker, article_pdf):
    worker = make_worker("--idle-timeout", "0")
    worker.wait_consuming()
    time.sleep(2)  # início concluído
    proc = psutil.Process(worker.pid)
    proc.cpu_percent(None)
    time.sleep(10)  # janela fixa de medição
    idle = proc.cpu_percent(None)
    assert idle < 5.0, f"worker ocioso usou {idle:.1f}% de uma CPU em 10 s"
    # depois de um job, com o conversor vivo e ocioso, o conjunto continua perto de zero
    job_id = f"it-{uuid.uuid4().hex[:8]}"
    rabbit.publish_job(job_id, article_pdf)
    rabbit.wait_events(job_id, _result)
    time.sleep(2)
    everything = [psutil.Process(worker.pid)] + worker.descendants()
    for p in everything:
        p.cpu_percent(None)
    time.sleep(10)
    loaded_idle = sum(p.cpu_percent(None) for p in everything)
    assert loaded_idle < 5.0, f"worker + conversor ociosos usaram {loaded_idle:.1f}% de uma CPU em 10 s"
    assert worker.stop() == 0
