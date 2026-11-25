"""Contrato v1 das mensagens da fila (tarefa 3.1 da `conversion-queue`): sem broker, sem rede."""
from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from artigo2md.jobqueue import contract
from artigo2md.jobqueue.contract import ContractError, parse_job
from artigo2md.router import ENGINES, Attempt, ConversionResult, Quality

ROOT = Path(__file__).resolve().parent.parent
CONTRACT_DOC = ROOT / "docs" / "specs" / "conversion-queue" / "contracts" / "queue-messages.md"


def _job(**overrides) -> dict:
    base = {"schema_version": 1, "type": "job", "job_id": "abc-123", "pdf_path": "/tmp/a.pdf",
            "engine": "auto", "attempt": 1}
    base.update(overrides)
    return {k: v for k, v in base.items() if v is not ...}


def _body(obj) -> bytes:
    return json.dumps(obj).encode("utf-8")


def _report() -> dict:
    analysis = {"pages": 2, "text_pages": 2, "scanned_pages": 0, "total_chars": 900, "table_hints": 1,
                "formula_score": 0, "article_score": 4, "fully_scanned": False}
    quality = Quality(score=100.0, accepted=True, tables_found=1, tables_expected=1,
                      chars_per_page=450.0, issues=[])
    result = ConversionResult(
        source="/tmp/a.pdf", markdown="# t", engine="docling", analysis=analysis,
        attempts=[Attempt("docling", 1.2, quality), Attempt("markitdown", 0.1, None, "falhou")],
    )
    return result.report()


# ── job válido ──────────────────────────────────────────────────────────────────
def test_valid_job_applies_defaults():
    job = parse_job(_body(_job(engine=..., attempt=...)))
    assert (job.job_id, job.pdf_path, job.engine, job.attempt) == ("abc-123", "/tmp/a.pdf", "auto", 1)


@pytest.mark.parametrize("engine", ENGINES)
def test_every_router_engine_is_accepted(engine):
    assert parse_job(_body(_job(engine=engine))).engine == engine


# ── job inválido ────────────────────────────────────────────────────────────────
def _code(body: bytes) -> ContractError:
    with pytest.raises(ContractError) as info:
        parse_job(body)
    return info.value


@pytest.mark.parametrize("body", [b"nao e json", b"\xff\xfe", b"", b"{"])
def test_not_json_is_invalid_message(body):
    err = _code(body)
    assert err.code == "invalid_message" and err.job_id is None


@pytest.mark.parametrize("body", [b"[1, 2]", b"\"texto\"", b"42", b"null"])
def test_root_that_is_not_an_object_is_invalid_message(body):
    assert _code(body).code == "invalid_message"


@pytest.mark.parametrize("field", ["schema_version", "type", "job_id", "pdf_path"])
def test_missing_required_field(field):
    assert _code(_body(_job(**{field: ...}))).code == "invalid_message"


@pytest.mark.parametrize("overrides", [
    {"pdf_path": 5}, {"pdf_path": ""}, {"engine": 3}, {"attempt": "1"}, {"attempt": 1.5},
    {"attempt": True}, {"attempt": 0}, {"job_id": 7}, {"schema_version": "1"}, {"type": "result"},
])
def test_wrong_type_or_value(overrides):
    assert _code(_body(_job(**overrides))).code == "invalid_message"


def test_extra_field_in_job_is_rejected_and_named():
    err = _code(_body(_job(extra="x")))
    assert err.code == "invalid_message" and "extra" in err.reason and err.job_id == "abc-123"


def test_future_version_cites_received_and_supported():
    err = _code(_body(_job(schema_version=2, campo_novo=1)))
    assert err.code == "unsupported_schema_version"
    assert "2" in err.reason and "1" in err.reason and err.job_id == "abc-123"


def test_invalid_engine_lists_the_valid_ones():
    err = _code(_body(_job(engine="inexistente")))
    assert err.code == "invalid_engine"
    assert all(name in err.reason for name in ENGINES) and "inexistente" in err.reason


