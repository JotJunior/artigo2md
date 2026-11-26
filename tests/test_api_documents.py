"""Rotas de fontes e documentos com `FakeStore` (`api/routes/documents.py`, tarefa 6.2; FR-005 a FR-009, US1)."""
from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from artigo2md.api.app import create_app
from artigo2md.api.settings import ApiSettings
from artigo2md.store import errors as se
from artigo2md.store.models import QualityRecord
from fake_store import FakeStore

HOST_DIR = "/host/data"
SOURCE_FIELDS = {"id", "project_id", "kind", "mode", "path", "created_at"}
DOCUMENT_FIELDS = {"id", "project_id", "source_id", "path", "created_at"}
STATUS_FIELDS = {
    "document_id", "project_id", "source_id", "path", "status", "latest_job_id", "latest_job_requested_at",
    "last_result",
}
RESULT_FIELDS = {"job_id", "markdown_path", "engine", "score", "accepted", "created_at"}
GOOD = QualityRecord(score=90.0, accepted=True, tables_found=1, tables_expected=1, chars_per_page=1200.0)


@pytest.fixture()
def store():
    return FakeStore()


def make_client(store, tmp_path, *, with_data_dir=True):
    env = {}
    if with_data_dir:
        env = {"ARTIGO2MD_DATA_DIR_HOST": HOST_DIR, "ARTIGO2MD_DATA_DIR_MOUNT": str(tmp_path)}
    settings = ApiSettings.from_env(env, Path("/nonexistent-cwd-for-tests"))
    return TestClient(create_app(settings, store=store), base_url="http://127.0.0.1")


@pytest.fixture()
def client(store, tmp_path):
    with make_client(store, tmp_path) as test_client:
        yield test_client


def run(coro):
    return asyncio.run(coro)


def code(response):
    return response.json()["error"]["code"]


def new_project(client, name="Estudos"):
    response = client.post("/v1/projects", json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def add_source(client, project_id, *, kind="folder", mode="in_place", path="/Users/a/pdfs", documents=()):
    return client.post(
        f"/v1/projects/{project_id}/sources",
        json={"kind": kind, "mode": mode, "path": path, "documents": list(documents)},
    )


def pdfs(count, base="/Users/a/pdfs"):
    return [f"{base}/doc-{index:04d}.pdf" for index in range(count)]


# ── fonte `in_place` e documentos (US1-6) ───────────────────────────────────────
def test_folder_source_creates_one_document_per_path_with_contract_fields(client):
    project_id = new_project(client)
    response = add_source(client, project_id, documents=pdfs(3))
    assert response.status_code == 201, response.text
    body = response.json()
    assert set(body) == {"source", "documents"}
    assert set(body["source"]) == SOURCE_FIELDS
    assert body["source"]["project_id"] == project_id
    assert (body["source"]["kind"], body["source"]["mode"]) == ("folder", "in_place")
    assert set(body["documents"]) == {"created", "existing", "rejected"}
    assert [item["path"] for item in body["documents"]["created"]] == pdfs(3)
    assert body["documents"]["existing"] == [] and body["documents"]["rejected"] == []
    for item in body["documents"]["created"]:
        assert set(item) == DOCUMENT_FIELDS and item["source_id"] == body["source"]["id"]
        assert datetime.fromisoformat(item["created_at"]).tzinfo is not None


def test_listing_shows_every_new_document_as_not_processed_by_id(client):
    project_id = new_project(client)
    add_source(client, project_id, documents=pdfs(3))
    listing = client.get(f"/v1/projects/{project_id}/documents")
    assert listing.status_code == 200
    items = listing.json()["documents"]
    assert [item["path"] for item in items] == pdfs(3)
    assert [item["document_id"] for item in items] == sorted(item["document_id"] for item in items)
    for item in items:
        assert set(item) == STATUS_FIELDS
        assert item["status"] == "not_processed"
        assert item["latest_job_id"] is None and item["latest_job_requested_at"] is None
        assert item["last_result"] is None


def test_source_without_documents_is_allowed_and_listed(client):
    project_id = new_project(client)
    assert add_source(client, project_id).status_code == 201
    listed = client.get(f"/v1/projects/{project_id}/sources")
    assert listed.status_code == 200 and len(listed.json()["sources"]) == 1
    assert set(listed.json()["sources"][0]) == SOURCE_FIELDS
    assert client.get(f"/v1/projects/{project_id}/documents").json() == {"documents": []}


# ── idempotência por (projeto, caminho) (US1-7) ─────────────────────────────────
def test_repeated_paths_go_to_existing_and_keep_document_and_original_source(client):
    project_id = new_project(client)
    first = add_source(client, project_id, documents=pdfs(2)).json()
    again = add_source(client, project_id, path="/Users/a/outra", documents=pdfs(3)).json()
    assert [item["path"] for item in again["documents"]["existing"]] == pdfs(2)
    assert [item["path"] for item in again["documents"]["created"]] == pdfs(3)[2:]
    originals = {item["path"]: item for item in first["documents"]["created"]}
    for item in again["documents"]["existing"]:
        assert item["id"] == originals[item["path"]]["id"]
        assert item["source_id"] == first["source"]["id"]  # mantém a fonte original
    assert len(client.get(f"/v1/projects/{project_id}/documents").json()["documents"]) == 3


def test_same_path_in_two_projects_is_two_documents(client):
    one, two = new_project(client, "Um"), new_project(client, "Dois")
    add_source(client, one, documents=pdfs(1))
    response = add_source(client, two, documents=pdfs(1))
    assert len(response.json()["documents"]["created"]) == 1


def test_post_documents_on_an_existing_source_is_idempotent(client):
    project_id = new_project(client)
    source = add_source(client, project_id, documents=pdfs(1)).json()["source"]
    url = f"/v1/projects/{project_id}/sources/{source['id']}/documents"
    response = client.post(url, json={"documents": pdfs(3)})
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"created", "existing", "rejected"}
    assert [item["path"] for item in body["existing"]] == pdfs(1)
    assert [item["path"] for item in body["created"]] == pdfs(3)[1:]
    assert all(item["source_id"] == source["id"] for item in body["created"])


