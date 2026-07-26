from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.file_manager import storage as file_manager_storage
from app.lora_trainer import db, ec2, local_uploads, router, s3
from app.lora_trainer.captioning import caption_image
from app.main import app

client = TestClient(app)


def make_job(**overrides) -> dict:
    job = {
        "id": 1,
        "status": "running",
        "trigger_word": "sks-person",
        "base_model": "sdxl",
        "steps": 1000,
        "rank": 16,
        "alpha": 16,
        "images": [],
        "instance_id": "i-0123456789",
        "output_file_id": None,
        "error_message": None,
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }
    job.update(overrides)
    return job


def make_file_record(id_: int = 5, name: str = "a.jpg", content_type: str = "image/jpeg"):
    return file_manager_storage.FileRecord(
        id=id_,
        name=name,
        folder_id=None,
        object_key="abc",
        content_type=content_type,
        size_bytes=3,
        created_at=datetime.now(UTC),
    )


def test_caption(monkeypatch):
    monkeypatch.setattr(
        file_manager_storage, "get_file_content", lambda file_id: (make_file_record(), b"img")
    )
    monkeypatch.setattr(router, "caption_image", lambda content: "a photo of a person")

    response = client.post("/lora-trainer/caption", json={"file_manager_id": 5})

    assert response.status_code == 200
    assert response.json() == {"caption": "a photo of a person"}


def test_create_job_requires_images():
    response = client.post(
        "/lora-trainer/jobs",
        json={"trigger_word": "sks", "images": []},
    )
    assert response.status_code == 400


