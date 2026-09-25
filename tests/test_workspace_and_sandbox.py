from __future__ import annotations

from pathlib import Path

import pytest

from agenteval.agents.executor.agent import execute
from agenteval.config import Settings
from agenteval.execution.docker.sandbox import DockerSandbox, docker_available
from agenteval.execution.workspace import list_files, write_file
from agenteval.llm.provider import FakeProvider
from agenteval.orchestration.deps import Deps


def test_write_file_records_diff(tmp_path: Path) -> None:
    first = write_file(tmp_path, "pkg/a.py", "x = 1\n")
    second = write_file(tmp_path, "pkg/a.py", "x = 2\n")

    assert first["created"] and first["lines_added"] == 1
    assert not second["created"] and (second["lines_added"], second["lines_removed"]) == (1, 1)
    assert list_files(tmp_path) == ["pkg/a.py"]


def test_write_file_rejects_path_escape(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        write_file(tmp_path, "../evil.py", "")


@pytest.mark.skipif(not docker_available(), reason="docker not available")
async def test_docker_sandbox_isolation(tmp_path: Path) -> None:
    sandbox = DockerSandbox(Settings(sandbox_image="python:3.12-slim", sandbox_timeout_s=60))
    (tmp_path / "hello.py").write_text("print('hi')\n")

    ok = await sandbox.run(str(tmp_path), "python hello.py")
    assert ok.passed and ok.stdout.strip() == "hi"

    offline = await sandbox.run(
        str(tmp_path), "python -c \"import urllib.request; urllib.request.urlopen('https://example.com', timeout=3)\""
    )
    assert not offline.passed


def _image_available(tag: str) -> bool:
    if not docker_available():
        return False
    import docker

    try:
        docker.from_env().images.get(tag)
        return True
    except docker.errors.ImageNotFound:
        return False


def _deps(settings: Settings) -> Deps:
    return Deps(settings=settings, llm=FakeProvider(), sandbox=DockerSandbox(settings))


@pytest.mark.skipif(not _image_available(Settings().sandbox_image), reason="sandbox image not built")
async def test_executor_collects_junit_evidence(tmp_path: Path) -> None:
    (tmp_path / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    (tmp_path / "test_calc.py").write_text(
        "from calc import add\n\n"
        "def test_ok():\n    assert add(2, 3) == 5\n\n"
        "def test_bad():\n    assert add(2, 2) == 5\n"
    )
    state = {"run_id": "r", "task": "t", "workspace": str(tmp_path)}

    record = (await execute(state, _deps(Settings())))["execution_results"][-1]  # type: ignore[arg-type]

    assert [c["name"] for c in record["checks"]] == ["compile", "pytest"]
    assert not record["passed"]
    assert (record["tests"]["passed"], record["tests"]["failed"]) == (1, 1)
    assert record["tests"]["failures"][0]["test"].endswith("test_bad")


@pytest.mark.skipif(not _image_available(Settings().sandbox_browser_image), reason="browser image not built")
async def test_playwright_runs_in_browser_image(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("<h1 id='t'>Hello AgentEval</h1>")
    (tmp_path / "test_ui.py").write_text(
        "from pathlib import Path\n"
        "from playwright.sync_api import Page\n\n"
        "def test_heading(page: Page):\n"
        "    page.goto((Path(__file__).parent / 'index.html').resolve().as_uri())\n"
        "    assert page.inner_text('#t') == 'Hello AgentEval'\n"
    )
    state = {"run_id": "r", "task": "t", "workspace": str(tmp_path)}

    record = (await execute(state, _deps(Settings())))["execution_results"][-1]  # type: ignore[arg-type]

    assert record["checks"][1]["image"] == Settings().sandbox_browser_image
    assert record["passed"], record["checks"][1]["stdout"]
    assert record["tests"]["passed"] == 1


async def test_missing_image_is_reported_not_raised(tmp_path: Path) -> None:
    if not docker_available():
        pytest.skip("docker not available")
    result = await DockerSandbox(Settings()).run(str(tmp_path), "true", image="agenteval-does-not-exist:latest")
    assert result.exit_code == 125 and "sandbox error" in result.stderr
