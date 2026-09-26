"""Run exactly one agent on one task against the real sample app, and show everything it did.

    uv run python -m agenteval.dev.stage planner --task password-reset
    uv run python -m agenteval.dev.stage research --task password-reset   # continues from the planner's saved state
    uv run python -m agenteval.dev.stage planner --task all --brief       # every task, compact table

State lives in .agenteval-dev/<task>/ (state.json, the workspace, and one transcript per agent), so
each agent starts from the previous agent's real output. `planner` always starts fresh.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, get_args

from agenteval.config import ProviderName, Settings
from agenteval.datasets import SAMPLE_APP, get_task, load_tasks
from agenteval.evaluation.draft import score_draft
from agenteval.evaluation.retrieval import score_retrieval
from agenteval.execution.docker.sandbox import DockerSandbox, Sandbox
from agenteval.execution.workspace import seed_workspace
from agenteval.llm.provider import LLMProvider, RecordingProvider, get_provider
from agenteval.observability.events import NodeError, traced
from agenteval.orchestration.deps import Deps
from agenteval.orchestration.graph import NODES

ROOT = Path(__file__).resolve().parents[2]
DEV_DIR = ROOT / ".agenteval-dev"
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "llm"


@dataclass
class StageResult:
    agent: str
    task_id: str
    ok: bool
    event: dict[str, Any]
    transcript: list[dict[str, Any]]
    directory: Path
    started_fresh: bool
    error: str | None = None
    notes: list[str] = field(default_factory=list)
    scorecard: dict[str, Any] | None = None  # research only: what it read vs the ground truth

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent": self.agent,
            "task_id": self.task_id,
            "ok": self.ok,
            "error": self.error,
            "notes": self.notes,
            "started_fresh": self.started_fresh,
            "event": self.event,
            "transcript": self.transcript,
            "scorecard": self.scorecard,
        }


def _save_result(result: StageResult) -> None:
    (result.directory / f"{result.agent}.result.json").write_text(json.dumps(result.to_dict(), indent=2))


def load_stage_result(task_id: str, agent: str, dev_dir: Path = DEV_DIR) -> dict[str, Any] | None:
    """The saved result of the last time this agent ran on this task (success or failure)."""
    path = dev_dir / task_id / f"{agent}.result.json"
    return json.loads(path.read_text()) if path.is_file() else None


def stage_status(task_id: str, dev_dir: Path = DEV_DIR) -> list[dict[str, Any]]:
    """For every agent in pipeline order: has it run on this task, and how did it go."""
    rows = []
    for agent in NODES:
        saved = load_stage_result(task_id, agent, dev_dir)
        event = saved["event"] if saved else None
        rows.append(
            {
                "agent": agent,
                "ran": saved is not None,
                "ok": saved["ok"] if saved else None,
                "event_type": event["event_type"] if event else None,
                "latency_ms": event["latency_ms"] if event else None,
                "tokens_in": event["token_usage"]["input"] if event else None,
                "tokens_out": event["token_usage"]["output"] if event else None,
            }
        )
    return rows


# What each stage stores in the state, so that re-running an early stage can forget what came after it.
PRODUCES = {
    "research": ["context"],
    "draft": ["artifacts"],
    "execute": ["execution_results"],
    "validate": ["validation_results"],
    "analyze_failure": ["failures"],
    "repair": ["repairs"],
    "evaluate": ["metrics", "status"],
}
REWINDING = ("research", "draft")


def rewind(state: dict[str, Any], base: Path, agent: str) -> None:
    """Re-running research or the coder invalidates everything derived from them.

    The workspace goes back to the untouched app (so a new draft never builds on an earlier one, and
    research never reads files an earlier draft changed), and this stage's and every later stage's saved
    results are forgotten.
    """
    later = list(NODES)[list(NODES).index(agent) :]
    workspace = Path(state["workspace"])
    shutil.rmtree(workspace, ignore_errors=True)
    seed_workspace(SAMPLE_APP, workspace)
    state["trajectory"] = [e for e in state.get("trajectory", []) if e["agent"] not in later]
    for stage in later:
        for key in PRODUCES.get(stage, []):
            state.pop(key, None)
        (base / f"{stage}.result.json").unlink(missing_ok=True)


async def run_stage(
    agent: str,
    task_id: str,
    *,
    llm: LLMProvider,
    sandbox: Sandbox,
    settings: Settings,
    dev_dir: Path = DEV_DIR,
    fresh: bool = False,
) -> StageResult:
    task = get_task(task_id)
    base = dev_dir / task_id
    state_file = base / "state.json"

    started_fresh = fresh or agent == "planner" or not state_file.exists()
    if started_fresh:
        shutil.rmtree(base, ignore_errors=True)
        base.mkdir(parents=True)
        workspace = base / "workspace"
        seed_workspace(SAMPLE_APP, workspace)
        state: dict[str, Any] = {
            "run_id": f"dev-{task_id}",
            "task": task.task,
            "workspace": str(workspace),
            "trajectory": [],
        }
    else:
        state = json.loads(state_file.read_text())

    recorder = RecordingProvider(llm)
    node = traced(agent, NODES[agent], Deps(settings=settings, llm=recorder, sandbox=sandbox))
    transcript_file = base / f"{agent}.transcript.json"
    notes = []
    if started_fresh and agent != "planner":
        notes.append("no earlier state found: started from the untouched app, without a plan")
    if not started_fresh and agent in REWINDING:
        rewind(state, base, agent)
        notes.append("workspace reset to the untouched app; results of this and later stages were discarded")

    try:
        update = await node(state)  # type: ignore[arg-type]
    except NodeError as exc:
        transcript_file.write_text(json.dumps(recorder.transcript, indent=2))
        failed = StageResult(
            agent, task_id, False, exc.event, recorder.transcript, base, started_fresh, str(exc), notes
        )
        _save_result(failed)
        return failed

    events = update.pop("trajectory")
    state["trajectory"] = [*state.get("trajectory", []), *events]
    state.update(update)  # same as LangGraph: a node's keys replace the old values
    state_file.write_text(json.dumps(state, indent=2))
    transcript_file.write_text(json.dumps(recorder.transcript, indent=2))
    result = StageResult(agent, task_id, True, events[-1], recorder.transcript, base, started_fresh, None, notes)
    if agent == "research":
        result.scorecard = score_retrieval(task, events[-1]["output"]["files_selected"]).to_dict()
    elif agent == "draft":
        result.scorecard = await score_draft(
            task, Path(state["workspace"]), sandbox, settings, state.get("plan", {}), state.get("artifacts", [])
        )
    _save_result(result)
    return result


def save_fixture(result: StageResult) -> Path:
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    path = FIXTURE_DIR / f"{result.agent}__{result.task_id}.json"
    path.write_text(json.dumps(result.transcript, indent=2))
    return path


# --- presentation ---------------------------------------------------------------------------------


def clip(text: str, limit: int, full: bool) -> str:
    if full or len(text) <= limit:
        return text
    return f"{text[:limit]}\n… [{len(text) - limit} more characters; use --full or read the transcript file]"


def summarize_output(output: dict[str, Any]) -> str:
    """One line of the scalar facts in an event's output (list -> its length; 'issues' shown in full)."""
    parts = []
    for key, value in output.items():
        if isinstance(value, bool | int | float) or (isinstance(value, str) and len(value) <= 40):
            parts.append(f"{key}={value}")
        elif isinstance(value, list):
            parts.append(f"{key}={len(value)}")
        elif isinstance(value, dict) and "issues" in value:
            parts.append(f"{key}.issues={value['issues']}")
    return "  ".join(parts)


