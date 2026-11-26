"""Fluxo ao vivo SSE (`api/live.py` e `api/routes/events.py`, tarefas 8.1 e 8.2; FR-019, FR-020, SC-004, S-03).

Duas camadas: o difusor (`LiveHub`) direto, e a aplicação inteira sobre ASGI puro (o `httpx.ASGITransport` não entrega
corpo em fluxo, então o SSE é lido com um pequeno condutor ASGI que enxerga cada quadro assim que ele sai).
"""
from __future__ import annotations

import asyncio
import copy
import json
import logging
from pathlib import Path

import httpx
import pytest

from artigo2md.api.app import create_app
from artigo2md.api.errors import ApiError
from artigo2md.api.live import KEEPALIVE_FRAME, LiveHub, format_event
from artigo2md.api.settings import ApiSettings
from artigo2md.jobqueue import schema
from fake_store import FakeStore
from queue_support import report

HOST = "/host/data"
QUALITY = {"score": 80.0, "accepted": True, "tables_found": 1, "tables_expected": 1, "chars_per_page": 450.0,
           "issues": []}


# ── difusor ─────────────────────────────────────────────────────────────────────
def hub_run(coro):
    return asyncio.run(coro)


def test_format_event_is_one_data_line_without_id():
    frame = format_event("progress", {"job_id": 1, "message": "Olá\nmundo"})
    assert frame.startswith("event: progress\ndata: ") and frame.endswith("\n\n")
    assert "\nid:" not in frame and not frame.startswith("id:")
    lines = frame.rstrip("\n").split("\n")
    assert len(lines) == 2 and json.loads(lines[1][len("data: "):]) == {"job_id": 1, "message": "Olá\nmundo"}


def test_hub_delivers_only_to_subscribers_of_the_project_and_to_all_of_them():
    async def main():
        hub = LiveHub()
        a1, a2, b = hub.subscribe(1), hub.subscribe(1), hub.subscribe(2)
        assert hub.publish(1, "job_state", {"job_id": 7}) == 2
        assert a1.queue.qsize() == 1 and a2.queue.qsize() == 1 and b.queue.qsize() == 0
        assert hub.publish(3, "job_state", {}) == 0  # ninguém no projeto 3
        assert hub.subscriber_count == 3

    hub_run(main())


def test_hub_keeps_the_order_of_a_job():
    async def main():
        hub = LiveHub()
        subscriber = hub.subscribe(1)
        for step in range(5):
            hub.publish(1, "progress", {"job_id": 1, "message": str(step)})
        frames = [subscriber.queue.get_nowait() for _ in range(5)]
        assert [json.loads(f.split("data: ")[1])["message"] for f in frames] == ["0", "1", "2", "3", "4"]

    hub_run(main())


def test_hub_refuses_the_subscriber_above_the_cap_with_too_many_streams():
    async def main():
        hub = LiveHub(max_subscribers=3)
        subscribers = [hub.subscribe(n) for n in (1, 1, 2)]
        with pytest.raises(ApiError) as caught:
            hub.subscribe(3)
        assert caught.value.code == "too_many_streams" and caught.value.status == 503
        hub.unsubscribe(subscribers[0])  # abriu vaga
        hub.subscribe(3)
        assert hub.subscriber_count == 3

    hub_run(main())


def test_default_cap_is_32():
    async def main():
        hub = LiveHub()
        for _ in range(32):
            hub.subscribe(1)
        with pytest.raises(ApiError):
            hub.subscribe(1)

    hub_run(main())


def test_slow_client_is_closed_when_its_queue_fills_without_affecting_the_others(caplog):
    async def main():
        hub = LiveHub(queue_size=3)
        slow, fast = hub.subscribe(1), hub.subscribe(1)
        received = []

        async def consume():
            async for frame in hub.stream(fast):
                received.append(frame)

        consumer = asyncio.create_task(consume())
        for step in range(10):
            hub.publish(1, "progress", {"job_id": 1, "message": str(step)})
            await asyncio.sleep(0)  # o cliente rápido consome entre uma emissão e outra
        assert slow.closed and slow.overflowed and hub.subscriber_count == 1  # o lento saiu do difusor
        assert len(received) == 10  # o rápido recebeu tudo
        drained = [frame async for frame in hub.stream(slow)]  # o fluxo do lento termina sem entregar o resto
        assert drained == []
        hub.close_project(1)
        await asyncio.wait_for(consumer, 1)

    with caplog.at_level(logging.WARNING, logger="artigo2md.api"):
        hub_run(main())
    assert "não consumiu" in caplog.text