def test_repeated_path_inside_one_list_is_created_once(client):
    project_id = new_project(client)
    body = add_source(client, project_id, documents=[pdfs(1)[0]] * 2).json()["documents"]
    assert len(body["created"]) == 1 and len(body["existing"]) == 1


# ── caminho inválido citado na mensagem sem afetar os válidos (US1-8) ───────────
LONG_PATH = "/Users/a/" + "x" * 1030 + ".pdf"


@pytest.mark.parametrize(
    "bad,fragment",
    [
        ("", "vazio"),
        ("relativo/arquivo.pdf", "relativo/arquivo.pdf"),
        ("/Users/a/livro.txt", "/Users/a/livro.txt"),
        ("/Users/a/semextensao", "/Users/a/semextensao"),
        ("/Users/a/nulo\0.pdf", "nulo"),
        (LONG_PATH, "1024"),
    ],
)
def test_invalid_paths_are_rejected_with_message_and_valid_ones_are_saved(client, bad, fragment):
    project_id = new_project(client)
    good = pdfs(2)
    body = add_source(client, project_id, documents=[good[0], bad, good[1]]).json()["documents"]
    assert [item["path"] for item in body["created"]] == good
    assert len(body["rejected"]) == 1
    rejected = body["rejected"][0]
    assert set(rejected) == {"path", "message"} and rejected["path"] == bad
    assert fragment in rejected["message"]
    assert "\0" not in rejected["message"]
    assert len(client.get(f"/v1/projects/{project_id}/documents").json()["documents"]) == 2


def test_extension_check_ignores_case(client):
    project_id = new_project(client)
    body = add_source(client, project_id, documents=["/Users/a/LIVRO.PDF"]).json()["documents"]
    assert len(body["created"]) == 1 and body["rejected"] == []


def test_all_invalid_still_registers_the_source_with_everything_rejected(client):
    project_id = new_project(client)
    response = add_source(client, project_id, documents=["x.pdf", "/Users/a/y.txt"])
    assert response.status_code == 201
    assert response.json()["documents"]["created"] == []
    assert len(response.json()["documents"]["rejected"]) == 2


# ── kind = file ─────────────────────────────────────────────────────────────────
def test_file_source_requires_documents_to_be_exactly_the_path(client):
    project_id = new_project(client)
    path = "/Users/a/artigo.pdf"
    ok = add_source(client, project_id, kind="file", path=path, documents=[path])
    assert ok.status_code == 201 and [item["path"] for item in ok.json()["documents"]["created"]] == [path]
    for documents in ([], [path, "/Users/a/b.pdf"], ["/Users/a/b.pdf"]):
        response = add_source(client, project_id, kind="file", path=path, documents=documents)
        assert response.status_code == 400 and code(response) == "invalid_request", documents
    assert len(client.get(f"/v1/projects/{project_id}/sources").json()["sources"]) == 1


