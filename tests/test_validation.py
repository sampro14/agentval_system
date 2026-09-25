from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agenteval.agents.failure_analyzer.agent import analyze_failure, rule_candidates
from agenteval.agents.validator.agent import regressions
from agenteval.config import Settings
from agenteval.execution.checks import plan_checks
from agenteval.execution.docker.sandbox import ExecutionResult
from agenteval.execution.reports import parse_junit
from agenteval.llm.provider import FakeProvider
from agenteval.orchestration.deps import Deps
from agenteval.storage.repositories.runs import RunRepository, TrajectoryRepository
from agenteval.workers.run_worker import execute_run
from tests.conftest import PLAN, FakeSandbox, junit_xml, scripted_llm

SandboxFactory = Callable[[list[int | dict[str, str]]], FakeSandbox]


# --- execution evidence -------------------------------------------------------------------------


def test_parse_junit(tmp_path: Path) -> None:
    report = tmp_path / "r.xml"
    report.write_text(junit_xml({"test_a": "passed", "test_b": "failed"}))

    summary = parse_junit(report)

    assert summary is not None
    assert (summary["total"], summary["passed"], summary["failed"]) == (2, 1, 1)
    assert summary["passed_tests"] == ["test_calc::test_a"]
    assert summary["failures"][0]["message"] == "assert 1 == 5"
    assert parse_junit(tmp_path / "missing.xml") is None


def test_plan_checks_selects_browser_image(tmp_path: Path) -> None:
    settings = Settings()
    (tmp_path / "test_unit.py").write_text("def test_x(): pass\n")
    assert [c.image for c in plan_checks(tmp_path, settings)] == [settings.sandbox_image] * 2

    (tmp_path / "test_ui.py").write_text("from playwright.sync_api import Page\n")
    assert plan_checks(tmp_path, settings)[1].image == settings.sandbox_browser_image


def test_regressions_only_counts_previously_passing_tests() -> None:
    def execution(passed: list[str], failed: list[str]) -> dict[str, Any]:
        return {"tests": {"passed_tests": passed, "failures": [{"test": t} for t in failed]}}

    history = [execution(["a", "b"], ["c"]), execution(["a"], ["b", "c"])]
    assert regressions(history) == ["b"]


# --- failure attribution rules ------------------------------------------------------------------


def _state(
    tmp_path: Path,
    checks: list[dict[str, Any]],
    tests: dict[str, Any] | None = None,
    validation: dict[str, Any] | None = None,
    repairs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "run_id": "r",
        "task": "add an add() function",
        "workspace": str(tmp_path),
        "plan": PLAN,
        "execution_results": [{"checks": checks, "tests": tests, "passed": False}],
        "validation_results": [
            validation or {"passed": False, "failed_checks": ["functional_correctness"], "evidence": {}}
        ],
        "repairs": repairs or [],
        "trajectory": [],
    }


def _check(name: str, exit_code: int, output: str = "", timed_out: bool = False) -> dict[str, Any]:
    return {
        "name": name,
        "exit_code": exit_code,
        "stdout": output,
        "stderr": "",
        "timed_out": timed_out,
        "passed": exit_code == 0 and not timed_out,
        "duration_ms": 10,
    }


def test_rule_sandbox_error_is_environment(tmp_path: Path) -> None:
    state = _state(tmp_path, [_check("compile", 125, "No such image: agenteval-sandbox")])
    assert rule_candidates(state)[0].category == "ENVIRONMENT"  # type: ignore[arg-type]


def test_rule_compile_failure_is_code_generation(tmp_path: Path) -> None:
    best = rule_candidates(_state(tmp_path, [_check("compile", 1, "SyntaxError")]))[0]  # type: ignore[arg-type]
    assert (best.category, best.confidence) == ("CODE_GENERATION", 0.95)


def test_rule_no_tests(tmp_path: Path) -> None:
    state = _state(tmp_path, [_check("compile", 0), _check("pytest", 5, "no tests ran")])
    assert rule_candidates(state)[0].root_cause == "No tests were generated or collected"  # type: ignore[arg-type]


def test_rule_unavailable_dependency(tmp_path: Path) -> None:
    (tmp_path / "calc.py").write_text("")
    output = "ModuleNotFoundError: No module named 'requests'\nNo module named 'calc.sub'"
    state = _state(tmp_path, [_check("compile", 0), _check("pytest", 2, output)])

    causes = [c.root_cause for c in rule_candidates(state)]  # type: ignore[arg-type]
    assert "Code depends on unavailable module 'requests'" in causes
    assert not any("'calc'" in c for c in causes)  # module exists in the workspace


def test_rule_regression_after_repair(tmp_path: Path) -> None:
    validation = {"passed": False, "failed_checks": ["regression_safety"], "evidence": {"regressions": ["t::a"]}}
    state = _state(tmp_path, [_check("compile", 0), _check("pytest", 0)], {"total": 1}, validation, [{"iteration": 1}])
    assert rule_candidates(state)[0].category == "REPAIR"  # type: ignore[arg-type]


def test_rule_unplanned_requirement_is_planning(tmp_path: Path) -> None:
    validation = {
        "passed": False,
        "failed_checks": ["requirement_coverage"],
        "evidence": {},
        "requirements": [
            {"requirement": "subtract() returns difference", "implemented": False, "tested": False},
            {"requirement": "add() returns the sum", "implemented": True, "tested": False},
        ],
    }
    state = _state(tmp_path, [_check("compile", 0), _check("pytest", 0)], {"total": 1}, validation)
    categories = {c.root_cause.split(":")[0]: c.category for c in rule_candidates(state)}  # type: ignore[arg-type]
    assert categories == {
        "Requirement missing from the plan": "PLANNING",
        "Planned requirement not tested": "CODE_GENERATION",
    }


