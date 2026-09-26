from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from agenteval.agents.coder.agent import (
    Draft,
    DraftFile,
    build_prompt,
    defined_names,
    draft,
    draft_problems,
    plan_adherence,
)
from agenteval.agents.repairer.agent import repair
from agenteval.config import Settings
from agenteval.datasets import SAMPLE_APP
from agenteval.execution.workspace import read_files, seed_workspace
from agenteval.llm.provider import FakeProvider, ReplayProvider
from agenteval.orchestration.deps import Deps
from tests.conftest import FakeSandbox

PLAN = {
    "goal": "Add change_password",
    "tasks": [
        {"id": "T1", "description": "impl", "files_to_touch": ["authkit/users.py"], "new_files": []},
        {"id": "T2", "description": "tests", "files_to_touch": [], "new_files": ["tests/test_change.py"]},
    ],
}

USERS_WITH_NEW_FUNCTION = """\"\"\"users\"\"\"
import sqlite3

from authkit.security import hash_password, verify_password


class UserError(Exception):
    pass


class DuplicateUserError(UserError):
    pass


class AuthenticationError(UserError):
    pass


def register(conn, email, password):
    return 1


def get_user(conn, user_id):
    return None


def get_user_by_email(conn, email):
    return None


def authenticate(conn, email, password):
    return 1


def change_password(conn, user_id, old_password, new_password):
    return None
"""


def reply(*files: tuple[str, str], summary: str = "did it") -> str:
    return json.dumps({"summary": summary, "files": [{"path": p, "content": c} for p, c in files]})


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    seed_workspace(SAMPLE_APP, tmp_path)
    return tmp_path


def state_for(workspace: Path, **extra: Any) -> dict[str, Any]:
    users = (workspace / "authkit" / "users.py").read_text()
    return {
        "run_id": "r",
        "task": "Add change_password",
        "workspace": str(workspace),
        "plan": PLAN,
        "context": [
            {"source": "repository", "path": "authkit/users.py", "content": users, "why": "the plan edits this file"}
        ],
        **extra,
    }


def deps_for(llm: Any) -> Deps:
    return Deps(settings=Settings(), llm=llm, sandbox=FakeSandbox([0]))


# --- the guards ------------------------------------------------------------------------------------------


def test_defined_names() -> None:
    assert defined_names("def a(): ...\nclass B: ...\nasync def c(): ...\nX = 1") == {"a", "B", "c"}
    assert defined_names("def broken(:") == set()


def test_a_valid_draft_has_no_problems(workspace: Path) -> None:
    good = Draft(files=[DraftFile(path="authkit/users.py", content=USERS_WITH_NEW_FUNCTION)])
    assert draft_problems(good, workspace) == []


@pytest.mark.parametrize(
    ("files", "expected"),
    [
        ([], "you returned no files"),
        ([("a.py", "def f(:\n  pass")], "a.py is not valid Python"),
        ([("a.py", "   \n")], "a.py has no content"),
        ([("a.py", "x = 1"), ("a.py", "x = 2")], "a.py is listed twice"),
        ([("../evil.py", "x = 1")], "../evil.py is outside the repository"),
        ([("/etc/x.py", "x = 1")], "/etc/x.py is outside the repository"),
    ],
)
def test_bad_drafts_are_rejected(workspace: Path, files: list[tuple[str, str]], expected: str) -> None:
    problems = draft_problems(Draft(files=[DraftFile(path=p, content=c) for p, c in files]), workspace)
    assert any(expected in p for p in problems), problems


def test_an_empty_init_file_is_fine(workspace: Path) -> None:
    assert draft_problems(Draft(files=[DraftFile(path="pkg/__init__.py", content="")]), workspace) == []


def test_rewriting_a_file_without_its_existing_functions_is_rejected(workspace: Path) -> None:
    """The coder writes whole files, so a draft that forgets existing code would silently delete it."""
    forgetful = Draft(files=[DraftFile(path="authkit/users.py", content="def change_password(): ...\n")])

    (problem,) = draft_problems(forgetful, workspace)

    assert "authkit/users.py no longer defines" in problem
    assert all(name in problem for name in ("register", "authenticate", "UserError"))


def test_plan_adherence() -> None:
    assert plan_adherence(PLAN, ["authkit/users.py", "tests/test_change.py"]) == {
        "planned_not_written": [],
        "written_not_planned": [],
    }
    assert plan_adherence(PLAN, ["authkit/users.py", "authkit/extra.py"]) == {
        "planned_not_written": ["tests/test_change.py"],
        "written_not_planned": ["authkit/extra.py"],
    }
    assert plan_adherence({}, ["anything.py"])["written_not_planned"] == []  # no plan, nothing to compare


# --- the agent ---------------------------------------------------------------------------------------------


