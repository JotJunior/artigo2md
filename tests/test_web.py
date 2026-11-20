import io
import time
import zipfile

from fastapi.testclient import TestClient

from artigo2md.web import create_app


def _wait(client, job_id, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in ("done", "error"):
            return job
        time.sleep(0.2)
    raise TimeoutError(job_id)


def test_upload_convert_download(tmp_path, article_pdf):
    client = TestClient(create_app(tmp_path / "dados"))
    assert client.get("/").status_code == 200
    assert "docling" in client.get("/api/info").json()["engines"]

    files = [
        ("files", ("artigo.pdf", article_pdf.read_bytes(), "application/pdf")),
        ("files", ("nao-pdf.pdf", b"isto nao e pdf", "application/pdf")),
    ]
    created = client.post("/api/jobs", files=files, data={"engine": "markitdown"}).json()
    assert created[1]["status"] == "error"
    job = _wait(client, created[0]["id"])
    assert job["status"] == "done", job
    assert "randomized trial" in client.get(f"/api/jobs/{job['id']}/markdown").text

    z = zipfile.ZipFile(io.BytesIO(client.get("/api/download.zip").content))
    assert z.namelist() == ["artigo.md"]

    assert client.delete(f"/api/jobs/{job['id']}").json() == {"ok": True}
    assert client.get("/api/jobs").json() == []


def test_rejects_invalid_engine(tmp_path, article_pdf):
    client = TestClient(create_app(tmp_path / "dados"))
    r = client.post("/api/jobs", files=[("files", ("a.pdf", article_pdf.read_bytes()))],
                    data={"engine": "nada"})
    assert r.status_code == 400


def test_converter_process_unloads_when_idle(tmp_path, article_pdf):
    client = TestClient(create_app(tmp_path / "dados", idle_timeout=1))
    assert client.get("/api/info").json()["converter"]["loaded"] is False
    created = client.post("/api/jobs", files=[("files", ("a.pdf", article_pdf.read_bytes()))],
                          data={"engine": "markitdown"}).json()
    assert _wait(client, created[0]["id"])["status"] == "done"
    assert client.get("/api/info").json()["converter"]["loaded"] is True
    end = time.time() + 10
    while time.time() < end and client.get("/api/info").json()["converter"]["loaded"]:
        time.sleep(0.3)
    assert client.get("/api/info").json()["converter"]["loaded"] is False
    # e volta a funcionar depois de descarregado
    again = client.post("/api/jobs", files=[("files", ("b.pdf", article_pdf.read_bytes()))],
                        data={"engine": "markitdown"}).json()
    assert _wait(client, again[0]["id"])["status"] == "done"


def test_info_does_not_import_heavy_engines(tmp_path):
    import sys

    for mod in ("docling", "pymupdf4llm"):
        sys.modules.pop(mod, None)
    TestClient(create_app(tmp_path / "dados")).get("/api/info")
    assert "docling" not in sys.modules and "pymupdf4llm" not in sys.modules
