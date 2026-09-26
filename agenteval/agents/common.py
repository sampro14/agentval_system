"""Helpers shared by the agents."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agenteval.llm.provider import LLMProvider, parse_json


async def complete_json[T](
    llm: LLMProvider,
    system: str,
    prompt: str,
    parse: Callable[[dict[str, Any]], T],
    *,
    who: str,
    attempts: int = 2,
) -> tuple[T, int]:
    """Ask for a JSON object, validate it with `parse`, and retry with the error fed back.

    `parse` turns the decoded object into the agent's result and raises ValueError (pydantic's
    ValidationError is one) for anything it will not accept. A reply that is not JSON at all is
    handled the same way. Returns the result and how many attempts it took; after `attempts` bad
    replies it raises a ValueError that carries the last problem.
    """
    current, error = prompt, ""
    for attempt in range(1, attempts + 1):
        response = await llm.complete(system, current)
        try:
            return parse(parse_json(response.text)), attempt
        except ValueError as exc:  # includes json.JSONDecodeError and pydantic.ValidationError
            error = str(exc)
            current = (
                f"{prompt}\n\nYour previous reply was rejected: {error}\n"
                "Reply again with only the corrected JSON object."
            )
    raise ValueError(f"{who} output still invalid after {attempts} attempts: {error}")
