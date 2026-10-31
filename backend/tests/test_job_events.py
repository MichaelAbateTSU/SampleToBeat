import asyncio
import json
import threading

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient

from choplab import api, store


class Connected:
    async def is_disconnected(self):
        return False


@pytest.mark.parametrize("blocked_read", [1, 2])
def test_blocked_job_read_does_not_block_http_health(monkeypatch, blocked_read):
    entered, release = threading.Event(), threading.Event()
    calls = []
    loop_thread = threading.get_ident()

    def slow_get(kind, id):
        calls.append(threading.get_ident())
        if len(calls) == blocked_read:
            entered.set()
            assert release.wait(10), "Test did not release the synthetic database read"
        return {"id": id, "status": "completed"}

    monkeypatch.setattr(store, "get", slow_get)
    # Release from a different OS thread even if a regression freezes asyncio.
    watchdog = threading.Timer(5, release.set)
    watchdog.start()

    async def check():
        async with AsyncClient(transport=ASGITransport(app=api.app), base_url="http://test") as client:
            events = asyncio.create_task(client.get("/api/jobs/test/events"))
            try:
                assert await asyncio.to_thread(entered.wait, 2)
                assert not release.is_set(), "Job read blocked the event loop until the watchdog released it"
                health = await asyncio.wait_for(client.get("/api/health"), timeout=1)
                assert health.status_code == 200 and health.json()["ok"]
                assert not release.is_set(), "Health waited for the job read instead of responding independently"
            finally:
                release.set()
                response = await asyncio.wait_for(events, timeout=3)
            assert response.status_code == 200
            assert '"status": "completed"' in response.text

    try:
        asyncio.run(check())
        assert len(calls) == 2 and all(thread != loop_thread for thread in calls)
    finally:
        release.set()
        watchdog.cancel()
        watchdog.join()


def test_job_stream_emits_changes_only_and_stops_at_terminal(monkeypatch):
    queued = {"id": "j", "status": "queued", "progress": 0}
    running = {**queued, "status": "running", "progress": 0.5}
    completed = {**running, "status": "completed", "progress": 1}
    records = iter([queued, queued, queued, running, completed])
    reads = []
    def get(*args):
        reads.append(args)
        return next(records)
    monkeypatch.setattr(store, "get", get)

    async def check():
        response = await api.job_events("j", Connected())
        assert response.media_type == "text/event-stream"
        assert response.headers["cache-control"] == "no-cache"
        chunks = [chunk async for chunk in response.body_iterator]
        assert [json.loads(chunk.removeprefix("data: ").strip()) for chunk in chunks] == [queued, running, completed]

    asyncio.run(check())
    assert len(reads) == 5


def test_disconnect_stops_before_another_database_read(monkeypatch):
    calls = []
    monkeypatch.setattr(store, "get", lambda *args: calls.append(args) or {"id": "j", "status": "running"})
    class Disconnects:
        checks = 0
        async def is_disconnected(self):
            self.checks += 1
            return self.checks > 1

    async def check():
        response = await api.job_events("j", Disconnects())
        assert len([chunk async for chunk in response.body_iterator]) == 1

    asyncio.run(check())
    assert len(calls) == 2


@pytest.mark.parametrize("missing", [False, True])
def test_initial_read_errors_remain_explicit_http_errors(monkeypatch, missing):
    def get(*args):
        if missing:
            return None
        raise store.StoreUnavailable("Configured PostgreSQL is unavailable; no fallback.")
    monkeypatch.setattr(store, "get", get)

    async def check():
        async with AsyncClient(transport=ASGITransport(app=api.app), base_url="http://test") as client:
            response = await client.get("/api/jobs/j/events")
            assert response.status_code == (404 if missing else 503)
            if not missing:
                assert response.headers["retry-after"] == "10"
                assert "no fallback" in response.json()["detail"]
            assert (await client.get("/api/health")).status_code == 200

    asyncio.run(check())


@pytest.mark.parametrize("missing", [False, True])
def test_error_after_stream_starts_is_not_hidden_or_reported_as_completion(monkeypatch, missing):
    count = 0
    def get(*args):
        nonlocal count
        count += 1
        if count <= 2:
            return {"id": "j", "status": "running"}
        if missing:
            return None
        raise store.StoreUnavailable("PostgreSQL read failed during observation.")
    monkeypatch.setattr(store, "get", get)

    async def check():
        response = await api.job_events("j", Connected())
        iterator = response.body_iterator
        first = await anext(iterator)
        assert '"status": "running"' in first and "completed" not in first
        with pytest.raises(HTTPException if missing else store.StoreUnavailable) as error:
            await anext(iterator)
        if missing:
            assert error.value.status_code == 404
        else:
            assert "read failed" in str(error.value)

    asyncio.run(check())
