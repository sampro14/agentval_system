from __future__ import annotations

from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agenteval.apps.api.main import create_app
from agenteval.config import Settings, get_settings
from agenteval.llm.provider import (
    GEMINI_THINKING_HEADROOM,
    AnthropicProvider,
    DeepSeekProvider,
    FakeProvider,
    GeminiProvider,
    LLMTruncated,
    OpenAIProvider,
    TimeoutProvider,
    get_provider,
    parse_json,
)
from agenteval.storage.repositories.runs import RunRepository


class Recorder:
    """Async stand-in for an SDK method: records kwargs and returns a canned response."""

    def __init__(self, response: Any):
        self.response = response
        self.kwargs: dict[str, Any] = {}

    async def __call__(self, **kwargs: Any) -> Any:
        self.kwargs = kwargs
        return self.response


def openai_client(output_text: str) -> tuple[Any, Recorder]:
    response = SimpleNamespace(
        output_text=output_text,
        usage=SimpleNamespace(input_tokens=11, output_tokens=7),
        model="gpt-6-sol-2026",
        status="completed" if output_text else "incomplete",
        incomplete_details=None if output_text else {"reason": "max_output_tokens"},
        error=None,
    )
    create = Recorder(response)
    return SimpleNamespace(responses=SimpleNamespace(create=create)), create


def deepseek_client(content: str | None) -> tuple[Any, Recorder]:
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content), finish_reason="stop")],
        usage=SimpleNamespace(prompt_tokens=13, completion_tokens=5),
        model="deepseek-v4-pro",
    )
    create = Recorder(response)
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))), create


def gemini_client(text: str | None) -> tuple[Any, Recorder]:
    response = SimpleNamespace(
        text=text,
        usage_metadata=SimpleNamespace(prompt_token_count=17, candidates_token_count=3),
        model_version="gemini-3.8-flash-001",
        candidates=[SimpleNamespace(finish_reason="SAFETY")],
        prompt_feedback=None,
    )
    generate = Recorder(response)
    return SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate))), generate


def test_default_model_per_provider() -> None:
    assert Settings(llm_provider="openai").resolved_model == "gpt-6-sol"
    assert Settings(llm_provider="gemini").resolved_model == "gemini-3.8-flash"
    assert Settings(llm_provider="deepseek").resolved_model == "deepseek-v4-pro"
    assert Settings(llm_provider="anthropic").resolved_model == "claude-opus-5"
    assert Settings(llm_provider="openai", llm_model="gpt-6-luna").resolved_model == "gpt-6-luna"


def test_get_provider_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "DEEPSEEK_API_KEY"):
        monkeypatch.setenv(var, "test-key")
    expected = {
        "anthropic": AnthropicProvider,
        "openai": OpenAIProvider,
        "gemini": GeminiProvider,
        "deepseek": DeepSeekProvider,
        "fake": FakeProvider,
    }
    for name, cls in expected.items():
        provider = get_provider(Settings(llm_provider=name))  # type: ignore[arg-type]
        assert isinstance(provider, TimeoutProvider) and isinstance(provider.inner, cls)


async def test_openai_maps_request_and_usage() -> None:
    client, create = openai_client('{"ok": true}')
    response = await OpenAIProvider(Settings(llm_provider="openai", llm_max_tokens=500), client).complete("sys", "hi")

    assert create.kwargs == {"model": "gpt-6-sol", "instructions": "sys", "input": "hi", "max_output_tokens": 500}
    assert (response.text, response.input_tokens, response.output_tokens) == ('{"ok": true}', 11, 7)
    assert response.model == "gpt-6-sol-2026"


async def test_deepseek_maps_request_and_usage() -> None:
    client, create = deepseek_client("done")
    response = await DeepSeekProvider(Settings(llm_provider="deepseek"), client).complete("sys", "hi")

    assert create.kwargs["model"] == "deepseek-v4-pro"
    assert create.kwargs["messages"] == [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}]
    assert (response.text, response.input_tokens, response.output_tokens) == ("done", 13, 5)


async def test_gemini_maps_request_and_usage() -> None:
    client, generate = gemini_client("done")
    response = await GeminiProvider(Settings(llm_provider="gemini", llm_max_tokens=500), client).complete("sys", "hi")

    assert generate.kwargs["model"] == "gemini-3.8-flash" and generate.kwargs["contents"] == "hi"
    config = generate.kwargs["config"]
    assert config.system_instruction == "sys" and config.max_output_tokens == 500  # low effort: no headroom
    assert config.thinking_config.thinking_level.name == "LOW"
    assert (response.text, response.input_tokens, response.output_tokens) == ("done", 17, 3)
    assert response.model == "gemini-3.8-flash-001"


