import zipfile
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

from fastapi.testclient import TestClient

import app.frame_extractor.router as frame_extractor_router
from app.file_manager import storage as file_manager_storage
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


def test_extract_requires_file_or_file_manager_id():
    response = client.post("/frame-extractor/extract")

    assert response.status_code == 400


def test_extract_from_file_manager_file(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(frame_extractor_router, "JOBS_DIR", tmp_path)

    record = file_manager_storage.FileRecord(
        id=7,
        name="clip.mp4",
        folder_id=None,
        object_key="abc123",
        content_type="video/mp4",
        size_bytes=3,
        created_at=datetime.now(UTC),
    )
    monkeypatch.setattr(
        frame_extractor_router.file_manager_storage,
        "get_file_content",
        lambda file_id: (record, b"fake-video-bytes"),
    )
    monkeypatch.setattr(
        frame_extractor_router,
        "extract_frames",
        lambda video_path, output_dir, interval_seconds: (
            output_dir.mkdir(parents=True, exist_ok=True),
            (output_dir / "frame_0000.jpg").write_bytes(b"frame"),
            ["frame_0000.jpg"],
        )[-1],
    )

    response = client.post("/frame-extractor/extract?file_manager_file_id=7")

    assert response.status_code == 200
    body = response.json()
    assert body["frame_count"] == 1
    assert body["frames"] == [f"/frame-extractor/jobs/{body['job_id']}/frames/frame_0000.jpg"]


def test_download_selected_frames(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(frame_extractor_router, "JOBS_DIR", tmp_path)
    job_dir = tmp_path / "job1"
    job_dir.mkdir()
    (job_dir / "frame_0000.jpg").write_bytes(b"one")
    (job_dir / "frame_0001.jpg").write_bytes(b"two")

    response = client.post(
        "/frame-extractor/jobs/job1/download", json={"filenames": ["frame_0001.jpg"]}
    )

    assert response.status_code == 200
    with zipfile.ZipFile(BytesIO(response.content)) as archive:
        assert archive.namelist() == ["frame_0001.jpg"]
        assert archive.read("frame_0001.jpg") == b"two"


def test_download_selected_frames_rejects_path_traversal(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(frame_extractor_router, "JOBS_DIR", tmp_path)
    job_dir = tmp_path / "job1"
    job_dir.mkdir()
    (job_dir / "frame_0000.jpg").write_bytes(b"one")

    response = client.post(
        "/frame-extractor/jobs/job1/download", json={"filenames": ["../../etc/passwd"]}
    )

    assert response.status_code == 400
