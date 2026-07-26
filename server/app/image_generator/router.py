import asyncio
import time

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from app.file_manager import storage as file_manager_storage
from app.image_generator import db, ec2, models

router = APIRouter(prefix="/image-generator", tags=["image-generator"])

# How long `_bring_up_session` waits for the instance to boot, its inference
# server to report healthy, and the chosen LoRA to finish uploading, before
# giving up and marking the session failed (and terminating the instance,
# since a session that never became usable has nothing worth keeping).
BRING_UP_TIMEOUT_SECONDS = 300.0
BRING_UP_POLL_INTERVAL_SECONDS = 5.0


class SessionOut(BaseModel):
    id: int
    status: str
    loaded_model: str | None
    error_message: str | None
    created_at: str
    updated_at: str


def _session_out(session: dict) -> SessionOut:
    return SessionOut(
        id=session["id"],
        status=session["status"],
        loaded_model=session["loaded_model"],
        error_message=session["error_message"],
        created_at=session["created_at"].isoformat(),
        updated_at=session["updated_at"].isoformat(),
    )


@router.get("/models")
def list_models():
    return {"models": models.list_models()}


async def _bring_up_session(session_id: int, instance_id: str, model_name: str) -> None:
    """Background task kicked off right after launch: waits for the instance
    to reach `running` and its inference server to answer `/health`, pushes
    the chosen LoRA to it, then marks the session `running`. Any failure
    along the way — timeout, the instance terminating unexpectedly, the
    upload failing — marks the session `failed` and cleans up the instance,
    since a session that never became usable shouldn't keep billing."""
    deadline = time.monotonic() + BRING_UP_TIMEOUT_SECONDS
    try:
        ip: str | None = None
        while time.monotonic() < deadline:
            state = ec2.describe_instance_state(instance_id)
            if state == "terminated":
                raise RuntimeError("instance terminated before becoming ready")
            if state == "running":
                ip = ec2.instance_public_ip(instance_id)
                if ip:
                    async with httpx.AsyncClient() as client:
                        try:
                            resp = await client.get(
                                f"http://{ip}:{ec2.INFERENCE_PORT}/health", timeout=5
                            )
                            if resp.status_code == 200:
                                break
                        except httpx.RequestError:
                            pass
            await asyncio.sleep(BRING_UP_POLL_INTERVAL_SECONDS)
        else:
            raise RuntimeError("timed out waiting for the instance to become ready")

        model_path = models.get_model_path(model_name)
        content = model_path.read_bytes()
        # /load-lora triggers the *first* load of the base SDXL checkpoint
        # into the pipeline (not just the small LoRA weights) — a one-time
        # cost that can run past a minute on a fresh instance, unlike every
        # later request. Give it a much longer timeout than a normal call.
        async with httpx.AsyncClient(timeout=600) as client:
            resp = await client.post(
                f"http://{ip}:{ec2.INFERENCE_PORT}/load-lora",
                headers={"Authorization": f"Bearer {ec2.INFERENCE_TOKEN}"},
                files={"file": (model_name, content)},
            )
            resp.raise_for_status()

        db.update_session(session_id, status="running", loaded_model=model_name)
    except Exception as exc:  # noqa: BLE001 -- surfaced via session.error_message, not swallowed
        # Some exceptions (httpx.ReadTimeout among them) stringify to "" —
        # fall back to the type name so error_message is never blank.
        message = str(exc) or type(exc).__name__
        db.update_session(session_id, status="failed", error_message=message)
        try:
            ec2.terminate_instance(instance_id)
        except Exception:  # noqa: BLE001 -- best-effort cleanup, already reporting the real error above
            pass


class StartSessionRequest(BaseModel):
    model: str


@router.post("/sessions", response_model=SessionOut)
async def start_session(body: StartSessionRequest):
    try:
        models.get_model_path(body.model)
    except models.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    if db.get_active_session() is not None:
        raise HTTPException(status_code=409, detail="a session is already active")

    session = db.create_session()
    try:
        instance_id = ec2.launch_inference_instance(session["id"])
    except Exception as exc:
        session = db.update_session(session["id"], status="failed", error_message=str(exc))
        raise HTTPException(
            status_code=502, detail=f"failed to launch instance: {exc}"
        ) from exc

    session = db.update_session(session["id"], instance_id=instance_id, status="launching")

    async def run_bring_up():
        await _bring_up_session(session["id"], instance_id, body.model)

    asyncio.create_task(run_bring_up())
    return _session_out(session)


@router.post("/sessions/{session_id}/stop", response_model=SessionOut)
def stop_session(session_id: int):
    session = db.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    if session["status"] in ("terminated", "failed"):
        raise HTTPException(status_code=400, detail="session is already stopped")
    if session["instance_id"]:
        ec2.terminate_instance(session["instance_id"])
    session = db.update_session(session_id, status="terminated")
    return _session_out(session)


@router.get("/sessions/{session_id}/log")
def get_session_log(session_id: int):
    """Pulls the instance's own log via SSM — the only remote debugging aid
    this app has, since there's no S3/SSH by design. Requires
    `IMAGE_GENERATOR_INSTANCE_PROFILE_ARN` and the instance's SSM agent to
    have finished registering (~30-60s after boot)."""
    session = db.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    if not session["instance_id"]:
        raise HTTPException(status_code=400, detail="session has no instance yet")
    try:
        log = ec2.fetch_instance_log(session["instance_id"])
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return Response(content=log, media_type="text/plain")


@router.get("/sessions/current", response_model=SessionOut | None)
def get_current_session():
    session = db.get_active_session()
    if session is None:
        return None
    return _session_out(session)


class GenerateRequest(BaseModel):
    prompt: str
    steps: int = 20


class GenerateOut(BaseModel):
    file_id: int
    name: str


@router.post("/generate", response_model=GenerateOut)
async def generate(body: GenerateRequest):
    session = db.get_active_session()
    if session is None or session["status"] != "running":
        raise HTTPException(status_code=400, detail="no running session")

    ip = ec2.instance_public_ip(session["instance_id"])
    if not ip:
        raise HTTPException(status_code=502, detail="instance has no public IP yet")

    async with httpx.AsyncClient(timeout=120) as client:
        try:
            resp = await client.post(
                f"http://{ip}:{ec2.INFERENCE_PORT}/generate",
                headers={"Authorization": f"Bearer {ec2.INFERENCE_TOKEN}"},
                json={"prompt": body.prompt, "steps": body.steps},
            )
        except httpx.RequestError as exc:
            raise HTTPException(
                status_code=502, detail=f"could not reach inference server: {exc}"
            ) from exc
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail="generation failed")

    name = f"{body.prompt[:40].strip() or 'generated'}.png"
    record = file_manager_storage.save_file(
        name=name, content=resp.content, content_type="image/png"
    )
    return GenerateOut(file_id=record.id, name=record.name)


POLL_INTERVAL_SECONDS = 20.0


async def poll_session_status() -> None:
    """Background loop (started from `main.py`'s lifespan) reconciling the
    active session against real EC2 state — catches a session left running
    in the DB after its instance was terminated out-of-band (e.g. from the
    AWS console), so the header widget doesn't keep showing "running"
    forever for an instance that's actually gone."""
    while True:
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
        try:
            session = db.get_active_session()
            if session is None or not session["instance_id"]:
                continue
            if (
                ec2.describe_instance_state(session["instance_id"]) == "terminated"
                and session["status"] not in ("terminated", "failed")
            ):
                db.update_session(session["id"], status="terminated")
        except Exception:  # noqa: BLE001 -- one bad poll shouldn't kill the loop
            pass
