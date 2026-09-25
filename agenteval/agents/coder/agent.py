"""Drafting / coding agent: produce the implementation as file writes captured as diffs (design doc §8.3)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agenteval.execution.workspace import write_file
from agenteval.llm.provider import parse_json
from agenteval.orchestration.deps import Deps
from agenteval.orchestration.state import AgentEvalState

SYSTEM = """You are the coding agent in a software-engineering workflow.
Implement the plan inside the repository, including pytest tests that verify the behaviour.

The code is executed offline in an isolated container with Python 3.12. Only the standard library,
pytest, and (for browser tests) playwright with pytest-playwright and Chromium are installed; do not
use any other third-party package. Put tests in test_*.py files so pytest collects them. Browser
tests must use pytest-playwright's `page` fixture and load pages via file:// or a server the test
starts itself.

Respond with only a JSON object:
{"summary": str, "files": [{"path": relative path, "content": full new file content}]}"""


def build_prompt(state: AgentEvalState, feedback: str = "") -> str:
    context = "\n\n".join(
        f"--- {c['path']} ---\n{c['content']}" if "path" in c else f"Notes: {c['content']}"
        for c in state.get("context", [])
    )
    prompt = f"Task:\n{state['task']}\n\nPlan:\n{json.dumps(state.get('plan', {}))}\n\nContext:\n{context or '(none)'}"
    if feedback:
        prompt += f"\n\nThe previous attempt failed. Fix it.\n{feedback}"
    return prompt


async def write_changes(state: AgentEvalState, deps: Deps, feedback: str = "") -> tuple[str, list[dict[str, Any]]]:
    draft = parse_json((await deps.llm.complete(SYSTEM, build_prompt(state, feedback))).text)
    workspace = Path(state["workspace"])
    artifacts = [write_file(workspace, f["path"], f["content"]) for f in draft.get("files", [])]
    return draft.get("summary", ""), artifacts


async def draft(state: AgentEvalState, deps: Deps) -> dict[str, Any]:
    summary, artifacts = await write_changes(state, deps)
    return {
        "artifacts": state.get("artifacts", []) + artifacts,
        "event": {
            "event_type": "DRAFT_CREATED",
            "status": "ok" if artifacts else "empty",
            "output": {
                "summary": summary,
                "files_modified": [a["path"] for a in artifacts],
                "lines_added": sum(a["lines_added"] for a in artifacts),
                "lines_removed": sum(a["lines_removed"] for a in artifacts),
            },
        },
    }
