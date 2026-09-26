from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agenteval.agents.researcher import agent as research_module
from agenteval.agents.researcher.agent import plan_edited_files, plan_summary, research
from agenteval.config import Settings
from agenteval.datasets import SAMPLE_APP, get_task, load_tasks
from agenteval.evaluation.retrieval import score_retrieval
from agenteval.execution.workspace import seed_workspace
from agenteval.llm.provider import FakeProvider, ReplayProvider
from agenteval.orchestration.deps import Deps
from agenteval.storage.repositories.runs import RunRepository, TrajectoryRepository
from agenteval.workers.run_worker import execute_run
from tests.conftest import FakeSandbox, scripted_llm

PLAN = {
    "goal": "Add password reset",
    "tasks": [
        {"id": "T1", "description": "migration", "files_to_touch": [], "new_files": ["migrations/003_x.sql"]},
        {"id": "T2", "description": "logic", "files_to_touch": ["authkit/users.py"], "new_files": []},
        {"id": "T3", "description": "tests", "files_to_touch": ["tests/test_users.py", "authkit/users.py"]},
    ],
}


def state_for(workspace: Path, plan: dict[str, Any] | None = PLAN) -> dict[str, Any]:
    return {"run_id": "r", "task": "Add password reset", "workspace": str(workspace), "plan": plan or {}}


def deps_for(llm: Any) -> Deps:
    return Deps(settings=Settings(), llm=llm, sandbox=FakeSandbox([0]))


def choice(*paths: str) -> str:
    return json.dumps({"files": [{"path": p, "why": f"uses {p}"} for p in paths]})


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    seed_workspace(SAMPLE_APP, tmp_path)
    return tmp_path


# --- the two rules enforced in code ---------------------------------------------------------------


async def test_files_the_plan_edits_are_always_read_even_if_the_model_lists_nothing(workspace: Path) -> None:
    update = await research(state_for(workspace), deps_for(FakeProvider(lambda s, p: choice())))  # type: ignore[arg-type]

    output = update["event"]["output"]
    assert output["files_selected"] == ["authkit/users.py", "tests/test_users.py"]  # plan order, no duplicates
    assert output["auto_included"] == output["files_selected"] and output["llm_selected"] == []
    assert output["reasons"]["authkit/users.py"] == "the plan edits this file"
    by_path = {c["path"]: c for c in update["context"]}
    assert "def register(" in by_path["authkit/users.py"]["content"]  # the coder gets the real contents


async def test_empty_files_are_never_read(workspace: Path) -> None:
    llm = FakeProvider(lambda s, p: choice("authkit/__init__.py", "authkit/security.py"))
    update = await research(state_for(workspace), deps_for(llm))  # type: ignore[arg-type]

    output = update["event"]["output"]
    assert "authkit/__init__.py" not in output["files_selected"]
    assert output["empty_skipped"] == ["authkit/__init__.py"]
    assert "authkit/security.py" in output["llm_selected"]
    assert "authkit/__init__.py: (empty file)" in llm.calls[0][1]  # the model was told, too


# --- what the model adds ----------------------------------------------------------------------------


async def test_model_choices_come_after_the_plan_files_with_their_reasons(workspace: Path) -> None:
    llm = FakeProvider(lambda s, p: choice("authkit/security.py", "authkit/users.py", "migrations/001_users.sql"))
    update = await research(state_for(workspace), deps_for(llm))  # type: ignore[arg-type]

    output = update["event"]["output"]
    assert output["files_selected"] == [
        "authkit/users.py",
        "tests/test_users.py",
        "authkit/security.py",
        "migrations/001_users.sql",
    ]
    assert output["llm_selected"] == ["authkit/security.py", "migrations/001_users.sql"]
    assert output["reasons"]["authkit/security.py"] == "uses authkit/security.py"
    assert {c["why"] for c in update["context"]} >= {"the plan edits this file", "uses authkit/security.py"}
    assert not any(c.get("source") == "research_notes" for c in update["context"])  # no free-text noise


async def test_unknown_files_are_dropped_and_reported(workspace: Path) -> None:
    llm = FakeProvider(lambda s, p: choice("authkit/nope.py", "authkit/security.py"))
    update = await research(state_for(workspace), deps_for(llm))  # type: ignore[arg-type]

    output = update["event"]["output"]
    assert output["unknown_files"] == ["authkit/nope.py"] and "authkit/nope.py" not in output["files_selected"]


async def test_the_model_sees_the_repo_summary_and_the_plan_not_file_contents(workspace: Path) -> None:
    llm = FakeProvider(lambda s, p: choice())
    await research(state_for(workspace), deps_for(llm))  # type: ignore[arg-type]

    prompt = llm.calls[0][1]
    assert "hash_token(token)" in prompt and "migrations/002_sessions.sql: creates table sessions" in prompt
    assert "T2: logic (edits: authkit/users.py; creates: -)" in prompt
    assert "Already included automatically (the plan edits them): authkit/users.py, tests/test_users.py" in prompt
    assert "hashlib.pbkdf2_hmac" not in prompt  # contents are read by code, not sent for selection


# --- limits and problems ----------------------------------------------------------------------------


