"""Working data: the sample target app, the task set, hidden acceptance tests and reference solutions."""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

DATASETS_ROOT = Path(__file__).resolve().parents[1] / "datasets"
SAMPLE_APP = DATASETS_ROOT / "sample_app"
SEEDS: dict[str, Path] = {"sample_app": SAMPLE_APP}


@dataclass(frozen=True)
class Task:
    id: str
    title: str
    task: str
    difficulty: str
    tags: tuple[str, ...]
    requirements: tuple[str, ...]
    acceptance_test: str | None = None
    # Ground truth for the research agent: files that must be read to solve the task, and files that
    # are reasonable to read. Both are paths in the sample app.
    needed_context: tuple[str, ...] = ()
    helpful_context: tuple[str, ...] = ()

    @property
    def acceptance_path(self) -> Path | None:
        """The hidden acceptance test. Agents never see it; the harness runs it after a run."""
        return DATASETS_ROOT / "acceptance" / self.acceptance_test if self.acceptance_test else None

    @property
    def reference_dir(self) -> Path:
        """A known-good solution, as files to copy over the sample app."""
        return DATASETS_ROOT / "reference" / self.id


@lru_cache
def load_tasks() -> tuple[Task, ...]:
    raw = json.loads((DATASETS_ROOT / "tasks.json").read_text())
    tasks = tuple(
        Task(
            id=item["id"],
            title=item["title"],
            task=item["task"],
            difficulty=item["difficulty"],
            tags=tuple(item.get("tags", [])),
            requirements=tuple(item.get("requirements", [])),
            acceptance_test=item.get("acceptance_test"),
            needed_context=tuple(item.get("needed_context", [])),
            helpful_context=tuple(item.get("helpful_context", [])),
        )
        for item in raw
    )
    ids = [t.id for t in tasks]
    if len(ids) != len(set(ids)):
        raise ValueError(f"duplicate task ids in tasks.json: {sorted({i for i in ids if ids.count(i) > 1})}")
    for t in tasks:
        if t.acceptance_path is not None and not t.acceptance_path.is_file():
            raise ValueError(f"task {t.id}: acceptance test {t.acceptance_test} not found")
        for path in (*t.needed_context, *t.helpful_context):
            if not (SAMPLE_APP / path).is_file():
                raise ValueError(f"task {t.id}: context file {path} is not in the sample app")
    return tasks


def get_task(task_id: str) -> Task:
    for task in load_tasks():
        if task.id == task_id:
            return task
    raise KeyError(task_id)
