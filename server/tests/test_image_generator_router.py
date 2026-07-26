from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.file_manager import storage as file_manager_storage
from app.image_generator import db, ec2, models, router
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


def make_session(**overrides) -> dict:
    session = {
        "id": 1,
        "instance_id": "i-0123456789",
        "status": "running",
        "loaded_model": None,
        "error_message": None,
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }
    session.update(overrides)
    return session


def make_file_record(
    id_: int = 5, name: str = "lora.safetensors"
) -> file_manager_storage.FileRecord:
    return file_manager_storage.FileRecord(
        id=id_,
        name=name,
        folder_id=None,
        object_key="abc123",
        content_type="application/octet-stream",
        size_bytes=1024,
        created_at=datetime.now(UTC),
    )


def test_list_models(monkeypatch):
    monkeypatch.setattr(models, "list_models", lambda: ["nandhini.safetensors"])

    response = client.get("/image-generator/models")

    assert response.status_code == 200
    assert response.json() == {"models": ["nandhini.safetensors"]}


def test_start_session_requires_existing_model(monkeypatch):
    def raise_not_found(name):
        raise models.NotFoundError(f"model not found: {name}")

    monkeypatch.setattr(models, "get_model_path", raise_not_found)

    response = client.post("/image-generator/sessions", json={"model": "missing.safetensors"})

    assert response.status_code == 404


def test_start_session_rejects_when_already_active(monkeypatch):
    monkeypatch.setattr(models, "get_model_path", lambda name: "/models/" + name)
    monkeypatch.setattr(db, "get_active_session", lambda: make_session())

    response = client.post("/image-generator/sessions", json={"model": "nandhini.safetensors"})

    assert response.status_code == 409


def test_start_session_marks_failed_on_launch_error(monkeypatch):
    monkeypatch.setattr(models, "get_model_path", lambda name: "/models/" + name)
    monkeypatch.setattr(db, "get_active_session", lambda: None)
    created = make_session(status="launching", instance_id=None)
    monkeypatch.setattr(db, "create_session", lambda: created)

    def boom(session_id):
        raise RuntimeError("VcpuLimitExceeded")

    monkeypatch.setattr(ec2, "launch_inference_instance", boom)
    failed = make_session(status="failed", error_message="VcpuLimitExceeded", instance_id=None)
    update_calls = []

    def fake_update_session(session_id, **kwargs):
        update_calls.append(kwargs)
        return failed

    monkeypatch.setattr(db, "update_session", fake_update_session)

    response = client.post("/image-generator/sessions", json={"model": "nandhini.safetensors"})

    assert response.status_code == 502
    assert "VcpuLimitExceeded" in response.json()["detail"]
    assert update_calls == [{"status": "failed", "error_message": "VcpuLimitExceeded"}]


def test_start_session_launches_and_returns_launching(monkeypatch):
    monkeypatch.setattr(models, "get_model_path", lambda name: "/models/" + name)
    monkeypatch.setattr(db, "get_active_session", lambda: None)
    created = make_session(status="launching", instance_id=None)
    monkeypatch.setattr(db, "create_session", lambda: created)
    monkeypatch.setattr(ec2, "launch_inference_instance", lambda session_id: "i-newinstance")
    launching = make_session(status="launching", instance_id="i-newinstance")
    monkeypatch.setattr(db, "update_session", lambda session_id, **kwargs: launching)
    # The background bring-up task fails fast (instance already "terminated")
    # instead of polling for minutes, keeping this test quick — its own
    # success/failure path is covered separately below.
    monkeypatch.setattr(ec2, "describe_instance_state", lambda instance_id: "terminated")

    response = client.post("/image-generator/sessions", json={"model": "nandhini.safetensors"})

    assert response.status_code == 200
    assert response.json()["status"] == "launching"


def test_stop_session_not_found(monkeypatch):
    monkeypatch.setattr(db, "get_session", lambda session_id: None)

    response = client.post("/image-generator/sessions/1/stop")

    assert response.status_code == 404


def test_stop_session_rejects_already_stopped(monkeypatch):
    monkeypatch.setattr(db, "get_session", lambda session_id: make_session(status="terminated"))

    response = client.post("/image-generator/sessions/1/stop")

    assert response.status_code == 400


