"""Thin LLM provider abstraction so agents stay model-agnostic and testable offline."""

from __future__ import annotations

import asyncio
import json
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

import anthropic
import openai
from google import genai
from google.genai import types as genai_types

from agenteval.config import Settings


@dataclass
class LLMResponse:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0  # includes reasoning tokens: they are billed as output
    model: str = ""
    reasoning_tokens: int = 0  # the part of output_tokens the model spent thinking, when the provider reports it


class LLMTruncated(RuntimeError):
    """The model hit its output-token limit, so the reply is incomplete and must not be used."""


class LLMProvider(Protocol):
    async def complete(self, system: str, prompt: str) -> LLMResponse: ...


class AnthropicProvider:
    def __init__(self, settings: Settings, client: anthropic.AsyncAnthropic | None = None):
        self._settings = settings
        self._client = client or anthropic.AsyncAnthropic()

    async def complete(self, system: str, prompt: str) -> LLMResponse:
        response = await self._client.beta.messages.create(
            model=self._settings.resolved_model,
            max_tokens=self._settings.llm_max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
            thinking={"type": "adaptive"},
            output_config={"effort": self._settings.resolved_effort},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
        if response.stop_reason == "refusal":
            raise RuntimeError(f"LLM refused request: {response.stop_details}")
        if response.stop_reason == "max_tokens":
            raise LLMTruncated(f"Claude stopped at the {self._settings.llm_max_tokens:,}-token output limit")
        text = "".join(block.text for block in response.content if block.type == "text")
        return LLMResponse(
            text=text,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            model=response.model,
        )


def _reason_of(details: Any) -> str | None:
    """The `reason` of an incomplete-response detail, whether it is an object or a plain dict."""
    if details is None:
        return None
    return details.get("reason") if isinstance(details, dict) else getattr(details, "reason", None)


class OpenAIProvider:
    """OpenAI via the Responses API."""

    def __init__(self, settings: Settings, client: openai.AsyncOpenAI | None = None):
        self._settings = settings
        self._client = client or openai.AsyncOpenAI()

    async def complete(self, system: str, prompt: str) -> LLMResponse:
        response = await self._client.responses.create(
            model=self._settings.resolved_model,
            instructions=system,
            input=prompt,
            max_output_tokens=self._settings.llm_max_tokens,
        )
        detail = response.incomplete_details or response.error
        if _reason_of(response.incomplete_details) == "max_output_tokens":
            raise LLMTruncated(f"OpenAI stopped at the {self._settings.llm_max_tokens:,}-token output limit")
        if not response.output_text:
            raise RuntimeError(f"OpenAI returned no text (status={response.status}, detail={detail})")
        usage = response.usage
        reasoning = getattr(getattr(usage, "output_tokens_details", None), "reasoning_tokens", 0) or 0
        return LLMResponse(
            text=response.output_text,
            input_tokens=usage.input_tokens if usage else 0,
            output_tokens=usage.output_tokens if usage else 0,
            model=response.model,
            reasoning_tokens=reasoning,
        )


class DeepSeekProvider:
    """DeepSeek through its OpenAI-compatible Chat Completions endpoint."""

    def __init__(self, settings: Settings, client: openai.AsyncOpenAI | None = None):
        self._settings = settings
        self._client = client or openai.AsyncOpenAI(
            base_url=settings.deepseek_base_url, api_key=os.environ.get("DEEPSEEK_API_KEY")
        )

    async def complete(self, system: str, prompt: str) -> LLMResponse:
        response = await self._client.chat.completions.create(
            model=self._settings.resolved_model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            max_tokens=self._settings.llm_max_tokens,
        )
        choice = response.choices[0] if response.choices else None
        if choice is not None and choice.finish_reason == "length":
            raise LLMTruncated(f"DeepSeek stopped at the {self._settings.llm_max_tokens:,}-token output limit")
        if choice is None or not choice.message.content:
            reason = choice.finish_reason if choice else "no choices"
            raise RuntimeError(f"DeepSeek returned no text (finish_reason={reason})")
        usage = response.usage
        reasoning = getattr(getattr(usage, "completion_tokens_details", None), "reasoning_tokens", 0) or 0
        return LLMResponse(
            text=choice.message.content,
            input_tokens=usage.prompt_tokens if usage else 0,
            output_tokens=usage.completion_tokens if usage else 0,
            model=response.model,
            reasoning_tokens=reasoning,
        )


# llm_effort -> Gemini 3 `thinking_level`. Hidden reasoning is billed and counts toward the output limit,
# and a hard task can think for a minute or more, so the effort setting is the main speed/cost dial.
GEMINI_THINKING_LEVELS = {"low": "LOW", "medium": "MEDIUM", "high": "HIGH", "xhigh": "HIGH", "max": "HIGH"}
# Extra output tokens on top of llm_max_tokens when the model thinks a lot: thinking shares the limit
# with the answer, and without headroom a long think leaves the answer cut off (seen at 15k of 16k).
GEMINI_THINKING_HEADROOM = 24_000


class GeminiProvider:
    """Google Gemini via the google-genai SDK (key from GEMINI_API_KEY or GOOGLE_API_KEY)."""

    def __init__(self, settings: Settings, client: genai.Client | None = None):
        self._settings = settings
        self._client = client or genai.Client()

    def _config(self, system: str) -> genai_types.GenerateContentConfig:
        s = self._settings
        level = GEMINI_THINKING_LEVELS[s.resolved_effort] if s.resolved_model.startswith("gemini-3") else None
        headroom = GEMINI_THINKING_HEADROOM if level in (None, "MEDIUM", "HIGH") else 0
        return genai_types.GenerateContentConfig(
            system_instruction=system,
            max_output_tokens=s.llm_max_tokens + headroom,
            thinking_config=genai_types.ThinkingConfig(thinking_level=level) if level else None,
            automatic_function_calling=genai_types.AutomaticFunctionCallingConfig(disable=True),
        )

    async def complete(self, system: str, prompt: str) -> LLMResponse:
        config = self._config(system)
        response = await self._client.aio.models.generate_content(
            model=self._settings.resolved_model, contents=prompt, config=config
        )
        usage = response.usage_metadata
        thoughts = (getattr(usage, "thoughts_token_count", 0) or 0) if usage else 0
        visible = (usage.candidates_token_count or 0) if usage else 0
        finish = response.candidates[0].finish_reason if response.candidates else None
        if getattr(finish, "name", str(finish)) == "MAX_TOKENS":
            raise LLMTruncated(
                f"Gemini stopped at the {config.max_output_tokens:,}-token output limit: {thoughts:,} tokens of "
                f"hidden reasoning, {visible:,} of answer. "
                "Lower AGENTEVAL_LLM_EFFORT or raise AGENTEVAL_LLM_MAX_TOKENS."
            )
        if not response.text:
            raise RuntimeError(f"Gemini returned no text (reason={finish or response.prompt_feedback})")
        return LLMResponse(
            text=response.text,
            input_tokens=(usage.prompt_token_count or 0) if usage else 0,
            output_tokens=visible + thoughts,
            model=response.model_version or self._settings.resolved_model,
            reasoning_tokens=thoughts,
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


class RecordingProvider:
    """Wraps a provider and keeps every exchange, recorded *before* the caller parses the reply.

    So even when an agent fails to parse a bad response, the raw text is still available to inspect,
    and the exchanges can be saved as fixtures for `ReplayProvider`.
    """

    def __init__(self, inner: LLMProvider):
        self._inner = inner
        self.transcript: list[dict[str, Any]] = []

    async def complete(self, system: str, prompt: str) -> LLMResponse:
        response = await self._inner.complete(system, prompt)
        self.transcript.append(
            {
                "system": system,
                "prompt": prompt,
                "response": response.text,
                "input_tokens": response.input_tokens,
                "output_tokens": response.output_tokens,
                "model": response.model,
            }
        )
        return response


class ReplayProvider:
    """Serves recorded real responses in order, so tests can use genuine model output deterministically."""

    def __init__(self, exchanges: list[dict[str, Any]]):
        self._exchanges = list(exchanges)
        self.calls: list[tuple[str, str]] = []

    async def complete(self, system: str, prompt: str) -> LLMResponse:
        if len(self.calls) >= len(self._exchanges):
            raise RuntimeError(f"ReplayProvider has only {len(self._exchanges)} recorded exchange(s)")
        exchange = self._exchanges[len(self.calls)]
        self.calls.append((system, prompt))
        return LLMResponse(
            text=exchange["response"],
            input_tokens=exchange.get("input_tokens", 0),
            output_tokens=exchange.get("output_tokens", 0),
            model=exchange.get("model", "replay"),
        )


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
        return json.dumps({"files": []})
    if "validation agent" in system:
        return json.dumps({"requirements": [_DEMO_REQUIREMENT], "issues": []})
    if "failure analyzer" in system:
        return json.dumps(_DEMO_FAILURE)
    return json.dumps(_DEMO_DRAFT)


PROVIDERS: dict[str, Callable[[Settings], LLMProvider]] = {
    "anthropic": AnthropicProvider,
    "openai": OpenAIProvider,
    "gemini": GeminiProvider,
    "deepseek": DeepSeekProvider,
    "fake": lambda _settings: FakeProvider(demo_responder),
}


class TimeoutProvider:
    """Fails a provider call that takes too long, instead of letting one stalled request hang a run."""

    def __init__(self, inner: LLMProvider, seconds: float, label: str = ""):
        self.inner = inner
        self._seconds = seconds
        self._label = label

    async def complete(self, system: str, prompt: str) -> LLMResponse:
        try:
            return await asyncio.wait_for(self.inner.complete(system, prompt), self._seconds)
        except TimeoutError:
            raise TimeoutError(f"LLM call timed out after {self._seconds:g}s ({self._label})") from None


def get_provider(settings: Settings) -> LLMProvider:
    label = f"{settings.llm_provider}, {settings.resolved_model}"
    return TimeoutProvider(PROVIDERS[settings.llm_provider](settings), settings.llm_timeout_s, label)


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)
_OPEN_FENCE = re.compile(r"^\s*```(?:json)?\s*")
# strict=False accepts raw newlines and tabs inside JSON strings, which models often emit in code they embed.
_DECODER = json.JSONDecoder(strict=False)


def parse_json(text: str) -> Any:
    """Parse a JSON object from model output, however it is wrapped.

    Handles an unquoted reply, a code fence (closed or not), prose before or after the object, and raw
    newlines inside strings. If nothing works it raises json.JSONDecodeError describing the first `{`
    that failed to parse, which points at the real problem rather than at the start of the text.
    """
    candidates = [text.strip()]
    if match := _FENCE.search(text):
        candidates.append(match.group(1).strip())
    candidates.append(_OPEN_FENCE.sub("", text, count=1).strip())
    for candidate in candidates:
        try:
            value = _DECODER.decode(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value

    first_error: json.JSONDecodeError | None = None
    start = text.find("{")
    while start != -1:
        try:
            value, _ = _DECODER.raw_decode(text[start:])
        except json.JSONDecodeError as exc:
            first_error = first_error or exc
        else:
            if isinstance(value, dict):
                return value
        start = text.find("{", start + 1)
    raise first_error or json.JSONDecodeError("no JSON object found in the reply", text, 0)