def test_file_source_with_invalid_document_path_is_refused_and_nothing_is_written(client):
    project_id = new_project(client)
    response = add_source(client, project_id, kind="file", path="/Users/a/livro.txt", documents=["/Users/a/livro.txt"])
    assert response.status_code == 422 and code(response) == "validation_failed"
    assert "/Users/a/livro.txt" in response.json()["error"]["message"]
    assert client.get(f"/v1/projects/{project_id}/sources").json() == {"sources": []}


def test_documents_of_a_file_source_must_stay_exactly_that_path(client):
    project_id = new_project(client)
    path = "/Users/a/artigo.pdf"
    source = add_source(client, project_id, kind="file", path=path, documents=[path]).json()["source"]
    url = f"/v1/projects/{project_id}/sources/{source['id']}/documents"
    assert client.post(url, json={"documents": [path]}).json()["existing"][0]["path"] == path
    assert client.post(url, json={"documents": ["/Users/a/outro.pdf"]}).status_code == 400


# ── domínio: kind, mode, caminho da fonte ───────────────────────────────────────
@pytest.mark.parametrize("field,value,listed", [
    ("kind", "directory", "folder, file"),
    ("mode", "linked", "in_place, copied"),
])
def test_kind_and_mode_outside_the_lists_are_422_naming_the_valid_values(client, field, value, listed):
    project_id = new_project(client)
    payload = {"kind": "folder", "mode": "in_place", "path": "/Users/a/pdfs", "documents": []}
    payload[field] = value
    response = client.post(f"/v1/projects/{project_id}/sources", json=payload)
    assert response.status_code == 422 and code(response) == "validation_failed"
    assert listed in response.json()["error"]["message"]
    assert client.get(f"/v1/projects/{project_id}/sources").json() == {"sources": []}


@pytest.mark.parametrize("path", ["", "relativo", "/a\0b"])
def test_invalid_source_path_is_422_and_writes_nothing(client, path):
    project_id = new_project(client)
    response = add_source(client, project_id, path=path)
    assert response.status_code == 422 and code(response) == "validation_failed"
    assert client.get(f"/v1/projects/{project_id}/sources").json() == {"sources": []}


# ── fonte `copied` ──────────────────────────────────────────────────────────────
def copied_root(project_id):
    return f"{HOST_DIR}/sources/{project_id}"


def test_copied_source_inside_the_project_folder_is_accepted(client):
    project_id = new_project(client)
    root = copied_root(project_id)
    docs = [f"{root}/a.pdf", f"{root}/sub/b.pdf"]
    response = add_source(client, project_id, mode="copied", path=root, documents=docs)
    assert response.status_code == 201, response.text
    assert [item["path"] for item in response.json()["documents"]["created"]] == docs


def test_copied_source_outside_the_folder_is_422_and_writes_nothing(client):
    project_id = new_project(client)
    for path in (
        "/Users/a/pdfs",                                  # fora do diretório de dados
        f"{HOST_DIR}/sources/{project_id}x",              # sem fronteira de componente
        f"{HOST_DIR}/sources/{project_id + 1}",           # pasta de outro projeto
        f"{copied_root(project_id)}/../{project_id + 1}", # componente `..`
        f"{HOST_DIR}/outra",
    ):
        response = add_source(client, project_id, mode="copied", path=path)
        assert response.status_code == 422 and code(response) == "validation_failed", path
        assert path in response.json()["error"]["message"] or ".." in path
    assert client.get(f"/v1/projects/{project_id}/sources").json() == {"sources": []}


def test_copied_documents_outside_the_folder_are_rejected_individually(client):
    project_id = new_project(client)
    root = copied_root(project_id)
    inside = f"{root}/ok.pdf"
    outside = [
        "/Users/a/fora.pdf",
        f"{HOST_DIR}/sources/{project_id}x/a.pdf",
        f"{HOST_DIR}/sources/{project_id + 1}/a.pdf",
        f"{root}/../{project_id + 1}/a.pdf",
    ]
    body = add_source(client, project_id, mode="copied", path=root, documents=[inside, *outside]).json()["documents"]
    assert [item["path"] for item in body["created"]] == [inside]
    assert [item["path"] for item in body["rejected"]] == outside
    assert all("sources" in item["message"] for item in body["rejected"])


def test_copied_documents_added_later_follow_the_same_rule(client):
    project_id = new_project(client)
    root = copied_root(project_id)
    source = add_source(client, project_id, mode="copied", path=root).json()["source"]
    url = f"/v1/projects/{project_id}/sources/{source['id']}/documents"
    body = client.post(url, json={"documents": [f"{root}/a.pdf", "/Users/a/fora.pdf"]}).json()
    assert len(body["created"]) == 1 and [item["path"] for item in body["rejected"]] == ["/Users/a/fora.pdf"]


