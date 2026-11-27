"""Verificação ponta a ponta da API com a infraestrutura REAL (fase 11 da `project-api`), só com `ARTIGO2MD_STACK=1`.

Rode com:  ARTIGO2MD_STACK=1 uv run pytest tests/test_api_integration.py

O que sobe (nada do que o usuário já tem rodando é tocado):

- um projeto Compose descartável e próprio (`launch_stack` de `test_stack_integration.py`): Postgres, RabbitMQ e a
  imagem da API RECONSTRUÍDA do código atual, com portas livres, senhas geradas na hora e pasta de dados própria
  montada em `/data` (o único volume da API);
- o worker NATIVO (`artigo2md worker`), com Docling de verdade e os modelos já baixados no cache local
  (`HF_HUB_OFFLINE=1`: nada é baixado nem enviado a serviço externo), gravando na MESMA pasta de dados.

Cada cenário abaixo cita o cenário do `quickstart.md`. Os números medidos (latência SSE, vazão) são impressos com
`-s` e registrados no quickstart: nenhum limiar é inventado aqui além do da decisão 1.1.1 (2 s em 95%).

Os testes compartilham a mesma infraestrutura e rodam na ordem do arquivo. Sem a variável, tudo é pulado e
`uv run pytest` passa sem Docker.
"""
from __future__ import annotations

import http.client
import json
import os
import socket
import statistics
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import pytest

from artigo2md.jobqueue import schema
from conftest import _article
from test_api_contract_coverage import assert_matches_representation
from test_stack_integration import Stack, launch_stack

pytestmark = pytest.mark.skipif(
    not os.environ.get("ARTIGO2MD_STACK"),
    reason="usa Docker, Postgres, RabbitMQ e o Docling real: defina ARTIGO2MD_STACK=1",
)

CONVERSION_DEADLINE = 300.0  # segundos para uma conversão real terminar (modelos já em cache)
SSE_LATENCY_GOAL_SECONDS = 2.0  # decisão 1.1.1 (SC-004): 2 s em 95% das medições
SSE_LATENCY_GOAL_RATIO = 0.95
EVENTS_EXCHANGE = "artigo2md.events"
JOBS_QUEUE = "artigo2md.jobs.convert"
EVENTS_QUEUE = "artigo2md.events.all"


# ── ajudantes ───────────────────────────────────────────────────────────────────
@dataclass
class Reply:
    status: int
    headers: dict[str, str]
    body: bytes

    def json(self) -> Any:
        return json.loads(self.body.decode("utf-8"))


class Api:
    """Cliente HTTP mínimo da API no ar (127.0.0.1, porta publicada do container)."""

    def __init__(self, stack: Stack) -> None:
        self.port = stack.ports["ARTIGO2MD_API_PORT"]

    def request(self, method: str, path: str, body: Any = None, *, headers: dict[str, str] | None = None,
                timeout: float = 120.0, raw: bytes | None = None) -> Reply:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=timeout)
        try:
            send = dict(headers or {})
            payload = raw
            if body is not None:
                payload = json.dumps(body).encode("utf-8")
                send.setdefault("Content-Type", "application/json")
            conn.request(method, path, body=payload, headers=send)
            response = conn.getresponse()
            return Reply(response.status, {k.lower(): v for k, v in response.getheaders()}, response.read())
        finally:
            conn.close()

    def get(self, path: str, **kwargs: Any) -> Reply:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, body: Any = None, **kwargs: Any) -> Reply:
        return self.request("POST", path, body, **kwargs)

    def ok(self, reply: Reply, status: int) -> Any:
        assert reply.status == status, f"HTTP {reply.status} (esperado {status}): {reply.body[:400]!r}"
        return reply.json() if reply.body and reply.headers.get("content-type", "").startswith("application/json") else None


