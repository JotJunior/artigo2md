"""Rotas de projetos com `FakeStore` (`api/routes/projects.py`, tarefa 6.1; FR-001 a FR-003, US1)."""
from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from artigo2md.api.app import create_app
from artigo2md.api.settings import ApiSettings
from artigo2md.store import errors as se
from fake_store import FakeStore

PROJECT_FIELDS = {"id", "name", "status", "created_at", "updated_at"}


@pytest.fixture()
def store():
    return FakeStore()


@pytest.fixture()
def client(store):
    settings = ApiSettings.from_env({}, Path("/nonexistent-cwd-for-tests"))
    with TestClient(create_app(settings, store=store), base_url="http://127.0.0.1") as test_client:
        yield test_client


def run(coro):
    return asyncio.run(coro)


def create(client, name="Estudos"):
    response = client.post("/v1/projects", json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()


def code(response):
    return response.json()["error"]["code"]


# ── criar e ler ─────────────────────────────────────────────────────────────────
def test_create_returns_201_with_exactly_the_contract_fields(client):
    body = create(client, "  Estudos  ")
    assert set(body) == PROJECT_FIELDS
    assert body["name"] == "Estudos" and body["status"] == "active" and isinstance(body["id"], int)
    for field in ("created_at", "updated_at"):
        assert datetime.fromisoformat(body[field]).tzinfo is not None


def test_get_returns_the_project_and_unknown_is_404(client):
    created = create(client)
    assert client.get(f"/v1/projects/{created['id']}").json() == created
    missing = client.get("/v1/projects/9999")
    assert missing.status_code == 404 and code(missing) == "not_found"


@pytest.mark.parametrize("name", ["éstudos", "ÉSTUDOS", "  Éstudos ", "E\u0301studos", "É\u0301".replace("É\u0301", "\u00c9") + "studos"])
def test_duplicate_name_among_active_projects_ignores_case_and_unicode_composition(client, store, name):
    """Regra do job-store: NFC + casefold (`name_key`). Acento distinto NÃO é duplicata: ver o teste seguinte."""
    create(client, "Éstudos")
    response = client.post("/v1/projects", json={"name": name})
    assert response.status_code == 409 and code(response) == "project_name_taken"
    assert len(client.get("/v1/projects").json()["projects"]) == 1  # nada foi gravado


def test_a_different_accent_is_a_different_name(client):
    create(client, "Estudos")
    assert client.post("/v1/projects", json={"name": "Éstudos"}).status_code == 201


@pytest.mark.parametrize("name", ["", "   ", "\t\n"])
def test_empty_name_is_422_validation_failed(client, name):
    response = client.post("/v1/projects", json={"name": name})
    assert response.status_code == 422 and code(response) == "validation_failed"
    assert "não pode ficar vazio" in response.json()["error"]["message"]


@pytest.mark.parametrize("payload", [{}, {"name": 5}, {"name": None}, {"name": ["a"]}, {"name": "a", "extra": 1},
                                     {"nome": "a"}, [], "texto", None])
def test_malformed_body_is_400_invalid_request(client, payload):
    response = client.post("/v1/projects", json=payload)
    assert response.status_code == 400 and code(response) == "invalid_request"


def test_malformed_json_is_400_and_does_not_echo_the_text(client):
    response = client.post("/v1/projects", content=b'{"name": "x", SEGREDO-123', headers={"Content-Type": "application/json"})
    assert response.status_code == 400 and code(response) == "invalid_request"
    assert "SEGREDO-123" not in response.text


def test_wrong_content_type_is_415(client):
    response = client.post("/v1/projects", content=b'{"name": "x"}', headers={"Content-Type": "text/plain"})
    assert response.status_code == 415 and code(response) == "unsupported_media_type"


@pytest.mark.parametrize("project_id", ["0", "-1", "abc", "1.5", str(2**63)])
def test_invalid_project_id_is_400(client, project_id):
    for method, path in (("GET", ""), ("PATCH", ""), ("POST", "/archive"), ("POST", "/unarchive")):
        response = client.request(method, f"/v1/projects/{project_id}{path}",
                                  json={"name": "x"} if method == "PATCH" else None)
        assert response.status_code == 400 and code(response) == "invalid_request", (method, path, project_id)


# ── listar ──────────────────────────────────────────────────────────────────────
def test_list_is_ordered_by_creation_and_hides_archived_by_default(client):
    first, second, third = create(client, "A"), create(client, "B"), create(client, "C")
    client.post(f"/v1/projects/{second['id']}/archive")
    default = client.get("/v1/projects").json()["projects"]
    assert [item["id"] for item in default] == [first["id"], third["id"]]
    everything = client.get("/v1/projects", params={"include_archived": "true"}).json()["projects"]
    assert [item["id"] for item in everything] == [first["id"], second["id"], third["id"]]
    assert everything[1]["status"] == "archived"
    assert all(set(item) == PROJECT_FIELDS for item in everything)


def test_empty_list_is_an_empty_array(client):
    assert client.get("/v1/projects").json() == {"projects": []}


@pytest.mark.parametrize("value", ["talvez", "2", ""])
def test_invalid_include_archived_is_400(client, value):
    response = client.get("/v1/projects", params={"include_archived": value})
    assert response.status_code == 400 and code(response) == "invalid_request"


# ── renomear ────────────────────────────────────────────────────────────────────
def test_rename_changes_the_name_and_updated_at(client):
    created = create(client, "Estudos")
    renamed = client.patch(f"/v1/projects/{created['id']}", json={"name": "  Pesquisa "})
    assert renamed.status_code == 200
    body = renamed.json()
    assert body["name"] == "Pesquisa" and body["id"] == created["id"] and body["created_at"] == created["created_at"]
    assert body["updated_at"] > created["updated_at"]
    assert client.get("/v1/projects").json()["projects"][0]["name"] == "Pesquisa"


def test_rename_to_a_name_used_by_another_active_project_is_409(client):
    create(client, "Estudos")
    other = create(client, "Pesquisa")
    response = client.patch(f"/v1/projects/{other['id']}", json={"name": "ESTUDOS"})
    assert response.status_code == 409 and code(response) == "project_name_taken"
    assert client.get(f"/v1/projects/{other['id']}").json()["name"] == "Pesquisa"


def test_rename_to_the_same_name_with_other_spelling_is_allowed(client):
    created = create(client, "estudos")
    response = client.patch(f"/v1/projects/{created['id']}", json={"name": "Estudos"})
    assert response.status_code == 200 and response.json()["name"] == "Estudos"


def test_rename_to_a_name_used_only_by_an_archived_project_is_allowed(client):
    old = create(client, "Estudos")
    client.post(f"/v1/projects/{old['id']}/archive")
    other = create(client, "Pesquisa")
    assert client.patch(f"/v1/projects/{other['id']}", json={"name": "Estudos"}).status_code == 200


def test_rename_validation_and_not_found(client):
    created = create(client)
    assert client.patch(f"/v1/projects/{created['id']}", json={"name": " "}).status_code == 422
    assert client.patch(f"/v1/projects/{created['id']}", json={"nome": "x"}).status_code == 400
    assert client.patch("/v1/projects/9999", json={"name": "x"}).status_code == 404


# ── arquivar / desarquivar ──────────────────────────────────────────────────────
def seed_jobs(store, project_id, count):
    async def go():
        source = await store.add_source(project_id, "folder", "in_place", "/x")
        job_ids = []
        for index in range(count):
            document, _ = await store.add_document(project_id, source.id, f"/x/{index}.pdf")
            job_ids.append((await store.create_job(document.id, "auto")).id)
        return job_ids

    return run(go())


def test_archive_cancels_open_jobs_and_reports_their_ids(client, store):
    project = create(client)
    job_ids = seed_jobs(store, project["id"], 3)
    run(store.start_job(job_ids[0]))
    run(store.cancel_job(job_ids[2], "cancelado no teste"))  # um já final não entra na lista
    response = client.post(f"/v1/projects/{project['id']}/archive")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"project", "cancelled_job_ids"}
    assert body["project"]["status"] == "archived" and set(body["project"]) == PROJECT_FIELDS
    assert body["cancelled_job_ids"] == sorted(job_ids[:2])
    assert all(run(store.get_job(job_id)).status == "cancelled" for job_id in job_ids)


