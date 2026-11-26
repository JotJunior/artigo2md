"""Contrato da fila em `jobqueue/schema.py`: sem dependência de conversão e igual aos dataclasses reais (3.1)."""
from __future__ import annotations

import subprocess
import sys

import pytest

from artigo2md.jobqueue import contract, schema
from artigo2md.router import ENGINES, Attempt, ConversionResult, Quality


def test_engines_literal_equals_router_engines():
    assert schema.ENGINES == ENGINES


def test_quality_kinds_equal_introspection_of_the_dataclass():
    assert schema.QUALITY_KINDS == contract._dataclass_kinds(Quality)


def test_attempt_kinds_equal_introspection_of_the_dataclass():
    assert schema.ATTEMPT_KINDS == contract._dataclass_kinds(Attempt)


def test_analysis_kinds_equal_introspection_of_the_dataclass():
    assert schema.ANALYSIS_KINDS == contract._analysis_kinds()


def test_report_kinds_equal_introspection_of_conversion_result():
    assert schema.REPORT_KINDS == contract._report_kinds()
    assert "markdown" not in schema.REPORT_KINDS


def test_contract_facade_reexports_the_same_objects():
    for name in ("parse_job", "build_job", "build_progress", "build_result", "build_error", "encode",
                 "validate_event", "validate_report", "json_depth", "utc_now_iso", "ContractError", "Job",
                 "ERROR_CODES", "ROUTING_KEYS", "JOB_ID_RE", "MAX_BODY_BYTES", "MAX_JSON_DEPTH",
                 "CONTENT_TYPE", "SCHEMA_VERSION"):
        assert getattr(contract, name) is getattr(schema, name), name


def test_schema_round_trip_without_the_conversion_modules():
    job = schema.parse_job(schema.encode(schema.build_job("j1", "/data/a.pdf", "docling", 2)))
    assert (job.job_id, job.engine, job.attempt) == ("j1", "docling", 2)
    with pytest.raises(schema.ContractError) as err:
        schema.parse_job(schema.encode(schema.build_job("j1", "/data/a.pdf", "inventado")))
    assert err.value.code == "invalid_engine"


_PROBE = """
import sys
import artigo2md.jobqueue.schema
import artigo2md.jobqueue.topology
import artigo2md.jobqueue.amqp
import artigo2md.jobqueue.output
bad = [m for m in ("pypdfium2", "artigo2md.router", "artigo2md.pdfscan", "artigo2md.engines", "docling",
                   "pymupdf4llm", "psutil") if m in sys.modules]
print(",".join(bad))
"""


def test_importing_schema_topology_amqp_output_does_not_load_conversion_modules():
    # processo novo: o ambiente de desenvolvimento tem `router`, mas ele não pode ser puxado pelo contrato.
    done = subprocess.run([sys.executable, "-c", _PROBE], capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == ""
