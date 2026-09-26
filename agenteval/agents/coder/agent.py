"""Drafting / coding agent: produce the implementation as file writes captured as diffs (design doc §8.3).

The coder writes whole files, which makes it dangerous in two ways that are guarded in code rather than
left to the prompt: a reply can be empty or contain broken Python, and rewriting a file from memory can
silently delete code it was not asked to change. A draft is validated before anything is written; a bad
one is sent back once with the reason. The coder is also always shown files as they are now (including
ones an earlier draft created), so a repair builds on the draft instead of replacing it.
"""

from __future__ import annotations

import ast
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from agenteval.agents.common import complete_json
from agenteval.execution.workspace import MAX_FILE_BYTES, write_file
from agenteval.orchestration.deps import Deps
from agenteval.orchestration.state import AgentEvalState

MAX_DIFF_CHARS = 20_000

SYSTEM = """You are the coding agent in a software-engineering workflow.
Implement the plan inside the repository, including pytest tests that verify the behaviour.

You write whole files. The context shows the current contents of the files you need. For every file that
already exists, your reply must contain the complete new file: keep every existing import, function, class
and test that the task does not ask you to change. Only write files the plan names, and reuse the helpers
the context shows instead of writing new ones.

The code is executed offline in an isolated container with Python 3.12. Only the standard library,
pytest, and (for browser tests) playwright with pytest-playwright and Chromium are installed; do not
use any other third-party package. Put tests in test_*.py files so pytest collects them. Browser
tests must use pytest-playwright's `page` fixture and load pages via file:// or a server the test
starts itself. New tests should use the fixtures already defined in tests/conftest.py.

Respond with only a JSON object, with no text before or after it:
{"summary": str, "files": [{"path": relative path, "content": full new file content}]}
Inside JSON strings, escape quotes and write line breaks as \\n."""


class DraftFile(BaseModel):
    path: str
    content: str


class Draft(BaseModel):
    summary: str = ""
    files: list[DraftFile] = Field(default_factory=list)


@dataclass
class WrittenChanges:
    summary: str
    artifacts: list[dict[str, Any]]
    attempts: int
    plan_check: dict[str, list[str]]


# --- what the coder is shown --------------------------------------------------------------------------


def current_context(state: AgentEvalState) -> list[tuple[str, str, str]]:
    """(path, why, content) for every file the coder should see, read from the workspace as it is now.

    That is the research context plus every file an earlier draft wrote. Using the live workspace (not
    the research-time copy) is what lets a repair build on the draft.
    """
    workspace = Path(state["workspace"])
    reasons = {c["path"]: c.get("why", "") for c in state.get("context", []) if "path" in c}
    for artifact in state.get("artifacts", []):
        reasons.setdefault(artifact["path"], "written by an earlier draft")
    files = []
    for path, why in reasons.items():
        target = workspace / path
        if target.is_file() and target.stat().st_size <= MAX_FILE_BYTES:
            files.append((path, why, target.read_text(errors="replace")))
    return files


def build_prompt(state: AgentEvalState, feedback: str = "") -> str:
    context = "\n\n".join(
        f"--- {path}{f' ({why})' if why else ''} ---\n{content}" for path, why, content in current_context(state)
    )
    prompt = f"Task:\n{state['task']}\n\nPlan:\n{json.dumps(state.get('plan', {}))}\n\nContext:\n{context or '(none)'}"
    if feedback:
        prompt += f"\n\nThe previous attempt failed. The files above are as that attempt left them. Fix it.\n{feedback}"
    return prompt


# --- checking what comes back -------------------------------------------------------------------------


def defined_names(source: str) -> set[str]:
    """Names of the top-level functions and classes."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()
    return {n.name for n in tree.body if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)}


def draft_problems(draft: Draft, workspace: Path) -> list[str]:
    """Reasons to reject a draft before writing anything."""
    if not draft.files:
        return ["you returned no files: write the implementation and its tests"]
    problems: list[str] = []
    seen: set[str] = set()
    for f in draft.files:
        if f.path in seen:
            problems.append(f"{f.path} is listed twice")
        seen.add(f.path)
        if f.path.startswith("/") or ".." in Path(f.path).parts:
            problems.append(f"{f.path} is outside the repository")
            continue
        if not f.content.strip() and Path(f.path).name != "__init__.py":
            problems.append(f"{f.path} has no content")
        if f.path.endswith(".py"):
            try:
                ast.parse(f.content)
            except SyntaxError as exc:
                problems.append(f"{f.path} is not valid Python: {exc.msg} (line {exc.lineno})")
                continue
            existing = workspace / f.path
            if existing.is_file():
                lost = sorted(defined_names(existing.read_text(errors="replace")) - defined_names(f.content))
                if lost:
                    problems.append(
                        f"{f.path} no longer defines {', '.join(lost)}: keep existing code the task does not change"
                    )
    return problems


def plan_adherence(plan: dict[str, Any], written: list[str]) -> dict[str, list[str]]:
    """Files the plan names versus files actually written."""
    tasks: list[dict[str, Any]] = plan.get("tasks", [])
    planned: list[str] = list(
        dict.fromkeys(f for t in tasks for f in t.get("files_to_touch", []) + t.get("new_files", []))
    )
    written_unique = list(dict.fromkeys(written))
    return {
        "planned_not_written": [f for f in planned if f not in written_unique],
        "written_not_planned": [f for f in written_unique if f not in planned] if planned else [],
    }


# --- the agent ----------------------------------------------------------------------------------------


async def write_changes(state: AgentEvalState, deps: Deps, feedback: str = "") -> WrittenChanges:
    workspace = Path(state["workspace"])

    def parse(data: dict[str, Any]) -> Draft:
        draft = Draft.model_validate(data)
        if problems := draft_problems(draft, workspace):
            raise ValueError("; ".join(problems))
        return draft

    draft, attempts = await complete_json(deps.llm, SYSTEM, build_prompt(state, feedback), parse, who="coder")
    artifacts = [write_file(workspace, f.path, f.content) for f in draft.files]
    return WrittenChanges(
        summary=draft.summary,
        artifacts=artifacts,
        attempts=attempts,
        plan_check=plan_adherence(state.get("plan", {}), [str(a["path"]) for a in artifacts]),
    )


def diffs_of(artifacts: list[dict[str, Any]]) -> dict[str, str]:
    """Per-file unified diffs for the trajectory, so the result can be inspected. Capped per file."""
    return {a["path"]: a["diff"][:MAX_DIFF_CHARS] for a in artifacts}


async def draft(state: AgentEvalState, deps: Deps) -> dict[str, Any]:
    changes = await write_changes(state, deps)
    artifacts = changes.artifacts
    return {
        "artifacts": state.get("artifacts", []) + artifacts,
        "event": {
            "event_type": "DRAFT_CREATED",
            "output": {
                "attempts": changes.attempts,
                "summary": changes.summary,
                "files_modified": [a["path"] for a in artifacts],
                "lines_added": sum(a["lines_added"] for a in artifacts),
                "lines_removed": sum(a["lines_removed"] for a in artifacts),
                "plan_check": changes.plan_check,
                "diffs": diffs_of(artifacts),
            },
        },
    }