class Worker:
    """Processo `artigo2md worker` do host, com Docling real, apontado para a stack de teste."""

    def __init__(self, stack: Stack, workers: int = 2) -> None:
        self.stack = stack
        self.workers = workers
        self.process: subprocess.Popen[str] | None = None
        self.log_path = stack.work / "worker.log"

    def start(self, workers: int | None = None) -> None:
        assert self.process is None or self.process.poll() is not None, "worker já está rodando"
        env = {
            **self.stack.env,
            "RABBITMQ_DEFAULT_USER": self.stack.user,
            "RABBITMQ_DEFAULT_PASS": self.stack.password,
            "ARTIGO2MD_RABBITMQ_PORT": str(self.stack.ports["ARTIGO2MD_RABBITMQ_PORT"]),
            "HF_HUB_OFFLINE": "1",  # 100% local: nada é baixado nem enviado
            "PYTHONUNBUFFERED": "1",
        }
        code = ("import sys; from artigo2md.cli import main; sys.exit(main(['worker', '--data-dir', sys.argv[1], "
                "'--workers', sys.argv[2], '--retry-delay', '3', '--idle-timeout', '120']))")
        log = open(self.log_path, "ab")
        self.process = subprocess.Popen(
            [sys.executable, "-c", code, str(self.stack.data_dir), str(workers or self.workers)],
            env=env, cwd=self.stack.work, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, text=True,
        )
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if queue_info(self.stack, JOBS_QUEUE)["consumers"] >= 1:
                return
            assert self.process.poll() is None, f"o worker terminou na partida: {self.tail()}"
            time.sleep(1)
        pytest.fail(f"o worker não registrou consumidor a tempo: {self.tail()}")

    def stop(self) -> None:
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=90)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=30)
        self.process = None

    @property
    def alive(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def tail(self, size: int = 1500) -> str:
        try:
            return self.stack._scrub(self.log_path.read_text(errors="replace")[-size:])
        except OSError:
            return ""


def wait_for(predicate: Callable[[], Any], timeout: float, interval: float = 0.5, what: str = "condição") -> Any:
    deadline = time.monotonic() + timeout
    last: Any = None
    while time.monotonic() < deadline:
        last = predicate()
        if last:
            return last
        time.sleep(interval)
    pytest.fail(f"tempo esgotado esperando: {what} (último valor: {last!r})")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_z(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)


def make_pdfs(folder: Path, count: int, prefix: str = "artigo") -> list[Path]:
    folder.mkdir(parents=True, exist_ok=True)
    return [_article(folder / f"{prefix}-{index:03d}.pdf") for index in range(count)]


def create_project(api: Api, name: str) -> dict:
    project = api.ok(api.post("/v1/projects", {"name": name}), 201)
    assert_matches_representation("Project", project)
    return project


def add_folder(api: Api, project_id: int, folder: Path, pdfs: list[Path]) -> dict:
    body = {"kind": "folder", "mode": "in_place", "path": str(folder), "documents": [str(p) for p in pdfs]}
    outcome = api.ok(api.post(f"/v1/projects/{project_id}/sources", body), 201)
    assert_matches_representation("Source", outcome["source"])
    assert outcome["documents"]["rejected"] == [], outcome["documents"]["rejected"]
    return outcome


def document_status(api: Api, document_id: int) -> dict:
    status = api.ok(api.get(f"/v1/documents/{document_id}"), 200)
    assert_matches_representation("DocumentStatus", status)
    return status


def wait_document(api: Api, document_id: int, final: str = "succeeded", timeout: float = CONVERSION_DEADLINE) -> dict:
    return wait_for(lambda: (s := document_status(api, document_id))["status"] == final and s,
                    timeout, what=f"documento {document_id} -> {final}")


def sql_rows(stack: Stack, sql: str) -> list[list[str]]:
    res = stack.psql(sql)
    assert res.returncode == 0, stack.show(res)
    return [line.split("|") for line in res.stdout.splitlines() if line.strip()]


def sql_scalar(stack: Stack, sql: str) -> str:
    return sql_rows(stack, sql)[0][0]


def publish_event(stack: Stack, routing_key: str, payload: str) -> None:
    """Publica um corpo CRU em `artigo2md.events` pelo painel do RabbitMQ (cenário 5 do quickstart)."""
    status, body = stack.management("POST", f"/api/exchanges/%2F/{EVENTS_EXCHANGE}/publish", {
        "properties": {"delivery_mode": 2, "content_type": "application/json"},
        "routing_key": routing_key, "payload": payload, "payload_encoding": "string",
    })
    assert status == 200 and json.loads(body)["routed"] is True, (status, body)


def queue_info(stack: Stack, queue: str) -> dict[str, int]:
    """Contagens EXATAS da fila (`rabbitmqctl`): as estatísticas do painel só atualizam a cada ~5 s."""
    res = stack.dc("exec", "-T", "rabbitmq", "rabbitmqctl", "list_queues", "name", "messages", "messages_unacknowledged",
                   "consumers", "--formatter", "json")
    assert res.returncode == 0, stack.show(res)
    for entry in json.loads(res.stdout):
        if entry["name"] == queue:
            return {"messages": int(entry["messages"]), "unacked": int(entry["messages_unacknowledged"]),
                    "consumers": int(entry["consumers"])}
    return {"messages": 0, "unacked": 0, "consumers": 0}


def queue_messages(stack: Stack, queue: str) -> int:
    return queue_info(stack, queue)["messages"]


def drain(stack: Stack, queue: str, timeout: float = 120.0) -> None:
    wait_for(lambda: queue_messages(stack, queue) == 0, timeout, interval=0.5, what=f"fila {queue} vazia")


def api_logs(stack: Stack) -> str:
    res = stack.dc("logs", "--no-color", "api")
    return stack._scrub(res.stdout + res.stderr)


SAMPLE_REPORT = {
    "source": "amostra.pdf",
    "engine": "docling",
    "analysis": {"pages": 1, "text_pages": 1, "scanned_pages": 0, "total_chars": 100, "table_hints": 0,
                 "formula_score": 0, "article_score": 0, "fully_scanned": False},
    "attempts": [{"engine": "docling", "seconds": 1.0, "error": None,
                  "quality": {"score": 90.0, "accepted": True, "tables_found": 0, "tables_expected": 0,
                              "chars_per_page": 100.0, "issues": []}}],
}


def synthetic_result(job_id: int, markdown_path: str, attempt: int = 1) -> str:
    message = schema.build_result(str(job_id), attempt, markdown_path, "docling", SAMPLE_REPORT)
    schema.validate_event(schema.encode(message))  # o corpo sintético É um evento válido do contrato
    return schema.encode(message).decode("utf-8")


# ── fixtures ────────────────────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def stack(tmp_path_factory: pytest.TempPathFactory):
    # com o opt-in do painel: os testes abaixo publicam/consultam pela API HTTP do painel do RabbitMQ
    with launch_stack(tmp_path_factory, rebuild_api=True, management_panel=True) as st:
        res = st.script("up.sh")
        assert res.returncode == 0, st.show(res)
        yield st


@pytest.fixture(scope="module")
def api(stack: Stack) -> Api:
    return Api(stack)


@pytest.fixture(scope="module")
def worker(stack: Stack):
    instance = Worker(stack)
    try:
        yield instance
    finally:
        instance.stop()


@pytest.fixture(autouse=True)
def services_running(request):
    """Um teste que falha no meio de parar a API/o broker não pode arrastar os seguintes: religa antes de cada teste."""
    if "stack" not in request.fixturenames:
        yield
        return
    stack = request.getfixturevalue("stack")
    table = stack.service_table()
    for service in ("rabbitmq", "api"):
        if table.get(service, ("", ""))[0] != "running":
            stack.dc("start", service)
    if table.get("api", ("", ""))[0] != "running" or table.get("rabbitmq", ("", ""))[0] != "running":
        wait_for(lambda: all(v == ("running", "healthy") for k, v in stack.service_table().items() if k in ("api", "rabbitmq")),
                 180, 2.0, "serviços religados")
    yield


@pytest.fixture(scope="module")
def pdf_dir(stack: Stack) -> Path:
    """Pasta de PDFs do host lida pelo worker nativo (a API nunca lê o disco dos PDFs)."""
    return stack.work / "pdfs"


# ═════════════════════════════════════════════════════════════════════════════════
# 11.1 — ciclo completo com worker real (SC-001; quickstart cenários 1 e 2)
# ═════════════════════════════════════════════════════════════════════════════════
def test_api_is_up_and_migrated(stack: Stack, api: Api):
    reply = api.get("/health")
    assert reply.status == 200 and reply.json()["status"] == "ok"
    status = api.ok(api.get("/v1/status"), 200)
    assert status["publisher"]["connected"] is True and status["consumer"]["connected"] is True
    assert api.ok(api.get("/v1/projects"), 200) == {"projects": []}
    tables = {row[0] for row in sql_rows(stack, "SELECT table_name FROM information_schema.tables "
                                                "WHERE table_schema = 'public'")}
    assert {"projects", "sources", "documents", "jobs", "job_results", "history_events"} <= tables


def test_full_cycle_with_real_worker(stack: Stack, api: Api, worker: Worker, pdf_dir: Path):
    """Cenário 1: projeto -> fonte -> job -> succeeded -> Markdown byte a byte (SC-001)."""
    worker.start()
    folder = pdf_dir / "ciclo"
    pdfs = make_pdfs(folder, 1, "amostra")

    project = create_project(api, "Estudos")
    outcome = add_folder(api, project["id"], folder, pdfs)
    document = outcome["documents"]["created"][0]
    assert_matches_representation_document(document)

    enqueued = api.ok(api.post(f"/v1/projects/{project['id']}/jobs", {"engine": "auto", "not_processed": True}), 202)
    assert len(enqueued["enqueued"]) == 1 and enqueued["skipped"] == [] and enqueued["failed"] == []
    job = enqueued["enqueued"][0]
    assert_matches_representation("Job", job)
    assert job["status"] == "pending"

    status = wait_document(api, document["id"])
    assert status["last_result"]["job_id"] == job["id"]
    assert_matches_representation("ResultSummary", status["last_result"])

    markdown = api.get(f"/v1/documents/{document['id']}/markdown")
    assert markdown.status == 200
    assert markdown.headers["content-type"].startswith("text/markdown")
    on_disk = (stack.data_dir / str(job["id"]) / "amostra-000.md").read_bytes()
    assert Path(status["last_result"]["markdown_path"]) == stack.data_dir / str(job["id"]) / "amostra-000.md"
    assert markdown.body == on_disk and len(on_disk) > 100, "o Markdown servido tem de ser byte a byte o arquivo do worker"

    # Resultado, tentativas e job
    result = api.ok(api.get(f"/v1/documents/{document['id']}/result"), 200)
    assert_matches_representation("Result", result["result"])
    assert result["attempts"], "o resultado traz as tentativas do motor"
    for attempt in result["attempts"]:
        assert_matches_representation("Attempt", attempt)
    final_job = api.ok(api.get(f"/v1/jobs/{job['id']}"), 200)
    assert_matches_representation("Job", final_job)
    assert final_job["status"] == "succeeded" and final_job["finished_at"] and final_job["error_summary"] is None
    assert api.ok(api.get(f"/v1/jobs/{job['id']}/result"), 200)["job_id"] == job["id"]
    assert api.ok(api.get(f"/v1/jobs/{job['id']}/attempts"), 200)["attempts"] == result["attempts"]

    # Listagem de documentos e fontes
    listed = api.ok(api.get(f"/v1/projects/{project['id']}/documents"), 200)["documents"]
    assert [d["document_id"] for d in listed] == [document["id"]]
    for item in listed:
        assert_matches_representation("DocumentStatus", item)
    sources = api.ok(api.get(f"/v1/projects/{project['id']}/sources"), 200)["sources"]
    assert [s["id"] for s in sources] == [outcome["source"]["id"]]
    assert_matches_representation("Project", api.ok(api.get(f"/v1/projects/{project['id']}"), 200))

    # Histórico (11.1.3): criação, início, tentativa e sucesso
    events = api.ok(api.get(f"/v1/projects/{project['id']}/history?limit=500"), 200)["events"]
    for event in events:
        assert_matches_representation("HistoryEvent", event)
    types = [e["event_type"] for e in events]
    for expected in ("job_created", "job_started", "attempt_recorded", "job_succeeded"):
        assert expected in types, f"{expected} ausente do histórico: {types}"
    assert types.count("job_succeeded") == 1 and types.count("job_created") == 1
    by_document = api.ok(api.get(f"/v1/documents/{document['id']}/history?limit=500"), 200)["events"]
    assert {e["document_id"] for e in by_document} == {document["id"]}
    assert "job_succeeded" in [e["event_type"] for e in by_document]


def assert_matches_representation_document(document: dict) -> None:
    assert set(document) == {"id", "project_id", "source_id", "path", "created_at"}
    assert isinstance(document["id"], int) and isinstance(document["path"], str)
    assert datetime.fromisoformat(document["created_at"]).tzinfo is not None


def test_invalid_pdf_fails_with_a_message_in_portuguese(stack: Stack, api: Api, worker: Worker, pdf_dir: Path):
    """Cenário 2: o arquivo `.pdf` que não é PDF vira job `failed` com `error_summary` em português."""
    folder = pdf_dir / "invalido"
    folder.mkdir(parents=True)
    fake = folder / "falso.pdf"
    fake.write_bytes(b"isto nao e pdf")
    project = create_project(api, "Invalidos")
    outcome = add_folder(api, project["id"], folder, [fake])
    document = outcome["documents"]["created"][0]

    api.ok(api.post(f"/v1/projects/{project['id']}/jobs", {"engine": "auto", "not_processed": True}), 202)
    status = wait_document(api, document["id"], final="failed", timeout=120)
    job = api.ok(api.get(f"/v1/jobs/{status['latest_job_id']}"), 200)
    assert_matches_representation("Job", job)
    summary = job["error_summary"]
    assert summary and "Traceback" not in summary and "Exception" not in summary
    assert any(word in summary.lower() for word in ("pdf", "arquivo", "documento")), summary
    assert status["last_result"] is None
    assert api.get(f"/v1/documents/{document['id']}/markdown").status == 404
    assert api.get(f"/v1/documents/{document['id']}/result").json()["error"]["code"] == "no_result"
    events = api.ok(api.get(f"/v1/projects/{project['id']}/history"), 200)["events"]
    assert "job_failed" in [e["event_type"] for e in events]
    assert sql_scalar(stack, f"SELECT count(*) FROM job_results WHERE job_id = {job['id']}") == "0"


def test_integration_tests_are_skipped_without_the_variable():
    """11.1.4: este módulo inteiro é pulado sem `ARTIGO2MD_STACK` (a suíte comum não precisa de Docker)."""
    env = {k: v for k, v in os.environ.items() if k != "ARTIGO2MD_STACK"}
    res = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(Path(__file__).resolve())],
        env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=120,
    )
    assert res.returncode == 0, res.stdout[-800:]
    assert "skipped" in res.stdout and "failed" not in res.stdout and "error" not in res.stdout.lower().replace("errors", "")


