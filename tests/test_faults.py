from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from agenteval.agents.planner.agent import Plan
from agenteval.config import Settings
from agenteval.datasets import DATASETS_ROOT, SAMPLE_APP, get_task
from agenteval.dev.faults import (
    Case,
    Outcome,
    advance,
    build_state,
    build_workspace,
    load_cases,
    load_plan,
    render_analysis_row,
    render_analysis_summary,
    render_row,
    render_summary,
    run_all,
    run_case,
    select,
    summarize,
    summarize_analysis,
)
from agenteval.execution.docker.sandbox import DockerSandbox, docker_available
from agenteval.llm.provider import FakeProvider
from agenteval.orchestration.deps import Deps
from tests.conftest import FakeSandbox, scripted_llm

CASES = load_cases()
BY_ID = {c.id: c for c in CASES}


# --- the dataset itself ----------------------------------------------------------------------------


def test_the_case_list_has_the_planned_shape() -> None:
    assert len(CASES) == 22 and len({c.id for c in CASES}) == 22
    assert sum(c.kind == "fault" for c in CASES) == 16 and sum(c.kind == "control" for c in CASES) == 6
    assert (sum(c.split == "dev" for c in CASES), sum(c.split == "heldout" for c in CASES)) == (14, 8)
    assert {c.task_id for c in CASES if c.kind == "control"} == {
        "change-password",
        "email-validation",
        "password-reset",
        "login-lockout",
        "delete-account",
        "login-form-validation",
    }


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.id)
def test_every_case_is_well_formed(case: Case, tmp_path: Path) -> None:
    Plan.model_validate(load_plan(case.plan))  # the gold plan is a valid plan
    assert case.overlay == case.overlay_dir.is_dir()
    build_workspace(case, tmp_path / "ws")  # raises if a file to remove does not exist

    expected = case.expected
    assert set(expected) - {"category_alternates"} == {
        "execution_passes",
        "detected",
        "failed_checks",
        "category",
        "stage",
    }
    if case.kind == "control":
        assert not case.overlay and not case.remove and not case.settings
        assert expected["detected"] is False and expected["category"] is None
    else:
        assert case.overlay or case.remove or case.settings, "a fault must change something"
        assert expected["detected"] is True and expected["category"] and expected["stage"]


def test_faults_are_single_changes_of_the_reference() -> None:
    """Every overlay file replaces a file the reference solution already has, so each fault is one edit."""
    for case in CASES:
        if not case.overlay:
            continue
        for path in case.overlay_dir.rglob("*"):
            if path.is_file():
                relative = path.relative_to(case.overlay_dir)
                assert (get_task(case.task_id).reference_dir / relative).is_file(), (case.id, relative)


def test_build_state_matches_what_the_coder_would_have_written(tmp_path: Path) -> None:
    state = build_state(BY_ID["pr-plaintext-token"], tmp_path)

    paths = {a["path"] for a in state["artifacts"]}
    assert paths == {"authkit/users.py", "migrations/003_password_reset_tokens.sql", "tests/test_password_reset.py"}
    users = (tmp_path / "workspace" / "authkit" / "users.py").read_text()
    assert '(user["id"], token, expires_at.isoformat())' in users  # the fault is in place
    assert state["task"] == get_task("password-reset").task
    assert [e["agent"] for e in state["trajectory"]] == ["planner", "research", "draft"]


def test_removals_and_plan_variants(tmp_path: Path) -> None:
    no_tests = build_state(BY_ID["da-no-tests"], tmp_path / "a")
    assert not (tmp_path / "a" / "workspace" / "tests" / "test_delete_account.py").exists()
    assert {a["path"] for a in no_tests["artifacts"]} == {"authkit/users.py"}

    no_migration = build_state(BY_ID["pr-plan-omits-migration"], tmp_path / "b")
    assert all("migrations/003" not in f for t in no_migration["plan"]["tasks"] for f in t["new_files"])
    assert not (tmp_path / "b" / "workspace" / "migrations" / "003_password_reset_tokens.sql").exists()


# --- running and scoring (fake sandbox and LLM) ----------------------------------------------------


async def run(case_id: str, sandbox: FakeSandbox, llm: Any, tmp_path: Path) -> Any:
    return await run_case(BY_ID[case_id], 1, llm=llm, sandbox_for=lambda s: sandbox, settings=Settings(), base=tmp_path)


async def test_a_control_is_accepted(tmp_path: Path) -> None:
    outcome = await run("control-change-password", FakeSandbox([0]), scripted_llm(), tmp_path)

    assert (outcome.execution_passed, outcome.detected, outcome.failed_checks) == (True, False, [])
    assert outcome.verdict_correct and outcome.stages_run == ["execute", "validate"] and outcome.tokens > 0


async def test_a_fault_that_breaks_the_tests_is_caught(tmp_path: Path) -> None:
    outcome = await run("cp-no-old-check", FakeSandbox([1]), scripted_llm(), tmp_path)

    assert outcome.detected and outcome.failed_checks == ["functional_correctness"] and outcome.verdict_correct
    assert outcome.transcript == []  # the LLM requirement review is skipped when the tests already fail


