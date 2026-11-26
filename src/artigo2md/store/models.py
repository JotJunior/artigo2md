"""Registros devolvidos pela camada e constantes dos valores enumerados.

Não importa `router` (que puxa `pdfscan`/pypdfium2, ausente na imagem da API): `QualityRecord` espelha
`router.Quality` e um teste confere que os nomes dos campos coincidem (research, Decision 8).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

from .errors import StoreValidationError

# --- Valores enumerados (iguais aos CHECK do SQL; um teste confere) ---------------------------------
PROJECT_STATUSES = ("active", "archived")
SOURCE_KINDS = ("folder", "file")
SOURCE_MODES = ("in_place", "copied")
JOB_STATUSES = ("pending", "running", "succeeded", "failed", "cancelled")
FINAL_JOB_STATUSES = ("succeeded", "failed", "cancelled")
# Igual a `router.ENGINES` (src/artigo2md/router.py); um teste confere a igualdade.
REQUESTED_ENGINES = ("auto", "docling", "pymupdf", "mineru", "markitdown")
EVENT_TYPES = (
    "project_created",
    "project_archived",
    "project_unarchived",
    "source_added",
    "document_added",
    "job_created",
    "job_started",
    "attempt_started",
    "attempt_recorded",
    "job_succeeded",
    "job_failed",
    "job_cancelled",
    "project_renamed",
)
NOT_PROCESSED = "not_processed"
DOCUMENT_STATUSES = (NOT_PROCESSED,) + JOB_STATUSES


def _finite(value: float) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


@dataclass(frozen=True)
class QualityRecord:
    """Espelho de `router.Quality` (mesmos seis campos); `issues` é imutável e mantém a ordem."""

    score: float
    accepted: bool
    tables_found: int
    tables_expected: int
    chars_per_page: float
    issues: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Recusa valores fora de faixa antes de qualquer SQL (FR-007)."""
        if not _finite(self.score) or not 0 <= self.score <= 100:
            raise StoreValidationError("A nota de qualidade (score) precisa estar entre 0 e 100.")
        if not isinstance(self.accepted, bool):
            raise StoreValidationError("O campo accepted da qualidade precisa ser verdadeiro ou falso.")
        for name in ("tables_found", "tables_expected"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise StoreValidationError(f"O campo {name} da qualidade precisa ser um inteiro não negativo.")
        if not _finite(self.chars_per_page) or self.chars_per_page < 0:
            raise StoreValidationError("O campo chars_per_page da qualidade não pode ser negativo.")
        object.__setattr__(self, "issues", tuple(self.issues))
        if not all(isinstance(item, str) for item in self.issues):
            raise StoreValidationError("Cada item de issues da qualidade precisa ser texto.")


@dataclass(frozen=True)
class Project:
    id: int
    name: str
    status: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class Source:
    id: int
    project_id: int
    kind: str
    mode: str
    path: str
    created_at: datetime


@dataclass(frozen=True)
class Document:
    id: int
    project_id: int
    source_id: int
    path: str
    created_at: datetime


@dataclass(frozen=True)
class Job:
    id: int
    project_id: int
    document_id: int
    requested_engine: str
    status: str
    requested_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    error_summary: str | None
    # Instante em que a API confirmou a publicação do job na fila (migração 0002); `None` = não publicado.
    published_at: datetime | None = None


@dataclass(frozen=True)
class Attempt:
    id: int
    job_id: int
    position: int
    engine: str
    seconds: float
    quality: QualityRecord | None
    error: str | None
    created_at: datetime


@dataclass(frozen=True)
class Result:
    job_id: int
    markdown_path: str
    engine: str
    quality: QualityRecord
    created_at: datetime

    @property
    def warnings(self) -> tuple[str, ...]:
        """Os avisos do resultado são as próprias `issues` da avaliação do motor vencedor (FR-007)."""
        return self.quality.issues


@dataclass(frozen=True)
class HistoryEvent:
    id: int
    project_id: int
    document_id: int | None
    job_id: int | None
    event_type: str
    occurred_at: datetime
    message: str


@dataclass(frozen=True)
class ResultSummary:
    job_id: int
    markdown_path: str
    engine: str
    score: float
    accepted: bool
    created_at: datetime


@dataclass(frozen=True)
class DocumentStatus:
    document_id: int
    project_id: int
    source_id: int
    path: str
    status: str
    latest_job_id: int | None
    latest_job_requested_at: datetime | None
    last_result: ResultSummary | None


@dataclass(frozen=True)
class ArchiveOutcome:
    project: Project
    cancelled_job_ids: tuple[int, ...]
