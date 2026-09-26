from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from agenteval.config import Settings
from agenteval.dev.stage import parse_args, render, render_table, run_stage, save_fixture
from agenteval.llm.provider import FakeProvider, LLMResponse, RecordingProvider, ReplayProvider, TimeoutProvider
from agenteval.observability.events import NodeError
from tests.conftest import FakeSandbox, scripted_llm

TASK = "change-password"


async def test_recording_provider_keeps_every_exchange() -> None:
    recorder = RecordingProvider(FakeProvider(lambda s, p: f"{s}|{p}"))
    await recorder.complete("sys", "one")
    await recorder.complete("sys", "two")

    assert [(e["prompt"], e["response"]) for e in recorder.transcript] == [("one", "sys|one"), ("two", "sys|two")]
    assert recorder.transcript[0]["model"] == "fake"


async def test_replay_provider_serves_recorded_responses_in_order() -> None:
    replay = ReplayProvider([{"response": "a", "input_tokens": 3}, {"response": "b", "model": "m"}])

    first, second = await replay.complete("s", "p"), await replay.complete("s", "p")

    assert (first.text, first.input_tokens, second.text, second.model) == ("a", 3, "b", "m")
    with pytest.raises(RuntimeError, match="only 2 recorded"):
        await replay.complete("s", "p")


async def test_stages_chain_through_saved_state(tmp_path: Path, settings: Settings) -> None:
    llm, sandbox = scripted_llm(), FakeSandbox([0])

    planned = await run_stage("planner", TASK, llm=llm, sandbox=sandbox, settings=settings, dev_dir=tmp_path)
    assert planned.ok and planned.started_fresh and planned.event["event_type"] == "PLAN_CREATED"
    assert (tmp_path / TASK / "workspace" / "authkit" / "users.py").is_file()  # seeded with the real sample app

    researched = await run_stage("research", TASK, llm=llm, sandbox=sandbox, settings=settings, dev_dir=tmp_path)
    assert researched.ok and not researched.started_fresh
    state = json.loads((tmp_path / TASK / "state.json").read_text())
    assert state["plan"]["goal"] == "add function"  # the planner's output reached the next stage
    assert [e["agent"] for e in state["trajectory"]] == ["planner", "research"]

    drafted = await run_stage("draft", TASK, llm=llm, sandbox=sandbox, settings=settings, dev_dir=tmp_path)
    assert drafted.ok and (tmp_path / TASK / "workspace" / "calc.py").is_file()

    executed = await run_stage("execute", TASK, llm=llm, sandbox=sandbox, settings=settings, dev_dir=tmp_path)
    assert executed.ok and executed.event["output"]["tests"]["passed"] == 1


async def test_planner_always_starts_fresh(tmp_path: Path, settings: Settings) -> None:
    llm, sandbox = scripted_llm(), FakeSandbox([0])
    await run_stage("planner", TASK, llm=llm, sandbox=sandbox, settings=settings, dev_dir=tmp_path)
    await run_stage("draft", TASK, llm=llm, sandbox=sandbox, settings=settings, dev_dir=tmp_path)
    assert (tmp_path / TASK / "workspace" / "calc.py").is_file()

    again = await run_stage("planner", TASK, llm=llm, sandbox=sandbox, settings=settings, dev_dir=tmp_path)

    assert again.started_fresh
    assert not (tmp_path / TASK / "workspace" / "calc.py").exists()  # the earlier draft was discarded
    state = json.loads((tmp_path / TASK / "state.json").read_text())
    assert [e["agent"] for e in state["trajectory"]] == ["planner"]


async def test_stage_without_earlier_state_is_flagged(tmp_path: Path, settings: Settings) -> None:
    result = await run_stage(
        "research", TASK, llm=scripted_llm(), sandbox=FakeSandbox([0]), settings=settings, dev_dir=tmp_path
    )
    assert result.started_fresh and "no earlier state" in result.notes[0]


async def test_bad_response_is_reported_with_the_raw_text(tmp_path: Path, settings: Settings) -> None:
    result = await run_stage(
        "planner",
        TASK,
        llm=FakeProvider(lambda s, p: "Sure! Here is the plan, but no JSON"),
        sandbox=FakeSandbox([0]),
        settings=settings,
        dev_dir=tmp_path,
    )

    assert not result.ok and result.error and "planner failed" in result.error
    assert result.event["event_type"] == "PLANNER_ERROR"
    # The raw reply is kept even though parsing failed, so it can be inspected.
    saved = json.loads((tmp_path / TASK / "planner.transcript.json").read_text())
    assert saved[0]["response"].startswith("Sure!")
    assert "FAILED" in render(result, brief=True) and "ERROR:" in render(result)
    assert isinstance(NodeError, type)