def scorecard_line(scorecard: dict[str, Any]) -> str:
    if scorecard.get("kind") == "draft":
        verdict = "PASS" if scorecard["acceptance_passed"] else "FAIL"
        return (
            f"acceptance={verdict} ({scorecard['acceptance_tests_passed']}/{scorecard['acceptance_tests_total']} "
            f"hidden tests)  files_written={len(scorecard['files_written'])}"
        )
    return (
        f"recall={scorecard['recall']:.0%} ({len(scorecard['found'])}/{len(scorecard['needed'])} needed files read)  "
        f"precision={scorecard['precision']:.0%}"
    )


def render_scorecard(scorecard: dict[str, Any]) -> str:
    if scorecard.get("kind") == "draft":
        lines = [f"\n── against the hidden acceptance test ──\n{scorecard_line(scorecard)}"]
        lines.append(f"  written:               {', '.join(scorecard['files_written']) or 'nothing'}")
        lines.append(f"  planned, not written:  {', '.join(scorecard['planned_not_written']) or 'none'}")
        lines.append(f"  written, not planned:  {', '.join(scorecard['written_not_planned']) or 'none'}")
        return "\n".join(lines)
    lines = [f"\n── against the reference solution ──\n{scorecard_line(scorecard)}"]
    lines.append(f"  needed:     {', '.join(scorecard['needed']) or '-'}")
    lines.append(f"  missed:     {', '.join(scorecard['missed']) or 'none'}")
    lines.append(f"  helpful:    {', '.join(scorecard['helpful_hits']) or 'none'}")
    lines.append(f"  irrelevant: {', '.join(scorecard['irrelevant']) or 'none'}")
    return "\n".join(lines)


