"""Independent ground truth: run a task's hidden acceptance test against the agents' final workspace."""

from __future__ import annotations

import shutil
from pathlib import Path

from agenteval.config import Settings
from agenteval.datasets import Task
from agenteval.execution.docker.sandbox import Sandbox
from agenteval.execution.reports import parse_junit
from agenteval.execution.workspace import REPORT_DIR

ACCEPTANCE_DIR = ".acceptance"
ACCEPTANCE_REPORT = f"{REPORT_DIR}/acceptance.xml"
ACCEPTANCE_COMMAND = (
    f"python -m pytest -q -p no:cacheprovider --junitxml={ACCEPTANCE_REPORT} {ACCEPTANCE_DIR}/test_acceptance.py"
)


async def run_acceptance(task: Task, workspace: Path, sandbox: Sandbox, settings: Settings) -> dict[str, float] | None:
    """Return acceptance metrics, or None if the task has no acceptance test.

    The test is copied in only for this run and removed afterwards, so it can never leak into what
    later agents see.
    """
    source = task.acceptance_path
    if source is None:
        return None
    target = workspace / ACCEPTANCE_DIR
    (workspace / REPORT_DIR).mkdir(exist_ok=True)
    target.mkdir(exist_ok=True)
    shutil.copy(source, target / "test_acceptance.py")
    try:
        uses_browser = "playwright" in source.read_text()
        image = settings.sandbox_browser_image if uses_browser else settings.sandbox_image
        result = await sandbox.run(str(workspace), ACCEPTANCE_COMMAND, image=image)
        summary = parse_junit(workspace / ACCEPTANCE_REPORT)
    finally:
        shutil.rmtree(target, ignore_errors=True)

    total = summary["total"] if summary else 0
    passed_tests = summary["passed"] if summary else 0
    return {
        "acceptance_passed": 1.0 if result.passed and total > 0 and passed_tests == total else 0.0,
        "acceptance_tests_passed": float(passed_tests),
        "acceptance_tests_total": float(total),
    }
