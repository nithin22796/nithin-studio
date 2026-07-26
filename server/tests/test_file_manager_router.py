from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.file_manager import storage
from app.main import app

client = TestClient(app)


def make_folder(id_: int = 1, name: str = "docs", parent_id: int | None = None) -> storage.Folder:
    return storage.Folder(id=id_, name=name, parent_id=parent_id, created_at=datetime.now(UTC))


def make_file(
    id_: int = 1,
    name: str = "a.txt",
    folder_id: int | None = None,
    content_type: str = "text/plain",
) -> storage.FileRecord:
    return storage.FileRecord(
        id=id_,
        name=name,
        folder_id=folder_id,
        object_key="abc123",
        content_type=content_type,
        size_bytes=3,
        created_at=datetime.now(UTC),
    )


def test_create_folder(monkeypatch):
    def fake_create_folder(name, parent_id=None):
        return make_folder(name=name, parent_id=parent_id)

    monkeypatch.setattr(storage, "create_folder", fake_create_folder)

    response = client.post("/file-manager/folders", json={"name": "docs"})

    assert response.status_code == 200
    assert response.json() == {"id": 1, "name": "docs", "parent_id": None}


def test_get_or_create_folder(monkeypatch):
    def fake_get_or_create_folder(name, parent_id=None):
        return make_folder(name=name, parent_id=parent_id)

    monkeypatch.setattr(storage, "get_or_create_folder", fake_get_or_create_folder)

    response = client.post("/file-manager/folders/get-or-create", json={"name": "lora-trainer"})

    assert response.status_code == 200
    assert response.json() == {"id": 1, "name": "lora-trainer", "parent_id": None}


def test_list_items_not_found(monkeypatch):
    def raise_not_found(folder_id=None):
        raise storage.NotFoundError("folder 99 not found")

    monkeypatch.setattr(storage, "list_contents", raise_not_found)

    response = client.get("/file-manager/items?folder_id=99")

    assert response.status_code == 404


def test_delete_folder_conflict(monkeypatch):
    def raise_conflict(folder_id):
        raise storage.ConflictError("folder is not empty")

    monkeypatch.setattr(storage, "delete_folder", raise_conflict)

    response = client.delete("/file-manager/folders/1")

    assert response.status_code == 409


def test_download_file_sets_content_disposition(monkeypatch):
    monkeypatch.setattr(storage, "get_file_content", lambda file_id: (make_file(), b"hey"))

    response = client.get("/file-manager/files/1/content")

    assert response.status_code == 200
    assert response.content == b"hey"
    assert 'attachment; filename="a.txt"' in response.headers["content-disposition"]


def test_download_file_inline_for_safe_content_type(monkeypatch):
    monkeypatch.setattr(
        storage,
        "get_file_content",
        lambda file_id: (make_file(name="a.png", content_type="image/png"), b"hey"),
    )

    response = client.get("/file-manager/files/1/content?disposition=inline")

    assert 'inline; filename="a.png"' in response.headers["content-disposition"]


def test_download_file_inline_request_forced_to_attachment_for_unsafe_content_type(monkeypatch):
    """A content_type outside the inline-safe allowlist (e.g. text/html, or
    image/svg+xml — SVG can carry a <script> tag) must never render inline,
    even if the caller explicitly asks for it — otherwise a crafted upload
    can execute as a document on this server's own origin. See
    `_INLINE_SAFE_CONTENT_TYPES`."""
    monkeypatch.setattr(
        storage,
        "get_file_content",
        lambda file_id: (make_file(name="evil.svg", content_type="image/svg+xml"), b"<script/>"),
    )

    response = client.get("/file-manager/files/1/content?disposition=inline")

    assert 'attachment; filename="evil.svg"' in response.headers["content-disposition"]
