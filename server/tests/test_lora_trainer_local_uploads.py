from fastapi.testclient import TestClient

from app.lora_trainer import local_uploads
from app.main import app

# .__enter__() (never .__exit__()ed — fine, the process exits when the
# suite finishes) is required, not optional: a bare TestClient(app) never
# runs the app's lifespan() at all (it only sends a fresh, throwaway ASGI
# scope per request), so init_db()/etc. never create any tables, and any
# asyncio.create_task()-based background job gets killed the instant its
# request's ephemeral event loop tears down. Confirmed by hand: without
# this, every table in a genuinely fresh Postgres stays absent and
# background-job tests race-fail unpredictably.
client = TestClient(app).__enter__()


def test_save_and_get_upload_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(local_uploads, "UPLOADS_DIR", tmp_path)

    upload_id = local_uploads.save_upload("photo.jpg", "image/jpeg", b"hello")

    assert upload_id < 0
    name, content_type, content = local_uploads.get_upload(upload_id)
    assert name == "photo.jpg"
    assert content_type == "image/jpeg"
    assert content == b"hello"


def test_get_upload_not_found(tmp_path, monkeypatch):
    monkeypatch.setattr(local_uploads, "UPLOADS_DIR", tmp_path)

    try:
        local_uploads.get_upload(-999999)
        assert False, "expected NotFoundError"
    except local_uploads.NotFoundError:
        pass


def test_delete_upload_removes_file(tmp_path, monkeypatch):
    monkeypatch.setattr(local_uploads, "UPLOADS_DIR", tmp_path)

    upload_id = local_uploads.save_upload("photo.jpg", "image/jpeg", b"hello")
    local_uploads.delete_upload(upload_id)

    try:
        local_uploads.get_upload(upload_id)
        assert False, "expected NotFoundError after delete"
    except local_uploads.NotFoundError:
        pass


def test_upload_dataset_image_endpoint(tmp_path, monkeypatch):
    monkeypatch.setattr(local_uploads, "UPLOADS_DIR", tmp_path)

    response = client.post(
        "/lora-trainer/uploads",
        files={"file": ("photo.jpg", b"hello", "image/jpeg")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "photo.jpg"
    assert body["file_manager_id"] < 0


def test_get_upload_content_endpoint(tmp_path, monkeypatch):
    monkeypatch.setattr(local_uploads, "UPLOADS_DIR", tmp_path)
    upload_id = local_uploads.save_upload("photo.jpg", "image/jpeg", b"hello")

    response = client.get("/lora-trainer/uploads/content", params={"id": upload_id})

    assert response.status_code == 200
    assert response.content == b"hello"
    assert response.headers["content-type"] == "image/jpeg"


def test_get_upload_content_not_found():
    response = client.get("/lora-trainer/uploads/content", params={"id": -123456})

    assert response.status_code == 404


def test_get_upload_content_inline_for_safe_content_type(tmp_path, monkeypatch):
    monkeypatch.setattr(local_uploads, "UPLOADS_DIR", tmp_path)
    upload_id = local_uploads.save_upload("photo.jpg", "image/jpeg", b"hello")

    response = client.get(
        "/lora-trainer/uploads/content", params={"id": upload_id, "disposition": "inline"}
    )

    assert 'inline; filename="photo.jpg"' in response.headers["content-disposition"]


def test_get_upload_content_inline_request_forced_to_attachment_for_unsafe_content_type(
    tmp_path, monkeypatch
):
    """Same fix as file-manager's `_INLINE_SAFE_CONTENT_TYPES` — a crafted
    upload with a browser-executable content_type (SVG can carry a <script>
    tag) must never render inline, even if explicitly requested."""
    monkeypatch.setattr(local_uploads, "UPLOADS_DIR", tmp_path)
    upload_id = local_uploads.save_upload("evil.svg", "image/svg+xml", b"<script/>")

    response = client.get(
        "/lora-trainer/uploads/content", params={"id": upload_id, "disposition": "inline"}
    )

    assert 'attachment; filename="evil.svg"' in response.headers["content-disposition"]
