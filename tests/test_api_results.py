"""Resultado, Markdown, histórico e exportação (`api/routes/results.py`, `api/export.py`; tarefas 9.1 a 9.3; FR-021 a
FR-025, US4, SC-003, S-04, S-07)."""
from __future__ import annotations

import asyncio
import io
import os
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from artigo2md.api import export as export_module
from artigo2md.api.app import create_app
from artigo2md.api.settings import ApiSettings
from artigo2md.store.models import QualityRecord
from fake_store import FakeStore

HOST = "/host/data"
QUALITY = QualityRecord(87.5, True, 2, 3, 1234.5, ("tabela partida", "fórmula simplificada"))
LOW = QualityRecord(40.0, False, 0, 1, 100.0, ("pouco texto",))
RESULT_FIELDS = {"job_id", "markdown_path", "engine", "quality", "warnings", "created_at"}
QUALITY_FIELDS = {"score", "accepted", "tables_found", "tables_expected", "chars_per_page", "issues"}
EVENT_FIELDS = {"id", "project_id", "document_id", "job_id", "event_type", "occurred_at", "message"}


def run(coro):
    return asyncio.run(coro)


class World:
    def __init__(self, tmp_path: Path, *, configured: bool = True):
        env = {"ARTIGO2MD_DATA_DIR_HOST": HOST, "ARTIGO2MD_DATA_DIR_MOUNT": str(tmp_path / "data")} if configured else {}
        (tmp_path / "data").mkdir(exist_ok=True)
        self.mount = tmp_path / "data"
        self.settings = ApiSettings.from_env(env, Path("/nonexistent-cwd-for-tests"))
        self.store = FakeStore()
        self.app = create_app(self.settings, store=self.store)
        self.client = TestClient(self.app, base_url="http://127.0.0.1")

    def project(self, name="P"):
        return run(self.store.create_project(name))

    def document(self, project, path="/Users/a/x.pdf"):
        async def go():
            source = await self.store.add_source(project.id, "folder", "in_place", "/Users/a")
            return (await self.store.add_document(project.id, source.id, path))[0]

        return run(go())

    def conclude(self, document, *, name="a.md", content: bytes | None = b"# Titulo\n", engine="docling",
                 quality=QUALITY, write=True, markdown_path=None, attempts=True):
        """Cria um job concluído e (opcionalmente) grava o arquivo como o worker: `<data_dir>/<job_id>/<name>`."""

        async def go():
            job = await self.store.create_job(document.id, "auto")
            await self.store.start_job(job.id)
            if attempts:
                await self.store.record_attempt(job.id, "pymupdf", 0.5, LOW, None)
                await self.store.record_attempt(job.id, engine, 1.5, quality, None)
            host_path = markdown_path or f"{HOST}/{job.id}/{name}"
            await self.store.complete_job(job.id, host_path, engine, quality, "ok")
            return job, host_path

        job, host_path = run(go())
        if write and content is not None and markdown_path is None:
            target = self.mount / str(job.id)
            target.mkdir(exist_ok=True)
            (target / name).write_bytes(content)
        return job, host_path

    def fail(self, document):
        async def go():
            job = await self.store.create_job(document.id, "auto")
            await self.store.start_job(job.id)
            await self.store.fail_job(job.id, "motor caiu", "Falhou.")
            return job

        return run(go())


@pytest.fixture()
def world(tmp_path):
    return World(tmp_path)


def code(response):
    return response.json()["error"]["code"]


# ── 9.1 resultado ───────────────────────────────────────────────────────────────
def test_document_result_returns_result_and_attempts_of_the_current_job(world):
    document = world.document(world.project())
    job, host_path = world.conclude(document)
    response = world.client.get(f"/v1/documents/{document.id}/result")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"result", "attempts"}
    result = body["result"]
    assert set(result) == RESULT_FIELDS and set(result["quality"]) == QUALITY_FIELDS
    assert (result["job_id"], result["markdown_path"], result["engine"]) == (job.id, host_path, "docling")
    assert result["quality"]["score"] == 87.5 and result["warnings"] == result["quality"]["issues"]
    assert result["warnings"] == ["tabela partida", "fórmula simplificada"]
    assert result["created_at"].endswith("+00:00") or "T" in result["created_at"]
    assert [(a["position"], a["engine"]) for a in body["attempts"]] == [(1, "pymupdf"), (2, "docling")]
    assert body["attempts"][0]["quality"]["score"] == 40.0 and body["attempts"][0]["job_id"] == job.id


