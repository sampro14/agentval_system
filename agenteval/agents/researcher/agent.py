"""Research / context agent: decide what the coder must read, and read it (design doc §8.2).

The coder writes whole files and sees only the context chosen here, so two rules are enforced in code
rather than left to the model: every existing file the plan edits is always included (otherwise the
coder would overwrite code it never saw), and empty files are never read. The model adds the rest:
helpers to reuse, table definitions, test conventions, each with a one-line reason.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from agenteval.agents.common import complete_json
from agenteval.agents.repo import empty_files, summarize_repo
from agenteval.execution.workspace import MAX_FILE_BYTES, list_files, read_files
from agenteval.orchestration.deps import Deps
from agenteval.orchestration.state import AgentEvalState

MAX_CONTEXT_FILES = 10
MAX_CONTEXT_BYTES = 120_000
PLAN_EDIT_REASON = "the plan edits this file"

SYSTEM = """You are the research agent in a software-engineering workflow.
The coder implements the plan next. It can only use the files you choose: it sees their full contents
and nothing else, and it writes whole files. Files the plan edits are already included automatically,
so do not list them.
From the repository summary, choose the additional existing files whose contents the coder must read to
do the work correctly:
- modules whose functions it will call or should reuse;
- tables: whenever the plan creates, alters or queries a table, or references one (a foreign key, an
  UPDATE, a DELETE), include the migration that defines each table involved, because the coder needs the
  exact table and column names;
- existing tests or fixtures whose conventions the new tests must follow.
Give each file a concrete one-line reason that names what the coder will use from it (a function it will
call, a column name it must get right, a fixture it will use). If you cannot name something concrete, leave
the file out; reading a file "to see the patterns" or "for context" is not a reason. Skip files that are
empty or unrelated to the task.
Respond with only a JSON object:
{"files": [{"path": <a path from the summary>, "why": <one line>}]}"""


class FileChoice(BaseModel):
    path: str
    why: str = ""


class Selection(BaseModel):
    files: list[FileChoice] = Field(default_factory=list)


def plan_summary(plan: dict[str, Any]) -> str:
    tasks = plan.get("tasks", [])
    if not tasks:
        return "(no plan)"
    lines = [f"Goal: {plan.get('goal', '')}"]
    for t in tasks:
        touched = ", ".join(t.get("files_to_touch", [])) or "-"
        created = ", ".join(t.get("new_files", [])) or "-"
        lines.append(f"- {t['id']}: {t['description']} (edits: {touched}; creates: {created})")
    return "\n".join(lines)


def plan_edited_files(plan: dict[str, Any], repo_files: list[str]) -> tuple[list[str], list[str]]:
    """The existing files the plan edits (in plan order), and the ones it names that do not exist."""
    wanted = [f for t in plan.get("tasks", []) for f in t.get("files_to_touch", [])]
    unique = list(dict.fromkeys(wanted))
    return [f for f in unique if f in repo_files], [f for f in unique if f not in repo_files]


async def research(state: AgentEvalState, deps: Deps) -> dict[str, Any]:
    workspace = Path(state["workspace"])
    repo_files = list_files(workspace)
    empty = empty_files(workspace)
    plan = state.get("plan", {})
    auto, missing = plan_edited_files(plan, repo_files)

    prompt = (
        f"Task:\n{state['task']}\n\nPlan:\n{plan_summary(plan)}\n\n"
        f"Already included automatically (the plan edits them): {', '.join(auto) or 'none'}\n\n"
        f"Repository (every file, with its top-level functions, classes and constants):\n"
        f"{summarize_repo(workspace)}"
    )
    selection, attempts = await complete_json(deps.llm, SYSTEM, prompt, Selection.model_validate, who="research")

    reasons = dict.fromkeys(auto, PLAN_EDIT_REASON)
    unknown: list[str] = []
    skipped_empty: list[str] = []
    for choice in selection.files:
        if choice.path in reasons:
            continue  # already included, or listed twice
        if choice.path not in repo_files:
            unknown.append(choice.path)
        elif choice.path in empty:
            skipped_empty.append(choice.path)
        else:
            reasons[choice.path] = choice.why

    # Read in priority order (the plan's files first) within the size limits.
    chosen: list[str] = []
    dropped: list[str] = []
    too_large: list[str] = []
    total = 0
    for path in reasons:
        size = (workspace / path).stat().st_size
        if size > MAX_FILE_BYTES:
            too_large.append(path)
        elif len(chosen) >= MAX_CONTEXT_FILES or total + size > MAX_CONTEXT_BYTES:
            dropped.append(path)
        else:
            chosen.append(path)
            total += size

    contents = read_files(workspace, chosen)
    context = [
        {"source": "repository", "path": path, "content": contents[path], "why": reasons[path]} for path in chosen
    ]

    problems = [f"the plan edits {f}, which does not exist" for f in missing]
    problems += [f"{f} is too large to read ({(workspace / f).stat().st_size:,} bytes)" for f in too_large]
    problems += [f"{f} was dropped: context limit reached" for f in dropped]
    return {
        "context": context,
        "event": {
            "event_type": "CONTEXT_GATHERED",
            "output": {
                "attempts": attempts,
                "files_available": len(repo_files),
                "files_selected": chosen,
                "auto_included": [p for p in chosen if p in auto],
                "llm_selected": [p for p in chosen if p not in auto],
                "reasons": {p: reasons[p] for p in chosen},
                "unknown_files": unknown,
                "empty_skipped": skipped_empty,
                "dropped": dropped,
                "too_large": too_large,
                "bytes_read": total,
                "problems": problems,
            },
        },
    }
