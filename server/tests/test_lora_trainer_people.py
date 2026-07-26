import io
import time

import numpy as np
from fastapi.testclient import TestClient
from PIL import Image

from app.lora_trainer import people, router
from app.main import app

client = TestClient(app)


def _unit(vec: list[float]) -> np.ndarray:
    arr = np.array(vec, dtype=np.float32)
    return arr / np.linalg.norm(arr)


def _blank_image_bytes(size: tuple[int, int] = (100, 100)) -> bytes:
    image = Image.new("RGB", size, color=(120, 130, 140))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _wait_for(path: str, timeout: float = 2.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(path).json()
        if body["status"] != "running":
            return body
        time.sleep(0.02)
    raise TimeoutError("job did not finish in time")


def test_cluster_identities_groups_similar_embeddings():
    same_a = people.DetectedFace(file_id=1, bbox=[0, 0, 10, 10], embedding=_unit([1, 0, 0]))
    same_b = people.DetectedFace(file_id=2, bbox=[0, 0, 10, 10], embedding=_unit([0.95, 0.1, 0]))
    different = people.DetectedFace(file_id=3, bbox=[0, 0, 10, 10], embedding=_unit([0, 1, 0]))

    clusters = people.cluster_identities([same_a, same_b, different])

    assert sorted(clusters) == [[0, 1], [2]]


def test_representative_face_picks_largest_bbox():
    small = people.DetectedFace(file_id=1, bbox=[0, 0, 10, 10], embedding=_unit([1, 0]))
    large = people.DetectedFace(file_id=2, bbox=[0, 0, 40, 40], embedding=_unit([1, 0]))

    rep = people.representative_face([small, large], [0, 1])

    assert rep is large


def test_best_match_returns_closest_within_threshold():
    target = _unit([1, 0])
    close = people.Face(bbox=[0, 0, 10, 10], embedding=_unit([0.9, 0.1]))
    far = people.Face(bbox=[0, 0, 10, 10], embedding=_unit([0, 1]))

    match = people.best_match([target], [far, close])

    assert match is close


def test_best_match_returns_none_below_threshold():
    target = _unit([1, 0])
    far = people.Face(bbox=[0, 0, 10, 10], embedding=_unit([0, 1]))

    assert people.best_match([target], [far]) is None


def test_best_match_matches_against_any_of_several_targets():
    # Simulates picking the same person from two different angle clusters —
    # a face should match if it's close to *either* reference embedding.
    profile_target = _unit([0, 1])
    frontal_target = _unit([1, 0])
    profile_face = people.Face(bbox=[0, 0, 10, 10], embedding=_unit([0.05, 0.95]))

    match = people.best_match([frontal_target, profile_target], [profile_face])

    assert match is profile_face


def test_all_matches_returns_every_matching_face():
    target = _unit([1, 0])
    close_a = people.Face(bbox=[40, 0, 10, 10], embedding=_unit([0.9, 0.1]))
    close_b = people.Face(bbox=[0, 0, 10, 10], embedding=_unit([0.95, 0.05]))
    far = people.Face(bbox=[20, 0, 10, 10], embedding=_unit([0, 1]))

    matches = people.all_matches([target], [close_a, far, close_b])

    # Sorted left-to-right by bbox x1, not input order.
    assert matches == [close_b, close_a]


def test_all_matches_returns_empty_list_when_none_match():
    target = _unit([1, 0])
    far = people.Face(bbox=[0, 0, 10, 10], embedding=_unit([0, 1]))

    assert people.all_matches([target], [far]) == []


def test_person_box_expands_around_face_and_clamps_to_image():
    box = people.person_box([40, 40, 60, 80], image_size=(100, 100))

    x1, y1, x2, y2 = box
    assert x1 < 40 and x2 > 60  # wider than the face horizontally
    assert y1 <= 40  # a little room above
    assert y2 == 100  # clamped to image bounds, expanded well past the face
    assert 0 <= x1 and x2 <= 100


def test_start_people_detect_requires_images():
    response = client.post("/lora-trainer/people/detect", json={"file_manager_ids": []})

    assert response.status_code == 400


def test_get_people_detect_not_found():
    response = client.get("/lora-trainer/people/detect/does-not-exist")

    assert response.status_code == 404


def test_people_detect_job_returns_identities(monkeypatch):
    monkeypatch.setattr(
        router.file_manager_storage,
        "get_file_content",
        lambda file_id: (None, _blank_image_bytes()),
    )
    face = people.DetectedFace(file_id=1, bbox=[0, 0, 10, 10], embedding=_unit([1, 0]))
    monkeypatch.setattr(
        router.people,
        "detect_all_faces",
        lambda items, on_progress=None, should_cancel=None: [face],
    )
    monkeypatch.setattr(router.people, "cluster_identities", lambda faces: [[0]])
    monkeypatch.setattr(router.people, "representative_face", lambda faces, members: face)
    monkeypatch.setattr(router.people, "crop_face_thumbnail", lambda image, bbox: b"thumb")

    response = client.post("/lora-trainer/people/detect", json={"file_manager_ids": [1]})
    assert response.status_code == 200
    job_id = response.json()["job_id"]

    result = _wait_for(f"/lora-trainer/people/detect/{job_id}")
    assert result["status"] == "succeeded"
    assert len(result["identities"]) == 1
    assert result["identities"][0]["face_count"] == 1
    assert result["progress"] == 1.0


def test_people_detect_job_reports_failure(monkeypatch):
    def boom(file_id):
        raise RuntimeError("storage exploded")

    monkeypatch.setattr(router.file_manager_storage, "get_file_content", boom)

    response = client.post("/lora-trainer/people/detect", json={"file_manager_ids": [1]})
    job_id = response.json()["job_id"]

    result = _wait_for(f"/lora-trainer/people/detect/{job_id}")
    assert result["status"] == "failed"
    assert "storage exploded" in result["error"]


def test_cancel_people_detect_not_found():
    response = client.post("/lora-trainer/people/detect/does-not-exist/cancel")

    assert response.status_code == 404


def test_people_detect_can_be_cancelled(monkeypatch):
    def slow_get_file_content(file_id):
        time.sleep(0.05)
        return (None, _blank_image_bytes())

    monkeypatch.setattr(router.file_manager_storage, "get_file_content", slow_get_file_content)

    response = client.post(
        "/lora-trainer/people/detect", json={"file_manager_ids": [1, 2, 3, 4, 5, 6, 7, 8]}
    )
    job_id = response.json()["job_id"]

    cancel_response = client.post(f"/lora-trainer/people/detect/{job_id}/cancel")
    assert cancel_response.status_code == 200

    result = _wait_for(f"/lora-trainer/people/detect/{job_id}")
    assert result["status"] == "cancelled"


def test_start_people_select_requires_images():
    response = client.post(
        "/lora-trainer/people/select",
        json={"file_manager_ids": [], "embeddings": [[1.0]]},
    )

    assert response.status_code == 400


def test_start_people_select_requires_embeddings():
    response = client.post(
        "/lora-trainer/people/select",
        json={"file_manager_ids": [1], "embeddings": []},
    )

    assert response.status_code == 400


def test_get_people_select_not_found():
    response = client.get("/lora-trainer/people/select/does-not-exist")

    assert response.status_code == 404


def test_people_select_crops_only_multi_face_images(monkeypatch):
    monkeypatch.setattr(
        router.file_manager_storage,
        "get_file_content",
        lambda file_id: (None, _blank_image_bytes()),
    )

    def fake_detect(content):
        return [people.Face(bbox=[0, 0, 10, 10], embedding=_unit([1, 0]))]

    monkeypatch.setattr(router.people, "detect_faces", fake_detect)

    response = client.post(
        "/lora-trainer/people/select",
        json={"file_manager_ids": [1], "embeddings": [[1.0, 0.0]]},
    )
    assert response.status_code == 200
    job_id = response.json()["job_id"]

    result = _wait_for(f"/lora-trainer/people/select/{job_id}")
    assert result["status"] == "succeeded"
    assert result["results"] == [{"file_manager_id": 1, "crops": []}]
    assert result["progress"] == 1.0


def test_people_select_crops_multi_face_image_matching_target(monkeypatch):
    monkeypatch.setattr(
        router.file_manager_storage,
        "get_file_content",
        lambda file_id: (None, _blank_image_bytes()),
    )

    def fake_detect(content):
        return [
            people.Face(bbox=[0, 0, 10, 10], embedding=_unit([1, 0])),
            people.Face(bbox=[50, 50, 60, 60], embedding=_unit([0, 1])),
        ]

    monkeypatch.setattr(router.people, "detect_faces", fake_detect)
    monkeypatch.setattr(router.people, "all_matches", lambda targets, faces: [faces[0]])
    monkeypatch.setattr(router.people, "person_box", lambda bbox, size: [1, 2, 3, 4])

    response = client.post(
        "/lora-trainer/people/select",
        json={"file_manager_ids": [1], "embeddings": [[1.0, 0.0]]},
    )
    job_id = response.json()["job_id"]

    result = _wait_for(f"/lora-trainer/people/select/{job_id}")
    assert result["results"] == [{"file_manager_id": 1, "crops": [[1, 2, 3, 4]]}]


def test_people_select_splits_photo_with_multiple_matches_of_same_person(monkeypatch):
    """A collage-style photo containing three instances of the trained
    person should produce three separate crop boxes — one per matched
    face — instead of only the single best match being kept."""
    monkeypatch.setattr(
        router.file_manager_storage,
        "get_file_content",
        lambda file_id: (None, _blank_image_bytes()),
    )

    def fake_detect(content):
        return [
            people.Face(bbox=[0, 0, 10, 10], embedding=_unit([1, 0])),
            people.Face(bbox=[20, 0, 30, 10], embedding=_unit([1, 0])),
            people.Face(bbox=[40, 0, 50, 10], embedding=_unit([1, 0])),
        ]

    monkeypatch.setattr(router.people, "detect_faces", fake_detect)

    response = client.post(
        "/lora-trainer/people/select",
        json={"file_manager_ids": [1], "embeddings": [[1.0, 0.0]]},
    )
    job_id = response.json()["job_id"]

    result = _wait_for(f"/lora-trainer/people/select/{job_id}")
    assert result["results"][0]["file_manager_id"] == 1
    assert len(result["results"][0]["crops"]) == 3


def test_cancel_people_select_not_found():
    response = client.post("/lora-trainer/people/select/does-not-exist/cancel")

    assert response.status_code == 404


def test_people_select_can_be_cancelled(monkeypatch):
    def slow_get_file_content(file_id):
        time.sleep(0.05)
        return (None, _blank_image_bytes())

    monkeypatch.setattr(router.file_manager_storage, "get_file_content", slow_get_file_content)

    response = client.post(
        "/lora-trainer/people/select",
        json={"file_manager_ids": [1, 2, 3, 4, 5, 6, 7, 8], "embeddings": [[1.0, 0.0]]},
    )
    job_id = response.json()["job_id"]

    cancel_response = client.post(f"/lora-trainer/people/select/{job_id}/cancel")
    assert cancel_response.status_code == 200

    result = _wait_for(f"/lora-trainer/people/select/{job_id}")
    assert result["status"] == "cancelled"


def test_crop_preview_returns_cropped_jpeg(monkeypatch):
    monkeypatch.setattr(
        router.file_manager_storage,
        "get_file_content",
        lambda file_id: (None, _blank_image_bytes(size=(100, 100))),
    )

    response = client.get(
        "/lora-trainer/crop-preview",
        params={"file_manager_id": 1, "x1": 10, "y1": 10, "x2": 60, "y2": 90},
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    image = Image.open(io.BytesIO(response.content))
    assert image.size == (50, 80)


def test_crop_preview_not_found(monkeypatch):
    def boom(file_id):
        from app.file_manager.storage import NotFoundError

        raise NotFoundError("no such file")

    monkeypatch.setattr(router.file_manager_storage, "get_file_content", boom)

    response = client.get(
        "/lora-trainer/crop-preview",
        params={"file_manager_id": 1, "x1": 0, "y1": 0, "x2": 10, "y2": 10},
    )

    assert response.status_code == 404
