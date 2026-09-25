"""Parse test-runner reports into structured evidence."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

MAX_MESSAGE = 800


def parse_junit(path: Path) -> dict[str, Any] | None:
    """Summarise a pytest JUnit XML report; None if the report is missing or unreadable."""
    try:
        root = ET.parse(path).getroot()
    except (FileNotFoundError, ET.ParseError):
        return None

    cases: list[dict[str, Any]] = []
    for case in root.iter("testcase"):
        name = f"{case.get('classname', '')}::{case.get('name', '')}".lstrip(":")
        outcome, message = "passed", ""
        for tag in ("failure", "error", "skipped"):
            node = case.find(tag)
            if node is not None:
                outcome = {"failure": "failed", "error": "error", "skipped": "skipped"}[tag]
                message = (node.get("message") or node.text or "")[:MAX_MESSAGE]
                break
        cases.append({"test": name, "outcome": outcome, "message": message})

    def count(outcome: str) -> int:
        return sum(1 for c in cases if c["outcome"] == outcome)

    return {
        "total": len(cases),
        "passed": count("passed"),
        "failed": count("failed"),
        "errors": count("error"),
        "skipped": count("skipped"),
        "passed_tests": [c["test"] for c in cases if c["outcome"] == "passed"],
        "failures": [
            {"test": c["test"], "outcome": c["outcome"], "message": c["message"]}
            for c in cases
            if c["outcome"] in ("failed", "error")
        ],
    }
