"""Interface web local (FastAPI). Uma conversão por vez — os modelos são pesados."""
from __future__ import annotations

import io
import json
import queue
import re
import threading
import time
import uuid
import zipfile
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse

from . import __version__
from .engines import available_engines, engine_table
from .router import ENGINES, convert

STATIC = Path(__file__).parent / "static"
SAFE_NAME = re.compile(r"[^\w.\- ]+", re.U)


def _safe_stem(filename: str) -> str:
    stem = Path(filename or "documento.pdf").stem
    return (SAFE_NAME.sub("_", stem).strip() or "documento")[:150]


class JobStore:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.jobs: dict[str, dict] = {}
        self.queue: queue.Queue[str] = queue.Queue()
        self._load()
        threading.Thread(target=self._worker, daemon=True).start()

    def _load(self) -> None:
        for meta in self.root.glob("*/job.json"):
            try:
                job = json.loads(meta.read_text(encoding="utf-8"))
            except Exception:
                continue
            if job.get("status") in ("queued", "running"):
                job.update(status="error", error="Interrompido (o servidor foi reiniciado).")
            self.jobs[job["id"]] = job

    def _save(self, job: dict) -> None:
        d = self.root / job["id"]
        tmp = d / "job.json.tmp"
        tmp.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(d / "job.json")

    def update(self, job_id: str, **fields) -> None:
        with self.lock:
            job = self.jobs[job_id]
            job.update(fields)
            self._save(job)

    def add(self, filename: str, data: bytes, engine: str) -> dict:
        job_id = uuid.uuid4().hex[:12]
        d = self.root / job_id
        d.mkdir()
        (d / "source.pdf").write_bytes(data)
        job = {
            "id": job_id, "filename": filename, "stem": _safe_stem(filename),
            "size": len(data), "engine_requested": engine, "status": "queued",
            "stage": "Na fila", "created_at": time.time(),
        }
        with self.lock:
            self.jobs[job_id] = job
            self._save(job)
        self.queue.put(job_id)
        return job

    def _worker(self) -> None:
        while True:
            job_id = self.queue.get()
            job = self.jobs.get(job_id)
            if not job:
                continue
            d = self.root / job_id
            self.update(job_id, status="running", stage="Iniciando", started_at=time.time())
            try:
                result = convert(
                    d / "source.pdf", engine=job["engine_requested"],
                    progress=lambda msg: self.update(job_id, stage=msg),
                )
                (d / "result.md").write_text(result.markdown, encoding="utf-8")
                report = result.report()
                last_q = next((a["quality"] for a in reversed(report["attempts"]) if a["quality"]), None)
                self.update(
                    job_id, status="done", stage=f"Concluído com {result.engine}",
                    engine=result.engine, analysis=report["analysis"],
                    attempts=report["attempts"], quality=last_q, finished_at=time.time(),
                )
            except Exception as exc:
                self.update(job_id, status="error", stage="Falha", error=str(exc)[:800],
                            finished_at=time.time())

    def get(self, job_id: str) -> dict:
        job = self.jobs.get(job_id)
        if not job:
            raise HTTPException(404, "Job não encontrado")
        return job

    def delete(self, job_id: str) -> None:
        job = self.get(job_id)
        if job["status"] in ("queued", "running"):
            raise HTTPException(409, "Job em andamento")
        with self.lock:
            self.jobs.pop(job_id, None)
        for f in sorted((self.root / job_id).glob("*")):
            f.unlink()
        (self.root / job_id).rmdir()


def create_app(data_dir: Path) -> FastAPI:
    store = JobStore(data_dir / "jobs")
    app = FastAPI(title="artigo2md", version=__version__)
    app.state.store = store

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/api/info")
    def info():
        return {"version": __version__, "engines": available_engines(), "engine_table": engine_table(),
                "choices": list(ENGINES)}

    @app.post("/api/jobs")
    async def create_jobs(files: list[UploadFile] = File(...), engine: str = Form("auto")):
        if engine not in ENGINES:
            raise HTTPException(400, f"Motor inválido: {engine}")
        created = []
        for f in files:
            data = await f.read()
            if not data.startswith(b"%PDF"):
                created.append({"filename": f.filename, "status": "error", "error": "Não é um PDF."})
                continue
            created.append(store.add(f.filename or "documento.pdf", data, engine))
        return created

    @app.get("/api/jobs")
    def list_jobs():
        return sorted(store.jobs.values(), key=lambda j: j["created_at"], reverse=True)

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str):
        return store.get(job_id)

    @app.delete("/api/jobs/{job_id}")
    def delete_job(job_id: str):
        store.delete(job_id)
        return {"ok": True}

    @app.get("/api/jobs/{job_id}/markdown")
    def markdown(job_id: str, download: bool = False):
        job = store.get(job_id)
        path = store.root / job_id / "result.md"
        if job["status"] != "done" or not path.exists():
            raise HTTPException(409, "Ainda não convertido")
        if download:
            return FileResponse(path, media_type="text/markdown; charset=utf-8",
                                filename=job["stem"] + ".md")
        return PlainTextResponse(path.read_text(encoding="utf-8"))

    @app.get("/api/download.zip")
    def download_zip(ids: str = ""):
        wanted = [i for i in ids.split(",") if i] or list(store.jobs)
        buf = io.BytesIO()
        used: set[str] = set()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for job_id in wanted:
                job = store.jobs.get(job_id)
                path = store.root / job_id / "result.md"
                if not job or job["status"] != "done" or not path.exists():
                    continue
                name, n = job["stem"] + ".md", 2
                while name in used:
                    name, n = f"{job['stem']} ({n}).md", n + 1
                used.add(name)
                zf.write(path, name)
        buf.seek(0)
        return StreamingResponse(buf, media_type="application/zip",
                                 headers={"Content-Disposition": 'attachment; filename="artigo2md.zip"'})

    return app
