"""Scores what the research agent chose to read against the task's ground truth (design doc §9.2).

This runs after the fact and never reaches the agents: they must not see which files count as right.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agenteval.datasets import Task


@dataclass(frozen=True)
class RetrievalReport:
    selected: tuple[str, ...]
    needed: tuple[str, ...]
    helpful: tuple[str, ...]

    @property
    def found(self) -> list[str]:
        return [f for f in self.needed if f in self.selected]

    @property
    def missed(self) -> list[str]:
        """Needed files the agent did not read: the failures that matter."""
        return [f for f in self.needed if f not in self.selected]

    @property
    def helpful_hits(self) -> list[str]:
        return [f for f in self.selected if f in self.helpful]

    @property
    def irrelevant(self) -> list[str]:
        """Files read that were neither required nor helpful: wasted context."""
        return [f for f in self.selected if f not in self.needed and f not in self.helpful]

    @property
    def recall(self) -> float:
        """Share of the needed files that were read. 1.0 when the task needs none."""
        return len(self.found) / len(self.needed) if self.needed else 1.0

    @property
    def precision(self) -> float:
        """Share of the files read that were needed or helpful."""
        relevant = len(self.selected) - len(self.irrelevant)
        return relevant / len(self.selected) if self.selected else 0.0

    def metrics(self) -> dict[str, float]:
        return {
            "retrieval_recall": self.recall,
            "retrieval_precision": self.precision,
            "retrieval_missed": float(len(self.missed)),
            "retrieval_irrelevant": float(len(self.irrelevant)),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "retrieval",
            "recall": self.recall,
            "precision": self.precision,
            "selected": list(self.selected),
            "needed": list(self.needed),
            "found": self.found,
            "missed": self.missed,
            "helpful_hits": self.helpful_hits,
            "irrelevant": self.irrelevant,
        }


def score_retrieval(task: Task, selected: list[str]) -> RetrievalReport:
    return RetrievalReport(tuple(selected), task.needed_context, task.helpful_context)
