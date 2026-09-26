"""Read-only reference data for the dashboard: the task set and the LLM providers."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from agenteval.config import DEFAULT_MODELS, PROVIDER_KEY_VARS, provider_configured
from agenteval.datasets import load_tasks

router = APIRouter(prefix="/api/v1", tags=["meta"])


class TaskOut(BaseModel):
    id: str
    title: str
    difficulty: str
    tags: list[str]
    task: str
    requirements: list[str]


class ProviderOut(BaseModel):
    name: str
    default_model: str
    configured: bool  # is its API key present in the API process's environment
    key_env_vars: list[str]  # which variable(s) to set; never the key itself


@router.get("/tasks", response_model=list[TaskOut])
async def list_tasks() -> list[TaskOut]:
    return [
        TaskOut(
            id=t.id,
            title=t.title,
            difficulty=t.difficulty,
            tags=list(t.tags),
            task=t.task,
            requirements=list(t.requirements),
        )
        for t in load_tasks()
    ]


@router.get("/providers", response_model=list[ProviderOut])
async def list_providers() -> list[ProviderOut]:
    return [
        ProviderOut(
            name=name,
            default_model=model,
            configured=provider_configured(name),
            key_env_vars=list(PROVIDER_KEY_VARS[name]),
        )
        for name, model in DEFAULT_MODELS.items()
    ]