def test_document_without_result_is_404_no_result_and_unknown_is_not_found(world):
    document = world.document(world.project())
    response = world.client.get(f"/v1/documents/{document.id}/result")
    assert response.status_code == 404 and code(response) == "no_result"
    assert "ainda não tem resultado" in response.json()["error"]["message"]
    assert code(world.client.get("/v1/documents/9999/result")) == "not_found"
    assert world.client.get("/v1/documents/0/result").status_code == 400


def test_current_result_survives_a_later_failed_job(world):
    document = world.document(world.project())
    job, _ = world.conclude(document)
    failed = world.fail(document)
    body = world.client.get(f"/v1/documents/{document.id}/result").json()
    assert body["result"]["job_id"] == job.id != failed.id
    assert world.client.get(f"/v1/documents/{document.id}").json()["status"] == "failed"


def test_newer_success_replaces_the_current_result(world):
    document = world.document(world.project())
    first, _ = world.conclude(document)
    second, _ = world.conclude(document, name="b.md", engine="markitdown")
    body = world.client.get(f"/v1/documents/{document.id}/result").json()
    assert body["result"]["job_id"] == second.id != first.id and body["result"]["engine"] == "markitdown"


def test_job_result_serializes_the_result_and_refuses_jobs_without_one(world):
    document = world.document(world.project())
    job, _ = world.conclude(document)
    ok = world.client.get(f"/v1/jobs/{job.id}/result")
    assert ok.status_code == 200 and set(ok.json()) == RESULT_FIELDS and ok.json()["job_id"] == job.id
    failed = world.fail(document)
    assert code(world.client.get(f"/v1/jobs/{failed.id}/result")) == "no_result"
    assert code(world.client.get("/v1/jobs/9999/result")) == "not_found"


# ── 9.1 histórico ───────────────────────────────────────────────────────────────
def test_project_history_is_ordered_paginated_and_serialized(world):
    project = world.project()
    document = world.document(project)
    world.conclude(document)
    everything = world.client.get(f"/v1/projects/{project.id}/history").json()["events"]
    assert everything and all(set(item) == EVENT_FIELDS for item in everything)
    keys = [(item["occurred_at"], item["id"]) for item in everything]
    assert keys == sorted(keys)
    assert everything[0]["event_type"] == "project_created"
    page = world.client.get(f"/v1/projects/{project.id}/history?limit=2&offset=1").json()["events"]
    assert page == everything[1:3]
    assert world.client.get(f"/v1/projects/{project.id}/history?offset=999").json() == {"events": []}


def test_document_history_only_has_the_document_events(world):
    project = world.project()
    first, second = world.document(project, "/Users/a/1.pdf"), world.document(project, "/Users/a/2.pdf")
    job, _ = world.conclude(first)
    world.conclude(second, name="b.md")
    events = world.client.get(f"/v1/documents/{first.id}/history").json()["events"]
    assert events and {item["document_id"] for item in events} == {first.id}
    assert "job_succeeded" in [item["event_type"] for item in events]
    assert all(item["job_id"] in (job.id, None) for item in events)


@pytest.mark.parametrize("query", ["limit=0", "limit=501", "limit=-1", "limit=abc", "limit=1.5", "offset=-1",
                                   "offset=x", f"offset={2**63}"])
def test_history_refuses_bad_limit_and_offset_with_400(world, query):
    project = world.project()
    document = world.document(project)
    for path in (f"/v1/projects/{project.id}/history", f"/v1/documents/{document.id}/history"):
        response = world.client.get(f"{path}?{query}")
        assert response.status_code == 400 and code(response) == "invalid_request"


def test_history_accepts_the_limit_edges_and_unknown_resources_are_404(world):
    project = world.project()
    document = world.document(project)
    for limit in (1, 500):
        assert world.client.get(f"/v1/projects/{project.id}/history?limit={limit}").status_code == 200
    assert code(world.client.get("/v1/projects/9999/history")) == "not_found"
    assert code(world.client.get("/v1/documents/9999/history")) == "not_found"
    events = world.client.get(f"/v1/documents/{document.id}/history").json()["events"]
    assert [item["event_type"] for item in events] == ["document_added"]


