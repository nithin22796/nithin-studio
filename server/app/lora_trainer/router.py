import asyncio
import base64
import io
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from fastapi import APIRouter, HTTPException, Response, UploadFile
from PIL import Image
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from app.file_manager import storage as file_manager_storage
from app.lora_trainer import db, duplicates, ec2, local_uploads, people, s3
from app.lora_trainer.captioning import caption_image
from app.shared_models import ModelMissingError

router = APIRouter(prefix="/lora-trainer", tags=["lora-trainer"])

# Mirrors file_manager.router's `_INLINE_SAFE_CONTENT_TYPES` — `content_type`
# here is whatever the uploading client claimed, never validated against the
# actual bytes, so serving arbitrary types `inline` would let a crafted
# upload (e.g. an SVG containing a <script> tag) execute as a document on
# this server's own origin. Dataset images are only ever real photos, so
# there's no need for file-manager's PDF entry here.
_INLINE_SAFE_CONTENT_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}


def _dataset_image_content(dataset_image_id: int) -> bytes:
    """Local uploads get negative ids (see `local_uploads`); everything else
    is a regular file-manager id. Every dataset-image read in this module
    goes through here so callers don't need to care which store an id
    belongs to."""
    if dataset_image_id < 0:
        _, _, content = local_uploads.get_upload(dataset_image_id)
        return content
    _, content = file_manager_storage.get_file_content(dataset_image_id)
    return content


def _dataset_image_name(dataset_image_id: int) -> str:
    if dataset_image_id < 0:
        name, _, _ = local_uploads.get_upload(dataset_image_id)
        return name
    return file_manager_storage.get_file(dataset_image_id).name


class UploadOut(BaseModel):
    file_manager_id: int
    name: str


@router.post("/uploads", response_model=UploadOut)
async def upload_dataset_image(file: UploadFile):
    """Saves a fresh dataset upload to local disk, not file-manager/MinIO —
    see `local_uploads` for why. Named to match file-manager's `FileOut`
    shape (`file_manager_id`) purely so the client can treat every dataset
    image the same way regardless of where it came from."""
    content = await file.read()
    upload_id = local_uploads.save_upload(
        name=file.filename or "untitled",
        content_type=file.content_type or "application/octet-stream",
        content=content,
    )
    return UploadOut(file_manager_id=upload_id, name=file.filename or "untitled")


@router.get("/uploads/content")
def get_upload_content(id: int, disposition: str = "attachment"):  # noqa: A002 -- matches file-manager's query param naming
    try:
        name, content_type, content = local_uploads.get_upload(id)
    except local_uploads.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    safe_disposition = (
        "inline"
        if disposition == "inline" and content_type in _INLINE_SAFE_CONTENT_TYPES
        else "attachment"
    )
    return Response(
        content=content,
        media_type=content_type,
        headers={"Content-Disposition": f'{safe_disposition}; filename="{name}"'},
    )


class CaptionRequest(BaseModel):
    file_manager_id: int


class CaptionResponse(BaseModel):
    caption: str


@router.post("/caption", response_model=CaptionResponse)
def caption(body: CaptionRequest):
    try:
        _, content = file_manager_storage.get_file_content(body.file_manager_id)
    except file_manager_storage.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    try:
        return CaptionResponse(caption=caption_image(content))
    except ModelMissingError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


class CaptionResult(BaseModel):
    file_manager_id: int
    caption: str


@dataclass
class CaptionBatchJob:
    status: str = "running"  # running, succeeded, failed, cancelled
    captions: list[CaptionResult] = field(default_factory=list)
    error: str | None = None
    progress: float = 0.0
    cancel_requested: threading.Event = field(default_factory=threading.Event)


_caption_batch_jobs: dict[str, CaptionBatchJob] = {}


def _run_caption_batch(job_id: str, file_manager_ids: list[int]) -> None:
    job = _caption_batch_jobs[job_id]
    try:
        total = len(file_manager_ids)
        captions = []
        for i, file_id in enumerate(file_manager_ids):
            if job.cancel_requested.is_set():
                job.status = "cancelled"
                return
            content = _dataset_image_content(file_id)
            captions.append(CaptionResult(file_manager_id=file_id, caption=caption_image(content)))
            job.progress = (i + 1) / total
    except Exception as exc:  # noqa: BLE001 -- surfaced via job.error, not swallowed
        job.status = "failed"
        job.error = str(exc)
        return
    job.status = "succeeded"
    job.captions = captions
    job.progress = 1.0