# ═════════════════════════════════════════════════════════════════════════════════
# 11.4 — latência SSE e vazão (SC-004; quickstart cenários 4 e 6) — só MEDEM e registram
# ═════════════════════════════════════════════════════════════════════════════════
class SseCollector:
    """Cliente SSE de teste: guarda (evento, dados, instante de chegada em UTC) de cada quadro recebido."""

    def __init__(self, api: Api, project_id: int) -> None:
        self.api = api
        self.project_id = project_id
        self.events: list[tuple[str, dict, datetime]] = []
        self.status = 0
        self.content_type = ""
        self.keepalives = 0
        self._conn: http.client.HTTPConnection | None = None
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._ready = threading.Event()

    def start(self) -> "SseCollector":
        self._thread.start()
        assert self._ready.wait(30), "o fluxo SSE não abriu a tempo"
        return self

    def _run(self) -> None:
        conn = http.client.HTTPConnection("127.0.0.1", self.api.port, timeout=300)
        self._conn = conn
        try:
            conn.request("GET", f"/v1/projects/{self.project_id}/events", headers={"Accept": "text/event-stream"})
            response = conn.getresponse()
            self.status = response.status
            self.content_type = response.getheader("content-type", "")
            self._ready.set()
            name, data = "", ""
            while True:
                line = response.fp.readline()
                if not line:
                    break
                text = line.decode("utf-8").rstrip("\r\n")
                if text.startswith(":"):
                    self.keepalives += 1
                elif text.startswith("event:"):
                    name = text[6:].strip()
                elif text.startswith("data:"):
                    data = text[5:].strip()
                elif text == "" and name:
                    self.events.append((name, json.loads(data), utc_now()))
                    name, data = "", ""
        except (OSError, ValueError, http.client.HTTPException):
            pass
        finally:
            self._ready.set()

    def stop(self) -> None:
        if self._conn is not None and self._conn.sock is not None:
            try:
                self._conn.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self._conn.close()
        self._thread.join(timeout=10)


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(fraction * (len(ordered) - 1))))]


