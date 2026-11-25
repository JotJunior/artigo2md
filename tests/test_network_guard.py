"""Teste da guarda de rede do `conftest.py` (tarefa 2.3.5)."""
from __future__ import annotations

import socket

import pytest

from conftest import _is_loopback


def test_is_loopback_classification():
    assert _is_loopback(("127.0.0.1", 5672))
    assert _is_loopback(("::1", 5672, 0, 0))
    assert _is_loopback(("localhost", 80))
    assert not _is_loopback(("192.0.2.1", 5672))
    assert not _is_loopback(("exemplo.invalid", 80))


def test_outbound_connection_is_refused_by_the_guard():
    with socket.socket() as s:
        with pytest.raises(AssertionError, match="conexão de rede"):
            s.connect(("192.0.2.1", 5672))  # TEST-NET-1 (RFC 5737): nunca roteável


def test_loopback_connection_is_still_allowed_for_local_servers():
    with socket.socket() as srv:
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        with socket.socket() as cli:
            cli.connect(srv.getsockname())
