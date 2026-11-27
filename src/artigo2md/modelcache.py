"""Cache dos modelos do Docling: verbos `status` e `download` do subcomando `artigo2md models`.

Contrato: docs/specs/desktop-stack-control/contracts/models-cli.md (FR-036, FR-039). Cada evento é uma
linha JSON em stdout, chaves em snake_case, com `schema_version`. Nenhuma linha carrega token, credencial
nem o texto cru de uma exceção: as mensagens (em português) são fixas por motivo.

Este módulo NUNCA importa `docling` (só `huggingface_hub`, sob demanda): importar `modelcache` é barato e
não toca o caminho de conversão. O destino é o cache padrão do Hugging Face, o mesmo que a conversão usa.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

SCHEMA_VERSION = 1

# Códigos de saída do subcomando (não colidem com os do worker, 0–5).
EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2
EXIT_PLATFORM = 6
EXIT_DISK = 7
EXIT_INTERRUPTED = 130

PROGRESS_INTERVAL_SECONDS = 1.0


@dataclass(frozen=True)
class ModelSpec:
    id: str
    repo_id: str
    revision: str


# Conferida por teste contra o Docling instalado (tests/test_models_cli.py::test_models_match_docling).
MODELS: tuple[ModelSpec, ...] = (
    # docling/datamodel/stage_model_specs.py (spec "Layout Heron")
    ModelSpec("layout", "docling-project/docling-layout-heron", "main"),
    # docling/models/stages/table_structure/table_structure_model.py (`download_models`)
    ModelSpec("tableformer", "docling-project/docling-models", "v2.3.0"),
)

REASON_MESSAGES = {
    "no_network": "Sem acesso à internet: não foi possível baixar os modelos. Conecte-se e tente de novo.",
    "disk_full": "Espaço em disco insuficiente para baixar os modelos. Libere espaço e tente de novo.",
    "interrupted": "O download dos modelos foi interrompido. Tente de novo; o que já foi baixado é reaproveitado.",
    "other": "Não foi possível baixar os modelos de conversão. Tente de novo.",
}

Emit = Callable[[dict[str, Any]], None]


def platform_supported() -> bool:
    """A preparação de modelos só é suportada no macOS (FR-036)."""
    return sys.platform == "darwin"


def _hub() -> Any:
    """Importa `huggingface_hub` sob demanda, com a telemetria desligada ANTES do import."""
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    import huggingface_hub
    from huggingface_hub import constants, utils  # noqa: F401 - submódulos não são atributos até importados

    return huggingface_hub


def cache_dir() -> str:
    """Pasta do cache do Hugging Face (`HF_HUB_CACHE`, `HF_HOME`, `XDG_CACHE_HOME`; padrão ~/.cache)."""
    return str(_hub().constants.HF_HUB_CACHE)


def emit_json(event: dict[str, Any]) -> None:
    """Escreve um evento como uma linha JSON em stdout (só JSON Lines, sem nada além)."""
    line = json.dumps({"schema_version": SCHEMA_VERSION, **event}, ensure_ascii=False)
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


def is_present(spec: ModelSpec, cache: str | None = None, *, snapshot_download: Callable[..., str] | None = None) -> bool:
    """O modelo está completo no cache? Sem rede; snapshot incompleto ou ausente conta como ausente."""
    download = snapshot_download or _hub().snapshot_download
    try:
        path = download(spec.repo_id, revision=spec.revision, cache_dir=cache or cache_dir(), local_files_only=True)
    except Exception:  # noqa: BLE001 - qualquer falha de leitura local (ausente, incompleto) = ausente
        return False
    folder = Path(path)
    return folder.is_dir() and any(folder.iterdir())


def status(cache: str | None = None, *, snapshot_download: Callable[..., str] | None = None) -> dict[str, Any]:
    cache = cache or cache_dir()
    return {
        "event": "status",
        "cache_dir": cache,
        "models": [
            {"id": m.id, "present": is_present(m, cache, snapshot_download=snapshot_download)} for m in MODELS
        ],
    }


def classify_failure(exc: BaseException) -> str:
    """Traduz uma exceção do download para o `reason` do contrato (sem expor o texto da exceção)."""
    if isinstance(exc, KeyboardInterrupt):
        return "interrupted"
    if isinstance(exc, OSError) and getattr(exc, "errno", None) == 28:  # ENOSPC
        return "disk_full"
    network: tuple[type[BaseException], ...] = (ConnectionError, TimeoutError)
    try:
        import httpx

        network += (httpx.TransportError,)
    except ImportError:  # pragma: no cover - httpx é dependência do huggingface_hub
        pass
    try:
        from huggingface_hub import errors as hub_errors

        network += (hub_errors.DryRunError, hub_errors.LocalEntryNotFoundError)
    except ImportError:  # pragma: no cover
        pass
    if isinstance(exc, network):
        return "no_network"
    return "other"


def _make_progress_class(base: type, emit: Emit, model_id: str, total_hint: int | None, clock: Callable[[], float]):
    """Subclasse de `huggingface_hub.utils.tqdm` que não desenha barra e emite `progress` (≤ 1 por segundo)."""
    state = {"last": None, "best": 0}

    class ProgressTqdm(base):  # type: ignore[misc, valid-type]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            kwargs["disable"] = True  # nunca desenha barra
            super().__init__(*args, **kwargs)
            self._bytes = int(kwargs.get("initial") or 0)
            self._is_bytes = kwargs.get("unit") == "B"

        def update(self, n: float | None = 1) -> bool | None:
            if not self._is_bytes:
                return None
            self._bytes += int(n or 0)
            state["best"] = max(state["best"], self._bytes)  # barras de transferência e de escrita: vale a maior
            now = clock()
            if state["last"] is None or now - state["last"] >= PROGRESS_INTERVAL_SECONDS:
                state["last"] = now
                total = total_hint if total_hint is not None else (int(self.total) if self.total else None)
                emit({"event": "progress", "id": model_id, "downloaded_bytes": state["best"], "total_bytes": total})
            return None

    return ProgressTqdm


def _free_bytes(cache: str) -> int:
    probe = Path(cache)
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    return shutil.disk_usage(probe).free


def download(
    emit: Emit,
    *,
    cache: str | None = None,
    snapshot_download: Callable[..., Any] | None = None,
    free_bytes: Callable[[str], int] | None = None,
    clock: Callable[[], float] = time.monotonic,
    base_tqdm: type | None = None,
) -> int:
    """Baixa só os modelos ausentes, emitindo os eventos do contrato. Devolve o código de saída."""
    hub = _hub()
    fetch = snapshot_download or hub.snapshot_download
    cache = cache or cache_dir()
    missing = [m for m in MODELS if not is_present(m, cache, snapshot_download=fetch)]

    def finish(status_: str, reason: str | None, message: str, code: int) -> int:
        emit({"event": "result", "status": status_, "reason": reason, "message": message})
        return code

    if not missing:
        emit({"event": "plan", "total_bytes": 0, "missing": []})
        return finish("done", None, "Os modelos de conversão já estão no computador.", EXIT_OK)

    sizes: dict[str, int | None] = {}
    try:
        for m in missing:
            files = fetch(m.repo_id, revision=m.revision, cache_dir=cache, dry_run=True)
            pending = [f for f in files if getattr(f, "will_download", True)]
            sizes[m.id] = None if any(f.file_size is None for f in pending) else sum(f.file_size for f in pending)
    except KeyboardInterrupt:
        return EXIT_INTERRUPTED
    except Exception as exc:  # noqa: BLE001
        reason = classify_failure(exc)
        return finish("failed", reason, REASON_MESSAGES[reason], EXIT_FAILED)

    total = None if any(v is None for v in sizes.values()) else sum(v for v in sizes.values() if v is not None)
    emit({"event": "plan", "total_bytes": total, "missing": [m.id for m in missing]})

    free = (free_bytes or _free_bytes)(cache)
    disk_ok = total is None or free >= total
    emit({"event": "disk", "free_bytes": free, "required_bytes": total, "ok": disk_ok})
    if not disk_ok:
        return finish("failed", "disk_full", REASON_MESSAGES["disk_full"], EXIT_DISK)

    base = base_tqdm or hub.utils.tqdm
    for m in missing:
        progress = _make_progress_class(base, emit, m.id, sizes.get(m.id), clock)
        try:
            fetch(m.repo_id, revision=m.revision, cache_dir=cache, tqdm_class=progress)
        except KeyboardInterrupt:
            return EXIT_INTERRUPTED
        except Exception as exc:  # noqa: BLE001
            reason = classify_failure(exc)
            return finish("failed", reason, REASON_MESSAGES[reason], EXIT_FAILED)
        emit({"event": "model_done", "id": m.id})
    return finish("done", None, "Modelos de conversão prontos.", EXIT_OK)


def human_line(event: dict[str, Any]) -> str | None:
    """Texto em português para o uso sem `--json` (uma linha por evento relevante)."""
    kind = event.get("event")
    if kind == "status":
        return "\n".join(f"{m['id']}: {'presente' if m['present'] else 'ausente'}" for m in event["models"])
    if kind == "plan":
        return f"Modelos a baixar: {', '.join(event['missing']) or 'nenhum'}"
    if kind == "disk" and not event["ok"]:
        return "Espaço em disco insuficiente."
    if kind == "progress":
        total = event.get("total_bytes")
        pct = f" ({event['downloaded_bytes'] * 100 // total}%)" if total else ""
        return f"{event['id']}: {event['downloaded_bytes']} bytes{pct}"
    if kind == "model_done":
        return f"{event['id']}: concluído"
    if kind == "result":
        return event["message"]
    return None


def emit_human(event: dict[str, Any]) -> None:
    line = human_line(event)
    if line:
        print(line, flush=True)


def run(verb: str, *, as_json: bool) -> int:
    """Executa um verbo do subcomando `models`. Plataforma fora do macOS = exit 6 (mensagem em stderr)."""
    if not platform_supported():
        print("A preparação de modelos só é suportada no macOS.", file=sys.stderr)
        return EXIT_PLATFORM
    emit = emit_json if as_json else emit_human
    if verb == "status":
        emit(status())
        return EXIT_OK
    if verb == "download":
        return download(emit)
    print(f"verbo desconhecido: {verb}", file=sys.stderr)
    return EXIT_USAGE
