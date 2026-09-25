"""Planner: natural-language task -> structured task graph (design doc §8.1)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from agenteval.execution.workspace import list_files
from agenteval.llm.provider import parse_json
from agenteval.orchestration.deps import Deps
from agenteval.orchestration.state import AgentEvalState

SYSTEM = """You are the planning agent in a software-engineering workflow.
Break the user's task into a small dependency graph of concrete engineering tasks.
Respond with only a JSON object of this shape:
{"goal": str, "tasks": [{"id": "T1", "description": str, "dependencies": [task ids],
  "expected_output": str, "validation_criteria": str}]}"""


class PlanTask(BaseModel):
    id: str
    description: str
    dependencies: list[str] = Field(default_factory=list)
    expected_output: str = ""
    validation_criteria: str = ""


class Plan(BaseModel):
    goal: str
    tasks: list[PlanTask]


async def plan(state: AgentEvalState, deps: Deps) -> dict[str, Any]:
    files = list_files(Path(state["workspace"]))
    prompt = (
        f"Task:\n{state['task']}\n\n"
        f"Repository files:\n{json.dumps(files) if files else '(empty repository)'}\n\n"
        "Available tools: file write, pytest inside an isolated container."
    )
    response = await deps.llm.complete(SYSTEM, prompt)
    parsed = Plan.model_validate(parse_json(response.text))
    known = {t.id for t in parsed.tasks}
    dangling = sorted({d for t in parsed.tasks for d in t.dependencies} - known)
    result = parsed.model_dump()
    return {
        "plan": result,
        "event": {
            "event_type": "PLAN_CREATED",
            "output": {"task_count": len(parsed.tasks), "dangling_dependencies": dangling, "plan": result},
        },
    }
