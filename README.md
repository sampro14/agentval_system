# AgentEval — Multi-Agent Reliability & Evaluation Platform

AgentEval evaluates multi-agent AI systems by recording each run as a full, observable trajectory
instead of a single pass/fail result:

**Plan → Research → Draft → Execute → Validate → Diagnose → Repair → Re-execute → Evaluate**

Every agent step is stored as a structured event with its output, latency, and token usage. Generated
code runs in an isolated Docker sandbox, and the pass/fail result comes from that run, not from the
agent's own claim. The full design is in [docs/system-design.md](docs/system-design.md).

## Status

**Phases 1 (core runtime) and 2 (validation) are done.**

### Phase 1: core runtime

| Component | Where |
|---|---|
| FastAPI API: create a run, get a run, trajectory, failures, evaluation | `agenteval/apps/api` |
| LangGraph workflow with a bounded repair loop | `agenteval/orchestration/graph.py` |
| Planner, research, and coder agents | `agenteval/agents/*` |
| Docker sandbox: CPU, memory, PID, and time limits; no network; container removed after each run | `agenteval/execution/docker` |
| Postgres schema for runs, events, tool calls, evaluations, failures, and repairs, with Alembic migrations | `agenteval/storage` |
| Redis-backed worker that saves events as each node finishes | `agenteval/workers` |
| LLM provider layer: Claude, or a fake provider for offline runs and tests | `agenteval/llm` |

### Phase 2: validation

- **Execution evidence:** each attempt runs a `compile` check and then `pytest`, producing a JUnit
  report with per-test results. Generated tests that import Playwright run in a separate browser
  image with headless Chromium. If the sandbox itself fails, for example because an image is
  missing, that is recorded as evidence and doesn't crash the run.
- **Validator:** starts with checks based only on the evidence: functional correctness, tests
  present, and no regressions (a test that passed in an earlier attempt now failing). A model then
  reviews requirement coverage and test coverage, but only if those checks pass. The review can fail
  a run but can never override a failing test.
- **Failure analyzer:** attributes each failure to a category (§8.6) and the responsible agent. It
  tries rules first: sandbox/environment errors, compile errors, no tests, unavailable
  dependencies, regressions after a repair, and requirements missing from the plan or the code.
  When no rule is confident (confidence below 0.8), it asks the model instead. Every failure
  records whether a rule or the model attributed it, plus a recommended action, which the repairer
  uses.

The repairer is still simple: it re-runs the coder with the failure record as feedback. Phases 3–6
add the metrics engine, targeted recovery, the UI, and the research tooling.

## Quick start

Requires Python 3.12, [uv](https://docs.astral.sh/uv/), and Docker.

```bash
uv sync
cp .env.example .env                 # set ANTHROPIC_API_KEY, or AGENTEVAL_LLM_PROVIDER=fake
docker compose up -d postgres redis  # Postgres on host port 5433 (override with AGENTEVAL_PG_PORT)
docker compose build sandbox         # image that runs generated code
docker compose build sandbox-browser # optional, ~2GB: Playwright + Chromium for browser tests
uv run alembic upgrade head

uv run uvicorn agenteval.apps.api.main:app --port 8000   # terminal 1
uv run python -m agenteval.workers.run_worker            # terminal 2
```

Submit a run:

```bash
curl -X POST localhost:8000/api/v1/runs -H 'content-type: application/json' \
  -d '{"task": "Add an add() function with pytest tests", "max_repair_iterations": 2}'

curl localhost:8000/api/v1/runs/<id>/trajectory
curl localhost:8000/api/v1/runs/<id>/evaluation
```

The worker runs on the host rather than in Compose because it bind-mounts per-run workspaces into
sibling sandbox containers.

## Development

```bash
uv run ruff check . && uv run ruff format --check .
uv run mypy agenteval
uv run pytest
```

The tests use SQLite and scripted fake LLM and sandbox classes. The Docker integration tests run only
when the matching sandbox images are built. CI builds the base image but not the browser image.

## Roadmap

1. ~~Core runtime~~
2. ~~Validation: Pytest and Playwright execution, validator agent, failure analyzer~~
3. Evaluation: stage-level metrics engine, reliability scorecard, OpenTelemetry tracing
4. Recovery: targeted repair agent, retry policy, recovery metrics
5. UI: Next.js dashboard, run viewer, trajectory viewer
6. Research: benchmark dataset, baselines, ablation studies

## License

MIT
