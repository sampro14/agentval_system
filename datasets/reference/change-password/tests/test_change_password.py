import sqlite3

import pytest

from authkit.users import AuthenticationError, UserError, authenticate, change_password, register


def test_the_new_password_replaces_the_old_one(conn: sqlite3.Connection) -> None:
    user_id = register(conn, "a@example.com", "old-password")
    change_password(conn, user_id, "old-password", "new-password-1")
    assert authenticate(conn, "a@example.com", "new-password-1") == user_id
    with pytest.raises(AuthenticationError):
        authenticate(conn, "a@example.com", "old-password")


def test_a_wrong_old_password_is_rejected(conn: sqlite3.Connection) -> None:
    user_id = register(conn, "a@example.com", "old-password")
    with pytest.raises(AuthenticationError):
        change_password(conn, user_id, "not-the-password", "new-password-1")
    assert authenticate(conn, "a@example.com", "old-password") == user_id  # nothing changed


def test_an_unknown_user_is_rejected(conn: sqlite3.Connection) -> None:
    with pytest.raises(AuthenticationError):
        change_password(conn, 999, "old-password", "new-password-1")


def test_a_short_new_password_is_rejected(conn: sqlite3.Connection) -> None:
    user_id = register(conn, "a@example.com", "old-password")
    with pytest.raises(UserError):
        change_password(conn, user_id, "old-password", "short")