def test_stream_sends_keepalive_comment_when_idle_and_cleans_up_on_aclose():
    async def main():
        hub = LiveHub()
        subscriber = hub.subscribe(1)
        stream = hub.stream(subscriber, keepalive=0.01)
        assert await stream.__anext__() == KEEPALIVE_FRAME
        assert KEEPALIVE_FRAME == ": keep-alive\n\n"
        await stream.aclose()
        assert hub.subscriber_count == 0 and subscriber.closed

    hub_run(main())


def test_unsubscribe_is_idempotent_and_leaves_no_residue():
    async def main():
        hub = LiveHub()
        subscriber = hub.subscribe(5)
        hub.unsubscribe(subscriber)
        hub.unsubscribe(subscriber)
        assert hub.subscriber_count == 0 and hub._by_project == {}

    hub_run(main())


def test_close_project_delivers_what_was_queued_and_then_ends_only_that_project():
    async def main():
        hub = LiveHub()
        a, b = hub.subscribe(1), hub.subscribe(2)
        hub.publish(1, "job_state", {"job_id": 1})
        hub.close_project(1)
        frames = [frame async for frame in hub.stream(a)]
        assert len(frames) == 1 and frames[0].startswith("event: job_state")
        assert not b.closed and hub.subscriber_count == 1
        hub.close_all()
        assert b.closed and hub.subscriber_count == 0

    hub_run(main())


# ── condutor ASGI do SSE ────────────────────────────────────────────────────────
class Sse:
    """Abre `GET <path>` direto na aplicação ASGI e entrega cada quadro do corpo assim que sai."""

    def __init__(self, app, path, headers=None):
        self.app = app
        self.scope = {
            "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "GET", "path": path,
            "raw_path": path.encode(), "query_string": b"", "root_path": "", "scheme": "http",
            "server": ("127.0.0.1", 80), "client": ("127.0.0.1", 50000),
            "headers": [(b"host", b"127.0.0.1")] + [(k.encode(), v.encode()) for k, v in (headers or {}).items()],
        }
        self.incoming: asyncio.Queue = asyncio.Queue()
        self.outgoing: asyncio.Queue = asyncio.Queue()
        self.incoming.put_nowait({"type": "http.request", "body": b"", "more_body": False})
        self.status = 0
        self.headers: dict[str, str] = {}
        self.finished = False
        self.task: asyncio.Task | None = None

    async def _receive(self):
        return await self.incoming.get()

    async def _send(self, message):
        await self.outgoing.put(message)

    async def open(self) -> "Sse":
        self.task = asyncio.create_task(self.app(self.scope, self._receive, self._send))
        start = await asyncio.wait_for(self.outgoing.get(), 5)
        assert start["type"] == "http.response.start"
        self.status = start["status"]
        self.headers = {k.decode().lower(): v.decode() for k, v in start["headers"]}
        return self

    async def chunk(self, timeout=2.0) -> str | None:
        """Próximo pedaço do corpo; `None` quando o corpo terminou."""
        if self.finished:
            return None
        message = await asyncio.wait_for(self.outgoing.get(), timeout)
        assert message["type"] == "http.response.body"
        if not message.get("more_body", False):
            self.finished = True
            return message["body"].decode() or None
        return message["body"].decode()

    async def event(self, timeout=2.0) -> tuple[str, dict]:
        text = await self.chunk(timeout)
        assert text is not None, "o fluxo terminou"
        lines = text.rstrip("\n").split("\n")
        assert lines[0].startswith("event: ") and lines[1].startswith("data: ") and len(lines) == 2, text
        return lines[0][len("event: "):], json.loads(lines[1][len("data: "):])

    async def events(self, count, timeout=2.0):
        return [await self.event(timeout) for _ in range(count)]

    async def nothing_more(self, wait=0.05):
        with pytest.raises(asyncio.TimeoutError):
            await self.chunk(wait)

    async def disconnect(self):
        self.incoming.put_nowait({"type": "http.disconnect"})
        assert self.task is not None
        await asyncio.wait_for(self.task, 5)


