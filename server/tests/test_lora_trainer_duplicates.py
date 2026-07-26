import io
import time

import numpy as np
from fastapi.testclient import TestClient
from PIL import Image

from app.lora_trainer import router
from app.lora_trainer.duplicates import find_duplicate_groups
from app.main import app

client = TestClient(app)


def _noise_image_bytes(seed: int, size: tuple[int, int] = (64, 64)) -> bytes:
    rng = np.random.default_rng(seed)
    array = rng.integers(0, 256, size=(*size, 3), dtype=np.uint8)
    image = Image.fromarray(array, mode="RGB")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _wait_for_job(job_id: str, timeout: float = 2.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(f"/lora-trainer/duplicates/{job_id}").json()
        if body["status"] != "running":
            return body
        time.sleep(0.02)
    raise TimeoutError("job did not finish in time")


def test_find_duplicate_groups_detects_identical_images():
    photo = _noise_image_bytes(seed=1)
    items = [(1, photo), (2, photo), (3, photo)]

    groups = find_duplicate_groups(items)

    assert groups == [[1, 2, 3]]


def test_find_duplicate_groups_ignores_dissimilar_images():
    items = [(1, _noise_image_bytes(seed=1)), (2, _noise_image_bytes(seed=2))]

    groups = find_duplicate_groups(items)

    assert groups == []


def test_start_duplicate_check_requires_images():
    response = client.post("/lora-trainer/duplicates", json={"file_manager_ids": []})

    assert response.status_code == 400


def test_get_duplicate_check_not_found():
    response = client.get("/lora-trainer/duplicates/does-not-exist")

    assert response.status_code == 404


def test_duplicate_check_job_returns_groups(monkeypatch):
    monkeypatch.setattr(
        router.file_manager_storage, "get_file_content", lambda file_id: (None, b"img")
    )
    monkeypatch.setattr(router.duplicates, "phash", lambda content: content)
    monkeypatch.setattr(router.duplicates, "group_hashes", lambda hashes: [[1, 2]])

    response = client.post(
        "/lora-trainer/duplicates", json={"file_manager_ids": [1, 2, 3]}
    )
    assert response.status_code == 200
    job_id = response.json()["job_id"]

    result = _wait_for_job(job_id)
    assert result["status"] == "succeeded"
    assert result["groups"] == [[1, 2]]
    assert result["progress"] == 1.0


def test_duplicate_check_job_reports_failure(monkeypatch):
    def boom(file_id):
        raise RuntimeError("storage exploded")

    monkeypatch.setattr(router.file_manager_storage, "get_file_content", boom)

    response = client.post("/lora-trainer/duplicates", json={"file_manager_ids": [1]})
    job_id = response.json()["job_id"]

    result = _wait_for_job(job_id)
    assert result["status"] == "failed"
    assert "storage exploded" in result["error"]


def test_cancel_duplicate_check_not_found():
    response = client.post("/lora-trainer/duplicates/does-not-exist/cancel")

    assert response.status_code == 404


def test_duplicate_check_can_be_cancelled(monkeypatch):
    monkeypatch.setattr(
        router.file_manager_storage, "get_file_content", lambda file_id: (None, b"img")
    )

    def slow_phash(content):
        time.sleep(0.05)
        return content

    monkeypatch.setattr(router.duplicates, "phash", slow_phash)

    response = client.post(
        "/lora-trainer/duplicates", json={"file_manager_ids": [1, 2, 3, 4, 5, 6, 7, 8]}
    )
    job_id = response.json()["job_id"]

    cancel_response = client.post(f"/lora-trainer/duplicates/{job_id}/cancel")
    assert cancel_response.status_code == 200

    result = _wait_for_job(job_id)
    assert result["status"] == "cancelled"
