"""Testes da seção de infraestrutura do `doctor` (`artigo2md.infra`), sem Docker.

Os serviços são simulados por servidores falsos em 127.0.0.1, porta efêmera.
"""
from __future__ import annotations

import contextlib
import http.server
import json
import socket
import threading
import time

import pytest

from artigo2md import infra

# ── servidores falsos ────────────────────────────────────────────────────────────


@contextlib.contextmanager
def fake_tcp(handler):
    """Servidor TCP falso: `handler(conn, received_list)` roda por conexão. Devolve (porta, recebidos)."""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(8)
    srv.settimeout(0.2)
    received: list[bytes] = []
    stop = threading.Event()
    conns: list[socket.socket] = []

    def serve():
        while not stop.is_set():
            try:
                conn, _ = srv.accept()
            except (TimeoutError, OSError):
                continue
            conns.append(conn)
            threading.Thread(target=handler, args=(conn, received), daemon=True).start()

    t = threading.Thread(target=serve, daemon=True)
    t.start()
    try:
        yield srv.getsockname()[1], received
    finally:
        stop.set()
        for c in conns:
            with contextlib.suppress(OSError):
                c.close()
        srv.close()
        t.join(1)


def replier(reply: bytes, read: int):
    def handler(conn, received):
        try:
            received.append(conn.recv(read))
            if reply:
                conn.sendall(reply)
        except OSError:
            pass
        finally:
            conn.close()

    return handler


def hanger(conn, received):
    """Aceita e nunca responde (até o teste fechar a conexão)."""
    with contextlib.suppress(OSError):
        conn.settimeout(15)
        conn.recv(1024)
        time.sleep(15)


def closed_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@contextlib.contextmanager
def fake_http(status=200, body=b'{"status": "ok", "service": "artigo2md-api", "version": "0.1.0"}'):
    seen: dict = {}

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            seen["path"] = self.path
            seen["host"] = self.headers.get("Host")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield srv.server_address[1], seen
    finally:
        srv.shutdown()
        srv.server_close()
        t.join(1)


# ── 3.1: parser do .env e resolução de portas ────────────────────────────────────
def test_parse_env_file_normalizes_like_the_stack_scripts(tmp_path):
    f = tmp_path / ".env"
    f.write_text(
        "# comentário\n"
        "\n"
        "ARTIGO2MD_POSTGRES_PORT=5433\n"
        "export ARTIGO2MD_RABBITMQ_PORT = 5673 \n"
        'ARTIGO2MD_API_PORT="8779"\n'
        "OUTRA='valor com espaço'\n"
        "linha sem igual\n"
        "=sem chave\n",
        encoding="utf-8",
    )

    values = infra.parse_env_file(f)

    assert values == {
        "ARTIGO2MD_POSTGRES_PORT": "5433",
        "ARTIGO2MD_RABBITMQ_PORT": "5673",
        "ARTIGO2MD_API_PORT": "8779",
        "OUTRA": "valor com espaço",
    }


def test_parse_env_file_does_not_execute_anything(tmp_path):
    marker = tmp_path / "executou"
    f = tmp_path / ".env"
    f.write_text(f"X=$(touch {marker})\nY=`touch {marker}`\n", encoding="utf-8")

    values = infra.parse_env_file(f)

    assert not marker.exists()
    assert values["X"].startswith("$(touch")


def test_parse_env_file_missing_file_gives_empty_dict(tmp_path):
    assert infra.parse_env_file(tmp_path / "nao-existe") == {}


def _ports(resolutions):
    return {k: (v.port, v.origin, v.invalid) for k, v in resolutions.items()}


def test_resolve_ports_defaults(tmp_path):
    res = infra.resolve_ports({}, cwd=tmp_path)

    assert _ports(res) == {
        "postgres": (5432, "padrão", False),
        "rabbitmq": (5672, "padrão", False),
        "api": (8778, "padrão", False),
    }


def test_resolve_ports_reads_stack_env_in_cwd(tmp_path):
    (tmp_path / "stack").mkdir()
    (tmp_path / "stack" / ".env").write_text("ARTIGO2MD_API_PORT=9000\n", encoding="utf-8")

    res = infra.resolve_ports({}, cwd=tmp_path)

    assert _ports(res)["api"] == (9000, "stack/.env", False)
    assert _ports(res)["postgres"] == (5432, "padrão", False)


def test_resolve_ports_order_environment_then_stack_env_var_then_stack_file(tmp_path):
    (tmp_path / "stack").mkdir()
    (tmp_path / "stack" / ".env").write_text(
        "ARTIGO2MD_POSTGRES_PORT=1111\nARTIGO2MD_RABBITMQ_PORT=2222\nARTIGO2MD_API_PORT=3333\n",
        encoding="utf-8",
    )
    other = tmp_path / "outro.env"
    other.write_text("ARTIGO2MD_POSTGRES_PORT=4444\nARTIGO2MD_RABBITMQ_PORT=5555\n", encoding="utf-8")
    environ = {"ARTIGO2MD_STACK_ENV": str(other), "ARTIGO2MD_POSTGRES_PORT": "6666"}

    res = infra.resolve_ports(environ, cwd=tmp_path)

    assert _ports(res) == {
        "postgres": (6666, "ambiente", False),
        "rabbitmq": (5555, "ARTIGO2MD_STACK_ENV", False),
        "api": (3333, "stack/.env", False),
    }