class World:
    """Aplicação com `FakeStore`, o cliente HTTP comum e a abertura de fluxos SSE, no mesmo laço."""

    def __init__(self, tmp_path: Path, live: LiveHub | None = None):
        env = {"ARTIGO2MD_DATA_DIR_HOST": HOST, "ARTIGO2MD_DATA_DIR_MOUNT": str(tmp_path)}
        settings = ApiSettings.from_env(env, Path("/nonexistent-cwd-for-tests"))
        self.store = FakeStore()
        self.app = create_app(settings, store=self.store)
        self.services = self.app.state.services
        self.services.broker = _Published()  # por padrão a publicação é confirmada
        if live is not None:
            self.services.live = live
        self.http = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://127.0.0.1")
        from artigo2md.api.consumer import EventHandler

        self.handler = EventHandler(self.services)

    async def open(self, project_id, headers=None) -> Sse:
        return await Sse(self.app, f"/v1/projects/{project_id}/events", headers).open()

    async def project(self, name="P", documents=1):
        project = (await self.http.post("/v1/projects", json={"name": name})).json()
        paths = [f"/Users/a/{name}-{n}.pdf" for n in range(documents)]
        body = {"kind": "folder", "mode": "in_place", "path": "/Users/a", "documents": paths}
        created = (await self.http.post(f"/v1/projects/{project['id']}/sources", json=body)).json()["documents"]["created"]
        return project["id"], [item["id"] for item in created]

    async def enqueue(self, document_id):
        body = (await self.http.post(f"/v1/documents/{document_id}/jobs", json={})).json()
        return body.get("job", body)  # 503 devolve o job falho em `job`

    async def feed(self, event):
        return await self.handler.handle(schema.encode(event))

    async def aclose(self):
        self.services.live.close_all()
        await self.http.aclose()


def run_world(tmp_path, scenario, live=None):
    async def main():
        world = World(tmp_path, live)
        try:
            await scenario(world)
        finally:
            await world.aclose()

    asyncio.run(main())


class _Published:
    """Publicadora que sempre confirma (a rota de job publica sem broker real)."""

    async def publish(self, message):
        return None


def result_event(job_id, attempt=1, engine="docling", path=f"{HOST}/1/a.md"):
    data = copy.deepcopy(report())
    data["engine"] = engine
    return schema.build_result(str(job_id), attempt, path, engine, data)


# ── rota ────────────────────────────────────────────────────────────────────────
def test_events_route_opens_an_event_stream_with_the_right_headers(tmp_path):
    async def scenario(world):
        project_id, _ = await world.project()
        stream = await world.open(project_id)
        assert stream.status == 200
        assert stream.headers["content-type"].startswith("text/event-stream")
        assert stream.headers["cache-control"] == "no-cache"
        await stream.disconnect()

    run_world(tmp_path, scenario)


def test_unknown_project_is_a_json_404_before_the_stream_opens(tmp_path):
    async def scenario(world):
        stream = await world.open(9999)
        assert stream.status == 404 and stream.headers["content-type"].startswith("application/json")
        body = json.loads(await stream.chunk())
        assert body["error"]["code"] == "not_found"
        assert world.services.live.subscriber_count == 0

    run_world(tmp_path, scenario)


def test_invalid_project_id_is_refused_before_the_stream_opens(tmp_path):
    async def scenario(world):
        for bad in ("0", "abc", "-1"):
            stream = await Sse(world.app, f"/v1/projects/{bad}/events").open()
            assert stream.status == 400
            await stream.chunk()
        assert world.services.live.subscriber_count == 0

    run_world(tmp_path, scenario)