def wait_all_final(api: Api, project_id: int, expected: int, timeout: float = CONVERSION_DEADLINE) -> list[dict]:
    def done() -> list[dict] | None:
        documents = api.ok(api.get(f"/v1/projects/{project_id}/documents"), 200)["documents"]
        if len(documents) == expected and all(d["status"] in ("succeeded", "failed", "cancelled") for d in documents):
            return documents
        return None

    return wait_for(done, timeout, interval=1.0, what=f"{expected} documentos finalizados")


def test_sse_latency_and_throughput_with_four_workers(stack: Stack, api: Api, worker: Worker, pdf_dir: Path):
    """11.4.1/11.4.2: latência `emitted_at` -> cliente SSE (meta da decisão 1.1.1) e vazão com 4 workers."""
    worker.stop()
    worker.start(workers=4)
    folder = pdf_dir / "sse"
    pdfs = make_pdfs(folder, 8, "sse")
    project = create_project(api, "Fluxo ao vivo")
    other = create_project(api, "Outro projeto")
    add_folder(api, project["id"], folder, pdfs)

    collector = SseCollector(api, project["id"]).start()
    other_collector = SseCollector(api, other["id"]).start()
    try:
        assert collector.status == 200 and collector.content_type.startswith("text/event-stream")
        started = time.monotonic()
        api.ok(api.post(f"/v1/projects/{project['id']}/jobs", {"engine": "auto", "not_processed": True}), 202)
        documents = wait_all_final(api, project["id"], 8)
        elapsed = time.monotonic() - started
        time.sleep(2)  # os últimos quadros ainda em trânsito
    finally:
        collector.stop()
        other_collector.stop()

    assert [d["status"] for d in documents] == ["succeeded"] * 8
    assert other_collector.events == [], "evento de outro projeto não pode aparecer (FR-019)"
    names = {name for name, _, _ in collector.events}
    assert {"job_state", "progress"} <= names, names
    states = [data["status"] for name, data, _ in collector.events if name == "job_state"]
    assert states.count("pending") == 8 and states.count("running") == 8 and states.count("succeeded") == 8
    latencies = []
    for name, data, arrived in collector.events:
        if name == "progress":
            assert set(data) == {"job_id", "document_id", "attempt", "message", "emitted_at"}
            latencies.append((arrived - parse_z(data["emitted_at"])).total_seconds())
    assert len(latencies) >= 8
    within = sum(1 for value in latencies if value <= SSE_LATENCY_GOAL_SECONDS) / len(latencies)
    print(f"\n[medicao 11.4.1] eventos progress={len(latencies)} latencia emitted_at->cliente SSE: "
          f"mediana={statistics.median(latencies) * 1000:.0f} ms p95={percentile(latencies, 0.95) * 1000:.0f} ms "
          f"max={max(latencies) * 1000:.0f} ms; dentro de {SSE_LATENCY_GOAL_SECONDS:.0f} s: {within:.1%} "
          f"(meta {SSE_LATENCY_GOAL_RATIO:.0%})")
    total_events = len(collector.events)
    print(f"[medicao 11.4.2] 8 PDFs reais, 4 workers: {total_events} eventos SSE em {elapsed:.1f} s "
          f"({total_events / elapsed:.2f} eventos/s de ponta a ponta; sem limiar, decisão 1.1.3)")
    assert within >= SSE_LATENCY_GOAL_RATIO, (
        f"meta da decisão 1.1.1 NÃO atendida: {within:.1%} dentro de {SSE_LATENCY_GOAL_SECONDS:.0f} s; reportar ao operador"
    )


