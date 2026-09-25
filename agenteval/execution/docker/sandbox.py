"""Ephemeral Docker sandbox for running agent-generated artifacts (design doc §17).

Agent code never executes on the host: each command runs in a fresh container with CPU,
memory, process and time limits, networking disabled by default, and the container removed
afterwards.
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import asdict, dataclass
from typing import Any, Protocol

import docker
from docker.errors import APIError, DockerException, ImageNotFound
from requests.exceptions import ConnectionError as RequestsConnectionError
from requests.exceptions import ReadTimeout

from agenteval.config import Settings

WORKDIR = "/workspace"


@dataclass
class ExecutionResult:
    command: str
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int
    timed_out: bool = False

    @property
    def passed(self) -> bool:
        return self.exit_code == 0 and not self.timed_out

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "passed": self.passed}


class Sandbox(Protocol):
    async def run(self, workspace: str, command: str, image: str | None = None) -> ExecutionResult: ...


class DockerSandbox:
    def __init__(self, settings: Settings):
        self._settings = settings

    async def run(self, workspace: str, command: str, image: str | None = None) -> ExecutionResult:
        return await asyncio.to_thread(self._run_sync, workspace, command, image or self._settings.sandbox_image)

    def _run_sync(self, workspace: str, command: str, image: str) -> ExecutionResult:
        s = self._settings
        client = docker.from_env()
        start = time.perf_counter()
        try:
            container = self._start(client, workspace, image, command)
        except (ImageNotFound, APIError) as exc:
            # Reported as evidence (exit 125, like `docker run`) so it is attributed to ENVIRONMENT.
            return ExecutionResult(command, 125, "", f"sandbox error: {exc}", int((time.perf_counter() - start) * 1000))
        timed_out = False
        try:
            try:
                exit_code = container.wait(timeout=s.sandbox_timeout_s)["StatusCode"]
            except (ReadTimeout, RequestsConnectionError):
                timed_out = True
                container.kill()
                exit_code = -1
            stdout = container.logs(stdout=True, stderr=False).decode(errors="replace")
            stderr = container.logs(stdout=False, stderr=True).decode(errors="replace")
        finally:
            container.remove(force=True)
        return ExecutionResult(
            command=command,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            duration_ms=int((time.perf_counter() - start) * 1000),
            timed_out=timed_out,
        )

    def _start(self, client: docker.DockerClient, workspace: str, image: str, command: str) -> Any:
        s = self._settings
        return client.containers.run(
            image,
            ["sh", "-c", command],
            volumes={workspace: {"bind": WORKDIR, "mode": "rw"}},
            working_dir=WORKDIR,
            # Run as the workspace owner so generated code can write (e.g. __pycache__) on Linux hosts.
            user=f"{os.getuid()}:{os.getgid()}",
            environment={"HOME": "/tmp"},
            network_disabled=not s.sandbox_network,
            mem_limit=s.sandbox_browser_memory if image == s.sandbox_browser_image else s.sandbox_memory,
            shm_size="512m",  # Chromium needs more than Docker's 64MB default
            nano_cpus=int(s.sandbox_cpus * 1e9),
            pids_limit=s.sandbox_pids_limit,
            detach=True,
        )


def docker_available() -> bool:
    try:
        docker.from_env().ping()
        return True
    except DockerException:
        return False
