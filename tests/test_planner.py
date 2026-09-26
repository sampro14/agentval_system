from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from agenteval.agents.planner.agent import (
    Plan,
    PlanTask,
    find_cycle,
    plan,
    quality_issues,
    structural_issues,
)
from agenteval.agents.repo import python_symbols, summarize_repo
from agenteval.config import Settings
from agenteval.datasets import SAMPLE_APP, get_task
from agenteval.execution.workspace import list_files, seed_workspace
from agenteval.llm.provider import FakeProvider, ReplayProvider, parse_json
from agenteval.orchestration.deps import Deps
from tests.conftest import FakeSandbox

REPO = [
    "authkit/users.py",
    "migrations/001_users.sql",
    "migrations/002_sessions.sql",
    "tests/test_users.py",
]


def task(
    id: str,
    deps: list[str] | None = None,
    touch: list[str] | None = None,
    new: list[str] | None = None,
    criteria: str = "ok",
) -> PlanTask:
    return PlanTask(
        id=id,
        description=f"do {id}",
        dependencies=deps or [],
        files_to_touch=touch or [],
        new_files=new or [],
        validation_criteria=criteria,
    )


def make_plan(*tasks: PlanTask) -> Plan:
    return Plan(goal="g", tasks=list(tasks))


# --- parse_json ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        '{"a": 1}',
        '```json\n{"a": 1}\n```',
        '```\n{"a": 1}\n```',
        'Sure! Here is the plan:\n{"a": 1}\nHope that helps.',
        'Note {not json} first. {"a": 1}',
        '```json\n{"a": 1}\n``` and some closing words',
    ],
)
def test_parse_json_tolerates_fences_and_prose(text: str) -> None:
    assert parse_json(text) == {"a": 1}


@pytest.mark.parametrize("text", ["no json here", '{"a": ', "[1, 2, 3]", ""])
def test_parse_json_rejects_non_objects(text: str) -> None:
    with pytest.raises(json.JSONDecodeError):
        parse_json(text)


# --- structural checks (these trigger a retry) ----------------------------------------------------


def test_structural_issues() -> None:
    assert structural_issues(make_plan(task("T1"), task("T2", ["T1"]))) == []
    assert structural_issues(make_plan(task("T1"), task("T1"))) == ["duplicate task id T1"]
    assert structural_issues(make_plan(task("T1", ["T9"]))) == ["T1 depends on unknown task T9"]
    assert "dependency cycle T1 -> T2 -> T1" in structural_issues(make_plan(task("T1", ["T2"]), task("T2", ["T1"])))
    assert "dependency cycle T1 -> T1" in structural_issues(make_plan(task("T1", ["T1"])))


def test_find_cycle_ignores_acyclic_diamonds() -> None:
    diamond = [task("A"), task("B", ["A"]), task("C", ["A"]), task("D", ["B", "C"])]
    assert find_cycle(diamond) is None


# --- quality checks (recorded, not retried) -------------------------------------------------------

TEXT = "Add password reset with a migration and tests."


def good_plan() -> Plan:
    return make_plan(
        task("T1", touch=[], new=["migrations/003_reset_tokens.sql"]),
        task("T2", ["T1"], touch=["authkit/users.py"]),
        task("T3", ["T2"], touch=["tests/test_users.py"]),
    )


def test_a_good_plan_has_no_issues() -> None:
    assert quality_issues(good_plan(), REPO, TEXT) == []


def test_quality_flags_bad_files_and_padding_tasks() -> None:
    bad = make_plan(
        task("T1", touch=["authkit/missing.py"]),
        task("T2", new=["authkit/users.py"], criteria=" "),
        task("T3"),
    )
    issues = quality_issues(bad, REPO, "Refactor")
    assert "T1 touches authkit/missing.py, which does not exist" in issues
    assert "T2 creates authkit/users.py, which already exists" in issues
    assert "T2 has no validation_criteria" in issues
    assert any(i.startswith("T3 changes no files") for i in issues)


def test_quality_flags_task_count_and_unsafe_paths() -> None:
    assert any("task count 1" in i for i in quality_issues(make_plan(task("T1", touch=["authkit/users.py"])), REPO, ""))
    unsafe = make_plan(task("T1", touch=["../x.py"]), task("T2", new=["/etc/passwd"]))
    assert sum("unsafe path" in i for i in quality_issues(unsafe, REPO, "")) == 2


def test_quality_migration_rules() -> None:
    no_sql = make_plan(task("T1", touch=["authkit/users.py"]), task("T2", touch=["tests/test_users.py"]))
    assert any("adds no new migrations" in i for i in quality_issues(no_sql, REPO, TEXT))

    stale_number = make_plan(task("T1", new=["migrations/002_again.sql"]), task("T2", touch=["tests/test_users.py"]))
    assert any("not numbered after the existing 002" in i for i in quality_issues(stale_number, REPO, TEXT))

    unnumbered = make_plan(task("T1", new=["migrations/reset.sql"]), task("T2", touch=["tests/test_users.py"]))
    assert any("not numbered" in i for i in quality_issues(unnumbered, REPO, TEXT))


def test_quality_flags_missing_test_file() -> None:
    plan_without_tests = make_plan(task("T1", touch=["authkit/users.py"]), task("T2", touch=["authkit/users.py"]))
    assert any("names no test file" in i for i in quality_issues(plan_without_tests, REPO, "Add tests"))


# --- what the planner is shown --------------------------------------------------------------------


