"""Consumes queued runs from Redis, executes the LangGraph workflow, and persists the trajectory
as it streams — each node's events are written as soon as the node finishes.

Run with: uv run python -m agenteval.workers.run_worker
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from typing import Any

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agenteval.config import Settings, get_settings
from agenteval.datasets import SEEDS, get_task
from agenteval.evaluation.acceptance import run_acceptance
from agenteval.evaluation.retrieval import score_retrieval
from agenteval.execution.docker.sandbox import DockerSandbox, Sandbox
from agenteval.execution.workspace import create_workspace, seed_workspace
from agenteval.llm.provider import LLMProvider, get_provider
from agenteval.observability.events import NodeError
from agenteval.orchestration.deps import Deps
from agenteval.orchestration.graph import build_graph
from agenteval.storage.db import make_engine, make_sessionmaker
from agenteval.storage.repositories.runs import RunRepository, TrajectoryRepository
from agenteval.workers.queue import RedisRunQueue

log = logging.getLogger("agenteval.worker")


async def execute_run(
    run_id: str,
    sessions: async_sessionmaker[AsyncSession],
    settings: Settings,
    llm: LLMProvider | None = None,
    sandbox: Sandbox | None = None,
    keep_workspace: bool = False,
) -> str:
    async with sessions() as session:
        runs, trajectory = RunRepository(session), TrajectoryRepository(session)
        run = await runs.get(run_id)
        if run is None:
            log.warning("run %s not found", run_id)
            return "missing"
        await runs.mark_running(run)

        # Only setting overrides go into Settings; the rest of model_config (task_id, seed) is run metadata.
        overrides = {k: v for k, v in run.model_config_.items() if k in Settings.model_fields}
        run_settings = settings.model_copy(update=overrides)
        deps = Deps(
            settings=run_settings,
            llm=llm or get_provider(run_settings),
            sandbox=sandbox or DockerSandbox(run_settings),
        )
        workspace = create_workspace(settings.workspace_root, run_id)
        if seed := SEEDS.get(run.model_config_.get("seed", "")):
            seed_workspace(seed, workspace)
        state: dict[str, Any] = {"run_id": run_id, "task": run.task, "workspace": str(workspace), "trajectory": []}
        failure_ids: list[str] = []
        final: dict[str, Any] = {}
        events_seen: list[dict[str, Any]] = []
        try:
            async for chunk in build_graph(deps).astream(state, stream_mode="updates"):
                for update in chunk.values():
                    events_seen += update.get("trajectory", [])
                    await trajectory.add_events(run_id, update.get("trajectory", []))
                    if "failures" in update:
                        row = await trajectory.add_failure(run_id, update["failures"][-1])
                        failure_ids.append(row.id)
                    if "repairs" in update:
                        repair = update["repairs"][-1]
                        await trajectory.add_repair(run_id, failure_ids[repair["failure_index"]], repair)
                    final.update(update)
            if task_id := run.model_config_.get("task_id"):
                # Independent ground truth from the task's hidden acceptance test, run before cleanup.
                acceptance = await run_acceptance(get_task(task_id), workspace, deps.sandbox, run_settings)
                if acceptance:
                    await trajectory.add_evaluations(run_id, acceptance, evaluator="acceptance_v1")
                # How well research chose what to read, scored against the task's ground truth.
                research = next((e for e in events_seen if e["agent"] == "research"), None)
                if research:
                    report = score_retrieval(get_task(task_id), research["output"]["files_selected"])
                    await trajectory.add_evaluations(run_id, report.metrics(), evaluator="retrieval_v1")
        except NodeError as exc:
            await trajectory.add_events(run_id, [exc.event])
            await runs.finish(run, "error", error=str(exc))
            log.exception("run %s errored", run_id)
            return "error"
        finally:
            if not keep_workspace:
                shutil.rmtree(workspace, ignore_errors=True)

        metrics = final.get("metrics", {})
        await trajectory.add_evaluations(run_id, metrics, evaluator="run_metrics_v1")
        if final.get("status") in ("succeeded", "recovered"):
            await trajectory.resolve_failures(run_id)
        status: str = final.get("status", "failed")
        await runs.finish(run, status, metrics)
        return status


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = get_settings()
    engine = make_engine(settings.database_url)
    sessions = make_sessionmaker(engine)
    # Blocking BLPOP must not race the client socket timeout.
    queue = RedisRunQueue(Redis.from_url(settings.redis_url, socket_timeout=None), settings.run_queue)
    log.info("worker listening on %s", settings.run_queue)
    while True:
        run_id = await queue.dequeue()
        if run_id:
            log.info("run %s -> %s", run_id, await execute_run(run_id, sessions, settings))


if __name__ == "__main__":
    asyncio.run(main())
