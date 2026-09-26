from datetime import UTC, datetime, timedelta

import pytest

from authkit.db import connect, migrate
from authkit.users import AccountLockedError, AuthenticationError, UserError, authenticate, register

T0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


@pytest.fixture
def conn():
    connection = connect()
    migrate(connection)  # lockout state needs a migration
    register(connection, "a@example.com", "right-password")
    return connection


def fail(conn, times, now=T0):
    for _ in range(times):
        with pytest.raises(AuthenticationError):
            authenticate(conn, "a@example.com", "wrong-password", now=now)


def test_locks_after_five_failures(conn):
    fail(conn, 5)  # the 5th failure itself is still an AuthenticationError
    with pytest.raises(AccountLockedError):
        authenticate(conn, "a@example.com", "right-password", now=T0 + timedelta(minutes=1))


def test_four_failures_do_not_lock(conn):
    fail(conn, 4)
    assert authenticate(conn, "a@example.com", "right-password", now=T0)


def test_lock_expires_after_fifteen_minutes(conn):
    fail(conn, 5)
    with pytest.raises(AccountLockedError):
        authenticate(conn, "a@example.com", "right-password", now=T0 + timedelta(minutes=14))
    assert authenticate(conn, "a@example.com", "right-password", now=T0 + timedelta(minutes=16))


def test_success_resets_the_counter(conn):
    fail(conn, 4)
    assert authenticate(conn, "a@example.com", "right-password", now=T0)
    fail(conn, 4)
    assert authenticate(conn, "a@example.com", "right-password", now=T0)


def test_unknown_email_never_locks(conn):
    for _ in range(10):
        with pytest.raises(AuthenticationError):
            authenticate(conn, "nobody@example.com", "whatever-pass", now=T0)


def test_locked_error_is_a_user_error():
    assert issubclass(AccountLockedError, UserError)
