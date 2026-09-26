from __future__ import annotations

import shutil
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agenteval.apps.api.main import create_app
from agenteval.config import Settings
from agenteval.datasets import DATASETS_ROOT, SAMPLE_APP, Task, get_task, load_tasks
from agenteval.evaluation.acceptance import ACCEPTANCE_DIR, run_acceptance
from agenteval.execution.docker.sandbox import DockerSandbox, docker_available
from agenteval.execution.reports import parse_junit
from agenteval.execution.workspace import list_files, seed_workspace
from agenteval.storage.repositories.runs import RunRepository, TrajectoryRepository
from agenteval.workers.run_worker import execute_run
from tests.conftest import FakeSandbox, scripted_llm

TASK_IDS = [t.id for t in load_tasks()]


# --- the data itself ----------------------------------------------------------------------------


def test_task_set_is_complete() -> None:
    assert TASK_IDS == [
        "change-password",
        "email-validation",
        "password-reset",
        "login-lockout",
        "delete-account",
        "login-form-validation",
    ]
    for task in load_tasks():
        assert task.requirements, task.id
        assert task.acceptance_path and task.acceptance_path.is_file(), task.id
        assert task.reference_dir.is_dir(), task.id


def test_get_task_unknown_id() -> None:
    with pytest.raises(KeyError):
        get_task("nope")


def test_seed_workspace_copies_the_app_and_skips_caches(tmp_path: Path) -> None:
    source = tmp_path / "template"
    (source / "pkg" / "__pycache__").mkdir(parents=True)
    (source / "pkg" / "__pycache__" / "x.pyc").write_text("")
    (source / ".agenteval").mkdir()
    (source / ".agenteval" / "pytest.xml").write_text("")
    (source / "pkg" / "a.py").write_text("x = 1\n")

    workspace = tmp_path / "ws"
    seed_workspace(source, workspace)

    assert list_files(workspace) == ["pkg/a.py"]
    assert not (workspace / "pkg" / "__pycache__").exists()
    assert not (workspace / ".agenteval").exists()


def test_sample_app_seeds_a_runnable_layout(tmp_path: Path) -> None:
    seed_workspace(SAMPLE_APP, tmp_path)
    files = list_files(tmp_path)
    for expected in ("authkit/users.py", "migrations/001_users.sql", "web/login.html", "tests/test_users.py"):
        assert expected in files


# --- the harness: hidden acceptance tests, reference solutions ------------------------------------


def _docker_image_ready(tag: str) -> bool:
    if not docker_available():
        return False
    import docker

    try:
        docker.from_env().images.get(tag)
        return True
    except docker.errors.ImageNotFound:
        return False


def _needs_browser(task: Task) -> bool:
    assert task.acceptance_path
    return "playwright" in task.acceptance_path.read_text()


async def _run_pytest(workspace: Path, target: str, image: str) -> dict[str, int] | None:
    command = f"python -m pytest -q -p no:cacheprovider --junitxml=.agenteval/{Path(target).stem}.xml {target}"
    (workspace / ".agenteval").mkdir(exist_ok=True)
    await DockerSandbox(Settings()).run(str(workspace), command, image=image)
    summary = parse_junit(workspace / ".agenteval" / f"{Path(target).stem}.xml")
    return {k: summary[k] for k in ("total", "passed", "failed", "errors")} if summary else None


@pytest.mark.parametrize("task_id", TASK_IDS)
async def test_acceptance_fails_untouched_and_passes_with_reference(task_id: str, tmp_path: Path) -> None:
    task = get_task(task_id)
    settings = Settings()
    image = settings.sandbox_browser_image if _needs_browser(task) else settings.sandbox_image
    if not (_docker_image_ready(image) and _docker_image_ready(settings.sandbox_image)):
        pytest.skip("sandbox images not built")
    assert task.acceptance_path

    untouched, solved = tmp_path / "untouched", tmp_path / "solved"
    for workspace in (untouched, solved):
        seed_workspace(SAMPLE_APP, workspace)
        (workspace / ".acceptance").mkdir()
        shutil.copy(task.acceptance_path, workspace / ".acceptance" / "test_acceptance.py")
    seed_workspace(task.reference_dir, solved)

    before = await _run_pytest(untouched, ".acceptance/test_acceptance.py", image)
    after = await _run_pytest(solved, ".acceptance/test_acceptance.py", image)
    own_tests = await _run_pytest(solved, "tests", settings.sandbox_image)

    assert before is not None and (before["passed"] < before["total"] or before["errors"]), (
        "must fail without a solution"
    )
    assert after is not None and after["total"] > 0 and after["passed"] == after["total"], after
    assert own_tests is not None and own_tests["failed"] == own_tests["errors"] == 0, "reference must not break the app"


