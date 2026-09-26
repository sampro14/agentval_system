from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agenteval.apps.api.main import create_app
from agenteval.config import get_settings, load_env_file
from agenteval.llm.provider import FakeProvider
from agenteval.storage.repositories.runs import RunRepository, TrajectoryRepository
from tests.conftest import FakeSandbox, scripted_llm

TASK = "change-password"


class Queue:
    async def enqueue(self, run_id: str) -> None:
        pass


def build_app(
    sessions: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch, tmp_path: Path, dev: bool
) -> Any:
    if dev:
        monkeypatch.setenv("AGENTEVAL_ENABLE_DEV_ENDPOINTS", "true")
    else:
        monkeypatch.delenv("AGENTEVAL_ENABLE_DEV_ENDPOINTS", raising=False)
    get_settings.cache_clear()
    app = create_app(use_lifespan=False)
    app.state.sessions, app.state.queue = sessions, Queue()
    app.state.dev_dir = tmp_path / "dev"
    app.state.stage_deps = lambda settings: (scripted_llm(), FakeSandbox([0]))
    return app


@pytest.fixture
async def api(
    sessions: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> AsyncIterator[tuple[AsyncClient, Any]]:
    app = build_app(sessions, monkeypatch, tmp_path, dev=True)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client, app
    get_settings.cache_clear()


# --- run history ---------------------------------------------------------------------------------


async def test_list_runs_newest_first_with_limit_and_acceptance(
    api: tuple[AsyncClient, Any], sessions: async_sessionmaker[AsyncSession]
) -> None:
    client, _ = api
    ids = []
    for name in ("first", "second", "third"):
        async with sessions() as session:
            ids.append((await RunRepository(session).create(name, "software_engineering", {"task_id": TASK})).id)
        await asyncio.sleep(0.01)
    async with sessions() as session:
        await TrajectoryRepository(session).add_evaluations(ids[1], {"acceptance_passed": 1.0}, "acceptance_v1")

    runs = (await client.get("/api/v1/runs")).json()

    assert [r["task"] for r in runs] == ["third", "second", "first"]
    assert [r["acceptance_passed"] for r in runs] == [None, 1.0, None]
    assert runs[0]["task_id"] == TASK
    assert [r["task"] for r in (await client.get("/api/v1/runs?limit=2")).json()] == ["third", "second"]
    assert (await client.get("/api/v1/runs?limit=0")).status_code == 422
    assert (await client.get(f"/api/v1/runs/{ids[1]}")).json()["acceptance_passed"] == 1.0


async def test_trajectory_after_seq_returns_only_newer_events(
    api: tuple[AsyncClient, Any], sessions: async_sessionmaker[AsyncSession]
) -> None:
    client, _ = api
    async with sessions() as session:
        run = await RunRepository(session).create("t", "software_engineering", {})
        events = [
            {
                "event_id": f"evt_{i}",
                "agent": "planner",
                "event_type": "X",
                "output": {},
                "status": "ok",
                "latency_ms": 1,
                "token_usage": {},
                "timestamp": "2026-01-01T00:00:00+00:00",
            }
            for i in range(3)
        ]
        await TrajectoryRepository(session).add_events(run.id, events)

    async def seqs(query: str) -> list[int]:
        response = await client.get(f"/api/v1/runs/{run.id}/trajectory{query}")
        return [e["seq"] for e in response.json()]

    assert await seqs("") == [1, 2, 3]
    assert await seqs("?after_seq=1") == [2, 3]
    assert await seqs("?after_seq=3") == []
    assert (await client.get(f"/api/v1/runs/{run.id}/trajectory?after_seq=-1")).status_code == 422


# --- reference data -----------------------------------------------------------------------------


async def test_tasks_endpoint(api: tuple[AsyncClient, Any]) -> None:
    client, _ = api
    tasks = (await client.get("/api/v1/tasks")).json()

    assert [t["id"] for t in tasks][:2] == ["change-password", "email-validation"] and len(tasks) == 6
    assert tasks[0]["difficulty"] == "easy" and tasks[0]["requirements"] and "change_password" in tasks[0]["task"]


async def test_providers_report_configuration_but_never_a_key(
    api: tuple[AsyncClient, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = api
    for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "DEEPSEEK_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-very-secret-value")

    response = await client.get("/api/v1/providers")
    providers = {p["name"]: p for p in response.json()}

    assert providers["openai"]["configured"] is True and providers["openai"]["default_model"] == "gpt-6-sol"
    assert providers["anthropic"]["configured"] is False
    assert providers["fake"]["configured"] is True
    assert providers["gemini"]["key_env_vars"] == ["GEMINI_API_KEY", "GOOGLE_API_KEY"]
    assert "sk-very-secret-value" not in response.text

    monkeypatch.setenv("GOOGLE_API_KEY", "x")  # either variable counts for Gemini
    assert {p["name"]: p for p in (await client.get("/api/v1/providers")).json()}["gemini"]["configured"] is True


async def test_cors_allows_only_the_dashboard_origin(api: tuple[AsyncClient, Any]) -> None:
    client, _ = api
    headers = {"Origin": "http://localhost:3000", "Access-Control-Request-Method": "POST"}
    allowed = await client.options("/api/v1/runs", headers=headers)
    assert allowed.headers["access-control-allow-origin"] == "http://localhost:3000"

    blocked = await client.options("/api/v1/runs", headers={**headers, "Origin": "http://evil.example"})
    assert "access-control-allow-origin" not in blocked.headers


# --- one agent at a time -------------------------------------------------------------------------


async def stage(client: AsyncClient, agent: str, **extra: Any) -> Any:
    return await client.post("/api/v1/dev/stages", json={"agent": agent, "task_id": TASK, "provider": "fake", **extra})


async def test_dev_endpoints_are_off_by_default(
    sessions: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    app = build_app(sessions, monkeypatch, tmp_path, dev=False)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await stage(client, "planner")
        assert response.status_code == 404 and "AGENTEVAL_ENABLE_DEV_ENDPOINTS" in response.json()["detail"]
        assert (await client.get(f"/api/v1/dev/stages/{TASK}")).status_code == 404
    get_settings.cache_clear()


async def test_stages_chain_and_status_survives_a_reload(api: tuple[AsyncClient, Any]) -> None:
    client, _ = api
    before = (await client.get(f"/api/v1/dev/stages/{TASK}")).json()["agents"]
    assert [a["agent"] for a in before][:3] == ["planner", "research", "draft"]
    assert not any(a["ran"] for a in before)

    planned = (await stage(client, "planner")).json()
    assert planned["ok"] and planned["event"]["event_type"] == "PLAN_CREATED" and planned["transcript"]
    researched = (await stage(client, "research")).json()
    assert researched["ok"] and not researched["started_fresh"]

    status = {a["agent"]: a for a in (await client.get(f"/api/v1/dev/stages/{TASK}")).json()["agents"]}
    assert (status["planner"]["ran"], status["research"]["ran"], status["draft"]["ran"]) == (True, True, False)
    assert status["planner"]["ok"] is True and status["planner"]["tokens_out"] >= 0

    saved = (await client.get(f"/api/v1/dev/stages/{TASK}/planner")).json()
    assert saved["event"]["event_type"] == "PLAN_CREATED" and saved["transcript"][0]["prompt"]
    assert (await client.get(f"/api/v1/dev/stages/{TASK}/validate")).status_code == 404


async def test_draft_event_carries_the_diffs(api: tuple[AsyncClient, Any]) -> None:
    client, _ = api
    await stage(client, "planner")
    await stage(client, "research")
    drafted = (await stage(client, "draft")).json()

    diff = drafted["event"]["output"]["diffs"]["calc.py"]
    assert "+def add(a, b):" in diff and "+++ b/calc.py" in diff


async def test_a_failed_stage_is_saved_with_its_error(api: tuple[AsyncClient, Any]) -> None:
    client, app = api
    app.state.stage_deps = lambda settings: (FakeProvider(lambda s, p: "no json at all"), FakeSandbox([0]))

    failed = (await stage(client, "planner")).json()

    assert failed["ok"] is False and "planner failed" in failed["error"]
    assert failed["transcript"][0]["response"] == "no json at all"
    reloaded = (await client.get(f"/api/v1/dev/stages/{TASK}/planner")).json()
    assert reloaded["ok"] is False and reloaded["event"]["event_type"] == "PLANNER_ERROR"
    status = (await client.get(f"/api/v1/dev/stages/{TASK}")).json()["agents"][0]
    assert (status["ran"], status["ok"]) == (True, False)


async def test_fresh_discards_earlier_stages(api: tuple[AsyncClient, Any]) -> None:
    client, _ = api
    await stage(client, "planner")
    await stage(client, "research")
    await stage(client, "research", fresh=True)

    ran = [a["agent"] for a in (await client.get(f"/api/v1/dev/stages/{TASK}")).json()["agents"] if a["ran"]]
    assert ran == ["research"]  # the planner's saved result was cleared with the rest of the state


async def test_busy_task_is_rejected_with_409(api: tuple[AsyncClient, Any]) -> None:
    client, app = api
    app.state.stage_running.add(TASK)
    assert (await stage(client, "planner")).status_code == 409


async def test_bad_requests(api: tuple[AsyncClient, Any]) -> None:
    client, app = api
    assert (await client.post("/api/v1/dev/stages", json={"agent": "planner", "task_id": "nope"})).status_code == 404
    assert (await client.get("/api/v1/dev/stages/nope")).status_code == 404
    assert (await stage(client, "not-an-agent")).status_code == 422
    assert (
        await client.post("/api/v1/dev/stages", json={"agent": "planner", "task_id": TASK, "provider": "llama"})
    ).status_code == 422

    def no_key(settings: Any) -> Any:
        raise RuntimeError("api_key client option must be set")

    app.state.stage_deps = no_key
    response = await stage(client, "planner")
    assert response.status_code == 400 and "api_key client option" in response.json()["detail"]
    assert TASK not in app.state.stage_running  # a failed start must not leave the task locked


# --- .env loading --------------------------------------------------------------------------------


def test_load_env_file_skips_blank_values_and_never_overrides(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("AE_TEST_NEW=from-file\nAE_TEST_BLANK=\nAE_TEST_EXISTING=from-file\n")
    monkeypatch.delenv("AE_TEST_NEW", raising=False)
    monkeypatch.delenv("AE_TEST_BLANK", raising=False)
    monkeypatch.setenv("AE_TEST_EXISTING", "already-set")

    try:
        assert load_env_file(env_file) == ["AE_TEST_NEW"]
        assert os.environ["AE_TEST_NEW"] == "from-file"
        assert "AE_TEST_BLANK" not in os.environ  # a blank line in .env.example can't shadow a real key
        assert os.environ["AE_TEST_EXISTING"] == "already-set"
    finally:
        os.environ.pop("AE_TEST_NEW", None)


def test_load_env_file_missing_file_is_fine(tmp_path: Path) -> None:
    assert load_env_file(tmp_path / "nope.env") == []
