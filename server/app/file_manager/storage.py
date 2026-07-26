import os
import uuid
from dataclasses import dataclass
from datetime import datetime

import boto3
from botocore.client import Config

from app.file_manager.db import get_connection

S3_ENDPOINT_URL = os.environ["FILE_MANAGER_S3_ENDPOINT_URL"]
S3_ACCESS_KEY = os.environ["FILE_MANAGER_S3_ACCESS_KEY"]
S3_SECRET_KEY = os.environ["FILE_MANAGER_S3_SECRET_KEY"]
S3_BUCKET = os.environ.get("FILE_MANAGER_S3_BUCKET", "file-manager")


class NotFoundError(Exception):
    pass


class ConflictError(Exception):
    pass


@dataclass
class Folder:
    id: int
    name: str
    parent_id: int | None
    created_at: datetime


@dataclass
class FileRecord:
    id: int
    name: str
    folder_id: int | None
    object_key: str
    content_type: str
    size_bytes: int
    created_at: datetime


def _s3_client():
    return boto3.client(
        "s3",
        endpoint_url=S3_ENDPOINT_URL,
        aws_access_key_id=S3_ACCESS_KEY,
        aws_secret_access_key=S3_SECRET_KEY,
        config=Config(signature_version="s3v4"),
        region_name="us-east-1",
    )


def ensure_bucket() -> None:
    client = _s3_client()
    existing = {b["Name"] for b in client.list_buckets().get("Buckets", [])}
    if S3_BUCKET not in existing:
        client.create_bucket(Bucket=S3_BUCKET)


def _folder_exists(conn, folder_id: int) -> bool:
    return conn.execute("SELECT 1 FROM folders WHERE id = %s", (folder_id,)).fetchone() is not None


def create_folder(name: str, parent_id: int | None = None) -> Folder:
    with get_connection() as conn:
        if parent_id is not None and not _folder_exists(conn, parent_id):
            raise NotFoundError(f"parent folder {parent_id} not found")
        row = conn.execute(
            """
            INSERT INTO folders (name, parent_id)
            VALUES (%s, %s)
            RETURNING id, name, parent_id, created_at
            """,
            (name, parent_id),
        ).fetchone()
        conn.commit()
        return Folder(**row)


def get_or_create_folder(name: str, parent_id: int | None = None) -> Folder:
    """Like `create_folder`, but returns the existing folder if one with this
    exact name already exists under `parent_id`, instead of creating a
    sibling duplicate. Useful for app modules (e.g. lora-trainer) that want
    a stable, dedicated folder to upload into without tracking its id
    themselves across sessions."""
    with get_connection() as conn:
        if parent_id is not None and not _folder_exists(conn, parent_id):
            raise NotFoundError(f"parent folder {parent_id} not found")
        row = conn.execute(
            "SELECT id, name, parent_id, created_at FROM folders "
            "WHERE name = %s AND parent_id IS NOT DISTINCT FROM %s",
            (name, parent_id),
        ).fetchone()
        if row is not None:
            return Folder(**row)
        row = conn.execute(
            """
            INSERT INTO folders (name, parent_id)
            VALUES (%s, %s)
            RETURNING id, name, parent_id, created_at
            """,
            (name, parent_id),
        ).fetchone()
        conn.commit()
        return Folder(**row)


def rename_folder(folder_id: int, name: str) -> Folder:
    with get_connection() as conn:
        row = conn.execute(
            """
            UPDATE folders SET name = %s WHERE id = %s
            RETURNING id, name, parent_id, created_at
            """,
            (name, folder_id),
        ).fetchone()
        if row is None:
            raise NotFoundError(f"folder {folder_id} not found")
        conn.commit()
        return Folder(**row)


def delete_folder(folder_id: int) -> None:
    with get_connection() as conn:
        has_children = conn.execute(
            "SELECT 1 FROM folders WHERE parent_id = %s LIMIT 1", (folder_id,)
        ).fetchone()
        has_files = conn.execute(
            "SELECT 1 FROM files WHERE folder_id = %s LIMIT 1", (folder_id,)
        ).fetchone()
        if has_children or has_files:
            raise ConflictError("folder is not empty")
        deleted = conn.execute(
            "DELETE FROM folders WHERE id = %s RETURNING id", (folder_id,)
        ).fetchone()
        if deleted is None:
            raise NotFoundError(f"folder {folder_id} not found")
        conn.commit()


def list_all_folders() -> list[Folder]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, name, parent_id, created_at FROM folders ORDER BY name"
        ).fetchall()
        return [Folder(**r) for r in rows]


