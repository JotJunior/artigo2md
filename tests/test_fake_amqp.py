"""Testes do corretor AMQP falso (tarefa 2.3.4) e da conformidade das assinaturas com o pika real (2.2.5)."""
from __future__ import annotations

import inspect
from types import SimpleNamespace

import pytest

from fake_amqp import FakeBroker, FakeChannel, FakeClock, FakeConnection, amqp_exc, topic_matches


def make(clock=None):
    broker = FakeBroker(clock)
    conn = broker.connection()
    ch = conn.channel()
    ch.exchange_declare("ex", "direct", durable=True)
    ch.queue_declare("q", durable=True)
    ch.queue_bind("q", "ex", "k")
    return broker, conn, ch


def props(**kw):
    return SimpleNamespace(**kw)


def test_prefetch_one_holds_at_most_one_unacked_delivery():
    broker, conn, ch = make()
    ch.basic_qos(prefetch_count=1)
    got = []
    ch.basic_consume("q", lambda c, m, p, b: got.append((m.delivery_tag, b)))
    for body in (b"a", b"b", b"c"):
        ch.basic_publish("ex", "k", body)
    conn.process_data_events()
    assert [b for _, b in got] == [b"a"]  # nunca mais de uma sem ack
    ch.basic_ack(got[0][0])
    conn.process_data_events()
    assert [b for _, b in got] == [b"a", b"b"]
    assert broker.depth("q") == 1


def test_connection_loss_returns_unacked_message_as_redelivered():
    broker, conn, ch = make()
    ch.basic_qos(prefetch_count=1)
    ch.basic_consume("q", lambda *a: None)
    ch.basic_publish("ex", "k", b"job")
    conn.process_data_events()
    assert broker.depth("q") == 0
    conn.lose()
    assert broker.depth("q") == 1
    assert broker.peek("q")[0].redelivered is True
    with pytest.raises(amqp_exc.AMQPConnectionError):
        conn.process_data_events()


def test_nack_requeue_puts_message_back_at_the_head_flagged_redelivered():
    broker, conn, ch = make()
    ch.basic_qos(prefetch_count=1)
    seen = []
    ch.basic_consume("q", lambda c, m, p, b: seen.append(m))
    ch.basic_publish("ex", "k", b"x")
    conn.process_data_events()
    ch.basic_nack(seen[0].delivery_tag, requeue=True)
    conn.process_data_events()
    assert seen[1].redelivered is True and seen[0].redelivered is False


def test_cancel_stops_new_deliveries_and_returns_pending_ones():
    broker, conn, ch = make()
    ch.basic_qos(prefetch_count=1)
    tag = ch.basic_consume("q", lambda *a: None)
    ch.basic_publish("ex", "k", b"x")
    conn.broker.dispatch()  # entrega despachada ao canal, ainda não processada
    returned = ch.basic_cancel(tag)
    assert len(returned) == 1
    ch.basic_publish("ex", "k", b"y")
    conn.process_data_events()
    assert broker.depth("q") == 1  # "y" não foi entregue a ninguém


def test_publisher_confirm_denied_raises_nack_error():
    broker, conn, ch = make()
    ch.confirm_delivery()
    broker.nack_next_publishes = 1
    with pytest.raises(amqp_exc.NackError):
        ch.basic_publish("ex", "k", b"x", mandatory=True)
    ch.basic_publish("ex", "k", b"x", mandatory=True)  # a seguinte passa


def test_mandatory_unroutable_with_confirm_raises():
    broker, conn, ch = make()
    ch.confirm_delivery()
    with pytest.raises(amqp_exc.UnroutableError):
        ch.basic_publish("ex", "outra-chave", b"x", mandatory=True)


def test_per_message_expiration_dead_letters_back_to_the_jobs_exchange():
    clock = FakeClock()
    broker, conn, ch = make(clock)
    ch.exchange_declare("retry", "direct", durable=True)
    ch.queue_declare("wait", durable=True, arguments={"x-dead-letter-exchange": "ex",
                                                     "x-dead-letter-routing-key": "k"})
    ch.queue_bind("wait", "retry", "k")
    ch.basic_publish("retry", "k", b"job", props(expiration="1000", headers={"n": 1}))
    assert broker.depth("wait") == 1 and broker.depth("q") == 0
    clock.advance(0.5)
    assert broker.depth("wait") == 1 and broker.depth("q") == 0
    clock.advance(0.6)
    assert broker.depth("wait") == 0 and broker.depth("q") == 1
    assert broker.peek("q")[0].body == b"job"


def test_topology_redeclaration_is_idempotent_and_divergence_is_precondition_failed():
    broker, conn, ch = make()
    ch.queue_declare("q", durable=True)  # idêntica: ok
    other = conn.channel()
    with pytest.raises(amqp_exc.ChannelClosedByBroker) as err:
        other.queue_declare("q", durable=True, arguments={"x-queue-type": "classic"})
    assert err.value.reply_code == 406


def test_topic_exchange_routing():
    assert topic_matches("job.#", "job.progress")
    assert topic_matches("job.*", "job.result")
    assert not topic_matches("job.*", "other.result")
    assert topic_matches("#", "a.b.c")


def test_broker_down_refuses_new_connections_and_kills_open_ones():
    broker, conn, ch = make()
    broker.set_down()
    with pytest.raises(amqp_exc.AMQPConnectionError):
        broker.connection()
    assert conn.closed
    broker.set_up()
    assert broker.connection().is_open


def test_idle_loop_counts_iterations_and_records_time_limit():
    broker, conn, ch = make()
    for _ in range(3):
        conn.process_data_events(time_limit=0.5)
    assert conn.process_calls == 3 and conn.last_time_limit == 0.5


# ── conformidade das assinaturas com o pika real (2.2.5): só com o extra `queue` instalado ──
def _pika_pairs():
    pika = pytest.importorskip("pika")
    from pika.adapters.blocking_connection import BlockingChannel, BlockingConnection

    return [
        (BlockingChannel, FakeChannel, ["exchange_declare", "queue_declare", "queue_bind", "basic_qos",
                                        "confirm_delivery", "basic_publish", "basic_consume",
                                        "basic_cancel", "basic_ack", "basic_nack"]),
        (BlockingConnection, FakeConnection, ["process_data_events"]),
    ]


def test_fake_signatures_match_the_real_pika_parameter_names_and_defaults():
    for real, fake, names in _pika_pairs():
        for name in names:
            rs = inspect.signature(getattr(real, name))
            fs = inspect.signature(getattr(fake, name))
            real_params = [(p.name, p.default if p.default is not inspect.Parameter.empty else None)
                           for p in rs.parameters.values()]
            fake_params = [(p.name, p.default if p.default is not inspect.Parameter.empty else None)
                           for p in fs.parameters.values()]
            assert [n for n, _ in fake_params] == [n for n, _ in real_params], (real.__name__, name)
            # defaults relevantes iguais (exchange_type do pika é enum: compara pelo valor)
            for (n, rd), (_, fd) in zip(real_params, fake_params):
                rd = getattr(rd, "value", rd)
                assert fd == rd, (real.__name__, name, n)


def test_fake_exceptions_are_the_real_pika_ones_when_available():
    pytest.importorskip("pika")
    import pika.exceptions as real

    assert amqp_exc.NackError is real.NackError
    assert amqp_exc.ChannelClosedByBroker is real.ChannelClosedByBroker
