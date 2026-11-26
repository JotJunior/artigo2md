"""Exclusão de projeto com limpeza segura de arquivos (`api/files.py`, `DELETE /v1/projects/{id}`; tarefa 6.3, FR-004)."""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from artigo2md.api import files
from artigo2md.api.app import create_app
from artigo2md.api.errors import ApiError
from artigo2md.api.settings import ApiSettings
from artigo2md.store import errors as se
from fake_store import FakeStore

HOST_DIR = "/host/data"
needs_non_root = pytest.mark.skipif(
    not hasattr(os, "geteuid") or os.geteuid() == 0, reason="root ignora permissões de arquivo"
)


@pytest.fixture()
def store():
    return FakeStore()


@pytest.fixture()
def mount(tmp_path):
    root = tmp_path / "data"
    root.mkdir()
    return root


def make_client(store, mount):
    env = {"ARTIGO2MD_DATA_DIR_HOST": HOST_DIR, "ARTIGO2MD_DATA_DIR_MOUNT": str(mount)}
    settings = ApiSettings.from_env(env, Path("/nonexistent-cwd-for-tests"))
    return TestClient(create_app(settings, store=store), base_url="http://127.0.0.1")


@pytest.fixture()
def client(store, mount):
    with make_client(store, mount) as test_client:
        yield test_client


def run(coro):
    return asyncio.run(coro)


def code(response):
    return response.json()["error"]["code"]


