"""Serviços de fundo da API: publicadora, consumidora e republicação de jobs (`plan` S-05; FR-011, FR-017, FR-032).

`lifespan` (em `app.py`) chama `Background.start()` na partida e `Background.stop()` no encerramento. Cada dependência
tem o PRÓPRIO laço de reconexão, independente das outras (FR-032):

- **banco**: o supervisor chama `services.get_store()` com recuo exponencial (1 s a 30 s) até a abertura funcionar —
  e a abertura real aplica as migrações (`apply_migrations`) antes de abrir o pool (`deps._open_real_store`); migração
  que falha mantém `/health` e faz as rotas de banco responderem `503` em português, e a API se recupera sozinha;
- **publicadora** (`BrokerPublisher`): thread própria com recuo; a CADA conexão ela dispara a republicação;
- **consumidora** (`EventConsumer`): thread própria com recuo, que repete a mesma mensagem com o banco fora.

Republicação (R-06, at-least-once): `list_unpublished_jobs` (jobs `pending` sem `published_at`) é publicado de novo,
na ordem de `id`, a cada conexão da publicadora; o que a publicadora confirma ganha `published_at` e nunca é
republicado. Job que uma requisição desta API está publicando AGORA (`Services.inflight`) é pulado.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from ..jobqueue import schema
from ..store import errors as store_errors
from .broker import BrokerPublisher, EventConsumer, PublishBatch, PublishFailed
from .consumer import EventHandler, TransientStoreError
from .deps import Services
from .errors import ApiError

logger = logging.getLogger("artigo2md.api")

BACKOFF_START_SECONDS = 1.0
BACKOFF_MAX_SECONDS = 30.0
REPUBLISH_PAGE = 1000


def _is_database_problem(exc: BaseException) -> bool:
    if isinstance(exc, ApiError):
        return exc.code == "database_unavailable"
    return isinstance(
        exc,
        (store_errors.DatabaseUnavailableError, store_errors.StoreConfigError, store_errors.MigrationError),
    )


async def republish_unpublished(services: Services, publisher: Any) -> int:
    """Publica de novo os jobs `pending` sem `published_at`. Devolve quantos foram confirmados.

    Falha de publicação interrompe a rodada SEM falhar nenhum job (eles seguem `pending` e serão republicados na
    próxima conexão); `PublishFailed` é propagada para quem agenda decidir a nova tentativa.
    """
    store = await services.get_store()
    batch = PublishBatch(publisher)
    done = 0
    seen: set[int] = set()
    while True:
        page = [job for job in await store.list_unpublished_jobs(REPUBLISH_PAGE)
                if job.id not in seen and job.id not in services.inflight]
        if not page:
            return done
        for job in page:
            seen.add(job.id)
            document = await store.get_document(job.document_id)
            await batch.publish(schema.build_job(str(job.id), document.path, job.requested_engine, 1))
            try:
                await store.mark_job_published(job.id)
            except store_errors.StoreError as exc:
                logger.warning("Job %s republicado, mas a marcação de publicação falhou (%s).", job.id,
                               type(exc).__name__)
            done += 1


class Background:
    """Cria, inicia e encerra publicadora, consumidora e supervisor. `options` repassa `connect`, `backoff_*` e
    `poll` às threads (testes com o broker falso)."""

    def __init__(self, services: Services, options: dict[str, Any] | None = None) -> None:
        self._services = services
        self._options = dict(options or {})
        self._task: asyncio.Task[None] | None = None
        self._wake = asyncio.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self.publisher: BrokerPublisher | None = None
        self.consumer: EventConsumer | None = None

    # ── ciclo de vida ───────────────────────────────────────────────────────────
    async def start(self) -> None:
        services = self._services
        self._loop = asyncio.get_running_loop()
        broker_settings = services.settings.broker
        if broker_settings is None:
            logger.warning("Publicadora e consumidora não iniciadas: %s",
                           services.settings.broker_error or "configuração do broker ausente.")
        else:
            thread_options = {key: value for key, value in self._options.items()
                              if key in ("connect", "backoff_start", "backoff_max", "poll")}
            self.publisher = BrokerPublisher(broker_settings, on_connect=self._on_publisher_connect, **thread_options)
            services.broker = self.publisher
            handler = EventHandler(services)
            self.consumer = EventConsumer(
                broker_settings, handler.handle, self._loop, retry_on=TransientStoreError, **thread_options
            )
            services.consumer = self.consumer
            self.publisher.start()
            self.consumer.start()
        self._task = asyncio.create_task(self._supervise(), name="artigo2md-supervisor")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # o encerramento nunca falha por causa do supervisor
                pass
        # As threads fazem `join`: fora do laço, para a consumidora ainda poder concluir o evento em tratamento.
        if self.consumer is not None:
            await asyncio.to_thread(self.consumer.stop)
        if self.publisher is not None:
            await asyncio.to_thread(self.publisher.stop)

    # ── gancho da publicadora (roda na thread dela) ─────────────────────────────
    def _on_publisher_connect(self) -> None:
        loop = self._loop
        if loop is not None and not loop.is_closed():
            loop.call_soon_threadsafe(self._wake.set)

    # ── supervisor ──────────────────────────────────────────────────────────────
    async def _supervise(self) -> None:
        backoff_start = self._options.get("backoff_start", BACKOFF_START_SECONDS)
        backoff_max = self._options.get("backoff_max", BACKOFF_MAX_SECONDS)
        delay = backoff_start
        # 1) banco pronto (a abertura aplica as migrações); falha mantém a API de pé e tenta de novo.
        while True:
            try:
                await self._services.get_store()
                break
            except Exception as exc:
                logger.warning("Banco indisponível ou não migrado (%s); nova tentativa em %.0f s.",
                               type(exc).__name__, delay)
                await asyncio.sleep(delay)
                delay = min(delay * 2, backoff_max)
        logger.info("Banco pronto e migrado.")
        # 2) republicação a cada conexão da publicadora (e uma vez na partida).
        self._wake.set()
        delay = backoff_start
        while True:
            await self._wake.wait()
            self._wake.clear()
            publisher = self.publisher
            if publisher is None or not publisher.connected:
                continue  # sem publicadora conectada não há o que publicar: a próxima conexão acorda de novo
            try:
                count = await republish_unpublished(self._services, publisher)
            except PublishFailed:
                logger.warning("Republicação interrompida: a fila não confirmou a mensagem.")
                if publisher.connected:  # recusa pontual: tenta de novo; conexão caída: a próxima conexão acorda
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, backoff_max)
                    self._wake.set()
                continue
            except Exception as exc:
                logger.warning("Republicação de jobs pendentes falhou (%s); nova tentativa em %.0f s.",
                               type(exc).__name__, delay)
                await asyncio.sleep(delay)
                delay = min(delay * 2, backoff_max)
                self._wake.set()
                continue
            delay = backoff_start
            if count:
                logger.info("%d job(s) pendente(s) republicado(s) na fila.", count)
