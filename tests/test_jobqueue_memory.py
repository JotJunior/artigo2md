"""Gate de RAM compartilhado (tarefa 3.4 da `conversion-queue`)."""
from __future__ import annotations

import subprocess
import sys
import threading

import pytest

from artigo2md.jobqueue import memory
from artigo2md.jobqueue.memory import MemoryGate, MemoryReading


class FakeRam:
    """Leitura de memória controlada pelo teste."""

    def __init__(self, available_mb: float, total_mb: float = 16384):
        self.available_mb = available_mb
        self.total_mb = total_mb
        self.reads = 0

    def __call__(self) -> MemoryReading:
        self.reads += 1
        return MemoryReading(self.available_mb, self.total_mb)


def test_below_the_limit_nothing_starts_and_message_is_in_portuguese():
    gate = MemoryGate(2048, FakeRam(1500))
    result = gate.try_acquire()
    assert not result.allowed and result.reservation is None
    assert result.message == ("Memória livre insuficiente para iniciar mais um conversor "
                              "(1500 MB livres, mínimo 2048 MB). Nova verificação em 10 s.")
    assert gate.reserved_mb == 0


def test_with_enough_memory_the_gate_releases_and_reserves():
    gate = MemoryGate(2048, FakeRam(4096))
    result = gate.try_acquire()
    assert result.allowed and result.message is None
    assert gate.reserved_mb == 2048


def test_exactly_the_minimum_passes():
    assert MemoryGate(2048, FakeRam(2048)).try_acquire().allowed


def test_minimum_zero_always_passes():
    ram = FakeRam(0)
    gate = MemoryGate(0, ram)
    assert gate.try_acquire().allowed and gate.try_acquire().allowed


def test_second_slot_discounts_the_reservation_of_the_first():
    gate = MemoryGate(2048, FakeRam(3000))  # cabe um conversor, não dois
    first, second = gate.try_acquire(), gate.try_acquire()
    assert first.allowed and not second.allowed
    assert second.free_mb == 3000 - 2048
    assert "952 MB livres" in second.message


def test_release_after_load_failure_lets_the_next_slot_pass():
    gate = MemoryGate(2048, FakeRam(3000))
    first = gate.try_acquire()
    assert not gate.try_acquire().allowed
    first.reservation.release()
    assert gate.reserved_mb == 0
    assert gate.try_acquire().allowed


def test_release_is_idempotent_and_works_as_context_manager():
    gate = MemoryGate(1000, FakeRam(5000))
    first = gate.try_acquire().reservation
    second = gate.try_acquire().reservation
    first.release()
    first.release()
    assert first.released and gate.reserved_mb == 1000
    with second:
        pass
    assert gate.reserved_mb == 0


def test_simultaneous_cold_slots_with_memory_for_only_one():
    gate = MemoryGate(2048, FakeRam(2500))
    slots = 8
    barrier = threading.Barrier(slots)
    results = []

    def cold_slot():
        barrier.wait()
        results.append(gate.try_acquire())

    threads = [threading.Thread(target=cold_slot) for _ in range(slots)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert sum(r.allowed for r in results) == 1
    assert gate.reserved_mb == 2048


def test_still_ok_does_not_discount_its_own_reservation_and_does_not_reserve():
    ram = FakeRam(2500)
    gate = MemoryGate(2048, ram)
    mine = gate.try_acquire().reservation
    assert gate.still_ok(mine).allowed
    assert not gate.still_ok().allowed  # quem não tem reserva desconta a dos outros
    ram.available_mb = 1000  # a memória caiu entre a checagem e a entrega
    again = gate.still_ok(mine)
    assert not again.allowed and "1000 MB livres" in again.message
    assert gate.reserved_mb == 2048


def test_memory_is_read_at_each_evaluation():
    ram = FakeRam(1000)
    gate = MemoryGate(2048, ram)
    assert not gate.try_acquire().allowed
    ram.available_mb = 4000
    assert gate.try_acquire().allowed
    assert ram.reads == 2


def test_pool_of_one_slot_works_sequentially_with_memory_for_a_single_converter():
    gate = MemoryGate(2048, FakeRam(2500, total_mb=16384))
    assert gate.pool_warning(1) is None
    for _ in range(3):  # N = 1: um único slot reutiliza a reserva ciclo após ciclo
        result = gate.try_acquire()
        assert result.allowed
        result.reservation.release()
    assert gate.reserved_mb == 0


def test_pool_above_total_ram_warns_but_is_not_refused():
    gate = MemoryGate(4096, FakeRam(8000, total_mb=16384))
    warning = gate.pool_warning(5)  # 5 x 4096 = 20480 > 16384
    assert warning == ("Aviso: 5 conversores podem esgotar a memória deste computador; a proteção de RAM "
                       "vai adiar conversores quando faltar memória.")
    assert gate.pool_warning(4) is None  # 4 x 4096 = 16384, não passa do total


def test_pool_warning_without_total_is_silent():
    assert memory.pool_warning(10, 4096, None) is None


def test_module_loads_without_psutil_or_engines():
    code = (
        "import sys, artigo2md.jobqueue.memory\n"
        "print(','.join(m for m in ('psutil', 'pika', 'docling', 'pymupdf4llm') if m in sys.modules))\n"
    )
    out = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == ""


def test_real_reader_returns_plausible_numbers():
    pytest.importorskip("psutil")
    reading = memory.psutil_reader()
    assert 0 < reading.available_mb <= reading.total_mb