def test_in_place_source_may_point_anywhere_even_inside_the_data_dir(client):
    project_id = new_project(client)
    assert add_source(client, project_id, path=f"{HOST_DIR}/qualquer").status_code == 201


def test_copied_source_without_data_dir_is_503_data_dir_unavailable(store, tmp_path):
    with make_client(store, tmp_path, with_data_dir=False) as bare:
        project_id = new_project(bare)
        response = add_source(bare, project_id, mode="copied", path="/x/sources/1")
        assert response.status_code == 503 and code(response) == "data_dir_unavailable"
        assert add_source(bare, project_id).status_code == 201  # in_place não precisa do diretório de dados


# ── 404, 400, 413, 415 ──────────────────────────────────────────────────────────
def test_unknown_project_and_source_are_404(client):
    project_id = new_project(client)
    for response in (
        client.get("/v1/projects/9999/sources"),
        client.get("/v1/projects/9999/documents"),
        add_source(client, 9999),
        client.post("/v1/projects/9999/sources/1/documents", json={"documents": []}),
        client.post(f"/v1/projects/{project_id}/sources/9999/documents", json={"documents": []}),
        client.get("/v1/documents/9999"),
    ):
        assert response.status_code == 404 and code(response) == "not_found", response.url


def test_source_of_another_project_is_404_for_this_one(client):
    one, two = new_project(client, "Um"), new_project(client, "Dois")
    source = add_source(client, one, documents=pdfs(1)).json()["source"]
    response = client.post(f"/v1/projects/{two}/sources/{source['id']}/documents", json={"documents": pdfs(2)})
    assert response.status_code == 404 and code(response) == "not_found"
    assert client.get(f"/v1/projects/{two}/documents").json() == {"documents": []}


@pytest.mark.parametrize("payload", [
    {"kind": "folder", "mode": "in_place"},                                       # path ausente
    {"kind": "folder", "mode": "in_place", "path": "/a", "extra": 1},             # campo desconhecido
    {"kind": 1, "mode": "in_place", "path": "/a"},                                # tipo errado
    {"kind": "folder", "mode": "in_place", "path": "/a", "documents": "/a/b.pdf"},
    {"kind": "folder", "mode": "in_place", "path": "/a", "documents": [1]},
])
def test_malformed_source_body_is_400(client, payload):
    project_id = new_project(client)
    response = client.post(f"/v1/projects/{project_id}/sources", json=payload)
    assert response.status_code == 400 and code(response) == "invalid_request"


def test_malformed_documents_body_and_ids_are_400(client):
    project_id = new_project(client)
    source = add_source(client, project_id).json()["source"]
    url = f"/v1/projects/{project_id}/sources/{source['id']}/documents"
    assert client.post(url, json={}).status_code == 400
    assert client.post(url, json={"documents": [], "x": 1}).status_code == 400
    assert client.get("/v1/documents/0").status_code == 400
    assert client.get("/v1/projects/0/documents").status_code == 400
    assert client.post(url, content="{", headers={"content-type": "application/json"}).status_code == 400


def test_body_without_json_content_type_is_415(client):
    project_id = new_project(client)
    response = client.post(f"/v1/projects/{project_id}/sources", content="{}", headers={"content-type": "text/plain"})
    assert response.status_code == 415


def test_more_than_10000_paths_is_413_and_writes_nothing(client):
    project_id = new_project(client)
    response = add_source(client, project_id, documents=["/a.pdf"] * 10_001)
    assert response.status_code == 413 and code(response) == "payload_too_large"
    assert client.get(f"/v1/projects/{project_id}/sources").json() == {"sources": []}
    source = add_source(client, project_id).json()["source"]
    url = f"/v1/projects/{project_id}/sources/{source['id']}/documents"
    assert client.post(url, json={"documents": ["/a.pdf"] * 10_001}).status_code == 413


# ── atomicidade e indisponibilidade do banco ────────────────────────────────────
def test_failure_while_saving_documents_leaves_no_source_behind(store, client):
    project_id = new_project(client)
    store.inject_failure("add_document", se.DatabaseUnavailableError("fora"))
    response = add_source(client, project_id, documents=pdfs(2))
    assert response.status_code == 503 and code(response) == "database_unavailable"
    assert client.get(f"/v1/projects/{project_id}/sources").json() == {"sources": []}