def new_project(client, name="Estudos"):
    response = client.post("/v1/projects", json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def make_job(store, project_id, count=1, path="/Users/a/a.pdf"):
    """Cria fonte + documento + `count` jobs pelo store; devolve os ids dos jobs."""
    source = run(store.add_source(project_id, "folder", "in_place", "/Users/a"))
    document, _ = run(store.add_document(project_id, source.id, path))
    jobs = []
    for _ in range(count):
        job = run(store.create_job(document.id, "auto"))
        run(store.cancel_job(job.id, "Cancelado."))  # libera o documento para um novo job (um ativo por documento)
        jobs.append(job.id)
    return jobs


def make_tree(base: Path, files_count=2) -> Path:
    base.mkdir(parents=True)
    for index in range(files_count):
        (base / f"f{index}.md").write_text("conteúdo")
    nested = base / "sub"
    nested.mkdir()
    (nested / "n.md").write_text("conteúdo")
    return base


# ── 6.3.1: 204, banco e arquivos ────────────────────────────────────────────────
def test_delete_returns_204_empty_and_the_project_is_gone(client):
    project_id = new_project(client)
    response = client.delete(f"/v1/projects/{project_id}")
    assert response.status_code == 204 and response.content == b""
    assert client.get(f"/v1/projects/{project_id}").status_code == 404
    assert client.delete(f"/v1/projects/{project_id}").status_code == 404


def test_delete_unknown_project_is_404(client):
    response = client.delete("/v1/projects/9999")
    assert response.status_code == 404 and code(response) == "not_found"
    assert client.delete("/v1/projects/0").status_code == 400


def test_delete_removes_records_and_directories_only_of_that_project(store, client, mount):
    one, two = new_project(client, "Um"), new_project(client, "Dois")
    jobs_one = make_job(store, one, count=2)
    jobs_two = make_job(store, two)
    for job_id in jobs_one + jobs_two:
        make_tree(mount / str(job_id))
    make_tree(mount / "sources" / str(one))
    make_tree(mount / "sources" / str(two))
    stray = mount / "stray.md"
    stray.write_text("fora de qualquer job")

    assert client.delete(f"/v1/projects/{one}").status_code == 204

    for job_id in jobs_one:
        assert not (mount / str(job_id)).exists()
    assert not (mount / "sources" / str(one)).exists()
    # outro projeto e o resto do diretório de dados intactos
    assert (mount / str(jobs_two[0]) / "sub" / "n.md").read_text() == "conteúdo"
    assert (mount / "sources" / str(two) / "f0.md").exists()
    assert (mount / "sources").is_dir() and stray.read_text() == "fora de qualquer job"
    assert client.get(f"/v1/projects/{two}").status_code == 200


def test_delete_is_ok_when_no_directory_exists(store, client, mount):
    project_id = new_project(client)
    make_job(store, project_id, count=2)
    assert client.delete(f"/v1/projects/{project_id}").status_code == 204


# ── 6.3.3: retenção de diretórios de jobs substituídos ──────────────────────────
def test_replaced_job_directories_are_kept_until_the_project_is_deleted(store, client, mount):
    project_id = new_project(client)
    first, second = make_job(store, project_id, count=2)
    make_tree(mount / str(first))
    make_tree(mount / str(second))
    # operações que NÃO são a exclusão não limpam nada: arquivar, desarquivar, ler, listar
    assert client.post(f"/v1/projects/{project_id}/archive").status_code == 200
    assert client.post(f"/v1/projects/{project_id}/unarchive").status_code == 200
    client.get(f"/v1/projects/{project_id}/documents")
    assert (mount / str(first) / "f0.md").exists() and (mount / str(second) / "f0.md").exists()
    assert client.delete(f"/v1/projects/{project_id}").status_code == 204
    assert not (mount / str(first)).exists() and not (mount / str(second)).exists()


# ── 6.3.4: links simbólicos, confinamento e fontes in_place ─────────────────────
def test_symlink_in_place_of_a_job_directory_is_not_followed(store, client, mount, tmp_path):
    project_id = new_project(client)
    (job_id,) = make_job(store, project_id)
    outside = make_tree(tmp_path / "fora")
    (mount / str(job_id)).symlink_to(outside, target_is_directory=True)
    assert client.delete(f"/v1/projects/{project_id}").status_code == 204
    assert (outside / "f0.md").exists() and (outside / "sub" / "n.md").exists()
    assert (mount / str(job_id)).is_symlink()  # o link em si também fica: nada fora do que a API cria é tocado


def test_symlink_inside_the_tree_is_unlinked_but_never_followed(store, client, mount, tmp_path):
    project_id = new_project(client)
    (job_id,) = make_job(store, project_id)
    outside = make_tree(tmp_path / "fora")
    tree = make_tree(mount / str(job_id))
    (tree / "link-dir").symlink_to(outside, target_is_directory=True)
    (tree / "link-file").symlink_to(outside / "f0.md")
    assert client.delete(f"/v1/projects/{project_id}").status_code == 204
    assert not (mount / str(job_id)).exists()
    assert (outside / "f0.md").exists() and (outside / "sub" / "n.md").exists()


def test_symlinked_sources_directory_is_not_followed(store, client, mount, tmp_path):
    project_id = new_project(client)
    outside = make_tree(tmp_path / "fora-sources" / str(project_id))
    (mount / "sources").symlink_to(outside.parent, target_is_directory=True)
    assert client.delete(f"/v1/projects/{project_id}").status_code == 204
    assert (outside / "f0.md").exists()


def test_symlink_in_place_of_the_project_sources_directory_is_not_followed(store, client, mount, tmp_path):
    project_id = new_project(client)
    outside = make_tree(tmp_path / "fora")
    (mount / "sources").mkdir()
    (mount / "sources" / str(project_id)).symlink_to(outside, target_is_directory=True)
    assert client.delete(f"/v1/projects/{project_id}").status_code == 204
    assert (outside / "f0.md").exists()


def test_a_regular_file_named_like_a_job_is_not_removed(store, client, mount):
    project_id = new_project(client)
    (job_id,) = make_job(store, project_id)
    marker = mount / str(job_id)
    marker.write_text("arquivo, não diretório")
    assert client.delete(f"/v1/projects/{project_id}").status_code == 204
    assert marker.read_text() == "arquivo, não diretório"


def test_in_place_source_files_are_never_removed(store, client, mount, tmp_path):
    project_id = new_project(client)
    # fonte in_place FORA do diretório de dados e outra DENTRO dele (fora de sources/<id>/ e de <job_id>/)
    outside_pdf = tmp_path / "meus-pdfs" / "a.pdf"
    outside_pdf.parent.mkdir()
    outside_pdf.write_bytes(b"%PDF")
    inside_pdf = mount / "biblioteca" / "b.pdf"
    inside_pdf.parent.mkdir()
    inside_pdf.write_bytes(b"%PDF")
    source = run(store.add_source(project_id, "folder", "in_place", str(inside_pdf.parent)))
    run(store.add_document(project_id, source.id, str(inside_pdf)))
    assert client.delete(f"/v1/projects/{project_id}").status_code == 204
    assert outside_pdf.read_bytes() == b"%PDF" and inside_pdf.read_bytes() == b"%PDF"


def test_only_derived_paths_are_candidates(tmp_path):
    root = str(tmp_path)
    assert files.derived_targets(root, 7, [3, 4]) == [f"{root}/3", f"{root}/4", f"{root}/sources/7"]
    # ids inválidos (não inteiros, zero, negativos, bool) nunca viram caminho
    assert files.derived_targets(root, 7, [0, -1, True, "../x", 2.5, None]) == [f"{root}/sources/7"]
    assert files.derived_targets(root, 0, []) == []


def test_unconfigured_or_missing_mount_is_data_dir_unavailable(tmp_path):
    for mount in (None, "", str(tmp_path / "nao-existe")):
        with pytest.raises(ApiError) as caught:
            files.plan_removal(mount, 1, [1])
        assert caught.value.code == "data_dir_unavailable"


def test_delete_without_data_dir_is_503_and_keeps_the_project(store, tmp_path):
    settings = ApiSettings.from_env({}, Path("/nonexistent-cwd-for-tests"))
    with TestClient(create_app(settings, store=store), base_url="http://127.0.0.1") as bare:
        project_id = new_project(bare)
        response = bare.delete(f"/v1/projects/{project_id}")
        assert response.status_code == 503 and code(response) == "data_dir_unavailable"
        assert bare.get(f"/v1/projects/{project_id}").status_code == 200


# ── 6.3.5: permissão antes do COMMIT ────────────────────────────────────────────
@needs_non_root
def test_directory_without_permission_is_503_in_portuguese_and_nothing_changes(store, client, mount):
    project_id = new_project(client)
    (job_id,) = make_job(store, project_id)
    tree = make_tree(mount / str(job_id))
    sources = make_tree(mount / "sources" / str(project_id))
    tree.chmod(0o500)  # só o teste altera permissões; a API jamais chama chmod
    try:
        response = client.delete(f"/v1/projects/{project_id}")
        assert response.status_code == 503 and code(response) == "data_dir_permission_denied"
        message = response.json()["error"]["message"]
        assert "permiss" in message and "root" in message and "chmod" not in message
        assert str(mount) not in message
        assert client.get(f"/v1/projects/{project_id}").status_code == 200  # banco intacto
        assert (sources / "f0.md").exists() and (tree / "f0.md").exists()  # nenhum arquivo removido
        assert (tree.stat().st_mode & 0o777) == 0o500  # a API não mudou a permissão
    finally:
        tree.chmod(0o700)
    assert client.delete(f"/v1/projects/{project_id}").status_code == 204  # com permissão, a exclusão passa
    assert not tree.exists() and not sources.exists()


@needs_non_root
def test_nested_directory_without_permission_is_also_refused(store, client, mount):
    project_id = new_project(client)
    (job_id,) = make_job(store, project_id)
    tree = make_tree(mount / str(job_id))
    (tree / "sub").chmod(0o500)
    try:
        response = client.delete(f"/v1/projects/{project_id}")
        assert response.status_code == 503 and code(response) == "data_dir_permission_denied"
        assert client.get(f"/v1/projects/{project_id}").status_code == 200
    finally:
        (tree / "sub").chmod(0o700)


@needs_non_root
def test_mount_without_write_permission_is_refused(store, client, mount):
    project_id = new_project(client)
    (job_id,) = make_job(store, project_id)
    make_tree(mount / str(job_id))
    mount.chmod(0o500)
    try:
        response = client.delete(f"/v1/projects/{project_id}")
        assert response.status_code == 503 and code(response) == "data_dir_permission_denied"
        assert client.get(f"/v1/projects/{project_id}").status_code == 200
    finally:
        mount.chmod(0o700)


def test_files_module_never_changes_permissions():
    source = Path(files.__file__).read_text()
    for forbidden in ("chmod", "chown", "setuid", "sudo"):
        assert forbidden not in source.replace("jamais `chmod`", "").replace("sem `chmod`", "")


# ── falhas ──────────────────────────────────────────────────────────────────────
def test_database_failure_keeps_everything(store, client, mount):
    project_id = new_project(client)
    (job_id,) = make_job(store, project_id)
    make_tree(mount / str(job_id))
    store.inject_failure("delete_project", se.DatabaseUnavailableError("fora"))
    response = client.delete(f"/v1/projects/{project_id}")
    assert response.status_code == 503 and code(response) == "database_unavailable"
    assert (mount / str(job_id) / "f0.md").exists()
    assert client.get(f"/v1/projects/{project_id}").status_code == 200


def test_failure_removing_after_commit_is_logged_and_does_not_undo_the_deletion(
    store, client, mount, monkeypatch, caplog
):
    project_id = new_project(client)
    (job_id,) = make_job(store, project_id)
    make_tree(mount / str(job_id))

    def boom(path, *args, **kwargs):
        raise PermissionError(13, "Permission denied", str(path))

    monkeypatch.setattr(files.shutil, "rmtree", boom)
    with caplog.at_level(logging.WARNING, logger="artigo2md.api"):
        response = client.delete(f"/v1/projects/{project_id}")
    assert response.status_code == 204
    assert client.get(f"/v1/projects/{project_id}").status_code == 404
    assert any("exclusão do projeto foi mantida" in record.getMessage() for record in caplog.records)
    assert str(mount) not in caplog.text  # o log não cita caminho nem texto do SO


def test_rmtree_is_never_asked_to_remove_a_link_or_anything_outside_the_mount(store, client, mount, tmp_path, monkeypatch):
    project_id = new_project(client)
    jobs = make_job(store, project_id, count=2)
    make_tree(mount / str(jobs[0]))
    outside = make_tree(tmp_path / "fora")
    (mount / str(jobs[1])).symlink_to(outside, target_is_directory=True)
    (mount / "sources").mkdir()
    make_tree(mount / "sources" / str(project_id))
    seen = []
    real = files.shutil.rmtree
    monkeypatch.setattr(files.shutil, "rmtree", lambda path, *a, **k: (seen.append(path), real(path, *a, **k))[1])
    assert client.delete(f"/v1/projects/{project_id}").status_code == 204
    root = os.path.realpath(mount)
    assert sorted(seen) == sorted([f"{root}/{jobs[0]}", f"{root}/sources/{project_id}"])
