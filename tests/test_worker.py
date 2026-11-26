"""`ConversionWorker`: classificação de erro, `tick`, `kill` e limite de tempo (tarefa 4.1 da `conversion-queue`).

Usa filhos falsos (`tests/fake_child.py`) e, para `invalid_pdf`, o filho real com PDFs sem motor carregado.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time

import pytest

import fake_child
from artigo2md import worker as worker_mod
from artigo2md.worker import (
    ConversionError,
    ConversionTimeout,
    ConversionWorker,
    WorkerCrashed,
)


@pytest.fixture
def make_worker(monkeypatch):
    created: list[ConversionWorker] = []

    def make(target) -> ConversionWorker:
        monkeypatch.setattr(worker_mod, "_child_main", target)
        w = ConversionWorker(idle_timeout=0)
        created.append(w)
        return w

    yield make
    for w in created:
        w.kill()
        w.shutdown()


def test_progress_and_done_without_tick_behave_as_before(make_worker):
    w = make_worker(fake_child.echo_main)
    seen: list[str] = []
    out = w.convert("/x/a.pdf", "docling", seen.append)
    assert out["markdown"] == "# /x/a.pdf" and out["engine"] == "docling"
    assert seen == ["Carregando o conversor", "meio"]


def test_tick_is_called_while_waiting_for_the_child(make_worker, monkeypatch):
    monkeypatch.setattr(worker_mod, "TICK_INTERVAL", 0.05)
    w = make_worker(fake_child.slow_main)
    ticks: list[int] = []
    out = w.convert("/x/a.pdf", "auto", lambda _m: None, tick=lambda: ticks.append(1))
    assert out["markdown"] == "# lento"
    assert len(ticks) >= 5  # ~0,6 s de espera com intervalos de 0,05 s


def test_error_from_the_child_is_a_conversion_error_of_kind_error(make_worker):
    w = make_worker(fake_child.error_main)
    with pytest.raises(ConversionError) as info:
        w.convert("/x/a.pdf", "auto", lambda _m: None)
    assert info.value.kind == "error" and "Nenhum motor" in str(info.value)
    assert isinstance(info.value, RuntimeError)  # web.py continua pegando RuntimeError/Exception


def test_invalid_pdf_from_the_child_has_its_own_kind(make_worker):
    w = make_worker(fake_child.invalid_pdf_main)
    with pytest.raises(ConversionError) as info:
        w.convert("/x/a.pdf", "auto", lambda _m: None)
    assert info.value.kind == "invalid_pdf" and "não é um PDF válido" in str(info.value)


def test_real_child_reports_invalid_pdf_for_garbage_and_encrypted_files(not_a_pdf, protected_pdf):
    w = ConversionWorker(idle_timeout=0)
    try:
        for path in (not_a_pdf, protected_pdf):
            with pytest.raises(ConversionError) as info:
                w.convert(str(path), "auto", lambda _m: None)
            assert info.value.kind == "invalid_pdf"
            assert path.name in str(info.value)
    finally:
        w.shutdown()


def test_child_that_dies_raises_worker_crashed_with_the_usual_message(make_worker):
    w = make_worker(fake_child.die_main)
    with pytest.raises(WorkerCrashed) as info:
        w.convert("/x/a.pdf", "auto", lambda _m: None)
    assert str(info.value) == ("O processo de conversão terminou inesperadamente "
                               "(possível falta de memória). Tente de novo ou use outro motor.")
    assert not w.alive


def test_crash_is_detected_with_tick_and_timeout_too(make_worker, monkeypatch):
    monkeypatch.setattr(worker_mod, "TICK_INTERVAL", 0.05)
    w = make_worker(fake_child.die_main)
    with pytest.raises(WorkerCrashed):
        w.convert("/x/a.pdf", "auto", lambda _m: None, tick=lambda: None, timeout=30)


def test_conversion_timeout_kills_the_child_and_is_a_distinct_error(make_worker, monkeypatch):
    monkeypatch.setattr(worker_mod, "TICK_INTERVAL", 0.05)
    w = make_worker(fake_child.hang_main)
    started = time.monotonic()
    with pytest.raises(ConversionTimeout) as info:
        w.convert("/x/a.pdf", "auto", lambda _m: None, timeout=0.3)
    assert time.monotonic() - started < 5
    assert info.value.seconds == 0.3 and "0.3 s" in str(info.value)
    assert not w.alive
    assert not isinstance(info.value, (ConversionError, WorkerCrashed))


def test_timeout_zero_means_no_limit(make_worker, monkeypatch):
    monkeypatch.setattr(worker_mod, "TICK_INTERVAL", 0.05)
    w = make_worker(fake_child.slow_main)
    assert w.convert("/x/a.pdf", "auto", lambda _m: None, timeout=0)["markdown"] == "# lento"


def test_worker_is_reusable_after_a_timeout(make_worker, monkeypatch):
    monkeypatch.setattr(worker_mod, "TICK_INTERVAL", 0.05)
    w = make_worker(fake_child.hang_main)
    with pytest.raises(ConversionTimeout):
        w.convert("/x/a.pdf", "auto", lambda _m: None, timeout=0.2)
    monkeypatch.setattr(worker_mod, "_child_main", fake_child.echo_main)
    assert w.convert("/x/b.pdf", "auto", lambda _m: None)["markdown"] == "# /x/b.pdf"


def test_kill_stops_the_child_without_waiting_for_the_lock(make_worker):
    w = make_worker(fake_child.hang_main)
    errors: list[BaseException] = []

    def run():
        try:
            w.convert("/x/a.pdf", "auto", lambda _m: None)
        except BaseException as exc:  # noqa: BLE001 - coletamos para afirmar abaixo
            errors.append(exc)

    thread = threading.Thread(target=run)
    thread.start()
    deadline = time.monotonic() + 10
    while not w.alive and time.monotonic() < deadline:
        time.sleep(0.02)
    time.sleep(0.2)  # o filho já recebeu o job e está pendurado; o lock está com `convert`
    started = time.monotonic()
    w.kill()
    assert time.monotonic() - started < 5
    thread.join(10)
    assert not thread.is_alive()
    assert len(errors) == 1 and isinstance(errors[0], WorkerCrashed)
    assert not w.alive


def test_kill_without_a_child_is_a_noop():
    ConversionWorker(idle_timeout=0).kill()


def test_exception_in_tick_kills_the_child_so_no_stale_answer_remains(make_worker, monkeypatch):
    monkeypatch.setattr(worker_mod, "TICK_INTERVAL", 0.05)
    w = make_worker(fake_child.hang_main)

    def broken_tick():
        raise ConnectionError("perdi o broker")

    with pytest.raises(ConnectionError):
        w.convert("/x/a.pdf", "auto", lambda _m: None, tick=broken_tick)
    assert not w.alive


def test_main_process_does_not_import_the_engines():
    code = (
        "import sys, artigo2md.worker, artigo2md.web\n"
        "print(','.join(m for m in ('docling', 'pymupdf4llm', 'pika', 'psutil') if m in sys.modules))\n"
    )
    out = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == ""


@pytest.mark.skipif(not hasattr(os, "killpg"), reason="grupos de processos só existem em POSIX")
def test_sigint_to_the_process_group_does_not_kill_the_child(tmp_path):
    """FR-013: o Ctrl+C chega ao grupo inteiro; o filho não pode morrer (viraria `worker_crashed`)."""
    scenario = os.path.join(os.path.dirname(__file__), "sigint_group_scenario.py")
    proc = subprocess.Popen([sys.executable, scenario], stdout=subprocess.PIPE, text=True,
                            start_new_session=True)  # líder do próprio grupo: o killpg não alcança o pytest
    try:
        assert proc.stdout.readline().strip() == "READY"  # o filho já está convertendo
        os.killpg(proc.pid, signal.SIGINT)
        result = json.loads(proc.stdout.readline())
        proc.wait(timeout=30)
    finally:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGKILL)
    assert result["err"] is None, result  # sem WorkerCrashed
    assert result["markdown"] == "# concluído"


def test_child_that_ignores_sigint_is_still_stopped_by_shutdown_and_kill(make_worker):
    """9.1.1: ignorar SIGINT não pode impedir o pai de encerrar o filho (`shutdown`, usado pelo `web.py`, e `kill`)."""
    w = make_worker(fake_child.real_main_slow_convert)
    w.convert("/x/a.pdf", "auto", lambda _m: None)
    child = w._proc
    os.kill(child.pid, signal.SIGINT)
    time.sleep(0.3)
    assert child.is_alive()  # o SIGINT foi ignorado
    w.shutdown()
    assert not child.is_alive() and not w.alive

    w.convert("/x/a.pdf", "auto", lambda _m: None)
    child = w._proc
    w.kill()
    assert not child.is_alive()
