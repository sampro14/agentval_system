"""User registration and authentication."""

import sqlite3

from authkit.security import hash_password, verify_password

MIN_PASSWORD_LENGTH = 8


class UserError(Exception):
    """Base class for user-facing errors."""


class DuplicateUserError(UserError):
    """The email is already registered."""


class AuthenticationError(UserError):
    """Wrong email or password."""


def register(conn: sqlite3.Connection, email: str, password: str) -> int:
    email = email.strip().lower()
    if len(password) < MIN_PASSWORD_LENGTH:
        raise UserError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")
    try:
        cursor = conn.execute(
            "INSERT INTO users (email, password_hash) VALUES (?, ?)", (email, hash_password(password))
        )
    except sqlite3.IntegrityError as exc:
        raise DuplicateUserError(email) from exc
    conn.commit()
    return int(cursor.lastrowid or 0)


def get_user(conn: sqlite3.Connection, user_id: int) -> sqlite3.Row | None:
    row: sqlite3.Row | None = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return row


def get_user_by_email(conn: sqlite3.Connection, email: str) -> sqlite3.Row | None:
    row: sqlite3.Row | None = conn.execute(
        "SELECT * FROM users WHERE email = ?", (email.strip().lower(),)
    ).fetchone()
    return row


def authenticate(conn: sqlite3.Connection, email: str, password: str) -> int:
    """Return the user id for valid credentials, otherwise raise AuthenticationError."""
    row = get_user_by_email(conn, email)
    if row is None or not verify_password(password, row["password_hash"]):
        raise AuthenticationError("invalid credentials")
    return int(row["id"])


def delete_user(conn: sqlite3.Connection, user_id: int) -> bool:
    """Delete a user and their sessions; return False if the user does not exist."""
    conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
    cursor = conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    conn.commit()
    return cursor.rowcount > 0
