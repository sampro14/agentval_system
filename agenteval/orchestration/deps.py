from dataclasses import dataclass

from agenteval.config import Settings
from agenteval.execution.docker.sandbox import Sandbox
from agenteval.llm.provider import LLMProvider


@dataclass
class Deps:
    """Runtime dependencies injected into every graph node."""

    settings: Settings
    llm: LLMProvider
    sandbox: Sandbox