def test_untrusted_origin_is_403_and_does_not_open_the_stream(tmp_path):
    async def scenario(world):
        project_id, _ = await world.project()
        stream = await world.open(project_id, headers={"origin": "http://evil.example"})
        assert stream.status == 403
        assert json.loads(await stream.chunk())["error"]["code"] == "origin_not_allowed"
        assert world.services.live.subscriber_count == 0

    run_world(tmp_path, scenario)


def test_too_many_streams_is_a_json_503_in_portuguese(tmp_path):
    async def scenario(world):
        project_id, _ = await world.project()
        first = await world.open(project_id)
        refused = await world.open(project_id)
        assert refused.status == 503 and refused.headers["content-type"].startswith("application/json")
        error = json.loads(await refused.chunk())["error"]
        assert error["code"] == "too_many_streams" and "conexões" in error["message"]
        await first.disconnect()
        again = await world.open(project_id)  # a vaga voltou
        assert again.status == 200
        await again.disconnect()

    run_world(tmp_path, scenario, live=LiveHub(max_subscribers=1))


def test_client_leaving_releases_the_subscriber_and_the_queue(tmp_path):
    async def scenario(world):
        project_id, _ = await world.project()
        streams = [await world.open(project_id) for _ in range(3)]
        assert world.services.live.subscriber_count == 3
        for stream in streams:
            await stream.disconnect()
        assert world.services.live.subscriber_count == 0 and world.services.live._by_project == {}

    run_world(tmp_path, scenario)


def test_idle_stream_sends_a_keep_alive_comment(tmp_path):
    async def scenario(world):
        project_id, _ = await world.project()
        stream = await world.open(project_id)
        # o difusor padrão usa 15 s; aqui o gerador é substituído por um de intervalo curto
        await stream.disconnect()
        subscriber = world.services.live.subscribe(project_id)
        quick = world.services.live.stream(subscriber, keepalive=0.01)
        assert await quick.__anext__() == ": keep-alive\n\n"
        await quick.aclose()

    run_world(tmp_path, scenario)


# ── eventos vindos do consumidor ────────────────────────────────────────────────
def test_progress_event_starts_the_job_and_flows_with_emitted_at_copied(tmp_path):
    async def scenario(world):
        project_id, (document_id,) = await world.project()
        job = await world.enqueue(document_id)
        stream = await world.open(project_id)
        event = schema.build_progress(str(job["id"]), 1, "Convertendo a página 3")
        outcome = await world.feed(event)
        assert outcome.applied
        (state, progress) = await stream.events(2)
        assert state == ("job_state", {"job_id": job["id"], "document_id": document_id, "attempt": 1,
                                       "status": "running", "message": "Job iniciado."})
        assert progress[0] == "progress"
        assert progress[1] == {"job_id": job["id"], "document_id": document_id, "attempt": 1,
                               "message": "Convertendo a página 3", "emitted_at": event["emitted_at"]}
        # um segundo progress não repete o job_state running
        again = schema.build_progress(str(job["id"]), 1, "Convertendo a página 4")
        await world.feed(again)
        assert (await stream.event())[0] == "progress"
        await stream.nothing_more()
        await stream.disconnect()

    run_world(tmp_path, scenario)


def test_result_event_emits_succeeded_and_a_late_event_emits_nothing(tmp_path):
    async def scenario(world):
        project_id, (document_id,) = await world.project()
        job = await world.enqueue(document_id)
        stream = await world.open(project_id)
        assert (await world.feed(result_event(job["id"]))).applied
        name, data = (await stream.events(2))[1]  # job_state running (transição) e depois succeeded
        assert name == "job_state" and data["status"] == "succeeded" and data["attempt"] == 1
        assert data["message"] == "Conversão concluída com o motor docling." and data["document_id"] == document_id
        assert not (await world.feed(result_event(job["id"]))).applied  # duplicata
        await stream.nothing_more()
        await stream.disconnect()

    run_world(tmp_path, scenario)


