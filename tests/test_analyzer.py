from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from agenteval.agents.failure_analyzer.agent import (
    SYSTEM,
    analyze_failure,
    coder_context,
    rule_candidates,
)
from agenteval.config import Settings
from agenteval.datasets import SAMPLE_APP
from agenteval.execution.workspace import seed_workspace
from agenteval.llm.provider import FakeProvider, ReplayProvider
from agenteval.orchestration.deps import Deps
from tests.conftest import PLAN, FakeSandbox

LLM_ANSWER = {
    "failure_type": "CODE_GENERATION",
    "root_cause": "wrong logic",
    "confidence": 0.9,
    "evidence": ["assert"],
    "recommended_action": "fix it",
}


def check(name: str, exit_code: int = 0, output: str = "", timed_out: bool = False) -> dict[str, Any]:
    return {
        "name": name,
        "exit_code": -1 if timed_out else exit_code,
        "stdout": output,
        "stderr": "",
        "timed_out": timed_out,
        "passed": exit_code == 0 and not timed_out,
        "duration_ms": 15_000 if timed_out else 10,
    }


def state_for(
    workspace: Path,
    checks: list[dict[str, Any]],
    failed_checks: list[str] | None = None,
    trajectory: list[dict[str, Any]] | None = None,
    tests: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "run_id": "r",
        "task": "add a feature",
        "workspace": str(workspace),
        "plan": PLAN,
        "artifacts": [{"path": "authkit/users.py", "diff": "+x"}],
        "execution_results": [{"checks": checks, "tests": tests, "passed": False}],
        "validation_results": [
            {"passed": False, "failed_checks": failed_checks or ["functional_correctness"], "evidence": {}}
        ],
        "trajectory": trajectory or [],
    }


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    seed_workspace(SAMPLE_APP, tmp_path)
    return tmp_path


def deps_for(llm: Any) -> Deps:
    return Deps(settings=Settings(), llm=llm, sandbox=FakeSandbox([0]))


# --- timeouts --------------------------------------------------------------------------------------


def test_a_test_timeout_after_a_passing_compile_is_the_codes_fault(workspace: Path) -> None:
    """The sandbox just ran the compile check fine, so it works; the tests hung."""
    state = state_for(workspace, [check("compile"), check("pytest", timed_out=True)])

    best = rule_candidates(state)[0]  # type: ignore[arg-type]

    assert (best.category, best.confidence) == ("CODE_GENERATION", 0.8)
    assert "compile passed" in best.evidence[0]


def test_a_timeout_where_the_sandbox_itself_is_not_working_stays_environment(workspace: Path) -> None:
    compile_hangs = state_for(workspace, [check("compile", timed_out=True)])
    assert rule_candidates(compile_hangs)[0].category == "ENVIRONMENT"  # type: ignore[arg-type]

    no_compile_check = state_for(workspace, [check("pytest", timed_out=True)])
    assert rule_candidates(no_compile_check)[0].category == "ENVIRONMENT"  # type: ignore[arg-type]


async def test_a_hang_is_settled_by_the_rule_without_asking_the_model(workspace: Path) -> None:
    llm = FakeProvider()
    state = state_for(workspace, [check("compile"), check("pytest", timed_out=True)])

    update = await analyze_failure(state, deps_for(llm))  # type: ignore[arg-type]

    failure = update["failures"][-1]
    assert (failure["category"], failure["stage"], failure["attribution_method"]) == (
        "CODE_GENERATION",
        "draft",
        "rule",
    )
    assert llm.calls == []


# --- no tests written ------------------------------------------------------------------------------


async def test_missing_tests_are_settled_by_the_rule(workspace: Path) -> None:
    llm = FakeProvider()
    state = state_for(workspace, [check("compile"), check("pytest")], failed_checks=["tests_written"])

    update = await analyze_failure(state, deps_for(llm))  # type: ignore[arg-type]

    failure = update["failures"][-1]
    assert failure["category"] == "CODE_GENERATION" and failure["attribution_method"] == "rule"
    assert failure["confidence"] == 0.9 and "tests" in failure["recommended_action"].lower()
    assert llm.calls == []


