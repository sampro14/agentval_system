from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AGENTEVAL_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://agenteval:agenteval@localhost:5433/agenteval"
    redis_url: str = "redis://localhost:6379/0"
    run_queue: str = "agenteval:runs"

    llm_provider: Literal["anthropic", "fake"] = "anthropic"
    llm_model: str = "claude-opus-5"
    llm_max_tokens: int = 16000
    llm_effort: Literal["low", "medium", "high", "xhigh", "max"] = "high"

    max_repair_iterations: int = 3

    sandbox_image: str = "agenteval-sandbox:latest"
    sandbox_browser_image: str = "agenteval-sandbox-browser:latest"
    sandbox_browser_memory: str = "2g"
    sandbox_cpus: float = 1.0
    sandbox_memory: str = "512m"
    sandbox_timeout_s: int = 120
    sandbox_pids_limit: int = 256
    sandbox_network: bool = False
    workspace_root: str = "/tmp/agenteval/workspaces"


@lru_cache
def get_settings() -> Settings:
    return Settings()