def test_resolve_ports_missing_stack_env_var_file_falls_through(tmp_path):
    res = infra.resolve_ports({"ARTIGO2MD_STACK_ENV": str(tmp_path / "nada.env")}, cwd=tmp_path)

    assert _ports(res)["postgres"] == (5432, "padrão", False)


def test_resolve_ports_empty_value_counts_as_unset(tmp_path):
    res = infra.resolve_ports({"ARTIGO2MD_API_PORT": "  "}, cwd=tmp_path)

    assert _ports(res)["api"] == (8778, "padrão", False)


@pytest.mark.parametrize("bad", ["abc", "0", "65536", "-1", "54 32", "5432abc", "５４３２", "1.5"])
def test_resolve_ports_invalid_value_is_flagged_without_exception(tmp_path, bad):
    res = infra.resolve_ports({"ARTIGO2MD_RABBITMQ_PORT": bad}, cwd=tmp_path)

    assert res["rabbitmq"].invalid is True
    assert res["rabbitmq"].port is None
    assert res["rabbitmq"].var == "ARTIGO2MD_RABBITMQ_PORT"
    assert res["rabbitmq"].origin == "ambiente"


@pytest.mark.parametrize("good", ["1", "65535", " 8080 "])
def test_resolve_ports_boundaries_are_valid(tmp_path, good):
    res = infra.resolve_ports({"ARTIGO2MD_API_PORT": good}, cwd=tmp_path)

    assert res["api"].invalid is False
    assert res["api"].port == int(good)


def test_resolve_ports_invalid_value_does_not_fall_through_to_next_source(tmp_path):
    (tmp_path / "stack").mkdir()
    (tmp_path / "stack" / ".env").write_text("ARTIGO2MD_API_PORT=9000\n", encoding="utf-8")

    res = infra.resolve_ports({"ARTIGO2MD_API_PORT": "abc"}, cwd=tmp_path)

    assert res["api"].invalid is True


# ── 3.2: Postgres ────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("first", [b"S", b"N", b"E"])
def test_postgres_sslrequest_answers_s_n_or_e_are_reachable(first):
    reply = first + (b"\x00\x00\x00\x10SFATAL\x00" if first == b"E" else b"")
    with fake_tcp(replier(reply, 8)) as (port, received):
        assert infra.check_postgres(port) == infra.REACHABLE

    assert received[0] == bytes([0, 0, 0, 8, 4, 210, 22, 47])  # Int32(8) + Int32(80877103)


def test_postgres_unexpected_first_byte_is_unreachable():
    with fake_tcp(replier(b"HTTP/1.1 400\r\n", 8)) as (port, _):
        assert infra.check_postgres(port) == infra.UNREACHABLE


def test_postgres_connection_closed_without_reply_is_unreachable():
    with fake_tcp(replier(b"", 8)) as (port, _):
        assert infra.check_postgres(port) == infra.UNREACHABLE


def test_postgres_refused_is_unreachable():
    assert infra.check_postgres(closed_port()) == infra.UNREACHABLE


# ── 3.2: RabbitMQ ────────────────────────────────────────────────────────────────
CONNECTION_START = b"\x01\x00\x00\x00\x00\x01\x2a\x00\x0a\x00\x0a\x00\x09"


def test_rabbitmq_connection_start_frame_is_reachable():
    with fake_tcp(replier(CONNECTION_START, 8)) as (port, received):
        assert infra.check_rabbitmq(port) == infra.REACHABLE

    assert received[0] == b"AMQP\x00\x00\x09\x01"


def test_rabbitmq_answering_with_its_own_header_is_protocol_mismatch():
    with fake_tcp(replier(b"AMQP\x00\x00\x09\x01", 8)) as (port, _):
        assert infra.check_rabbitmq(port) == infra.PROTOCOL_MISMATCH


@pytest.mark.parametrize(
    "reply",
    [b"", b"\x01\x00", b"\x02\x00\x00\x00\x00\x00\x00", b"\x01\x00\x01\x00\x00\x00\x00", b"HTTP/1.1 400 Bad\r\n"],
    ids=["fechou-sem-resposta", "parcial", "tipo-errado", "canal-errado", "outro-protocolo"],
)
def test_rabbitmq_partial_or_wrong_replies_are_unreachable(reply):
    with fake_tcp(replier(reply, 8)) as (port, _):
        assert infra.check_rabbitmq(port) == infra.UNREACHABLE


def test_rabbitmq_refused_is_unreachable():
    assert infra.check_rabbitmq(closed_port()) == infra.UNREACHABLE


# ── 3.2: API ─────────────────────────────────────────────────────────────────────
def test_api_health_ok_is_reachable_and_host_header_has_the_port():
    with fake_http() as (port, seen):
        assert infra.check_api(port) == infra.REACHABLE

    assert seen["path"] == "/health"
    assert seen["host"] == f"127.0.0.1:{port}"