class StartCaptionBatchRequest(BaseModel):
    file_manager_ids: list[int]


class StartCaptionBatchOut(BaseModel):
    job_id: str


@router.post("/captions", response_model=StartCaptionBatchOut)
async def start_caption_batch(body: StartCaptionBatchRequest):
    if not body.file_manager_ids:
        raise HTTPException(status_code=400, detail="at least one image is required")

    job_id = uuid.uuid4().hex
    _caption_batch_jobs[job_id] = CaptionBatchJob()

    async def run_in_background():
        await run_in_threadpool(_run_caption_batch, job_id, body.file_manager_ids)

    asyncio.create_task(run_in_background())
    return StartCaptionBatchOut(job_id=job_id)


class CaptionBatchOut(BaseModel):
    status: str
    captions: list[CaptionResult]
    error: str | None
    progress: float


@router.get("/captions/{job_id}", response_model=CaptionBatchOut)
def get_caption_batch(job_id: str):
    job = _caption_batch_jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return CaptionBatchOut(
        status=job.status, captions=job.captions, error=job.error, progress=job.progress
    )


@router.post("/captions/{job_id}/cancel", response_model=CaptionBatchOut)
def cancel_caption_batch(job_id: str):
    job = _caption_batch_jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    if job.status == "running":
        job.cancel_requested.set()
    return CaptionBatchOut(
        status=job.status, captions=job.captions, error=job.error, progress=job.progress
    )


@dataclass
class DuplicateCheckJob:
    status: str = "running"  # running, succeeded, failed, cancelled
    groups: list[list[int]] = field(default_factory=list)
    error: str | None = None
    progress: float = 0.0
    cancel_requested: threading.Event = field(default_factory=threading.Event)


_duplicate_jobs: dict[str, DuplicateCheckJob] = {}
# Hashing runs in a thread via run_in_threadpool already, so this only
# guards against two checks racing on the same job dict — not a real
# bottleneck since each job is independent (unlike image-upscaler's shared
# model instance).
_duplicate_lock = threading.Lock()


def _run_duplicate_check(job_id: str, file_manager_ids: list[int]) -> None:
    job = _duplicate_jobs[job_id]
    try:
        total = len(file_manager_ids)
        hashes = []
        for i, file_id in enumerate(file_manager_ids):
            if job.cancel_requested.is_set():
                job.status = "cancelled"
                return
            content = _dataset_image_content(file_id)
            hashes.append((file_id, duplicates.phash(content)))
            job.progress = (i + 1) / total
        with _duplicate_lock:
            groups = duplicates.group_hashes(hashes)
    except Exception as exc:  # noqa: BLE001 -- surfaced via job.error, not swallowed
        job.status = "failed"
        job.error = str(exc)
        return
    job.status = "succeeded"
    job.groups = groups
    job.progress = 1.0


class StartDuplicateCheckRequest(BaseModel):
    file_manager_ids: list[int]


class StartDuplicateCheckOut(BaseModel):
    job_id: str


@router.post("/duplicates", response_model=StartDuplicateCheckOut)
async def start_duplicate_check(body: StartDuplicateCheckRequest):
    if not body.file_manager_ids:
        raise HTTPException(status_code=400, detail="at least one image is required")

    job_id = uuid.uuid4().hex
    _duplicate_jobs[job_id] = DuplicateCheckJob()

    async def run_in_background():
        await run_in_threadpool(_run_duplicate_check, job_id, body.file_manager_ids)

    asyncio.create_task(run_in_background())
    return StartDuplicateCheckOut(job_id=job_id)


class DuplicateCheckOut(BaseModel):
    status: str
    groups: list[list[int]]
    error: str | None
    progress: float


@router.get("/duplicates/{job_id}", response_model=DuplicateCheckOut)
def get_duplicate_check(job_id: str):
    job = _duplicate_jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return DuplicateCheckOut(
        status=job.status, groups=job.groups, error=job.error, progress=job.progress
    )


@router.post("/duplicates/{job_id}/cancel", response_model=DuplicateCheckOut)
def cancel_duplicate_check(job_id: str):
    job = _duplicate_jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    if job.status == "running":
        job.cancel_requested.set()
    return DuplicateCheckOut(
        status=job.status, groups=job.groups, error=job.error, progress=job.progress
    )


class PeopleIdentity(BaseModel):
    embedding: list[float]
    thumbnail: str  # base64-encoded JPEG
    face_count: int


