"""Topologia declarada (tarefa 4.2 da `conversion-queue`): canal falso, sem broker."""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

import fake_amqp
from artigo2md.jobqueue import topology
from artigo2md.jobqueue.topology import TopologyMismatch

SPEC = Path(__file__).resolve().parent.parent / "docs" / "specs" / "conversion-queue"


def _channel(broker=None):
    broker = broker or fake_amqp.FakeBroker()
    return broker, broker.connection().channel()


def test_declares_the_eight_objects_with_the_expected_arguments():
    broker, channel = _channel()
    topology.declare(channel)
    assert {n: (e["type"], e["durable"], e["arguments"]) for n, e in broker.exchanges.items()} == {
        "artigo2md.jobs": ("direct", True, {}),
        "artigo2md.retry": ("direct", True, {}),
        "artigo2md.dead": ("direct", True, {}),
        "artigo2md.events": ("topic", True, {}),
    }
    queues = {n: (q.durable, q.arguments) for n, q in broker.queues.items()}
    assert queues == {
        "artigo2md.jobs.convert": (True, {"x-queue-type": "classic"}),
        "artigo2md.jobs.retry": (True, {"x-queue-type": "classic", "x-dead-letter-exchange": "artigo2md.jobs",
                                        "x-dead-letter-routing-key": "convert"}),
        "artigo2md.jobs.dead": (True, {"x-queue-type": "classic"}),
        "artigo2md.events.all": (True, {"x-queue-type": "classic"}),
    }
    assert set(broker.bindings) == {
        ("artigo2md.jobs", "artigo2md.jobs.convert", "convert"),
        ("artigo2md.retry", "artigo2md.jobs.retry", "convert"),
        ("artigo2md.dead", "artigo2md.jobs.dead", "dead"),
        ("artigo2md.events", "artigo2md.events.all", "job.#"),
    }


def test_retry_queue_has_no_ttl_so_retry_delay_never_changes_the_topology():
    for queue in topology.QUEUES:
        assert not any(key.startswith("x-message-ttl") or key == "x-expires" for key in queue.arguments)


def test_declaration_is_idempotent_across_channels_and_connections():
    broker, channel = _channel()
    topology.declare(channel)
    snapshot = (dict(broker.exchanges), {n: (q.durable, dict(q.arguments)) for n, q in broker.queues.items()},
                list(broker.bindings))
    topology.declare(channel)
    topology.declare(broker.connection().channel())
    assert (dict(broker.exchanges), {n: (q.durable, dict(q.arguments)) for n, q in broker.queues.items()},
            list(broker.bindings)) == snapshot


@pytest.mark.parametrize("kind,name,preexisting", [
    ("queue", "artigo2md.jobs.convert", dict(durable=False)),
    ("queue", "artigo2md.jobs.retry", dict(durable=True, arguments={"x-message-ttl": 5000})),
    ("exchange", "artigo2md.events", dict(exchange_type="direct", durable=True)),
])
def test_incompatible_existing_object_becomes_a_portuguese_message_and_nothing_changes(kind, name, preexisting):
    broker, channel = _channel()
    if kind == "queue":
        channel.queue_declare(queue=name, **preexisting)
    else:
        channel.exchange_declare(exchange=name, **preexisting)
    exchanges_before = {n: dict(e) for n, e in broker.exchanges.items()}
    queues_before = {n: (q.durable, dict(q.arguments)) for n, q in broker.queues.items()}
    with pytest.raises(TopologyMismatch) as info:
        topology.declare(broker.connection().channel())
    assert info.value.object_name == name
    assert info.value.message == (f"A fila/exchange {name} já existe no RabbitMQ com configuração diferente. "
                                  "Nada foi alterado. Remova-a pelo painel ou alinhe a configuração.")
    # nada apagado nem sobrescrito: os objetos que já existiam continuam exatamente como estavam
    for n, e in exchanges_before.items():
        assert broker.exchanges[n] == e
    for n, q in queues_before.items():
        assert (broker.queues[n].durable, broker.queues[n].arguments) == q


def test_other_errors_are_not_masked_as_topology_mismatch():
    broker, channel = _channel()
    broker.set_down()
    with pytest.raises(Exception) as info:
        topology.declare(channel)
    assert not isinstance(info.value, TopologyMismatch)


# ── documento e código não divergem (4.2.5) ──────────────────────────────────────
def test_names_and_arguments_match_research_decision_5():
    text = (SPEC / "research.md").read_text(encoding="utf-8")
    section = text.split("## Decision 5:", 1)[1].split("\n## ", 1)[0]
    rows = re.findall(r"^\| `(artigo2md\.[\w.]+)` \| (exchange `\w+`|fila clássica) \| sim \| ([^|]*)\|", section,
                      flags=re.M)
    assert len(rows) == 8
    documented = {}
    for name, kind, args in rows:
        documented[name] = (kind, dict(re.findall(r"`(x-[\w-]+)=([^`]+)`", args)))
    code = {e.name: (f"exchange `{e.kind}`", {}) for e in topology.EXCHANGES}
    code.update({q.name: ("fila clássica", {k: str(v) for k, v in q.arguments.items()}) for q in topology.QUEUES})
    assert documented == code


def test_dead_letter_and_event_names_match_the_message_contract():
    text = (SPEC / "contracts" / "queue-messages.md").read_text(encoding="utf-8")
    for name in (topology.EXCHANGE_JOBS, topology.EXCHANGE_EVENTS, topology.EXCHANGE_DEAD, topology.QUEUE_DEAD,
                 topology.RK_CONVERT, topology.RK_PROGRESS, topology.RK_RESULT, topology.RK_ERROR):
        assert f"`{name}`" in text, name
    assert f"routing key `{topology.RK_DEAD}`" in text
    assert f"routing key `{topology.RK_CONVERT}`" in text


def test_module_does_not_import_pika():
    code = "import sys, artigo2md.jobqueue.topology\nprint('pika' in sys.modules, 'psutil' in sys.modules)"
    out = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "False False"