def test_api_tolerates_extra_keys_in_health_payload():
    # Compatibilidade do contrato (api CHK008): chaves novas não quebram o doctor.
    body = json.dumps(
        {"status": "ok", "service": "artigo2md-api", "version": "9.9.9", "extra": {"a": 1}}
    ).encode()
    with fake_http(body=body) as (port, _):
        assert infra.check_api(port) == infra.REACHABLE


@pytest.mark.parametrize(
    ("status", "body"),
    [
        (200, b'{"status": "degraded"}'),
        (200, b"[1, 2]"),
        (200, b"nao eh json"),
        (200, b""),
        (500, b'{"status": "ok"}'),
        (404, b'{"status": "ok"}'),
    ],
)
def test_api_other_replies_are_unreachable(status, body):
    with fake_http(status=status, body=body) as (port, _):
        assert infra.check_api(port) == infra.UNREACHABLE


def test_api_refused_is_unreachable():
    assert infra.check_api(closed_port()) == infra.UNREACHABLE


def test_api_check_ignores_proxy_environment(monkeypatch):
    dead = closed_port()
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY"):
        monkeypatch.setenv(var, f"http://127.0.0.1:{dead}")
    monkeypatch.setenv("NO_PROXY", "")
    monkeypatch.setenv("no_proxy", "")
    with fake_http() as (port, _):
        assert infra.check_api(port) == infra.REACHABLE


# ── 3.2.4: paralelo e tempo limite ───────────────────────────────────────────────
def test_collect_reports_all_services_and_their_states(tmp_path):
    with fake_tcp(replier(b"S", 8)) as (pg, _), fake_tcp(replier(b"AMQP\x00\x00\x09\x01", 8)) as (mq, _):
        environ = {
            "ARTIGO2MD_POSTGRES_PORT": str(pg),
            "ARTIGO2MD_RABBITMQ_PORT": str(mq),
            "ARTIGO2MD_API_PORT": str(closed_port()),
        }
        reports = infra.collect(environ, cwd=tmp_path)

    by_service = {r.service: r for r in reports}
    assert [r.service for r in reports] == ["postgres", "rabbitmq", "api"]
    assert by_service["postgres"].state == infra.REACHABLE
    assert by_service["postgres"].message == "acessível"
    assert by_service["rabbitmq"].state == infra.PROTOCOL_MISMATCH
    assert by_service["rabbitmq"].message == "inacessível (versão de protocolo incompatível)"
    assert by_service["api"].state == infra.UNREACHABLE


def test_collect_servers_that_never_reply_stay_under_five_seconds(tmp_path):
    with fake_tcp(hanger) as (pg, _), fake_tcp(hanger) as (mq, _), fake_tcp(hanger) as (api, _):
        environ = {
            "ARTIGO2MD_POSTGRES_PORT": str(pg),
            "ARTIGO2MD_RABBITMQ_PORT": str(mq),
            "ARTIGO2MD_API_PORT": str(api),
        }
        start = time.monotonic()
        reports = infra.collect(environ, cwd=tmp_path)
        elapsed = time.monotonic() - start

    assert elapsed < 5.0
    assert {r.state for r in reports} == {infra.UNREACHABLE}


def test_collect_invalid_port_is_reported_without_connecting(tmp_path):
    reports = infra.collect({"ARTIGO2MD_POSTGRES_PORT": "abc"}, cwd=tmp_path)

    pg = reports[0]
    assert pg.state == infra.INVALID_PORT
    assert pg.port is None
    assert pg.message == "inacessível (porta inválida em ARTIGO2MD_POSTGRES_PORT)"


# ── formatação ───────────────────────────────────────────────────────────────────
def _report(service, port, state, message, origin="padrão", var="X"):
    return infra.InfraReport(service, "127.0.0.1", port, state, message, var, origin)


def test_format_section_all_reachable_has_no_instruction_line():
    reports = [
        _report("postgres", 5432, infra.REACHABLE, "acessível"),
        _report("rabbitmq", 5672, infra.REACHABLE, "acessível"),
        _report("api", 8778, infra.REACHABLE, "acessível"),
    ]

    lines = infra.format_section(reports)

    assert lines[0] == "Infraestrutura (versão desktop, opcional):"
    assert lines[1].split() == ["postgres", "127.0.0.1:5432", "acessível"]
    assert not any("sh stack/up.sh" in line for line in lines)


def test_format_section_shows_instruction_and_origin_when_something_is_down():
    reports = [
        _report("postgres", 5432, infra.REACHABLE, "acessível", origin="stack/.env"),
        _report("rabbitmq", None, infra.INVALID_PORT, "inacessível (porta inválida em V)", origin="ambiente"),
        _report("api", 8778, infra.UNREACHABLE, "inacessível"),
    ]

    lines = infra.format_section(reports)

    assert lines[2].split(maxsplit=1)[1] == "inacessível (porta inválida em V)"
    assert "(portas: ambiente, stack/.env, padrão)" in lines[-2]
    assert lines[-1] == "Para subir a infraestrutura: sh stack/up.sh (requer Docker; veja stack/.env.example)."