def test_archive_without_open_jobs_returns_an_empty_list(client):
    project = create(client)
    assert client.post(f"/v1/projects/{project['id']}/archive").json()["cancelled_job_ids"] == []


def test_archive_frees_the_name_and_hides_the_project(client):
    project = create(client, "Estudos")
    client.post(f"/v1/projects/{project['id']}/archive")
    assert client.get("/v1/projects").json() == {"projects": []}
    assert client.post("/v1/projects", json={"name": "Estudos"}).status_code == 201


def test_archive_twice_is_409_invalid_transition(client):
    project = create(client)
    client.post(f"/v1/projects/{project['id']}/archive")
    response = client.post(f"/v1/projects/{project['id']}/archive")
    assert response.status_code == 409 and code(response) == "invalid_transition"


def test_unarchive_reactivates_and_active_project_is_409(client):
    project = create(client)
    client.post(f"/v1/projects/{project['id']}/archive")
    back = client.post(f"/v1/projects/{project['id']}/unarchive")
    assert back.status_code == 200 and back.json()["status"] == "active"
    again = client.post(f"/v1/projects/{project['id']}/unarchive")
    assert again.status_code == 409 and code(again) == "invalid_transition"


def test_unarchive_with_the_name_in_use_is_409_and_renames_nothing(client):
    old = create(client, "Estudos")
    client.post(f"/v1/projects/{old['id']}/archive")
    create(client, "Estudos")
    response = client.post(f"/v1/projects/{old['id']}/unarchive")
    assert response.status_code == 409 and code(response) == "project_name_taken"
    unchanged = client.get(f"/v1/projects/{old['id']}").json()
    assert unchanged["status"] == "archived" and unchanged["name"] == "Estudos"


