from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agenteval.apps.api.main import create_app


class MemoryQueue:
    def __init__(self) -> None:
        self.items: list[str] = []

    async def enqueue(self, run_id: str) -> None:
        self.items.append(run_id)


@pytest.fixture
async def client(sessions: async_sessionmaker[AsyncSession]) -> AsyncIterator[tuple[AsyncClient, MemoryQueue]]:
    app = create_app(use_lifespan=False)
    queue = MemoryQueue()
    app.state.sessions = sessions
    app.state.queue = queue
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield http, queue


async def test_health(client: tuple[AsyncClient, MemoryQueue]) -> None:
    http, _ = client
    assert (await http.get("/health")).json() == {"status": "ok"}


async def test_create_and_fetch_run(client: tuple[AsyncClient, MemoryQueue]) -> None:
    http, queue = client
    response = await http.post("/api/v1/runs", json={"task": "Implement password reset", "max_repair_iterations": 2})
    assert response.status_code == 202
    run = response.json()
    assert run["status"] == "queued"
    assert queue.items == [run["id"]]

    fetched = await http.get(f"/api/v1/runs/{run['id']}")
    assert fetched.json()["task"] == "Implement password reset"
    assert (await http.get(f"/api/v1/runs/{run['id']}/trajectory")).json() == []


async def test_unknown_run_is_404(client: tuple[AsyncClient, MemoryQueue]) -> None:
    http, _ = client
    assert (await http.get("/api/v1/runs/nope/trajectory")).status_code == 404


async def test_rejects_empty_task(client: tuple[AsyncClient, MemoryQueue]) -> None:
    http, _ = client
    assert (await http.post("/api/v1/runs", json={"task": ""})).status_code == 422
