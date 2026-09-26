"""Small input validators."""

import re

_USERNAME = re.compile(r"^[a-z][a-z0-9_]{2,19}$")


def is_valid_username(value: str) -> bool:
    """3-20 characters: a lowercase letter, then lowercase letters, digits or underscores."""
    return bool(_USERNAME.match(value))


def normalize_whitespace(value: str) -> str:
    """Trim the ends and collapse runs of whitespace into single spaces."""
    return " ".join(value.split())
