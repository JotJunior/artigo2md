"""Higiene dos logs da API (`src/artigo2md/api`, tarefa 10.2.3; FR-031, FR-032, plan S-08).

Verificação estática (AST): toda chamada `logger.<nível>(...)` tem texto em português, nunca recebe o objeto de
exceção (nem `exc_info`, que despejaria o texto bruto do driver e a pilha), nem corpo de evento, nem senha.
Só o NOME do tipo da exceção (`type(exc).__name__`) e identificadores/contadores entram nos logs.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

API_DIR = Path(__file__).resolve().parent.parent / "src" / "artigo2md" / "api"
LEVELS = {"debug", "info", "warning", "error", "exception", "critical"}
FORBIDDEN_NAMES = {"exc", "e", "err", "error", "body", "event", "payload", "password", "secret", "credentials"}
ENGLISH_MARKERS = (" the ", " failed ", " error ", " could not ", " unable ", " invalid ", " connection ")


def _logger_calls():
    for path in sorted(API_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in LEVELS
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "logger"
            ):
                yield path, node


SAFE_ATTRIBUTES = {"code"}  # `exc.code`: código curto do contrato da fila, nunca o texto do driver
SAFE_EVENT_KEYS = {"code", "attempt"}  # `event["code"]`: já validado contra `ERROR_CODES` antes de chegar ao log
# Mensagens montadas em `settings.py` citam só nomes de variável (nunca valores); ver `_load_*` do módulo.
PRE_BUILT_MESSAGES = {("settings.py", "message")}


def _unsafe_names(arg: ast.AST):
    """Nomes proibidos que chegam ao log, ignorando as formas seguras (`type(exc)`, `exc.code`, `event["code"]`)."""
    if isinstance(arg, ast.Call) and isinstance(arg.func, ast.Name) and arg.func.id == "type":
        return  # `type(exc).__name__`: só o nome do tipo
    if isinstance(arg, ast.Attribute) and arg.attr in SAFE_ATTRIBUTES:
        return
    if (
        isinstance(arg, ast.Subscript)
        and isinstance(arg.slice, ast.Constant)
        and arg.slice.value in SAFE_EVENT_KEYS
    ):
        return
    if isinstance(arg, ast.Name) and arg.id in FORBIDDEN_NAMES:
        yield arg.id
    for child in ast.iter_child_nodes(arg):
        yield from _unsafe_names(child)


def _message(node: ast.Call) -> str:
    if not node.args:
        return ""
    first = node.args[0]
    return "".join(
        part.value for part in ast.walk(first) if isinstance(part, ast.Constant) and isinstance(part.value, str)
    )


CALLS = list(_logger_calls())


def test_the_scan_finds_the_api_logger_calls():
    assert len(CALLS) >= 30  # se cair a zero, o AST deixou de enxergar os logs e o guarda virou decorativo


@pytest.mark.parametrize("path,node", CALLS, ids=lambda v: f"{v.name}" if isinstance(v, Path) else f"L{v.lineno}")
def test_log_call_never_carries_raw_exception_event_body_or_secret(path, node):
    where = f"{path.name}:{node.lineno}"
    assert node.func.attr != "exception", f"{where}: logger.exception despeja a pilha com o texto bruto do driver"
    assert not any(kw.arg == "exc_info" for kw in node.keywords), f"{where}: exc_info"
    for arg in [*node.args[1:], *(kw.value for kw in node.keywords)]:
        found = list(_unsafe_names(arg))
        assert not found, f"{where}: {found} vai ao log"
        for sub in ast.walk(arg):
            if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name) and sub.func.id in {"str", "repr"}:
                pytest.fail(f"{where}: {sub.func.id}() de objeto arbitrário no log")


@pytest.mark.parametrize("path,node", CALLS, ids=lambda v: f"{v.name}" if isinstance(v, Path) else f"L{v.lineno}")
def test_log_message_is_in_portuguese(path, node):
    text = f" {_message(node).lower()} "
    where = f"{path.name}:{node.lineno}"
    first = node.args[0] if node.args else None
    if isinstance(first, ast.Name) and (path.name, first.id) in PRE_BUILT_MESSAGES:
        return
    assert text.strip(), f"{where}: mensagem não literal"
    assert not any(marker in text for marker in ENGLISH_MARKERS), f"{where}: texto em inglês: {text.strip()}"


@pytest.mark.parametrize("expr", ["exc", "body", "event", "f'{exc}'", "event['reason']", "exc.args"])
def test_the_checker_flags_unsafe_log_arguments(expr):
    assert list(_unsafe_names(ast.parse(expr, mode="eval").body))


@pytest.mark.parametrize("expr", ["type(exc).__name__", "exc.code", "event['code']", "job_id", "delay"])
def test_the_checker_accepts_safe_log_arguments(expr):
    assert not list(_unsafe_names(ast.parse(expr, mode="eval").body))
