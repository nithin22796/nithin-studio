import time

from fastapi.testclient import TestClient

from app.lora_trainer import router
from app.main import app

client = TestClient(app)


def _wait_for(job_id: str, timeout: float = 2.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(f"/lora-trainer/captions/{job_id}").json()
        if body["status"] != "running":
            return body
        time.sleep(0.02)
    raise TimeoutError("job did not finish in time")


def test_start_caption_batch_requires_images():
    response = client.post("/lora-trainer/captions", json={"file_manager_ids": []})

    assert response.status_code == 400


def test_get_caption_batch_not_found():
    response = client.get("/lora-trainer/captions/does-not-exist")

    assert response.status_code == 404


def test_caption_batch_job_returns_captions(monkeypatch):
    monkeypatch.setattr(
        router.file_manager_storage, "get_file_content", lambda file_id: (None, b"img")
    )
    monkeypatch.setattr(router, "caption_image", lambda content: "a photo of a person")

    response = client.post(
        "/lora-trainer/captions", json={"file_manager_ids": [1, 2]}
    )
    assert response.status_code == 200
    job_id = response.json()["job_id"]

    result = _wait_for(job_id)
    assert result["status"] == "succeeded"
    assert result["progress"] == 1.0
    assert result["captions"] == [
        {"file_manager_id": 1, "caption": "a photo of a person"},
        {"file_manager_id": 2, "caption": "a photo of a person"},
    ]


def test_caption_batch_job_reports_failure(monkeypatch):
    def boom(file_id):
        raise RuntimeError("storage exploded")

    monkeypatch.setattr(router.file_manager_storage, "get_file_content", boom)

    response = client.post("/lora-trainer/captions", json={"file_manager_ids": [1]})
    job_id = response.json()["job_id"]

    result = _wait_for(job_id)
    assert result["status"] == "failed"
    assert "storage exploded" in result["error"]


def test_cancel_caption_batch_not_found():
    response = client.post("/lora-trainer/captions/does-not-exist/cancel")

    assert response.status_code == 404


def test_caption_batch_can_be_cancelled(monkeypatch):
    def slow_get_file_content(file_id):
        time.sleep(0.05)
        return (None, b"img")

    monkeypatch.setattr(router.file_manager_storage, "get_file_content", slow_get_file_content)
    monkeypatch.setattr(router, "caption_image", lambda content: "a photo of a person")

    response = client.post(
        "/lora-trainer/captions", json={"file_manager_ids": [1, 2, 3, 4, 5, 6, 7, 8]}
    )
    job_id = response.json()["job_id"]

    cancel_response = client.post(f"/lora-trainer/captions/{job_id}/cancel")
    assert cancel_response.status_code == 200

    result = _wait_for(job_id)
    assert result["status"] == "cancelled"
