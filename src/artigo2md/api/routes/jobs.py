"""Rotas de enfileiramento e reprocessamento (`contracts/http-api.md`, "Jobs"; FR-010 a FR-012, FR-026, US2, US5).

Fluxo por documento (`research.md`, Decision 8): (1) `create_job` (COMMIT próprio); (2) publicação da mensagem de job,
com confirmação do broker; (3) confirmada -> `mark_job_published`; não confirmada (broker fora, recusa, prazo
estourado) -> `fail_job` com motivo em português. O job nunca fica `pending` sem mensagem confirmada por mais que a
janela de uma queda da própria API, e essa janela é fechada na partida/reconexão seguinte (`list_unpublished_jobs`,
tarefa 7.6).

`pdf_path` da mensagem é o caminho ABSOLUTO DO HOST gravado no documento (FR-011; o worker é nativo e lê o disco do
host). A conversão host -> container (`to_container_path`) vale só para ler o `markdown_path` devolvido pelo worker.

Lote (S-05): `PublishBatch` faz os documentos restantes falharem na hora depois da primeira publicação não confirmada,
sem nova espera de prazo por documento.

Fluxo ao vivo (`live-events.md`): a criação emite `job_state pending` (`attempt` nulo); publicação não confirmada
emite `job_state failed`; o cancelamento emite `job_state cancelled`. Sempre DEPOIS da gravação.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, StrictBool, StrictInt

from ...jobqueue import schema
from ...store import errors as store_errors
from ...store.models import REQUESTED_ENGINES
from ..broker import PublishBatch, PublishFailed
from ..deps import Services, get_services
from ..errors import ApiError, error_body
from ..live import MSG_JOB_CREATED

logger = logging.getLogger("artigo2md.api")

MAX_ID = 2**63 - 1  # bigint do banco
MAX_DOCUMENT_IDS = 10_000  # FR-010 (block-001/dec-023)

DocumentId = Annotated[int, Path(gt=0, le=MAX_ID)]
JobId = Annotated[int, Path(gt=0, le=MAX_ID)]
ProjectId = Annotated[int, Path(gt=0, le=MAX_ID)]

router = APIRouter(tags=["jobs"])

FAILED_SUMMARY = "Não foi possível enviar o job para a fila. O documento pode ser enfileirado de novo."
FAILED_MESSAGE = "Falha ao enviar o job para a fila."
_BROKER_DOWN = "A fila de conversão não está disponível no momento."
STACK_HINT = "Se a fila estiver fora do ar, suba a stack com: sh stack/up.sh"  # FR-032
ACTIVE_JOB_SKIPPED = "O documento já tem um job pendente ou em andamento."


class JobBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    engine: str = "auto"


class ProjectJobsBody(BaseModel):
    """Exatamente um entre `document_ids` e `not_processed` (conferido na rota: combinação inválida = 400)."""

    model_config = ConfigDict(extra="forbid")
    engine: str = "auto"
    document_ids: list[StrictInt] | None = None
    not_processed: StrictBool | None = None


# ── serialização explícita (`published_at` não é exposto) ───────────────────────
def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def job_to_json(job: Any) -> dict[str, Any]:
    return {
        "id": job.id,
        "project_id": job.project_id,
        "document_id": job.document_id,
        "requested_engine": job.requested_engine,
        "status": job.status,
        "requested_at": _iso(job.requested_at),
        "started_at": _iso(job.started_at),
        "finished_at": _iso(job.finished_at),
        "error_summary": job.error_summary,
    }


def quality_to_json(quality: Any) -> dict[str, Any] | None:
    if quality is None:
        return None
    return {
        "score": quality.score,
        "accepted": quality.accepted,
        "tables_found": quality.tables_found,
        "tables_expected": quality.tables_expected,
        "chars_per_page": quality.chars_per_page,
        "issues": list(quality.issues),
    }


def attempt_to_json(attempt: Any) -> dict[str, Any]:
    return {
        "id": attempt.id,
        "job_id": attempt.job_id,
        "position": attempt.position,
        "engine": attempt.engine,
        "seconds": attempt.seconds,
        "quality": quality_to_json(attempt.quality),
        "error": attempt.error,
        "created_at": _iso(attempt.created_at),
    }


CANCEL_MESSAGE = "Job cancelado pelo usuário."


# ── regras de pedido ────────────────────────────────────────────────────────────
def check_engine(engine: str) -> None:
    if engine not in REQUESTED_ENGINES:
        raise ApiError("validation_failed", f"O motor precisa ser um de: {', '.join(REQUESTED_ENGINES)}.")


class _Unavailable:
    """Publicadora ausente (ainda não iniciada): toda publicação falha na hora, como a publicadora desconectada."""

    async def publish(self, message: dict[str, Any]) -> None:
        raise PublishFailed(_BROKER_DOWN)


def _publisher(services: Services) -> Any:
    return services.broker if services.broker is not None else _Unavailable()


async def _finalize_publish(
    services: Services, store: Any, job: Any, publisher: Any, document_path: str, engine: str
) -> tuple[Any, bool]:
    """Enquanto publica, o job fica em `services.inflight` (a republicação de fundo o pula); ver `_publish_outcome`."""
    services.inflight.add(job.id)
    services.live.job_state(job.project_id, job.id, job.document_id, "pending", MSG_JOB_CREATED)
    try:
        outcome, published = await _publish_outcome(store, job, publisher, document_path, engine)
    finally:
        services.inflight.discard(job.id)
    if not published and outcome.status == "failed":
        services.live.job_state(outcome.project_id, outcome.id, outcome.document_id, "failed", FAILED_MESSAGE)
    return outcome, published


async def _publish_outcome(store: Any, job: Any, publisher: Any, document_path: str, engine: str) -> tuple[Any, bool]:
    """Publica o job e grava o desfecho. Devolve `(job, publicado)`. Erro do banco ao gravar o desfecho NÃO desfaz a
    publicação (a mensagem já está na fila) e, se a publicação falhou, o job `pending` sem `published_at` é republicado
    na reconexão (R-06)."""
    message = schema.build_job(str(job.id), document_path, engine, 1)
    try:
        await publisher.publish(message)
    except PublishFailed as failure:
        try:
            summary = f"{FAILED_SUMMARY} Motivo: {failure.reason} {STACK_HINT}"
            failed, _event = await store.fail_job(job.id, summary, FAILED_MESSAGE)
        except (store_errors.InvalidJobTransitionError, store_errors.JobNotFoundError):
            failed = await store.get_job(job.id)  # cancelado/excluído no intervalo: devolve o estado real
        return failed, False
    try:
        job = await store.mark_job_published(job.id)
    except store_errors.StoreError as exc:
        logger.warning("Job %s publicado, mas a marcação de publicação falhou (%s); será conferido na reconexão.",
                       job.id, type(exc).__name__)
    return job, True


# ── POST /v1/documents/{id}/jobs ────────────────────────────────────────────────
@router.post("/documents/{document_id}/jobs", status_code=202)
async def enqueue_document(
    document_id: DocumentId, body: JobBody | None = None, services: Services = Depends(get_services)
) -> JSONResponse:
    engine = body.engine if body is not None else "auto"
    store = await services.get_store()
    document = await store.get_document(document_id)  # 404 antes de qualquer recusa de domínio
    check_engine(engine)
    job = await store.create_job(document.id, engine)  # 409 active_job_exists se já há job ativo
    job, published = await _finalize_publish(services, store, job, _publisher(services), document.path, engine)
    if not published and job.status == "failed":
        content = error_body("broker_unavailable")
        content["job"] = job_to_json(job)
        return JSONResponse(content, status_code=503)
    return JSONResponse(job_to_json(job), status_code=202)


# ── POST /v1/projects/{id}/jobs ─────────────────────────────────────────────────
def _target_mode(body: ProjectJobsBody) -> None:
    """Exatamente um entre `document_ids` (lista não vazia) e `not_processed` (`true`); senão 400."""
    if body.document_ids is not None and body.not_processed is not None:
        raise ApiError("invalid_request", "Informe document_ids ou not_processed, não os dois.")
    if body.document_ids is None and body.not_processed is None:
        raise ApiError("invalid_request", "Informe document_ids ou not_processed.")
    if body.document_ids is not None and not body.document_ids:
        raise ApiError("invalid_request", "O campo document_ids não pode ser vazio.")
    if body.not_processed is not None and body.not_processed is not True:
        raise ApiError("invalid_request", "O campo not_processed só aceita o valor true.")
    if body.document_ids is not None and len(body.document_ids) > MAX_DOCUMENT_IDS:
        raise ApiError(
            "payload_too_large",
            f"O pedido traz mais de {MAX_DOCUMENT_IDS} documentos; envie em lotes menores.",
        )


@router.post("/projects/{project_id}/jobs", status_code=202)
async def enqueue_project(
    project_id: ProjectId, body: ProjectJobsBody, services: Services = Depends(get_services)
) -> dict[str, Any]:
    _target_mode(body)
    store = await services.get_store()
    await store.get_project(project_id)  # 404
    statuses = await store.list_documents(project_id)
    if body.document_ids is not None:
        known = {item.document_id: item for item in statuses}
        wanted = list(dict.fromkeys(body.document_ids))  # sem repetição, na ordem pedida
        if any(item not in known for item in wanted):
            raise ApiError("not_found", "Algum documento informado não existe neste projeto.")  # nada é criado
        targets = [known[item] for item in wanted]
    else:
        targets = [item for item in statuses if item.status == "not_processed"]
    check_engine(body.engine)

    publisher = PublishBatch(_publisher(services))
    enqueued: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    if body.not_processed is not None:
        # US2-2/FR-012: documento com job pendente ou em andamento é ignorado com aviso, nunca em silêncio.
        for item in statuses:
            if item.status in ("pending", "running"):
                skipped.append({"document_id": item.document_id, "message": ACTIVE_JOB_SKIPPED})
    for target in targets:
        try:
            job = await store.create_job(target.document_id, body.engine)
        except store_errors.ActiveJobExistsError as exc:
            skipped.append({"document_id": target.document_id, "message": str(exc)})
            continue
        job, published = await _finalize_publish(services, store, job, publisher, target.path, body.engine)
        (enqueued if published else failed).append(job_to_json(job))
    return {"enqueued": enqueued, "skipped": skipped, "failed": failed}


# ── GET /v1/jobs/{id}, cancelamento e tentativas (tarefa 7.5) ───────────────────
@router.get("/jobs/{job_id}")
async def get_job(job_id: JobId, services: Services = Depends(get_services)) -> dict[str, Any]:
    store = await services.get_store()
    return job_to_json(await store.get_job(job_id))


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(job_id: JobId, services: Services = Depends(get_services)) -> dict[str, Any]:
    """`pending|running -> cancelled` (inclusive job órfão, sem worker vivo, FR-018). Job final: 409
    `invalid_transition`. Eventos tardios do job cancelado são descartados pelo consumidor (FR-016)."""
    store = await services.get_store()
    job, _event = await store.cancel_job(job_id, CANCEL_MESSAGE)
    services.live.job_state(job.project_id, job.id, job.document_id, "cancelled", CANCEL_MESSAGE)
    return job_to_json(job)


@router.get("/jobs/{job_id}/attempts")
async def list_attempts(job_id: JobId, services: Services = Depends(get_services)) -> dict[str, Any]:
    store = await services.get_store()
    return {"attempts": [attempt_to_json(item) for item in await store.list_attempts(job_id)]}
