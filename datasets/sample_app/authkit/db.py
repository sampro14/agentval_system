"""SQLite connection helper and a tiny forward-only migration runner."""

import sqlite3
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


def connect(path: str = ":memory:") -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def migrate(conn: sqlite3.Connection) -> list[str]:
    """Apply every not-yet-applied migrations/*.sql file in name order; return the names applied."""
    conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (name TEXT PRIMARY KEY)")
    applied = {row["name"] for row in conn.execute("SELECT name FROM schema_migrations")}
    ran = []
    for sql_file in sorted(MIGRATIONS_DIR.glob("*.sql")):
        if sql_file.name in applied:
            continue
        conn.executescript(sql_file.read_text())
        conn.execute("INSERT INTO schema_migrations (name) VALUES (?)", (sql_file.name,))
        conn.commit()
        ran.append(sql_file.name)
    return ran