def test_archive_and_unarchive_unknown_project_are_404(client):
    assert client.post("/v1/projects/9999/archive").status_code == 404
    assert client.post("/v1/projects/9999/unarchive").status_code == 404


def test_routes_without_body_do_not_require_content_type(client):
    project = create(client)
    assert client.post(f"/v1/projects/{project['id']}/archive").status_code == 200
    assert client.post(f"/v1/projects/{project['id']}/unarchive").status_code == 200


# ── indisponibilidade e métodos ─────────────────────────────────────────────────
def test_database_outage_is_503_in_portuguese_with_the_stack_hint(client, store):
    store.inject_failure("list_projects", se.DatabaseUnavailableError("detalhe-do-driver senha=abc"))
    response = client.get("/v1/projects")
    assert response.status_code == 503 and code(response) == "database_unavailable"
    assert "sh stack/up.sh" in response.json()["error"]["message"]
    assert "senha" not in response.text and "driver" not in response.text
    assert client.get("/v1/projects").status_code == 200  # volta sozinha


@pytest.mark.parametrize("method,path", [("PUT", "/v1/projects"), ("DELETE", "/v1/projects"),
                                         ("GET", "/v1/projects/1/archive"), ("PUT", "/v1/projects/1")])
def test_other_methods_are_405_with_the_error_body(client, method, path):
    response = client.request(method, path)
    assert response.status_code == 405 and "allow" in response.headers
    assert set(response.json()["error"]) == {"code", "message"}


def test_origin_policy_applies_to_project_routes(client):
    refused = client.post("/v1/projects", json={"name": "x"}, headers={"Origin": "https://evil.example"})
    assert refused.status_code == 403 and code(refused) == "origin_not_allowed"
    assert client.get("/v1/projects").json() == {"projects": []}  # nada foi criado