def test_consumer_throughput_and_list_documents_latency_with_1000_documents(stack: Stack, api: Api, worker: Worker):
    """11.4.2: vazão do consumidor (fila acumulada com a API parada, depois drenada) e `list_documents` com 1000."""
    worker.stop()
    project = create_project(api, "Mil documentos")
    paths = [str(stack.work / "fantasma" / f"doc-{index:04d}.pdf") for index in range(1000)]
    body = {"kind": "folder", "mode": "in_place", "path": str(stack.work / "fantasma"), "documents": paths}
    outcome = api.ok(api.post(f"/v1/projects/{project['id']}/sources", body), 201)
    assert len(outcome["documents"]["created"]) == 1000

    samples = []
    for _ in range(15):
        began = time.perf_counter()
        reply = api.get(f"/v1/projects/{project['id']}/documents")
        samples.append(time.perf_counter() - began)
        assert reply.status == 200 and len(reply.json()["documents"]) == 1000
    print(f"\n[medicao 11.4.2] list_documents com 1000 documentos (HTTP completo, 15 medições): "
          f"mediana={statistics.median(samples) * 1000:.0f} ms p95={percentile(samples, 0.95) * 1000:.0f} ms "
          f"max={max(samples) * 1000:.0f} ms")

    # Um job `pending` (o worker está parado: a mensagem do job fica na fila) para receber os `progress`.
    job = api.ok(api.post(f"/v1/documents/{outcome['documents']['created'][0]['id']}/jobs", {"engine": "auto"}), 202)
    stack.dc("stop", "api")
    count = 1000
    try:
        began = time.perf_counter()
        for _ in range(count):
            publish_event(stack, "job.progress", json.dumps(schema.build_progress(str(job["id"]), 1, "Convertendo com docling")))
        published_in = time.perf_counter() - began
        assert queue_messages(stack, EVENTS_QUEUE) >= count
    finally:
        stack.dc("start", "api")
    first_drop = wait_for(lambda: (d := queue_messages(stack, EVENTS_QUEUE)) < count and time.perf_counter(),
                          180, interval=0.05, what="consumidor começar a drenar")
    drain(stack, EVENTS_QUEUE, 180)
    drained_in = time.perf_counter() - first_drop
    print(f"[medicao 11.4.2] consumidor sequencial: {count} eventos progress drenados em {drained_in:.1f} s "
          f"(~{count / max(drained_in, 1e-9):.0f} eventos/s; publicação de teste levou {published_in:.1f} s; sem limiar, decisão 1.1.3)")
    assert api.ok(api.get(f"/v1/jobs/{job['id']}"), 200)["status"] == "running"
    # limpeza: a mensagem do job fica sem consumidor; remove-se para não cair no worker depois
    assert stack.management("DELETE", f"/api/queues/%2F/{JOBS_QUEUE}/contents")[0] in (200, 204)