@pytest.mark.parametrize(
    ("provider_cls", "client_factory", "empty", "match"),
    [
        (OpenAIProvider, openai_client, "", "output limit"),
        (DeepSeekProvider, deepseek_client, None, "finish_reason=stop"),
        (GeminiProvider, gemini_client, None, "SAFETY"),
    ],
)
async def test_empty_response_raises(provider_cls: Any, client_factory: Any, empty: Any, match: str) -> None:
    client, _ = client_factory(empty)
    with pytest.raises(RuntimeError, match=match):
        await provider_cls(Settings(), client).complete("sys", "hi")


# --- per-run provider override through the API --------------------------------------------------


class MemoryQueue:
    async def enqueue(self, run_id: str) -> None:
        pass


@pytest.fixture
async def http(
    sessions: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncClient]:
    monkeypatch.setenv("AGENTEVAL_LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("AGENTEVAL_LLM_MODEL", "claude-opus-5")
    get_settings.cache_clear()
    app = create_app(use_lifespan=False)
    app.state.sessions, app.state.queue = sessions, MemoryQueue()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client
    get_settings.cache_clear()


async def test_run_provider_override_uses_provider_default(
    http: AsyncClient, sessions: async_sessionmaker[AsyncSession]
) -> None:
    run = (await http.post("/api/v1/runs", json={"task": "t", "provider": "gemini"})).json()
    assert (run["provider"], run["model"]) == ("gemini", "gemini-3.8-flash")  # not the global claude model

    async with sessions() as session:
        stored = await RunRepository(session).get(run["id"])
    assert stored is not None
    assert stored.model_config_["llm_provider"] == "gemini"


async def test_run_defaults_and_explicit_model(http: AsyncClient) -> None:
    default = (await http.post("/api/v1/runs", json={"task": "t"})).json()
    assert (default["provider"], default["model"]) == ("anthropic", "claude-opus-5")

    explicit = (await http.post("/api/v1/runs", json={"task": "t", "provider": "openai", "model": "gpt-6-luna"})).json()
    assert (explicit["provider"], explicit["model"]) == ("openai", "gpt-6-luna")


async def test_unknown_provider_rejected(http: AsyncClient) -> None:
    assert (await http.post("/api/v1/runs", json={"task": "t", "provider": "llama"})).status_code == 422


# --- reasoning budget, truncation and cost accounting ---------------------------------------------------


def gemini_reply(text: str = "done", finish: str = "STOP", thoughts: int | None = None, visible: int = 3) -> Any:
    return SimpleNamespace(
        text=text,
        usage_metadata=SimpleNamespace(
            prompt_token_count=17, candidates_token_count=visible, thoughts_token_count=thoughts
        ),
        model_version="gemini-3.8-flash-001",
        candidates=[SimpleNamespace(finish_reason=SimpleNamespace(name=finish))],
        prompt_feedback=None,
    )


def gemini_with(reply: Any) -> tuple[Any, Recorder]:
    generate = Recorder(reply)
    return SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate))), generate


@pytest.mark.parametrize(
    ("effort", "level", "headroom"),
    [("low", "LOW", 0), ("medium", "MEDIUM", GEMINI_THINKING_HEADROOM), ("high", "HIGH", GEMINI_THINKING_HEADROOM)]
    + [("xhigh", "HIGH", GEMINI_THINKING_HEADROOM), ("max", "HIGH", GEMINI_THINKING_HEADROOM)],
)
async def test_gemini_effort_maps_to_thinking_level(effort: Any, level: str, headroom: int) -> None:
    client, generate = gemini_with(gemini_reply())
    settings = Settings(llm_provider="gemini", llm_effort=effort, llm_max_tokens=1000)

    await GeminiProvider(settings, client).complete("sys", "hi")

    config = generate.kwargs["config"]
    assert config.thinking_config.thinking_level.name == level
    assert config.max_output_tokens == 1000 + headroom  # low thinking needs no room; the others do


async def test_gemini_older_models_get_no_thinking_level() -> None:
    client, generate = gemini_with(gemini_reply())
    settings = Settings(llm_provider="gemini", llm_model="gemini-2.5-flash", llm_effort="low")

    await GeminiProvider(settings, client).complete("sys", "hi")

    assert generate.kwargs["config"].thinking_config is None


async def test_gemini_reasoning_tokens_are_counted_because_they_are_billed() -> None:
    client, _ = gemini_with(gemini_reply(thoughts=15_000, visible=600))
    response = await GeminiProvider(Settings(llm_provider="gemini"), client).complete("sys", "hi")

    assert response.output_tokens == 15_600 and response.reasoning_tokens == 15_000


async def test_gemini_truncation_is_a_clear_error_not_a_half_reply() -> None:
    client, _ = gemini_with(
        gemini_reply(text="5 (e.g. `t0 + timedelta", finish="MAX_TOKENS", thoughts=15_357, visible=639)
    )

    with pytest.raises(LLMTruncated, match=r"15,357 tokens of hidden reasoning, 639 of answer.*AGENTEVAL_LLM_EFFORT"):
        await GeminiProvider(Settings(llm_provider="gemini"), client).complete("sys", "hi")


