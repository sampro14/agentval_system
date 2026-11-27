"""Export finished runs from a running AgentEval API into static files for the hosted replay.

The dashboard's replay build (apps/web, NEXT_PUBLIC_REPLAY=1) serves these files instead of calling the API, and
plays each run back step by step. Pick a few runs that tell the story: a clean pass, a run that failed and was
repaired, and one that stayed failed.

    uv run python -m agenteval.dev.replay                       # every finished run (up to 200)
    uv run python -m agenteval.dev.replay --run ID --run ID     # only these runs, in this order
    uv run python -m agenteval.dev.replay --api http://localhost:8001 --out apps/web/public/replay

Home-directory paths and anything shaped like an API key are scrubbed before writing, since run outputs can carry
workspace paths and provider error messages.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = ROOT / "apps" / "web" / "public" / "replay"
FINISHED = {"succeeded", "recovered", "failed", "error"}

_HOME = re.compile(r"(/Users/|/home/|[A-Za-z]:\\+Users\\+)[^/\\\s\"'`]+")
_KEYS = re.compile(
    r"(AIza[0-9A-Za-z_\-]{20,}"  # Google
    r"|sk-ant-[0-9A-Za-z_\-]{20,}"  # Anthropic
    r"|sk-(?:proj-)?[0-9A-Za-z_\-]{20,}"  # OpenAI, DeepSeek
    r"|gsk_[0-9A-Za-z]{20,}"  # Groq
    r"|(?:key|api_key|apikey|token)=[0-9A-Za-z_\-]{16,})"
)


def scrub(value: Any) -> Any:
    """Replace home-directory prefixes with ~ and redact anything that looks like an API key, recursively."""
    if isinstance(value, str):
        return _KEYS.sub("[redacted]", _HOME.sub("~", value))
    if isinstance(value, list):
        return [scrub(v) for v in value]
    if isinstance(value, dict):
        return {k: scrub(v) for k, v in value.items()}
    return value


async def _get(client: httpx.AsyncClient, path: str, **params: Any) -> Any:
    response = await client.get(path, params=params or None)
    response.raise_for_status()
    return response.json()


async def export(client: httpx.AsyncClient, out: Path, run_ids: list[str] | None = None) -> list[dict[str, Any]]:
    """Write runs.json, tasks.json, meta.json and runs/<id>.json under `out`. Returns the exported runs."""
    if run_ids:
        runs = [await _get(client, f"/api/v1/runs/{run_id}") for run_id in run_ids]
        unfinished = [r["id"] for r in runs if r["status"] not in FINISHED]
        if unfinished:
            raise ValueError(f"these runs have not finished: {', '.join(unfinished)}")
    else:
        runs = [r for r in await _get(client, "/api/v1/runs", limit=200) if r["status"] in FINISHED]
    if not runs:
        raise ValueError("no finished runs to export: start a run from the dashboard first")

    bundles = {
        run["id"]: {
            "run": run,
            "trajectory": await _get(client, f"/api/v1/runs/{run['id']}/trajectory", after_seq=0),
            "failures": await _get(client, f"/api/v1/runs/{run['id']}/failures"),
            "evaluation": await _get(client, f"/api/v1/runs/{run['id']}/evaluation"),
        }
        for run in runs
    }
    tasks = await _get(client, "/api/v1/tasks")
    _save(out, runs, bundles, tasks)
    exported: list[dict[str, Any]] = runs
    return exported


def _save(out: Path, runs: list[dict[str, Any]], bundles: dict[str, Any], tasks: Any) -> None:
    if out.exists():
        shutil.rmtree(out)
    (out / "runs").mkdir(parents=True)
    for run_id, bundle in bundles.items():
        _write(out / "runs" / f"{run_id}.json", scrub(bundle))
    _write(out / "runs.json", scrub(runs))
    _write(out / "tasks.json", scrub(tasks))
    _write(out / "meta.json", {"exported_at": datetime.now(UTC).isoformat(timespec="seconds"), "runs": len(runs)})


def _write(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--api", default="http://localhost:8001", help="base URL of the running API")
    parser.add_argument("--run", action="append", default=[], help="export only this run id (repeatable)")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="output folder (replaced)")
    return parser.parse_args(argv)


async def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        async with httpx.AsyncClient(base_url=args.api, timeout=30) as client:
            runs = await export(client, args.out, args.run or None)
    except httpx.HTTPError as e:
        print(f"could not read from the API at {args.api}: {e}", file=sys.stderr)
        return 1
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 1
    print(f"exported {len(runs)} run(s) to {args.out}")
    for run in runs:
        print(f"  {run['id'][:8]}  {run['status']:10} {run.get('task_id') or run['task'][:40]}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
