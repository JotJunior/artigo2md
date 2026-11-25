"""Processo auxiliar de conversão, que se encerra sozinho quando fica ocioso.

Os modelos (Docling, pymupdf4llm) ocupam de 1 a 2 GB depois de carregados, e o Python
não devolve essa memória ao sistema. Por isso as conversões da interface web rodam num
processo separado: em lote ele continua vivo e rápido; parado por `idle_timeout`
segundos, ele termina e a RAM volta inteira para o sistema.
"""
from __future__ import annotations

import multiprocessing as mp
import signal
import threading
import time
from typing import Callable


class ConversionError(RuntimeError):
    """O filho concluiu com falha. `kind`: `error` (qualquer erro de conversão) ou `invalid_pdf`."""

    def __init__(self, message: str, kind: str = "error"):
        super().__init__(message)
        self.kind = kind


class WorkerCrashed(RuntimeError):
    """O processo de conversão morreu sem responder (possível falta de memória)."""


class ConversionTimeout(RuntimeError):
    """A conversão passou do limite de tempo e o filho foi encerrado."""

    def __init__(self, seconds: float):
        super().__init__(f"A conversão excedeu o limite de {seconds:g} s e foi encerrada.")
        self.seconds = seconds


CRASH_MESSAGE = (
    "O processo de conversão terminou inesperadamente "
    "(possível falta de memória). Tente de novo ou use outro motor."
)
TICK_INTERVAL = 0.5  # segundos entre chamadas de `tick` enquanto espera o filho


def _child_main(conn) -> None:
    # O Ctrl+C do terminal chega a todo o grupo de processos. O encerramento do filho é sempre do pai
    # (`_stop`/`kill`); se o filho morresse com KeyboardInterrupt, o pai leria EOF e trataria como
    # `worker_crashed` em vez de concluir o job ou devolvê-lo à fila (FR-013).
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    from .pdfscan import InvalidPdfError
    from .router import convert

    while True:
        try:
            msg = conn.recv()
        except (EOFError, KeyboardInterrupt):
            return
        if msg is None:
            return
        path, engine = msg
        try:
            result = convert(path, engine=engine, progress=lambda m: conn.send(("progress", m)))
            conn.send(("done", {"markdown": result.markdown, "engine": result.engine,
                                "report": result.report()}))
        except InvalidPdfError as exc:
            conn.send(("invalid_pdf", str(exc)[:800]))
        except Exception as exc:
            conn.send(("error", str(exc)[:800]))


class ConversionWorker:
    def __init__(self, idle_timeout: float = 300):
        self.idle_timeout = idle_timeout
        self._ctx = mp.get_context("spawn")  # "fork" é inseguro com PyTorch no macOS
        self._proc = None
        self._conn = None
        self._lock = threading.Lock()
        self._last_used = time.monotonic()
        if idle_timeout > 0:
            threading.Thread(target=self._reaper, daemon=True, name="artigo2md-reaper").start()

    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.is_alive()

    def _start(self) -> None:
        parent, child = self._ctx.Pipe()
        self._proc = self._ctx.Process(target=_child_main, args=(child,),
                                       name="artigo2md-worker", daemon=True)
        self._proc.start()
        child.close()
        self._conn = parent

    def _stop(self) -> None:
        proc, conn = self._proc, self._conn
        self._proc = self._conn = None
        if conn is not None:
            try:
                conn.send(None)
            except Exception:
                pass
        if proc is not None:
            proc.join(5)
            if proc.is_alive():
                proc.terminate()
                proc.join(5)
        if conn is not None:
            conn.close()

    def kill(self) -> None:
        """Encerra o filho de imediato, sem esperar o lock (encerramento por prazo, FR-013).

        Quem está em `convert` percebe a morte do filho e levanta `WorkerCrashed`.
        """
        proc = self._proc
        if proc is not None and proc.is_alive():
            proc.kill()
            proc.join(5)

    def convert(
        self,
        path: str,
        engine: str,
        progress: Callable[[str], None],
        tick: Callable[[], None] | None = None,
        timeout: float = 0,
    ) -> dict:
        """Converte `path` no processo auxiliar.

        `tick` (opcional) é chamado a cada `TICK_INTERVAL` s enquanto se espera o filho (mantém o heartbeat
        de quem usa esta chamada numa conexão AMQP). `timeout` > 0 limita a conversão: ao estourar, o filho
        é encerrado e levanta `ConversionTimeout`. Sem `tick` e sem `timeout` o comportamento é o de sempre.
        """
        with self._lock:
            deadline = time.monotonic() + timeout if timeout and timeout > 0 else None
            try:
                if not self.alive:
                    progress("Carregando o conversor")
                    self._start()
                self._conn.send((str(path), engine))
                while True:
                    wait = None
                    if tick is not None or deadline is not None:
                        wait = TICK_INTERVAL
                        if deadline is not None:
                            wait = max(0.0, min(wait, deadline - time.monotonic()))
                    try:
                        ready = wait is None or self._conn.poll(wait)
                        message = self._conn.recv() if ready else None
                    except (EOFError, OSError):
                        self._stop()
                        raise WorkerCrashed(CRASH_MESSAGE) from None
                    if message is None:  # a espera terminou sem resposta do filho
                        if deadline is not None and time.monotonic() >= deadline:
                            self._kill_and_stop()
                            raise ConversionTimeout(timeout)
                        if tick is not None:
                            self._callback(tick)
                        continue
                    kind, payload = message
                    if kind == "progress":
                        self._callback(progress, payload)
                    elif kind == "done":
                        return payload
                    else:
                        raise ConversionError(payload, kind)
            finally:
                self._last_used = time.monotonic()

    def _callback(self, func: Callable, *args) -> None:
        """Chama `tick`/`progress`; se levantarem, o filho é encerrado para não deixar resposta velha no cano."""
        try:
            func(*args)
        except BaseException:
            self._kill_and_stop()
            raise

    def _kill_and_stop(self) -> None:
        proc = self._proc
        if proc is not None and proc.is_alive():
            proc.kill()
        self._stop()

    def _reaper(self) -> None:
        interval = max(0.2, min(30.0, self.idle_timeout / 4))
        while True:
            time.sleep(interval)
            if not self.alive or time.monotonic() - self._last_used < self.idle_timeout:
                continue
            if self._lock.acquire(blocking=False):  # ocupado = convertendo; tenta depois
                try:
                    if self.alive and time.monotonic() - self._last_used >= self.idle_timeout:
                        self._stop()
                finally:
                    self._lock.release()

    def shutdown(self) -> None:
        with self._lock:
            self._stop()
