import os

import psycopg
from psycopg.rows import dict_row

DATABASE_URL = os.environ["IMAGE_GENERATOR_DATABASE_URL"]


def _admin_database_url() -> str:
    prefix, _, _ = DATABASE_URL.rpartition("/")
    return f"{prefix}/postgres"


def _ensure_database_exists() -> None:
    db_name = DATABASE_URL.rsplit("/", 1)[-1]
    with psycopg.connect(_admin_database_url(), autocommit=True) as conn:
        exists = conn.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (db_name,)
        ).fetchone()
        if not exists:
            conn.execute(f'CREATE DATABASE "{db_name}"')


def get_connection() -> psycopg.Connection:
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)


def init_db() -> None:
    _ensure_database_exists()
    with get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                id SERIAL PRIMARY KEY,
                instance_id TEXT,
                status TEXT NOT NULL DEFAULT 'launching',
                loaded_model TEXT,
                error_message TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        # Models are now picked from a local directory (see `models.py`) by
        # filename rather than a file-manager id — this table predates that
        # change, so migrate any existing installs.
        conn.execute("ALTER TABLE sessions DROP COLUMN IF EXISTS loaded_file_id")
        conn.execute("ALTER TABLE sessions ADD COLUMN IF NOT EXISTS loaded_model TEXT")


def create_session() -> dict:
    with get_connection() as conn:
        row = conn.execute("INSERT INTO sessions DEFAULT VALUES RETURNING *").fetchone()
        conn.commit()
        return row


def get_session(session_id: int) -> dict | None:
    with get_connection() as conn:
        return conn.execute("SELECT * FROM sessions WHERE id = %s", (session_id,)).fetchone()


def get_active_session() -> dict | None:
    """The current session, if any — the most recent one not in a terminal
    state (`terminated`/`failed`). Since only one session is ever active at
    a time, this is what both the header widget and the generate endpoint
    check against."""
    with get_connection() as conn:
        return conn.execute(
            """
            SELECT * FROM sessions
            WHERE status NOT IN ('terminated', 'failed')
            ORDER BY created_at DESC
            LIMIT 1
            """
        ).fetchone()


def update_session(session_id: int, **fields) -> dict:
    columns = ", ".join(f"{key} = %s" for key in fields)
    with get_connection() as conn:
        row = conn.execute(
            f"UPDATE sessions SET {columns}, updated_at = now() WHERE id = %s RETURNING *",
            (*fields.values(), session_id),
        ).fetchone()
        conn.commit()
        return row
