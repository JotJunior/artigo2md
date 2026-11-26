#!/usr/bin/env python3
"""Medição de RAM do worker da fila (FR-015, tarefas 7.1 e 7.2 da `conversion-queue`).

Fora do pacote instalado: o pacote `artigo2md` nunca importa este script. Roda à mão, num Mac, com a infraestrutura
no ar (RabbitMQ em 127.0.0.1; porta e credenciais lidas como o worker faz: ambiente, `ARTIGO2MD_STACK_ENV` ou
`./stack/.env` do diretório de trabalho). Subcomandos:

    make-batch --dir PASTA                    gera o lote sintético (texto nativo, escaneado, tabelas) e o manifesto
    run --batch-dir PASTA --out BRUTO.json    mede o worker para cada tamanho de pool e grava a saída bruta
    report BRUTO.json --out RELATORIO.md      gera o relatório a partir da saída bruta (nenhum número digitado à mão)

Protocolo de `run`, para cada tamanho de pool N (detalhes em `research.md`, Decision 8):

1. inicia `artigo2md worker --workers N` e espera N consumidores;
2. aquecimento: publica N jobs (um por slot) e espera os N eventos finais; cada filho é amostrado antes disso
   (ocioso: primeira amostra do processo, logo após o `spawn`) e depois (carregado: primeira amostra posterior ao fim
   do aquecimento, conversor ainda vivo);
3. lote: publica todos os PDFs `--repeat` vezes e espera os eventos finais; o pico é o maior valor observado;
4. a cada 0,5 s amostra, com `psutil`, RSS e USS de cada filho (`Process.children()`), mais `available` e `total`.

USS: no macOS o `psutil` só lê o USS de outro processo com privilégio de administrador (`AccessDenied` sem ele). Nesse
caso a amostra grava `uss_mb = null` e o motivo, e acrescenta `footprint_mb`, a "pegada física" lida pelo utilitário
`/usr/bin/footprint` do próprio macOS (a mesma medida do Monitor de Atividade). A métrica usada nas derivações é
sempre informada no relatório; nenhum valor é estimado ou completado.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

MB = 1024 * 1024
SAMPLE_INTERVAL = 0.5
SCHEMA = 1
GATE_MESSAGE = "Memória livre insuficiente"
DEFAULT_IDLE_TIMEOUT = 600
METRIC_PREFERENCE = ("uss_mb", "footprint_mb", "rss_mb")


# ══ funções puras (cobertas por tests/test_measure_worker_ram.py) ═════════════════════════════════
def sample_metric(proc: dict) -> tuple[str, float] | None:
    """Métrica de memória de uma amostra de processo: USS se houver; senão a pegada física; senão o RSS."""
    for name in METRIC_PREFERENCE:
        value = proc.get(name)
        if value is not None:
            return name, float(value)
    return None


def run_metric_name(raw_run: dict) -> str:
    """Métrica única do run (a de mais alta preferência presente em TODAS as amostras de filhos)."""
    children = [p for s in raw_run["samples"] for p in s["procs"] if p["role"] == "child"]
    for name in METRIC_PREFERENCE:
        if children and all(p.get(name) is not None for p in children):
            return name
    raise ValueError("amostras de filhos sem uma métrica de memória comum")


def summarize_child(samples: list[dict], warmup_done_t: float | None, metric: str) -> dict:
    """Ocioso, carregado e pico de UM processo filho a partir das suas amostras ({t, rss_mb, ...}) em ordem de `t`."""
    if not samples:
        raise ValueError("processo sem amostras")
    values = [s[metric] for s in samples]
    loaded = None
    if warmup_done_t is not None:
        after = [s for s in samples if s["t"] >= warmup_done_t]
        loaded = after[0] if after else None
    peak = max(samples, key=lambda s: s[metric])
    return {
        "first_seen_t": samples[0]["t"],
        "last_seen_t": samples[-1]["t"],
        "samples": len(samples),
        "idle_mb": values[0],
        "loaded_mb": loaded[metric] if loaded else None,
        "peak_mb": peak[metric],
        "peak_rss_mb": max(s["rss_mb"] for s in samples),
        "idle_rss_mb": samples[0]["rss_mb"],
        "loaded_rss_mb": loaded["rss_mb"] if loaded else None,
    }


def count_helpers(raw_run: dict) -> int:
    return len({p["pid"] for s in raw_run["samples"] for p in s["procs"] if p["role"] == "helper"})


def children_by_pid(raw_run: dict, metric: str) -> dict[int, list[dict]]:
    out: dict[int, list[dict]] = {}
    for sample in raw_run["samples"]:
        for proc in sample["procs"]:
            if proc["role"] == "child" and proc.get(metric) is not None:
                out.setdefault(proc["pid"], []).append({**proc, "t": sample["t"]})
    return out


def aggregate_run(raw_run: dict) -> dict:
    """Agregados de um run: por processo filho, pico do conjunto e menor memória disponível."""
    metric = run_metric_name(raw_run)
    per_pid = children_by_pid(raw_run, metric)
    warm = raw_run.get("warmup_done_t")
    children = {pid: summarize_child(samples, warm, metric) for pid, samples in sorted(per_pid.items())}
    totals = []
    for sample in raw_run["samples"]:
        values = [p[metric] for p in sample["procs"] if p["role"] == "child" and p.get(metric) is not None]
        if values:
            totals.append(sum(values))
    available = [s["available_mb"] for s in raw_run["samples"]]
    return {
        "metric": metric,
        "children": children,
        "children_count": len(children),
        "helpers_count": count_helpers(raw_run),
        "peak_per_child_mb": max((c["peak_mb"] for c in children.values()), default=None),
        "peak_children_total_mb": max(totals, default=None),
        "min_available_mb": min(available, default=None),
        "gate_deferrals": raw_run.get("gate_deferrals", 0),
    }


def derive_min_free_memory(peak_per_child_mb: float, margin: float) -> int:
    """`min_free_memory = ceil(pico_por_conversor × margem)` (research.md, Decision 8)."""
    if peak_per_child_mb <= 0 or margin < 1:
        raise ValueError("pico deve ser > 0 e margem >= 1")
    return math.ceil(peak_per_child_mb * margin)


def derive_default_workers(total_mb: float, system_reserve_mb: float, peak_per_child_mb: float) -> int:
    """`workers_padrão = max(1, floor((total − reserva_do_sistema) / pico_por_conversor))` (Decision 8)."""
    if peak_per_child_mb <= 0 or total_mb <= 0 or system_reserve_mb < 0:
        raise ValueError("pico e total devem ser > 0 e a reserva >= 0")
    return max(1, math.floor((total_mb - system_reserve_mb) / peak_per_child_mb))


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def count_gate_deferrals(log_text: str) -> int:
    return sum(1 for line in log_text.splitlines() if GATE_MESSAGE in line)


def classify_child(cmdline: list[str]) -> str:
    """`child` = processo de conversão (`multiprocessing.spawn`); `helper` = outro descendente (por exemplo, o
    `multiprocessing.resource_tracker`, que não converte e não entra nas somas de memória do pool)."""
    text = " ".join(cmdline)
    return "child" if "multiprocessing.spawn" in text else "helper"


def parse_pools(text: str) -> list[int]:
    pools = sorted({int(part) for part in text.split(",") if part.strip()})
    if not pools or pools[0] < 1:
        raise ValueError("--pools deve listar inteiros >= 1")
    return pools


def parse_footprint_json(text: str) -> float:
    """Pegada física (MB) do primeiro processo da saída JSON de `footprint -p PID --json ARQUIVO`."""
    return json.loads(text)["processes"][0]["footprint"] / MB


# ══ lote de PDFs (sintético, sem dado de usuário) ═════════════════════════════════════════════════
PARAGRAPH = (
    "Background. Attention-deficit/hyperactivity disorder is a common neurodevelopmental condition. "
    "Methods. We conducted a randomized trial with 120 participants. Results. The intervention reduced "
    "symptoms compared with placebo. Discussion. These findings support the use of the intervention in adults. "
)
PROFILES = {"texto-nativo": "texto nativo", "escaneado": "escaneado (só imagem)", "tabelas": "com tabelas"}


def _fixed_pdf():
    from fpdf import FPDF

    pdf = FPDF()
    pdf.set_creation_date(datetime(2026, 1, 1, tzinfo=timezone.utc))  # saída reproduzível: mesmo SHA-256
    pdf.set_font("Helvetica", size=11)
    return pdf


def make_native_text(path: Path, pages: int = 12) -> None:
    pdf = _fixed_pdf()
    for page in range(pages):
        pdf.add_page()
        pdf.set_font("Helvetica", "B", 16)
        pdf.cell(0, 10, f"Section {page + 1}", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", size=11)
        pdf.multi_cell(0, 6, PARAGRAPH * 8)
    pdf.output(str(path))


def make_tables(path: Path, pages: int = 6, rows: int = 25) -> None:
    pdf = _fixed_pdf()
    for page in range(pages):
        pdf.add_page()
        pdf.cell(0, 8, f"Table {page + 1}. Baseline characteristics", new_x="LMARGIN", new_y="NEXT")
        with pdf.table() as table:
            for r in range(rows):
                row = table.row()
                for cell in (("Group", "N", "Age", "Score") if r == 0 else
                             (f"Group {r}", str(60 + r), f"{30 + r / 10:.1f}", f"{r * 3 % 17}")):
                    row.cell(cell)
        pdf.multi_cell(0, 6, PARAGRAPH * 2)
    pdf.output(str(path))


def make_scanned(path: Path, source: Path) -> None:
    """Escaneado: renderiza as páginas de um PDF de texto como imagens e remonta um PDF só de imagens."""
    import pypdfium2 as pdfium

    pdf = _fixed_pdf()
    document = pdfium.PdfDocument(str(source))
    with tempfile.TemporaryDirectory() as tmp:
        for index in range(len(document)):
            image = document[index].render(scale=1.5).to_pil().convert("L")
            png = Path(tmp) / f"p{index}.png"
            image.save(png)
            pdf.add_page()
            pdf.image(str(png), x=0, y=0, w=pdf.w, h=pdf.h)
    document.close()
    pdf.output(str(path))


def describe_pdf(path: Path, profile: str) -> dict:
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(str(path))
    pages = len(document)
    document.close()
    return {"name": path.name, "pages": pages, "profile": PROFILES.get(profile, profile),
            "sha256": sha256_of(path), "bytes": path.stat().st_size}


def make_batch(directory: Path) -> list[dict]:
    directory.mkdir(parents=True, exist_ok=True)
    native, scanned, tables = directory / "texto-nativo.pdf", directory / "escaneado.pdf", directory / "tabelas.pdf"
    make_native_text(native)
    make_tables(tables)
    source = directory / "_origem-do-escaneado.pdf"
    make_native_text(source, pages=6)
    make_scanned(scanned, source)
    source.unlink()
    manifest = [describe_pdf(native, "texto-nativo"), describe_pdf(scanned, "escaneado"),
                describe_pdf(tables, "tabelas")]
    (directory / "manifesto.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


# ══ medição (impuro: processos, broker, sistema) ══════════════════════════════════════════════════
def system_info() -> dict:
    def run(*cmd: str) -> str:
        try:
            return subprocess.run(cmd, capture_output=True, text=True, timeout=10).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return ""

    import psutil

    return {
        "hw_model": run("sysctl", "-n", "hw.model"),
        "hw_memsize_bytes": run("sysctl", "-n", "hw.memsize"),
        "sw_vers": run("sw_vers"),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "psutil": psutil.__version__,
        "total_mb": psutil.virtual_memory().total / MB,
    }


def footprint_mb(pid: int) -> float | None:
    """Pegada física do processo, em MB, pelo `footprint` do macOS (None se indisponível; nunca estimada)."""
    if shutil.which("footprint") is None:
        return None
    with tempfile.NamedTemporaryFile(suffix=".json") as out:
        done = subprocess.run(["footprint", "-p", str(pid), "--json", out.name], capture_output=True, timeout=20)
        if done.returncode != 0:
            return None
        try:
            return parse_footprint_json(Path(out.name).read_text(encoding="utf-8"))
        except (ValueError, KeyError, IndexError):
            return None


def read_process(proc, role: str) -> dict | None:
    import psutil

    try:
        info = {"pid": proc.pid, "role": role, "rss_mb": proc.memory_info().rss / MB,
                "uss_mb": None, "uss_error": None, "footprint_mb": None}
        try:
            info["uss_mb"] = proc.memory_full_info().uss / MB
        except psutil.AccessDenied:
            info["uss_error"] = "AccessDenied"
        if info["uss_mb"] is None:
            info["footprint_mb"] = footprint_mb(proc.pid)
            if info["footprint_mb"] is None and shutil.which("footprint") is not None:
                return None  # o processo acabou de terminar: amostra sem métrica é descartada, não completada
        return info
    except (psutil.NoSuchProcess, psutil.ZombieProcess):
        return None


def take_sample(worker_pid: int, t0: float) -> dict:
    import psutil

    main = psutil.Process(worker_pid)
    procs = [read_process(main, "main")]
    for child in main.children(recursive=True):
        try:
            role = classify_child(child.cmdline())
        except psutil.Error:
            role = "child"  # sem a linha de comando não dá para distinguir: conta como conversor, nunca omite
        procs.append(read_process(child, role))
    vm = psutil.virtual_memory()
    return {"t": round(time.monotonic() - t0, 3), "available_mb": vm.available / MB, "total_mb": vm.total / MB,
            "procs": [p for p in procs if p]}


class Bus:
    """Publica jobs e lê eventos do RabbitMQ local (credenciais e porta como o worker as resolve)."""

    def __init__(self):
        import pika

        from artigo2md.jobqueue import settings as queue_settings, topology

        credentials = queue_settings.resolve_credentials()
        self.pika, self.topology = pika, topology
        self.port = queue_settings.resolve_port()
        self.conn = pika.BlockingConnection(pika.ConnectionParameters(
            host="127.0.0.1", port=self.port, virtual_host="/", heartbeat=60, connection_attempts=1,
            credentials=pika.PlainCredentials(credentials.user, credentials.password)))
        self.channel = self.conn.channel()
        self.channel.confirm_delivery()
        topology.declare(self.channel)

    def purge(self) -> None:
        for queue in (self.topology.QUEUE_JOBS, self.topology.QUEUE_RETRY, self.topology.QUEUE_DEAD,
                      self.topology.QUEUE_EVENTS):
            self.channel.queue_purge(queue)

    def consumers(self) -> int:
        return self.channel.queue_declare(self.topology.QUEUE_JOBS, passive=True).method.consumer_count

    def publish(self, job_id: str, pdf: Path) -> None:
        from artigo2md.jobqueue import contract

        body = contract.encode(contract.build_job(job_id, str(pdf.resolve()), "auto", 1))
        props = self.pika.BasicProperties(content_type="application/json", delivery_mode=2)
        self.channel.basic_publish(self.topology.EXCHANGE_JOBS, self.topology.RK_CONVERT, body, props, mandatory=True)

    def drain_events(self) -> list[dict]:
        out = []
        while True:
            method, _props, body = self.channel.basic_get(self.topology.QUEUE_EVENTS, auto_ack=True)
            if method is None:
                return out
            out.append(json.loads(body))

    def close(self) -> None:
        try:
            self.conn.close()
        except Exception:
            pass


def run_pool(pool: int, batch: list[Path], repeat: int, min_free_mb: int, idle_timeout: int, job_timeout: float,
             work: Path, label: str) -> dict:
    """Mede UM tamanho de pool seguindo o protocolo do cabeçalho; devolve o run bruto."""
    bus = Bus()
    bus.purge()
    data_dir = work / f"dados-{label}"
    data_dir.mkdir(parents=True, exist_ok=True)
    log_path = work / f"worker-{label}.log"
    env = dict(os.environ)  # o worker lê credenciais e porta do mesmo ambiente / ./stack/.env
    args = [sys.executable, "-m", "artigo2md.cli", "worker", "--data-dir", str(data_dir), "--workers", str(pool),
            "--min-free-memory", str(min_free_mb), "--idle-timeout", str(idle_timeout), "--shutdown-timeout", "30"]
    t0 = time.monotonic()
    with open(log_path, "wb") as log:
        worker = subprocess.Popen(args, env=env, stdout=log, stderr=log, stdin=subprocess.DEVNULL,
                                  start_new_session=True)
    raw = {"label": label, "workers": pool, "min_free_memory_mb": min_free_mb, "idle_timeout": idle_timeout,
           "args": args[3:], "started_at": datetime.now(timezone.utc).isoformat(), "samples": [], "events": [],
           "warmup_done_t": None, "batch_done_t": None, "worker_exit_code": None, "failure": None}
    pending: set[str] = set()
    published_batch = False

    def tick() -> None:
        raw["samples"].append(take_sample(worker.pid, t0))
        for event in bus.drain_events():
            raw["events"].append({"t": round(time.monotonic() - t0, 3), "type": event["type"],
                                  "job_id": event["job_id"], "code": event.get("code"),
                                  "retryable": event.get("retryable"), "engine": event.get("engine")})
            if event["type"] in ("result", "error") and not (event["type"] == "error" and event.get("retryable")):
                pending.discard(event["job_id"])

    def wait(predicate, what: str) -> bool:
        deadline = time.monotonic() + job_timeout
        while time.monotonic() < deadline:
            if worker.poll() is not None:
                raw["failure"] = f"o worker terminou antes de {what} (código {worker.returncode})"
                return False
            started = time.monotonic()
            tick()
            if predicate():
                return True
            time.sleep(max(0.0, SAMPLE_INTERVAL - (time.monotonic() - started)))
        raw["failure"] = f"tempo esgotado esperando {what}"
        return False

    try:
        # Na execução do gate o worker pode (de propósito) manter slots frios: basta um consumidor para começar.
        expected_consumers = 1 if label.startswith("gate") else pool
        if not wait(lambda: bus.consumers() >= expected_consumers, f"{expected_consumers} consumidor(es)"):
            return raw
        warm_ids = [f"{label}-warm-{i}" for i in range(pool)]  # um por slot; no gate, quem estiver ativo os atende
        pending.update(warm_ids)
        for i, job_id in enumerate(warm_ids):
            bus.publish(job_id, batch[i % len(batch)])
        raw["jobs"] = {"warmup": pool, "batch": 0}
        if not wait(lambda: not pending, "o fim do aquecimento"):
            return raw
        raw["warmup_done_t"] = round(time.monotonic() - t0, 3)
        batch_ids = []
        for r in range(repeat):
            for index, pdf in enumerate(batch):
                batch_ids.append((f"{label}-b{r}-{index}", pdf))
        pending.update(job_id for job_id, _ in batch_ids)
        for job_id, pdf in batch_ids:
            bus.publish(job_id, pdf)
        published_batch = True
        raw["jobs"]["batch"] = len(batch_ids)
        if not wait(lambda: not pending, "o fim do lote"):
            return raw
        raw["batch_done_t"] = round(time.monotonic() - t0, 3)
        for _ in range(4):  # algumas amostras com o conversor ainda vivo e ocioso
            tick()
            time.sleep(SAMPLE_INTERVAL)
    finally:
        if worker.poll() is None:
            worker.send_signal(signal.SIGTERM)
            try:
                worker.wait(60)
            except subprocess.TimeoutExpired:
                os.killpg(worker.pid, signal.SIGKILL)
                worker.wait(30)
        raw["worker_exit_code"] = worker.returncode
        raw["gate_deferrals"] = count_gate_deferrals(log_path.read_text(encoding="utf-8", errors="replace"))
        raw["batch_published"] = published_batch
        raw["events_summary"] = {
            "result": sum(1 for e in raw["events"] if e["type"] == "result"),
            "error": sum(1 for e in raw["events"] if e["type"] == "error"),
            "progress": sum(1 for e in raw["events"] if e["type"] == "progress"),
        }
        bus.close()
    return raw


def command_run(args: argparse.Namespace) -> int:
    batch_dir = Path(args.batch_dir).resolve()
    manifest = json.loads((batch_dir / "manifesto.json").read_text(encoding="utf-8"))
    batch = [batch_dir / item["name"] for item in manifest]
    for item, path in zip(manifest, batch):  # o lote medido é o do manifesto
        if sha256_of(path) != item["sha256"]:
            print(f"O SHA-256 de {path.name} difere do manifesto; gere o lote de novo.", file=sys.stderr)
            return 2
    pools = parse_pools(args.pools)
    measured_pools = [] if args.only_gate else pools
    work = Path(tempfile.mkdtemp(prefix="artigo2md-ram-"))
    result = {"schema": SCHEMA, "system": system_info(), "batch": manifest, "repeat": args.repeat,
              "sample_interval_s": SAMPLE_INTERVAL, "runs": []}
    try:
        for pool in measured_pools:
            print(f"== pool {pool}: medindo (min-free-memory {args.min_free_memory} MB) ==", flush=True)
            result["runs"].append(run_pool(pool, batch, args.repeat, args.min_free_memory, args.idle_timeout,
                                           args.job_timeout, work, f"pool{pool}"))
            if result["runs"][-1]["failure"]:
                print(f"   FALHA: {result['runs'][-1]['failure']}", file=sys.stderr)
        if args.gate_min_free_memory is not None:
            gate_pool = args.gate_pool or max(pools)
            print(f"== gate: pool {gate_pool} com --min-free-memory {args.gate_min_free_memory} MB ==", flush=True)
            run = run_pool(gate_pool, batch, args.repeat, args.gate_min_free_memory, args.idle_timeout,
                           args.job_timeout, work, f"gate{gate_pool}")
            run["kind"] = "gate"
            result["runs"].append(run)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Saída bruta gravada em {args.out}")
    return 1 if any(r["failure"] for r in result["runs"]) else 0


# ══ relatório (gerado da saída bruta) ═════════════════════════════════════════════════════════════
def _fmt(value: float | None, digits: int = 0) -> str:
    return "—" if value is None else f"{value:.{digits}f}"


def render_report(raw: dict) -> str:
    system, lines = raw["system"], []
    out = lines.append
    out("# Medição de RAM do worker da fila")
    out("")
    out("Gerado por `tools/measure_worker_ram.py report` a partir da saída bruta da execução; cada número abaixo")
    out("vem dessa saída, sem valor digitado à mão ou estimado. Unidades: MB = 1 MiB (1048576 bytes).")
    out("")
    out("## Máquina")
    out("")
    out(f"- Modelo (`sysctl -n hw.model`): `{system['hw_model']}`")
    out(f"- Memória total (`sysctl -n hw.memsize`): {system['hw_memsize_bytes']} bytes "
        f"({int(system['hw_memsize_bytes']) / (1 << 30):.0f} GiB); `psutil.virtual_memory().total` = "
        f"{system['total_mb']:.0f} MB")
    out(f"- Sistema (`sw_vers`): {' | '.join(part.strip() for part in system['sw_vers'].splitlines())}")
    out(f"- Python {system['python']}, psutil {system['psutil']}, {system['platform']}")
    out("")
    out("## PDFs do lote")
    out("")
    out("| Nome | Páginas | Perfil | Bytes | SHA-256 |")
    out("|------|--------:|--------|------:|---------|")
    for item in raw["batch"]:
        out(f"| `{item['name']}` | {item['pages']} | {item['profile']} | {item['bytes']} | `{item['sha256']}` |")
    out("")
    out(f"Lote repetido {raw['repeat']} vez(es) por tamanho de pool, motor `auto`. PDFs sintéticos gerados por "
        "`tools/measure_worker_ram.py make-batch`; nenhum PDF de usuário.")
    out("")
    pool_runs = [r for r in raw["runs"] if r.get("kind") != "gate"]
    gate_runs = [r for r in raw["runs"] if r.get("kind") == "gate"]
    out("## Tamanhos de pool testados")
    out("")
    out("| Pool | `--min-free-memory` (MB) | Conversores vistos | Pico por filho (MB) | Pico do conjunto de filhos (MB) "
        "| Menor `available` (MB) | Adiamentos do gate | Eventos `result`/`error` | Saída do worker |")
    out("|-----:|------------------------:|--------------:|--------------------:|-------------------------------:"
        "|-----------------------:|-------------------:|-------------------------:|----------------:|")
    aggregates = {}
    for run in pool_runs + gate_runs:
        if run.get("failure") and not run["samples"]:
            continue
        agg = aggregates[run["label"]] = aggregate_run(run)
        summary = run.get("events_summary", {})
        out(f"| {run['workers']} | {run['min_free_memory_mb']} | {agg['children_count']} | "
            f"{_fmt(agg['peak_per_child_mb'])} | {_fmt(agg['peak_children_total_mb'])} | "
            f"{_fmt(agg['min_available_mb'])} | {agg['gate_deferrals']} | "
            f"{summary.get('result', 0)}/{summary.get('error', 0)} | {run['worker_exit_code']} |")
    out("")
    out("## Consumo por processo de conversão")
    out("")
    for run in pool_runs + gate_runs:
        agg = aggregates.get(run["label"])
        if agg is None:
            continue
        title = f"Pool {run['workers']}" + (" (execução do gate)" if run.get("kind") == "gate" else "")
        out(f"### {title}")
        out("")
        out(f"Métrica de memória: `{agg['metric']}`"
            + (" (USS indisponível sem privilégio de administrador no macOS: `psutil` devolve `AccessDenied`; "
               "usada a pegada física do `footprint`)" if agg["metric"] == "footprint_mb" else "") + ".")
        out("")
        out(f"| PID | Amostras | Ocioso (1ª amostra) | Carregado (após o aquecimento) | Pico | RSS ocioso | RSS carregado | RSS pico |")
        out("|----:|---------:|--------------------:|-------------------------------:|-----:|-----------:|--------------:|---------:|")
        for pid, child in agg["children"].items():
            out(f"| {pid} | {child['samples']} | {_fmt(child['idle_mb'])} | {_fmt(child['loaded_mb'])} | "
                f"{_fmt(child['peak_mb'])} | {_fmt(child['idle_rss_mb'])} | {_fmt(child['loaded_rss_mb'])} | "
                f"{_fmt(child['peak_rss_mb'])} |")
        out("")
        if run.get("failure"):
            out(f"> Execução incompleta: {run['failure']}.")
            out("")
    out("Conversor = processo `multiprocessing.spawn` iniciado pelo `ConversionWorker`; os demais descendentes do worker "
        "(por exemplo, o `multiprocessing.resource_tracker`) ficam na saída bruta, com o papel `helper`, e não entram "
        "nas colunas acima.")
    out("")
    out("## Comportamento da proteção de RAM (gate)")
    out("")
    if gate_runs:
        for run in gate_runs:
            agg = aggregates.get(run["label"])
            if agg is None:
                continue
            out(f"- Pool {run['workers']} com `--min-free-memory {run['min_free_memory_mb']}`: "
                f"{agg['gate_deferrals']} aviso(s) \"{GATE_MESSAGE}\" no log do worker; "
                f"{agg['children_count']} processo(s) de conversão iniciado(s); "
                f"eventos finais `result`/`error`: {run.get('events_summary', {}).get('result', 0)}/"
                f"{run.get('events_summary', {}).get('error', 0)}.")
    else:
        out("Nenhuma execução com o gate forçado nesta saída bruta.")
    out("")
    out("## Memória disponível ao longo do lote")
    out("")
    out("| Pool | Início do run (MB) | Mínimo (MB) | Fim do run (MB) |")
    out("|-----:|-------------------:|------------:|----------------:|")
    for run in pool_runs + gate_runs:
        if not run["samples"]:
            continue
        avail = [s["available_mb"] for s in run["samples"]]
        out(f"| {run['workers']}{' (gate)' if run.get('kind') == 'gate' else ''} | {avail[0]:.0f} | "
            f"{min(avail):.0f} | {avail[-1]:.0f} |")
    out("")
    return "\n".join(lines) + "\n"


def merge_raw(raws: list[dict]) -> dict:
    """Reúne os runs de várias saídas brutas do MESMO lote e da mesma máquina (nada é recalculado)."""
    merged = json.loads(json.dumps(raws[0]))
    for other in raws[1:]:
        if other["batch"] != merged["batch"] or other["repeat"] != merged["repeat"]:
            raise ValueError("as saídas brutas são de lotes ou repetições diferentes")
        if other["system"]["hw_model"] != merged["system"]["hw_model"]:
            raise ValueError("as saídas brutas são de máquinas diferentes")
        merged["runs"].extend(other["runs"])
    return merged


# ── derivação dos padrões (regra da Decision 8; nenhum número digitado à mão) ──────────────────────
MARGIN_STEP = 0.05


def derive_defaults(raw: dict) -> dict:
    """Aplica a regra de derivação aos números da saída bruta.

    - `pico`: maior pico de um conversor em todos os runs (métrica do run);
    - `margem`: dispersão observada entre conversores (maior pico / menor pico), arredondada para cima em passos de
      0,05 (nunca abaixo de 1);
    - `reserva_do_sistema`: memória ocupada pelo resto do sistema no pior início observado
      (`total − menor available da primeira amostra dos runs`), arredondada para cima;
    - `min_free_memory = ceil(pico × margem)`; `workers = floor((total − reserva) / pico)`, nunca acima do maior
      pool efetivamente medido (um padrão não extrapola além da evidência) e nunca abaixo de 1.
    """
    aggregates = [(run, aggregate_run(run)) for run in raw["runs"] if run["samples"]]
    metrics = {agg["metric"] for _run, agg in aggregates}
    if len(metrics) != 1:
        raise ValueError("os runs usam métricas de memória diferentes")
    child_peaks = [child["peak_mb"] for _run, agg in aggregates for child in agg["children"].values()]
    peak, lowest = max(child_peaks), min(child_peaks)
    margin = max(1.0, math.ceil(round(peak / lowest / MARGIN_STEP, 9)) * MARGIN_STEP)
    total_mb = raw["system"]["total_mb"]
    starts = [run["samples"][0]["available_mb"] for run, _agg in aggregates]
    reserve_mb = math.ceil(total_mb - min(starts))
    pool_runs = [run for run, _agg in aggregates if run.get("kind") != "gate" and not run.get("failure")]
    largest_pool = max(run["workers"] for run in pool_runs)
    by_formula = derive_default_workers(total_mb, reserve_mb, peak)
    return {
        "metric": metrics.pop(), "peak_mb": peak, "lowest_peak_mb": lowest, "margin": round(margin, 2),
        "total_mb": total_mb, "system_reserve_mb": reserve_mb, "min_start_available_mb": min(starts),
        "min_free_memory_mb": derive_min_free_memory(peak, round(margin, 2)),
        "workers_by_formula": by_formula, "largest_measured_pool": largest_pool,
        "default_workers": min(by_formula, largest_pool),
        "pool_sizes_measured": sorted({run["workers"] for run in pool_runs}),
    }


def render_derivation(raw: dict) -> str:
    d = derive_defaults(raw)
    lines = [
        "## Derivação dos padrões de `--workers` e `--min-free-memory`",
        "",
        "Regra (`research.md`, Decision 8, com as constantes extraídas só dos números acima; `tools/measure_worker_ram.py "
        "derive` reproduz esta seção a partir da saída bruta):",
        "",
        f"- Métrica de memória: `{d['metric']}`. Pico por conversor = maior pico observado em todos os runs = "
        f"**{d['peak_mb']:.0f} MB**; menor pico de um conversor = {d['lowest_peak_mb']:.0f} MB.",
        f"- `margem` = dispersão observada entre conversores (maior pico ÷ menor pico = "
        f"{d['peak_mb'] / d['lowest_peak_mb']:.3f}), arredondada para cima em passos de {MARGIN_STEP} = **{d['margin']:.2f}**.",
        f"- `reserva_do_sistema` = memória ocupada pelo resto do sistema no pior início observado = "
        f"total ({d['total_mb']:.0f} MB) − menor `available` na primeira amostra de um run "
        f"({d['min_start_available_mb']:.0f} MB) = **{d['system_reserve_mb']} MB**.",
        f"- `min_free_memory` = ⌈{d['peak_mb']:.0f} × {d['margin']:.2f}⌉ = **{d['min_free_memory_mb']} MB**.",
        f"- `workers` pela fórmula = ⌊({d['total_mb']:.0f} − {d['system_reserve_mb']}) ÷ {d['peak_mb']:.0f}⌋ = "
        f"{d['workers_by_formula']}; tamanhos de pool efetivamente medidos: {d['pool_sizes_measured']}; o padrão não "
        f"extrapola além do maior pool medido ({d['largest_measured_pool']}): **`workers` padrão = {d['default_workers']}**.",
        "",
        "Limites da medição (nada além disto foi medido): lote sintético pequeno (ver tabela de PDFs), uma máquina, "
        "motor `auto`; PDFs maiores ou de outros perfis podem exigir mais memória, e a proteção de RAM (FR-005) "
        "continua valendo em máquinas menores que a medida. USS indisponível sem privilégio de administrador no macOS; a "
        "pegada física (`footprint`) é a métrica usada.",
        "",
        "```json",
        json.dumps({k: d[k] for k in ("metric", "peak_mb", "margin", "total_mb", "system_reserve_mb",
                                      "min_free_memory_mb", "workers_by_formula", "largest_measured_pool",
                                      "default_workers", "pool_sizes_measured")}, ensure_ascii=False, indent=2),
        "```",
        "",
    ]
    return "\n".join(lines)


def command_merge(args: argparse.Namespace) -> int:
    raw = merge_raw([json.loads(Path(path).read_text(encoding="utf-8")) for path in args.raw])
    Path(args.out).write_text(json.dumps(raw, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"Saída bruta reunida gravada em {args.out}")
    return 0


def command_derive(args: argparse.Namespace) -> int:
    raw = merge_raw([json.loads(Path(path).read_text(encoding="utf-8")) for path in args.raw])
    sys.stdout.write(render_derivation(raw))
    return 0


def command_report(args: argparse.Namespace) -> int:
    raw = merge_raw([json.loads(Path(path).read_text(encoding="utf-8")) for path in args.raw])
    text = render_report(raw)
    if args.derive:
        text += "\n" + render_derivation(raw)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"Relatório gravado em {args.out}")
    else:
        sys.stdout.write(text)
    return 0


def command_make_batch(args: argparse.Namespace) -> int:
    for item in make_batch(Path(args.dir)):
        print(f"{item['name']}: {item['pages']} páginas, {item['profile']}, sha256 {item['sha256']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    sub = parser.add_subparsers(dest="command", required=True)
    mk = sub.add_parser("make-batch", help="gera o lote sintético de PDFs e o manifesto")
    mk.add_argument("--dir", required=True)
    mk.set_defaults(func=command_make_batch)
    rn = sub.add_parser("run", help="mede o worker e grava a saída bruta (JSON)")
    rn.add_argument("--batch-dir", required=True)
    rn.add_argument("--out", required=True)
    rn.add_argument("--pools", default="1,2,3", help="tamanhos de pool, separados por vírgula (padrão: 1,2,3)")
    rn.add_argument("--repeat", type=int, default=2, help="vezes que o lote é publicado por pool (padrão: 2)")
    rn.add_argument("--min-free-memory", type=int, default=0,
                    help="MB passados ao worker nas medições de pool (0 = sem adiar; padrão: 0)")
    rn.add_argument("--idle-timeout", type=int, default=DEFAULT_IDLE_TIMEOUT)
    rn.add_argument("--job-timeout", type=float, default=1800.0, help="espera máxima por fase, em s (padrão: 1800)")
    rn.add_argument("--gate-min-free-memory", type=int, default=None,
                    help="se informado, acrescenta uma execução com este --min-free-memory para exercitar o gate")
    rn.add_argument("--gate-pool", type=int, default=None, help="pool da execução do gate (padrão: o maior)")
    rn.add_argument("--only-gate", action="store_true",
                    help="só a execução do gate (use com --gate-min-free-memory; junte as saídas no `report`)")
    rn.set_defaults(func=command_run)
    dv = sub.add_parser("derive", help="aplica a regra de derivação aos números da saída bruta")
    dv.add_argument("raw", nargs="+")
    dv.set_defaults(func=command_derive)
    mg = sub.add_parser("merge", help="reúne várias saídas brutas do mesmo lote em um arquivo")
    mg.add_argument("raw", nargs="+")
    mg.add_argument("--out", required=True)
    mg.set_defaults(func=command_merge)
    rp = sub.add_parser("report", help="gera o relatório Markdown a partir da saída bruta")
    rp.add_argument("raw", nargs="+", help="uma ou mais saídas brutas do mesmo lote (os runs são reunidos)")
    rp.add_argument("--out")
    rp.add_argument("--derive", action="store_true", help="acrescenta a seção de derivação dos padrões")
    rp.set_defaults(func=command_report)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
