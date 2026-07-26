import os

import psycopg
from psycopg.rows import dict_row

DATABASE_URL = os.environ["DATABASE_URL"]


def get_connection() -> psycopg.Connection:
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)


def init_db() -> None:
    with get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS alerts (
                id SERIAL PRIMARY KEY,
                monitor_name TEXT NOT NULL,
                status TEXT NOT NULL,
                message TEXT,
                received_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS activity_log (
                id SERIAL PRIMARY KEY,
                source TEXT NOT NULL,
                level TEXT NOT NULL,
                message TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )


def insert_alert(monitor_name: str, status: str, message: str | None) -> dict:
    with get_connection() as conn:
        row = conn.execute(
            """
            INSERT INTO alerts (monitor_name, status, message)
            VALUES (%s, %s, %s)
            RETURNING id, monitor_name, status, message, received_at
            """,
            (monitor_name, status, message),
        ).fetchone()
        conn.commit()
        return row


def list_alerts(limit: int = 100) -> list[dict]:
    with get_connection() as conn:
        return conn.execute(
            """
            SELECT id, monitor_name, status, message, received_at
            FROM alerts
            ORDER BY received_at DESC
            LIMIT %s
            """,
            (limit,),
        ).fetchall()


def insert_activity(source: str, level: str, message: str) -> dict:
    with get_connection() as conn:
        row = conn.execute(
            """
            INSERT INTO activity_log (source, level, message)
            VALUES (%s, %s, %s)
            RETURNING id, source, level, message, created_at
            """,
            (source, level, message),
        ).fetchone()
        conn.commit()
        return row


def list_activity(limit: int = 100) -> list[dict]:
    with get_connection() as conn:
        return conn.execute(
            """
            SELECT id, source, level, message, created_at
            FROM activity_log
            ORDER BY created_at DESC
            LIMIT %s
            """,
            (limit,),
        ).fetchall()
