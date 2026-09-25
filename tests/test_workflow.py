from __future__ import annotations

from collections.abc import Callable

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agenteval.config import Settings
from agenteval.storage.repositories.runs import RunRepository, TrajectoryRepository
from agenteval.workers.run_worker import execute_run
from tests.conftest import FakeSandbox, scripted_llm


async def _run(
    sessions: async_sessionmaker[AsyncSession],
    settings: Settings,
    sandbox: FakeSandbox,
    max_repairs: int = 3,
) -> tuple[str, str]:
    async with sessions() as session:
        run = await RunRepository(session).create(
            "add an add() function", "software_engineering", {"max_repair_iterations": max_repairs}
        )
    status = await execute_run(run.id, sessions, settings, llm=scripted_llm(), sandbox=sandbox)
    return run.id, status


async def _agents(sessions: async_sessionmaker[AsyncSession], run_id: str) -> list[str]:
    async with sessions() as session:
        return [e.agent_name for e in await TrajectoryRepository(session).events(run_id)]


async def test_first_pass_success(
    sessions: async_sessionmaker[AsyncSession], settings: Settings, sandbox_factory: Callable[[list[int]], FakeSandbox]
) -> None:
    run_id, status = await _run(sessions, settings, sandbox_factory([0]))

    assert status == "succeeded"
    assert await _agents(sessions, run_id) == ["planner", "research", "draft", "execute", "validate", "evaluate"]
    async with sessions() as session:
        run = await RunRepository(session).get(run_id)
        assert run is not None and run.final_score == 1.0 and run.total_tokens
        metrics = {e.metric: e.score for e in await TrajectoryRepository(session).evaluations(run_id)}
    assert metrics["first_pass_success"] == 1.0
    assert metrics["repair_iterations"] == 0


async def test_recovers_after_one_repair(
    sessions: async_sessionmaker[AsyncSession], settings: Settings, sandbox_factory: Callable[[list[int]], FakeSandbox]
) -> None:
    run_id, status = await _run(sessions, settings, sandbox_factory([1, 0]))

    assert status == "recovered"
    assert await _agents(sessions, run_id) == [
        "planner",
        "research",
        "draft",
        "execute",
        "validate",
        "analyze_failure",
        "repair",
        "execute",
        "validate",
        "evaluate",
    ]
    async with sessions() as session:
        failures = await TrajectoryRepository(session).failures(run_id)
    assert [(f.category, f.resolved) for f in failures] == [("CODE_GENERATION", True)]


async def test_stops_when_repair_budget_exhausted(
    sessions: async_sessionmaker[AsyncSession], settings: Settings, sandbox_factory: Callable[[list[int]], FakeSandbox]
) -> None:
    sandbox = sandbox_factory([1])
    run_id, status = await _run(sessions, settings, sandbox, max_repairs=2)

    assert status == "failed"
    assert len(sandbox.calls) == 3  # initial attempt + 2 repairs
    agents = await _agents(sessions, run_id)
    assert agents.count("repair") == 2
    assert agents[-2:] == ["analyze_failure", "evaluate"]


async def test_node_error_marks_run_errored(
    sessions: async_sessionmaker[AsyncSession], settings: Settings, sandbox_factory: Callable[[list[int]], FakeSandbox]
) -> None:
    from agenteval.llm.provider import FakeProvider

    async with sessions() as session:
        run = await RunRepository(session).create("x", "software_engineering", {})
    status = await execute_run(
        run.id, sessions, settings, llm=FakeProvider(lambda s, p: "not json"), sandbox=sandbox_factory([0])
    )

    assert status == "error"
    assert await _agents(sessions, run.id) == ["planner"]
    async with sessions() as session:
        stored = await RunRepository(session).get(run.id)
    assert stored is not None and stored.status == "error"
