"""Cenário de `tests/test_worker.py` (FR-013): roda como processo próprio, líder de um grupo de processos.

O teste envia SIGINT ao GRUPO (como o Ctrl+C do terminal) enquanto o filho converte. Aqui o pai absorve o sinal
(quem decide o que fazer com ele é o serviço, não este cenário) e imprime, ao fim, um JSON com o desfecho.
"""
import json
import signal
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import fake_child  # noqa: E402
from artigo2md import worker as worker_mod  # noqa: E402


def main() -> None:
    # Um handler de Python (e não SIG_IGN): a disposição "ignorar" seria herdada pelo filho `spawn` e mascararia
    # o defeito; o serviço real também instala handler próprio.
    signal.signal(signal.SIGINT, lambda *_: None)
    worker_mod._child_main = fake_child.real_main_slow_convert
    worker = worker_mod.ConversionWorker(idle_timeout=0)
    outcome: dict = {}

    def run() -> None:
        def progress(message: str) -> None:
            if message == "convertendo":
                print("READY", flush=True)

        try:
            outcome["out"] = worker.convert("/x/a.pdf", "auto", progress)
        except BaseException as exc:  # noqa: BLE001 - o desfecho inteiro vai para o teste
            outcome["err"] = type(exc).__name__

    thread = threading.Thread(target=run)
    thread.start()
    thread.join(30)
    outcome["child_alive_after"] = worker.alive
    worker.shutdown()
    print(json.dumps({"err": outcome.get("err"), "markdown": outcome.get("out", {}).get("markdown"),
                      "child_alive_after": outcome["child_alive_after"]}), flush=True)


if __name__ == "__main__":  # o `spawn` reimporta este arquivo no filho
    main()
