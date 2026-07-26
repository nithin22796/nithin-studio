import threading
import time

import pytest
from fastapi.testclient import TestClient

from app.image_upscaler import router
from app.image_upscaler.upscaler import SCALE_FACTORS, UpscaleResult, upscale_image
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


def _wait_for_job(job_id: str, timeout: float = 10.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(f"/image-upscaler/jobs/{job_id}").json()
        if body["status"] != "running":
            return body
        time.sleep(0.02)
    raise TimeoutError("job did not finish in time")


def test_upscale_image_rejects_unsupported_scale():
    with pytest.raises(ValueError, match="unsupported scale factor"):
        upscale_image(b"fake-bytes", 8)


def test_upscale_rejects_unsupported_scale_over_http():
    response = client.post(
        "/image-upscaler/upscale",
        params={"scale": 8},
        files={"file": ("photo.jpg", b"fake-bytes", "image/jpeg")},
    )

    assert response.status_code == 400
    assert str(SCALE_FACTORS) in response.json()["detail"]


def test_get_job_not_found():
    response = client.get("/image-upscaler/jobs/does-not-exist")

    assert response.status_code == 404


def test_upscale_job_saves_result_to_file_manager(monkeypatch):
    monkeypatch.setattr(
        router,
        "upscale_image",
        lambda content, scale, on_progress=None, should_cancel=None: UpscaleResult(
            image=b"upscaled-bytes", faces_detected=1
        ),
    )

    class FakeRecord:
        id = 7
        name = "photo-2x.png"
        content_type = "image/png"
        size_bytes = len(b"upscaled-bytes")

    monkeypatch.setattr(router.file_manager_storage, "save_file", lambda **kwargs: FakeRecord())

    response = client.post(
        "/image-upscaler/upscale",
        params={"scale": 2},
        files={"file": ("photo.jpg", b"fake-bytes", "image/jpeg")},
    )
    assert response.status_code == 200
    job_id = response.json()["job_id"]

    result = _wait_for_job(job_id)
    assert result["status"] == "succeeded"
    assert result["file_id"] == 7
    assert result["name"] == "photo-2x.png"


def test_upscale_job_reports_no_face_detected_diagnostics(monkeypatch):
    monkeypatch.setattr(
        router,
        "upscale_image",
        lambda content, scale, on_progress=None, should_cancel=None: UpscaleResult(
            image=b"upscaled-bytes", faces_detected=0
        ),
    )

    class FakeRecord:
        id = 8
        name = "photo-2x.png"
        content_type = "image/png"
        size_bytes = len(b"upscaled-bytes")

    monkeypatch.setattr(router.file_manager_storage, "save_file", lambda **kwargs: FakeRecord())

    response = client.post(
        "/image-upscaler/upscale",
        params={"scale": 2},
        files={"file": ("photo.jpg", b"fake-bytes", "image/jpeg")},
    )
    job_id = response.json()["job_id"]

    result = _wait_for_job(job_id)
    assert result["status"] == "succeeded"
    assert result["diagnostics"] is not None
    assert "no face detected" in result["diagnostics"]


def test_upscale_job_reports_progress_and_failure(monkeypatch):
    def fake_upscale(content, scale, on_progress=None, should_cancel=None):
        if on_progress:
            on_progress(3, 88)
        raise RuntimeError("model exploded")

    monkeypatch.setattr(router, "upscale_image", fake_upscale)

    response = client.post(
        "/image-upscaler/upscale",
        params={"scale": 2},
        files={"file": ("photo.jpg", b"fake-bytes", "image/jpeg")},
    )
    job_id = response.json()["job_id"]

    result = _wait_for_job(job_id)
    assert result["status"] == "failed"
    assert "model exploded" in result["error"]


def test_cancel_job(monkeypatch):
    from app.image_upscaler.upscaler import JobCancelled

    started = threading.Event()

    def fake_upscale(content, scale, on_progress=None, should_cancel=None):
        started.set()
        while not should_cancel():
            time.sleep(0.01)
        raise JobCancelled("cancelled")

    monkeypatch.setattr(router, "upscale_image", fake_upscale)

    response = client.post(
        "/image-upscaler/upscale",
        params={"scale": 2},
        files={"file": ("photo.jpg", b"fake-bytes", "image/jpeg")},
    )
    job_id = response.json()["job_id"]
    started.wait(timeout=2.0)

    cancel_response = client.post(f"/image-upscaler/jobs/{job_id}/cancel")
    assert cancel_response.status_code == 200

    result = _wait_for_job(job_id)
    assert result["status"] == "cancelled"


def test_cancel_unknown_job_returns_404():
    response = client.post("/image-upscaler/jobs/does-not-exist/cancel")

    assert response.status_code == 404