@dataclass
class PeopleDetectJob:
    status: str = "running"  # running, succeeded, failed, cancelled
    identities: list[PeopleIdentity] = field(default_factory=list)
    error: str | None = None
    progress: float = 0.0
    cancel_requested: threading.Event = field(default_factory=threading.Event)


_people_detect_jobs: dict[str, PeopleDetectJob] = {}


def _run_people_detect(job_id: str, file_manager_ids: list[int]) -> None:
    job = _people_detect_jobs[job_id]
    try:
        contents: dict[int, bytes] = {}
        for file_id in file_manager_ids:
            if job.cancel_requested.is_set():
                job.status = "cancelled"
                return
            contents[file_id] = _dataset_image_content(file_id)

        def on_progress(done: int, total: int) -> None:
            job.progress = done / total

        faces = people.detect_all_faces(
            list(contents.items()), on_progress, should_cancel=job.cancel_requested.is_set
        )
        clusters = people.cluster_identities(faces)

        identities = []
        for member_indices in clusters:
            rep = people.representative_face(faces, member_indices)
            image = Image.open(io.BytesIO(contents[rep.file_id])).convert("RGB")
            thumbnail = people.crop_face_thumbnail(image, rep.bbox)
            identities.append(
                PeopleIdentity(
                    embedding=rep.embedding.tolist(),
                    thumbnail=base64.b64encode(thumbnail).decode("ascii"),
                    face_count=len(member_indices),
                )
            )
    except people.DetectionCancelled:
        job.status = "cancelled"
        return
    except Exception as exc:  # noqa: BLE001 -- surfaced via job.error, not swallowed
        job.status = "failed"
        job.error = str(exc)
        return
    job.status = "succeeded"
    job.identities = identities
    job.progress = 1.0


class StartPeopleDetectRequest(BaseModel):
    file_manager_ids: list[int]


class StartPeopleDetectOut(BaseModel):
    job_id: str


@router.post("/people/detect", response_model=StartPeopleDetectOut)
async def start_people_detect(body: StartPeopleDetectRequest):
    if not body.file_manager_ids:
        raise HTTPException(status_code=400, detail="at least one image is required")

    job_id = uuid.uuid4().hex
    _people_detect_jobs[job_id] = PeopleDetectJob()

    async def run_in_background():
        await run_in_threadpool(_run_people_detect, job_id, body.file_manager_ids)

    asyncio.create_task(run_in_background())
    return StartPeopleDetectOut(job_id=job_id)


class PeopleDetectOut(BaseModel):
    status: str
    identities: list[PeopleIdentity]
    error: str | None
    progress: float


@router.get("/people/detect/{job_id}", response_model=PeopleDetectOut)
def get_people_detect(job_id: str):
    job = _people_detect_jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return PeopleDetectOut(
        status=job.status, identities=job.identities, error=job.error, progress=job.progress
    )


@router.post("/people/detect/{job_id}/cancel", response_model=PeopleDetectOut)
def cancel_people_detect(job_id: str):
    job = _people_detect_jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    if job.status == "running":
        job.cancel_requested.set()
    return PeopleDetectOut(
        status=job.status, identities=job.identities, error=job.error, progress=job.progress
    )


class PersonCropResult(BaseModel):
    file_manager_id: int
    # One box per matched instance of the trained person in this photo — a
    # collage containing the person more than once produces more than one
    # box here, each becoming its own training image. Empty means the photo
    # either has one face (nothing to crop) or no face matching the
    # selected identity (passed through unmodified).
    crops: list[list[int]]


@dataclass
class PeopleSelectJob:
    status: str = "running"  # running, succeeded, failed, cancelled
    results: list[PersonCropResult] = field(default_factory=list)
    error: str | None = None
    progress: float = 0.0
    cancel_requested: threading.Event = field(default_factory=threading.Event)


_people_select_jobs: dict[str, PeopleSelectJob] = {}


def _run_people_select(
    job_id: str, file_manager_ids: list[int], embeddings: list[list[float]]
) -> None:
    job = _people_select_jobs[job_id]
    try:
        targets = [np.array(embedding, dtype=np.float32) for embedding in embeddings]
        total = len(file_manager_ids)
        results = []
        for i, file_id in enumerate(file_manager_ids):
            if job.cancel_requested.is_set():
                job.status = "cancelled"
                return
            content = _dataset_image_content(file_id)
            faces = people.detect_faces(content)
            crops: list[list[int]] = []
            if len(faces) > 1:
                matches = people.all_matches(targets, faces)
                if matches:
                    image = Image.open(io.BytesIO(content)).convert("RGB")
                    crops = [people.person_box(match.bbox, image.size) for match in matches]
            results.append(PersonCropResult(file_manager_id=file_id, crops=crops))
            job.progress = (i + 1) / total
    except Exception as exc:  # noqa: BLE001 -- surfaced via job.error, not swallowed
        job.status = "failed"
        job.error = str(exc)
        return
    job.status = "succeeded"
    job.results = results
    job.progress = 1.0