async def test_missing_tests_are_caught_by_evidence_without_asking_the_llm(tmp_path: Path) -> None:
    outcome = await run("da-no-tests", FakeSandbox([0]), scripted_llm(), tmp_path)

    assert outcome.execution_passed  # the code is fine and the old tests still pass
    assert outcome.failed_checks == ["tests_written"] and outcome.verdict_correct
    assert outcome.transcript == []  # the LLM review was never needed


async def test_a_fault_only_the_requirement_review_can_catch(tmp_path: Path) -> None:
    review = {"requirements": [{"requirement": "only a hash is stored", "implemented": False, "tested": True}]}
    outcome = await run("pr-plaintext-token", FakeSandbox([0]), scripted_llm(review=review), tmp_path)

    assert outcome.execution_passed and outcome.failed_checks == ["requirement_coverage"] and outcome.verdict_correct
    assert len(outcome.transcript) == 1  # this one needed the model


async def test_wrong_verdicts_are_scored_as_wrong(tmp_path: Path) -> None:
    missed = await run("pr-plaintext-token", FakeSandbox([0]), scripted_llm(), tmp_path / "a")  # review says fine
    assert missed.detected is False and not missed.verdict_correct

    review = {"requirements": [{"requirement": "x", "implemented": False, "tested": False}]}
    flagged = await run("control-change-password", FakeSandbox([0]), scripted_llm(review=review), tmp_path / "b")
    assert flagged.detected is True and not flagged.verdict_correct  # a false positive


async def test_a_harness_error_is_a_failed_case_not_a_crash(tmp_path: Path) -> None:
    def boom(settings: Settings) -> Any:
        raise RuntimeError("docker is down")

    outcome = await run_case(
        BY_ID["cp-no-old-check"], 1, llm=scripted_llm(), sandbox_for=boom, settings=Settings(), base=tmp_path
    )
    assert "docker is down" in (outcome.error or "") and not outcome.verdict_correct


async def test_case_settings_reach_the_sandbox(tmp_path: Path) -> None:
    seen: list[Settings] = []

    def factory(settings: Settings) -> FakeSandbox:
        seen.append(settings)
        return FakeSandbox([0])

    await run_case(
        BY_ID["sandbox-missing-image"], 1, llm=scripted_llm(), sandbox_for=factory, settings=Settings(), base=tmp_path
    )
    await run_case(
        BY_ID["timeout-infinite-loop"],
        1,
        llm=scripted_llm(),
        sandbox_for=factory,
        settings=Settings(),
        base=tmp_path / "t",
    )

    assert seen[0].sandbox_image == "agenteval-does-not-exist:latest" and seen[1].sandbox_timeout_s == 15


async def test_run_all_repeats_every_case_and_summarizes(tmp_path: Path) -> None:
    cases = [BY_ID["cp-no-old-check"], BY_ID["control-change-password"]]
    finished: list[str] = []
    outcomes = await run_all(
        cases,
        2,
        llm=scripted_llm(),
        sandbox_for=lambda s: FakeSandbox([1] if s is None else [0]),
        settings=Settings(),
        base=tmp_path,
        stages=("execute", "validate"),
        on_done=lambda o: finished.append(o.case.id),
    )

    assert len(outcomes) == 4 and len(finished) == 4
    summary = summarize(outcomes)
    assert (summary["faults"], summary["controls"], summary["runs"]) == (2, 2, 4)
    assert summary["false_positive_rate"] == 0.0
    assert summary["detection_rate"] == 0.0  # the fake sandbox passed for the fault too, so it was (wrongly) accepted
    assert "false positives on controls=0%" in render_summary(summary)
    assert "BAD cp-no-old-check" in render_row(next(o for o in outcomes if o.case.id == "cp-no-old-check"))


def test_select_by_split_and_id() -> None:
    assert len(select(CASES, "dev", [])) == 14 and len(select(CASES, "heldout", [])) == 8
    assert len(select(CASES, "all", [])) == 22
    assert [c.id for c in select(CASES, "all", ["da-no-tests"])] == ["da-no-tests"]


def test_summary_of_nothing() -> None:
    assert summarize([])["detection_rate"] is None


# --- the faults really break what their labels say (real sandbox) ----------------------------------


def _needs_browser(case: Case) -> bool:
    return case.task_id == "login-form-validation"


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.id)
async def test_execution_matches_the_label_in_the_real_sandbox(case: Case, tmp_path: Path) -> None:
    settings = Settings()
    import docker

    if not docker_available():
        pytest.skip("docker not available")
    needed = [settings.sandbox_image, *([settings.sandbox_browser_image] if _needs_browser(case) else [])]
    for image in needed:
        try:
            docker.from_env().images.get(image)
        except docker.errors.ImageNotFound:
            pytest.skip(f"image {image} not built")

    state = build_state(case, tmp_path)
    case_settings = settings.model_copy(update=case.settings)
    deps = Deps(settings=case_settings, llm=FakeProvider(), sandbox=DockerSandbox(case_settings))

    await advance(state, "execute", deps)

    assert state["execution_results"][-1]["passed"] is case.expected["execution_passes"]
    assert (SAMPLE_APP / "authkit" / "users.py").is_file()  # the untouched sample app was not modified
    assert DATASETS_ROOT.is_dir()


