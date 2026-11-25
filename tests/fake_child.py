"""Alvos de processo filho falsos para `tests/test_worker.py` (importáveis pelo `spawn`; sem motores)."""
from __future__ import annotations

import os
import time


def echo_main(conn) -> None:
    """Responde `done` com um Markdown fixo e o pid do filho."""
    while True:
        try:
            msg = conn.recv()
        except (EOFError, KeyboardInterrupt):
            return
        if msg is None:
            return
        path, engine = msg
        conn.send(("progress", "meio"))
        conn.send(("done", {"markdown": f"# {path}", "engine": engine, "report": {"pid": os.getpid()}}))


def slow_main(conn) -> None:
    """Fica 0,6 s trabalhando (com `progress`) antes de responder."""
    msg = conn.recv()
    if msg is None:
        return
    time.sleep(0.6)
    conn.send(("done", {"markdown": "# lento", "engine": "x", "report": {}}))


def hang_main(conn) -> None:
    """Recebe o job e nunca responde."""
    conn.recv()
    time.sleep(60)


def die_main(conn) -> None:
    """Morre sem responder (como o kernel matando por falta de memória)."""
    conn.recv()
    os._exit(9)


def invalid_pdf_main(conn) -> None:
    conn.recv()
    conn.send(("invalid_pdf", "a.pdf não é um PDF válido."))


def error_main(conn) -> None:
    conn.recv()
    conn.send(("error", "Nenhum motor conseguiu converter a.pdf."))


def env_main(conn) -> None:
    """Devolve, no Markdown, o valor que o filho enxerga das variáveis de credencial do RabbitMQ."""
    import json

    msg = conn.recv()
    if msg is None:
        return
    seen = {name: os.environ.get(name) for name in ("RABBITMQ_DEFAULT_USER", "RABBITMQ_DEFAULT_PASS")}
    conn.send(("done", {"markdown": json.dumps(seen), "engine": "x", "report": {}}))


def real_main_slow_convert(conn) -> None:
    """Roda o `_child_main` REAL (com o tratamento de sinais dele), trocando só a conversão por uma espera de 1,5 s.

    O alvo é este (importável pelo `spawn`) e não o próprio `_child_main` porque a troca de `router.convert`
    precisa acontecer dentro do processo filho.
    """
    import types

    from artigo2md import router, worker

    def slow_convert(path, engine="auto", progress=None):
        progress("convertendo")
        time.sleep(1.5)
        return types.SimpleNamespace(markdown="# concluído", engine=engine, report=lambda: {"pid": os.getpid()})

    router.convert = slow_convert
    worker._child_main(conn)
