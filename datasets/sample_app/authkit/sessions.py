"""Login sessions, identified by a random bearer token that is stored hashed."""

import secrets
import sqlite3

from authkit.security import hash_token


def create_session(conn: sqlite3.Connection, user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    conn.execute("INSERT INTO sessions (user_id, token_hash) VALUES (?, ?)", (user_id, hash_token(token)))
    conn.commit()
    return token


def get_session_user(conn: sqlite3.Connection, token: str) -> int | None:
    row = conn.execute("SELECT user_id FROM sessions WHERE token_hash = ?", (hash_token(token),)).fetchone()
    return int(row["user_id"]) if row else None
