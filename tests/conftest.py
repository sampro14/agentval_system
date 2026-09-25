from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agenteval.config import Settings
from agenteval.execution.checks import JUNIT_REPORT
from agenteval.execution.docker.sandbox import ExecutionResult
from agenteval.llm.provider import FakeProvider
from agenteval.storage.db import make_engine, make_sessionmaker
from agenteval.storage.models import Base

PLAN = {
    "goal": "add function",
    "tasks": [{"id": "T1", "description": "write add() returning the sum", "dependencies": []}],
}
DRAFT = {"summary": "add add()", "files": [{"path": "calc.py", "content": "def add(a, b):\n    return a + b\n"}]}
COVERED = {"requirement": "add() returns the sum", "implemented": True, "tested": True, "evidence": "test_add"}


def scripted_llm(review: dict[str, Any] | None = None, analysis: dict[str, Any] | None = None) -> FakeProvider:
    def respond(system: str, _prompt: str) -> str:
        if "planning agent" in system:
            return json.dumps(PLAN)
        if "research agent" in system:
            return json.dumps({"relevant_files": [], "notes": "empty repo"})
        if "validation agent" in system:
            return json.dumps(review or {"requirements": [COVERED], "issues": []})
        if "failure analyzer" in system:
            return json.dumps(
                analysis
                or {
                    "failure_type": "CODE_GENERATION",
                    "root_cause": "add() is wrong",
                    "confidence": 0.75,
                    "evidence": ["assert 1 == 5"],
                    "recommended_action": "fix add()",
                }
            )
        return f"```json\n{json.dumps(DRAFT)}\n```"

    return FakeProvider(respond)


def junit_xml(cases: dict[str, str]) -> str:
    """cases: test name -> 'passed' | 'failed'."""
    body = "".join(
        f'<testcase classname="test_calc" name="{name}">'
        + ('<failure message="assert 1 == 5">AssertionError</failure>' if outcome == "failed" else "")
        + "</testcase>"
        for name, outcome in cases.items()
    )
    return f'<?xml version="1.0"?><testsuites><testsuite name="pytest">{body}</testsuite></testsuites>'


class FakeSandbox:
    """Scripted sandbox. Each pytest attempt consumes the next outcome (the last one repeats).

    An outcome is either a pytest exit code (0 -> one passing test, 1 -> one failing test,
    5 -> no tests) or a dict of test name -> 'passed'/'failed'. Compile always succeeds.
    """

    def __init__(self, outcomes: list[int | dict[str, str]]):
        self.outcomes = outcomes
        self.calls: list[str] = []
        self.images: list[str | None] = []

    async def run(self, workspace: str, command: str, image: str | None = None) -> ExecutionResult:
        self.images.append(image)
        if "pytest" not in command:
            return ExecutionResult(command=command, exit_code=0, stdout="", stderr="", duration_ms=1)
        outcome = self.outcomes[min(len(self.calls), len(self.outcomes) - 1)]
        self.calls.append(workspace)
        if isinstance(outcome, int):
            cases = {} if outcome == 5 else {"test_add": "passed" if outcome == 0 else "failed"}
        else:
            cases = outcome
        code = 5 if not cases else (1 if "failed" in cases.values() else 0)
        if cases:
            (Path(workspace) / JUNIT_REPORT).write_text(junit_xml(cases))
        stdout = "no tests ran" if code == 5 else ("1 passed" if code == 0 else "FAILED test_calc.py::test_add")
        return ExecutionResult(command=command, exit_code=code, stdout=stdout, stderr="", duration_ms=5)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url="sqlite+aiosqlite://",
        llm_provider="fake",
        workspace_root=str(tmp_path / "workspaces"),
    )


@pytest.fixture
async def sessions(tmp_path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield make_sessionmaker(engine)
    await engine.dispose()


@pytest.fixture
def sandbox_factory() -> Callable[[list[int | dict[str, str]]], FakeSandbox]:
    return FakeSandbox