def test_transient_error_is_a_warning_and_the_job_stays_running(tmp_path):
    async def scenario(world):
        project_id, (document_id,) = await world.project()
        job = await world.enqueue(document_id)
        stream = await world.open(project_id)
        event = schema.build_error(str(job["id"]), 1, "conversion_failed", "Memória insuficiente; tentando de novo.",
                                   True, 3)
        await world.feed(event)
        running, warning = await stream.events(2)
        assert running[1]["status"] == "running"
        assert warning == ("warning", {"job_id": job["id"], "document_id": document_id, "attempt": 1,
                                       "message": "Memória insuficiente; tentando de novo.",
                                       "emitted_at": event["emitted_at"]})
        assert (await world.http.get(f"/v1/jobs/{job['id']}")).json()["status"] == "running"
        await stream.disconnect()

    run_world(tmp_path, scenario)


def test_final_error_emits_failed_with_the_reason(tmp_path):
    async def scenario(world):
        project_id, (document_id,) = await world.project()
        job = await world.enqueue(document_id)
        stream = await world.open(project_id)
        await world.feed(schema.build_error(str(job["id"]), 3, "conversion_failed", "O PDF está corrompido.", False, 3))
        _, failed = (await stream.events(2))[1]
        assert failed["status"] == "failed" and failed["attempt"] == 3
        assert failed["message"] == "Conversão falhou: O PDF está corrompido."
        await stream.disconnect()

    run_world(tmp_path, scenario)


def test_result_with_a_path_outside_the_data_dir_emits_failed(tmp_path):
    async def scenario(world):
        project_id, (document_id,) = await world.project()
        job = await world.enqueue(document_id)
        stream = await world.open(project_id)
        await world.feed(result_event(job["id"], path="/etc/passwd"))
        _, failed = (await stream.events(2))[1]
        assert failed["status"] == "failed" and "caminho de Markdown recusado" in failed["message"]
        assert "passwd" not in json.dumps(failed)
        await stream.disconnect()

    run_world(tmp_path, scenario)


def test_discarded_events_emit_nothing(tmp_path):
    async def scenario(world):
        project_id, (document_id,) = await world.project()
        job = await world.enqueue(document_id)
        stream = await world.open(project_id)
        await world.handler.handle(b"{nao e json")
        await world.feed(schema.build_progress("999999", 1, "de outro mundo"))
        await world.http.post(f"/v1/jobs/{job['id']}/cancel")
        await stream.events(1)  # o cancelled
        await world.feed(schema.build_progress(str(job["id"]), 1, "tardio"))  # job cancelado: descartado
        await stream.nothing_more()
        await stream.disconnect()

    run_world(tmp_path, scenario)


def test_events_are_filtered_by_project(tmp_path):
    async def scenario(world):
        project_a, (doc_a,) = await world.project("A")
        project_b, (doc_b,) = await world.project("B")
        job_a, job_b = await world.enqueue(doc_a), await world.enqueue(doc_b)
        stream_a, stream_b = await world.open(project_a), await world.open(project_b)
        await world.feed(schema.build_progress(str(job_b["id"]), 1, "só do B"))
        assert (await stream_b.events(2))[1][1]["message"] == "só do B"
        await stream_a.nothing_more()
        await world.feed(schema.build_progress(str(job_a["id"]), 1, "só do A"))
        assert (await stream_a.events(2))[1][1]["message"] == "só do A"
        await stream_b.nothing_more()
        await stream_a.disconnect()
        await stream_b.disconnect()

    run_world(tmp_path, scenario)


def test_two_clients_of_the_same_project_both_receive_each_event(tmp_path):
    async def scenario(world):
        project_id, (document_id,) = await world.project()
        job = await world.enqueue(document_id)
        first, second = await world.open(project_id), await world.open(project_id)
        await world.feed(schema.build_progress(str(job["id"]), 1, "x"))
        assert await first.events(2) == await second.events(2)
        await first.disconnect()
        await second.disconnect()

    run_world(tmp_path, scenario)


# ── eventos vindos das rotas ────────────────────────────────────────────────────
def test_job_creation_emits_pending_with_null_attempt_and_a_failed_publication_emits_failed(tmp_path):
    async def scenario(world):
        project_id, (document_id,) = await world.project()
        world.services.broker = None  # sem publicadora: a publicação não é confirmada
        stream = await world.open(project_id)
        job = await world.enqueue(document_id)
        pending, failed = await stream.events(2)
        assert pending == ("job_state", {"job_id": job["id"], "document_id": document_id, "attempt": None,
                                         "status": "pending", "message": "Job criado."})
        assert failed[0] == "job_state" and failed[1]["status"] == "failed" and failed[1]["attempt"] is None
        await stream.nothing_more()
        await stream.disconnect()

    run_world(tmp_path, scenario)


