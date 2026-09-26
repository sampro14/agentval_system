"""Scores what the coder produced against ground truth the agents never see (design doc §9.4).

Like the retrieval scorecard this runs after the fact, from the harness. The hidden acceptance test
is the independent verdict on the code; plan adherence shows whether the coder did what was planned.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agenteval.agents.coder.agent import plan_adherence
from agenteval.config import Settings
from agenteval.datasets import Task
from agenteval.evaluation.acceptance import run_acceptance
from agenteval.execution.docker.sandbox import Sandbox


async def score_draft(
    task: Task,
    workspace: Path,
    sandbox: Sandbox,
    settings: Settings,
    plan: dict[str, Any],
    artifacts: list[dict[str, Any]],
) -> dict[str, Any]:
    acceptance = await run_acceptance(task, workspace, sandbox, settings) or {}
    return {
        "kind": "draft",
        "acceptance_passed": bool(acceptance.get("acceptance_passed")),
        "acceptance_tests_passed": int(acceptance.get("acceptance_tests_passed", 0)),
        "acceptance_tests_total": int(acceptance.get("acceptance_tests_total", 0)),
        "files_written": [a["path"] for a in artifacts],
        **plan_adherence(plan, [a["path"] for a in artifacts]),
    }
