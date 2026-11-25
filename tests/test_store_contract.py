"""Testes de contrato da camada `artigo2md.store` que não precisam do banco nem de Docker.

Testes de código `async` usam `asyncio.run(...)` dentro do próprio teste: nenhuma dependência de teste nova
(`pytest-asyncio` não é usado nem adicionado ao `pyproject.toml`; Princípio II).
"""
from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from artigo2md import router
from artigo2md.store import errors, models
from artigo2md.store.errors import StoreValidationError
from artigo2md.store.models import QualityRecord

ROOT = Path(__file__).resolve().parent.parent
GOOD = dict(score=82.5, accepted=True, tables_found=2, tables_expected=2, chars_per_page=1500.0, issues=("ação", 'tabela "x"'))


def test_quality_record_fields_match_router_quality() -> None:
    assert [f.name for f in dataclasses.fields(QualityRecord)] == [f.name for f in dataclasses.fields(router.Quality)]


def test_requested_engines_equal_router_engines() -> None:
    assert models.REQUESTED_ENGINES == router.ENGINES


def test_document_statuses_cover_not_processed_and_job_states() -> None:
    assert models.DOCUMENT_STATUSES == ("not_processed",) + models.JOB_STATUSES
    assert set(models.FINAL_JOB_STATUSES) <= set(models.JOB_STATUSES)


def test_quality_record_accepts_boundaries_and_keeps_issue_order() -> None:
    for score in (0, 0.0, 100, 100.0):
        assert QualityRecord(**{**GOOD, "score": score}).score == score
    record = QualityRecord(**{**GOOD, "issues": ["b", "a", "ç"]})
    assert record.issues == ("b", "a", "ç")
    assert QualityRecord(**{**GOOD, "issues": ()}).issues == ()


@pytest.mark.parametrize(
    "bad",
    [
        {"score": 101},
        {"score": -0.1},
        {"score": float("nan")},
        {"score": True},
        {"accepted": 1},
        {"tables_found": -1},
        {"tables_expected": -3},
        {"tables_found": 1.5},
        {"chars_per_page": -0.5},
        {"chars_per_page": float("inf")},
        {"issues": ("ok", 3)},
    ],
)
def test_quality_record_rejects_out_of_range_values(bad: dict) -> None:
    with pytest.raises(StoreValidationError):
        QualityRecord(**{**GOOD, **bad})


def test_result_warnings_are_the_quality_issues() -> None:
    from datetime import datetime, timezone

    result = models.Result(
        job_id=1,
        markdown_path="/x.md",
        engine="docling",
        quality=QualityRecord(**GOOD),
        created_at=datetime.now(timezone.utc),
    )
    assert result.warnings == result.quality.issues == GOOD["issues"]


def test_models_are_frozen() -> None:
    record = QualityRecord(**GOOD)
    with pytest.raises(dataclasses.FrozenInstanceError):
        record.score = 1.0  # type: ignore[misc]


def test_error_hierarchy_matches_contract() -> None:
    e = errors
    assert issubclass(e.StoreConfigError, e.StoreError)
    assert issubclass(e.StoreValidationError, e.StoreError)
    for rule in (e.ProjectNameTakenError, e.ActiveJobExistsError, e.InvalidJobTransitionError):
        assert issubclass(rule, e.StoreRuleError) and not issubclass(rule, e.StoreDatabaseError)
    for nf in (e.ProjectNotFoundError, e.DocumentNotFoundError, e.JobNotFoundError):
        assert issubclass(nf, e.StoreNotFoundError)
    for mig in (e.SchemaTooNewError, e.MigrationChecksumError, e.MigrationFailedError, e.MigrationLockTimeoutError):
        assert issubclass(mig, e.MigrationError)
    assert issubclass(e.MigrationError, e.StoreError)
    assert issubclass(e.DatabaseUnavailableError, e.StoreError)
    assert issubclass(e.StoreDatabaseError, e.StoreError)


def test_package_exports_without_loading_the_driver() -> None:
    import subprocess
    import sys

    code = (
        "import sys, artigo2md.store as s;"
        "assert s.DbSettings and s.StoreError and s.MigrationLockTimeoutError;"
        "assert 'asyncpg' not in sys.modules"
    )
    result = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, cwd=ROOT / "src")
    assert result.returncode == 0, result.stderr


def _pyproject() -> dict:
    tomllib = pytest.importorskip("tomllib")  # Python >= 3.11
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def _names(requirements: list[str]) -> set[str]:
    import re

    return {re.split(r"[<>=!~\[; ]", r, maxsplit=1)[0].lower() for r in requirements}


def test_asyncpg_is_in_api_group_and_dev_extra_but_not_in_dependencies() -> None:
    data = _pyproject()
    assert "asyncpg" in _names(data["dependency-groups"]["api"])
    assert "asyncpg" in _names(data["project"]["optional-dependencies"]["dev"])
    assert "asyncpg" not in _names(data["project"]["dependencies"])
    assert any(r.replace(" ", "") == "asyncpg>=0.32.0,<0.33" for r in data["dependency-groups"]["api"])


def test_api_group_has_no_heavy_engines_and_no_new_test_dependency() -> None:
    data = _pyproject()
    api = _names(data["dependency-groups"]["api"])
    assert not api & {"docling", "pymupdf4llm", "pypdfium2", "markitdown"}
    assert "pytest-asyncio" not in _names(data["project"]["optional-dependencies"]["dev"])