def render(result: StageResult, *, full: bool = False, brief: bool = False) -> str:
    usage = result.event["token_usage"]
    model = result.transcript[0]["model"] if result.transcript else "-"
    header = f"{result.agent} · {result.task_id} · {model}"
    status = "OK" if result.ok else "FAILED"
    facts = (
        f"{status}  {result.event['event_type']}  latency={result.event['latency_ms']}ms  "
        f"llm_calls={usage['llm_calls']}  tokens in/out={usage['input']}/{usage['output']}"
    )
    if brief:
        extra = f"\n  {scorecard_line(result.scorecard)}" if result.scorecard else ""
        return f"{header}\n  {facts}\n  {summarize_output(result.event['output'])}{extra}" + (
            f"\n  ERROR: {result.error}" if result.error else ""
        )

    lines = [f"{'═' * 8} {header} {'═' * 8}", *(f"note: {n}" for n in result.notes)]
    for i, exchange in enumerate(result.transcript, start=1):
        lines += [
            f"\n── LLM call {i}: system prompt ──",
            clip(exchange["system"], 1500, full),
            f"\n── LLM call {i}: user prompt ──",
            clip(exchange["prompt"], 3500, full),
            f"\n── LLM call {i}: raw response ({exchange['output_tokens']} tokens) ──",
            clip(exchange["response"], 3500, full),
        ]
    lines += [
        f"\n── event ──\n{facts}",
        clip(json.dumps(result.event["output"], indent=2), 4000, full),
    ]
    if result.scorecard:
        lines.append(render_scorecard(result.scorecard))
    if result.error:
        lines.append(f"\nERROR: {result.error}")
    lines.append(f"\nstate + transcript saved in {result.directory}")
    return "\n".join(lines)


def render_table(results: list[StageResult]) -> str:
    rows = [f"{'task':24} {'ok':3} {'calls':5} {'in':>6} {'out':>6} {'ms':>7}  facts"]
    for r in results:
        u = r.event["token_usage"]
        rows.append(
            f"{r.task_id:24} {'yes' if r.ok else 'NO':3} {u['llm_calls']:5} {u['input']:>6} {u['output']:>6} "
            f"{r.event['latency_ms']:>7}  {summarize_output(r.event['output'])}"
            + (f"  {scorecard_line(r.scorecard)}" if r.scorecard else "")
        )
    total_in = sum(r.event["token_usage"]["input"] for r in results)
    total_out = sum(r.event["token_usage"]["output"] for r in results)
    rows.append(f"{'total':24} {'':3} {'':5} {total_in:>6} {total_out:>6}")
    return "\n".join(rows)


# --- command line ---------------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("agent", choices=sorted(NODES))
    parser.add_argument("--task", required=True, help="task id from datasets/tasks.json, or 'all'")
    parser.add_argument("--provider", default="gemini", choices=get_args(ProviderName))
    parser.add_argument("--model", default=None, help="override the provider's default model")
    parser.add_argument("--fresh", action="store_true", help="discard saved state and start from the untouched app")
    parser.add_argument("--full", action="store_true", help="do not truncate prompts and responses")
    parser.add_argument("--brief", action="store_true", help="one compact block per task")
    parser.add_argument("--save-fixture", action="store_true", help="save the real LLM exchange to tests/fixtures/llm/")
    return parser.parse_args(argv)


async def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    settings = Settings(llm_provider=args.provider, llm_model=args.model)
    llm, sandbox = get_provider(settings), DockerSandbox(settings)
    task_ids = [t.id for t in load_tasks()] if args.task == "all" else [args.task]

    results = []
    for task_id in task_ids:
        result = await run_stage(args.agent, task_id, llm=llm, sandbox=sandbox, settings=settings, fresh=args.fresh)
        results.append(result)
        print(render(result, full=args.full, brief=args.brief or len(task_ids) > 1))
        if args.save_fixture and result.ok:
            print(f"fixture saved: {save_fixture(result)}")
    if len(results) > 1:
        print(f"\n{render_table(results)}")
    return 0 if all(r.ok for r in results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
