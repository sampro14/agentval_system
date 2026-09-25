"""Research / context agent: gather evidence from the repository before drafting (design doc §8.2)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agenteval.execution.workspace import list_files, read_files
from agenteval.llm.provider import parse_json
from agenteval.orchestration.deps import Deps
from agenteval.orchestration.state import AgentEvalState

SYSTEM = """You are the research agent in a software-engineering workflow.
Given a task, its plan and the repository file list, choose the files whose contents are needed
to implement the plan. Respond with only a JSON object:
{"relevant_files": [paths from the list], "notes": str}"""


async def research(state: AgentEvalState, deps: Deps) -> dict[str, Any]:
    workspace = Path(state["workspace"])
    files = list_files(workspace)
    prompt = (
        f"Task:\n{state['task']}\n\nPlan:\n{json.dumps(state.get('plan', {}))}\n\n"
        f"Repository files:\n{json.dumps(files)}"
    )
    selection = parse_json((await deps.llm.complete(SYSTEM, prompt)).text)
    requested = [f for f in selection.get("relevant_files", []) if isinstance(f, str)]
    unknown = [f for f in requested if f not in files]
    contents = read_files(workspace, [f for f in requested if f in files])
    context = [{"source": "repository", "path": path, "content": text} for path, text in contents.items()]
    notes = selection.get("notes", "")
    if notes:
        context.append({"source": "research_notes", "content": notes})
    return {
        "context": context,
        "event": {
            "event_type": "CONTEXT_GATHERED",
            "output": {"files_available": len(files), "files_selected": list(contents), "unknown_files": unknown},
        },
    }
