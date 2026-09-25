"""Thin LLM provider abstraction so agents stay model-agnostic and testable offline."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

import anthropic

from agenteval.config import Settings


@dataclass
class LLMResponse:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""


class LLMProvider(Protocol):
    async def complete(self, system: str, prompt: str) -> LLMResponse: ...


class AnthropicProvider:
    def __init__(self, settings: Settings, client: anthropic.AsyncAnthropic | None = None):
        self._settings = settings
        self._client = client or anthropic.AsyncAnthropic()

    async def complete(self, system: str, prompt: str) -> LLMResponse:
        response = await self._client.beta.messages.create(
            model=self._settings.llm_model,
            max_tokens=self._settings.llm_max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
            thinking={"type": "adaptive"},
            output_config={"effort": self._settings.llm_effort},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
        if response.stop_reason == "refusal":
            raise RuntimeError(f"LLM refused request: {response.stop_details}")
        text = "".join(block.text for block in response.content if block.type == "text")
        return LLMResponse(
            text=text,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            model=response.model,
        )


class FakeProvider:
    """Deterministic provider for tests and offline runs.

    `responder` maps (system, prompt) to response text; by default it echoes an empty JSON object.
    """

    def __init__(self, responder: Callable[[str, str], str] | None = None):
        self._responder = responder or (lambda _system, _prompt: "{}")
        self.calls: list[tuple[str, str]] = []

    async def complete(self, system: str, prompt: str) -> LLMResponse:
        self.calls.append((system, prompt))
        text = self._responder(system, prompt)
        return LLMResponse(text=text, input_tokens=len(prompt) // 4, output_tokens=len(text) // 4, model="fake")


_DEMO_PLAN = {
    "goal": "Add an add() function with tests",
    "tasks": [
        {"id": "T1", "description": "Implement add() in calc.py", "dependencies": []},
        {"id": "T2", "description": "Test add() with pytest", "dependencies": ["T1"]},
    ],
}
_DEMO_DRAFT = {
    "summary": "Implemented add() and a pytest test",
    "files": [
        {"path": "calc.py", "content": "def add(a, b):\n    return a + b\n"},
        {"path": "test_calc.py", "content": "from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n"},
    ],
}

_DEMO_REQUIREMENT = {
    "requirement": "add() returns the sum",
    "implemented": True,
    "tested": True,
    "evidence": "test_add",
}
_DEMO_FAILURE = {
    "failure_type": "CODE_GENERATION",
    "root_cause": "Implementation does not satisfy the tests",
    "confidence": 0.7,
    "evidence": ["failing assertion"],
    "recommended_action": "Fix add()",
}


def demo_responder(system: str, _prompt: str) -> str:
    """Canned responses so `llm_provider=fake` runs the whole workflow offline."""
    if "planning agent" in system:
        return json.dumps(_DEMO_PLAN)
    if "research agent" in system:
        return json.dumps({"relevant_files": [], "notes": "Empty repository"})
    if "validation agent" in system:
        return json.dumps({"requirements": [_DEMO_REQUIREMENT], "issues": []})
    if "failure analyzer" in system:
        return json.dumps(_DEMO_FAILURE)
    return json.dumps(_DEMO_DRAFT)


def get_provider(settings: Settings) -> LLMProvider:
    if settings.llm_provider == "fake":
        return FakeProvider(demo_responder)
    return AnthropicProvider(settings)


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def parse_json(text: str) -> Any:
    """Parse a JSON object from model output, tolerating a surrounding code fence."""
    match = _FENCE.search(text)
    return json.loads(match.group(1) if match else text)
