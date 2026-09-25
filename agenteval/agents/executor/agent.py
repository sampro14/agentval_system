"""Execution agent: run artifacts in the sandbox; results are evidence, not claims (design doc §8.4)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agenteval.execution.checks import JUNIT_REPORT, plan_checks, reset_reports
from agenteval.execution.reports import parse_junit
from agenteval.orchestration.deps import Deps
from agenteval.orchestration.state import AgentEvalState

OUTPUT_TAIL = 4000


async def execute(state: AgentEvalState, deps: Deps) -> dict[str, Any]:
    workspace = Path(state["workspace"])
    reset_reports(workspace)
    checks: list[dict[str, Any]] = []
    for check in plan_checks(workspace, deps.settings):
        result = await deps.sandbox.run(str(workspace), check.command, image=check.image)
        checks.append(
            {
                "name": check.name,
                "image": check.image,
                "required": check.required,
                **result.to_dict(),
                "stdout": result.stdout[-OUTPUT_TAIL:],
                "stderr": result.stderr[-OUTPUT_TAIL:],
            }
        )
        if check.name == "compile" and not result.passed:
            break  # a syntax error makes every later check meaningless

    tests = parse_junit(workspace / JUNIT_REPORT)
    record = {
        "checks": checks,
        "tests": tests,
        "passed": all(c["passed"] for c in checks if c["required"]),
    }
    return {
        "execution_results": state.get("execution_results", []) + [record],
        "event": {
            "event_type": "EXECUTION_PASSED" if record["passed"] else "EXECUTION_FAILED",
            "status": "ok" if record["passed"] else "failed",
            "output": {
                "checks": checks,
                "tests": {k: v for k, v in tests.items() if k != "passed_tests"} if tests else None,
                "passed": record["passed"],
            },
        },
    }