async def test_run_acceptance_reports_metrics_and_cleans_up(tmp_path: Path) -> None:
    task = get_task("change-password")
    seed_workspace(SAMPLE_APP, tmp_path)

    metrics = await run_acceptance(task, tmp_path, FakeSandbox([{"test_a": "passed", "test_b": "failed"}]), Settings())

    assert metrics == {"acceptance_passed": 0.0, "acceptance_tests_passed": 1.0, "acceptance_tests_total": 2.0}
    assert not (tmp_path / ACCEPTANCE_DIR).exists()  # the hidden test never stays in the workspace


async def test_run_acceptance_uses_browser_image_for_playwright_tasks(tmp_path: Path) -> None:
    sandbox = FakeSandbox([{"test_a": "passed"}])
    metrics = await run_acceptance(get_task("login-form-validation"), tmp_path, sandbox, Settings())

    assert metrics and metrics["acceptance_passed"] == 1.0
    assert sandbox.images == [Settings().sandbox_browser_image]


async def test_run_acceptance_without_test_is_none(tmp_path: Path) -> None:
    task = Task(id="x", title="x", task="x", difficulty="easy", tags=(), requirements=())
    assert await run_acceptance(task, tmp_path, FakeSandbox([0]), Settings()) is None


# --- worker + API integration --------------------------------------------------------------------


async def test_worker_seeds_workspace_and_stores_acceptance(
    sessions: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    class SnoopingSandbox(FakeSandbox):
        seen: list[list[str]] = []

        async def run(self, workspace: str, command: str, image: str | None = None):  # type: ignore[no-untyped-def]
            self.seen.append(list_files(Path(workspace)))
            return await super().run(workspace, command, image)

    sandbox = SnoopingSandbox([0, {"test_a": "passed"}])
    async with sessions() as session:
        run = await RunRepository(session).create(
            get_task("change-password").task,
            "software_engineering",
            {"task_id": "change-password", "seed": "sample_app", "max_repair_iterations": 0},
        )
    status = await execute_run(run.id, sessions, settings, llm=scripted_llm(), sandbox=sandbox)

    assert status == "succeeded"
    assert "authkit/users.py" in sandbox.seen[0]  # the agents worked on the seeded app
    async with sessions() as session:
        metrics = {e.metric: e.score for e in await TrajectoryRepository(session).evaluations(run.id)}
    assert metrics["acceptance_passed"] == 1.0
    assert metrics["task_success"] == 1.0  # the run's own metrics are still recorded separately


@pytest.fixture
async def http(sessions: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncClient]:
    class Queue:
        async def enqueue(self, run_id: str) -> None:
            pass

    app = create_app(use_lifespan=False)
    app.state.sessions, app.state.queue = sessions, Queue()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


async def test_api_creates_run_from_task_id(http: AsyncClient, sessions: async_sessionmaker[AsyncSession]) -> None:
    response = await http.post("/api/v1/runs", json={"task_id": "password-reset"})

    assert response.status_code == 202
    run = response.json()
    assert run["task_id"] == "password-reset"
    assert run["task"] == get_task("password-reset").task
    async with sessions() as session:
        stored = await RunRepository(session).get(run["id"])
    assert stored and stored.model_config_["seed"] == "sample_app"


async def test_api_task_text_override_keeps_the_seed(http: AsyncClient) -> None:
    run = (await http.post("/api/v1/runs", json={"task_id": "password-reset", "task": "custom wording"})).json()
    assert (run["task"], run["task_id"]) == ("custom wording", "password-reset")


async def test_api_unknown_task_id_is_404(http: AsyncClient) -> None:
    assert (await http.post("/api/v1/runs", json={"task_id": "nope"})).status_code == 404


async def test_api_requires_task_or_task_id(http: AsyncClient) -> None:
    assert (await http.post("/api/v1/runs", json={})).status_code == 422


def test_datasets_root_contains_the_expected_folders() -> None:
    for name in ("sample_app", "acceptance", "reference", "tasks.json"):
        assert (DATASETS_ROOT / name).exists()
