"""Run endpoints (design doc §15)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from agenteval.storage.models import Run
from agenteval.storage.repositories.runs import RunRepository, TrajectoryRepository
from agenteval.workers.queue import RunQueue

router = APIRouter(prefix="/api/v1/runs", tags=["runs"])


class CreateRun(BaseModel):
    task: str = Field(min_length=1)
    workflow: str = "software_engineering"
    model: str | None = None
    max_repair_iterations: int = Field(default=3, ge=0, le=10)


class RunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    task: str
    workflow: str
    workflow_version: str
    status: str
    error: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    total_tokens: int | None
    total_latency_ms: int | None
    final_score: float | None


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
    model_config: dict[str, Any] = {"max_repair_iterations": body.max_repair_iterations}
    if body.model:
        model_config["llm_model"] = body.model
    run = await RunRepository(session).create(body.task, body.workflow, model_config)
    await queue.enqueue(run.id)
    return run


@router.get("/{run_id}", response_model=RunOut)
async def get_run(run_id: str, session: AsyncSession = Depends(get_session)) -> Run:
    return await _require_run(run_id, session)


@router.get("/{run_id}/trajectory", response_model=list[EventOut])
async def get_trajectory(run_id: str, session: AsyncSession = Depends(get_session)) -> list[Any]:
    await _require_run(run_id, session)
    return list(await TrajectoryRepository(session).events(run_id))


@router.get("/{run_id}/failures", response_model=list[FailureOut])
async def get_failures(run_id: str, session: AsyncSession = Depends(get_session)) -> list[Any]:
    await _require_run(run_id, session)
    return list(await TrajectoryRepository(session).failures(run_id))


@router.get("/{run_id}/evaluation", response_model=list[EvaluationOut])
async def get_evaluation(run_id: str, session: AsyncSession = Depends(get_session)) -> list[Any]:
    await _require_run(run_id, session)
    return list(await TrajectoryRepository(session).evaluations(run_id))
