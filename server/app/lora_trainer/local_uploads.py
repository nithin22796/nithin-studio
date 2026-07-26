"""Local-disk staging area for lora-trainer's "Upload images" flow.

Fresh dataset uploads never touch file-manager/MinIO — they're saved
directly to `LORA_TRAINER_UPLOADS_DIR` on this machine and given a
synthetic *negative* id (file-manager ids are always positive), so every
existing dataset-image code path that already threads a plain `int` id
through (duplicate groups, person crops, captions, removal) keeps working
unchanged — only the "read the actual bytes" step needs to branch on sign
(see `router._dataset_image_content`).

This is deliberately in-memory + on-disk only, no DB: entries don't need to
survive a server restart, since they're cleared out right after a
successful training upload anyway (see `router.create_job`).
"""

import os
import uuid
from dataclasses import dataclass
from pathlib import Path

_DEFAULT_UPLOADS_DIR = (
    "/Users/nithin/workspace/studio/nithin-studio/server/data/lora-trainer-uploads"
)
UPLOADS_DIR = Path(os.environ.get("LORA_TRAINER_UPLOADS_DIR", _DEFAULT_UPLOADS_DIR))


class NotFoundError(Exception):
    pass


@dataclass
class _Upload:
    name: str
    content_type: str
    path: Path


_uploads: dict[int, _Upload] = {}
_next_id = 0


def save_upload(name: str, content_type: str, content: bytes) -> int:
    global _next_id
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    _next_id -= 1
    upload_id = _next_id
    path = UPLOADS_DIR / f"{uuid.uuid4().hex}_{name}"
    path.write_bytes(content)
    _uploads[upload_id] = _Upload(name=name, content_type=content_type, path=path)
    return upload_id


def get_upload(upload_id: int) -> tuple[str, str, bytes]:
    """Returns (name, content_type, content)."""
    upload = _uploads.get(upload_id)
    if upload is None:
        raise NotFoundError(f"upload {upload_id} not found")
    return upload.name, upload.content_type, upload.path.read_bytes()


def delete_upload(upload_id: int) -> None:
    upload = _uploads.pop(upload_id, None)
    if upload is not None:
        upload.path.unlink(missing_ok=True)