# --- what the coder was shown ----------------------------------------------------------------------


def research_event(files: list[str]) -> dict[str, Any]:
    return {"agent": "research", "event_type": "CONTEXT_GATHERED", "status": "ok", "output": {"files_selected": files}}


def test_coder_context_lists_what_was_shown_and_what_was_not(workspace: Path) -> None:
    state = state_for(workspace, [], trajectory=[research_event(["authkit/users.py", "tests/conftest.py"])])

    context = coder_context(state)  # type: ignore[arg-type]

    assert context["files_the_coder_was_shown"] == ["authkit/users.py", "tests/conftest.py"]
    not_shown = context["repository_files_not_shown"]
    assert "migrations/002_sessions.sql" in not_shown and "authkit/sessions.py" in not_shown
    assert "authkit/users.py" not in not_shown  # shown
    # files the coder itself wrote are not "missing context"
    state["artifacts"] = [{"path": "authkit/sessions.py", "diff": ""}]
    assert "authkit/sessions.py" not in coder_context(state)["repository_files_not_shown"]  # type: ignore[arg-type]


def test_coder_context_is_omitted_when_there_is_no_research_record(workspace: Path) -> None:
    assert coder_context(state_for(workspace, [])) == {}  # type: ignore[arg-type]
    other = [{"agent": "research", "event_type": "X", "status": "ok", "output": {}}]
    assert coder_context(state_for(workspace, [], trajectory=other)) == {}  # type: ignore[arg-type]


async def test_the_model_is_shown_what_the_coder_saw(workspace: Path) -> None:
    llm = FakeProvider(lambda s, p: json.dumps(LLM_ANSWER))
    state = state_for(
        workspace,
        [check("compile"), check("pytest", exit_code=1)],
        tests={"total": 1, "failed": 1, "errors": 0, "failures": [{"test": "t", "message": "no such column"}]},
        trajectory=[research_event(["authkit/users.py"])],
    )

    await analyze_failure(state, deps_for(llm))  # type: ignore[arg-type]

    prompt = llm.calls[0][1]
    assert '"files_the_coder_was_shown": ["authkit/users.py"]' in prompt
    assert "repository_files_not_shown" in prompt and "migrations/002_sessions.sql" in prompt
    assert '"files_written": ["authkit/users.py"]' in prompt


def test_the_prompt_defines_the_categories_and_warns_against_over_blaming_research() -> None:
    for category in ("PLANNING", "RETRIEVAL or CONTEXT", "CODE_GENERATION", "ENVIRONMENT", "REPAIR"):
        assert category in SYSTEM
    assert "ordinary" in SYSTEM and "logic bug is CODE_GENERATION" in SYSTEM


# --- robustness ------------------------------------------------------------------------------------


async def test_a_malformed_answer_is_retried_with_the_reason(workspace: Path) -> None:
    llm = ReplayProvider([{"response": '{"confidence": "very"}'}, {"response": json.dumps(LLM_ANSWER)}])
    state = state_for(
        workspace,
        [check("compile"), check("pytest", exit_code=1)],
        tests={"total": 1, "failed": 1, "errors": 0, "failures": []},
    )

    update = await analyze_failure(state, deps_for(llm))  # type: ignore[arg-type]

    assert update["failures"][-1]["root_cause"] == "wrong logic"
    assert "previous reply was rejected" in llm.calls[1][1]


async def test_an_answer_that_stays_malformed_fails_clearly(workspace: Path) -> None:
    llm = FakeProvider(lambda s, p: "I think it is the coder")
    state = state_for(workspace, [check("compile"), check("pytest", exit_code=1)])
    with pytest.raises(ValueError, match="failure analyzer output still invalid after 2 attempts"):
        await analyze_failure(state, deps_for(llm))  # type: ignore[arg-type]
