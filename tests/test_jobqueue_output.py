"""Destino confinado e gravação atômica do Markdown (tarefa 3.2 da `conversion-queue`)."""
from __future__ import annotations

import errno
import os
import shutil
import socket
import tempfile
from pathlib import Path

import pytest

from artigo2md.jobqueue import output
from artigo2md.jobqueue.output import OutputError


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    path = tmp_path / "data"
    path.mkdir()
    return path


def _snapshot(root: Path) -> set[str]:
    return {str(p.relative_to(root)) for p in root.rglob("*")}


def test_writes_to_job_folder_with_pdf_stem(data_dir):
    target = output.write_markdown(data_dir, "job-1", "/x/y/Estudo Final.v2.pdf", "# Olá\n")
    assert target == data_dir.resolve() / "job-1" / "Estudo Final.v2.md"
    assert target.read_text(encoding="utf-8") == "# Olá\n"


# ── travessia e nomes inválidos ─────────────────────────────────────────────────
@pytest.mark.parametrize("job_id", ["..", "../x", "a/b", "/abs", "a\\b", "", ".oculto", "a\x00b", "x" * 129])
def test_invalid_job_id_writes_nothing(data_dir, tmp_path, job_id):
    with pytest.raises(OutputError) as info:
        output.write_markdown(data_dir, job_id, "/x/a.pdf", "x")
    assert info.value.code == "invalid_path"
    assert _snapshot(data_dir) == set()
    assert _snapshot(tmp_path) == {"data"}


@pytest.mark.parametrize("pdf_path", ["/x/.oculto.pdf", "/x/..pdf", "/x/.pdf", "/x/a\x00b.pdf", "/",
                                      "/x/" + "n" * 300 + ".pdf"])
def test_invalid_derived_name_writes_nothing(data_dir, tmp_path, pdf_path):
    with pytest.raises(OutputError) as info:
        output.write_markdown(data_dir, "job-1", pdf_path, "x")
    assert info.value.code == "invalid_path"
    assert _snapshot(tmp_path) == {"data"}


def test_backslash_in_stem_is_rejected(data_dir):
    with pytest.raises(OutputError) as info:
        output.write_markdown(data_dir, "job-1", "/x/a\\b.pdf", "x")
    assert info.value.code == "invalid_path"


def test_stem_never_carries_directories(data_dir):
    target = output.write_markdown(data_dir, "job-1", "/x/../../etc/passwd.pdf", "x")
    assert target.parent == data_dir.resolve() / "job-1" and target.name == "passwd.md"


def test_symlink_planted_as_job_folder_is_blocked(data_dir, tmp_path):
    outside = tmp_path / "fora"
    outside.mkdir()
    (data_dir / "job-1").symlink_to(outside, target_is_directory=True)
    with pytest.raises(OutputError) as info:
        output.write_markdown(data_dir, "job-1", "/x/a.pdf", "x")
    assert info.value.code == "invalid_path"
    assert list(outside.iterdir()) == []


def test_symlink_to_a_sibling_job_folder_is_blocked(data_dir):
    (data_dir / "outro").mkdir()
    (data_dir / "job-1").symlink_to(data_dir / "outro", target_is_directory=True)
    with pytest.raises(OutputError) as info:
        output.write_markdown(data_dir, "job-1", "/x/a.pdf", "x")
    assert info.value.code == "invalid_path"
    assert list((data_dir / "outro").iterdir()) == []


def test_dangling_symlink_and_regular_file_as_job_folder_are_blocked(data_dir, tmp_path):
    (data_dir / "job-1").symlink_to(tmp_path / "nao-existe", target_is_directory=True)
    (data_dir / "job-2").write_text("arquivo")
    for job_id in ("job-1", "job-2"):
        with pytest.raises(OutputError) as info:
            output.write_markdown(data_dir, job_id, "/x/a.pdf", "x")
        assert info.value.code == "invalid_path"
    assert not (tmp_path / "nao-existe").exists()


def test_symlink_planted_as_the_output_file_is_replaced_not_followed(data_dir, tmp_path):
    outside = tmp_path / "alvo.txt"
    outside.write_text("intacto")
    (data_dir / "job-1").mkdir()
    (data_dir / "job-1" / "a.md").symlink_to(outside)
    target = output.write_markdown(data_dir, "job-1", "/x/a.pdf", "novo")
    assert outside.read_text() == "intacto"
    assert not target.is_symlink() and target.read_text() == "novo"


def test_enametoolong_from_the_system_is_invalid_path(data_dir, monkeypatch):
    def boom(*_a, **_k):
        raise OSError(errno.ENAMETOOLONG, "File name too long")

    monkeypatch.setattr(output.tempfile, "mkstemp", boom)
    with pytest.raises(OutputError) as info:
        output.write_markdown(data_dir, "job-1", "/x/a.pdf", "x")
    assert info.value.code == "invalid_path"


