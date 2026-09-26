"""User registration and authentication."""

import sqlite3
from datetime import UTC, datetime, timedelta

from authkit.security import hash_password, verify_password

MIN_PASSWORD_LENGTH = 8
MAX_FAILED_LOGINS = 5
LOCKOUT_DURATION = timedelta(minutes=15)


class UserError(Exception):
    """Base class for user-facing errors."""


class DuplicateUserError(UserError):
    """The email is already registered."""


class AuthenticationError(UserError):
    """Wrong email or password."""


class AccountLockedError(UserError):
    """Too many failed logins; the account is temporarily locked."""


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


def authenticate(conn: sqlite3.Connection, email: str, password: str, now: datetime | None = None) -> int:
    """Return the user id for valid credentials.

    Raises AuthenticationError for wrong credentials and AccountLockedError while the account is locked.
    """
    current = now or datetime.now(UTC)
    row = get_user_by_email(conn, email)
    if row is None:
        raise AuthenticationError("invalid credentials")
    if row["locked_until"] and datetime.fromisoformat(row["locked_until"]) > current:
        raise AccountLockedError("account is temporarily locked")

    if verify_password(password, row["password_hash"]):
        conn.execute("UPDATE users SET failed_logins = 0, locked_until = NULL WHERE id = ?", (row["id"],))
        conn.commit()
        return int(row["id"])

    failures = row["failed_logins"] + 1
    if failures >= MAX_FAILED_LOGINS:
        conn.execute(
            "UPDATE users SET failed_logins = 0, locked_until = ? WHERE id = ?",
            ((current + LOCKOUT_DURATION).isoformat(), row["id"]),
        )
    else:
        conn.execute("UPDATE users SET failed_logins = ? WHERE id = ?", (failures, row["id"]))
    conn.commit()
    raise AuthenticationError("invalid credentials")
