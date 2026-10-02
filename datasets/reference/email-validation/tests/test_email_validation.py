import sqlite3

import pytest

from authkit.users import InvalidEmailError, UserError, get_user, register


@pytest.mark.parametrize("email", ["a@b", "@b.co", "a@@b.co", "a b@c.co", "a@b.", "plain"])
def test_invalid_emails_are_rejected(conn: sqlite3.Connection, email: str) -> None:
    with pytest.raises(InvalidEmailError):
        register(conn, email, "correct-horse")


@pytest.mark.parametrize("email", ["a@b.co", "  First.Last@Example.COM  "])
def test_valid_emails_are_registered_trimmed_and_lowercased(conn: sqlite3.Connection, email: str) -> None:
    user = get_user(conn, register(conn, email, "correct-horse"))
    assert user is not None and user["email"] == email.strip().lower()


def test_the_error_is_a_user_error() -> None:
    assert issubclass(InvalidEmailError, UserError)