async def test_low_confidence_uses_llm_and_clamps_category(tmp_path: Path) -> None:
    state = _state(tmp_path, [_check("compile", 0), _check("pytest", 1)], {"total": 1, "failed": 1, "failures": []})
    llm = scripted_llm(analysis={"failure_type": "MADE_UP", "root_cause": "?", "confidence": 3})
    deps = Deps(settings=Settings(), llm=llm, sandbox=FakeSandbox([0]))

    failure = (await analyze_failure(state, deps))["failures"][-1]  # type: ignore[arg-type]

    assert failure["attribution_method"] == "llm"
    assert (failure["category"], failure["confidence"], failure["stage"]) == ("UNKNOWN", 1.0, "unknown")


async def test_confident_rule_skips_llm(tmp_path: Path) -> None:
    state = _state(tmp_path, [_check("compile", 1, "SyntaxError")])
    llm = FakeProvider()
    deps = Deps(settings=Settings(), llm=llm, sandbox=FakeSandbox([0]))

    failure = (await analyze_failure(state, deps))["failures"][-1]  # type: ignore[arg-type]

    assert failure["attribution_method"] == "rule" and failure["stage"] == "draft"
    assert llm.calls == []


# --- validator + workflow -----------------------------------------------------------------------


async def _run(
    sessions: async_sessionmaker[AsyncSession],
    settings: Settings,
    sandbox: FakeSandbox,
    llm: FakeProvider,
    max_repairs: int = 3,
) -> tuple[str, str]:
    async with sessions() as session:
        run = await RunRepository(session).create("add", "software_engineering", {"max_repair_iterations": max_repairs})
    return run.id, await execute_run(run.id, sessions, settings, llm=llm, sandbox=sandbox)


async def _events(sessions: async_sessionmaker[AsyncSession], run_id: str, agent: str) -> list[dict[str, Any]]:
    async with sessions() as session:
        return [e.output for e in await TrajectoryRepository(session).events(run_id) if e.agent_name == agent]


async def test_llm_review_cannot_override_failing_tests(
    sessions: async_sessionmaker[AsyncSession], settings: Settings, sandbox_factory: SandboxFactory
) -> None:
    llm = scripted_llm()
    run_id, status = await _run(sessions, settings, sandbox_factory([1]), llm, max_repairs=0)

    assert status == "failed"
    (validation,) = await _events(sessions, run_id, "validate")
    assert validation["failed_checks"] == ["functional_correctness"]
    assert validation["evidence"]["requirements_review"].startswith("skipped")
    assert not any("validation agent" in system for system, _ in llm.calls)


async def test_requirement_gap_fails_passing_tests(
    sessions: async_sessionmaker[AsyncSession], settings: Settings, sandbox_factory: SandboxFactory
) -> None:
    review = {"requirements": [{"requirement": "add() returns the sum", "implemented": True, "tested": False}]}
    run_id, status = await _run(sessions, settings, sandbox_factory([0]), scripted_llm(review=review), max_repairs=0)

    assert status == "failed"
    (validation,) = await _events(sessions, run_id, "validate")
    assert validation["failed_checks"] == ["test_coverage"]
    async with sessions() as session:
        (failure,) = await TrajectoryRepository(session).failures(run_id)
    assert (failure.category, failure.stage) == ("CODE_GENERATION", "draft")


async def test_regression_after_repair_is_attributed_to_repair(
    sessions: async_sessionmaker[AsyncSession], settings: Settings, sandbox_factory: SandboxFactory
) -> None:
    sandbox = sandbox_factory([{"test_a": "passed", "test_b": "failed"}, {"test_a": "failed", "test_b": "passed"}])
    run_id, status = await _run(sessions, settings, sandbox, scripted_llm(), max_repairs=1)

    assert status == "failed"
    async with sessions() as session:
        failures = await TrajectoryRepository(session).failures(run_id)
    assert [(f.category, f.stage, f.attribution_method) for f in failures] == [
        ("CODE_GENERATION", "draft", "llm"),
        ("REPAIR", "repair", "rule"),
    ]


async def test_execution_event_carries_per_test_evidence(
    sessions: async_sessionmaker[AsyncSession], settings: Settings, sandbox_factory: SandboxFactory
) -> None:
    run_id, status = await _run(sessions, settings, sandbox_factory([0]), scripted_llm())

    assert status == "succeeded"
    (execution,) = await _events(sessions, run_id, "execute")
    assert [c["name"] for c in execution["checks"]] == ["compile", "pytest"]
    assert execution["tests"]["total"] == 1 and execution["passed"]


async def test_sandbox_error_is_recorded_as_evidence(
    sessions: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    class BrokenSandbox(FakeSandbox):
        async def run(self, workspace: str, command: str, image: str | None = None) -> ExecutionResult:
            return ExecutionResult(command, 125, "", "sandbox error: No such image", 1)

    run_id, status = await _run(sessions, settings, BrokenSandbox([0]), scripted_llm(), max_repairs=0)

    assert status == "failed"
    async with sessions() as session:
        (failure,) = await TrajectoryRepository(session).failures(run_id)
    assert (failure.category, failure.stage, failure.attribution_method) == ("ENVIRONMENT", "execute", "rule")