async def test_render_and_fixture(tmp_path: Path, settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("agenteval.dev.stage.FIXTURE_DIR", tmp_path / "fixtures")
    result = await run_stage(
        "planner", TASK, llm=scripted_llm(), sandbox=FakeSandbox([0]), settings=settings, dev_dir=tmp_path
    )

    text = render(result)
    assert "LLM call 1: system prompt" in text and "PLAN_CREATED" in text and "raw response" in text
    assert "tokens in/out=" in render(result, brief=True)
    assert "total" in render_table([result])

    fixture = save_fixture(result)
    assert fixture.name == f"planner__{TASK}.json"
    replay = ReplayProvider(json.loads(fixture.read_text()))
    assert (await replay.complete("s", "p")).text == result.transcript[0]["response"]


def test_cli_defaults() -> None:
    args = parse_args(["planner", "--task", "password-reset"])
    assert (args.provider, args.fresh, args.brief) == ("gemini", False, False)
    with pytest.raises(SystemExit):
        parse_args(["not-an-agent", "--task", "x"])


# --- a stalled provider must fail, not hang -----------------------------------------------------


class NeverAnswers:
    async def complete(self, system: str, prompt: str) -> LLMResponse:
        await asyncio.sleep(3600)
        raise AssertionError("unreachable")


async def test_timeout_provider_fails_a_stalled_call_with_a_clear_message() -> None:
    provider = TimeoutProvider(NeverAnswers(), 0.05, "gemini, gemini-3.8-flash")

    with pytest.raises(TimeoutError, match=r"timed out after 0\.05s \(gemini, gemini-3.8-flash\)"):
        await provider.complete("sys", "hi")


async def test_timeout_provider_passes_fast_calls_through() -> None:
    provider = TimeoutProvider(FakeProvider(lambda s, p: "ok"), 5)
    assert (await provider.complete("s", "p")).text == "ok"


async def test_stalled_planner_is_recorded_as_an_error_not_a_hang(tmp_path: Path, settings: Settings) -> None:
    result = await asyncio.wait_for(
        run_stage(
            "planner",
            TASK,
            llm=TimeoutProvider(NeverAnswers(), 0.05, "test"),
            sandbox=FakeSandbox([0]),
            settings=settings,
            dev_dir=tmp_path,
        ),
        timeout=10,
    )

    assert not result.ok and "timed out" in (result.error or "")
    assert result.event["event_type"] == "PLANNER_ERROR"


# --- re-running early stages, and the coder's scorecard -------------------------------------------------


async def _through_draft(tmp_path: Path, settings: Settings, sandbox: FakeSandbox) -> None:
    llm = scripted_llm()
    for agent in ("planner", "research", "draft"):
        await run_stage(agent, TASK, llm=llm, sandbox=sandbox, settings=settings, dev_dir=tmp_path)


async def test_draft_is_scored_against_the_hidden_acceptance_test(tmp_path: Path, settings: Settings) -> None:
    sandbox = FakeSandbox([{"test_a": "passed", "test_b": "passed"}])
    llm = scripted_llm()
    await run_stage("planner", TASK, llm=llm, sandbox=sandbox, settings=settings, dev_dir=tmp_path)
    await run_stage("research", TASK, llm=llm, sandbox=sandbox, settings=settings, dev_dir=tmp_path)

    drafted = await run_stage("draft", TASK, llm=llm, sandbox=sandbox, settings=settings, dev_dir=tmp_path)

    card = drafted.scorecard
    assert card is not None and card["kind"] == "draft"
    assert (card["acceptance_passed"], card["acceptance_tests_passed"], card["acceptance_tests_total"]) == (True, 2, 2)
    assert card["files_written"] == ["calc.py"]
    assert not (tmp_path / TASK / "workspace" / ".acceptance").exists()  # the hidden test never stays behind
    assert "acceptance=PASS (2/2 hidden tests)" in render(drafted, brief=True)


async def test_a_failing_acceptance_test_is_reported_as_a_failed_draft(tmp_path: Path, settings: Settings) -> None:
    sandbox = FakeSandbox([{"test_a": "passed", "test_b": "failed"}])
    await _through_draft(tmp_path, settings, sandbox)
    saved = json.loads((tmp_path / TASK / "draft.result.json").read_text())

    assert saved["ok"] is True  # the agent ran fine; the verdict is about the code
    assert saved["scorecard"]["acceptance_passed"] is False and saved["scorecard"]["acceptance_tests_passed"] == 1


async def test_rerunning_the_coder_starts_from_the_untouched_app(tmp_path: Path, settings: Settings) -> None:
    sandbox = FakeSandbox([{"t": "passed"}])
    await _through_draft(tmp_path, settings, sandbox)
    workspace = tmp_path / TASK / "workspace"
    (workspace / "authkit" / "users.py").write_text("# an earlier draft that broke the file\n")
    await run_stage("execute", TASK, llm=scripted_llm(), sandbox=sandbox, settings=settings, dev_dir=tmp_path)

    again = await run_stage("draft", TASK, llm=scripted_llm(), sandbox=sandbox, settings=settings, dev_dir=tmp_path)

    assert "workspace reset" in again.notes[0]
    assert "def register(" in (workspace / "authkit" / "users.py").read_text()  # restored, not built upon
    state = json.loads((tmp_path / TASK / "state.json").read_text())
    assert len(state["artifacts"]) == 1  # this draft only, not the earlier one as well
    assert [e["agent"] for e in state["trajectory"]] == ["planner", "research", "draft"]  # execute was forgotten
    assert not (tmp_path / TASK / "execute.result.json").exists()
    assert (tmp_path / TASK / "research.result.json").exists()  # earlier stages are kept


async def test_rerunning_research_discards_the_draft(tmp_path: Path, settings: Settings) -> None:
    sandbox = FakeSandbox([{"t": "passed"}])
    await _through_draft(tmp_path, settings, sandbox)

    await run_stage("research", TASK, llm=scripted_llm(), sandbox=sandbox, settings=settings, dev_dir=tmp_path)

    assert not (tmp_path / TASK / "workspace" / "calc.py").exists()
    state = json.loads((tmp_path / TASK / "state.json").read_text())
    assert "artifacts" not in state and [e["agent"] for e in state["trajectory"]] == ["planner", "research"]
    assert not (tmp_path / TASK / "draft.result.json").exists()