async def test_limits_drop_the_model_extras_before_plan_files(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(research_module, "MAX_CONTEXT_FILES", 2)
    llm = FakeProvider(lambda s, p: choice("authkit/security.py"))
    update = await research(state_for(workspace), deps_for(llm))  # type: ignore[arg-type]

    output = update["event"]["output"]
    assert output["files_selected"] == ["authkit/users.py", "tests/test_users.py"]
    assert output["dropped"] == ["authkit/security.py"]
    assert "authkit/security.py was dropped: context limit reached" in output["problems"]


async def test_size_limit(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(research_module, "MAX_CONTEXT_BYTES", 1500)
    update = await research(state_for(workspace), deps_for(FakeProvider(lambda s, p: choice())))  # type: ignore[arg-type]

    output = update["event"]["output"]
    assert output["bytes_read"] <= 1500 and output["dropped"]


async def test_a_plan_that_names_a_missing_file_is_a_problem_not_a_crash(workspace: Path) -> None:
    plan = {"goal": "g", "tasks": [{"id": "T1", "description": "d", "files_to_touch": ["authkit/ghost.py"]}]}
    update = await research(state_for(workspace, plan), deps_for(FakeProvider(lambda s, p: choice())))  # type: ignore[arg-type]

    assert update["event"]["output"]["problems"] == ["the plan edits authkit/ghost.py, which does not exist"]
    assert update["context"] == []


async def test_works_without_a_plan(workspace: Path) -> None:
    update = await research(state_for(workspace, None), deps_for(FakeProvider(lambda s, p: choice("authkit/users.py"))))  # type: ignore[arg-type]
    assert update["event"]["output"]["files_selected"] == ["authkit/users.py"]


# --- robustness -----------------------------------------------------------------------------------------


async def test_retries_once_when_the_reply_is_not_json(workspace: Path) -> None:
    llm = ReplayProvider([{"response": "I would read users.py"}, {"response": choice("authkit/security.py")}])
    update = await research(state_for(workspace), deps_for(llm))  # type: ignore[arg-type]

    assert update["event"]["output"]["attempts"] == 2
    assert "previous reply was rejected" in llm.calls[1][1]


async def test_fails_clearly_after_two_bad_replies(workspace: Path) -> None:
    llm = FakeProvider(lambda s, p: '{"files": "authkit/users.py"}')  # wrong type
    with pytest.raises(ValueError, match="research output still invalid after 2 attempts"):
        await research(state_for(workspace), deps_for(llm))  # type: ignore[arg-type]
    assert len(llm.calls) == 2


def test_plan_helpers() -> None:
    assert plan_edited_files(PLAN, ["authkit/users.py", "tests/test_users.py"]) == (
        ["authkit/users.py", "tests/test_users.py"],
        [],
    )
    assert plan_edited_files(PLAN, ["authkit/users.py"])[1] == ["tests/test_users.py"]
    assert plan_summary({}) == "(no plan)"


# --- scoring against the ground truth --------------------------------------------------------------------


def test_score_retrieval() -> None:
    task = get_task("password-reset")
    report = score_retrieval(task, ["authkit/users.py", "authkit/security.py", "authkit/mailer.py", "authkit/clock.py"])

    assert report.found == ["authkit/users.py", "authkit/security.py"]
    assert report.missed == ["migrations/001_users.sql"]
    assert report.helpful_hits == ["authkit/clock.py"]
    assert report.irrelevant == ["authkit/mailer.py"]
    assert report.recall == pytest.approx(2 / 3) and report.precision == pytest.approx(3 / 4)
    assert report.metrics() == {
        "retrieval_recall": pytest.approx(2 / 3),
        "retrieval_precision": 0.75,
        "retrieval_missed": 1.0,
        "retrieval_irrelevant": 1.0,
    }


def test_score_retrieval_edge_cases() -> None:
    task = get_task("change-password")
    assert score_retrieval(task, []).recall == 0.0 and score_retrieval(task, []).precision == 0.0
    assert score_retrieval(task, ["authkit/users.py"]).recall == 1.0


# --- the ground truth itself ------------------------------------------------------------------------------


def _imports(source: str) -> set[str]:
    modules = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
        elif isinstance(node, ast.Import):
            modules.update(a.name for a in node.names)
    return modules


@pytest.mark.parametrize("task", load_tasks(), ids=lambda t: t.id)
def test_needed_context_covers_what_the_reference_solution_touches(task: Any) -> None:
    """The curated lists must include everything a solution objectively has to read: the existing files it
    edits, and the modules it newly imports."""
    lower_bound: set[str] = set()
    for changed in task.reference_dir.rglob("*"):
        if not changed.is_file():
            continue
        relative = str(changed.relative_to(task.reference_dir))
        base = SAMPLE_APP / relative
        if not base.is_file():
            continue  # a new file: nothing to read
        lower_bound.add(relative)
        if relative.endswith(".py"):
            for module in _imports(changed.read_text()) - _imports(base.read_text()):
                candidate = module.replace(".", "/") + ".py"
                if (SAMPLE_APP / candidate).is_file():
                    lower_bound.add(candidate)

    assert lower_bound <= set(task.needed_context), sorted(lower_bound - set(task.needed_context))
    assert not set(task.needed_context) & set(task.helpful_context)
    assert task.needed_context, "every task needs at least one required file"


# --- stored after a real run -------------------------------------------------------------------------------


async def test_worker_stores_retrieval_metrics_for_dataset_runs(
    sessions: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    async with sessions() as session:
        run = await RunRepository(session).create(
            get_task("change-password").task,
            "software_engineering",
            {"task_id": "change-password", "seed": "sample_app", "max_repair_iterations": 0},
        )
    llm = scripted_llm(research={"files": [{"path": "authkit/users.py", "why": "edit"}, {"path": "authkit/mailer.py"}]})

    await execute_run(run.id, sessions, settings, llm=llm, sandbox=FakeSandbox([0, {"t": "passed"}]))

    async with sessions() as session:
        metrics = {e.metric: e.score for e in await TrajectoryRepository(session).evaluations(run.id)}
    assert metrics["retrieval_recall"] == 1.0
    assert metrics["retrieval_precision"] == 0.5  # users.py needed, mailer.py wasted
    assert (metrics["retrieval_missed"], metrics["retrieval_irrelevant"]) == (0.0, 1.0)