def list_contents(folder_id: int | None = None) -> tuple[list[Folder], list[FileRecord]]:
    with get_connection() as conn:
        if folder_id is not None and not _folder_exists(conn, folder_id):
            raise NotFoundError(f"folder {folder_id} not found")
        folder_rows = conn.execute(
            "SELECT id, name, parent_id, created_at FROM folders "
            "WHERE parent_id IS NOT DISTINCT FROM %s ORDER BY name",
            (folder_id,),
        ).fetchall()
        file_rows = conn.execute(
            "SELECT id, name, folder_id, object_key, content_type, size_bytes, created_at "
            "FROM files WHERE folder_id IS NOT DISTINCT FROM %s ORDER BY name",
            (folder_id,),
        ).fetchall()
        return [Folder(**r) for r in folder_rows], [FileRecord(**r) for r in file_rows]


def search(query: str) -> tuple[list[Folder], list[FileRecord]]:
    pattern = f"%{query}%"
    with get_connection() as conn:
        folder_rows = conn.execute(
            "SELECT id, name, parent_id, created_at FROM folders WHERE name ILIKE %s ORDER BY name",
            (pattern,),
        ).fetchall()
        file_rows = conn.execute(
            "SELECT id, name, folder_id, object_key, content_type, size_bytes, created_at "
            "FROM files WHERE name ILIKE %s ORDER BY name",
            (pattern,),
        ).fetchall()
        return [Folder(**r) for r in folder_rows], [FileRecord(**r) for r in file_rows]


def save_file(
    name: str, content: bytes, content_type: str, folder_id: int | None = None
) -> FileRecord:
    """Upload a file's bytes and record its metadata.

    Safe to call directly, in-process, from any other app module that needs
    to persist a file (e.g. `from app.file_manager.storage import save_file`).
    """
    with get_connection() as conn:
        if folder_id is not None and not _folder_exists(conn, folder_id):
            raise NotFoundError(f"folder {folder_id} not found")

        object_key = uuid.uuid4().hex
        ensure_bucket()
        _s3_client().put_object(
            Bucket=S3_BUCKET, Key=object_key, Body=content, ContentType=content_type
        )

        row = conn.execute(
            """
            INSERT INTO files (name, folder_id, object_key, content_type, size_bytes)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id, name, folder_id, object_key, content_type, size_bytes, created_at
            """,
            (name, folder_id, object_key, content_type, len(content)),
        ).fetchone()
        conn.commit()
        return FileRecord(**row)


def get_file(file_id: int) -> FileRecord:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id, name, folder_id, object_key, content_type, size_bytes, created_at "
            "FROM files WHERE id = %s",
            (file_id,),
        ).fetchone()
        if row is None:
            raise NotFoundError(f"file {file_id} not found")
        return FileRecord(**row)


def get_file_content(file_id: int) -> tuple[FileRecord, bytes]:
    """Fetch a file's bytes by id.

    Safe to call directly, in-process, from any other app module that needs
    to read back a stored file (e.g. `from app.file_manager.storage import get_file_content`).
    """
    record = get_file(file_id)
    body = _s3_client().get_object(Bucket=S3_BUCKET, Key=record.object_key)["Body"].read()
    return record, body


def rename_file(file_id: int, name: str) -> FileRecord:
    with get_connection() as conn:
        row = conn.execute(
            """
            UPDATE files SET name = %s WHERE id = %s
            RETURNING id, name, folder_id, object_key, content_type, size_bytes, created_at
            """,
            (name, file_id),
        ).fetchone()
        if row is None:
            raise NotFoundError(f"file {file_id} not found")
        conn.commit()
        return FileRecord(**row)


def move_file(file_id: int, folder_id: int | None) -> FileRecord:
    with get_connection() as conn:
        if folder_id is not None and not _folder_exists(conn, folder_id):
            raise NotFoundError(f"folder {folder_id} not found")
        row = conn.execute(
            """
            UPDATE files SET folder_id = %s WHERE id = %s
            RETURNING id, name, folder_id, object_key, content_type, size_bytes, created_at
            """,
            (folder_id, file_id),
        ).fetchone()
        if row is None:
            raise NotFoundError(f"file {file_id} not found")
        conn.commit()
        return FileRecord(**row)


def delete_file(file_id: int) -> None:
    with get_connection() as conn:
        row = conn.execute(
            "DELETE FROM files WHERE id = %s RETURNING object_key", (file_id,)
        ).fetchone()
        if row is None:
            raise NotFoundError(f"file {file_id} not found")
        conn.commit()
    _s3_client().delete_object(Bucket=S3_BUCKET, Key=row["object_key"])
