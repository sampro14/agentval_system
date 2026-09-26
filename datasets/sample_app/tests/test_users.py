import sqlite3

import pytest

from authkit.db import migrate
from authkit.sessions import create_session, get_session_user
from authkit.users import (
    AuthenticationError,
    DuplicateUserError,
    UserError,
    authenticate,
    get_user,
    register,
)


def test_migrate_is_idempotent(conn: sqlite3.Connection) -> None:
    assert migrate(conn) == []


def test_register_normalises_email(conn: sqlite3.Connection) -> None:
    user_id = register(conn, "  Alice@Example.COM ", "correct-horse")
    user = get_user(conn, user_id)
    assert user is not None and user["email"] == "alice@example.com"


def test_register_rejects_short_password(conn: sqlite3.Connection) -> None:
    with pytest.raises(UserError):
        register(conn, "a@example.com", "short")


def test_register_rejects_duplicate_email(conn: sqlite3.Connection) -> None:
    register(conn, "a@example.com", "correct-horse")
    with pytest.raises(DuplicateUserError):
        register(conn, "A@example.com", "another-password")


def test_authenticate(conn: sqlite3.Connection) -> None:
    user_id = register(conn, "a@example.com", "correct-horse")
    assert authenticate(conn, "a@example.com", "correct-horse") == user_id
    with pytest.raises(AuthenticationError):
        authenticate(conn, "a@example.com", "wrong-password")
    with pytest.raises(AuthenticationError):
        authenticate(conn, "nobody@example.com", "correct-horse")


def test_session_roundtrip(conn: sqlite3.Connection) -> None:
    user_id = register(conn, "a@example.com", "correct-horse")
    token = create_session(conn, user_id)
    assert get_session_user(conn, token) == user_id
    assert get_session_user(conn, "not-a-token") is None
