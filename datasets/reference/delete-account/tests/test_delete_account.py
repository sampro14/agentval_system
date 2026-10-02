import sqlite3

from authkit.sessions import create_session, get_session_user
from authkit.users import delete_user, get_user, register


def test_deleting_a_user_removes_their_sessions(conn: sqlite3.Connection) -> None:
    user_id = register(conn, "a@example.com", "correct-horse")
    token = create_session(conn, user_id)
    other_id = register(conn, "b@example.com", "correct-horse")
    other_token = create_session(conn, other_id)

    assert delete_user(conn, user_id) is True

    assert get_user(conn, user_id) is None
    assert get_session_user(conn, token) is None
    assert get_session_user(conn, other_token) == other_id  # other users are untouched


def test_deleting_a_missing_user_returns_false(conn: sqlite3.Connection) -> None:
    assert delete_user(conn, 12345) is False


def test_the_email_can_be_registered_again(conn: sqlite3.Connection) -> None:
    user_id = register(conn, "a@example.com", "correct-horse")
    delete_user(conn, user_id)
    assert register(conn, "a@example.com", "correct-horse") != user_id
