"""An in-memory audit trail of security-relevant events."""

from datetime import datetime
from typing import Any

from authkit.clock import utcnow


class AuditLog:
    def __init__(self) -> None:
        self._entries: list[dict[str, Any]] = []

    def record(self, event: str, **details: Any) -> dict[str, Any]:
        entry = {"event": event, "at": utcnow(), **details}
        self._entries.append(entry)
        return entry

    def entries(self, event: str | None = None) -> list[dict[str, Any]]:
        return [e for e in self._entries if event is None or e["event"] == event]

    def since(self, moment: datetime) -> list[dict[str, Any]]:
        return [e for e in self._entries if e["at"] >= moment]