# --- the failure analyzer under test ---------------------------------------------------------------


ANALYSIS = {
    "failure_type": "CODE_GENERATION",
    "root_cause": "the lockout limit is wrong",
    "confidence": 0.9,
    "evidence": ["assert"],
    "recommended_action": "fix it",
}


async def analyze(
    case_id: str, sandbox: FakeSandbox, tmp_path: Path, analysis: dict[str, Any] | None = None
) -> Outcome:
    return await run_case(
        BY_ID[case_id],
        1,
        llm=scripted_llm(analysis=analysis or ANALYSIS),
        sandbox_for=lambda s: sandbox,
        settings=Settings(),
        base=tmp_path,
        stages=("execute", "validate", "analyze_failure"),
    )


async def test_a_rejected_fault_is_analyzed_and_scored(tmp_path: Path) -> None:
    outcome = await analyze("ll-four-tries", FakeSandbox([1]), tmp_path)

    assert outcome.analyzed and (outcome.category, outcome.stage) == ("CODE_GENERATION", "draft")
    assert outcome.method == "llm" and outcome.confidence == 0.9 and outcome.analysis_correct
    assert "analyze_failure" in outcome.stages_run
    assert render_analysis_row(outcome).startswith("ok ")


async def test_controls_and_accepted_solutions_are_not_analyzed(tmp_path: Path) -> None:
    control = await analyze("control-change-password", FakeSandbox([0]), tmp_path / "a")
    assert not control.analyzed and "analyze_failure" not in control.stages_run

    accepted_fault = await analyze("pr-plaintext-token", FakeSandbox([0]), tmp_path / "b")  # validator wrongly accepts
    assert not accepted_fault.analyzed and not accepted_fault.analysis_correct


async def test_alternate_categories_are_accepted(tmp_path: Path) -> None:
    right = await analyze(
        "da-schema-context-missed", FakeSandbox([1]), tmp_path / "a", {**ANALYSIS, "failure_type": "CONTEXT"}
    )
    assert right.category == "CONTEXT" and right.category_correct and right.stage == "research"

    wrong = await analyze("da-schema-context-missed", FakeSandbox([1]), tmp_path / "b")
    assert not wrong.category_correct and not wrong.analysis_correct


def test_recorded_research_files_reach_the_state(tmp_path: Path) -> None:
    state = build_state(BY_ID["da-schema-context-missed"], tmp_path)
    research = next(e for e in state["trajectory"] if e["agent"] == "research")
    assert research["output"]["files_selected"] == ["authkit/users.py", "tests/test_users.py", "tests/conftest.py"]
    assert "owner_id" in (tmp_path / "workspace" / "authkit" / "users.py").read_text()  # the guessed column


async def test_a_repair_history_makes_a_regression_visible(tmp_path: Path) -> None:
    sandbox = FakeSandbox([{"t1": "passed"}, {"t1": "failed"}])  # the good run first, then the broken one
    outcome = await analyze("rp-regression-after-repair", sandbox, tmp_path, {**ANALYSIS, "failure_type": "REPAIR"})

    state = outcome.state
    assert len(state["execution_results"]) == 2 and len(state["repairs"]) == 1
    assert [e["agent"] for e in state["trajectory"]][-3:] != []
    assert set(outcome.failed_checks) == {"functional_correctness", "regression_safety"}
    assert (outcome.category, outcome.stage) == ("REPAIR", "repair") and outcome.analysis_correct


def test_summarize_analysis() -> None:
    def outcome(case_id: str, category: str, stage: str, method: str, confidence: float) -> Outcome:
        o = Outcome(BY_ID[case_id], 1)
        o.category, o.stage, o.method, o.confidence = category, stage, method, confidence
        return o

    outcomes = [
        outcome("ll-four-tries", "CODE_GENERATION", "draft", "llm", 0.95),
        outcome("cp-no-old-check", "CODE_GENERATION", "draft", "rule", 0.95),
        outcome("timeout-infinite-loop", "ENVIRONMENT", "execute", "llm", 0.8),
        Outcome(BY_ID["pr-plaintext-token"], 1),  # never analyzed
    ]

    summary = summarize_analysis(outcomes)

    assert (summary["faults"], summary["not_analyzed"]) == (4, 1)
    assert summary["both_correct"] == pytest.approx(2 / 3)
    assert summary["by_method"] == {"llm": {"runs": 2, "correct": 1}, "rule": {"runs": 1, "correct": 1}}
    assert summary["confusion"]["CODE_GENERATION/draft -> ENVIRONMENT/execute"] == 1
    assert summary["confidence_when_right"] == pytest.approx(0.95) and summary["confidence_when_wrong"] == 0.8
    assert "both=67%" in render_analysis_summary(summary)
