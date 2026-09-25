"""Isolated per-run workspace on disk; every modification is captured as a diff (design doc §8.3)."""

from __future__ import annotations

import difflib
from pathlib import Path

MAX_FILE_BYTES = 50_000
IGNORED_DIRS = {".git", "__pycache__", ".pytest_cache", "node_modules", ".venv", ".agenteval"}


def create_workspace(root: str, run_id: str) -> Path:
    path = Path(root) / run_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def _resolve(workspace: Path, relative: str) -> Path:
    target = (workspace / relative).resolve()
    if not target.is_relative_to(workspace.resolve()):
        raise ValueError(f"path escapes workspace: {relative}")
    return target


def list_files(workspace: Path) -> list[str]:
    return sorted(
        str(p.relative_to(workspace))
        for p in workspace.rglob("*")
        if p.is_file() and not IGNORED_DIRS.intersection(p.relative_to(workspace).parts)
    )


def read_files(workspace: Path, paths: list[str]) -> dict[str, str]:
    contents = {}
    for relative in paths:
        target = _resolve(workspace, relative)
        if target.is_file() and target.stat().st_size <= MAX_FILE_BYTES:
            contents[relative] = target.read_text(errors="replace")
    return contents


def write_file(workspace: Path, relative: str, content: str) -> dict[str, object]:
    """Write a file and return an artifact record containing its unified diff."""
    target = _resolve(workspace, relative)
    before = target.read_text(errors="replace") if target.exists() else ""
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    diff = "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            content.splitlines(keepends=True),
            fromfile=f"a/{relative}",
            tofile=f"b/{relative}",
        )
    )
    added = sum(1 for line in diff.splitlines() if line.startswith("+") and not line.startswith("+++"))
    removed = sum(1 for line in diff.splitlines() if line.startswith("-") and not line.startswith("---"))
    return {"path": relative, "created": not before, "lines_added": added, "lines_removed": removed, "diff": diff}