def test_python_symbols() -> None:
    source = "\n".join(
        [
            "MIN = 1",
            "lower = 2",
            "class A(Base):",
            "    def m(self): ...",
            "class B: ...",
            "async def f(x, y): ...",
            "def g(): ...",
        ]
    )
    assert python_symbols(source) == ["MIN", "class A(Base)", "class B", "f(x, y)", "g()"]
    assert python_symbols("def broken(:") == []


def test_repo_summary_of_the_real_sample_app(tmp_path: Path) -> None:
    seed_workspace(SAMPLE_APP, tmp_path)
    summary = summarize_repo(tmp_path)

    assert "- authkit/users.py: MIN_PASSWORD_LENGTH; class UserError(Exception)" in summary
    assert "register(conn, email, password)" in summary
    assert "authenticate(conn, email, password)" in summary
    assert "hash_token(token)" in summary  # the helper a password-reset solution should reuse
    assert "- migrations/002_sessions.sql: creates table sessions" in summary
    assert "element ids #login-form, #email, #password, #error, #submit" in summary
    assert "tests/test_users.py: 6 tests" in summary


def test_repo_summary_of_an_empty_workspace(tmp_path: Path) -> None:
    assert summarize_repo(tmp_path) == "(empty repository)"


# --- the agent -------------------------------------------------------------------------------------


def state_for(workspace: Path, task_id: str = "password-reset") -> dict[str, Any]:
    return {"run_id": "r", "task": get_task(task_id).task, "workspace": str(workspace), "trajectory": []}


def deps_for(llm: Any) -> Deps:
    return Deps(settings=Settings(), llm=llm, sandbox=FakeSandbox([0]))


GOOD = {
    "goal": "Password reset",
    "tasks": [
        {
            "id": "T1",
            "description": "migration",
            "files_to_touch": [],
            "new_files": ["migrations/003_tokens.sql"],
            "validation_criteria": "applies",
        },
        {
            "id": "T2",
            "description": "logic",
            "dependencies": ["T1"],
            "files_to_touch": ["authkit/users.py"],
            "validation_criteria": "flow works",
        },
        {
            "id": "T3",
            "description": "tests",
            "dependencies": ["T2"],
            "new_files": ["tests/test_password_reset.py"],
            "validation_criteria": "pass",
        },
    ],
}


async def test_plan_sees_the_real_repo_and_reports_quality(tmp_path: Path) -> None:
    seed_workspace(SAMPLE_APP, tmp_path)
    llm = FakeProvider(lambda s, p: json.dumps(GOOD))

    update = await plan(state_for(tmp_path), deps_for(llm))  # type: ignore[arg-type]

    prompt = llm.calls[0][1]
    assert "hash_token(token)" in prompt and "migrations/002_sessions.sql: creates table sessions" in prompt
    output = update["event"]["output"]
    assert output["attempts"] == 1 and output["task_count"] == 3
    assert output["quality"] == {"ok": True, "issues": []}
    assert update["plan"]["tasks"][0]["new_files"] == ["migrations/003_tokens.sql"]


async def test_plan_retries_once_with_the_error_fed_back(tmp_path: Path) -> None:
    seed_workspace(SAMPLE_APP, tmp_path)
    cyclic = {
        "goal": "g",
        "tasks": [
            {"id": "T1", "description": "a", "dependencies": ["T2"]},
            {"id": "T2", "description": "b", "dependencies": ["T1"]},
        ],
    }
    llm = ReplayProvider([{"response": json.dumps(cyclic)}, {"response": json.dumps(GOOD)}])

    update = await plan(state_for(tmp_path), deps_for(llm))  # type: ignore[arg-type]

    assert update["event"]["output"]["attempts"] == 2
    assert "dependency cycle" in llm.calls[1][1] and "previous reply was rejected" in llm.calls[1][1]


async def test_plan_retries_on_unparseable_reply(tmp_path: Path) -> None:
    seed_workspace(SAMPLE_APP, tmp_path)
    llm = ReplayProvider([{"response": "I cannot do that"}, {"response": f"Here you go:\n{json.dumps(GOOD)}"}])

    update = await plan(state_for(tmp_path), deps_for(llm))  # type: ignore[arg-type]

    assert update["event"]["output"]["attempts"] == 2


async def test_plan_fails_clearly_after_two_bad_replies(tmp_path: Path) -> None:
    seed_workspace(SAMPLE_APP, tmp_path)
    llm = FakeProvider(lambda s, p: '{"goal": "g", "tasks": []}')

    with pytest.raises(ValueError, match="still invalid after 2 attempts"):
        await plan(state_for(tmp_path), deps_for(llm))  # type: ignore[arg-type]
    assert len(llm.calls) == 2


async def test_plan_records_soft_issues_without_retrying(tmp_path: Path) -> None:
    seed_workspace(SAMPLE_APP, tmp_path)
    padded = {
        "goal": "g",
        "tasks": [
            {"id": "T1", "description": "inspect the schema"},
            {"id": "T2", "description": "edit", "files_to_touch": ["authkit/users.py"], "validation_criteria": "x"},
        ],
    }
    llm = FakeProvider(lambda s, p: json.dumps(padded))

    update = await plan(state_for(tmp_path), deps_for(llm))  # type: ignore[arg-type]

    output = update["event"]["output"]
    assert len(llm.calls) == 1 and output["attempts"] == 1 and not output["quality"]["ok"]
    assert any("T1 changes no files" in i for i in output["quality"]["issues"])
    assert any("adds no new migrations" in i for i in output["quality"]["issues"])


def test_sample_app_file_list_matches_what_tasks_expect(tmp_path: Path) -> None:
    seed_workspace(SAMPLE_APP, tmp_path)
    assert {"authkit/sessions.py", "migrations/002_sessions.sql"} <= set(list_files(tmp_path))