# ═════════════════════════════════════════════════════════════════════════════════
# 11.2 — resiliência (SC-005, SC-006; quickstart cenários 3, 4 e 5)
# ═════════════════════════════════════════════════════════════════════════════════
def test_scenario3_broker_down_with_1000_documents(stack: Stack, api: Api, worker: Worker, pdf_dir: Path):
    worker.stop()
    worker.start(workers=2)
    project = create_project(api, "Broker fora")
    paths = [str(stack.work / "fantasma3" / f"doc-{index:04d}.pdf") for index in range(1000)]
    body = {"kind": "folder", "mode": "in_place", "path": str(stack.work / "fantasma3"), "documents": paths}
    api.ok(api.post(f"/v1/projects/{project['id']}/sources", body), 201)
    started_at = stack.inspect(stack.container_ids()["api"], "{{.State.StartedAt}}")

    stack.dc("stop", "rabbitmq")
    try:
        assert api.get("/health").status == 200
        began = time.perf_counter()
        reply = api.post(f"/v1/projects/{project['id']}/jobs", {"engine": "auto", "not_processed": True}, timeout=900)
        took = time.perf_counter() - began
        body = api.ok(reply, 202)
        print(f"\n[medicao 11.2.1] 1000 documentos com o broker parado: HTTP 202 em {took:.1f} s")
        assert body["enqueued"] == [] and body["skipped"] == []
        assert len(body["failed"]) == 1000
        for job in (body["failed"][0], body["failed"][-1]):
            assert_matches_representation("Job", job)
        assert all(job["status"] == "failed" and job["error_summary"] for job in body["failed"])
        assert len({job["error_summary"] for job in body["failed"]}) <= 3
        summary = body["failed"][0]["error_summary"]
        assert "Traceback" not in summary and any(w in summary.lower() for w in ("fila", "broker", "publicar", "rabbit"))
        assert api.get("/health").status == 200
        assert api.ok(api.get("/v1/status"), 200)["publisher"]["connected"] is False
        assert sql_scalar(stack, "SELECT count(*) FROM jobs WHERE status = 'pending' AND published_at IS NULL") == "0"
        assert sql_scalar(stack, f"SELECT count(*) FROM jobs WHERE project_id = {project['id']} AND status = 'failed'") == "1000"
    finally:
        up = stack.dc("start", "rabbitmq")
        assert up.returncode == 0, stack.show(up)

    wait_for(lambda: stack.service_table().get("rabbitmq") == ("running", "healthy"), 120, 2.0, "rabbitmq saudável")
    stack.wait_management()
    # A API retoma sem reiniciar: o publicador e o consumidor reconectam sozinhos.
    wait_for(lambda: (s := api.ok(api.get("/v1/status"), 200))["publisher"]["connected"]
             and s["consumer"]["connected"], 180, 2.0, "API reconectada ao broker")
    assert stack.inspect(stack.container_ids()["api"], "{{.State.StartedAt}}") == started_at, "a API foi reiniciada"

    # Retomada: o worker nativo (que também perdeu a conexão) reconecta sozinho (FR-014 da conversion-queue) e um
    # novo pedido funciona de ponta a ponta, sem reiniciar nada.
    assert worker.alive, f"o worker encerrou com a queda do broker: {worker.tail(400)}"
    wait_for(lambda: queue_info(stack, JOBS_QUEUE)["consumers"] >= 1, 180, 2.0, "worker reconectado")
    folder = pdf_dir / "retomada"
    pdfs = make_pdfs(folder, 1, "retomada")
    added = add_folder(api, project["id"], folder, pdfs)
    document = added["documents"]["created"][0]
    api.ok(api.post(f"/v1/documents/{document['id']}/jobs", {"engine": "auto"}), 202)
    wait_document(api, document["id"])


def test_scenario4_api_restart_in_the_middle_of_20_documents(stack: Stack, api: Api, worker: Worker, pdf_dir: Path):
    worker.stop()
    worker.start(workers=2)
    folder = pdf_dir / "reinicio"
    pdfs = make_pdfs(folder, 20, "reinicio")
    project = create_project(api, "Reinicio")
    add_folder(api, project["id"], folder, pdfs)
    enqueued = api.ok(api.post(f"/v1/projects/{project['id']}/jobs", {"engine": "auto", "not_processed": True}), 202)
    assert len(enqueued["enqueued"]) == 20
    job_ids = sorted(job["id"] for job in enqueued["enqueued"])

    wait_for(lambda: sql_scalar(stack, f"SELECT count(*) FROM jobs WHERE project_id = {project['id']} "
                                      "AND status = 'succeeded'") != "0", CONVERSION_DEADLINE, 1.0, "primeiro job concluído")
    stack.dc("stop", "api")
    done_before = int(sql_scalar(stack, f"SELECT count(*) FROM jobs WHERE project_id = {project['id']} AND status = 'succeeded'"))
    assert done_before < 20, "a API precisa parar no MEIO da fila"

    def workers_finished() -> bool:
        info = queue_info(stack, JOBS_QUEUE)
        return info["messages"] == 0 and info["unacked"] == 0

    wait_for(workers_finished, CONVERSION_DEADLINE, 2.0, "workers terminarem a fila com a API parada")
    assert queue_messages(stack, EVENTS_QUEUE) > 0, "eventos acumulam, duráveis, enquanto a API está parada"
    started = stack.dc("start", "api")
    assert started.returncode == 0, stack.show(started)
    wait_for(lambda: api.get("/health").status == 200 if _safe(lambda: api.get("/health")) else False, 120, 1.0, "API de volta")
    documents = wait_all_final(api, project["id"], 20)
    assert [d["status"] for d in documents] == ["succeeded"] * 20

    ids = ",".join(str(i) for i in job_ids)
    assert sql_scalar(stack, f"SELECT count(*) FROM job_results WHERE job_id IN ({ids})") == "20"
    duplicated = sql_rows(stack, "SELECT job_id, event_type, count(*) FROM history_events "
                                 f"WHERE job_id IN ({ids}) AND event_type IN ('job_created', 'job_started', 'job_succeeded') "
                                 "GROUP BY 1, 2 HAVING count(*) > 1")
    assert duplicated == [], f"histórico duplicado: {duplicated}"
    mismatched = sql_rows(stack, "SELECT a.job_id FROM (SELECT job_id, count(*) AS n FROM job_attempts "
                                 f"WHERE job_id IN ({ids}) GROUP BY 1) a LEFT JOIN (SELECT job_id, count(*) AS n FROM history_events "
                                 f"WHERE event_type = 'attempt_recorded' AND job_id IN ({ids}) GROUP BY 1) h USING (job_id) "
                                 "WHERE a.n IS DISTINCT FROM h.n")
    assert mismatched == [], f"tentativas e histórico divergem: {mismatched}"
    print(f"\n[observacao 11.2.2] API parada com {done_before} de 20 jobs concluídos; ao voltar, 20 succeeded, "
          "20 resultados, nenhum histórico/tentativa duplicado")