# ── 9.2 Markdown ────────────────────────────────────────────────────────────────
PAYLOADS = [
    b"# T\xc3\xadtulo\n\nTexto com acentua\xc3\xa7\xc3\xa3o.\n",
    b"\xef\xbb\xbfcom BOM\r\nlinhas CRLF\r\n\r\nsem quebra final",
    b"\x00\x01 bytes que nao sao texto \xff\xfe\n",
    b"",
]


@pytest.mark.parametrize("content", PAYLOADS)
def test_markdown_is_delivered_byte_for_byte_as_the_worker_wrote_it(world, content):
    document = world.document(world.project())
    world.conclude(document, name="Relatório final.md", content=content)  # nome com espaço e acento, como o do worker
    response = world.client.get(f"/v1/documents/{document.id}/markdown")
    assert response.status_code == 200
    assert response.content == content
    assert response.headers["content-type"] == "text/markdown; charset=utf-8"


def test_markdown_without_result_is_404_no_result_without_touching_the_disk(world, monkeypatch):
    document = world.document(world.project())

    def explode(*args, **kwargs):
        raise AssertionError("o disco não pode ser acessado sem resultado")

    monkeypatch.setattr(export_module, "open_host_file", explode)
    monkeypatch.setattr("artigo2md.api.paths.open_confined", explode)
    response = world.client.get(f"/v1/documents/{document.id}/markdown")
    assert response.status_code == 404 and code(response) == "no_result"
    world.fail(document)
    assert code(world.client.get(f"/v1/documents/{document.id}/markdown")) == "no_result"
    assert code(world.client.get("/v1/documents/9999/markdown")) == "not_found"


def test_markdown_file_that_vanished_is_404_not_found(world):
    document = world.document(world.project())
    world.conclude(document, write=False)
    response = world.client.get(f"/v1/documents/{document.id}/markdown")
    assert response.status_code == 404 and code(response) == "not_found"


@pytest.mark.parametrize(
    "path",
    ["/etc/passwd", f"{HOST}/../etc/passwd", f"{HOST}/1/../../etc/passwd", f"{HOST}-outro/1/a.md", "relativo/a.md",
     f"{HOST}//1/a.md", f"{HOST}/./1/a.md", "/"],
)
def test_malicious_stored_markdown_paths_are_refused_with_403_and_never_read(world, path):
    document = world.document(world.project())
    world.conclude(document, markdown_path=path, attempts=False)
    response = world.client.get(f"/v1/documents/{document.id}/markdown")
    assert response.status_code == 403 and code(response) == "path_not_allowed"
    assert "passwd" not in response.text


def test_symlink_and_directory_and_escape_inside_the_data_dir_are_refused(world, tmp_path):
    document = world.document(world.project())
    secret = tmp_path / "segredo.md"
    secret.write_text("NAO PODE VAZAR")
    job, host_path = world.conclude(document, write=False)
    target = world.mount / str(job.id)
    target.mkdir()
    (target / "a.md").symlink_to(secret)  # link simbólico para fora da pasta de dados
    response = world.client.get(f"/v1/documents/{document.id}/markdown")
    assert response.status_code == 403 and "NAO PODE VAZAR" not in response.text
    (target / "a.md").unlink()
    (target / "a.md").mkdir()  # diretório no lugar do arquivo
    assert world.client.get(f"/v1/documents/{document.id}/markdown").status_code == 403
    (target / "a.md").rmdir()
    # diretório do job é um link para fora da pasta de dados: o caminho real do descritor sai do ponto de montagem
    outside = tmp_path / "fora"
    outside.mkdir()
    (outside / "a.md").write_text("NAO PODE VAZAR")
    target.rmdir()
    target.symlink_to(outside)
    response = world.client.get(f"/v1/documents/{document.id}/markdown")
    assert response.status_code == 403 and "NAO PODE VAZAR" not in response.text


