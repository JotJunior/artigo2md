"""Serviço do worker: pool de N slots, sinais e encerramento sem processos órfãos (FR-004, FR-013, FR-014, FR-016).

O processo principal cria N slots (thread + conexão + canal + `ConversionWorker`); no máximo N conversões
ocorrem ao mesmo tempo. SIGINT/SIGTERM param o consumo, dão até `--shutdown-timeout` s ao job em andamento e,
esgotado o prazo, encerram o filho de conversão e devolvem o job à fila sem `ack`. A reconexão (backoff de
1 s até 15 s) e a topologia vivem no slot; aqui só se decide o código de saída:

0 encerrado por sinal; 4 credenciais ausentes ou recusadas; 5 topologia incompatível; 1 defeito inesperado.
"""
from __future__ import annotations

import signal
import threading
import time
from typing import Any, Callable

from ..worker import ConversionWorker
from .memory import MemoryGate
from .settings import (EXIT_CREDENTIALS, EXIT_FAILURE, EXIT_OK, EXIT_TOPOLOGY, WorkerSettings)
from .slot import STOPPED, BUSY, CredentialsRefused, Slot, default_log
from .topology import TopologyMismatch

MAIN_POLL_SECONDS = 0.2
JOIN_GRACE_SECONDS = 10.0  # tempo para as threads terminarem depois de o filho ser encerrado
SIGNALS = (signal.SIGINT, signal.SIGTERM)


class Service:
    def __init__(
        self,
        settings: WorkerSettings,
        *,
        gate: MemoryGate | None = None,
        converter_factory: Callable[[WorkerSettings], Any] | None = None,
        connect: Callable[[WorkerSettings], Any] | None = None,
        properties: Callable[..., Any] | None = None,
        log: Callable[[str], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
        wait: Callable[[float], Any] | None = None,
        poll_interval: float | None = None,
    ):
        self.settings = settings
        self._log = log or default_log
        self.gate = gate or MemoryGate(settings.min_free_memory_mb)
        factory = converter_factory or (lambda s: ConversionWorker(idle_timeout=s.idle_timeout))
        slot_kwargs: dict[str, Any] = {"connect": connect, "properties": properties, "log": self._log,
                                       "clock": clock, "wait": wait}
        if poll_interval is not None:
            slot_kwargs["poll_interval"] = poll_interval
        self.slots = [Slot(i + 1, settings, self.gate, factory(settings), **slot_kwargs)
                      for i in range(settings.workers)]
        self._threads: list[threading.Thread] = []
        self._shutdown = threading.Event()

    # ── controle ────────────────────────────────────────────────────────────────────────────────
    def request_shutdown(self) -> None:
        """Pede o encerramento ordenado (chamado pelos manipuladores de sinal)."""
        self._shutdown.set()

    def _install_signals(self) -> dict:
        previous = {}
        if threading.current_thread() is threading.main_thread():
            for sig in SIGNALS:
                previous[sig] = signal.signal(sig, lambda *_args: self.request_shutdown())
        return previous

    @staticmethod
    def _restore_signals(previous: dict) -> None:
        for sig, handler in previous.items():
            signal.signal(sig, handler)

    # ── execução ────────────────────────────────────────────────────────────────────────────────
    def run(self, install_signals: bool = True) -> int:
        warning = self.gate.pool_warning(self.settings.workers)
        if warning:
            self._log(warning)
        previous = self._install_signals() if install_signals else {}
        try:
            self._start()
            exit_code = self._supervise()
            self._shutdown_slots()
        finally:
            self._restore_signals(previous)
        return exit_code

    def _start(self) -> None:
        for slot in self.slots:
            thread = threading.Thread(target=slot.run, name=f"artigo2md-slot-{slot.index}", daemon=True)
            self._threads.append(thread)
            thread.start()

    def _supervise(self) -> int:
        while not self._shutdown.wait(MAIN_POLL_SECONDS):
            for slot in self.slots:
                if slot.fatal is not None:
                    return self._fatal_exit_code(slot.fatal)
            if all(slot.state == STOPPED for slot in self.slots):
                return EXIT_FAILURE
        return EXIT_OK

    def _fatal_exit_code(self, error: BaseException) -> int:
        if isinstance(error, CredentialsRefused):
            self._log(error.message)
            return EXIT_CREDENTIALS
        if isinstance(error, TopologyMismatch):
            self._log(error.message)
            return EXIT_TOPOLOGY
        self._log(f"Erro inesperado no worker ({type(error).__name__}): {str(error)[:300]}")
        return EXIT_FAILURE

    def _shutdown_slots(self) -> None:
        for slot in self.slots:
            slot.request_stop()
        if any(slot.state == BUSY for slot in self.slots):
            self._log(f"Encerrando: aguardando o job em andamento por até "
                      f"{self.settings.shutdown_timeout:g} s...")
        self._join(time.monotonic() + self.settings.shutdown_timeout)
        if any(thread.is_alive() for thread in self._threads):  # prazo esgotado: devolve os jobs sem ack
            for slot in self.slots:
                if slot.state != STOPPED:
                    slot.abandon()
            self._join(time.monotonic() + JOIN_GRACE_SECONDS)
        for slot in self.slots:  # nenhum filho de conversão sobrevive à saída (SC-008)
            slot.converter.kill()
            slot.converter.shutdown()

    def _join(self, deadline: float) -> None:
        for thread in self._threads:
            thread.join(max(0.0, deadline - time.monotonic()))