class StartPeopleSelectRequest(BaseModel):
    file_manager_ids: list[int]
    embeddings: list[list[float]]


class StartPeopleSelectOut(BaseModel):
    job_id: str


@router.post("/people/select", response_model=StartPeopleSelectOut)
async def start_people_select(body: StartPeopleSelectRequest):
    if not body.file_manager_ids:
        raise HTTPException(status_code=400, detail="at least one image is required")
    if not body.embeddings:
        raise HTTPException(status_code=400, detail="at least one identity is required")

    job_id = uuid.uuid4().hex
    _people_select_jobs[job_id] = PeopleSelectJob()

    async def run_in_background():
        await run_in_threadpool(_run_people_select, job_id, body.file_manager_ids, body.embeddings)

    asyncio.create_task(run_in_background())
    return StartPeopleSelectOut(job_id=job_id)


class PeopleSelectOut(BaseModel):
    status: str
    results: list[PersonCropResult]
    error: str | None
    progress: float


@router.get("/people/select/{job_id}", response_model=PeopleSelectOut)
def get_people_select(job_id: str):
    job = _people_select_jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return PeopleSelectOut(
        status=job.status, results=job.results, error=job.error, progress=job.progress
    )


@router.post("/people/select/{job_id}/cancel", response_model=PeopleSelectOut)
def cancel_people_select(job_id: str):
    job = _people_select_jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    if job.status == "running":
        job.cancel_requested.set()
    return PeopleSelectOut(
        status=job.status, results=job.results, error=job.error, progress=job.progress
    )


