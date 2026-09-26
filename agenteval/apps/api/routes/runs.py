"""Run endpoints (design doc §15)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from agenteval.config import DEFAULT_MODELS, ProviderName, get_settings
from agenteval.datasets import get_task
from agenteval.storage.models import Run
from agenteval.storage.repositories.runs import RunRepository, TrajectoryRepository
from agenteval.workers.queue import RunQueue

router = APIRouter(prefix="/api/v1/runs", tags=["runs"])


class CreateRun(BaseModel):
    task: str | None = Field(default=None, min_length=1)
    task_id: str | None = None  # a task from datasets/tasks.json, run against the sample app
    workflow: str = "software_engineering"
    provider: ProviderName | None = None
    model: str | None = None
    max_repair_iterations: int = Field(default=3, ge=0, le=10)

    @model_validator(mode="after")
    def _needs_a_task(self) -> CreateRun:
        if not self.task and not self.task_id:
            raise ValueError("provide `task` or `task_id`")
        return self


class RunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    task: str
    workflow: str
    workflow_version: str
    provider: str | None
    model: str | None
    task_id: str | None
    status: str
    error: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    total_tokens: int | None
    total_latency_ms: int | None
    final_score: float | None
    # 1.0 / 0.0 from the task's hidden acceptance test; None if the run has no such test (or it hasn't run yet).
    acceptance_passed: float | None = None


class EventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    seq: int
    agent_name: str
    event_type: str
    status: str
    output: dict[str, Any]
    latency_ms: int
    token_usage: dict[str, Any]
    created_at: datetime


class FailureOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    stage: str
    category: str
    root_cause: str
    confidence: float
    evidence: list[Any]
    recommended_action: str | None
    attribution_method: str | None
    resolved: bool


class EvaluationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    metric: str
    score: float
    evaluator: str


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.sessions() as session:
        yield session


def get_queue(request: Request) -> RunQueue:
    queue: RunQueue = request.app.state.queue
    return queue


async def _require_run(run_id: str, session: AsyncSession) -> Run:
    run = await RunRepository(session).get(run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "run not found")
    return run


@router.post("", status_code=status.HTTP_202_ACCEPTED, response_model=RunOut)
async def create_run(
    body: CreateRun, session: AsyncSession = Depends(get_session), queue: RunQueue = Depends(get_queue)
) -> Run:
    settings = get_settings()
    task_text = body.task
    seed_config: dict[str, Any] = {}
    if body.task_id:
        try:
            dataset_task = get_task(body.task_id)
        except KeyError:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown task_id: {body.task_id}") from None
        task_text = task_text or dataset_task.task
        seed_config = {"task_id": dataset_task.id, "seed": "sample_app"}
    provider = body.provider or settings.llm_provider
    # A provider override without a model uses that provider's default, not the global model.
    model = body.model or (DEFAULT_MODELS[provider] if body.provider else settings.resolved_model)
    model_config: dict[str, Any] = {
        "llm_provider": provider,
        "llm_model": model,
        "max_repair_iterations": body.max_repair_iterations,
        **seed_config,
    }
    run = await RunRepository(session).create(task_text or "", body.workflow, model_config)
    await queue.enqueue(run.id)
    return run


async def _with_acceptance(runs: list[Run], session: AsyncSession) -> list[RunOut]:
    acceptance = await TrajectoryRepository(session).metric_by_run([r.id for r in runs], "acceptance_passed")
    return [RunOut.model_validate(r).model_copy(update={"acceptance_passed": acceptance.get(r.id)}) for r in runs]


@router.get("", response_model=list[RunOut])
async def list_runs(limit: int = Query(50, ge=1, le=200), session: AsyncSession = Depends(get_session)) -> list[RunOut]:
    """Newest runs first."""
    return await _with_acceptance(await RunRepository(session).list_runs(limit), session)


@router.get("/{run_id}", response_model=RunOut)
async def get_run(run_id: str, session: AsyncSession = Depends(get_session)) -> RunOut:
    run = await _require_run(run_id, session)
    return (await _with_acceptance([run], session))[0]


@router.get("/{run_id}/trajectory", response_model=list[EventOut])
async def get_trajectory(
    run_id: str, after_seq: int = Query(0, ge=0), session: AsyncSession = Depends(get_session)
) -> list[Any]:
    """The run's events in order. Pass after_seq (the last seq already seen) to get only newer ones."""
    await _require_run(run_id, session)
    return list(await TrajectoryRepository(session).events(run_id, after_seq))


@router.get("/{run_id}/failures", response_model=list[FailureOut])
async def get_failures(run_id: str, session: AsyncSession = Depends(get_session)) -> list[Any]:
    await _require_run(run_id, session)
    return list(await TrajectoryRepository(session).failures(run_id))


@router.get("/{run_id}/evaluation", response_model=list[EvaluationOut])
async def get_evaluation(run_id: str, session: AsyncSession = Depends(get_session)) -> list[Any]:
    await _require_run(run_id, session)
    return list(await TrajectoryRepository(session).evaluations(run_id))
