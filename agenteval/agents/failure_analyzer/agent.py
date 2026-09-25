"""Failure analyzer: attribute a failed validation to a category and the responsible agent (design doc §8.6, §11).

Deterministic rules over execution/validation evidence run first. When the best rule is not confident
(< LLM_THRESHOLD), an LLM refines the attribution using the evidence, plan and trajectory summary.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from agenteval.llm.provider import parse_json
from agenteval.orchestration.deps import Deps
from agenteval.orchestration.state import AgentEvalState

CATEGORIES = (
    "PLANNING",
    "RETRIEVAL",
    "CONTEXT",
    "TOOL_SELECTION",
    "TOOL_EXECUTION",
    "CODE_GENERATION",
    "VALIDATION",
    "ENVIRONMENT",
    "REPAIR",
    "UNKNOWN",
)
LLM_THRESHOLD = 0.8
SANDBOX_ERROR_CODES = {125, 126, 127}
SANDBOX_ERROR_MARKERS = ("No such image", "pull access denied", "OCI runtime", "executable file not found")
MISSING_MODULE = re.compile(r"No module named '([\w.]+)'")

SYSTEM = f"""You are the failure analyzer in a multi-agent software-engineering workflow.
Given the evidence of a failed validation, the plan and a summary of the agent trajectory, identify the
root cause and which stage is responsible. failure_type must be one of: {", ".join(CATEGORIES)}.
Respond with only a JSON object:
{{"failure_type": str, "root_cause": str, "confidence": number between 0 and 1,
  "evidence": [str], "recommended_action": str}}"""


@dataclass
class Attribution:
    category: str
    root_cause: str
    confidence: float
    evidence: list[str] = field(default_factory=list)
    recommended_action: str = ""


def _output(check: dict[str, Any]) -> str:
    return f"{check.get('stdout', '')}\n{check.get('stderr', '')}"


STOPWORDS = {"the", "and", "for", "with", "that", "this", "should", "must", "are", "from", "into", "when"}


def _stem(word: str) -> str:
    for suffix in ("ing", "ed", "es", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: -len(suffix)]
    return word


def _words(text: str) -> set[str]:
    return {_stem(w) for w in re.findall(r"[a-z]{3,}", text.lower()) if w not in STOPWORDS}


def _is_planned(requirement: str, plan: dict[str, Any]) -> bool:
    wanted = _words(requirement)
    if not wanted:
        return True
    planned = _words(
        " ".join(f"{t.get('description', '')} {t.get('expected_output', '')}" for t in plan.get("tasks", []))
    )
    return len(wanted & planned) / len(wanted) >= 0.5


def rule_candidates(state: AgentEvalState) -> list[Attribution]:
    execution = state["execution_results"][-1]
    validation = state["validation_results"][-1]
    checks = {c["name"]: c for c in execution["checks"]}
    tests = execution.get("tests") or {}
    candidates: list[Attribution] = []

    for check in execution["checks"]:
        output = _output(check)
        if check["exit_code"] in SANDBOX_ERROR_CODES or any(m in output for m in SANDBOX_ERROR_MARKERS):
            candidates.append(
                Attribution(
                    "ENVIRONMENT",
                    f"Sandbox could not run the '{check['name']}' check",
                    0.95,
                    [output[-500:]],
                    "Fix the sandbox image or runtime; the generated code was not exercised",
                )
            )
        if check["timed_out"]:
            candidates.append(
                Attribution(
                    "ENVIRONMENT",
                    f"'{check['name']}' timed out",
                    0.6,
                    [f"timeout after {check['duration_ms']} ms"],
                    "Check for hanging tests or infinite loops, or raise the sandbox timeout",
                )
            )

    compile_check = checks.get("compile")
    if compile_check and not compile_check["passed"] and not compile_check["timed_out"]:
        candidates.append(
            Attribution(
                "CODE_GENERATION",
                "Generated code does not compile",
                0.95,
                [_output(compile_check)[-800:]],
                "Fix the syntax errors reported by compileall",
            )
        )

    pytest_check = checks.get("pytest")
    if pytest_check:
        output = _output(pytest_check)
        if pytest_check["exit_code"] == 5 or (not tests.get("total") and pytest_check["exit_code"] in (0, 5)):
            candidates.append(
                Attribution(
                    "CODE_GENERATION",
                    "No tests were generated or collected",
                    0.9,
                    [output[-300:]],
                    "Add pytest tests in test_*.py files covering every requirement",
                )
            )
        for module in sorted(set(MISSING_MODULE.findall(output))):
            top = module.split(".")[0]
            workspace = Path(state["workspace"])
            if not (workspace / f"{top}.py").exists() and not (workspace / top).is_dir():
                candidates.append(
                    Attribution(
                        "CODE_GENERATION",
                        f"Code depends on unavailable module '{top}'",
                        0.8,
                        [f"No module named '{module}'"],
                        f"Remove the dependency on '{top}'; only the standard library and pytest are installed",
                    )
                )
        if tests.get("failed") or tests.get("errors"):
            candidates.append(
                Attribution(
                    "CODE_GENERATION",
                    f"{tests.get('failed', 0) + tests.get('errors', 0)} test(s) failed",
                    0.6,
                    [f"{f['test']}: {f['message']}" for f in tests.get("failures", [])[:5]],
                    "Fix the implementation (or incorrect tests) using the failing assertions",
                )
            )

    regressed = validation["evidence"].get("regressions", [])
    if regressed and state.get("repairs"):
        candidates.append(
            Attribution(
                "REPAIR",
                "Repair broke previously passing tests",
                0.85,
                regressed,
                "Revert the regressing change and make a narrower fix",
            )
        )

    for req in validation.get("requirements", []):
        if req.get("implemented") and req.get("tested"):
            continue
        gap = "not implemented" if not req.get("implemented") else "not tested"
        if _is_planned(req["requirement"], state.get("plan", {})):
            candidates.append(
                Attribution(
                    "CODE_GENERATION",
                    f"Planned requirement {gap}: {req['requirement']}",
                    0.7,
                    [req.get("evidence", "")],
                    f"Implement and test: {req['requirement']}",
                )
            )
        else:
            candidates.append(
                Attribution(
                    "PLANNING",
                    f"Requirement missing from the plan: {req['requirement']}",
                    0.7,
                    [req.get("evidence", "")],
                    f"Add a plan task for: {req['requirement']}",
                )
            )

    return sorted(candidates, key=lambda c: c.confidence, reverse=True)


def responsible_stage(category: str, repaired: bool) -> str:
    if category == "CODE_GENERATION":
        return "repair" if repaired else "draft"
    return {
        "PLANNING": "planner",
        "RETRIEVAL": "research",
        "CONTEXT": "research",
        "TOOL_SELECTION": "execute",
        "TOOL_EXECUTION": "execute",
        "ENVIRONMENT": "execute",
        "VALIDATION": "validate",
        "REPAIR": "repair",
    }.get(category, "unknown")


async def llm_attribution(state: AgentEvalState, deps: Deps, candidates: list[Attribution]) -> Attribution:
    execution = state["execution_results"][-1]
    evidence = {
        "checks": [
            {k: c[k] for k in ("name", "exit_code", "timed_out")} | {"output_tail": _output(c)[-1500:]}
            for c in execution["checks"]
        ],
        "tests": {k: v for k, v in (execution.get("tests") or {}).items() if k != "passed_tests"},
        "validation": state["validation_results"][-1],
        "rule_candidates": [asdict(c) for c in candidates],
    }
    trajectory = [f"{e['agent']}: {e['event_type']} ({e['status']})" for e in state.get("trajectory", [])]
    prompt = (
        f"Task:\n{state['task']}\n\nPlan:\n{json.dumps(state.get('plan', {}))}\n\n"
        f"Trajectory:\n{json.dumps(trajectory)}\n\nEvidence:\n{json.dumps(evidence)}"
    )
    result = parse_json((await deps.llm.complete(SYSTEM, prompt)).text)
    category = str(result.get("failure_type", "UNKNOWN")).upper()
    try:
        confidence = min(max(float(result.get("confidence", 0.5)), 0.0), 1.0)
    except (TypeError, ValueError):
        confidence = 0.5
    return Attribution(
        category=category if category in CATEGORIES else "UNKNOWN",
        root_cause=str(result.get("root_cause", "")),
        confidence=confidence,
        evidence=[str(e) for e in result.get("evidence", [])],
        recommended_action=str(result.get("recommended_action", "")),
    )


async def analyze_failure(state: AgentEvalState, deps: Deps) -> dict[str, Any]:
    candidates = rule_candidates(state)
    best = candidates[0] if candidates else None
    if best and best.confidence >= LLM_THRESHOLD:
        attribution, method = best, "rule"
    else:
        attribution, method = await llm_attribution(state, deps, candidates), "llm"

    failure = {
        "stage": responsible_stage(attribution.category, bool(state.get("repairs"))),
        "category": attribution.category,
        "root_cause": attribution.root_cause,
        "confidence": attribution.confidence,
        "evidence": attribution.evidence,
        "recommended_action": attribution.recommended_action,
        "attribution_method": method,
        "failed_checks": state["validation_results"][-1]["failed_checks"],
    }
    return {
        "failures": state.get("failures", []) + [failure],
        "event": {
            "event_type": "FAILURE_CLASSIFIED",
            "output": {**failure, "rule_candidates": [asdict(c) for c in candidates[:5]]},
        },
    }
