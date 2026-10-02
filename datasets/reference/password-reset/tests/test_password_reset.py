import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from authkit.users import (
    AuthenticationError,
    InvalidTokenError,
    UserError,
    authenticate,
    register,
    request_password_reset,
    reset_password,
)

T0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def test_an_unknown_email_gets_no_token(conn: sqlite3.Connection) -> None:
    assert request_password_reset(conn, "nobody@example.com", now=T0) is None


def test_the_full_reset_flow(conn: sqlite3.Connection) -> None:
    user_id = register(conn, "a@example.com", "old-password")
    token = request_password_reset(conn, "a@example.com", now=T0)
    assert token
    reset_password(conn, token, "new-password-1", now=T0 + timedelta(minutes=5))
    assert authenticate(conn, "a@example.com", "new-password-1") == user_id
    with pytest.raises(AuthenticationError):
        authenticate(conn, "a@example.com", "old-password")


def test_a_token_can_only_be_used_once(conn: sqlite3.Connection) -> None:
    register(conn, "a@example.com", "old-password")
    token = request_password_reset(conn, "a@example.com", now=T0)
    reset_password(conn, token, "new-password-1", now=T0)
    with pytest.raises(InvalidTokenError):
        reset_password(conn, token, "new-password-2", now=T0)


def test_a_token_expires_after_an_hour(conn: sqlite3.Connection) -> None:
    register(conn, "a@example.com", "old-password")
    token = request_password_reset(conn, "a@example.com", now=T0)
    with pytest.raises(InvalidTokenError):
        reset_password(conn, token, "new-password-1", now=T0 + timedelta(hours=1, minutes=1))


def test_an_unknown_token_is_rejected(conn: sqlite3.Connection) -> None:
    with pytest.raises(InvalidTokenError):
        reset_password(conn, "not-a-real-token", "new-password-1", now=T0)


def test_a_short_new_password_is_rejected(conn: sqlite3.Connection) -> None:
    register(conn, "a@example.com", "old-password")
    token = request_password_reset(conn, "a@example.com", now=T0)
    with pytest.raises(UserError):
        reset_password(conn, token, "short", now=T0)