def test_markdown_without_data_dir_configuration_is_503_data_dir_unavailable(tmp_path):
    world = World(tmp_path, configured=False)
    document = world.document(world.project())
    world.conclude(document, write=False)
    response = world.client.get(f"/v1/documents/{document.id}/markdown")
    assert response.status_code == 503 and code(response) == "data_dir_unavailable"


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignora permissões de arquivo")
def test_unreadable_markdown_is_503_data_dir_permission_denied_in_portuguese(world):
    document = world.document(world.project())
    job, _ = world.conclude(document)
    path = world.mount / str(job.id) / "a.md"
    path.chmod(0)
    try:
        response = world.client.get(f"/v1/documents/{document.id}/markdown")
    finally:
        path.chmod(0o644)
    assert response.status_code == 503 and code(response) == "data_dir_permission_denied"
    message = response.json()["error"]["message"]
    assert "permissão" in message and "root" in message and str(path) not in message


def test_markdown_module_never_calls_open_directly_on_a_stored_path():
    import ast

    for module in (export_module, __import__("artigo2md.api.routes.results", fromlist=["x"])):
        tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "open"]
        assert calls == [], module.__name__


# ── 9.3 exportação ──────────────────────────────────────────────────────────────
def read_zip(response) -> zipfile.ZipFile:
    return zipfile.ZipFile(io.BytesIO(response.content))


def project_with_results(world, count=3, *, same_name=False):
    project = world.project()
    documents = [world.document(project, f"/Users/a/{n}.pdf") for n in range(count)]
    for n, document in enumerate(documents):
        world.conclude(document, name="mesmo.md" if same_name else f"doc-{n}.md", content=f"# Doc {n}\n".encode())
    return project, documents


def test_export_returns_a_zip_with_one_md_per_document_and_the_attachment_headers(world):
    project, documents = project_with_results(world)
    response = world.client.get(f"/v1/projects/{project.id}/export")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert response.headers["content-disposition"] == f'attachment; filename="projeto-{project.id}.zip"'
    archive = read_zip(response)
    assert archive.testzip() is None
    assert archive.namelist() == ["doc-0.md", "doc-1.md", "doc-2.md"]
    assert archive.read("doc-1.md") == b"# Doc 1\n"
    assert int(response.headers["content-length"]) == len(response.content)


def test_export_names_collisions_with_the_document_id_in_document_order(world):
    project, documents = project_with_results(world, same_name=True)
    names = read_zip(world.client.get(f"/v1/projects/{project.id}/export")).namelist()
    assert names == ["mesmo.md", f"mesmo-{documents[1].id}.md", f"mesmo-{documents[2].id}.md"]


def test_export_entry_names_never_carry_directories(world):
    project = world.project()
    document = world.document(project)
    world.conclude(document, name="a.md")
    # o `last_result` aponta para um subdiretório do job: a entrada usa só o nome do arquivo
    names = read_zip(world.client.get(f"/v1/projects/{project.id}/export")).namelist()
    assert names == ["a.md"] and all("/" not in n and "\\" not in n for n in names)


def test_entry_names_are_unique_even_when_a_generated_name_collides_with_another_stem():
    items = [
        export_module.Exportable(5, "/x/a.pdf", "/h/1/a.md", None),
        export_module.Exportable(6, "/x/b.pdf", "/h/2/a.md", None),
        export_module.Exportable(7, "/x/c.pdf", "/h/3/a-6.md", None),
        export_module.Exportable(8, "/x/d.pdf", "/h/4/..md", None),
    ]
    names = export_module.entry_names(items)
    assert len(set(names)) == 4 and names[0] == "a.md" and names[1] == "a-6.md"
    assert all("/" not in n for n in names)


def test_entry_names_do_not_collide_when_compared_without_case():
    """FR-021: `Artigo.md` e `artigo.md` colidiriam ao extrair no APFS padrão (sem diferenciar maiúsculas)."""
    items = [
        export_module.Exportable(1, "/x/a.pdf", "/h/1/Artigo.md", None),
        export_module.Exportable(2, "/x/b.pdf", "/h/2/artigo.md", None),
        export_module.Exportable(3, "/x/c.pdf", "/h/3/ARTIGO.md", None),
    ]
    names = export_module.entry_names(items)
    assert names == ["Artigo.md", "artigo-2.md", "ARTIGO-3.md"]
    assert len({name.casefold() for name in names}) == 3


