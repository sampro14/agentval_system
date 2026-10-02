"""Run the failure-handling agents on known-broken solutions and score them against known answers.

This is design doc §22, Experiment 2 (injected failures). Every case is a complete solution to a task
(sample app + the reference solution) with at most one deliberate fault, or an untouched reference as a
control. No planner, research or coder LLM call is involved: the case's state is built directly, then the
real executor runs in the real sandbox, and the agent under test runs with the real LLM.

    uv run python -m agenteval.dev.faults validate                  # the dev split (safe to tune on)
    uv run python -m agenteval.dev.faults validate --split heldout  # only for the final check
    uv run python -m agenteval.dev.faults validate --case da-no-tests --repeat 3
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import shutil
import statistics
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agenteval.config import Settings
from agenteval.datasets import DATASETS_ROOT, SAMPLE_APP, get_task
from agenteval.execution.docker.sandbox import DockerSandbox, Sandbox
from agenteval.execution.workspace import IGNORED_DIRS, list_files, make_artifact, seed_workspace
from agenteval.llm.provider import LLMProvider, RecordingProvider, get_provider
from agenteval.observability.events import NodeError, traced
from agenteval.orchestration.deps import Deps
from agenteval.orchestration.graph import NODES

FAULTS_DIR = DATASETS_ROOT / "faults"
PLANS_DIR = DATASETS_ROOT / "plans"
ROOT = Path(__file__).resolve().parents[2]
RESULTS_DIR = ROOT / ".agenteval-dev" / "faults"


@dataclass(frozen=True)
class Case:
    id: str
    kind: str  # "fault" or "control"
    task_id: str
    split: str  # "dev" (tune on it) or "heldout" (look only at the end)
    description: str
    plan: str
    overlay: bool
    remove: tuple[str, ...]
    settings: dict[str, Any]
    expected: dict[str, Any]
    overlay_from: str | None = None  # reuse another case's overlay files
    history: str | None = None  # "after_repair": a good run came first, then a repair broke it
    research_files: tuple[str, ...] | None = None  # what research is recorded as having read (default: nothing)

    @property
    def overlay_dir(self) -> Path:
        return FAULTS_DIR / "overlays" / (self.overlay_from or self.id)


def load_cases() -> list[Case]:
    raw = json.loads((FAULTS_DIR / "faults.json").read_text())
    cases = []
    for item in raw:
        extra = {"research_files": tuple(item["research_files"])} if item.get("research_files") is not None else {}
        cases.append(Case(**{**item, "remove": tuple(item["remove"]), **extra}))
    return cases


def load_plan(name: str) -> dict[str, Any]:
    plan: dict[str, Any] = json.loads((PLANS_DIR / f"{name}.json").read_text())
    return plan


# --- building a case -------------------------------------------------------------------------------------------


def build_workspace(case: Case, workspace: Path) -> None:
    """The sample app, plus the reference solution, plus the fault (overlay files and removals)."""
    shutil.rmtree(workspace, ignore_errors=True)
    seed_workspace(SAMPLE_APP, workspace)
    seed_workspace(get_task(case.task_id).reference_dir, workspace)
    if case.overlay:
        seed_workspace(case.overlay_dir, workspace)
    for relative in case.remove:
        (workspace / relative).unlink()


def diff_artifacts(workspace: Path) -> list[dict[str, Any]]:
    """What the coder would have written: every file that differs from the untouched sample app."""
    artifacts = []
    for relative in list_files(workspace):
        if IGNORED_DIRS.intersection(Path(relative).parts):
            continue
        after = (workspace / relative).read_text(errors="replace")
        base_file = SAMPLE_APP / relative
        before = base_file.read_text(errors="replace") if base_file.is_file() else ""
        if before != after:
            artifacts.append(make_artifact(relative, before, after))
    return artifacts


def _synthetic_event(
    run_id: str, seq: int, agent: str, event_type: str, output: dict[str, Any] | None = None, status: str = "ok"
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "event_id": f"evt_{agent}_{seq}",
        "timestamp": "2026-01-01T00:00:00+00:00",
        "agent": agent,
        "event_type": event_type,
        "status": status,
        "output": output or {},
        "latency_ms": 0,
        "token_usage": {"input": 0, "output": 0, "llm_calls": 0},
    }


def build_state(case: Case, base: Path) -> dict[str, Any]:
    """The state a run would be in right after the coder finished, for this case."""
    workspace = base / "workspace"
    build_workspace(case, workspace)
    run_id = f"fault-{case.id}"
    return {
        "run_id": run_id,
        "task": get_task(case.task_id).task,
        "workspace": str(workspace),
        "plan": load_plan(case.plan),
        "context": [],
        "artifacts": diff_artifacts(workspace),
        "trajectory": [
            _synthetic_event(run_id, 1, "planner", "PLAN_CREATED"),
            _synthetic_event(
                run_id,
                2,
                "research",
                "CONTEXT_GATHERED",
                {"files_available": 19, "files_selected": list(case.research_files)} if case.research_files else None,
            ),
            _synthetic_event(run_id, 3, "draft", "DRAFT_CREATED"),
        ],
    }


async def prepare_repair_history(case: Case, state: dict[str, Any], deps: Deps) -> None:
    """Make the run look like an earlier repair broke something that used to work.

    The clean solution runs first, so the state records tests that passed. Then the fault is put in place
    as if a repair had just rewritten the code, and the repair is recorded in the trajectory.
    """
    workspace = Path(state["workspace"])
    build_workspace(dataclasses.replace(case, overlay=False, remove=(), overlay_from=None), workspace)
    await advance(state, "execute", deps)
    build_workspace(case, workspace)
    state["artifacts"] = diff_artifacts(workspace)
    state["failures"] = [
        {
            "stage": "validation",
            "category": "CODE_GENERATION",
            "root_cause": "an earlier requirement was not met",
            "confidence": 0.9,
            "evidence": [],
            "recommended_action": "implement the missing requirement",
            "attribution_method": "llm",
        }
    ]
    state["repairs"] = [
        {
            "iteration": 1,
            "failure_index": 0,
            "action": "implemented the missing requirement",
            "files_modified": [a["path"] for a in state["artifacts"]],
        }
    ]
    run_id = state["run_id"]
    state["trajectory"] += [
        _synthetic_event(run_id, 5, "validate", "VALIDATION_FAILED", status="failed"),
        _synthetic_event(run_id, 6, "analyze_failure", "FAILURE_CLASSIFIED"),
        _synthetic_event(run_id, 7, "repair", "REPAIR_APPLIED"),
    ]


# --- running and scoring ---------------------------------------------------------------------------------------


@dataclass
class Outcome:
    case: Case
    repeat: int
    stages_run: list[str] = field(default_factory=list)
    execution_passed: bool | None = None
    detected: bool | None = None  # did the validator fail this solution?
    failed_checks: list[str] = field(default_factory=list)
    requirements_review: str = ""
    category: str | None = None  # the failure analyzer's answer, when it ran
    stage: str | None = None
    confidence: float | None = None
    method: str | None = None  # "rule" or "llm"
    root_cause: str = ""
    tokens: int = 0
    seconds: float = 0.0
    error: str | None = None
    state: dict[str, Any] = field(default_factory=dict)
    transcript: list[dict[str, Any]] = field(default_factory=list)

    @property
    def execution_as_expected(self) -> bool:
        return bool(self.execution_passed == self.case.expected["execution_passes"])

    @property
    def verdict_correct(self) -> bool:
        """Faults must be caught (by at least the expected checks); controls must be accepted."""
        if self.error or self.detected is None:
            return False
        expected = self.case.expected
        if self.detected != expected["detected"]:
            return False
        return set(expected["failed_checks"]) <= set(self.failed_checks)

    @property
    def analyzed(self) -> bool:
        return self.category is not None

    @property
    def category_correct(self) -> bool:
        acceptable = [self.case.expected["category"], *self.case.expected.get("category_alternates", [])]
        return self.category in acceptable

    @property
    def stage_correct(self) -> bool:
        return bool(self.stage == self.case.expected["stage"])

    @property
    def analysis_correct(self) -> bool:
        """The failure is attributed to the right cause and the right agent."""
        return self.analyzed and self.category_correct and self.stage_correct

    def to_dict(self) -> dict[str, Any]:
        return {
            "case": self.case.id,
            "kind": self.case.kind,
            "split": self.case.split,
            "repeat": self.repeat,
            "expected": self.case.expected,
            "execution_passed": self.execution_passed,
            "detected": self.detected,
            "failed_checks": self.failed_checks,
            "requirements_review": self.requirements_review,
            "verdict_correct": self.verdict_correct,
            "analysis": {
                "category": self.category,
                "stage": self.stage,
                "confidence": self.confidence,
                "method": self.method,
                "root_cause": self.root_cause,
                "correct": self.analysis_correct if self.analyzed else None,
            },
            "tokens": self.tokens,
            "seconds": round(self.seconds, 1),
            "error": self.error,
        }


async def advance(state: dict[str, Any], agent: str, deps: Deps) -> None:
    """Run one node and merge its result into the state, as LangGraph would."""
    update = await traced(agent, NODES[agent], deps)(state)  # type: ignore[arg-type]
    events = update.pop("trajectory")
    state["trajectory"] = [*state["trajectory"], *events]
    state.update(update)


def needs_analysis(case: Case, state: dict[str, Any]) -> bool:
    """The analyzer only runs on a rejected solution, and controls are not supposed to be rejected."""
    results = state.get("validation_results", [])
    return case.kind == "fault" and bool(results) and not results[-1]["passed"]


async def run_case(
    case: Case,
    repeat: int,
    *,
    llm: LLMProvider,
    sandbox_for: Callable[[Settings], Sandbox],
    settings: Settings,
    base: Path,
    stages: tuple[str, ...] = ("execute", "validate"),
) -> Outcome:
    outcome = Outcome(case, repeat)
    started = time.perf_counter()
    case_settings = settings.model_copy(update=case.settings)
    recorder = RecordingProvider(llm)
    try:
        deps = Deps(settings=case_settings, llm=recorder, sandbox=sandbox_for(case_settings))
        state = build_state(case, base)
        outcome.state = state
        if case.history == "after_repair":
            await prepare_repair_history(case, state, deps)
        for stage in stages:
            if stage == "analyze_failure" and not needs_analysis(case, state):
                continue
            await advance(state, stage, deps)
            outcome.stages_run.append(stage)
    except NodeError as exc:
        outcome.error = str(exc)
    except Exception as exc:  # a harness problem must show up as a failed case, not abort the whole run
        outcome.error = f"{type(exc).__name__}: {exc}"

    state = outcome.state
    if state.get("execution_results"):
        outcome.execution_passed = state["execution_results"][-1]["passed"]
    if state.get("validation_results"):
        validation = state["validation_results"][-1]
        outcome.detected = not validation["passed"]
        outcome.failed_checks = validation["failed_checks"]
        outcome.requirements_review = validation["evidence"]["requirements_review"]
    if state.get("failures"):
        failure = state["failures"][-1]
        outcome.category, outcome.stage = failure["category"], failure["stage"]
        outcome.confidence, outcome.method = failure["confidence"], failure["attribution_method"]
        outcome.root_cause = failure["root_cause"]
    events = state.get("trajectory", [])
    outcome.tokens = sum(e["token_usage"]["input"] + e["token_usage"]["output"] for e in events)
    outcome.transcript = recorder.transcript
    outcome.seconds = time.perf_counter() - started
    return outcome


def summarize(outcomes: list[Outcome]) -> dict[str, Any]:
    faults = [o for o in outcomes if o.case.kind == "fault"]
    controls = [o for o in outcomes if o.case.kind == "control"]
    caught = [o for o in faults if o.detected]

    def rate(count: int, total: int) -> float | None:
        return count / total if total else None

    return {
        "runs": len(outcomes),
        "errors": sum(bool(o.error) for o in outcomes),
        "faults": len(faults),
        "detection_rate": rate(len(caught), len(faults)),
        "controls": len(controls),
        "false_positive_rate": rate(sum(bool(o.detected) for o in controls), len(controls)),
        "verdicts_correct": rate(sum(o.verdict_correct for o in outcomes), len(outcomes)),
        "execution_as_expected": rate(sum(o.execution_as_expected for o in outcomes), len(outcomes)),
        "avg_tokens": statistics.mean(o.tokens for o in outcomes) if outcomes else 0,
        "avg_seconds": statistics.mean(o.seconds for o in outcomes) if outcomes else 0,
    }


def summarize_analysis(outcomes: list[Outcome]) -> dict[str, Any]:
    """How well the analyzer attributed the faults it was given."""
    faults = [o for o in outcomes if o.case.kind == "fault"]
    analyzed = [o for o in faults if o.analyzed]

    def rate(count: int, total: int) -> float | None:
        return count / total if total else None

    by_method: dict[str, dict[str, int]] = {}
    for o in analyzed:
        bucket = by_method.setdefault(str(o.method), {"runs": 0, "correct": 0})
        bucket["runs"] += 1
        bucket["correct"] += o.analysis_correct
    confusion: dict[str, int] = {}
    for o in analyzed:
        key = f"{o.case.expected['category']}/{o.case.expected['stage']} -> {o.category}/{o.stage}"
        confusion[key] = confusion.get(key, 0) + 1

    def mean_confidence(group: list[Outcome]) -> float | None:
        return statistics.mean(o.confidence or 0.0 for o in group) if group else None

    return {
        "faults": len(faults),
        "not_analyzed": len(faults) - len(analyzed),  # the validator let them through
        "errors": sum(bool(o.error) for o in faults),
        "category_accuracy": rate(sum(o.category_correct for o in analyzed), len(analyzed)),
        "stage_accuracy": rate(sum(o.stage_correct for o in analyzed), len(analyzed)),
        "both_correct": rate(sum(o.analysis_correct for o in analyzed), len(analyzed)),
        "by_method": by_method,
        "confusion": dict(sorted(confusion.items())),
        "confidence_when_right": mean_confidence([o for o in analyzed if o.analysis_correct]),
        "confidence_when_wrong": mean_confidence([o for o in analyzed if not o.analysis_correct]),
        "avg_tokens": statistics.mean(o.tokens for o in faults) if faults else 0,
        "avg_seconds": statistics.mean(o.seconds for o in faults) if faults else 0,
    }


# --- presentation ----------------------------------------------------------------------------------------------


def pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.0%}"


def render_row(o: Outcome) -> str:
    expected = "CAUGHT " if o.case.expected["detected"] else "ACCEPT "
    got = "-" if o.detected is None else ("CAUGHT" if o.detected else "ACCEPT")
    mark = "ok " if o.verdict_correct else "BAD"
    checks = ",".join(o.failed_checks) or "-"
    wanted = ",".join(o.case.expected["failed_checks"]) or "-"
    note = f"  ERROR: {o.error[:90]}" if o.error else ""
    return (
        f"{mark} {o.case.id:26} {o.case.kind:7} want {expected} got {got:6}  checks: {checks:38} "
        f"(want {wanted})  {o.tokens:>5} tok {o.seconds:>4.0f}s{note}"
    )


def render_analysis_row(o: Outcome) -> str:
    want = f"{o.case.expected['category']}/{o.case.expected['stage']}"
    got = f"{o.category}/{o.stage}" if o.analyzed else "(not analyzed)"
    mark = "ok " if o.analysis_correct else "BAD"
    how = f"{o.method} {o.confidence:.2f}" if o.analyzed and o.confidence is not None else "-"
    note = f"  ERROR: {o.error[:90]}" if o.error else ""
    return f"{mark} {o.case.id:26} want {want:28} got {got:28} via {how:9} {o.tokens:>5} tok {o.seconds:>4.0f}s{note}"


def render_analysis_summary(summary: dict[str, Any]) -> str:
    lines = [
        f"faults={summary['faults']} not_analyzed={summary['not_analyzed']} errors={summary['errors']} | "
        f"category correct={pct(summary['category_accuracy'])} | "
        f"agent-to-blame correct={pct(summary['stage_accuracy'])} | "
        f"both={pct(summary['both_correct'])}",
    ]
    for method, b in summary["by_method"].items():
        lines.append(f"  via {method}: {b['correct']}/{b['runs']} fully correct")
    if summary["confidence_when_right"] is not None:
        wrong = summary["confidence_when_wrong"]
        lines.append(
            f"  confidence when right {summary['confidence_when_right']:.2f}, when wrong "
            f"{'n/a' if wrong is None else f'{wrong:.2f}'}"
        )
    lines.append("  what was expected -> what it said:")
    lines += [f"    {count}x  {key}" for key, count in summary["confusion"].items()]
    return "\n".join(lines)


def render_summary(summary: dict[str, Any]) -> str:
    return (
        f"runs={summary['runs']} errors={summary['errors']} | detection of faults={pct(summary['detection_rate'])} "
        f"({summary['faults']} runs) | false positives on controls={pct(summary['false_positive_rate'])} "
        f"({summary['controls']} runs) | verdicts correct={pct(summary['verdicts_correct'])} | "
        f"execution as expected={pct(summary['execution_as_expected'])} | "
        f"avg {summary['avg_tokens']:.0f} tokens, {summary['avg_seconds']:.0f}s per run"
    )


# --- command line ----------------------------------------------------------------------------------------------


def stages_for(agent: str) -> tuple[str, ...]:
    return ("execute", "validate", "analyze_failure") if agent == "analyze" else ("execute", "validate")


def select(cases: list[Case], split: str, only: list[str]) -> list[Case]:
    chosen = [c for c in cases if split == "all" or c.split == split]
    return [c for c in chosen if c.id in only] if only else chosen


async def run_all(
    cases: list[Case],
    repeat: int,
    *,
    llm: LLMProvider,
    sandbox_for: Callable[[Settings], Sandbox],
    settings: Settings,
    base: Path,
    stages: tuple[str, ...],
    concurrency: int = 4,
    on_done: Callable[[Outcome], None] | None = None,
) -> list[Outcome]:
    gate = asyncio.Semaphore(concurrency)

    async def one(case: Case, n: int) -> Outcome:
        async with gate:
            outcome = await run_case(
                case,
                n,
                llm=llm,
                sandbox_for=sandbox_for,
                settings=settings,
                base=base / f"{case.id}-{n}",
                stages=stages,
            )
        if on_done:
            on_done(outcome)
        return outcome

    return list(await asyncio.gather(*(one(c, n) for n in range(1, repeat + 1) for c in cases)))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("agent", choices=["validate", "analyze"], help="the agent under test")
    parser.add_argument("--split", choices=["dev", "heldout", "all"], default="dev")
    parser.add_argument("--case", action="append", default=[], help="only this case id (repeatable)")
    parser.add_argument("--repeat", type=int, default=1, help="run every case this many times (LLM variance)")
    parser.add_argument("--provider", default="gemini")
    parser.add_argument("--model", default=None)
    parser.add_argument("--keep", action="store_true", help="keep the built workspaces")
    return parser.parse_args(argv)


async def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    settings = Settings(llm_provider=args.provider, llm_model=args.model)
    cases = select(load_cases(), args.split, args.case)
    if args.agent == "analyze":
        cases = [c for c in cases if c.kind == "fault"]  # controls have nothing to analyze
    if not cases:
        print("no cases selected", file=sys.stderr)
        return 2
    if args.split == "heldout":
        print("held-out cases: use these for the final check only, not for tuning\n")
    base = RESULTS_DIR / "workspaces"
    print(f"{len(cases)} case(s) x {args.repeat} repeat(s), agent={args.agent}, model={settings.resolved_model}\n")
    outcomes = await run_all(
        cases,
        args.repeat,
        llm=get_provider(settings),
        sandbox_for=DockerSandbox,
        settings=settings,
        base=base,
        stages=stages_for(args.agent),
        on_done=lambda o: print(render_analysis_row(o) if args.agent == "analyze" else render_row(o), flush=True),
    )
    if args.agent == "analyze":
        summary = summarize_analysis(outcomes)
        print(f"\n{render_analysis_summary(summary)}")
    else:
        summary = summarize(outcomes)
        print(f"\n{render_summary(summary)}")
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    (RESULTS_DIR / f"{args.agent}-{args.split}-{stamp}.json").write_text(
        json.dumps({"summary": summary, "outcomes": [o.to_dict() for o in outcomes]}, indent=2)
    )
    if not args.keep:
        shutil.rmtree(base, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