@pytest.mark.parametrize("job_id", ["", "-abc", "a b", "../x", "a/b", "é", "x" * 129])
def test_invalid_job_id_gives_no_job_id_in_the_error(job_id):
    err = _code(_body(_job(job_id=job_id)))
    assert err.code == "invalid_message" and err.job_id is None


def test_job_id_at_the_length_limit_is_valid():
    assert parse_job(_body(_job(job_id="a" * 128))).job_id == "a" * 128


def test_body_above_the_limit_is_rejected_without_parsing():
    body = _body(_job(pdf_path="/" + "a" * contract.MAX_BODY_BYTES))
    assert len(body) > contract.MAX_BODY_BYTES
    err = _code(body)
    assert err.code == "invalid_message" and str(contract.MAX_BODY_BYTES) in err.reason


def test_body_exactly_at_the_limit_is_accepted():
    pad = contract.MAX_BODY_BYTES - len(_body(_job(pdf_path="/")))
    body = _body(_job(pdf_path="/" + "a" * pad))
    assert len(body) == contract.MAX_BODY_BYTES
    assert parse_job(body).job_id == "abc-123"


def test_deeply_nested_json_is_rejected_before_json_loads(monkeypatch):
    def boom(*_a, **_k):
        raise AssertionError("json.loads não deveria rodar")

    monkeypatch.setattr(contract.json, "loads", boom)
    deep = b"[" * 5000 + b"]" * 5000
    assert _code(deep).code == "invalid_message"


def test_depth_counts_only_brackets_outside_strings():
    assert contract.json_depth(b'{"a": "[[[[[[[[[[ \\" {{{{"}') == 1
    assert contract.json_depth(b'[[[[[[[[[1]]]]]]]]]') == 9
    assert parse_job(_body(_job(pdf_path="/tmp/[[[[[[[[[[[a].pdf"))).job_id == "abc-123"


def test_depth_limit_boundary():
    nested = b'{"a":' * 8 + b"1" + b"}" * 8
    assert contract.json_depth(nested) == 8
    assert contract.json_depth(b'{"a":' * 9 + b"1" + b"}" * 9) == 9


# ── construtores e validação de eventos (SC-006) ────────────────────────────────
def test_every_built_message_passes_validation():
    now = datetime(2026, 11, 25, 12, 0, 0, 123000, tzinfo=timezone.utc)
    events = [
        contract.build_progress("j1", 1, "Inspecionando o PDF", now),
        contract.build_result("j1", 2, "/data/j1/a.md", "docling", _report(), now),
    ] + [contract.build_error("j1", 3, code, "motivo", retryable, 3, now)
         for code, retryable in contract.ERROR_CODES.items()]
    for event in events:
        assert contract.validate_event(event) is event
        assert contract.validate_event(contract.encode(event))["job_id"] == "j1"
    assert events[0]["emitted_at"] == "2026-11-25T12:00:00.123Z"


def test_built_job_passes_job_validation():
    job = parse_job(contract.encode(contract.build_job("j1", "/tmp/a.pdf", "docling", 2)))
    assert (job.engine, job.attempt) == ("docling", 2)


def test_event_with_extra_field_is_accepted_but_job_with_extra_field_is_not():
    event = contract.build_progress("j1", 1, "oi")
    event["campo_futuro"] = {"qualquer": 1}
    assert contract.validate_event(event)
    assert _code(_body(_job(campo_futuro=1))).code == "invalid_message"


