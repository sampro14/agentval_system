import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from authkit.users import AccountLockedError, AuthenticationError, authenticate, register

T0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


@pytest.fixture
def user(conn: sqlite3.Connection) -> str:
    register(conn, "a@example.com", "right-password")
    return "a@example.com"


def fail(conn: sqlite3.Connection, times: int) -> None:
    for _ in range(times):
        with pytest.raises(AuthenticationError):
            authenticate(conn, "a@example.com", "wrong-password", now=T0)


def test_five_failures_lock_the_account(conn: sqlite3.Connection, user: str) -> None:
    fail(conn, 5)
    with pytest.raises(AccountLockedError):
        authenticate(conn, user, "right-password", now=T0 + timedelta(minutes=1))


def test_four_failures_do_not_lock(conn: sqlite3.Connection, user: str) -> None:
    fail(conn, 4)
    assert authenticate(conn, user, "right-password", now=T0)


def test_the_lock_lasts_fifteen_minutes(conn: sqlite3.Connection, user: str) -> None:
    fail(conn, 5)
    with pytest.raises(AccountLockedError):
        authenticate(conn, user, "right-password", now=T0 + timedelta(minutes=14))
    assert authenticate(conn, user, "right-password", now=T0 + timedelta(minutes=16))


def test_an_unknown_email_never_locks(conn: sqlite3.Connection) -> None:
    for _ in range(10):
        with pytest.raises(AuthenticationError):
            authenticate(conn, "nobody@example.com", "whatever-pass", now=T0)
