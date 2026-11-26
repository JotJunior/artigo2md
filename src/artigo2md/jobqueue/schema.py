"""Contrato de mensagens da fila, versão de esquema 1 (`contracts/queue-messages.md`): definição única.

Valida o corpo de um `job` (bytes recebidos do broker) e constrói/valida os eventos `progress`, `result`
e `error`. Só biblioteca padrão: este módulo nunca importa `router`, `pdfscan`, `pypdfium2`, `pika`, `psutil`
nem os motores de conversão, e por isso pode ser importado pela API (imagem só com o grupo `api`).

O shape de `report` é declarado como mapas estáticos (`REPORT_KINDS`, `ANALYSIS_KINDS`, `ATTEMPT_KINDS`,
`QUALITY_KINDS`). Um teste (`tests/test_jobqueue_schema.py`) confere que são iguais aos derivados por introspecção
dos dataclasses reais (`ConversionResult`, `Analysis`, `Attempt`, `Quality`) e que `ENGINES` é igual a
`router.ENGINES`: nada é inventado aqui.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

# Cópia literal de `router.ENGINES` (igualdade conferida em tests/test_jobqueue_schema.py).
ENGINES = ("auto", "docling", "pymupdf", "mineru", "markitdown")

SCHEMA_VERSION = 1
SUPPORTED_VERSIONS = (1,)
CONTENT_TYPE = "application/json"

MAX_BODY_BYTES = 16384  # 16 KiB (queue-messages.md, "Limites de entrada")
MAX_JSON_DEPTH = 8

JOB_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
_EMITTED_AT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$")

TYPE_JOB = "job"
TYPE_PROGRESS = "progress"
TYPE_RESULT = "result"
TYPE_ERROR = "error"
EVENT_TYPES = (TYPE_PROGRESS, TYPE_RESULT, TYPE_ERROR)

ROUTING_KEYS = {
    TYPE_PROGRESS: "job.progress",
    TYPE_RESULT: "job.result",
    TYPE_ERROR: "job.error",
}

# código -> definitiva (False) ou passageira (True): tabela "Códigos de erro" do contrato.
ERROR_CODES: dict[str, bool] = {
    "invalid_message": False,
    "unsupported_schema_version": False,
    "invalid_engine": False,
    "invalid_path": False,
    "pdf_not_found": False,
    "invalid_pdf": False,
    "worker_crashed": True,
    "conversion_failed": True,
    "conversion_timeout": True,
    "write_failed": True,
}

_JOB_FIELDS = frozenset({"schema_version", "type", "job_id", "pdf_path", "engine", "attempt"})


class ContractError(ValueError):
    """Mensagem fora do contrato. `code` é um de `ERROR_CODES`; `reason` está em português.

    `job_id` só vem preenchido quando a mensagem trazia um `job_id` válido (há a quem atribuir o evento).
    """

    def __init__(self, code: str, reason: str, job_id: str | None = None):
        super().__init__(reason)
        self.code = code
        self.reason = reason
        self.job_id = job_id


@dataclass(frozen=True)
class Job:
    job_id: str
    pdf_path: str
    engine: str = "auto"
    attempt: int = 1
    schema_version: int = SCHEMA_VERSION


# ── validação do job ────────────────────────────────────────────────────────────
def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def json_depth(body: bytes) -> int:
    """Maior profundidade de `{`/`[` fora de strings, sem analisar o JSON (evita `RecursionError`)."""
    depth = deepest = 0
    in_string = escaped = False
    for byte in body:
        if in_string:
            if escaped:
                escaped = False
            elif byte == 0x5C:  # barra invertida
                escaped = True
            elif byte == 0x22:  # aspas
                in_string = False
            continue
        if byte == 0x22:
            in_string = True
        elif byte in (0x7B, 0x5B):  # { [
            depth += 1
            deepest = max(deepest, depth)
        elif byte in (0x7D, 0x5D):  # } ]
            depth = max(0, depth - 1)
    return deepest


def _safe_job_id(obj: Mapping[str, Any]) -> str | None:
    value = obj.get("job_id")
    return value if isinstance(value, str) and JOB_ID_RE.match(value) else None


def parse_job(body: bytes) -> Job:
    """Valida o corpo de um job e devolve o objeto tipado; `ContractError` caso contrário."""
    if len(body) > MAX_BODY_BYTES:
        raise ContractError(
            "invalid_message",
            f"Mensagem com {len(body)} bytes, acima do limite de {MAX_BODY_BYTES} bytes.",
        )
    if json_depth(body) > MAX_JSON_DEPTH:
        raise ContractError(
            "invalid_message",
            f"JSON aninhado demais: o máximo é {MAX_JSON_DEPTH} níveis.",
        )
    try:
        obj = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise ContractError("invalid_message", "O corpo da mensagem não é JSON UTF-8 válido.") from None
    if not isinstance(obj, dict):
        raise ContractError("invalid_message", "O JSON da mensagem deve ser um objeto na raiz.")

    job_id = _safe_job_id(obj)
    version = obj.get("schema_version")
    if "schema_version" not in obj or not _is_int(version):
        raise ContractError("invalid_message", "Campo schema_version ausente ou não inteiro.", job_id)
    if version not in SUPPORTED_VERSIONS:
        supported = ", ".join(str(v) for v in SUPPORTED_VERSIONS)
        raise ContractError(
            "unsupported_schema_version",
            f"schema_version {version} não suportada; versões suportadas: {supported}.",
            job_id,
        )
    if obj.get("type") != TYPE_JOB:
        raise ContractError(
            "invalid_message", f"Campo type deve ser \"{TYPE_JOB}\" nesta fila.", job_id
        )
    if "job_id" not in obj:
        raise ContractError("invalid_message", "Campo job_id ausente.")
    if job_id is None:
        raise ContractError(
            "invalid_message", "Campo job_id inválido: use letras, dígitos, \"_\" e \"-\" (até 128)."
        )
    extra = sorted(set(obj) - _JOB_FIELDS)
    if extra:
        raise ContractError(
            "invalid_message", f"Campos desconhecidos no job: {', '.join(extra)}.", job_id
        )

    pdf_path = obj.get("pdf_path")
    if not isinstance(pdf_path, str) or not pdf_path:
        raise ContractError("invalid_message", "Campo pdf_path ausente ou não é texto.", job_id)
    engine = obj.get("engine", "auto")
    if not isinstance(engine, str):
        raise ContractError("invalid_message", "Campo engine deve ser texto.", job_id)
    if engine not in ENGINES:
        raise ContractError(
            "invalid_engine",
            f"Motor inválido: {engine}. Opções: {', '.join(ENGINES)}.",
            job_id,
        )
    attempt = obj.get("attempt", 1)
    if not _is_int(attempt) or attempt < 1:
        raise ContractError("invalid_message", "Campo attempt deve ser inteiro maior ou igual a 1.", job_id)
    return Job(job_id=job_id, pdf_path=pdf_path, engine=engine, attempt=attempt, schema_version=version)


def build_job(job_id: str, pdf_path: str, engine: str = "auto", attempt: int = 1) -> dict:
    """Mensagem de job como dicionário (usada em testes e na cópia de retry com `attempt` novo)."""
    return {
        "schema_version": SCHEMA_VERSION,
        "type": TYPE_JOB,
        "job_id": job_id,
        "pdf_path": pdf_path,
        "engine": engine,
        "attempt": attempt,
    }


# ── eventos ─────────────────────────────────────────────────────────────────────
def utc_now_iso(now: datetime | None = None) -> str:
    """ISO 8601 UTC com sufixo `Z` (milissegundos)."""
    moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}Z"


def _base(kind: str, job_id: str, attempt: int, now: datetime | None) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "type": kind,
        "job_id": job_id,
        "attempt": attempt,
        "emitted_at": utc_now_iso(now),
    }


def build_progress(job_id: str, attempt: int, message: str, now: datetime | None = None) -> dict:
    return {**_base(TYPE_PROGRESS, job_id, attempt, now), "message": message}


def build_result(
    job_id: str, attempt: int, markdown_path: str, engine: str, report: dict, now: datetime | None = None
) -> dict:
    return {
        **_base(TYPE_RESULT, job_id, attempt, now),
        "markdown_path": markdown_path,
        "engine": engine,
        "report": report,
    }


def build_error(
    job_id: str,
    attempt: int,
    code: str,
    reason: str,
    retryable: bool,
    max_attempts: int,
    now: datetime | None = None,
) -> dict:
    if code not in ERROR_CODES:
        raise ValueError(f"Código de erro desconhecido: {code}")
    return {
        **_base(TYPE_ERROR, job_id, attempt, now),
        "code": code,
        "reason": reason,
        "retryable": retryable,
        "max_attempts": max_attempts,
    }


def encode(message: Mapping[str, Any]) -> bytes:
    """Corpo da mensagem: JSON UTF-8 compacto."""
    return json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


# Tipos do `report`, declarados como mapas estáticos (espelham `ConversionResult.report()`, `Analysis.to_dict()`,
# `Attempt` e `Quality`; a igualdade com a introspecção é conferida em tests/test_jobqueue_schema.py).
QUALITY_KINDS: dict[str, str] = {
    "score": "number",
    "accepted": "bool",
    "tables_found": "int",
    "tables_expected": "int",
    "chars_per_page": "number",
    "issues": "list[str]",
}
ATTEMPT_KINDS: dict[str, str] = {
    "engine": "str",
    "seconds": "number",
    "quality": "quality|null",
    "error": "str|null",
}
ANALYSIS_KINDS: dict[str, str] = {
    "pages": "int",
    "text_pages": "int",
    "scanned_pages": "int",
    "total_chars": "int",
    "table_hints": "int",
    "formula_score": "int",
    "article_score": "int",
    "fully_scanned": "bool",
}
REPORT_KINDS: dict[str, str] = {
    "source": "str",
    "engine": "str",
    "analysis": "object",
    "attempts": "list[attempt]",
}


def _check_kind(kind: str, value: Any, where: str, *, allow_extra: bool = False) -> None:
    ok: bool
    if kind == "int":
        ok = _is_int(value)
    elif kind == "number":
        ok = _is_number(value)
    elif kind == "bool":
        ok = isinstance(value, bool)
    elif kind == "str":
        ok = isinstance(value, str)
    elif kind == "str|null":
        ok = value is None or isinstance(value, str)
    elif kind == "list[str]":
        ok = isinstance(value, list) and all(isinstance(v, str) for v in value)
    elif kind == "object":
        ok = isinstance(value, dict)
    elif kind == "quality|null":
        if value is None:
            return
        _check_object(value, QUALITY_KINDS, where, allow_extra=allow_extra)
        return
    elif kind == "list[attempt]":
        if not isinstance(value, list):
            raise ContractError("invalid_message", f"{where} deve ser uma lista.")
        for i, item in enumerate(value):
            _check_object(item, ATTEMPT_KINDS, f"{where}[{i}]", allow_extra=allow_extra)
        return
    else:  # pragma: no cover - protegido pelo teste de cobertura das anotações
        raise AssertionError(f"tipo de contrato desconhecido: {kind}")
    if not ok:
        raise ContractError("invalid_message", f"{where} tem tipo inválido (esperado {kind}).")


def _check_object(obj: Any, kinds: Mapping[str, str], where: str, *, allow_extra: bool = False) -> None:
    """Confere campos obrigatórios e tipos. Campo extra só é recusado com `allow_extra=False` (o job é estrito)."""
    if not isinstance(obj, dict):
        raise ContractError("invalid_message", f"{where} deve ser um objeto.")
    missing = sorted(set(kinds) - set(obj))
    extra = sorted(set(obj) - set(kinds))
    if missing:
        raise ContractError("invalid_message", f"{where}: campos ausentes: {', '.join(missing)}.")
    if extra and not allow_extra:
        raise ContractError("invalid_message", f"{where}: campos desconhecidos: {', '.join(extra)}.")
    for name, kind in kinds.items():
        _check_kind(kind, obj[name], f"{where}.{name}", allow_extra=allow_extra)


def validate_report(report: Any) -> None:
    """O `report` de `result` traz os campos de `ConversionResult.report()` (mapas estáticos), com os tipos certos.

    Campos desconhecidos nos objetos aninhados (`report`, `analysis`, `attempts[i]`, `quality`) são IGNORADOS: o
    consumidor de eventos tolera evolução compatível (FR-016; `queue-messages.md`, "Campos desconhecidos" e
    "Evolução"). Campo obrigatório ausente ou com tipo errado continua recusado. O job segue estrito."""
    _check_object(report, REPORT_KINDS, "report", allow_extra=True)
    _check_object(report["analysis"], ANALYSIS_KINDS, "report.analysis", allow_extra=True)


def validate_event(event: Any) -> dict:
    """Valida um evento (objeto ou corpo em bytes). Campos desconhecidos são aceitos (evolução compatível)."""
    if isinstance(event, (bytes, bytearray)):
        try:
            event = json.loads(bytes(event).decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise ContractError("invalid_message", "O evento não é JSON UTF-8 válido.") from None
    if not isinstance(event, dict):
        raise ContractError("invalid_message", "O evento deve ser um objeto JSON.")
    common = {
        "schema_version": "int",
        "type": "str",
        "job_id": "str",
        "attempt": "int",
        "emitted_at": "str",
    }
    kind = event.get("type")
    if kind not in EVENT_TYPES:
        raise ContractError("invalid_message", f"Tipo de evento inválido: {kind!r}.")
    specific = {
        TYPE_PROGRESS: {"message": "str"},
        TYPE_RESULT: {"markdown_path": "str", "engine": "str", "report": "object"},
        TYPE_ERROR: {"code": "str", "reason": "str", "retryable": "bool", "max_attempts": "int"},
    }[kind]
    for name, expected in {**common, **specific}.items():
        if name not in event:
            raise ContractError("invalid_message", f"Evento sem o campo {name}.")
        _check_kind(expected, event[name], name)
    if event["schema_version"] not in SUPPORTED_VERSIONS:
        raise ContractError("unsupported_schema_version", "schema_version do evento não suportada.")
    if not JOB_ID_RE.match(event["job_id"]):
        raise ContractError("invalid_message", "job_id do evento inválido.")
    if event["attempt"] < 1:
        raise ContractError("invalid_message", "attempt do evento deve ser maior ou igual a 1.")
    if not _EMITTED_AT_RE.match(event["emitted_at"]):
        raise ContractError("invalid_message", "emitted_at deve ser ISO 8601 UTC com sufixo Z.")
    if kind == TYPE_RESULT:
        validate_report(event["report"])
    if kind == TYPE_ERROR and event["code"] not in ERROR_CODES:
        raise ContractError("invalid_message", f"Código de erro desconhecido: {event['code']}.")
    return event
