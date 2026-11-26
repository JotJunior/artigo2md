"""Cobertura do contrato publicado (tarefa 11.5; FR-033, SC-008). Sem Docker, sem banco, sem broker.

Compara a aplicação real (`create_app()`) com os contratos em `docs/specs/project-api/contracts/`:

- 11.5.1: as rotas registradas são exatamente as rotas de `http-api.md` e `live-events.md` (nenhuma rota oculta);
- 11.5.2: os campos de cada representação serializada são os da tabela "Representações" e os de `models.py`;
- 11.5.3: `/docs`, `/redoc` e `/openapi.json` respondem 404 e `/health` é o da API base.
"""
from __future__ import annotations

import dataclasses
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from artigo2md import __version__
from artigo2md.api.app import create_app
from artigo2md.api.routes.documents import (
    document_status_to_json,
    document_to_json,
    result_summary_to_json,
    source_to_json,
)
from artigo2md.api.routes.jobs import attempt_to_json, job_to_json, quality_to_json
from artigo2md.api.routes.projects import project_to_json
from artigo2md.api.routes.results import history_event_to_json, result_to_json
from artigo2md.api.settings import ApiSettings
from artigo2md.store import models

ROOT = Path(__file__).resolve().parent.parent
CONTRACTS = ROOT / "docs" / "specs" / "project-api" / "contracts"
HTTP_API = CONTRACTS / "http-api.md"
LIVE_EVENTS = CONTRACTS / "live-events.md"

METHODS = ("GET", "POST", "PATCH", "PUT", "DELETE")
NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)

# Linha de tabela cuja primeira coluna é "MÉTODO /rota" (com ou sem acento grave e com ou sem querystring).
ROUTE_ROW = re.compile(r"^\|\s*`?(" + "|".join(METHODS) + r")\s+(/[^\s`|?]*)(?:\?[^\s`|]*)?`?")
HEADING_ROUTE = re.compile(r"^##\s+(" + "|".join(METHODS) + r")\s+(/\S+)\s*$")


def bare_settings() -> ApiSettings:
    return ApiSettings.from_env({}, Path("/nonexistent-cwd-for-tests"))


def documented_routes() -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for line in HTTP_API.read_text(encoding="utf-8").splitlines():
        match = ROUTE_ROW.match(line)
        if match:
            found.add((match.group(1), match.group(2)))
    for line in LIVE_EVENTS.read_text(encoding="utf-8").splitlines():
        match = HEADING_ROUTE.match(line)
        if match:
            found.add((match.group(1), match.group(2)))
    return found


def registered_routes(app) -> set[tuple[str, str]]:
    """Rotas de negócio e `/health` da aplicação montada, lidas do esquema OpenAPI gerado em memória
    (`app.openapi()`, API pública; a rota `/openapi.json` segue desligada). O FastAPI atual guarda os roteadores
    incluídos em estrutura privada, por isso o esquema é a fonte estável; `include_in_schema=False` é vetado abaixo."""
    found: set[tuple[str, str]] = set()
    for path, operations in app.openapi()["paths"].items():
        for method in operations:
            found.add((method.upper(), path))
    return found


# ── 11.5.1 — rotas registradas x rotas publicadas ───────────────────────────────
def test_the_parser_finds_the_documented_routes():
    """Trava o próprio parser: o contrato tem rotas de todos os grupos e nenhuma duplicata silenciosa."""
    documented = documented_routes()
    assert ("GET", "/v1/projects") in documented
    assert ("GET", "/v1/projects/{project_id}/events") in documented
    assert ("GET", "/v1/status") in documented
    assert ("GET", "/v1/projects/{project_id}/export") in documented
    assert len(documented) >= 25


def test_registered_routes_are_exactly_the_documented_ones():
    app = create_app(bare_settings())
    registered = registered_routes(app) - {("GET", "/health")}
    documented = documented_routes()
    assert registered - documented == set(), f"rota registrada e não documentada (oculta): {sorted(registered - documented)}"
    assert documented - registered == set(), f"rota documentada e não registrada: {sorted(documented - registered)}"


def test_health_is_registered_without_prefix_and_business_routes_live_under_v1():
    app = create_app(bare_settings())
    registered = registered_routes(app)
    assert ("GET", "/health") in registered
    others = {path for _, path in registered if path != "/health"}
    assert others and all(path.startswith("/v1/") for path in others)


def test_no_route_is_hidden_from_the_schema_used_by_the_comparison():
    """A comparação lê o esquema; uma rota com `include_in_schema=False` escaparia dela — então ela é proibida."""
    for source in (ROOT / "src" / "artigo2md" / "api").rglob("*.py"):
        assert "include_in_schema" not in source.read_text(encoding="utf-8"), source