def test_export_only_includes_documents_with_a_current_result(world):
    project, documents = project_with_results(world, count=2)
    extra = world.document(project, "/Users/a/sem-resultado.pdf")
    world.fail(extra)
    names = read_zip(world.client.get(f"/v1/projects/{project.id}/export")).namelist()
    assert names == ["doc-0.md", "doc-1.md"]


def test_export_of_a_project_without_concluded_documents_is_404_no_result_and_unknown_is_not_found(world):
    project = world.project()
    world.document(project)
    response = world.client.get(f"/v1/projects/{project.id}/export")
    assert response.status_code == 404 and code(response) == "no_result"
    assert not world.app.state.services.export_active
    assert code(world.client.get("/v1/projects/9999/export")) == "not_found"


def test_export_omits_unreadable_files_and_lists_them_in_the_readme(world):
    project, documents = project_with_results(world)
    # remove o arquivo do 2º documento (sumiu do disco)
    status = run(world.store.list_documents(project.id))
    missing = status[1].last_result
    (world.mount / str(missing.job_id) / "doc-1.md").unlink()
    archive = read_zip(world.client.get(f"/v1/projects/{project.id}/export"))
    assert archive.namelist() == ["doc-0.md", "doc-2.md", "LEIA-ME.txt"]
    readme = archive.read("LEIA-ME.txt").decode("utf-8")
    assert "1.pdf" in readme and f"documento {documents[1].id}" in readme and "não existe mais" in readme
    assert "0.pdf" not in readme.replace("1.pdf", "")


def test_export_refuses_a_stored_path_outside_the_data_dir_in_the_readme_only(world):
    project = world.project()
    good, bad = world.document(project, "/Users/a/ok.pdf"), world.document(project, "/Users/a/ruim.pdf")
    world.conclude(good, name="ok.md")
    world.conclude(bad, markdown_path="/etc/passwd", attempts=False)
    archive = read_zip(world.client.get(f"/v1/projects/{project.id}/export"))
    assert archive.namelist() == ["ok.md", "LEIA-ME.txt"]
    assert "passwd" not in archive.read("LEIA-ME.txt").decode("utf-8") and b"root:" not in archive.read("ok.md")


def test_only_one_export_at_a_time_and_the_lock_is_released_after_the_download(world):
    project, _ = project_with_results(world, count=1)
    services = world.app.state.services
    services.export_active = True  # outra exportação em andamento
    response = world.client.get(f"/v1/projects/{project.id}/export")
    assert response.status_code == 409 and code(response) == "export_in_progress"
    assert services.export_active is True  # a recusada não libera a que está em andamento
    services.export_active = False
    assert world.client.get(f"/v1/projects/{project.id}/export").status_code == 200
    assert services.export_active is False
    assert world.client.get(f"/v1/projects/{project.id}/export").status_code == 200  # a próxima passa


def test_export_lock_stays_held_while_the_zip_is_being_delivered(tmp_path):
    world = World(tmp_path)
    project = world.project()
    # conteúdo incompressível maior que um pedaço de entrega (64 KiB): o gerador fica suspenso até o corpo ser consumido
    world.conclude(world.document(project), content=os.urandom(300_000))
    services = world.app.state.services

    async def scenario():
        import httpx

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=world.app), base_url="http://127.0.0.1") as http:
            first = await http.get(f"/v1/projects/{project.id}/export")  # o ASGITransport termina o corpo
            assert first.status_code == 200 and services.export_active is False

    asyncio.run(scenario())
    # durante a entrega: simulada pelo gerador de corpo ainda não consumido
    async def held():
        response = await _call_without_consuming(world.app, f"/v1/projects/{project.id}/export")
        assert response["status"] == 200 and services.export_active is True
        second = await _call_without_consuming(world.app, f"/v1/projects/{project.id}/export")
        assert second["status"] == 409
        await response["finish"]()
        assert services.export_active is False

    asyncio.run(held())