def test_confirmed_publication_emits_only_pending(tmp_path):
    async def scenario(world):
        world.services.broker = _Published()
        project_id, (document_id,) = await world.project()
        stream = await world.open(project_id)
        job = await world.enqueue(document_id)
        name, data = await stream.event()
        assert (name, data["status"], data["job_id"]) == ("job_state", "pending", job["id"])
        await stream.nothing_more()
        await stream.disconnect()

    run_world(tmp_path, scenario)


def test_batch_enqueue_emits_one_pending_per_document(tmp_path):
    async def scenario(world):
        world.services.broker = _Published()
        project_id, ids = await world.project(documents=3)
        stream = await world.open(project_id)
        response = await world.http.post(f"/v1/projects/{project_id}/jobs", json={"not_processed": True})
        enqueued = response.json()["enqueued"]
        events = await stream.events(3)
        assert [e[1]["job_id"] for e in events] == [j["id"] for j in enqueued]
        assert {e[1]["status"] for e in events} == {"pending"}
        await stream.disconnect()

    run_world(tmp_path, scenario)


def test_cancel_emits_cancelled_with_null_attempt(tmp_path):
    async def scenario(world):
        world.services.broker = _Published()
        project_id, (document_id,) = await world.project()
        job = await world.enqueue(document_id)
        stream = await world.open(project_id)
        assert (await world.http.post(f"/v1/jobs/{job['id']}/cancel")).status_code == 200
        name, data = await stream.event()
        assert name == "job_state" and data == {
            "job_id": job["id"], "document_id": document_id, "attempt": None, "status": "cancelled",
            "message": "Job cancelado pelo usuário."}
        # cancelar de novo é recusado e não emite nada
        assert (await world.http.post(f"/v1/jobs/{job['id']}/cancel")).status_code == 409
        await stream.nothing_more()
        await stream.disconnect()

    run_world(tmp_path, scenario)


def test_archive_emits_a_cancelled_per_open_job_and_keeps_the_stream_open(tmp_path):
    async def scenario(world):
        world.services.broker = _Published()
        project_id, ids = await world.project(documents=3)
        jobs = [await world.enqueue(doc) for doc in ids]
        await world.http.post(f"/v1/jobs/{jobs[2]['id']}/cancel")  # este já era final: não entra no archive
        await world.feed(schema.build_progress(str(jobs[1]["id"]), 1, "rodando"))  # running
        stream = await world.open(project_id)
        response = await world.http.post(f"/v1/projects/{project_id}/archive")
        assert response.json()["cancelled_job_ids"] == [jobs[0]["id"], jobs[1]["id"]]
        events = await stream.events(2)
        assert [(e[0], e[1]["job_id"], e[1]["status"], e[1]["attempt"]) for e in events] == [
            ("job_state", jobs[0]["id"], "cancelled", None), ("job_state", jobs[1]["id"], "cancelled", None)]
        assert events[0][1]["message"] == "Job cancelado porque o projeto foi arquivado."
        await stream.nothing_more()
        # o fluxo continua aberto e funcional depois do arquivamento (o projeto pode ser reativado)
        assert (await world.http.post(f"/v1/projects/{project_id}/unarchive")).status_code == 200
        job = await world.enqueue(ids[0])
        assert (await stream.event())[1]["job_id"] == job["id"]
        await stream.disconnect()

    run_world(tmp_path, scenario)


def test_archive_without_open_jobs_emits_nothing(tmp_path):
    async def scenario(world):
        project_id, _ = await world.project()
        stream = await world.open(project_id)
        assert (await world.http.post(f"/v1/projects/{project_id}/archive")).json()["cancelled_job_ids"] == []
        await stream.nothing_more()
        assert (await world.http.post(f"/v1/projects/{project_id}/archive")).status_code == 409
        await stream.nothing_more()
        await stream.disconnect()

    run_world(tmp_path, scenario)


