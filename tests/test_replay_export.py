from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agenteval.config import Settings, get_settings
from agenteval.dev.replay import export, scrub
from agenteval.storage.repositories.runs import RunRepository
from agenteval.workers.run_worker import execute_run
from tests.conftest import FakeSandbox, scripted_llm
from tests.test_dashboard_api import build_app


@pytest.fixture
async def client(
    sessions: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> AsyncIterator[AsyncClient]:
    app = build_app(sessions, monkeypatch, tmp_path, dev=False)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    get_settings.cache_clear()


async def _finished_run(sessions: async_sessionmaker[AsyncSession], settings: Settings, sandbox: FakeSandbox) -> str:
    async with sessions() as session:
        run = await RunRepository(session).create("task", "software_engineering", {"task_id": "change-password"})
    await execute_run(run.id, sessions, settings, llm=scripted_llm(), sandbox=sandbox)
    return run.id


def test_scrub_removes_home_paths_and_keys() -> None:
    value: Any = {
        "path": "/Users/alice/work/agenteval/.agenteval-dev/ws/calc.py",
        "nested": ["/home/bob/x", "C:\\Users\\carol\\x"],
        "error": "403 for url ...?key=AIzaSyA1234567890abcdefghijklmnop and sk-proj-abcdefghijklmnopqrstuvwxyz12",
        "count": 3,
    }
    out = scrub(value)
    assert out["path"] == "~/work/agenteval/.agenteval-dev/ws/calc.py"
    assert out["nested"] == ["~/x", "~\\x"]
    assert "AIza" not in out["error"] and "sk-proj" not in out["error"]
    assert out["count"] == 3


async def test_export_writes_one_bundle_per_finished_run(
    client: AsyncClient,
    sessions: async_sessionmaker[AsyncSession],
    settings: Settings,
    sandbox_factory: Callable[[list[int | dict[str, str]]], FakeSandbox],
    tmp_path: Path,
) -> None:
    passed = await _finished_run(sessions, settings, sandbox_factory([0]))
    repaired = await _finished_run(sessions, settings, sandbox_factory([1, 0]))
    out = tmp_path / "replay"

    runs = await export(client, out)

    assert {r["id"] for r in runs} == {passed, repaired}
    assert json.loads((out / "runs.json").read_text())[0]["id"] == repaired  # newest first, as the API lists them
    bundle = json.loads((out / "runs" / f"{repaired}.json").read_text())
    assert bundle["run"]["status"] == "recovered"
    assert [e["seq"] for e in bundle["trajectory"]] == sorted(e["seq"] for e in bundle["trajectory"])
    assert bundle["failures"] and bundle["evaluation"]
    assert json.loads((out / "tasks.json").read_text())
    assert json.loads((out / "meta.json").read_text())["runs"] == 2


async def test_export_only_the_named_runs_and_refuses_unfinished_ones(
    client: AsyncClient,
    sessions: async_sessionmaker[AsyncSession],
    settings: Settings,
    sandbox_factory: Callable[[list[int | dict[str, str]]], FakeSandbox],
    tmp_path: Path,
) -> None:
    chosen = await _finished_run(sessions, settings, sandbox_factory([0]))
    await _finished_run(sessions, settings, sandbox_factory([0]))
    async with sessions() as session:
        queued = (await RunRepository(session).create("later", "software_engineering", {})).id

    runs = await export(client, tmp_path / "one", [chosen])
    assert [r["id"] for r in runs] == [chosen]
    assert [p.stem for p in (tmp_path / "one" / "runs").iterdir()] == [chosen]

    with pytest.raises(ValueError, match="not finished"):
        await export(client, tmp_path / "two", [queued])
