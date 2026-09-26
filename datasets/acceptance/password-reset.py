from datetime import UTC, datetime, timedelta

import pytest

from authkit.db import connect, migrate
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


@pytest.fixture
def conn():
    connection = connect()
    migrate(connection)  # a token table without a migration would fail here
    return connection


def test_unknown_email_returns_none(conn):
    assert request_password_reset(conn, "nobody@example.com", now=T0) is None


def test_full_flow(conn):
    user_id = register(conn, "a@example.com", "old-password")
    token = request_password_reset(conn, "a@example.com", now=T0)
    assert token
    reset_password(conn, token, "new-password-1", now=T0 + timedelta(minutes=5))
    assert authenticate(conn, "a@example.com", "new-password-1") == user_id
    with pytest.raises(AuthenticationError):
        authenticate(conn, "a@example.com", "old-password")


def test_token_is_single_use(conn):
    register(conn, "a@example.com", "old-password")
    token = request_password_reset(conn, "a@example.com", now=T0)
    reset_password(conn, token, "new-password-1", now=T0)
    with pytest.raises(InvalidTokenError):
        reset_password(conn, token, "new-password-2", now=T0)


def test_token_expires_after_one_hour(conn):
    register(conn, "a@example.com", "old-password")
    token = request_password_reset(conn, "a@example.com", now=T0)
    with pytest.raises(InvalidTokenError):
        reset_password(conn, token, "new-password-1", now=T0 + timedelta(hours=1, minutes=1))
    # An expired token must not have changed the password.
    assert authenticate(conn, "a@example.com", "old-password")


def test_unknown_token(conn):
    with pytest.raises(InvalidTokenError):
        reset_password(conn, "not-a-real-token", "new-password-1", now=T0)


def test_short_new_password_rejected(conn):
    register(conn, "a@example.com", "old-password")
    token = request_password_reset(conn, "a@example.com", now=T0)
    with pytest.raises(UserError):
        reset_password(conn, token, "short", now=T0)


def test_token_not_stored_in_plaintext(conn):
    register(conn, "a@example.com", "old-password")
    token = request_password_reset(conn, "a@example.com", now=T0)
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
    for table in tables:
        for row in conn.execute(f"SELECT * FROM {table}"):
            assert token not in [str(value) for value in tuple(row)]


def test_invalid_token_error_is_a_user_error():
    assert issubclass(InvalidTokenError, UserError)
