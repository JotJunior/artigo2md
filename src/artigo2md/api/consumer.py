"""Tratamento dos eventos da fila de conversão (`research.md`, Decision 4; FR-013 a FR-017; `data-model.md`, "Mapeamento
evento da fila -> gravação").

`EventHandler.handle(body)` recebe o CORPO de uma mensagem de `artigo2md.events.all` e decide, sem nenhum acesso ao AMQP:

- **aplicar** no banco pela camada `store` (`progress`, `result`, `error`); ou
- **descartar de propósito** (fora do contrato, `schema_version` não suportada, job inexistente/final, projeto
  arquivado/excluído, duplicata), com registro em português que NUNCA inclui o corpo da mensagem (S-02, FR-016).

Banco indisponível é a ÚNICA condição que NÃO descarta: `handle` levanta `TransientStoreError` e quem consome repete a
MESMA mensagem depois de um recuo, sem `ack` (FR-017). Qualquer outra falha inesperada é registrada e a mensagem é
descartada, para uma mensagem venenosa nunca bloquear a fila.

`markdown_path` do `result` só é aceito se `to_container_path` o confina ao diretório de dados; caso contrário o job
vai a `failed` com motivo em português e o caminho recebido não é gravado nem lido (FR-025, US4-6).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from ..jobqueue import schema
from ..store import errors as store_errors
from ..store.models import QualityRecord
from .deps import Services
from .errors import ApiError
from .live import EVENT_PROGRESS, EVENT_WARNING, MSG_JOB_STARTED, job_state_data, progress_data
from .paths import to_container_path

logger = logging.getLogger("artigo2md.api")

MAX_TEXT_CHARS = 2000  # valor de 1.1.2: truncamento dos textos livres gravados dos eventos
FINAL_STATUSES = frozenset({"succeeded", "failed", "cancelled"})

# Diagnóstico do descompasso entre `--data-dir` do worker e `ARTIGO2MD_DATA_DIR` da API (research.md, Decision 10).
# Não ecoa o caminho recebido: o texto vai ao `error_summary` do job e, daí, às respostas HTTP.
OUTSIDE_DATA_DIR_SUMMARY = (
    "O Markdown foi gravado fora da pasta de dados da API. Confira se o --data-dir do worker é o mesmo caminho de "
    "ARTIGO2MD_DATA_DIR em stack/.env."
)
DATA_DIR_UNAVAILABLE_SUMMARY = (
    "A pasta de dados da API não está configurada; o resultado não pôde ser aceito. Confira ARTIGO2MD_DATA_DIR em "
    "stack/.env."
)


class TransientStoreError(Exception):
    """O banco não está disponível agora: a mesma mensagem deve ser tentada de novo mais tarde."""


@dataclass(frozen=True)
class Outcome:
    """Desfecho do tratamento: `applied` (gravado) ou `discarded` (descarte intencional, com o motivo)."""

    action: str
    reason: str = ""
    event: dict[str, Any] | None = None
    job: Any = None
    job_started: bool = False  # o evento levou o job de `pending` para `running`

    @property
    def applied(self) -> bool:
        return self.action == "applied"


def _discard(reason: str, event: dict[str, Any] | None = None) -> Outcome:
    return Outcome("discarded", reason, event)


def clip_text(value: str) -> str:
    return value if len(value) <= MAX_TEXT_CHARS else value[: MAX_TEXT_CHARS - 1] + "…"


def parse_job_id(value: str) -> int | None:
    """`job_id` da fila é `str(jobs.id)`: inteiro decimal positivo, sem zeros à esquerda."""
    if not value.isascii() or not value.isdigit() or value.startswith("0"):
        return None
    number = int(value)
    return number if number <= 2**63 - 1 else None


def _quality(item: dict[str, Any]) -> QualityRecord:
    return QualityRecord(
        score=item["score"], accepted=item["accepted"], tables_found=item["tables_found"],
        tables_expected=item["tables_expected"], chars_per_page=item["chars_per_page"],
        issues=tuple(clip_text(text) for text in item["issues"]),
    )


def _winning_quality(event: dict[str, Any]) -> QualityRecord | None:
    """Qualidade do primeiro item de `attempts` do motor vencedor com `quality` não nula."""
    for item in event["report"]["attempts"]:
        if item["engine"] == event["engine"] and item["quality"] is not None:
            return _quality(item["quality"])
    return None


def _is_transient(exc: BaseException) -> bool:
    if isinstance(exc, (store_errors.DatabaseUnavailableError, store_errors.StoreConfigError,
                        store_errors.MigrationError)):
        return True
    return isinstance(exc, ApiError) and exc.code == "database_unavailable"


class EventHandler:
    """Traduz um evento da fila em gravação. Sem estado próprio além das configurações e do `Store`."""

    def __init__(self, services: Services) -> None:
        self._services = services

    async def handle(self, body: bytes) -> Outcome:
        try:
            outcome = await self._handle(body)
            self._broadcast(outcome)
            return outcome
        except TransientStoreError:
            raise
        except Exception as exc:
            if _is_transient(exc):
                raise TransientStoreError from exc
            # Só o tipo vai ao log: a mensagem do driver pode trazer texto sensível (FR-031).
            logger.error("Evento descartado por erro inesperado no tratamento (%s).", type(exc).__name__)
            return _discard("erro-inesperado")

    # ── fluxo ao vivo ───────────────────────────────────────────────────────────
    def _broadcast(self, outcome: Outcome) -> None:
        """Repassa ao fluxo ao vivo SÓ o que foi gravado (`live-events.md`); descarte não gera evento. É sinal de
        interface: qualquer falha aqui é registrada e nunca desfaz nem repete o tratamento do evento."""
        if not outcome.applied or outcome.event is None or outcome.job is None:
            return
        try:
            self._emit_live(outcome)
        except Exception as exc:
            logger.warning("Falha ao repassar o evento ao fluxo ao vivo (%s).", type(exc).__name__)

    def _emit_live(self, outcome: Outcome) -> None:
        live, event, job = self._services.live, outcome.event, outcome.job
        attempt = event["attempt"]

        def state(status: str, message: str) -> None:
            live.publish(job.project_id, "job_state", job_state_data(job.id, job.document_id, status, message, attempt))

        if outcome.job_started:
            state("running", MSG_JOB_STARTED)
        if outcome.reason == "progress":
            live.publish(job.project_id, EVENT_PROGRESS, progress_data(
                job.id, job.document_id, attempt, clip_text(event["message"]), event["emitted_at"]))
        elif outcome.reason == "error-passageiro":
            live.publish(job.project_id, EVENT_WARNING, progress_data(
                job.id, job.document_id, attempt, clip_text(event["reason"]), event["emitted_at"]))
        elif outcome.reason == "error-definitivo":
            state("failed", clip_text(f"Conversão falhou: {clip_text(event['reason'])}"))
        elif outcome.reason == "resultado":
            state("succeeded", clip_text(f"Conversão concluída com o motor {event['engine']}."))
        elif outcome.reason == "resultado-recusado":
            state("failed", "Conversão falhou: caminho de Markdown recusado.")

    # ── validação ───────────────────────────────────────────────────────────────
    async def _handle(self, body: bytes) -> Outcome:
        if not isinstance(body, (bytes, bytearray)) or len(body) > schema.MAX_BODY_BYTES:
            logger.warning("Evento descartado: corpo acima do tamanho máximo permitido.")
            return _discard("corpo-grande")
        if schema.json_depth(bytes(body)) > schema.MAX_JSON_DEPTH:
            logger.warning("Evento descartado: JSON mais profundo que o máximo permitido.")
            return _discard("json-profundo")
        try:
            event = schema.validate_event(bytes(body))
        except schema.ContractError as exc:
            if exc.code == "unsupported_schema_version":
                logger.warning("Evento descartado: versão de esquema não suportada.")
                return _discard("versao-nao-suportada")
            logger.warning("Evento descartado: fora do contrato da fila (%s).", exc.code)
            return _discard("fora-do-contrato")
        job_id = parse_job_id(event["job_id"])
        if job_id is None:
            logger.warning("Evento descartado: job inexistente.")
            return _discard("job-inexistente", event)
        kind = event["type"]
        if kind == schema.TYPE_PROGRESS:
            return await self._progress(job_id, event)
        if kind == schema.TYPE_RESULT:
            return await self._result(job_id, event)
        return await self._error(job_id, event)

    async def _open(self, session: Any, job_id: int, event: dict[str, Any]) -> tuple[Any, Outcome | None]:
        """Job do evento, ou o desfecho de descarte (inexistente, final, projeto arquivado)."""
        try:
            job = await session.get_job(job_id)
        except store_errors.JobNotFoundError:
            logger.warning("Evento descartado: job inexistente.")
            return None, _discard("job-inexistente", event)
        if job.status in FINAL_STATUSES:
            logger.info("Evento descartado: o job %s já está em estado final.", job_id)
            return job, _discard("job-final", event)
        project = await session.get_project(job.project_id)
        if project.status != "active":
            logger.info("Evento descartado: o projeto do job %s está arquivado.", job_id)
            return job, _discard("projeto-arquivado", event)
        return job, None

    @staticmethod
    def _became_final(job_id: int, event: dict[str, Any]) -> Outcome:
        """O job ficou final (ex.: cancelado) entre a leitura e a transição: notificação repetida, sem erro (FR-016).
        A transação foi desfeita por inteiro, então nada parcial fica gravado."""
        logger.info("Evento descartado: o job %s ficou em estado final durante o tratamento.", job_id)
        return _discard("job-final", event)

    # ── progress ────────────────────────────────────────────────────────────────
    async def _progress(self, job_id: int, event: dict[str, Any]) -> Outcome:
        store = await self._services.get_store()
        try:
            async with store.transaction() as session:
                job, refusal = await self._open(session, job_id, event)
                if refusal is not None:
                    return refusal
                started = job.status == "pending"
                if started:
                    job = await session.start_job(job_id)
        except store_errors.InvalidJobTransitionError:
            return self._became_final(job_id, event)
        return Outcome("applied", "progress", event, job, started)

    # ── error ───────────────────────────────────────────────────────────────────
    async def _error(self, job_id: int, event: dict[str, Any]) -> Outcome:
        reason = clip_text(event["reason"])
        store = await self._services.get_store()
        try:
            async with store.transaction() as session:
                job, refusal = await self._open(session, job_id, event)
                if refusal is not None:
                    return refusal
                started = job.status == "pending"
                if started:
                    job = await session.start_job(job_id)
                if event["retryable"]:
                    # Passageiro: o job segue `running`; nada vai ao histórico (evita duplicata em reentrega).
                    logger.warning(
                        "Job %s: falha passageira na tentativa %s (%s).", job_id, event["attempt"], event["code"]
                    )
                    return Outcome("applied", "error-passageiro", event, job, started)
                job, _event = await session.fail_job(job_id, reason, clip_text(f"Conversão falhou: {reason}"))
        except store_errors.InvalidJobTransitionError:
            return self._became_final(job_id, event)
        return Outcome("applied", "error-definitivo", event, job, started)

    # ── result ──────────────────────────────────────────────────────────────────
    async def _result(self, job_id: int, event: dict[str, Any]) -> Outcome:
        try:
            quality = _winning_quality(event)
        except store_errors.StoreValidationError:
            logger.warning("Evento descartado: o resultado do job %s tem qualidade fora do domínio.", job_id)
            return _discard("fora-do-contrato", event)
        if quality is None:
            logger.warning("Evento descartado: o resultado do job %s não traz a qualidade do motor vencedor.", job_id)
            return _discard("resultado-sem-qualidade", event)
        settings = self._services.settings
        refused: str | None = None
        try:
            to_container_path(event["markdown_path"], settings.data_dir_host, settings.data_dir_mount)
        except ApiError as exc:
            refused = DATA_DIR_UNAVAILABLE_SUMMARY if exc.code == "data_dir_unavailable" else OUTSIDE_DATA_DIR_SUMMARY
        store = await self._services.get_store()
        try:
            async with store.transaction() as session:
                job, refusal = await self._open(session, job_id, event)
                if refusal is not None:
                    return refusal
                started = job.status == "pending"
                if started:
                    job = await session.start_job(job_id)
                if refused is not None:
                    # O caminho recebido não é gravado nem lido; só o motivo, em português.
                    logger.warning(
                        "Job %s: caminho de Markdown recusado. Confira se o --data-dir do worker é o mesmo caminho "
                        "de ARTIGO2MD_DATA_DIR em stack/.env.", job_id,
                    )
                    job, _event = await session.fail_job(
                        job_id, refused, "Conversão falhou: caminho de Markdown recusado."
                    )
                    return Outcome("applied", "resultado-recusado", event, job, started)
                for item in event["report"]["attempts"]:
                    await session.record_attempt(
                        job_id, item["engine"], item["seconds"],
                        None if item["quality"] is None else _quality(item["quality"]),
                        None if item["error"] is None else clip_text(item["error"]),
                    )
                job, _result, _event = await session.complete_job(
                    job_id, event["markdown_path"], event["engine"], quality,
                    clip_text(f"Conversão concluída com o motor {event['engine']}."),
                )
        except store_errors.InvalidJobTransitionError:
            # A transação foi desfeita por inteiro: nada parcial (nem o `start_job`) fica gravado.
            logger.info("Evento descartado: o resultado do job %s é duplicata.", job_id)
            return _discard("duplicata", event)
        except store_errors.StoreValidationError:
            logger.warning("Evento descartado: o resultado do job %s tem valores fora do domínio.", job_id)
            return _discard("fora-do-contrato", event)
        return Outcome("applied", "resultado", event, job, started)