async def test_a_valid_draft_is_written_and_reported(workspace: Path) -> None:
    llm = FakeProvider(
        lambda s, p: reply(("authkit/users.py", USERS_WITH_NEW_FUNCTION), ("tests/test_change.py", "def test_x(): ..."))
    )

    update = await draft(state_for(workspace), deps_for(llm))  # type: ignore[arg-type]

    output = update["event"]["output"]
    assert output["attempts"] == 1 and output["summary"] == "did it"
    assert output["files_modified"] == ["authkit/users.py", "tests/test_change.py"]
    assert output["plan_check"] == {"planned_not_written": [], "written_not_planned": []}
    assert "+def change_password" in output["diffs"]["authkit/users.py"]
    assert (workspace / "tests" / "test_change.py").is_file()


async def test_an_empty_draft_is_retried_with_the_reason(workspace: Path) -> None:
    llm = ReplayProvider([{"response": reply()}, {"response": reply(("authkit/users.py", USERS_WITH_NEW_FUNCTION))}])

    update = await draft(state_for(workspace), deps_for(llm))  # type: ignore[arg-type]

    assert update["event"]["output"]["attempts"] == 2
    assert "you returned no files" in llm.calls[1][1]


async def test_broken_python_is_retried_and_nothing_bad_is_written(workspace: Path) -> None:
    before = (workspace / "authkit" / "users.py").read_text()
    llm = ReplayProvider(
        [
            {"response": reply(("authkit/users.py", "def broken(:\n"))},
            {"response": reply(("authkit/users.py", USERS_WITH_NEW_FUNCTION))},
        ]
    )
    state = state_for(workspace)

    # After the first (rejected) reply the file must still be untouched; check via the retry prompt's state.
    update = await draft(state, deps_for(llm))  # type: ignore[arg-type]

    assert "is not valid Python" in llm.calls[1][1] and update["event"]["output"]["attempts"] == 2
    assert (workspace / "authkit" / "users.py").read_text() != before  # only the good draft was written


async def test_a_draft_that_deletes_existing_code_is_retried(workspace: Path) -> None:
    llm = ReplayProvider(
        [
            {"response": reply(("authkit/users.py", "def change_password(): ...\n"))},
            {"response": reply(("authkit/users.py", USERS_WITH_NEW_FUNCTION))},
        ]
    )

    update = await draft(state_for(workspace), deps_for(llm))  # type: ignore[arg-type]

    assert update["event"]["output"]["attempts"] == 2
    assert "no longer defines" in llm.calls[1][1] and "keep existing code" in llm.calls[1][1]


async def test_two_bad_replies_fail_clearly_and_write_nothing(workspace: Path) -> None:
    before = read_files(workspace, ["authkit/users.py"])
    llm = FakeProvider(lambda s, p: reply(("authkit/users.py", "def broken(:\n")))

    with pytest.raises(ValueError, match="coder output still invalid after 2 attempts"):
        await draft(state_for(workspace), deps_for(llm))  # type: ignore[arg-type]

    assert read_files(workspace, ["authkit/users.py"]) == before


async def test_a_reply_with_raw_newlines_in_strings_still_parses(workspace: Path) -> None:
    raw = '```json\n{"summary": "s", "files": [{"path": "notes.py", "content": "x = 1\ny = 2\n"}]}'
    update = await draft(state_for(workspace), deps_for(FakeProvider(lambda s, p: raw)))  # type: ignore[arg-type]
    assert (workspace / "notes.py").read_text() == "x = 1\ny = 2\n"
    assert update["event"]["output"]["attempts"] == 1


# --- what the coder is shown --------------------------------------------------------------------------------


def test_the_prompt_labels_each_file_with_why_it_is_there(workspace: Path) -> None:
    prompt = build_prompt(state_for(workspace))
    assert "--- authkit/users.py (the plan edits this file) ---" in prompt
    assert "def register(" in prompt


async def test_a_repair_sees_the_draft_not_the_stale_research_copy(workspace: Path) -> None:
    """Regression: a repair used to rebuild from the research-time contents and so threw the draft away."""
    llm = FakeProvider(
        lambda s, p: reply(("authkit/users.py", USERS_WITH_NEW_FUNCTION), ("tests/test_change.py", "def test_x(): ..."))
    )
    state = state_for(workspace)
    update = await draft(state, deps_for(llm))  # type: ignore[arg-type]
    state["artifacts"] = update["artifacts"]

    prompt = build_prompt(state, feedback="tests failed")

    assert "def change_password(conn, user_id, old_password, new_password)" in prompt  # the draft's code
    assert "--- tests/test_change.py (written by an earlier draft) ---" in prompt  # a file the draft created
    assert "The files above are as that attempt left them" in prompt


async def test_a_repair_runs_through_the_same_guards(workspace: Path) -> None:
    llm = ReplayProvider(
        [
            {"response": reply()},  # rejected: no files
            {"response": reply(("authkit/users.py", USERS_WITH_NEW_FUNCTION), summary="fixed it")},
        ]
    )
    state = state_for(workspace, failures=[{"category": "CODE_GENERATION", "root_cause": "x"}])

    update = await repair(state, deps_for(llm))  # type: ignore[arg-type]

    output = update["event"]["output"]
    assert output["attempts"] == 2 and output["action"] == "fixed it" and "diffs" in output
