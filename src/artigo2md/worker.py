"""Processo auxiliar de conversão, que se encerra sozinho quando fica ocioso.

Os modelos (Docling, pymupdf4llm) ocupam de 1 a 2 GB depois de carregados, e o Python
não devolve essa memória ao sistema. Por isso as conversões da interface web rodam num
processo separado: em lote ele continua vivo e rápido; parado por `idle_timeout`
segundos, ele termina e a RAM volta inteira para o sistema.
"""
from __future__ import annotations

import multiprocessing as mp
import threading
import time
from typing import Callable


def _child_main(conn) -> None:
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

    def convert(self, path: str, engine: str, progress: Callable[[str], None]) -> dict:
        with self._lock:
            try:
                if not self.alive:
                    progress("Carregando o conversor")
                    self._start()
                self._conn.send((str(path), engine))
                while True:
                    try:
                        kind, payload = self._conn.recv()
                    except (EOFError, OSError):
                        self._stop()
                        raise RuntimeError(
                            "O processo de conversão terminou inesperadamente "
                            "(possível falta de memória). Tente de novo ou use outro motor."
                        )
                    if kind == "progress":
                        progress(payload)
                    elif kind == "done":
                        return payload
                    else:
                        raise RuntimeError(payload)
            finally:
                self._last_used = time.monotonic()

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