@router.get("/crop-preview")
def crop_preview(file_manager_id: int, x1: int, y1: int, x2: int, y2: int):
    """Renders a person-box crop on demand, for the People step's per-photo
    confirmation preview — nothing is stored, this just crops in memory and
    returns the JPEG bytes directly so the client can point an <img> at it.
    `file_manager_id` is a query param (not a path segment) so it can be
    negative for local uploads — the default path `int` converter's regex
    doesn't allow a leading `-`."""
    try:
        content = _dataset_image_content(file_manager_id)
    except (file_manager_storage.NotFoundError, local_uploads.NotFoundError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    image = Image.open(io.BytesIO(content)).convert("RGB")
    cropped = image.crop((x1, y1, x2, y2))
    buffer = io.BytesIO()
    cropped.save(buffer, format="JPEG", quality=90)
    return Response(content=buffer.getvalue(), media_type="image/jpeg")


class JobImage(BaseModel):
    file_manager_id: int
    caption: str
    crop: list[int] | None = None


class CreateJobRequest(BaseModel):
    trigger_word: str
    base_model: str = "sdxl"
    steps: int = 1000
    rank: int = 16
    # sd-scripts defaults this to 1 if omitted, which scales the LoRA's
    # effective learning signal down by rank/1 — defaulting it to match
    # `rank` here instead gives a sane full-strength starting point.
    alpha: int = 16
    images: list[JobImage]


class JobOut(BaseModel):
    id: int
    status: str
    trigger_word: str
    base_model: str
    steps: int
    rank: int
    alpha: int
    error_message: str | None
    output_file_id: int | None
    created_at: str
    updated_at: str
    # 0-1, or None if the instance hasn't reported a step yet (still
    # syncing the dataset/loading the model) or the job isn't running.
    progress: float | None = None
    # Coarse phase ('syncing_dataset', 'loading_model') for the gap before
    # `progress` exists — None once progress is available, or if the job
    # isn't running.
    phase: str | None = None


def _job_out(job: dict, fetch_progress: bool = True) -> JobOut:
    progress = None
    phase = None
    if fetch_progress and job["status"] == "running":
        current_step = s3.read_progress(job["id"])
        if current_step is not None:
            progress = min(1.0, current_step / job["steps"])
        else:
            phase = s3.read_phase(job["id"])
    return JobOut(
        id=job["id"],
        status=job["status"],
        trigger_word=job["trigger_word"],
        base_model=job["base_model"],
        steps=job["steps"],
        rank=job["rank"],
        alpha=job["alpha"],
        error_message=job["error_message"],
        output_file_id=job["output_file_id"],
        created_at=job["created_at"].isoformat(),
        updated_at=job["updated_at"].isoformat(),
        progress=progress,
        phase=phase,
    )


@router.post("/jobs", response_model=JobOut)
def create_job(body: CreateJobRequest):
    if not body.images:
        raise HTTPException(status_code=400, detail="at least one image is required")
    if body.steps <= 0 or body.rank <= 0 or body.alpha <= 0:
        raise HTTPException(status_code=400, detail="steps, rank, and alpha must be positive")

    images_meta = []
    for image in body.images:
        try:
            filename = _dataset_image_name(image.file_manager_id)
        except (file_manager_storage.NotFoundError, local_uploads.NotFoundError) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        images_meta.append(
            {
                "file_manager_id": image.file_manager_id,
                "filename": filename,
                "caption": image.caption,
                "crop": image.crop,
            }
        )

    job = db.create_job(
        trigger_word=body.trigger_word,
        base_model=body.base_model,
        steps=body.steps,
        rank=body.rank,
        alpha=body.alpha,
        images=images_meta,
    )

    for i, image_meta in enumerate(images_meta):
        content = _dataset_image_content(image_meta["file_manager_id"])
        crop = image_meta["crop"]
        if crop is not None:
            image = Image.open(io.BytesIO(content)).convert("RGB")
            cropped = image.crop(tuple(crop))
            buffer = io.BytesIO()
            cropped.save(buffer, format="JPEG", quality=95)
            content = buffer.getvalue()
        suffix = ".jpg" if crop is not None else (Path(image_meta["filename"]).suffix or ".jpg")
        s3.upload_dataset_image(job["id"], f"{i:04d}{suffix}", content, image_meta["caption"])

    # The dataset is safely in S3 now — local-upload staging files (negative
    # ids) served their purpose and can go; file-manager-sourced images
    # aren't touched, since those live in the user's organized library.
    for image_meta in images_meta:
        if image_meta["file_manager_id"] < 0:
            local_uploads.delete_upload(image_meta["file_manager_id"])

    try:
        instance_id = ec2.launch_training_instance(
            job["id"], body.trigger_word, body.steps, body.rank, body.alpha
        )
    except Exception as exc:
        # The dataset is already in S3 at this point — if the instance never
        # launches (e.g. an AWS quota/capacity error), there's nothing left
        # to clean it up otherwise: `_refresh_job` only acts on "running"
        # jobs, so an uncaught failure here would leave the job stuck at
        # "queued" forever with its dataset orphaned in S3 indefinitely.
        s3.delete_job_prefix(job["id"])
        job = db.update_job(job["id"], status="failed", error_message=str(exc))
        raise HTTPException(
            status_code=502, detail=f"failed to launch training instance: {exc}"
        ) from exc

    job = db.update_job(job["id"], status="running", instance_id=instance_id)
    # The instance just launched — it hasn't reported a training step yet,
    # so skip the S3 progress lookup rather than making a guaranteed-empty
    # round trip.
    return _job_out(job, fetch_progress=False)


def _refresh_job(job: dict) -> dict:
    if job["status"] != "running":
        return job

    marker = s3.output_marker(job["id"])
    if marker == "done":
        model_bytes = s3.download_model(job["id"])
        file_record = file_manager_storage.save_file(
            name=f"{job['trigger_word']}-lora.safetensors",
            content=model_bytes,
            content_type="application/octet-stream",
        )
        s3.delete_job_prefix(job["id"])
        ec2.terminate_instance(job["instance_id"])
        return db.update_job(job["id"], status="succeeded", output_file_id=file_record.id)

    if marker == "failed":
        error_log = s3.read_error_log(job["id"])
        # Unlike success, the dataset is deliberately *not* deleted here —
        # it's what makes `retry_job` possible without re-uploading
        # everything. The bucket's lifecycle rule (see `aws/setup.sh`)
        # still expires it after a day as a safety net.
        ec2.terminate_instance(job["instance_id"])
        return db.update_job(job["id"], status="failed", error_message=error_log[-2000:])

    if ec2.describe_instance_state(job["instance_id"]) == "terminated":
        return db.update_job(
            job["id"],
            status="failed",
            error_message="training instance terminated without producing a result",
        )

    return job


@router.get("/jobs", response_model=list[JobOut])
def list_jobs():
    # Only undismissed jobs — since just one job trains at a time, this is
    # normally the running job or a single finished job awaiting a look, not
    # the full history (which stays in Postgres regardless, just unlisted
    # here — see `dismiss_job`).
    jobs = [_refresh_job(job) for job in db.list_active_jobs()]
    return [_job_out(job) for job in jobs]


@router.get("/jobs/{job_id}", response_model=JobOut)
def get_job(job_id: int):
    job = db.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return _job_out(_refresh_job(job))


@router.post("/jobs/{job_id}/dismiss", response_model=JobOut)
def dismiss_job(job_id: int):
    """Marks a finished job as seen — it stays in Postgres forever, just
    stops showing up in `list_jobs`. Only a job in a terminal state can be
    dismissed; a running job has nothing to "see" yet."""
    job = db.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    if job["status"] not in ("succeeded", "failed", "cancelled"):
        raise HTTPException(status_code=400, detail="only a finished job can be dismissed")
    job = db.update_job(job_id, dismissed=True)
    return _job_out(job, fetch_progress=False)


@router.post("/jobs/{job_id}/cancel", response_model=JobOut)
def cancel_job(job_id: int):
    """User-initiated stop: terminates the instance and deletes the S3
    dataset/output right away (unlike a natural failure, there's no reason
    to keep anything around for a retry — the user is deliberately calling
    this off)."""
    job = db.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    if job["status"] not in ("queued", "running"):
        raise HTTPException(status_code=400, detail="only a queued or running job can be cancelled")
    if job["instance_id"]:
        ec2.terminate_instance(job["instance_id"])
    s3.delete_job_prefix(job_id)
    job = db.update_job(job_id, status="cancelled", error_message="Cancelled by user")
    return _job_out(job, fetch_progress=False)


@router.post("/jobs/{job_id}/retry", response_model=JobOut)
def retry_job(job_id: int):
    """Relaunches training for a failed job against its *existing* S3
    dataset — `_refresh_job` deliberately doesn't delete it on failure, so
    this needs no re-upload. Only available for `failed` (not `cancelled`,
    whose dataset is deleted immediately — see `cancel_job`), and only
    within the bucket's ~1-day lifecycle window before the dataset expires."""
    job = db.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    if job["status"] != "failed":
        raise HTTPException(status_code=400, detail="only a failed job can be retried")
    try:
        instance_id = ec2.launch_training_instance(
            job_id, job["trigger_word"], job["steps"], job["rank"], job["alpha"]
        )
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"failed to relaunch training instance: {exc}"
        ) from exc
    job = db.update_job(
        job_id, status="running", instance_id=instance_id, error_message=None, dismissed=False
    )
    return _job_out(job, fetch_progress=False)