def test_stop_session_terminates_instance(monkeypatch):
    monkeypatch.setattr(db, "get_session", lambda session_id: make_session(status="running"))
    terminated_ids = []
    monkeypatch.setattr(
        ec2, "terminate_instance", lambda instance_id: terminated_ids.append(instance_id)
    )
    stopped = make_session(status="terminated")
    monkeypatch.setattr(db, "update_session", lambda session_id, **kwargs: stopped)

    response = client.post("/image-generator/sessions/1/stop")

    assert response.status_code == 200
    assert response.json()["status"] == "terminated"
    assert terminated_ids == ["i-0123456789"]


def test_get_session_log_not_found(monkeypatch):
    monkeypatch.setattr(db, "get_session", lambda session_id: None)

    response = client.get("/image-generator/sessions/1/log")

    assert response.status_code == 404


def test_get_session_log_requires_instance(monkeypatch):
    monkeypatch.setattr(db, "get_session", lambda session_id: make_session(instance_id=None))

    response = client.get("/image-generator/sessions/1/log")

    assert response.status_code == 400


def test_get_session_log_returns_log_text(monkeypatch):
    monkeypatch.setattr(db, "get_session", lambda session_id: make_session())
    monkeypatch.setattr(ec2, "fetch_instance_log", lambda instance_id: "boot ok\nserver ok\n")

    response = client.get("/image-generator/sessions/1/log")

    assert response.status_code == 200
    assert response.text == "boot ok\nserver ok\n"


def test_get_session_log_reports_ssm_failure(monkeypatch):
    monkeypatch.setattr(db, "get_session", lambda session_id: make_session())

    def boom(instance_id):
        raise RuntimeError("IMAGE_GENERATOR_INSTANCE_PROFILE_ARN isn't set")

    monkeypatch.setattr(ec2, "fetch_instance_log", boom)

    response = client.get("/image-generator/sessions/1/log")

    assert response.status_code == 502
    assert "INSTANCE_PROFILE_ARN" in response.json()["detail"]


def test_get_current_session_returns_null_when_none_active(monkeypatch):
    monkeypatch.setattr(db, "get_active_session", lambda: None)

    response = client.get("/image-generator/sessions/current")

    assert response.status_code == 200
    assert response.json() is None


def test_get_current_session_returns_active_session(monkeypatch):
    monkeypatch.setattr(db, "get_active_session", lambda: make_session())

    response = client.get("/image-generator/sessions/current")

    assert response.status_code == 200
    assert response.json()["id"] == 1
    assert response.json()["status"] == "running"


def test_generate_requires_running_session(monkeypatch):
    monkeypatch.setattr(db, "get_active_session", lambda: None)

    response = client.post("/image-generator/generate", json={"prompt": "a cat"})

    assert response.status_code == 400


def test_generate_requires_status_running_not_launching(monkeypatch):
    monkeypatch.setattr(db, "get_active_session", lambda: make_session(status="launching"))

    response = client.post("/image-generator/generate", json={"prompt": "a cat"})

    assert response.status_code == 400


def test_generate_saves_result_into_file_manager(monkeypatch):
    monkeypatch.setattr(db, "get_active_session", lambda: make_session(status="running"))
    monkeypatch.setattr(ec2, "instance_public_ip", lambda instance_id: "1.2.3.4")

    class FakeResponse:
        status_code = 200
        content = b"fake-png-bytes"

    class FakeAsyncClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, headers=None, json=None):
            assert "1.2.3.4" in url
            assert headers["Authorization"] == f"Bearer {ec2.INFERENCE_TOKEN}"
            return FakeResponse()

    monkeypatch.setattr(router.httpx, "AsyncClient", FakeAsyncClient)
    saved = make_file_record(id_=42, name="a_cat.png")
    monkeypatch.setattr(
        file_manager_storage,
        "save_file",
        lambda name, content, content_type: saved,
    )

    response = client.post("/image-generator/generate", json={"prompt": "a cat"})

    assert response.status_code == 200
    body = response.json()
    assert body["file_id"] == 42
    assert body["name"] == "a_cat.png"


def test_generate_reports_upstream_failure(monkeypatch):
    monkeypatch.setattr(db, "get_active_session", lambda: make_session(status="running"))
    monkeypatch.setattr(ec2, "instance_public_ip", lambda instance_id: "1.2.3.4")

    class FakeResponse:
        status_code = 500
        content = b""

    class FakeAsyncClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, headers=None, json=None):
            return FakeResponse()

    monkeypatch.setattr(router.httpx, "AsyncClient", FakeAsyncClient)

    response = client.post("/image-generator/generate", json={"prompt": "a cat"})

    assert response.status_code == 502