def _safe(call: Callable[[], Any]) -> bool:
    try:
        call()
        return True
    except OSError:
        return False


def test_scenario5_duplicate_invalid_and_unknown_events(stack: Stack, api: Api, worker: Worker):
    worker.stop()
    project = create_project(api, "Eventos fora do contrato")
    fantasma = stack.work / "fantasma5"
    paths = [str(fantasma / "doc.pdf")]
    body = {"kind": "folder", "mode": "in_place", "path": str(fantasma), "documents": paths}
    document = api.ok(api.post(f"/v1/projects/{project['id']}/sources", body), 201)["documents"]["created"][0]
    job = api.ok(api.post(f"/v1/documents/{document['id']}/jobs", {"engine": "auto"}), 202)
    markdown = str(stack.data_dir / str(job["id"]) / "doc.md")

    valid = synthetic_result(job["id"], markdown)
    publish_event(stack, "job.result", valid)
    publish_event(stack, "job.result", valid)  # duplicata (at-least-once)
    publish_event(stack, "job.result", "{isto-nao-e-json")  # JSON inválido
    wrong_version = json.loads(valid)
    wrong_version["schema_version"] = 2
    publish_event(stack, "job.result", json.dumps(wrong_version))  # versão não suportada
    publish_event(stack, "job.result", synthetic_result(999999999, markdown))  # job inexistente
    drain(stack, EVENTS_QUEUE, 60)
    time.sleep(1)

    assert api.ok(api.get(f"/v1/jobs/{job['id']}"), 200)["status"] == "succeeded"
    assert sql_scalar(stack, f"SELECT count(*) FROM job_results WHERE job_id = {job['id']}") == "1"
    assert sql_scalar(stack, f"SELECT count(*) FROM history_events WHERE job_id = {job['id']} AND event_type = 'job_succeeded'") == "1"
    assert sql_scalar(stack, f"SELECT count(*) FROM job_attempts WHERE job_id = {job['id']}") == "1"
    logs = api_logs(stack)
    discards = [line for line in logs.splitlines() if "Evento descartado" in line]
    # A duplicata (at-least-once, esperada) é registrada em INFO e não aparece no nível padrão (WARNING) do container;
    # a prova dela é o banco acima: um único resultado, um único `job_succeeded`, uma única tentativa.
    assert len(discards) >= 3, discards
    assert any("fora do contrato" in line for line in discards)
    assert any("versão de esquema não suportada" in line for line in discards)
    assert any("job inexistente" in line for line in discards)
    assert "isto-nao-e-json" not in logs and "999999999" not in logs, "o corpo da mensagem nunca vai ao log"
    assert api.ok(api.get("/v1/status"), 200)["consumer"]["connected"] is True, "o consumo segue"
    # nada de resíduo na fila de eventos: a mensagem inválida não foi devolvida em loop
    assert queue_messages(stack, EVENTS_QUEUE) == 0
    assert stack.management("DELETE", f"/api/queues/%2F/{JOBS_QUEUE}/contents")[0] in (200, 204)


# ═════════════════════════════════════════════════════════════════════════════════
# 11.3 — segurança e permissões do bind mount (SC-003, SC-007; quickstart cenários 7 e 8)
# ═════════════════════════════════════════════════════════════════════════════════
def test_scenario7_origin_and_host_are_refused_before_any_effect(stack: Stack, api: Api):
    before = sql_scalar(stack, "SELECT count(*) FROM projects")
    for origin in ("https://exemplo.invalid", "null", "tauri://localhost.evil.example"):
        reply = api.post("/v1/projects", {"name": f"Intruso {origin}"}, headers={"Origin": origin})
        assert reply.status == 403 and reply.json()["error"]["code"] == "origin_not_allowed", (origin, reply.status)
    assert sql_scalar(stack, "SELECT count(*) FROM projects") == before, "nada pode ser gravado"
    assert api.request("OPTIONS", "/v1/projects", headers={"Origin": "https://exemplo.invalid",
                                                           "Access-Control-Request-Method": "POST"}).status == 403
    ok = api.request("OPTIONS", "/v1/projects", headers={"Origin": "tauri://localhost",
                                                         "Access-Control-Request-Method": "POST"})
    assert ok.status == 200 and ok.headers.get("access-control-allow-origin") == "tauri://localhost"
    assert api.get("/health", headers={"Host": "evil.example"}).status == 400
    assert api.get("/v1/projects", headers={"Host": "evil.example"}).status == 400
    assert api.get("/health", headers={"Host": f"localhost:{api.port}"}).status == 200


