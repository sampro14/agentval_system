"""User registration and authentication."""

import secrets
import sqlite3
from datetime import UTC, datetime, timedelta

from authkit.security import hash_password, hash_token, verify_password

MIN_PASSWORD_LENGTH = 8
RESET_TOKEN_TTL = timedelta(hours=1)


class UserError(Exception):
    """Base class for user-facing errors."""


class DuplicateUserError(UserError):
    """The email is already registered."""


class AuthenticationError(UserError):
    """Wrong email or password."""


class InvalidTokenError(UserError):
    """The password reset token is unknown, expired or already used."""


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


def request_password_reset(conn: sqlite3.Connection, email: str, now: datetime | None = None) -> str | None:
    """Create a single-use reset token (valid for 1 hour); None if no user has this email."""
    user = get_user_by_email(conn, email)
    if user is None:
        return None
    token = secrets.token_urlsafe(32)
    expires_at = (now or datetime.now(UTC)) + RESET_TOKEN_TTL
    conn.execute(
        "INSERT INTO password_reset_tokens (user_id, token_hash, expires_at) VALUES (?, ?, ?)",
        (user["id"], hash_token(token), expires_at.isoformat()),
    )
    conn.commit()
    return token


def reset_password(conn: sqlite3.Connection, token: str, new_password: str, now: datetime | None = None) -> None:
    current = now or datetime.now(UTC)
    row = conn.execute(
        "SELECT id, user_id, expires_at, used_at FROM password_reset_tokens WHERE token_hash = ?",
        (hash_token(token),),
    ).fetchone()
    if row is None or row["used_at"] is not None or datetime.fromisoformat(row["expires_at"]) < current:
        raise InvalidTokenError("invalid or expired token")
    if len(new_password) < MIN_PASSWORD_LENGTH:
        raise UserError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")
    conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (hash_password(new_password), row["user_id"]))
    conn.execute("UPDATE password_reset_tokens SET used_at = ? WHERE id = ?", (current.isoformat(), row["id"]))
    conn.commit()