def test_database_down_is_503_with_the_stack_hint(store, client):
    project_id = new_project(client)
    store.inject_failure("list_documents", se.DatabaseUnavailableError("fora"))
    response = client.get(f"/v1/projects/{project_id}/documents")
    assert response.status_code == 503 and code(response) == "database_unavailable"
    assert "sh stack/up.sh" in response.json()["error"]["message"]


# ── lista de 1.000 documentos ───────────────────────────────────────────────────
def test_a_thousand_documents_are_registered_and_listed_in_id_order(client):
    project_id = new_project(client)
    response = add_source(client, project_id, documents=pdfs(1000))
    assert response.status_code == 201 and len(response.json()["documents"]["created"]) == 1000
    items = client.get(f"/v1/projects/{project_id}/documents").json()["documents"]
    assert len(items) == 1000
    assert [item["path"] for item in items] == pdfs(1000)
    ids = [item["document_id"] for item in items]
    assert ids == sorted(ids) and len(set(ids)) == 1000
    again = add_source(client, project_id, documents=pdfs(1000)).json()["documents"]
    assert len(again["existing"]) == 1000 and again["created"] == []


# ── DocumentStatus: mapeamento de estados (FR-009) ──────────────────────────────
def one_document(client):
    project_id = new_project(client)
    document = add_source(client, project_id, documents=pdfs(1)).json()["documents"]["created"][0]
    return project_id, document["id"]


def status_of(client, document_id):
    response = client.get(f"/v1/documents/{document_id}")
    assert response.status_code == 200, response.text
    return response.json()


def test_status_follows_the_latest_job(store, client):
    _, document_id = one_document(client)
    assert status_of(client, document_id)["status"] == "not_processed"

    job = run(store.create_job(document_id, "auto"))
    item = status_of(client, document_id)
    assert item["status"] == "pending" and item["latest_job_id"] == job.id
    assert datetime.fromisoformat(item["latest_job_requested_at"]).tzinfo is not None
    assert item["last_result"] is None

    run(store.start_job(job.id))
    assert status_of(client, document_id)["status"] == "running"

    run(store.record_attempt(job.id, "docling", 1.5, GOOD))
    run(store.complete_job(job.id, "/data/1/a.md", "docling", GOOD, "Concluído."))
    item = status_of(client, document_id)
    assert item["status"] == "succeeded"
    assert set(item["last_result"]) == RESULT_FIELDS
    assert item["last_result"]["job_id"] == job.id and item["last_result"]["engine"] == "docling"
    assert item["last_result"]["score"] == 90.0 and item["last_result"]["accepted"] is True
    assert item["last_result"]["markdown_path"] == "/data/1/a.md"

    newer = run(store.create_job(document_id, "pymupdf"))
    run(store.fail_job(newer.id, "Falhou.", "Job falhou."))
    item = status_of(client, document_id)
    assert item["status"] == "failed" and item["latest_job_id"] == newer.id
    assert item["last_result"]["job_id"] == job.id  # resultado vigente não some com um job mais novo falho

    last = run(store.create_job(document_id, "auto"))
    run(store.cancel_job(last.id, "Cancelado."))
    assert status_of(client, document_id)["status"] == "cancelled"


def test_listing_mixes_statuses_in_document_id_order(store, client):
    project_id = new_project(client)
    docs = add_source(client, project_id, documents=pdfs(3)).json()["documents"]["created"]
    run(store.create_job(docs[0]["id"], "auto"))
    failed = run(store.create_job(docs[1]["id"], "auto"))
    run(store.fail_job(failed.id, "Falhou.", "Job falhou."))
    items = client.get(f"/v1/projects/{project_id}/documents").json()["documents"]
    assert [item["status"] for item in items] == ["pending", "failed", "not_processed"]
    assert [item["document_id"] for item in items] == [doc["id"] for doc in docs]


def test_unit_get_matches_the_listing_entry(store, client):
    project_id = new_project(client)
    docs = add_source(client, project_id, documents=pdfs(3)).json()["documents"]["created"]
    run(store.create_job(docs[1]["id"], "auto"))
    listing = client.get(f"/v1/projects/{project_id}/documents").json()["documents"]
    for item in listing:
        assert status_of(client, item["document_id"]) == item


def test_responses_never_expose_internal_fields(client):
    project_id = new_project(client)
    body = add_source(client, project_id, documents=pdfs(1)).json()
    text = str(body) + client.get(f"/v1/projects/{project_id}/documents").text
    assert "published_at" not in text and "name_key" not in text
