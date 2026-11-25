"""Gate de RAM compartilhado entre os slots do worker (FR-005; tarefa 1.7 da `conversion-queue`).

Um gate **único por processo**, serializado por lock. O slot que passa no gate **reserva** memória (o valor de
`--min-free-memory`) até o conversor terminar de carregar ou falhar; os demais slots frios descontam as
reservas vigentes da memória livre ao avaliar. Assim, N slots frios simultâneos com memória para só um
conversor resultam em um único conversor carregando.

A leitura da memória é injetável (`reader`), para testar sem depender da máquina. O `psutil` só é importado
dentro da leitura padrão, ou seja, este módulo carrega sem ele. Unidade: 1 MB = 1 MiB (1048576 bytes), a
mesma de `--min-free-memory`.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Callable

RECHECK_SECONDS = 10  # nova verificação depois de uma recusa (research.md, Decision 7)
_MB = 1024 * 1024


@dataclass(frozen=True)
class MemoryReading:
    available_mb: float  # memória que pode ser dada sem swap (`psutil.virtual_memory().available`)
    total_mb: float


def psutil_reader() -> MemoryReading:
    """Leitura real: `psutil.virtual_memory()` (`available` e `total`)."""
    import psutil

    vm = psutil.virtual_memory()
    return MemoryReading(vm.available / _MB, vm.total / _MB)


class Reservation:
    """Reserva de memória de um slot; `release()` é idempotente e pode ser usada como gerenciador de contexto."""

    def __init__(self, gate: "MemoryGate", amount_mb: float):
        self._gate = gate
        self.amount_mb = amount_mb
        self._released = False

    @property
    def released(self) -> bool:
        return self._released

    def release(self) -> None:
        self._gate._release(self)

    def __enter__(self) -> "Reservation":
        return self

    def __exit__(self, *_exc) -> None:
        self.release()


@dataclass(frozen=True)
class GateResult:
    allowed: bool
    free_mb: float  # memória livre já descontadas as reservas dos outros slots
    min_free_mb: float
    reservation: Reservation | None = None
    message: str | None = None  # em português, só quando recusado


def insufficient_memory_message(free_mb: float, min_free_mb: float) -> str:
    return (
        f"Memória livre insuficiente para iniciar mais um conversor ({round(free_mb)} MB livres, "
        f"mínimo {round(min_free_mb)} MB). Nova verificação em {RECHECK_SECONDS} s."
    )


def pool_warning(workers: int, min_free_mb: float, total_mb: float | None) -> str | None:
    """Aviso (sem recusar o valor) quando `workers × min_free_memory` passa da memória total."""
    if total_mb is None or workers * min_free_mb <= total_mb:
        return None
    return (
        f"Aviso: {workers} conversores podem esgotar a memória deste computador; a proteção de RAM "
        "vai adiar conversores quando faltar memória."
    )


class MemoryGate:
    def __init__(self, min_free_mb: float, reader: Callable[[], MemoryReading] | None = None):
        self.min_free_mb = min_free_mb
        self._reader = reader or psutil_reader
        self._lock = threading.Lock()
        self._reservations: list[Reservation] = []

    def read(self) -> MemoryReading:
        return self._reader()

    @property
    def reserved_mb(self) -> float:
        with self._lock:
            return sum(r.amount_mb for r in self._reservations)

    def _free_locked(self, own: Reservation | None = None) -> float:
        others = sum(r.amount_mb for r in self._reservations if r is not own)
        return self._reader().available_mb - others

    def try_acquire(self) -> GateResult:
        """Avalia o gate; se passar, devolve uma reserva que o chamador deve liberar (carregou ou falhou)."""
        with self._lock:
            free = self._free_locked()
            if free < self.min_free_mb:
                return GateResult(False, free, self.min_free_mb,
                                  message=insufficient_memory_message(free, self.min_free_mb))
            reservation = Reservation(self, self.min_free_mb)
            self._reservations.append(reservation)
            return GateResult(True, free, self.min_free_mb, reservation)

    def still_ok(self, reservation: Reservation | None = None) -> GateResult:
        """Reavalia sem reservar (na entrega de um job): a própria reserva não é descontada de si mesma."""
        with self._lock:
            free = self._free_locked(own=reservation)
            if free < self.min_free_mb:
                return GateResult(False, free, self.min_free_mb,
                                  message=insufficient_memory_message(free, self.min_free_mb))
            return GateResult(True, free, self.min_free_mb)

    def _release(self, reservation: Reservation) -> None:
        with self._lock:
            if reservation._released:
                return
            reservation._released = True
            self._reservations = [r for r in self._reservations if r is not reservation]

    def pool_warning(self, workers: int) -> str | None:
        return pool_warning(workers, self.min_free_mb, self._reader().total_mb)