def test_scenario7_malicious_markdown_path_and_symlink_are_refused(stack: Stack, api: Api, worker: Worker):
    worker.stop()
    outside = stack.work / "fora-dos-dados.md"
    outside.write_text("SEGREDO-NAO-VAZAR\n", encoding="utf-8")
    (stack.data_dir / "atalho.md").symlink_to(outside)

    project = create_project(api, "Caminhos maliciosos")
    fantasma = stack.work / "fantasma7"
    paths = [str(fantasma / f"doc-{index}.pdf") for index in range(3)]
    body = {"kind": "folder", "mode": "in_place", "path": str(fantasma), "documents": paths}
    documents = api.ok(api.post(f"/v1/projects/{project['id']}/sources", body), 201)["documents"]["created"]
    jobs = [api.ok(api.post(f"/v1/documents/{doc['id']}/jobs", {"engine": "auto"}), 202) for doc in documents]
    dotdot = f"{stack.data_dir}/{jobs[0]['id']}/../../fora-dos-dados.md"
    absolute_outside = str(outside)
    through_link = str(stack.data_dir / "atalho.md")
    for job, path in zip(jobs, (dotdot, absolute_outside, through_link)):
        publish_event(stack, "job.result", synthetic_result(job["id"], path))
    drain(stack, EVENTS_QUEUE, 60)
    time.sleep(1)

    for job, document in zip(jobs[:2], documents[:2]):
        final = api.ok(api.get(f"/v1/jobs/{job['id']}"), 200)
        assert final["status"] == "failed" and final["error_summary"], final
        assert str(stack.data_dir) not in final["error_summary"] and "fora-dos-dados" not in final["error_summary"]
        assert api.get(f"/v1/documents/{document['id']}/markdown").status == 404
        assert sql_scalar(stack, f"SELECT count(*) FROM job_results WHERE job_id = {job['id']}") == "0"
    # O caminho está lexicalmente DENTRO da pasta de dados, mas o arquivo é um link para fora: a leitura é recusada.
    refused = api.get(f"/v1/documents/{documents[2]['id']}/markdown")
    assert refused.status in (403, 404) and b"SEGREDO" not in refused.body, (refused.status, refused.body[:200])
    print(f"\n[observacao 11.3.1] link simbólico dentro de /data apontando para fora: GET markdown -> "
          f"HTTP {refused.status} {refused.json()['error']['code']}")
    assert b"SEGREDO" not in api_logs(stack).encode()
    assert stack.management("DELETE", f"/api/queues/%2F/{JOBS_QUEUE}/contents")[0] in (200, 204)


def test_scenario7_container_has_a_single_mount_and_clean_modules(stack: Stack):
    ident = stack.container_ids()["api"]
    mounts = json.loads(stack.inspect(ident, "{{json .Mounts}}"))
    assert len(mounts) == 1, mounts
    assert mounts[0]["Destination"] == "/data" and mounts[0]["Type"] == "bind"
    assert Path(mounts[0]["Source"]).resolve() == stack.data_dir.resolve() or mounts[0]["Source"].endswith(str(stack.data_dir))
    check = ("import sys, artigo2md.api.app as a; a.create_app(); "
             "bad = [m for m in ('docling', 'pymupdf4llm', 'pypdfium2', 'artigo2md.router') if m in sys.modules]; "
             "print('LIMPO' if not bad else 'CARREGADO ' + ','.join(bad))")
    res = stack.dc("exec", "-T", "api", "python", "-c", check)
    assert res.returncode == 0 and "LIMPO" in res.stdout, stack.show(res)
    assert json.loads(stack.inspect(ident, "{{json .HostConfig.ReadonlyRootfs}}")) is True
    assert stack.inspect(ident, "{{.Config.User}}") == "10001:10001"


def test_scenario8_bind_mount_permissions_read_and_delete(stack: Stack, api: Api, worker: Worker, pdf_dir: Path):
    """O usuário 10001 do container lê o Markdown gravado pelo worker nativo e remove os arquivos do projeto."""
    worker.start(workers=2)
    folder = pdf_dir / "exclusao"
    pdfs = make_pdfs(folder, 1, "exclusao")
    project = create_project(api, "Para excluir")
    document = add_folder(api, project["id"], folder, pdfs)["documents"]["created"][0]
    job = api.ok(api.post(f"/v1/documents/{document['id']}/jobs", {"engine": "auto"}), 202)
    status = wait_document(api, document["id"])
    job_dir = stack.data_dir / str(job["id"])
    assert job_dir.is_dir()
    info = job_dir.stat()
    reading = api.get(f"/v1/documents/{document['id']}/markdown")
    assert reading.status == 200, (reading.status, reading.body[:300])
    assert reading.body == Path(status["last_result"]["markdown_path"]).read_bytes()
    print(f"\n[observacao 11.3.2] runtime: {subprocess.run(['docker', 'version', '--format', '{{.Server.Version}} {{.Server.Platform.Name}}'], capture_output=True, text=True).stdout.strip()}; "
          f"contexto: {subprocess.run(['docker', 'context', 'show'], capture_output=True, text=True).stdout.strip()}; "
          f"dono do diretório do job no host: uid={info.st_uid} modo={oct(info.st_mode & 0o777)}; leitura pelo container 10001: HTTP 200")

    deleted = api.request("DELETE", f"/v1/projects/{project['id']}")
    assert deleted.status == 204, (deleted.status, deleted.body[:300])
    assert not job_dir.exists(), "o diretório do job deve ser removido com o projeto"
    assert api.get(f"/v1/projects/{project['id']}").status == 404
    assert sql_scalar(stack, f"SELECT count(*) FROM jobs WHERE project_id = {project['id']}") == "0"
    assert stack.data_dir.is_dir(), "a pasta de dados em si nunca é removida"
    assert (stack.data_dir / "atalho.md").is_symlink(), "nada fora dos diretórios derivados do projeto é tocado"


def test_port_accepts_connections_only_on_loopback(stack: Stack):
    res = stack.dc("port", "api", "8000")
    assert res.returncode == 0 and res.stdout.strip() == f"127.0.0.1:{stack.ports['ARTIGO2MD_API_PORT']}", stack.show(res)
    candidates = set()
    for family, _, _, _, address in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
        if not address[0].startswith("127."):
            candidates.add(address[0])
    reached = []
    for ip in candidates:
        probe = socket.socket()
        probe.settimeout(3)
        try:
            probe.connect((ip, stack.ports["ARTIGO2MD_API_PORT"]))
            reached.append(ip)
        except OSError:
            pass
        finally:
            probe.close()
    assert reached == [], f"a porta respondeu fora do loopback: {reached}"
    print(f"\n[observacao 11.3.4] endereços não-loopback testados: {sorted(candidates) or 'nenhum (só a publicação 127.0.0.1 foi conferida)'}")
