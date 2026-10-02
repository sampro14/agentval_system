"""Validator: decide whether the result satisfies the original requirements (design doc §8.5).

Deterministic checks come from execution evidence and from what was written. The LLM requirements
review runs only when that evidence is clean, and can only turn a pass into a fail: it can never
override a failing test.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from agenteval.agents.common import complete_json
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


class Requirement(BaseModel):
    requirement: str
    implemented: bool = False
    tested: bool = False
    evidence: str = ""


class Review(BaseModel):
    requirements: list[Requirement] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)


def is_test_file(path: str) -> bool:
    name = Path(path).name
    return name.endswith(".py") and (name.startswith("test_") or name.endswith("_test.py"))


def tests_written(artifacts: list[dict[str, Any]]) -> list[str]:
    """The test files this run wrote. The total test count can't tell: it includes the repository's old tests."""
    return sorted({a["path"] for a in artifacts if is_test_file(a["path"])})


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


async def review_requirements(state: AgentEvalState, deps: Deps, tests: dict[str, Any]) -> Review:
    criteria = [
        {"task": t.get("description"), "validation_criteria": t.get("validation_criteria")}
        for t in state.get("plan", {}).get("tasks", [])
    ]
    prompt = (
        f"Task:\n{state['task']}\n\nPlan validation criteria:\n{json.dumps(criteria)}\n\n"
        f"Diff:\n{latest_diffs(state.get('artifacts', []))}\n\n"
        f"Passing tests:\n{json.dumps(tests.get('passed_tests', []))}"
    )
    review, _attempts = await complete_json(deps.llm, SYSTEM, prompt, Review.model_validate, who="validator")
    return review


async def validate(state: AgentEvalState, deps: Deps) -> dict[str, Any]:
    executions = state.get("execution_results", [])
    execution: dict[str, Any] = executions[-1] if executions else {"passed": False, "tests": None}
    tests: dict[str, Any] = execution.get("tests") or {}
    regressed = regressions(executions)
    new_tests = tests_written(state.get("artifacts", []))

    checks = {
        "functional_correctness": bool(execution["passed"]),
        "test_presence": tests.get("total", 0) > 0,
        "tests_written": bool(new_tests),
        "regression_safety": not regressed,
    }
    requirements: list[dict[str, Any]] = []
    issues: list[str] = []
    reviewed = False
    if all(checks.values()):
        review = await review_requirements(state, deps, tests)
        reviewed = True
        requirements = [r.model_dump() for r in review.requirements]
        issues = review.issues
        checks["requirement_coverage"] = bool(requirements) and all(r["implemented"] for r in requirements)
        checks["test_coverage"] = bool(requirements) and all(r["tested"] for r in requirements)

    passed = all(checks.values())
    result = {
        "passed": passed,
        "checks": checks,
        "failed_checks": [name for name, ok in checks.items() if not ok],
        "requirements": requirements,
        "issues": issues,
        "evidence": {
            "tests_total": tests.get("total", 0),
            "tests_passed": tests.get("passed", 0),
            "tests_written": new_tests,
            "regressions": regressed,
            "requirements_review": "llm" if reviewed else "skipped: the evidence checks already fail",
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
