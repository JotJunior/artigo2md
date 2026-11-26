"""Contrato de mensagens da fila (`contracts/queue-messages.md`): fachada sobre `schema.py`.

A definição vive em `schema.py` (só biblioteca padrão, importável pela API). Este módulo reexporta tudo para os
imports do worker continuarem válidos e guarda a introspecção dos dataclasses reais (`router`/`pdfscan`), usada
pelos testes para provar que os mapas estáticos de `schema.py` não divergem do código que produz o `report`.
"""
from __future__ import annotations

import dataclasses
import json  # noqa: F401 - os testes substituem `contract.json.loads`
from typing import Any

from ..pdfscan import Analysis
from ..router import ENGINES, Attempt, ConversionResult, Quality  # noqa: F401
from . import schema
from .schema import *  # noqa: F401,F403
from .schema import (  # noqa: F401
    ATTEMPT_KINDS,
    ANALYSIS_KINDS,
    QUALITY_KINDS,
    REPORT_KINDS,
    _EMITTED_AT_RE,
    _JOB_FIELDS,
    _check_kind,
    _check_object,
    _is_int,
    _is_number,
    _safe_job_id,
)

# Tipos do `report`, lidos das anotações dos dataclasses reais (com `from __future__ import annotations`
# elas são texto). Uma anotação nova sem entrada aqui faz o teste de contrato falhar.
_ANNOTATION_KINDS = {
    "int": "int",
    "float": "number",
    "bool": "bool",
    "str": "str",
    "str | None": "str|null",
    "list[str]": "list[str]",
    "dict": "object",
    "Quality | None": "quality|null",
    "list[Attempt]": "list[attempt]",
}


def _dataclass_kinds(cls: type, skip: tuple[str, ...] = ()) -> dict[str, str]:
    return {
        f.name: _ANNOTATION_KINDS[str(f.type)] for f in dataclasses.fields(cls) if f.name not in skip
    }


def _analysis_kinds() -> dict[str, str]:
    kinds = _dataclass_kinds(Analysis, skip=("page_info",))
    kinds["fully_scanned"] = "bool"  # propriedade acrescentada por `Analysis.to_dict()`
    return kinds


def _report_kinds() -> dict[str, str]:
    return {
        f.name: "object" if f.name == "analysis" else _ANNOTATION_KINDS[str(f.type)]
        for f in dataclasses.fields(ConversionResult)
        if f.name != "markdown"
    }
