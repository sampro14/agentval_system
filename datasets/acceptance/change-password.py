import pytest

from authkit.db import connect, migrate
from authkit.users import AuthenticationError, UserError, authenticate, change_password, register


@pytest.fixture
def conn():
    connection = connect()
    migrate(connection)
    return connection


def test_change_password_success(conn):
    user_id = register(conn, "a@example.com", "old-password")
    change_password(conn, user_id, "old-password", "new-password-1")
    assert authenticate(conn, "a@example.com", "new-password-1") == user_id
    with pytest.raises(AuthenticationError):
        authenticate(conn, "a@example.com", "old-password")


def test_wrong_old_password(conn):
    user_id = register(conn, "a@example.com", "old-password")
    with pytest.raises(AuthenticationError):
        change_password(conn, user_id, "not-the-password", "new-password-1")
    assert authenticate(conn, "a@example.com", "old-password") == user_id


def test_unknown_user(conn):
    with pytest.raises(AuthenticationError):
        change_password(conn, 999, "old-password", "new-password-1")


def test_short_new_password(conn):
    user_id = register(conn, "a@example.com", "old-password")
    with pytest.raises(UserError):
        change_password(conn, user_id, "old-password", "short")
    assert authenticate(conn, "a@example.com", "old-password") == user_id
