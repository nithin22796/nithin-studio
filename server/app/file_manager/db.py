import os

import psycopg
from psycopg.rows import dict_row

DATABASE_URL = os.environ["FILE_MANAGER_DATABASE_URL"]


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
            CREATE TABLE IF NOT EXISTS folders (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL,
                parent_id INTEGER REFERENCES folders(id) ON DELETE RESTRICT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS files (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL,
                folder_id INTEGER REFERENCES folders(id) ON DELETE RESTRICT,
                object_key TEXT NOT NULL UNIQUE,
                content_type TEXT NOT NULL,
                size_bytes BIGINT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