def test_export_lock_and_file_are_released_when_the_client_leaves_mid_download(tmp_path, monkeypatch):
    world = World(tmp_path)
    project = world.project()
    world.conclude(world.document(project), content=os.urandom(300_000))
    tmp_dir = tmp_path / "tmp"
    tmp_dir.mkdir()
    monkeypatch.setattr(export_module, "EXPORT_TMP_DIR", str(tmp_dir))
    services = world.app.state.services

    async def scenario():
        response = await _call_without_consuming(world.app, f"/v1/projects/{project.id}/export")
        assert response["status"] == 200 and services.export_active is True
        await response["disconnect"]()
        assert services.export_active is False
        assert list(tmp_dir.iterdir()) == []

    asyncio.run(scenario())


async def _call_without_consuming(app, path):
    """Chama a rota pelo ASGI e devolve o status assim que sai, SEM consumir o corpo; `finish` o consome e desconecta."""
    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "GET", "path": path,
        "raw_path": path.encode(), "query_string": b"", "root_path": "", "scheme": "http",
        "server": ("127.0.0.1", 80), "client": ("127.0.0.1", 50000), "headers": [(b"host", b"127.0.0.1")],
    }
    incoming: asyncio.Queue = asyncio.Queue()
    outgoing: asyncio.Queue = asyncio.Queue(maxsize=1)  # o corpo só avança quando alguém consome (contrapressão)
    incoming.put_nowait({"type": "http.request", "body": b"", "more_body": False})

    async def receive():
        return await incoming.get()

    async def send(message):
        await outgoing.put(message)

    task = asyncio.create_task(app(scope, receive, send))
    start = await asyncio.wait_for(outgoing.get(), 5)

    async def finish():
        while not task.done():
            try:
                await asyncio.wait_for(outgoing.get(), 0.05)
            except asyncio.TimeoutError:
                pass
        await task

    async def disconnect():
        incoming.put_nowait({"type": "http.disconnect"})
        try:
            await asyncio.wait_for(task, 5)
        except Exception:  # noqa: BLE001 - a desconexão pode terminar a resposta com erro; só o estado final importa
            pass

    return {"status": start["status"], "finish": finish, "disconnect": disconnect}


def test_export_over_the_cap_is_413_and_leaves_no_temporary_file_and_no_lock(world, tmp_path, monkeypatch):
    project, _ = project_with_results(world, count=3)
    tmp_dir = tmp_path / "tmp"
    tmp_dir.mkdir()
    monkeypatch.setattr(export_module, "EXPORT_TMP_DIR", str(tmp_dir))
    monkeypatch.setattr(export_module, "MAX_EXPORT_BYTES", 20)  # 3 arquivos de 8 bytes passam de 20
    response = world.client.get(f"/v1/projects/{project.id}/export")
    assert response.status_code == 413 and code(response) == "payload_too_large"
    assert "512 MiB" in response.json()["error"]["message"]
    assert list(tmp_dir.iterdir()) == [] and world.app.state.services.export_active is False
    monkeypatch.setattr(export_module, "MAX_EXPORT_BYTES", 24)  # exatamente o somado: passa
    assert world.client.get(f"/v1/projects/{project.id}/export").status_code == 200


def test_export_removes_the_temporary_zip_at_the_end(world, tmp_path, monkeypatch):
    project, _ = project_with_results(world, count=2)
    tmp_dir = tmp_path / "tmp"
    tmp_dir.mkdir()
    monkeypatch.setattr(export_module, "EXPORT_TMP_DIR", str(tmp_dir))
    assert world.client.get(f"/v1/projects/{project.id}/export").status_code == 200
    assert list(tmp_dir.iterdir()) == []


def test_export_without_data_dir_configuration_is_503(tmp_path):
    world = World(tmp_path, configured=False)
    project = world.project()
    world.conclude(world.document(project), write=False)
    response = world.client.get(f"/v1/projects/{project.id}/export")
    assert response.status_code == 503 and code(response) == "data_dir_unavailable"
    assert world.app.state.services.export_active is False


def test_export_of_many_documents_is_a_valid_zip(world):
    project = world.project()
    for n in range(60):
        world.conclude(world.document(project, f"/Users/a/{n:03d}.pdf"), name=f"{n:03d}.md", content=b"x" * n)
    archive = read_zip(world.client.get(f"/v1/projects/{project.id}/export"))
    assert len(archive.namelist()) == 60 and archive.read("059.md") == b"x" * 59
