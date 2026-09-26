"""Which checks the executor runs against a workspace, and in which sandbox image (design doc §8.4)."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from agenteval.config import Settings
from agenteval.execution.workspace import IGNORED_DIRS, REPORT_DIR

JUNIT_REPORT = f"{REPORT_DIR}/pytest.xml"
PYTEST_COMMAND = f"python -m pytest -q --tb=short -p no:cacheprovider --junitxml={JUNIT_REPORT}"
COMPILE_COMMAND = "python -m compileall -q ."


@dataclass(frozen=True)
class Check:
    name: str
    command: str
    image: str
    required: bool = True


def test_files(workspace: Path) -> list[Path]:
    return [
        p
        for pattern in ("test_*.py", "*_test.py")
        for p in workspace.rglob(pattern)
        if not IGNORED_DIRS.intersection(p.relative_to(workspace).parts)
    ]


def uses_browser(workspace: Path) -> bool:
    return any("playwright" in p.read_text(errors="replace") for p in test_files(workspace))


def plan_checks(workspace: Path, settings: Settings) -> list[Check]:
    test_image = settings.sandbox_browser_image if uses_browser(workspace) else settings.sandbox_image
    return [
        Check("compile", COMPILE_COMMAND, settings.sandbox_image),
        Check("pytest", PYTEST_COMMAND, test_image),
    ]


def reset_reports(workspace: Path) -> Path:
    """Clear reports from a previous attempt so stale evidence is never read."""
    reports = workspace / REPORT_DIR
    shutil.rmtree(reports, ignore_errors=True)
    reports.mkdir()
    return reports
