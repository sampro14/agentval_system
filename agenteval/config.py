import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from dotenv import dotenv_values
from pydantic_settings import BaseSettings, SettingsConfigDict

ProviderName = Literal["anthropic", "openai", "gemini", "deepseek", "fake"]
Effort = Literal["low", "medium", "high", "xhigh", "max"]

# Environment variables each provider's SDK reads its API key from (any one is enough).
PROVIDER_KEY_VARS: dict[str, tuple[str, ...]] = {
    "anthropic": ("ANTHROPIC_API_KEY",),
    "openai": ("OPENAI_API_KEY",),
    "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    "deepseek": ("DEEPSEEK_API_KEY",),
    "fake": (),
}

# Reasoning effort per provider when none is set. Only Gemini has been measured: on the coder, "low" passed the
# hidden acceptance tests 18/18 in a median of 6s, the same as "high" (18/18, median 42s, up to 94s against a
# 120s timeout) at about a ninth of the tokens. The others keep "high" until they are measured too.
DEFAULT_EFFORT: dict[str, Effort] = {
    "anthropic": "high",
    "openai": "high",
    "gemini": "low",
    "deepseek": "high",
    "fake": "high",
}

DEFAULT_MODELS: dict[str, str] = {
    "anthropic": "claude-opus-5",
    "openai": "gpt-6-sol",
    "gemini": "gemini-3.8-flash",
    "deepseek": "deepseek-v4-pro",
    "fake": "fake",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AGENTEVAL_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://agenteval:agenteval@localhost:5433/agenteval"
    redis_url: str = "redis://localhost:6379/0"
    run_queue: str = "agenteval:runs"

    llm_provider: ProviderName = "anthropic"
    llm_model: str | None = None  # None -> the provider's default (DEFAULT_MODELS)
    llm_max_tokens: int = 16000
    llm_effort: Effort | None = None  # None -> DEFAULT_EFFORT

    llm_timeout_s: float = 120  # a stalled provider call must fail, not hang the run forever

    deepseek_base_url: str = "https://api.deepseek.com"

    max_repair_iterations: int = 3

    # Origins allowed to call the API from a browser (the web dashboard).
    cors_origins: list[str] = ["http://localhost:3000"]
    # The one-agent-at-a-time endpoints spend LLM money and start containers, and the API has no login.
    enable_dev_endpoints: bool = False

    sandbox_image: str = "agenteval-sandbox:latest"
    sandbox_browser_image: str = "agenteval-sandbox-browser:latest"
    sandbox_browser_memory: str = "2g"
    sandbox_cpus: float = 1.0
    sandbox_memory: str = "512m"
    sandbox_timeout_s: int = 120
    sandbox_pids_limit: int = 256
    sandbox_network: bool = False
    workspace_root: str = "/tmp/agenteval/workspaces"

    @property
    def resolved_effort(self) -> Effort:
        """The reasoning effort to use: the explicit setting, else the provider's default."""
        return self.llm_effort or DEFAULT_EFFORT[self.llm_provider]

    @property
    def resolved_model(self) -> str:
        """The model to call: the explicit override, else the provider's default."""
        return self.llm_model or DEFAULT_MODELS[self.llm_provider]


def provider_configured(provider: str) -> bool:
    keys = PROVIDER_KEY_VARS[provider]
    return not keys or any(os.environ.get(var) for var in keys)


def load_env_file(path: str | Path = ".env") -> list[str]:
    """Export non-empty variables from a .env file into the process environment.

    The provider SDKs read their API keys from the environment, and Settings only understands
    AGENTEVAL_* names, so keys in .env would otherwise be ignored. Variables that are already set
    win, and blank values (as in .env.example) are skipped so they can't shadow a real key.
    Returns the names that were loaded.
    """
    loaded = []
    for name, value in dotenv_values(path).items():
        if value and name not in os.environ:
            os.environ[name] = value
            loaded.append(name)
    return loaded


@lru_cache
def get_settings() -> Settings:
    load_env_file()
    return Settings()
