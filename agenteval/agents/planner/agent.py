"""Planner: natural-language task -> structured task graph (design doc §8.1).

The planner is shown a compact summary of the real repository, must name the files each task changes,
and its output is checked before it is trusted: structural problems (duplicate ids, dangling
dependencies, cycles) trigger one retry with the error fed back; softer quality problems are recorded
on the event so they can be measured.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from agenteval.agents.common import complete_json
from agenteval.agents.repo import empty_files, summarize_repo
from agenteval.execution.workspace import list_files
from agenteval.orchestration.deps import Deps
from agenteval.orchestration.state import AgentEvalState

MIN_TASKS, MAX_TASKS = 2, 12

SYSTEM = """You are the planning agent in a software-engineering workflow.
Break the user's task into a small dependency graph of concrete implementation tasks.
Other agents already explore the repository, run the test suite and review the result, so do NOT create
tasks that only inspect code, run tests or verify; every task must change files.
Ground the plan in the repository summary you are given: refer to real files and functions. List in
`files_to_touch` only files that already exist, and in `new_files` only files that do not exist yet.
When the database schema changes, one task must add a new numbered SQL file in `migrations/`, continuing
the existing numbering. Test files belong in `files_to_touch` or `new_files` like any other file.
Only list a file in `files_to_touch` when the change really needs to edit it. A file marked
`(empty file)`, such as an empty `__init__.py`, needs no edit: modules are imported by their full path
(for example `from authkit.users import x`), so do not add re-exports or other changes there.
Use between 2 and 8 tasks.
Respond with only a JSON object of this shape:
{"goal": str, "tasks": [{"id": "T1", "description": str, "dependencies": [task ids],
  "files_to_touch": [paths], "new_files": [paths], "expected_output": str, "validation_criteria": str}]}"""


class PlanTask(BaseModel):
    id: str
    description: str
    dependencies: list[str] = Field(default_factory=list)
    files_to_touch: list[str] = Field(default_factory=list)
    new_files: list[str] = Field(default_factory=list)
    expected_output: str = ""
    validation_criteria: str = ""


class Plan(BaseModel):
    goal: str
    tasks: list[PlanTask] = Field(min_length=1)


# --- checking what comes back -------------------------------------------------------------------


def find_cycle(tasks: list[PlanTask]) -> list[str] | None:
    graph = {t.id: list(t.dependencies) for t in tasks}
    visiting: list[str] = []
    done: set[str] = set()

    def visit(node: str) -> list[str] | None:
        if node in visiting:
            return [*visiting[visiting.index(node) :], node]
        if node in done or node not in graph:
            return None
        visiting.append(node)
        for dep in graph[node]:
            if cycle := visit(dep):
                return cycle
        visiting.pop()
        done.add(node)
        return None

    for task in tasks:
        if cycle := visit(task.id):
            return cycle
    return None


def structural_issues(plan: Plan) -> list[str]:
    """Problems that make a plan unusable; these trigger a retry."""
    ids = [t.id for t in plan.tasks]
    issues = [f"duplicate task id {i}" for i in sorted({i for i in ids if ids.count(i) > 1})]
    for task in plan.tasks:
        issues += [f"{task.id} depends on unknown task {d}" for d in task.dependencies if d not in ids]
    if not issues and (cycle := find_cycle(plan.tasks)):
        issues.append(f"dependency cycle {' -> '.join(cycle)}")
    return issues


def _unsafe(path: str) -> bool:
    return path.startswith("/") or ".." in Path(path).parts


def quality_issues(plan: Plan, repo_files: list[str], task_text: str, empty: frozenset[str] = frozenset()) -> list[str]:
    """Softer problems, recorded on the event but not retried."""
    issues = []
    if not MIN_TASKS <= len(plan.tasks) <= MAX_TASKS:
        issues.append(f"task count {len(plan.tasks)} is outside {MIN_TASKS}-{MAX_TASKS}")
    existing = set(repo_files)
    planned_new: list[str] = []
    for task in plan.tasks:
        if not task.validation_criteria.strip():
            issues.append(f"{task.id} has no validation_criteria")
        if not task.files_to_touch and not task.new_files:
            issues.append(f"{task.id} changes no files (inspecting, running or verifying belongs to other agents)")
        for path in task.files_to_touch:
            if _unsafe(path):
                issues.append(f"{task.id} uses an unsafe path {path}")
            elif path not in existing:
                issues.append(f"{task.id} touches {path}, which does not exist")
            elif path in empty and Path(path).name == "__init__.py":
                issues.append(
                    f"{task.id} edits {path}, which is empty; modules are imported by full path, so it needs no change"
                )
        for path in task.new_files:
            if _unsafe(path):
                issues.append(f"{task.id} uses an unsafe path {path}")
            elif path in existing:
                issues.append(f"{task.id} creates {path}, which already exists")
        planned_new += task.new_files

    lowered = task_text.lower()
    new_sql = [p for p in planned_new if p.startswith("migrations/") and p.endswith(".sql")]
    if "migration" in lowered and not new_sql:
        issues.append("the task asks for a migration but the plan adds no new migrations/*.sql file")
    numbers = [int(m.group(1)) for f in repo_files if (m := re.match(r"migrations/(\d+)_", f))]
    for path in new_sql:
        match = re.match(r"migrations/(\d+)_", path)
        if numbers and (not match or int(match.group(1)) <= max(numbers)):
            issues.append(f"new migration {path} is not numbered after the existing {max(numbers):03d}")
    planned = {p for t in plan.tasks for p in (*t.files_to_touch, *t.new_files)}
    if "test" in lowered and not any(Path(p).name.startswith("test_") or p.startswith("tests/") for p in planned):
        issues.append("the task asks for tests but the plan names no test file")
    return issues


# --- the agent ----------------------------------------------------------------------------------


def parse_plan(data: dict[str, Any]) -> Plan:
    """Validate a reply as a plan; anything unusable raises ValueError, which triggers the retry."""
    parsed = Plan.model_validate(data)
    if problems := structural_issues(parsed):
        raise ValueError("; ".join(problems))
    return parsed


async def plan(state: AgentEvalState, deps: Deps) -> dict[str, Any]:
    workspace = Path(state["workspace"])
    repo_files = list_files(workspace)
    prompt = (
        f"Task:\n{state['task']}\n\n"
        f"Repository (every file, with its top-level functions, classes and constants):\n"
        f"{summarize_repo(workspace)}\n\n"
        "Available tools: file write, pytest inside an isolated container."
    )

    parsed, attempts = await complete_json(deps.llm, SYSTEM, prompt, parse_plan, who="planner")

    issues = quality_issues(parsed, repo_files, state["task"], frozenset(empty_files(workspace)))
    result = parsed.model_dump()
    return {
        "plan": result,
        "event": {
            "event_type": "PLAN_CREATED",
            "output": {
                "attempts": attempts,
                "task_count": len(parsed.tasks),
                "dangling_dependencies": [],
                "quality": {"ok": not issues, "issues": issues},
                "plan": result,
            },
        },
    }
