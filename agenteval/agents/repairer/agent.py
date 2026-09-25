"""Repair agent (Phase 1): re-run the coder with the failure evidence as feedback.

Phase 4 adds targeted repair proposals and recovery metrics (design doc §8.7).
"""

from __future__ import annotations

import json
from typing import Any

from agenteval.agents.coder.agent import write_changes
from agenteval.orchestration.deps import Deps
from agenteval.orchestration.state import AgentEvalState


async def repair(state: AgentEvalState, deps: Deps) -> dict[str, Any]:
    failure = state["failures"][-1]
    summary, artifacts = await write_changes(state, deps, feedback=json.dumps(failure))
    record = {
        "iteration": len(state.get("repairs", [])) + 1,
        "failure_index": len(state["failures"]) - 1,
        "action": summary,
        "files_modified": [a["path"] for a in artifacts],
    }
    return {
        "artifacts": state.get("artifacts", []) + artifacts,
        "repairs": state.get("repairs", []) + [record],
        "event": {"event_type": "REPAIR_APPLIED", "output": record},
    }