async def test_truncation_is_detected_for_every_provider() -> None:
    openai_reply = SimpleNamespace(
        output_text='{"half":',
        usage=None,
        model="m",
        status="incomplete",
        error=None,
        incomplete_details=SimpleNamespace(reason="max_output_tokens"),
    )
    client = SimpleNamespace(responses=SimpleNamespace(create=Recorder(openai_reply)))
    with pytest.raises(LLMTruncated, match="OpenAI stopped"):
        await OpenAIProvider(Settings(llm_provider="openai"), client).complete("s", "p")  # type: ignore[arg-type]

    deepseek_reply = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content='{"half":'), finish_reason="length")],
        usage=None,
        model="m",
    )
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=Recorder(deepseek_reply))))
    with pytest.raises(LLMTruncated, match="DeepSeek stopped"):
        await DeepSeekProvider(Settings(llm_provider="deepseek"), client).complete("s", "p")  # type: ignore[arg-type]

    claude_reply = SimpleNamespace(
        stop_reason="max_tokens",
        stop_details=None,
        content=[SimpleNamespace(type="text", text='{"half":')],
        usage=SimpleNamespace(input_tokens=1, output_tokens=1),
        model="m",
    )
    client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=Recorder(claude_reply))))
    with pytest.raises(LLMTruncated, match="Claude stopped"):
        await AnthropicProvider(Settings(llm_provider="anthropic"), client).complete("s", "p")  # type: ignore[arg-type]


async def test_openai_and_deepseek_report_reasoning_tokens() -> None:
    reply = SimpleNamespace(
        output_text="ok",
        model="m",
        status="completed",
        incomplete_details=None,
        error=None,
        usage=SimpleNamespace(
            input_tokens=5, output_tokens=900, output_tokens_details=SimpleNamespace(reasoning_tokens=800)
        ),
    )
    client = SimpleNamespace(responses=SimpleNamespace(create=Recorder(reply)))
    response = await OpenAIProvider(Settings(llm_provider="openai"), client).complete("s", "p")  # type: ignore[arg-type]
    assert (response.output_tokens, response.reasoning_tokens) == (900, 800)


# --- tolerant JSON parsing ------------------------------------------------------------------------------------


def test_parse_json_accepts_raw_newlines_inside_strings() -> None:
    """Models often put real line breaks inside the code they embed; strict JSON rejects them."""
    text = '```json\n{"summary": "s", "files": [{"path": "a.py", "content": "def f():\n    return 1\n"}]}'
    parsed = parse_json(text)  # note: also no closing fence, as seen in a real Gemini reply
    assert parsed["files"][0]["content"] == "def f():\n    return 1\n"


def test_parse_json_survives_backticks_inside_a_string_value() -> None:
    text = 'Here:\n```json\n{"files": [{"path": "README", "content": "use ```code``` blocks"}]}\n```\nDone.'
    assert parse_json(text)["files"][0]["content"] == "use ```code``` blocks"


def test_parse_json_error_points_at_the_real_problem() -> None:
    text = '```json\n{"a": 1, "b": [1, 2,, 3]}\n```'
    with pytest.raises(Exception) as excinfo:
        parse_json(text)
    assert "column 1 (char 0)" not in str(excinfo.value)  # not the misleading start-of-text error
    assert "char 20" in str(excinfo.value)  # the second comma, counted from the start of the object


def test_effort_defaults_per_provider_and_explicit_setting_wins() -> None:
    assert Settings(llm_provider="gemini").resolved_effort == "low"  # measured: same results as high, ~9x cheaper
    for provider in ("anthropic", "openai", "deepseek", "fake"):
        assert Settings(llm_provider=provider).resolved_effort == "high"  # type: ignore[arg-type]
    assert Settings(llm_provider="gemini", llm_effort="high").resolved_effort == "high"


async def test_gemini_defaults_to_low_thinking_and_high_when_asked() -> None:
    client, generate = gemini_with(gemini_reply())
    await GeminiProvider(Settings(llm_provider="gemini"), client).complete("s", "p")
    assert generate.kwargs["config"].thinking_config.thinking_level.name == "LOW"

    await GeminiProvider(Settings(llm_provider="gemini", llm_effort="high"), client).complete("s", "p")
    assert generate.kwargs["config"].thinking_config.thinking_level.name == "HIGH"


async def test_anthropic_sends_the_resolved_effort() -> None:
    reply = SimpleNamespace(
        stop_reason="end_turn",
        stop_details=None,
        content=[SimpleNamespace(type="text", text="ok")],
        usage=SimpleNamespace(input_tokens=1, output_tokens=1),
        model="m",
    )
    create = Recorder(reply)
    client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=create)))

    await AnthropicProvider(Settings(llm_provider="anthropic"), client).complete("s", "p")  # type: ignore[arg-type]

    assert create.kwargs["output_config"] == {"effort": "high"}