def test_create_job_launches_instance(monkeypatch):
    monkeypatch.setattr(file_manager_storage, "get_file", lambda file_id: make_file_record())
    monkeypatch.setattr(
        file_manager_storage, "get_file_content", lambda file_id: (make_file_record(), b"img")
    )
    monkeypatch.setattr(s3, "upload_dataset_image", lambda *a, **k: None)
    monkeypatch.setattr(ec2, "launch_training_instance", lambda *a, **k: "i-abc123")

    created = make_job(status="queued", instance_id=None)
    running = make_job(status="running")
    monkeypatch.setattr(db, "create_job", lambda **kwargs: created)
    monkeypatch.setattr(db, "update_job", lambda job_id, **kwargs: running)

    response = client.post(
        "/lora-trainer/jobs",
        json={
            "trigger_word": "sks-person",
            "steps": 1000,
            "rank": 16,
            "images": [{"file_manager_id": 5, "caption": "a photo of sks-person"}],
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "running"


def test_create_job_marks_failed_and_cleans_s3_if_instance_launch_fails(monkeypatch):
    monkeypatch.setattr(file_manager_storage, "get_file", lambda file_id: make_file_record())
    monkeypatch.setattr(
        file_manager_storage, "get_file_content", lambda file_id: (make_file_record(), b"img")
    )
    monkeypatch.setattr(s3, "upload_dataset_image", lambda *a, **k: None)

    deleted_prefixes = []
    monkeypatch.setattr(s3, "delete_job_prefix", lambda job_id: deleted_prefixes.append(job_id))

    def boom(*a, **k):
        raise RuntimeError("VcpuLimitExceeded")

    monkeypatch.setattr(ec2, "launch_training_instance", boom)

    created = make_job(status="queued", instance_id=None)
    failed = make_job(status="failed", error_message="VcpuLimitExceeded")
    monkeypatch.setattr(db, "create_job", lambda **kwargs: created)

    update_calls = []

    def fake_update_job(job_id, **kwargs):
        update_calls.append(kwargs)
        return failed

    monkeypatch.setattr(db, "update_job", fake_update_job)

    response = client.post(
        "/lora-trainer/jobs",
        json={
            "trigger_word": "sks-person",
            "steps": 1000,
            "rank": 16,
            "images": [{"file_manager_id": 5, "caption": "a photo of sks-person"}],
        },
    )

    assert response.status_code == 502
    assert "VcpuLimitExceeded" in response.json()["detail"]
    assert deleted_prefixes == [created["id"]]
    assert update_calls == [{"status": "failed", "error_message": "VcpuLimitExceeded"}]


def test_create_job_clears_local_uploads_but_not_file_manager_images(monkeypatch, tmp_path):
    monkeypatch.setattr(local_uploads, "UPLOADS_DIR", tmp_path)
    local_upload_id = local_uploads.save_upload("local.jpg", "image/jpeg", b"local-bytes")

    monkeypatch.setattr(file_manager_storage, "get_file", lambda file_id: make_file_record())
    monkeypatch.setattr(
        file_manager_storage, "get_file_content", lambda file_id: (make_file_record(), b"img")
    )
    monkeypatch.setattr(s3, "upload_dataset_image", lambda *a, **k: None)
    monkeypatch.setattr(ec2, "launch_training_instance", lambda *a, **k: "i-abc123")

    created = make_job(status="queued", instance_id=None)
    running = make_job(status="running")
    monkeypatch.setattr(db, "create_job", lambda **kwargs: created)
    monkeypatch.setattr(db, "update_job", lambda job_id, **kwargs: running)

    response = client.post(
        "/lora-trainer/jobs",
        json={
            "trigger_word": "sks-person",
            "steps": 1000,
            "rank": 16,
            "images": [
                {"file_manager_id": 5, "caption": "from file-manager"},
                {"file_manager_id": local_upload_id, "caption": "freshly uploaded"},
            ],
        },
    )

    assert response.status_code == 200
    try:
        local_uploads.get_upload(local_upload_id)
        assert False, "local upload should have been cleared after S3 upload"
    except local_uploads.NotFoundError:
        pass


def test_get_job_not_found(monkeypatch):
    monkeypatch.setattr(db, "get_job", lambda job_id: None)

    response = client.get("/lora-trainer/jobs/999")

    assert response.status_code == 404


def test_get_job_finalizes_on_success(monkeypatch):
    job = make_job()
    monkeypatch.setattr(db, "get_job", lambda job_id: job)
    monkeypatch.setattr(s3, "output_marker", lambda job_id: "done")
    monkeypatch.setattr(s3, "download_model", lambda job_id: b"weights")
    monkeypatch.setattr(s3, "delete_job_prefix", lambda job_id: None)
    monkeypatch.setattr(ec2, "terminate_instance", lambda instance_id: None)
    monkeypatch.setattr(
        file_manager_storage, "save_file", lambda **kwargs: make_file_record(id_=42)
    )
    succeeded = make_job(status="succeeded", output_file_id=42)
    monkeypatch.setattr(db, "update_job", lambda job_id, **kwargs: succeeded)

    response = client.get("/lora-trainer/jobs/1")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "succeeded"
    assert body["output_file_id"] == 42


def test_get_job_finalizes_on_failure(monkeypatch):
    job = make_job()
    monkeypatch.setattr(db, "get_job", lambda job_id: job)
    monkeypatch.setattr(s3, "output_marker", lambda job_id: "failed")
    monkeypatch.setattr(s3, "read_error_log", lambda job_id: "traceback: boom")
    deleted_prefixes = []
    monkeypatch.setattr(s3, "delete_job_prefix", lambda job_id: deleted_prefixes.append(job_id))
    monkeypatch.setattr(ec2, "terminate_instance", lambda instance_id: None)
    failed = make_job(status="failed", error_message="traceback: boom")
    monkeypatch.setattr(db, "update_job", lambda job_id, **kwargs: failed)

    response = client.get("/lora-trainer/jobs/1")

    assert response.status_code == 200
    assert response.json()["status"] == "failed"
    # Unlike success, a natural failure keeps the S3 dataset around so
    # `retry_job` can relaunch without re-uploading everything.
    assert deleted_prefixes == []


def test_cancel_job_not_found(monkeypatch):
    monkeypatch.setattr(db, "get_job", lambda job_id: None)

    response = client.post("/lora-trainer/jobs/999/cancel")

    assert response.status_code == 404


def test_cancel_job_rejects_finished_job(monkeypatch):
    monkeypatch.setattr(db, "get_job", lambda job_id: make_job(status="succeeded"))

    response = client.post("/lora-trainer/jobs/1/cancel")

    assert response.status_code == 400


def test_cancel_job_terminates_and_cleans_up(monkeypatch):
    monkeypatch.setattr(db, "get_job", lambda job_id: make_job(status="running"))
    terminated = []
    monkeypatch.setattr(
        ec2, "terminate_instance", lambda instance_id: terminated.append(instance_id)
    )
    deleted_prefixes = []
    monkeypatch.setattr(s3, "delete_job_prefix", lambda job_id: deleted_prefixes.append(job_id))
    cancelled = make_job(status="cancelled", error_message="Cancelled by user")
    update_calls = []

    def fake_update_job(job_id, **kwargs):
        update_calls.append(kwargs)
        return cancelled

    monkeypatch.setattr(db, "update_job", fake_update_job)

    response = client.post("/lora-trainer/jobs/1/cancel")

    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    assert terminated == ["i-0123456789"]
    assert deleted_prefixes == [1]
    assert update_calls == [{"status": "cancelled", "error_message": "Cancelled by user"}]


def test_retry_job_not_found(monkeypatch):
    monkeypatch.setattr(db, "get_job", lambda job_id: None)

    response = client.post("/lora-trainer/jobs/999/retry")

    assert response.status_code == 404


def test_retry_job_rejects_non_failed_job(monkeypatch):
    monkeypatch.setattr(db, "get_job", lambda job_id: make_job(status="cancelled"))

    response = client.post("/lora-trainer/jobs/1/retry")

    assert response.status_code == 400


def test_retry_job_relaunches_instance(monkeypatch):
    monkeypatch.setattr(db, "get_job", lambda job_id: make_job(status="failed"))
    monkeypatch.setattr(ec2, "launch_training_instance", lambda *a, **k: "i-retried")
    running = make_job(status="running", instance_id="i-retried")
    update_calls = []

    def fake_update_job(job_id, **kwargs):
        update_calls.append(kwargs)
        return running

    monkeypatch.setattr(db, "update_job", fake_update_job)

    response = client.post("/lora-trainer/jobs/1/retry")

    assert response.status_code == 200
    assert response.json()["status"] == "running"
    assert update_calls == [
        {
            "status": "running",
            "instance_id": "i-retried",
            "error_message": None,
            "dismissed": False,
        }
    ]


def test_retry_job_reports_launch_failure(monkeypatch):
    monkeypatch.setattr(db, "get_job", lambda job_id: make_job(status="failed"))

    def boom(*a, **k):
        raise RuntimeError("VcpuLimitExceeded")

    monkeypatch.setattr(ec2, "launch_training_instance", boom)

    response = client.post("/lora-trainer/jobs/1/retry")

    assert response.status_code == 502
    assert "VcpuLimitExceeded" in response.json()["detail"]


def test_get_job_log_not_found(monkeypatch):
    monkeypatch.setattr(db, "get_job", lambda job_id: None)

    response = client.get("/lora-trainer/jobs/999/log")

    assert response.status_code == 404


def test_get_job_log_returns_empty_body_when_none_yet(monkeypatch):
    """Deliberately 200, not 404 — a 404 here is a routine, frequent state
    (polled every ~4s while a log viewer is open) that the app's global
    exception handler would otherwise record as a fake "warning" activity
    event and flood the notification feed with."""
    monkeypatch.setattr(db, "get_job", lambda job_id: make_job())
    monkeypatch.setattr(s3, "read_current_log", lambda job_id: None)

    response = client.get("/lora-trainer/jobs/1/log")

    assert response.status_code == 200
    assert response.text == ""


def test_get_job_log_returns_current_content(monkeypatch):
    monkeypatch.setattr(db, "get_job", lambda job_id: make_job())
    monkeypatch.setattr(s3, "read_current_log", lambda job_id: "training log so far...")

    response = client.get("/lora-trainer/jobs/1/log")

    assert response.status_code == 200
    assert response.text == "training log so far..."


def test_get_job_reports_progress_while_running(monkeypatch):
    job = make_job(steps=1000)
    monkeypatch.setattr(db, "get_job", lambda job_id: job)
    monkeypatch.setattr(s3, "output_marker", lambda job_id: None)
    monkeypatch.setattr(ec2, "describe_instance_state", lambda instance_id: "running")
    monkeypatch.setattr(s3, "read_progress", lambda job_id: 250)

    response = client.get("/lora-trainer/jobs/1")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "running"
    assert body["progress"] == 0.25


def test_get_job_progress_is_none_before_instance_reports_a_step(monkeypatch):
    job = make_job(steps=1000)
    monkeypatch.setattr(db, "get_job", lambda job_id: job)
    monkeypatch.setattr(s3, "output_marker", lambda job_id: None)
    monkeypatch.setattr(ec2, "describe_instance_state", lambda instance_id: "running")
    monkeypatch.setattr(s3, "read_progress", lambda job_id: None)

    response = client.get("/lora-trainer/jobs/1")

    assert response.status_code == 200
    assert response.json()["progress"] is None


def test_get_job_reports_phase_before_progress_exists(monkeypatch):
    job = make_job(steps=1000)
    monkeypatch.setattr(db, "get_job", lambda job_id: job)
    monkeypatch.setattr(s3, "output_marker", lambda job_id: None)
    monkeypatch.setattr(ec2, "describe_instance_state", lambda instance_id: "running")
    monkeypatch.setattr(s3, "read_progress", lambda job_id: None)
    monkeypatch.setattr(s3, "read_phase", lambda job_id: "loading_model")

    response = client.get("/lora-trainer/jobs/1")

    assert response.status_code == 200
    body = response.json()
    assert body["progress"] is None
    assert body["phase"] == "loading_model"


def test_get_job_phase_is_none_once_progress_exists(monkeypatch):
    job = make_job(steps=1000)
    monkeypatch.setattr(db, "get_job", lambda job_id: job)
    monkeypatch.setattr(s3, "output_marker", lambda job_id: None)
    monkeypatch.setattr(ec2, "describe_instance_state", lambda instance_id: "running")
    monkeypatch.setattr(s3, "read_progress", lambda job_id: 250)

    response = client.get("/lora-trainer/jobs/1")

    assert response.status_code == 200
    assert response.json()["phase"] is None


def test_dismiss_job_allows_cancelled_job(monkeypatch):
    monkeypatch.setattr(db, "get_job", lambda job_id: make_job(status="cancelled"))
    monkeypatch.setattr(
        db, "update_job", lambda job_id, **kwargs: make_job(status="cancelled", dismissed=True)
    )

    response = client.post("/lora-trainer/jobs/1/dismiss")

    assert response.status_code == 200


def test_list_jobs_only_returns_active_jobs(monkeypatch):
    active = make_job(status="running")
    monkeypatch.setattr(db, "list_active_jobs", lambda: [active])
    monkeypatch.setattr(s3, "output_marker", lambda job_id: None)
    monkeypatch.setattr(ec2, "describe_instance_state", lambda instance_id: "running")
    monkeypatch.setattr(s3, "read_progress", lambda job_id: None)

    response = client.get("/lora-trainer/jobs")

    assert response.status_code == 200
    assert [j["id"] for j in response.json()] == [active["id"]]


def test_dismiss_job_not_found(monkeypatch):
    monkeypatch.setattr(db, "get_job", lambda job_id: None)

    response = client.post("/lora-trainer/jobs/999/dismiss")

    assert response.status_code == 404


def test_dismiss_job_rejects_running_job(monkeypatch):
    monkeypatch.setattr(db, "get_job", lambda job_id: make_job(status="running"))

    response = client.post("/lora-trainer/jobs/1/dismiss")

    assert response.status_code == 400


def test_dismiss_job_marks_finished_job_dismissed(monkeypatch):
    monkeypatch.setattr(db, "get_job", lambda job_id: make_job(status="succeeded"))
    update_calls = []

    def fake_update_job(job_id, **kwargs):
        update_calls.append(kwargs)
        return make_job(status="succeeded", dismissed=True)

    monkeypatch.setattr(db, "update_job", fake_update_job)

    response = client.post("/lora-trainer/jobs/1/dismiss")

    assert response.status_code == 200
    assert update_calls == [{"dismissed": True}]


def test_caption_image_is_importable():
    assert callable(caption_image)
