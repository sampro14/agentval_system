"""Every agent step emits a structured event (design doc §13) — "observable by default"."""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from agenteval.llm.provider import LLMProvider, LLMResponse
from agenteval.observability.tracing import span
from agenteval.orchestration.state import AgentEvalState

if TYPE_CHECKING:
    from agenteval.orchestration.deps import Deps

NodeFn = Callable[[AgentEvalState, "Deps"], Awaitable[dict[str, Any]]]


@dataclass
class MeteredLLM:
    """Wraps a provider to count calls and tokens for a single agent step."""

    inner: LLMProvider
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    responses: list[LLMResponse] = field(default_factory=list)

    async def complete(self, system: str, prompt: str) -> LLMResponse:
        response = await self.inner.complete(system, prompt)
        self.calls += 1
        self.input_tokens += response.input_tokens
        self.output_tokens += response.output_tokens
        return response


class NodeError(Exception):
    def __init__(self, event: dict[str, Any], cause: BaseException):
        super().__init__(f"{event['agent']} failed: {cause}")
        self.event = event


def make_event(
    run_id: str,
    agent: str,
    event_type: str,
    status: str,
    output: dict[str, Any],
    latency_ms: int,
    llm: MeteredLLM,
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "event_id": f"evt_{uuid.uuid4().hex[:12]}",
        "timestamp": datetime.now(UTC).isoformat(),
        "agent": agent,
        "event_type": event_type,
        "status": status,
        "output": output,
        "latency_ms": latency_ms,
        "token_usage": {"input": llm.input_tokens, "output": llm.output_tokens, "llm_calls": llm.calls},
    }


def traced(agent: str, fn: NodeFn, deps: Deps) -> Callable[[AgentEvalState], Awaitable[dict[str, Any]]]:
    """Wrap a node so it records latency, token usage and outcome as a trajectory event.

    A node may return an `event` key ({"event_type": ..., "output": ...}) to describe itself;
    it is stripped from the state update and folded into the recorded event.
    """

    async def node(state: AgentEvalState) -> dict[str, Any]:
        llm = MeteredLLM(deps.llm)
        start = time.perf_counter()
        with span(agent, run_id=state["run_id"]):
            try:
                update = await fn(state, replace(deps, llm=llm))
            except Exception as exc:
                latency = int((time.perf_counter() - start) * 1000)
                event = make_event(
                    state["run_id"], agent, f"{agent.upper()}_ERROR", "error", {"error": repr(exc)}, latency, llm
                )
                raise NodeError(event, exc) from exc
        latency = int((time.perf_counter() - start) * 1000)
        described = update.pop("event", {})
        event = make_event(
            state["run_id"],
            agent,
            described.get("event_type", f"{agent.upper()}_COMPLETED"),
            described.get("status", "ok"),
            described.get("output", {}),
            latency,
            llm,
        )
        return {**update, "trajectory": [event]}

    return node
