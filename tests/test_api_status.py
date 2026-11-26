"""`GET /v1/status` (`api/routes/status.py`, tarefa 7.3.5; FR-017, FR-033): só estado em memória."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from artigo2md.api.app import create_app
from artigo2md.api.settings import ApiSettings


def settings():
    return ApiSettings.from_env({}, Path("/nonexistent-cwd-for-tests"))


async def exploding_factory(_settings):
    raise AssertionError("GET /v1/status não pode tocar o banco")


def make(**kwargs):
    return TestClient(create_app(settings(), store_factory=exploding_factory, **kwargs), base_url="http://127.0.0.1")


def test_status_without_background_services_reports_everything_disconnected():
    with make() as client:
        response = client.get("/v1/status")
    assert response.status_code == 200
    assert response.json() == {
        "consumer": {"connected": False, "last_event_at": None}, "publisher": {"connected": False},
    }


def test_status_reads_the_in_memory_state_of_consumer_and_publisher_without_touching_database():
    consumer = SimpleNamespace(connected=True, last_event_at="2026-01-02T03:04:05+00:00")
    publisher = SimpleNamespace(connected=True)
    with make(consumer=consumer, broker=publisher) as client:
        first = client.get("/v1/status").json()
        consumer.connected, publisher.connected = False, False
        second = client.get("/v1/status").json()
    assert first == {
        "consumer": {"connected": True, "last_event_at": "2026-01-02T03:04:05+00:00"},
        "publisher": {"connected": True},
    }
    assert second["consumer"]["connected"] is False and second["publisher"] == {"connected": False}


def test_health_is_unchanged_and_status_survives_database_and_broker_down():
    with make() as client:
        assert client.get("/health").json()["status"] == "ok"
        assert set(client.get("/health").json()) == {"status", "service", "version"}
        assert client.get("/v1/status").status_code == 200