def test_every_documented_route_is_listed_in_the_status_column_of_the_contract():
    """Cada rota documentada aparece em linha de tabela ou título — sem rota só no texto corrido."""
    text = HTTP_API.read_text(encoding="utf-8") + LIVE_EVENTS.read_text(encoding="utf-8")
    for method, path in documented_routes():
        assert f"{method} {path}" in text


# ── 11.5.2 — campos serializados x tabela "Representações" x models.py ──────────
def representations() -> dict[str, dict[str, str]]:
    """Nome da representação -> {campo: texto do tipo} da tabela "Representações" de `http-api.md`."""
    result: dict[str, dict[str, str]] = {}
    inside = False
    for line in HTTP_API.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            inside = line.strip() == "## Representações"
            continue
        if not inside or not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in re.split(r"(?<!\\)\|", line.strip().strip("|"))]
        if len(cells) != 2 or not cells[0].startswith("`"):
            continue
        name = cells[0].strip("`")
        result[name] = field_types(cells[1])
    return result


FIELD = re.compile(r"(?:^|, )`([a-z_]+)`((?:[^,(]|\([^)]*\))*)")


def field_types(cell: str) -> dict[str, str]:
    return {match.group(1): match.group(2).strip() for match in FIELD.finditer(cell)}


def inline_document_fields() -> dict[str, str]:
    """`Document` = `id`, `project_id`, ... (linha de `DocumentOutcomes`, fora da tabela)."""
    text = HTTP_API.read_text(encoding="utf-8")
    match = re.search(r"com `Document` = ([^\n(]+)\(`models\.Document`\)", text)
    assert match, "linha de `Document` não encontrada em http-api.md"
    return {name: "" for name in re.findall(r"`([a-z_]+)`", match.group(1))}


def sample(model: type, **overrides):
    values: dict = {}
    for field in dataclasses.fields(model):
        values[field.name] = overrides.get(field.name, None)
    return model(**values)


def build_samples():
    quality = models.QualityRecord(
        score=87.5, accepted=True, tables_found=2, tables_expected=3, chars_per_page=1800.0, issues=("tabela perdida",)
    )
    summary = models.ResultSummary(job_id=7, markdown_path="/data/7/a.md", engine="docling", score=87.5, accepted=True, created_at=NOW)
    return {
        "Project": (project_to_json, models.Project(id=1, name="p", status="active", created_at=NOW, updated_at=NOW), models.Project),
        "Source": (
            source_to_json,
            models.Source(id=1, project_id=1, kind="file", mode="in_place", path="/a.pdf", created_at=NOW),
            models.Source,
        ),
        "DocumentStatus": (
            document_status_to_json,
            models.DocumentStatus(
                document_id=3, project_id=1, source_id=2, path="/a.pdf", status="succeeded",
                latest_job_id=7, latest_job_requested_at=NOW, last_result=summary,
            ),
            models.DocumentStatus,
        ),
        "ResultSummary": (result_summary_to_json, summary, models.ResultSummary),
        "Job": (
            job_to_json,
            models.Job(
                id=7, project_id=1, document_id=3, requested_engine="auto", status="failed", requested_at=NOW,
                started_at=NOW, finished_at=NOW, error_summary="falhou",
            ),
            models.Job,
        ),
        "Quality": (quality_to_json, quality, models.QualityRecord),
        "Attempt": (
            attempt_to_json,
            models.Attempt(id=1, job_id=7, position=1, engine="docling", seconds=1.5, quality=quality, error=None, created_at=NOW),
            models.Attempt,
        ),
        "Result": (
            result_to_json,
            models.Result(job_id=7, markdown_path="/data/7/a.md", engine="docling", quality=quality, created_at=NOW),
            models.Result,
        ),
        "HistoryEvent": (
            history_event_to_json,
            models.HistoryEvent(
                id=1, project_id=1, document_id=3, job_id=7, event_type="job_created", occurred_at=NOW, message="criado"
            ),
            models.HistoryEvent,
        ),
    }


SAMPLES = build_samples()
# Campos da dataclass que o contrato diz NÃO expor, ou propriedades que ele acrescenta.
NOT_EXPOSED = {"Job": {"published_at"}}
EXTRA_PROPERTIES = {"Result": {"warnings"}}


def test_the_table_lists_every_representation_covered_here():
    assert set(representations()) == set(SAMPLES)


@pytest.mark.parametrize("name", sorted(SAMPLES))
def test_serialized_fields_match_the_representations_table(name):
    serializer, instance, _ = SAMPLES[name]
    assert set(serializer(instance)) == set(representations()[name])


