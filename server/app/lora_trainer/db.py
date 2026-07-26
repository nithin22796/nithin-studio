import json
import os

import psycopg
from psycopg.rows import dict_row

DATABASE_URL = os.environ["LORA_TRAINER_DATABASE_URL"]


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
            CREATE TABLE IF NOT EXISTS jobs (
                id SERIAL PRIMARY KEY,
                status TEXT NOT NULL DEFAULT 'queued',
                trigger_word TEXT NOT NULL,
                base_model TEXT NOT NULL DEFAULT 'sdxl',
                steps INTEGER NOT NULL,
                rank INTEGER NOT NULL,
                alpha INTEGER NOT NULL DEFAULT 1,
                images JSONB NOT NULL,
                instance_id TEXT,
                output_file_id INTEGER,
                error_message TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        # Set once the user has clicked "close" on a finished (succeeded/
        # failed) job's card — the row stays in Postgres forever either way,
        # this only controls whether `list_active_jobs` still surfaces it.
        conn.execute(
            "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS dismissed BOOLEAN NOT NULL DEFAULT false"
        )
        # `alpha` (network_alpha) postdates the original table — existing
        # installs need this backfilled. Default matches sd-scripts' own
        # implicit default (1) so historical rows reflect what actually ran.
        conn.execute("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS alpha INTEGER NOT NULL DEFAULT 1")


def create_job(
    trigger_word: str,
    base_model: str,
    steps: int,
    rank: int,
    alpha: int,
    images: list[dict],
) -> dict:
    with get_connection() as conn:
        row = conn.execute(
            """
            INSERT INTO jobs (trigger_word, base_model, steps, rank, alpha, images)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING *
            """,
            (trigger_word, base_model, steps, rank, alpha, json.dumps(images)),
        ).fetchone()
        conn.commit()
        return row


def get_job(job_id: int) -> dict | None:
    with get_connection() as conn:
        return conn.execute("SELECT * FROM jobs WHERE id = %s", (job_id,)).fetchone()


def list_jobs() -> list[dict]:
    with get_connection() as conn:
        return conn.execute("SELECT * FROM jobs ORDER BY created_at DESC").fetchall()


def list_active_jobs() -> list[dict]:
    """Jobs the user hasn't dismissed yet — since only one job trains at a
    time, this is normally just the running job, or a single finished job
    awaiting a look, rather than the entire history."""
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM jobs WHERE dismissed = false ORDER BY created_at DESC"
        ).fetchall()


def update_job(job_id: int, **fields) -> dict:
    columns = ", ".join(f"{key} = %s" for key in fields)
    with get_connection() as conn:
        row = conn.execute(
            f"UPDATE jobs SET {columns}, updated_at = now() WHERE id = %s RETURNING *",
            (*fields.values(), job_id),
        ).fetchone()
        conn.commit()
        return row