def test_emitted_at_uses_utc_with_z_suffix():
    assert re.match(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$", contract.utc_now_iso())
    local = datetime.fromisoformat("2026-11-25T09:00:00-03:00")
    assert contract.utc_now_iso(local) == "2026-11-25T12:00:00.000Z"


def test_unknown_error_code_cannot_be_built():
    with pytest.raises(ValueError):
        contract.build_error("j1", 1, "inventado", "x", False, 3)


@pytest.mark.parametrize("mutate", [
    lambda e: e.pop("emitted_at"),
    lambda e: e.update(emitted_at="ontem"),
    lambda e: e.update(attempt=0),
    lambda e: e.update(job_id="a b"),
    lambda e: e.update(type="job"),
    lambda e: e.update(schema_version=9),
    lambda e: e.update(message=5),
])
def test_invalid_events_are_rejected(mutate):
    event = contract.build_progress("j1", 1, "oi")
    mutate(event)
    with pytest.raises(ContractError):
        contract.validate_event(event)


def test_error_event_field_types_and_unknown_code():
    event = contract.build_error("j1", 1, "invalid_pdf", "r", False, 3)
    for bad in ({"retryable": "false"}, {"max_attempts": "3"}, {"code": "x"}, {"reason": None}):
        with pytest.raises(ContractError):
            contract.validate_event({**event, **bad})


# ── report derivado dos dataclasses reais (3.1.3) ───────────────────────────────
def test_real_conversion_report_validates():
    contract.validate_report(_report())


def test_report_shape_comes_from_the_real_dataclasses():
    report = _report()
    assert set(report) == {"source", "engine", "analysis", "attempts"}
    assert set(report["attempts"][0]) == {"engine", "seconds", "quality", "error"}
    assert set(report["attempts"][0]["quality"]) == {
        "score", "accepted", "tables_found", "tables_expected", "chars_per_page", "issues"}


def test_every_annotation_of_the_report_dataclasses_has_a_contract_kind():
    import dataclasses

    from artigo2md.pdfscan import Analysis

    for cls in (ConversionResult, Attempt, Quality, Analysis):
        for f in dataclasses.fields(cls):
            if f.name in ("markdown", "page_info"):
                continue
            assert str(f.type) in contract._ANNOTATION_KINDS, (cls.__name__, f.name, f.type)


@pytest.mark.parametrize("mutate", [
    lambda r: r.pop("attempts"),
    lambda r: r.update(markdown="# vazou"),
    lambda r: r["analysis"].pop("fully_scanned"),
    lambda r: r["analysis"].update(pages="2"),
    lambda r: r["analysis"].update(invento=1),
    lambda r: r["attempts"][0]["quality"].update(score="alto"),
    lambda r: r["attempts"][0]["quality"].update(issues=[1]),
    lambda r: r["attempts"][1].update(error=3),
    lambda r: r.update(attempts={}),
])
def test_report_with_invented_or_wrong_fields_is_rejected(mutate):
    report = _report()
    mutate(report)
    with pytest.raises(ContractError):
        contract.validate_report(report)


def test_result_event_with_bad_report_is_rejected():
    event = contract.build_result("j1", 1, "/d/j1/a.md", "docling", {"source": "x"})
    with pytest.raises(ContractError):
        contract.validate_event(event)


# ── documento e código não divergem (3.1.6) ─────────────────────────────────────
def test_json_examples_in_the_contract_document_validate():
    text = CONTRACT_DOC.read_text(encoding="utf-8")
    examples = re.findall(r"```json\n(.*?)```", text, flags=re.S)
    assert examples, "o contrato deveria ter ao menos um exemplo JSON"
    for raw in examples:
        obj = json.loads(raw)
        if obj["type"] == "job":
            assert parse_job(raw.encode("utf-8")).job_id == obj["job_id"]
        else:
            contract.validate_event(obj)


def test_error_codes_match_the_contract_table():
    text = CONTRACT_DOC.read_text(encoding="utf-8")
    section = text.split("## Códigos de erro", 1)[1].split("\n## ", 1)[0]
    rows = re.findall(r"^\| `(\w+)` \| (definitiva|passageira) \|", section, flags=re.M)
    assert {code: cls == "passageira" for code, cls in rows} == contract.ERROR_CODES


def test_limits_match_the_contract_document():
    text = CONTRACT_DOC.read_text(encoding="utf-8")
    assert f"**{contract.MAX_BODY_BYTES} bytes**" in text
    assert f"**{contract.MAX_JSON_DEPTH}** níveis" in text


# ── isolamento de imports (3.1.7) ───────────────────────────────────────────────
def test_importing_contract_does_not_load_queue_or_engine_libraries():
    code = (
        "import sys, artigo2md.jobqueue.contract\n"
        "bad = [m for m in ('pika', 'psutil', 'docling', 'pymupdf4llm') if m in sys.modules]\n"
        "print(','.join(bad))\n"
    )
    out = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == ""
