"""Run one agent at a time against the sample app (the dashboard's step-by-step page).

These endpoints spend LLM money and start Docker containers, and the API has no login, so they are
off unless AGENTEVAL_ENABLE_DEV_ENDPOINTS=true.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, field_validator

from agenteval.config import ProviderName, Settings, get_settings
from agenteval.datasets import get_task
from agenteval.dev.stage import load_stage_result, run_stage, stage_status
from agenteval.execution.docker.sandbox import DockerSandbox, Sandbox
from agenteval.llm.provider import LLMProvider, get_provider
from agenteval.orchestration.graph import NODES

StageDeps = tuple[LLMProvider, Sandbox]


def default_stage_deps(settings: Settings) -> StageDeps:
    return get_provider(settings), DockerSandbox(settings)


def require_dev_endpoints() -> None:
    if not get_settings().enable_dev_endpoints:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "dev endpoints are disabled (set AGENTEVAL_ENABLE_DEV_ENDPOINTS=true)"
        )


router = APIRouter(prefix="/api/v1/dev", tags=["dev"], dependencies=[Depends(require_dev_endpoints)])


class StageRequest(BaseModel):
    agent: str
    task_id: str
    provider: ProviderName | None = None
    model: str | None = None
    fresh: bool = False

    @field_validator("agent")
    @classmethod
    def _known_agent(cls, value: str) -> str:
        if value not in NODES:
            raise ValueError(f"unknown agent {value!r}; choose one of {', '.join(NODES)}")
        return value


def _require_task(task_id: str) -> None:
    try:
        get_task(task_id)
    except KeyError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown task_id: {task_id}") from None


@router.get("/stages/{task_id}")
async def get_stage_status(task_id: str, request: Request) -> dict[str, Any]:
    _require_task(task_id)
    return {"task_id": task_id, "agents": stage_status(task_id, request.app.state.dev_dir)}


@router.get("/stages/{task_id}/{agent}")
async def get_stage_result(task_id: str, agent: str, request: Request) -> dict[str, Any]:
    _require_task(task_id)
    result = load_stage_result(task_id, agent, request.app.state.dev_dir)
    if result is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{agent} has not run on {task_id}")
    return result


@router.post("/stages")
async def run_one_stage(body: StageRequest, request: Request) -> dict[str, Any]:
    _require_task(body.task_id)
    running: set[str] = request.app.state.stage_running
    if body.task_id in running:
        raise HTTPException(status.HTTP_409_CONFLICT, f"an agent is already running on {body.task_id}")

    settings = get_settings().model_copy(
        update={"llm_provider": body.provider or get_settings().llm_provider, "llm_model": body.model}
    )
    try:
        llm, sandbox = request.app.state.stage_deps(settings)
    except Exception as exc:  # e.g. the provider's SDK refusing to start without an API key
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"cannot start {settings.llm_provider}: {exc}") from exc

    running.add(body.task_id)
    try:
        result = await run_stage(
            body.agent,
            body.task_id,
            llm=llm,
            sandbox=sandbox,
            settings=settings,
            dev_dir=request.app.state.dev_dir,
            fresh=body.fresh,
        )
    finally:
        running.discard(body.task_id)
    return result.to_dict()
