"""Confinamento de caminhos ao diretório de dados (`api/paths.py`, tarefa 5.2; FR-025, SC-003; S-01, S-07).

Roda no macOS sem Docker: o "caminho do host" é um prefixo virtual (`/host/data`) e o ponto de montagem é um
diretório temporário real.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from artigo2md.api import paths
from artigo2md.api.errors import ApiError
from artigo2md.api.paths import (
    check_copied_path,
    check_document_path,
    copied_sources_root,
    open_confined,
    open_host_file,
    to_container_path,
)

ROOT = Path(__file__).resolve().parent.parent
HOST = "/host/data"
SECRET = b"conteudo-secreto-fora-do-diretorio-de-dados"


@pytest.fixture()
def world(tmp_path):
    mount = tmp_path / "mount"
    mount.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.pdf").write_bytes(SECRET)
    (mount / "ok.pdf").write_bytes(b"%PDF-ok")
    (mount / "sub").mkdir()
    (mount / "sub" / "deep.pdf").write_bytes(b"%PDF-deep")
    return mount, outside


def read_all(fd: int) -> bytes:
    with os.fdopen(fd, "rb") as handle:
        return handle.read()


def code_of(exc: pytest.ExceptionInfo) -> str:
    assert isinstance(exc.value, ApiError)
    return exc.value.code


# ── to_container_path ───────────────────────────────────────────────────────────
@pytest.mark.parametrize("host_path,expected", [
    (HOST, "/mnt/d"),
    (f"{HOST}/a.pdf", "/mnt/d/a.pdf"),
    (f"{HOST}/sources/7/a b.pdf", "/mnt/d/sources/7/a b.pdf"),
    (f"{HOST}/ação/ç.pdf", "/mnt/d/ação/ç.pdf"),
])
def test_to_container_path_swaps_the_prefix(host_path, expected):
    assert to_container_path(host_path, HOST, "/mnt/d") == expected


@pytest.mark.parametrize("host_path", [
    "", "\0", f"{HOST}/a\0.pdf", "relativo/a.pdf", "a.pdf", ".", "..",
    f"{HOST}/../etc/passwd", f"{HOST}/a/../../etc/passwd", f"{HOST}/./a.pdf", f"{HOST}/a/..",
    f"{HOST}//a.pdf", f"{HOST}/a.pdf/", f"{HOST}/",
    "/etc/passwd", "/host", "/host/dat", "/host/data2/a.pdf", "/host/database", "/host/data.evil/a.pdf",
    "/HOST/data/a.pdf", "/host/DATA/a.pdf",  # comparação exata, sensível a maiúsculas
    "/mnt/d/a.pdf",  # o caminho do container não é um caminho de host válido
])
def test_to_container_path_refuses_unsafe_or_outside_paths(host_path):
    with pytest.raises(ApiError) as exc:
        to_container_path(host_path, HOST, "/mnt/d")
    assert code_of(exc) == "path_not_allowed"
    assert exc.value.status == 403
    assert host_path == "" or host_path not in exc.value.message or len(host_path) < 2  # não ecoa o caminho


def test_to_container_path_refuses_non_string():
    with pytest.raises(ApiError) as exc:
        to_container_path(None, HOST, "/mnt/d")  # type: ignore[arg-type]
    assert code_of(exc) == "path_not_allowed"


@pytest.mark.parametrize("host,mount", [(None, "/mnt/d"), (HOST, None), (None, None), ("", "/mnt/d")])
def test_to_container_path_without_configuration_is_data_dir_unavailable(host, mount):
    with pytest.raises(ApiError) as exc:
        to_container_path(f"{HOST}/a.pdf", host, mount)
    assert code_of(exc) == "data_dir_unavailable"
    assert exc.value.status == 503


def test_component_boundary_is_exact_with_a_root_that_looks_like_a_prefix_of_another():
    assert paths.is_under("/host/data/a", "/host/data")
    assert paths.is_under("/host/data", "/host/data")
    assert not paths.is_under("/host/data2", "/host/data")
    assert not paths.is_under("/host/dat", "/host/data")


# ── open_confined ───────────────────────────────────────────────────────────────
def test_opens_a_regular_file_inside_the_mount(world):
    mount, _ = world
    assert read_all(open_confined(str(mount / "ok.pdf"), str(mount))) == b"%PDF-ok"
    assert read_all(open_confined(str(mount / "sub" / "deep.pdf"), str(mount))) == b"%PDF-deep"


def test_open_host_file_runs_the_whole_chain(world):
    mount, _ = world
    assert read_all(open_host_file(f"{HOST}/sub/deep.pdf", HOST, str(mount))) == b"%PDF-deep"


def test_mount_given_through_a_symlink_is_accepted_when_the_file_really_is_inside(world, tmp_path):
    mount, _ = world
    link = tmp_path / "mount_link"
    link.symlink_to(mount)
    assert read_all(open_confined(str(link / "ok.pdf"), str(link))) == b"%PDF-ok"


def test_symlink_to_a_file_outside_is_refused(world):
    mount, outside = world
    (mount / "leak.pdf").symlink_to(outside / "secret.pdf")
    with pytest.raises(ApiError) as exc:
        open_confined(str(mount / "leak.pdf"), str(mount))
    assert code_of(exc) == "path_not_allowed"


def test_symlink_to_a_file_inside_is_also_refused_because_the_last_component_must_be_a_real_file(world):
    mount, _ = world
    (mount / "alias.pdf").symlink_to(mount / "ok.pdf")
    with pytest.raises(ApiError) as exc:
        open_confined(str(mount / "alias.pdf"), str(mount))
    assert code_of(exc) == "path_not_allowed"


def test_symlinked_directory_that_escapes_is_refused_by_the_descriptor_real_path(world):
    mount, outside = world
    (mount / "escape").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ApiError) as exc:
        open_confined(str(mount / "escape" / "secret.pdf"), str(mount))
    assert code_of(exc) == "path_not_allowed"


def test_absolute_path_outside_the_mount_is_refused(world):
    mount, outside = world
    with pytest.raises(ApiError) as exc:
        open_confined(str(outside / "secret.pdf"), str(mount))
    assert code_of(exc) == "path_not_allowed"


def test_dotdot_that_climbs_out_of_the_mount_is_refused(world):
    mount, outside = world
    sneaky = f"{mount}/sub/../../outside/secret.pdf"
    with pytest.raises(ApiError) as exc:
        open_confined(sneaky, str(mount))
    assert code_of(exc) == "path_not_allowed"


def test_directory_is_not_a_regular_file(world):
    mount, _ = world
    with pytest.raises(ApiError) as exc:
        open_confined(str(mount / "sub"), str(mount))
    assert code_of(exc) == "path_not_allowed"


def test_fifo_is_not_a_regular_file(world):
    mount, _ = world
    fifo = mount / "pipe.pdf"
    os.mkfifo(fifo)
    with pytest.raises(ApiError) as exc:
        open_confined(str(fifo), str(mount))  # sem O_NONBLOCK a abertura de um FIFO sem escritor travaria
    assert code_of(exc) == "path_not_allowed"


def test_missing_file_is_not_found(world):
    mount, _ = world
    with pytest.raises(ApiError) as exc:
        open_confined(str(mount / "nao-existe.pdf"), str(mount))
    assert code_of(exc) == "not_found"


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignora permissões")
def test_unreadable_file_is_data_dir_permission_denied(world):
    mount, _ = world
    target = mount / "ok.pdf"
    target.chmod(0)
    try:
        with pytest.raises(ApiError) as exc:
            open_confined(str(target), str(mount))
        assert code_of(exc) == "data_dir_permission_denied"
        assert exc.value.status == 503
    finally:
        target.chmod(0o644)


def test_missing_mount_is_data_dir_unavailable(world, tmp_path):
    with pytest.raises(ApiError) as exc:
        open_confined(str(tmp_path / "x" / "a.pdf"), str(tmp_path / "x"))
    assert code_of(exc) == "data_dir_unavailable"


def test_mount_that_is_a_file_is_data_dir_unavailable(world):
    mount, _ = world
    with pytest.raises(ApiError) as exc:
        open_confined(str(mount / "ok.pdf"), str(mount / "ok.pdf"))
    assert code_of(exc) == "data_dir_unavailable"


# ── bateria maliciosa de ponta a ponta (to_container_path + abertura) ────────────
def test_malicious_battery_reads_nothing_outside_the_data_dir(world, monkeypatch):
    mount, outside = world
    (mount / "link.pdf").symlink_to(outside / "secret.pdf")
    (mount / "dirlink").symlink_to(outside, target_is_directory=True)
    opened: list[str] = []
    real_open = os.open

    def spy(path, *args, **kwargs):
        opened.append(str(path))
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(paths.os, "open", spy)
    attacks = [
        f"{HOST}/../outside/secret.pdf",
        f"{HOST}/sub/../../outside/secret.pdf",
        f"{HOST}/a\0.pdf",
        "/etc/passwd",
        str(outside / "secret.pdf"),
        "/host/data2/secret.pdf",
        "/host/database/secret.pdf",
        "secret.pdf",
        "",
        f"{HOST}/link.pdf",
        f"{HOST}/dirlink/secret.pdf",
    ]
    for attack in attacks:
        with pytest.raises(ApiError) as exc:
            open_host_file(attack, HOST, str(mount))
        assert exc.value.code in ("path_not_allowed",), (attack, exc.value.code)
    # Nenhum caminho fora do diretório de dados chegou a ser aberto; só os dois links dentro dele foram tentados
    # (e recusados, com o descritor fechado).
    assert all(item.startswith(str(mount)) for item in opened), opened
    assert SECRET.decode() not in "".join(item for item in opened)


def test_no_descriptor_leaks_when_the_open_is_refused(world):
    mount, outside = world
    (mount / "escape").symlink_to(outside, target_is_directory=True)
    before = len(os.listdir("/dev/fd")) if os.path.isdir("/dev/fd") else None
    for _ in range(50):
        with pytest.raises(ApiError):
            open_confined(str(mount / "escape" / "secret.pdf"), str(mount))
    if before is not None:
        assert len(os.listdir("/dev/fd")) <= before + 1


# ── S-07: corrida entre conferência e abertura ──────────────────────────────────
def test_swap_to_symlink_right_before_the_open_is_refused(world, monkeypatch):
    """Um atacante troca o arquivo legítimo por um link para fora entre qualquer "conferência" e a abertura."""
    mount, outside = world
    victim = mount / "ok.pdf"
    real_open = os.open
    swapped = []

    def racing_open(path, flags, *args, **kwargs):
        if str(path) == str(victim) and not swapped:
            victim.unlink()
            victim.symlink_to(outside / "secret.pdf")
            swapped.append(True)
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(paths.os, "open", racing_open)
    with pytest.raises(ApiError) as exc:
        open_confined(str(victim), str(mount))
    assert swapped and code_of(exc) == "path_not_allowed"


def test_swap_of_a_parent_directory_to_an_escaping_symlink_is_refused(world, monkeypatch):
    mount, outside = world
    (outside / "sub").mkdir()
    (outside / "sub" / "deep.pdf").write_bytes(SECRET)
    parent = mount / "sub"
    real_open = os.open
    swapped = []

    def racing_open(path, flags, *args, **kwargs):
        if str(path).endswith("sub/deep.pdf") and not swapped:
            for child in parent.iterdir():
                child.unlink()
            parent.rmdir()
            parent.symlink_to(outside / "sub", target_is_directory=True)
            swapped.append(True)
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(paths.os, "open", racing_open)
    with pytest.raises(ApiError) as exc:
        open_confined(str(parent / "deep.pdf"), str(mount))
    assert swapped and code_of(exc) == "path_not_allowed"


def test_descriptor_whose_inode_differs_from_the_resolved_path_is_refused(world, monkeypatch):
    mount, _ = world
    monkeypatch.setattr(paths, "_descriptor_real_path", lambda fd: str(mount / "sub" / "deep.pdf"))
    with pytest.raises(ApiError) as exc:
        open_confined(str(mount / "ok.pdf"), str(mount))
    assert code_of(exc) == "path_not_allowed"


def test_without_a_way_to_check_the_descriptor_the_read_is_refused_fail_closed(world, monkeypatch):
    mount, _ = world
    monkeypatch.setattr(paths, "_descriptor_real_path", lambda fd: None)
    with pytest.raises(ApiError) as exc:
        open_confined(str(mount / "ok.pdf"), str(mount))
    assert code_of(exc) == "path_not_allowed"


def test_the_real_mechanism_is_available_on_this_platform(tmp_path):
    """Se este teste falhar, o confinamento estaria recusando tudo (fail-closed) nesta plataforma."""
    fd = os.open(tmp_path, os.O_RDONLY)
    try:
        real = paths._descriptor_real_path(fd)
    finally:
        os.close(fd)
    assert real == os.path.realpath(tmp_path)


def test_data_dir_unavailable_surfaces_through_the_whole_chain(world):
    with pytest.raises(ApiError) as exc:
        open_host_file(f"{HOST}/ok.pdf", None, None)
    assert code_of(exc) == "data_dir_unavailable"
    assert "stack/.env" in exc.value.message


# ── caminhos cadastrados pelo cliente (FR-008, FR-006) ───────────────────────────
@pytest.mark.parametrize("path", ["/Users/a/artigo.pdf", "/x/ARTIGO.PDF", "/x/a.Pdf", "/x/ação ü.pdf", "/a/.pdf"])
def test_valid_document_paths(path):
    assert check_document_path(path) is None


@pytest.mark.parametrize("path,fragment", [
    ("", "vazio"),
    ("relativo/a.pdf", "não é absoluto"),
    ("a.pdf", "não é absoluto"),
    ("/x/a\0.pdf", "nulo"),
    ("/x/a.txt", "não termina em .pdf"),
    ("/x/a.pdf ", "não termina em .pdf"),
    ("/x/pdf", "não termina em .pdf"),
    ("/" + "a" * 1021 + ".pdf", "excede 1024 bytes"),
    ("/" + "ç" * 600 + ".pdf", "excede 1024 bytes"),  # 2 bytes por caractere em UTF-8
])
def test_invalid_document_paths_cite_the_path_in_portuguese(path, fragment):
    message = check_document_path(path)
    assert message is not None and fragment in message
    if path and "\0" not in path and len(path) < 100:
        assert path in message


def test_document_path_at_exactly_1024_bytes_is_valid():
    path = "/" + "a" * 1019 + ".pdf"
    assert len(path.encode()) == 1024
    assert check_document_path(path) is None
    assert check_document_path(path + "x.pdf") is not None


def test_long_or_control_characters_do_not_flood_or_forge_the_message():
    message = check_document_path("/" + "a" * 5000 + ".txt")
    assert message is not None and len(message) < 300
    forged = check_document_path("/x/a\n\r\x1b[31m.txt")
    assert forged is not None and "\n" not in forged and "\x1b" not in forged


def test_copied_sources_root_and_check():
    assert copied_sources_root(HOST, 7) == "/host/data/sources/7"
    assert check_copied_path("/host/data/sources/7", HOST, 7) is None
    assert check_copied_path("/host/data/sources/7/a.pdf", HOST, 7) is None
    assert check_copied_path("/host/data/sources/7/sub/a.pdf", HOST, 7) is None


@pytest.mark.parametrize("path", [
    "/host/data/sources/8/a.pdf",  # outro projeto
    "/host/data/sources/70/a.pdf",  # prefixo parcial de componente
    "/host/data/sources/7x/a.pdf",
    "/host/data/sources/7/../8/a.pdf",
    "/host/data/sources/7/./a.pdf",
    "/host/data/sources/7//a.pdf",
    "/host/data/other/a.pdf",
    "/host/data/a.pdf",
    "/etc/a.pdf",
    "/host/data/sources/7\0/a.pdf",
    "",
    "sources/7/a.pdf",
])
def test_copied_path_outside_the_project_folder_is_refused_with_a_message_that_cites_it(path):
    message = check_copied_path(path, HOST, 7)
    assert message is not None and "fora da pasta permitida" in message


def test_copied_check_without_data_dir_is_data_dir_unavailable():
    with pytest.raises(ApiError) as exc:
        check_copied_path("/host/data/sources/7/a.pdf", None, 7)
    assert code_of(exc) == "data_dir_unavailable"


def test_paths_module_does_not_load_engines_or_the_driver():
    code = (
        "import sys, artigo2md.api.paths;"
        "bad=[m for m in ('docling','pymupdf4llm','pypdfium2','artigo2md.router','asyncpg') if m in sys.modules];"
        "print(','.join(bad)); sys.exit(1 if bad else 0)"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=ROOT)
    assert proc.returncode == 0, proc.stdout