@router.get("/jobs/{job_id}/log")
def get_job_log(job_id: int):
    """Current train.log content, whatever's been uploaded so far — see
    `ec2.py`'s monitor loop, which re-uploads it every ~15s while training
    runs, not just once at the end. Returns 200 with an empty body if
    nothing's been uploaded yet — that's a normal, frequent, expected state
    while a client polls this during the sync/model-load phase, not an
    error: the app's global exception handler records every 4xx as a
    "warning" activity event, so a 404 here would flood the activity feed/
    notifications with a fake warning every ~4s for as long as a log
    viewer stays open on a job with nothing to show yet."""
    job = db.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    log = s3.read_current_log(job_id)
    return Response(content=log or "", media_type="text/plain")


POLL_RUNNING_JOBS_INTERVAL_SECONDS = 20.0


async def poll_running_jobs() -> None:
    """Background loop (started from `main.py`'s lifespan) so a running
    job's completion/failure — and the EC2 termination + S3 cleanup that
    come with it — happen even if nobody has the wizard open to poll
    `GET /jobs`. Without this, `_refresh_job` only ever runs "on read", so a
    job could sit fully finished (or failed) in S3 indefinitely, with its
    EC2 instance never getting the app's own redundant terminate call and
    its S3 job prefix never getting cleaned up, until someone happened to
    load the page again."""
    while True:
        await asyncio.sleep(POLL_RUNNING_JOBS_INTERVAL_SECONDS)
        try:
            for job in db.list_jobs():
                if job["status"] == "running":
                    _refresh_job(job)
        except Exception:  # noqa: BLE001 -- one bad poll shouldn't kill the loop
            pass
