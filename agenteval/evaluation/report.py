"""Run-level metrics. Stage-level evaluators arrive in Phase 3 (design doc §9)."""

from __future__ import annotations

from typing import Any

from agenteval.orchestration.deps import Deps
from agenteval.orchestration.policies import last_validation_passed
from agenteval.orchestration.state import AgentEvalState


def compute_metrics(state: AgentEvalState) -> dict[str, Any]:
    events = state.get("trajectory", [])
    validations = state.get("validation_results", [])
    executions = state.get("execution_results", [])
    tests = (executions[-1].get("tests") if executions else None) or {}
    passed = last_validation_passed(state)
    first_pass = bool(validations) and validations[0]["passed"]
    input_tokens = sum(e["token_usage"]["input"] for e in events)
    output_tokens = sum(e["token_usage"]["output"] for e in events)
    return {
        "task_success": passed,
        "first_pass_success": first_pass,
        "recovered": passed and not first_pass,
        "repair_iterations": len(state.get("repairs", [])),
        "failures": len(state.get("failures", [])),
        "tests_total": tests.get("total", 0),
        "tests_passed": tests.get("passed", 0),
        "llm_calls": sum(e["token_usage"]["llm_calls"] for e in events),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
        "total_latency_ms": sum(e["latency_ms"] for e in events),
    }


def failure_categories(state: AgentEvalState) -> list[str]:
    return [f["category"] for f in state.get("failures", [])]


async def evaluate(state: AgentEvalState, deps: Deps) -> dict[str, Any]:
    metrics = compute_metrics(state)
    if not metrics["task_success"]:
        status = "failed"
    elif metrics["recovered"]:
        status = "recovered"
    else:
        status = "succeeded"
    output = {**metrics, "failure_categories": failure_categories(state)}
    return {"metrics": metrics, "status": status, "event": {"event_type": "RUN_EVALUATED", "output": output}}