def test_io_error_is_write_failed_and_transient_class(data_dir, monkeypatch):
    def boom(*_a, **_k):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(output.tempfile, "mkstemp", boom)
    with pytest.raises(OutputError) as info:
        output.write_markdown(data_dir, "job-1", "/x/a.pdf", "x")
    assert info.value.code == "write_failed" and "No space" in info.value.reason


# ── atomicidade e idempotência ──────────────────────────────────────────────────
def test_failure_midway_leaves_no_partial_file_or_temp(data_dir, monkeypatch):
    def boom(_fd):
        raise OSError(errno.EIO, "Input/output error")

    monkeypatch.setattr(output.os, "fsync", boom)
    with pytest.raises(OutputError) as info:
        output.write_markdown(data_dir, "job-1", "/x/a.pdf", "conteudo")
    assert info.value.code == "write_failed"
    assert list((data_dir / "job-1").iterdir()) == []


def test_failure_keeps_the_previous_output_intact(data_dir, monkeypatch):
    output.write_markdown(data_dir, "job-1", "/x/a.pdf", "antigo")

    def boom(_src, _dst):
        raise OSError(errno.EIO, "Input/output error")

    monkeypatch.setattr(output.os, "replace", boom)
    with pytest.raises(OutputError):
        output.write_markdown(data_dir, "job-1", "/x/a.pdf", "novo")
    names = sorted(p.name for p in (data_dir / "job-1").iterdir())
    assert names == ["a.md"]
    assert (data_dir / "job-1" / "a.md").read_text() == "antigo"


def test_rewrite_is_idempotent_and_last_wins(data_dir):
    first = output.write_markdown(data_dir, "job-1", "/x/a.pdf", "um")
    second = output.write_markdown(data_dir, "job-1", "/x/a.pdf", "dois")
    assert first == second and second.read_text() == "dois"
    assert [p.name for p in (data_dir / "job-1").iterdir()] == ["a.md"]


def test_utf8_content_is_written_without_newline_translation(data_dir):
    target = output.write_markdown(data_dir, "job-1", "/x/a.pdf", "linha\r\nção\n")
    assert target.read_bytes() == "linha\r\nção\n".encode("utf-8")


# ── pdf_path ────────────────────────────────────────────────────────────────────
def test_pdf_path_regular_file_and_symlink_to_regular_are_accepted(tmp_path):
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"%PDF")
    link = tmp_path / "link.pdf"
    link.symlink_to(pdf)
    assert output.check_pdf_path(str(pdf)) == pdf
    assert output.check_pdf_path(str(link)) == link


@pytest.mark.parametrize("bad", ["a.pdf", "./a.pdf", "", "/nao/existe.pdf", "/x\x00y.pdf"])
def test_pdf_path_relative_missing_or_with_nul(bad):
    with pytest.raises(OutputError) as info:
        output.check_pdf_path(bad)
    assert info.value.code == "pdf_not_found"


def test_pdf_path_directory_dangling_symlink_and_loop(tmp_path):
    (tmp_path / "pasta.pdf").mkdir()
    (tmp_path / "pendente.pdf").symlink_to(tmp_path / "nada.pdf")
    (tmp_path / "laco1").symlink_to(tmp_path / "laco2")
    (tmp_path / "laco2").symlink_to(tmp_path / "laco1")
    link_dir = tmp_path / "para-pasta.pdf"
    link_dir.symlink_to(tmp_path / "pasta.pdf", target_is_directory=True)
    for name in ("pasta.pdf", "pendente.pdf", "laco1", "para-pasta.pdf"):
        with pytest.raises(OutputError) as info:
            output.check_pdf_path(str(tmp_path / name))
        assert info.value.code == "pdf_not_found"


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="sem mkfifo")
def test_pdf_path_fifo_is_rejected_without_opening_it(tmp_path, monkeypatch):
    fifo = tmp_path / "fila.pdf"
    os.mkfifo(fifo)

    def no_open(*_a, **_k):
        raise AssertionError("o PDF não pode ser aberto na checagem")

    monkeypatch.setattr("builtins.open", no_open)
    monkeypatch.setattr(os, "open", no_open)
    with pytest.raises(OutputError) as info:
        output.check_pdf_path(str(fifo))
    assert info.value.code == "pdf_not_found"


def test_pdf_path_socket_is_rejected():
    folder = Path(tempfile.mkdtemp(prefix="a2m", dir="/tmp"))  # caminho curto: limite do AF_UNIX
    path = folder / "s.pdf"
    sock = socket.socket(socket.AF_UNIX)
    try:
        sock.bind(str(path))
        with pytest.raises(OutputError) as info:
            output.check_pdf_path(str(path))
        assert info.value.code == "pdf_not_found"
    finally:
        sock.close()
        shutil.rmtree(folder, ignore_errors=True)
