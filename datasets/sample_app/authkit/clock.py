"""Time helpers, so code that depends on the current time can be tested with a fixed one."""

from datetime import UTC, datetime, timedelta


def utcnow() -> datetime:
    """The current time as a timezone-aware UTC datetime."""
    return datetime.now(UTC)


def add_minutes(moment: datetime, minutes: int) -> datetime:
    return moment + timedelta(minutes=minutes)
