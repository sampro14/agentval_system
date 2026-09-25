"""Validator: decide whether the result satisfies the original requirements (design doc §8.5).

Deterministic checks come from execution evidence. The LLM requirements review runs only when that
evidence is clean, and can only turn a pass into a fail — it can never override a failing test.
"""

from __future__ import annotations

import json
from typing import Any

from agenteval.llm.provider import parse_json
from agenteval.orchestration.deps import Deps
from agenteval.orchestration.state import AgentEvalState

SYSTEM = """You are the validation agent in a software-engineering workflow.
Decide whether the implementation satisfies every requirement of the task. Derive the requirements
from the task and the plan's validation criteria. For each, judge from the diff and test names whether
it is implemented and whether a test exercises it. Be strict: unclear means false.
Respond with only a JSON object:
{"requirements": [{"requirement": str, "implemented": bool, "tested": bool, "evidence": str}],
 "issues": [str]}"""

MAX_DIFF_CHARS = 30_000


def regressions(executions: list[dict[str, Any]]) -> list[str]:
    """Tests that passed in an earlier execution of this run but fail in the latest one."""
    if len(executions) < 2 or not executions[-1].get("tests"):
        return []
    previously_passed = {t for e in executions[:-1] if e.get("tests") for t in e["tests"]["passed_tests"]}
    now_failing = {f["test"] for f in executions[-1]["tests"]["failures"]}
    return sorted(previously_passed & now_failing)


def latest_diffs(artifacts: list[dict[str, Any]]) -> str:
    latest = {a["path"]: a["diff"] for a in artifacts}  # later writes to the same path win
    return "\n".join(latest.values())[:MAX_DIFF_CHARS]


async def review_requirements(state: AgentEvalState, deps: Deps, tests: dict[str, Any]) -> dict[str, Any]:
    criteria = [
        {"task": t.get("description"), "validation_criteria": t.get("validation_criteria")}
        for t in state.get("plan", {}).get("tasks", [])
    ]
    prompt = (
        f"Task:\n{state['task']}\n\nPlan validation criteria:\n{json.dumps(criteria)}\n\n"
        f"Diff:\n{latest_diffs(state.get('artifacts', []))}\n\n"
        f"Passing tests:\n{json.dumps(tests.get('passed_tests', []))}"
    )
    review = parse_json((await deps.llm.complete(SYSTEM, prompt)).text)
    requirements = [r for r in review.get("requirements", []) if isinstance(r, dict) and "requirement" in r]
    return {"requirements": requirements, "issues": review.get("issues", [])}


async def validate(state: AgentEvalState, deps: Deps) -> dict[str, Any]:
    executions = state.get("execution_results", [])
    execution: dict[str, Any] = executions[-1] if executions else {"passed": False, "tests": None}
    tests: dict[str, Any] = execution.get("tests") or {}
    regressed = regressions(executions)

    checks = {
        "functional_correctness": bool(execution["passed"]),
        "test_presence": tests.get("total", 0) > 0,
        "regression_safety": not regressed,
    }
    review: dict[str, Any] = {"requirements": [], "issues": [], "skipped": True}
    if all(checks.values()):
        review = await review_requirements(state, deps, tests)
        reqs = review["requirements"]
        checks["requirement_coverage"] = bool(reqs) and all(r.get("implemented") for r in reqs)
        checks["test_coverage"] = bool(reqs) and all(r.get("tested") for r in reqs)

    passed = all(checks.values())
    result = {
        "passed": passed,
        "checks": checks,
        "failed_checks": [name for name, ok in checks.items() if not ok],
        "requirements": review["requirements"],
        "issues": review["issues"],
        "evidence": {
            "tests_total": tests.get("total", 0),
            "tests_passed": tests.get("passed", 0),
            "regressions": regressed,
            "requirements_review": "skipped: execution evidence already failing" if review.get("skipped") else "llm",
        },
    }
    return {
        "validation_results": state.get("validation_results", []) + [result],
        "event": {
            "event_type": "VALIDATION_PASSED" if passed else "VALIDATION_FAILED",
            "status": "ok" if passed else "failed",
            "output": result,
        },
    }
