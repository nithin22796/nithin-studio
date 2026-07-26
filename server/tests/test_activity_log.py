from datetime import UTC, datetime

from fastapi.testclient import TestClient

import app.main as main

# .__enter__() (never .__exit__()ed — fine, the process exits when the
# suite finishes) is required, not optional: a bare TestClient(app) never
# runs the app's lifespan() at all (it only sends a fresh, throwaway ASGI
# scope per request), so init_db()/etc. never create any tables, and any
# asyncio.create_task()-based background job gets killed the instant its
# request's ephemeral event loop tears down. Confirmed by hand: without
# this, every table in a genuinely fresh Postgres stays absent and
# background-job tests race-fail unpredictably.
client = TestClient(app=main.app, raise_server_exceptions=False).__enter__()


def make_activity(**overrides) -> dict:
    activity = {
        "id": 1,
        "source": "lora-trainer",
        "level": "error",
        "message": "boom",
        "created_at": datetime.now(UTC),
    }
    activity.update(overrides)
    return activity


def test_http_exception_is_logged(monkeypatch):
    calls = []
    monkeypatch.setattr(
        main, "insert_activity", lambda **kwargs: (calls.append(kwargs), make_activity())[-1]
    )

    async def fake_publish(message):
        return None

    monkeypatch.setattr(main.broadcaster, "publish", fake_publish)

    response = client.get("/frame-extractor/jobs/does-not-exist/download")

    assert response.status_code == 404
    assert len(calls) == 1
    assert calls[0]["source"] == "frame-extractor"
    assert calls[0]["level"] == "warning"


def test_unhandled_exception_is_logged_and_masked(monkeypatch):
    calls = []
    monkeypatch.setattr(
        main, "insert_activity", lambda **kwargs: (calls.append(kwargs), make_activity())[-1]
    )

    async def fake_publish(message):
        return None

    monkeypatch.setattr(main.broadcaster, "publish", fake_publish)

    def boom(job_id=None):
        raise RuntimeError("db exploded")

    monkeypatch.setattr(main, "list_alerts", boom)

    response = client.get("/alerts")

    assert response.status_code == 500
    assert response.json() == {"detail": "internal server error"}
    assert len(calls) == 1
    assert calls[0]["level"] == "error"
    assert "db exploded" in calls[0]["message"]


def test_get_activity_returns_list(monkeypatch):
    monkeypatch.setattr(main, "list_activity", lambda: [make_activity()])

    response = client.get("/activity")

    assert response.status_code == 200
    body = response.json()
    assert body[0]["source"] == "lora-trainer"
