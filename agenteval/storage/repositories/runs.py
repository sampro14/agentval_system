from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from agenteval.storage.models import AgentEvent, Evaluation, Failure, Repair, Run


class RunRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, task: str, workflow: str, model_config: dict[str, Any]) -> Run:
        run = Run(task=task, workflow=workflow, model_config_=model_config)
        self.session.add(run)
        await self.session.commit()
        return run

    async def get(self, run_id: str) -> Run | None:
        return await self.session.get(Run, run_id)

    async def mark_running(self, run: Run) -> None:
        run.status = "running"
        run.started_at = datetime.now(UTC)
        await self.session.commit()

    async def finish(
        self, run: Run, status: str, metrics: dict[str, Any] | None = None, error: str | None = None
    ) -> None:
        run.status = status
        run.error = error
        run.completed_at = datetime.now(UTC)
        if metrics:
            run.total_tokens = metrics.get("total_tokens")
            run.total_latency_ms = metrics.get("total_latency_ms")
            run.final_score = 1.0 if metrics.get("task_success") else 0.0
        await self.session.commit()


class TrajectoryRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def add_events(self, run_id: str, events: list[dict[str, Any]]) -> None:
        seq = (
            await self.session.scalar(
                select(func.coalesce(func.max(AgentEvent.seq), 0)).where(AgentEvent.run_id == run_id)
            )
            or 0
        )
        for offset, event in enumerate(events, start=1):
            self.session.add(
                AgentEvent(
                    id=event["event_id"],
                    run_id=run_id,
                    seq=seq + offset,
                    agent_name=event["agent"],
                    event_type=event["event_type"],
                    output=event["output"],
                    status=event["status"],
                    latency_ms=event["latency_ms"],
                    token_usage=event["token_usage"],
                    created_at=datetime.fromisoformat(event["timestamp"]),
                )
            )
        await self.session.commit()

    async def add_failure(self, run_id: str, failure: dict[str, Any]) -> Failure:
        row = Failure(
            run_id=run_id,
            stage=failure["stage"],
            category=failure["category"],
            root_cause=failure["root_cause"],
            confidence=failure["confidence"],
            evidence=failure["evidence"],
            recommended_action=failure.get("recommended_action"),
            attribution_method=failure.get("attribution_method"),
        )
        self.session.add(row)
        await self.session.commit()
        return row

    async def add_repair(self, run_id: str, failure_id: str | None, repair: dict[str, Any]) -> None:
        self.session.add(
            Repair(
                run_id=run_id,
                failure_id=failure_id,
                iteration=repair["iteration"],
                action=repair["action"],
                result={"files_modified": repair["files_modified"]},
                status="applied",
            )
        )
        await self.session.commit()

    async def resolve_failures(self, run_id: str) -> None:
        for failure in await self.failures(run_id):
            failure.resolved = True
        await self.session.commit()

    async def add_evaluations(self, run_id: str, metrics: dict[str, Any], evaluator: str) -> None:
        for metric, value in metrics.items():
            self.session.add(Evaluation(run_id=run_id, metric=metric, score=float(value), evaluator=evaluator))
        await self.session.commit()

    async def events(self, run_id: str) -> list[AgentEvent]:
        result = await self.session.scalars(
            select(AgentEvent).where(AgentEvent.run_id == run_id).order_by(AgentEvent.seq)
        )
        return list(result)

    async def failures(self, run_id: str) -> list[Failure]:
        return list(await self.session.scalars(select(Failure).where(Failure.run_id == run_id)))

    async def evaluations(self, run_id: str) -> list[Evaluation]:
        return list(await self.session.scalars(select(Evaluation).where(Evaluation.run_id == run_id)))