@pytest.mark.parametrize("name", sorted(SAMPLES))
def test_serialized_fields_match_models_py(name):
    serializer, instance, model = SAMPLES[name]
    expected = {field.name for field in dataclasses.fields(model)}
    expected -= NOT_EXPOSED.get(name, set())
    expected |= EXTRA_PROPERTIES.get(name, set())
    assert set(serializer(instance)) == expected


def assert_matches_representation(name: str, payload: dict) -> None:
    """Confere um payload (de teste ou recebido da API no ar) com a tabela "Representações": mesmos campos, tipos
    e `null` só onde o contrato o permite. Reutilizado pelos testes de integração com a stack real."""
    table = representations()[name]
    assert set(payload) == set(table), f"{name}: campos {sorted(payload)} != contrato {sorted(table)}"
    for field, description in table.items():
        value = payload[field]
        nullable = "null" in description
        if value is None:
            assert nullable, f"{name}.{field} veio nulo mas o contrato não permite null"
            continue
        if field.endswith("_at"):
            assert isinstance(value, str)
            assert datetime.fromisoformat(value).tzinfo is not None, f"{name}.{field} sem fuso"
        elif field == "id" or field.endswith("_id") or description.startswith("int"):
            assert isinstance(value, int) and not isinstance(value, bool), f"{name}.{field}"
        elif description.startswith("number"):
            assert isinstance(value, (int, float)) and not isinstance(value, bool), f"{name}.{field}"
        elif description.startswith("bool"):
            assert isinstance(value, bool), f"{name}.{field}"
        elif description.startswith("list[str]"):
            assert isinstance(value, list) and all(isinstance(item, str) for item in value), f"{name}.{field}"
        elif "`" in description and description.split("`")[1] in representations():
            assert isinstance(value, dict), f"{name}.{field}"
            assert_matches_representation(description.split("`")[1], value)
        else:
            assert isinstance(value, str), f"{name}.{field}: esperado texto, veio {type(value).__name__}"


@pytest.mark.parametrize("name", sorted(SAMPLES))
def test_serialized_values_have_the_documented_types(name):
    serializer, instance, _ = SAMPLES[name]
    assert_matches_representation(name, serializer(instance))


def test_the_type_check_rejects_a_wrong_payload():
    serializer, instance, _ = SAMPLES["Job"]
    good = serializer(instance)
    with pytest.raises(AssertionError):
        assert_matches_representation("Job", {**good, "id": "7"})
    with pytest.raises(AssertionError):
        assert_matches_representation("Job", {**good, "requested_at": None})
    with pytest.raises(AssertionError):
        assert_matches_representation("Job", {**good, "extra": 1})


@pytest.mark.parametrize("name", sorted(SAMPLES))
def test_field_names_are_snake_case_english(name):
    for field in representations()[name]:
        assert re.fullmatch(r"[a-z][a-z0-9]*(_[a-z0-9]+)*", field), field


def test_nested_null_representations_are_serialized_as_null():
    _, status, _ = SAMPLES["DocumentStatus"]
    bare = dataclasses.replace(status, latest_job_id=None, latest_job_requested_at=None, last_result=None)
    payload = document_status_to_json(bare)
    assert payload["latest_job_id"] is None and payload["latest_job_requested_at"] is None
    assert payload["last_result"] is None
    _, attempt, _ = SAMPLES["Attempt"]
    assert attempt_to_json(dataclasses.replace(attempt, quality=None, error="x"))["quality"] is None


def test_document_inside_document_outcomes_matches_models_py():
    expected = {field.name for field in dataclasses.fields(models.Document)}
    assert set(inline_document_fields()) == expected
    instance = models.Document(id=1, project_id=1, source_id=1, path="/a.pdf", created_at=NOW)
    assert set(document_to_json(instance)) == expected


def test_published_at_is_not_exposed_by_any_representation():
    for name in SAMPLES:
        assert "published_at" not in representations()[name]
        serializer, instance, _ = SAMPLES[name]
        assert "published_at" not in serializer(instance)


# ── 11.5.3 — documentação automática desligada e /health da API base ────────────
@pytest.fixture()
def client():
    with TestClient(create_app(bare_settings()), base_url="http://127.0.0.1") as test_client:
        yield test_client


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"])
def test_automatic_documentation_is_off(client, path):
    assert client.get(path).status_code == 404


def test_health_is_identical_to_the_base_contract(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert response.json() == {"status": "ok", "service": "artigo2md-api", "version": __version__}


def test_health_contract_has_no_extra_route_or_field():
    registered = registered_routes(create_app(bare_settings()))
    assert {item for item in registered if item[1].startswith("/health")} == {("GET", "/health")}
