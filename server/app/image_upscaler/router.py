import asyncio
import threading
import uuid
from dataclasses import dataclass, field

from fastapi import APIRouter, HTTPException, UploadFile
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from app.db import insert_activity
from app.file_manager import storage as file_manager_storage
from app.image_upscaler.upscaler import SCALE_FACTORS, JobCancelled, upscale_image

router = APIRouter(prefix="/image-upscaler", tags=["image-upscaler"])

# The model pipeline is a single shared (non-thread-safe) instance, and this
# is a personal, low-volume tool — serialize upscale runs rather than risk
# concurrent calls into the same model object.
_run_lock = threading.Lock()


@dataclass
class Job:
    status: str = "running"  # running, succeeded, failed, cancelled
    progress: int = 0
    total: int = 0
    file_id: int | None = None
    name: str | None = None
    content_type: str | None = None
    size_bytes: int | None = None
    error: str | None = None
    diagnostics: str | None = None
    cancel_requested: threading.Event = field(default_factory=threading.Event)


_jobs: dict[str, Job] = {}


def _run(job_id: str, content: bytes, scale: int, base_name: str) -> None:
    job = _jobs[job_id]

    def on_progress(current: int, total: int) -> None:
        job.progress = current
        job.total = total

    try:
        with _run_lock:
            result = upscale_image(
                content,
                scale,
                on_progress=on_progress,
                should_cancel=job.cancel_requested.is_set,
            )
    except JobCancelled:
        job.status = "cancelled"
        return
    except Exception as exc:  # noqa: BLE001 -- surfaced via job.error, not swallowed
        job.status = "failed"
        job.error = str(exc)
        return

    # Face restoration is silent by nature (no faces found, or GFPGAN's own
    # inference failing) — surface it instead of it disappearing, since a
    # photo with no restoration applied looks identical to a plain resize.
    if result.faces_detected == 0:
        job.diagnostics = "no face detected — only background upsampling was applied"
    elif result.gfpgan_failures:
        job.diagnostics = f"GFPGAN inference failed for {len(result.gfpgan_failures)} face(s)"
    if job.diagnostics:
        try:
            insert_activity(source="image-upscaler", level="warning", message=job.diagnostics)
        except Exception:  # noqa: BLE001 -- logging must never fail the job itself
            pass

    record = file_manager_storage.save_file(
        name=f"{base_name}-{scale}x.png", content=result.image, content_type="image/png"
    )
    job.status = "succeeded"
    job.file_id = record.id
    job.name = record.name
    job.content_type = record.content_type
    job.size_bytes = record.size_bytes


class StartJobOut(BaseModel):
    job_id: str


@router.post("/upscale", response_model=StartJobOut)
async def start_upscale(file: UploadFile, scale: int = 2):
    if scale not in SCALE_FACTORS:
        raise HTTPException(
            status_code=400,
            detail=f"scale must be one of {SCALE_FACTORS}",
        )

    content = await file.read()
    base_name = (file.filename or "image").rsplit(".", 1)[0]
    job_id = uuid.uuid4().hex
    _jobs[job_id] = Job()

    async def run_in_background():
        await run_in_threadpool(_run, job_id, content, scale, base_name)

    asyncio.create_task(run_in_background())
    return StartJobOut(job_id=job_id)


class JobOut(BaseModel):
    status: str
    progress: int
    total: int
    file_id: int | None
    name: str | None
    content_type: str | None
    size_bytes: int | None
    error: str | None
    diagnostics: str | None


def _job_out(job: Job) -> JobOut:
    return JobOut(
        status=job.status,
        progress=job.progress,
        total=job.total,
        file_id=job.file_id,
        name=job.name,
        content_type=job.content_type,
        size_bytes=job.size_bytes,
        error=job.error,
        diagnostics=job.diagnostics,
    )


@router.get("/jobs/{job_id}", response_model=JobOut)
def get_job(job_id: str):
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return _job_out(job)


@router.post("/jobs/{job_id}/cancel", response_model=JobOut)
def cancel_job(job_id: str):
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    if job.status == "running":
        job.cancel_requested.set()
    return _job_out(job)