def test_archived_project_still_opens_the_stream(tmp_path):
    async def scenario(world):
        project_id, _ = await world.project()
        await world.http.post(f"/v1/projects/{project_id}/archive")
        stream = await world.open(project_id)
        assert stream.status == 200
        await stream.disconnect()

    run_world(tmp_path, scenario)


def test_delete_emits_cancelled_per_open_job_and_then_ends_the_streams(tmp_path):
    async def scenario(world):
        world.services.broker = _Published()
        project_id, ids = await world.project(documents=2)
        other_id, (other_doc,) = await world.project("Outro")
        jobs = [await world.enqueue(doc) for doc in ids]
        await world.enqueue(other_doc)
        first, second = await world.open(project_id), await world.open(project_id)
        other = await world.open(other_id)
        assert (await world.http.delete(f"/v1/projects/{project_id}")).status_code == 204
        for stream in (first, second):
            events = await stream.events(2)
            assert [(e[1]["job_id"], e[1]["status"]) for e in events] == [(j["id"], "cancelled") for j in jobs]
            assert events[0][1]["message"] == "Job cancelado porque o projeto foi excluído."
            assert await stream.chunk() is None  # o servidor encerrou o fluxo
            await stream.disconnect()
        await other.nothing_more()  # o fluxo de outro projeto segue aberto
        assert world.services.live.subscriber_count == 1
        # ao reconectar, o projeto não existe mais
        again = await world.open(project_id)
        assert again.status == 404 and json.loads(await again.chunk())["error"]["code"] == "not_found"
        await other.disconnect()

    run_world(tmp_path, scenario)


def test_failed_delete_does_not_emit_or_close_anything(tmp_path):
    async def scenario(world):
        world.services.broker = _Published()
        project_id, (document_id,) = await world.project()
        await world.enqueue(document_id)
        stream = await world.open(project_id)
        assert (await world.http.delete("/v1/projects/9999")).status_code == 404
        await stream.nothing_more()
        assert world.services.live.subscriber_count == 1
        await stream.disconnect()

    run_world(tmp_path, scenario)


def test_reconnecting_replays_nothing_and_frames_carry_no_id(tmp_path):
    async def scenario(world):
        world.services.broker = _Published()
        project_id, (document_id,) = await world.project()
        first = await world.open(project_id)
        job = await world.enqueue(document_id)
        frame = await first.chunk()
        assert "id:" not in frame and frame.startswith("event: job_state\n")
        await first.disconnect()
        # Last-Event-ID é ignorado: nenhum evento antigo é reenviado; o estado se relê pelas rotas de consulta
        second = await world.open(project_id, headers={"last-event-id": "1"})
        await second.nothing_more()
        status = (await world.http.get(f"/v1/projects/{project_id}/documents")).json()["documents"][0]
        assert status["status"] == "pending" and status["latest_job_id"] == job["id"]
        await second.disconnect()

    run_world(tmp_path, scenario)


def test_closing_the_api_ends_every_stream(tmp_path):
    async def scenario(world):
        project_id, _ = await world.project()
        stream = await world.open(project_id)
        await world.services.close()
        assert await stream.chunk() is None
        await stream.disconnect()

    run_world(tmp_path, scenario)


def test_live_emission_failure_never_breaks_event_handling(tmp_path, caplog):
    async def scenario(world):
        project_id, (document_id,) = await world.project()
        job = await world.enqueue(document_id)

        class Broken:
            def publish(self, *args, **kwargs):
                raise RuntimeError("difusor quebrado")

        real, world.services.live = world.services.live, Broken()
        with caplog.at_level(logging.WARNING, logger="artigo2md.api"):
            outcome = await world.feed(schema.build_progress(str(job["id"]), 1, "segue"))
        world.services.live = real
        assert outcome.applied and (await world.http.get(f"/v1/jobs/{job['id']}")).json()["status"] == "running"

    run_world(tmp_path, scenario)
    assert "Falha ao repassar" in caplog.text and "difusor quebrado" not in caplog.text
