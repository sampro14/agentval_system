import pytest

from authkit.db import connect, migrate
from authkit.sessions import create_session, get_session_user
from authkit.users import delete_user, get_user, register


@pytest.fixture
def conn():
    connection = connect()
    migrate(connection)
    return connection


def test_delete_user_with_sessions(conn):
    user_id = register(conn, "a@example.com", "correct-horse")
    token = create_session(conn, user_id)
    other_id = register(conn, "b@example.com", "correct-horse")
    other_token = create_session(conn, other_id)

    assert delete_user(conn, user_id) is True

    assert get_user(conn, user_id) is None
    assert get_session_user(conn, token) is None
    assert conn.execute("SELECT COUNT(*) FROM sessions WHERE user_id = ?", (user_id,)).fetchone()[0] == 0
    # Other users are untouched.
    assert get_session_user(conn, other_token) == other_id


def test_delete_user_without_sessions(conn):
    user_id = register(conn, "a@example.com", "correct-horse")
    assert delete_user(conn, user_id) is True


def test_delete_missing_user_returns_false(conn):
    assert delete_user(conn, 12345) is False


def test_email_can_be_registered_again(conn):
    user_id = register(conn, "a@example.com", "correct-horse")
    delete_user(conn, user_id)
    assert register(conn, "a@example.com", "correct-horse") != user_id
