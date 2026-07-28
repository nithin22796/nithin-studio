import asyncio
import mimetypes
import os
import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool
from starlette.responses import Response

from app.file_manager import storage as file_manager_storage
from app.file_manager.storage import NotFoundError
from app.image_importer.scraper import (
    HEADERS,
    download_image,
    fetch_page_image_urls,
    filename_from_url,
)

router = APIRouter(prefix="/image-importer", tags=["image-importer"])

# Not under shared_models.MODELS_DIR — this is scratch space for in-flight
# imports, not a model file. Each preview job gets its own subdirectory
# (named after the job id) so cleanup is always just deleting that one
# directory, never a risk of touching another job's files.
_DEFAULT_TEMP_DIR = "/Users/nithin/workspace/studio/nithin-studio/server/data/temp"
TEMP_DIR = Path(os.environ.get("IMAGE_IMPORTER_TEMP_DIR", _DEFAULT_TEMP_DIR))


@dataclass
class TempImage:
    id: str
    filename: str  # relative to TEMP_DIR / <preview job id>
    content_type: str
    original_url: str


@dataclass
class PreviewJob:
    status: str = "running"  # running, succeeded, failed, cancelled
    progress: int = 0
    total: int = 0
    images: list[TempImage] = field(default_factory=list)
    error: str | None = None
    cancel_requested: bool = False


@dataclass
class ImportJob:
    status: str = "running"  # running, succeeded, failed, cancelled
    progress: int = 0
    total: int = 0
    saved_file_ids: list[int] = field(default_factory=list)
    skipped: int = 0
    error: str | None = None
    cancel_requested: bool = False


_preview_jobs: dict[str, PreviewJob] = {}
_import_jobs: dict[str, ImportJob] = {}


def _discard_preview_job(preview_job_id: str) -> None:
    _preview_jobs.pop(preview_job_id, None)
    shutil.rmtree(TEMP_DIR / preview_job_id, ignore_errors=True)


def _clear_all_temp_dirs() -> None:
    """Wipes every job's temp files, not just a tracked one — this is the
    only scratch space this app uses, so a full clear is always safe. Called
    at the start of every new preview so that anything orphaned (e.g. a dev
    server reload killing an in-flight job's cleanup) can never accumulate
    across attempts, regardless of what caused it."""
    _preview_jobs.clear()
    _import_jobs.clear()
    shutil.rmtree(TEMP_DIR, ignore_errors=True)


def _local_filename(temp_id: str, url: str, content_type: str) -> str:
    suffix = Path(filename_from_url(url)).suffix
    if not suffix:
        suffix = mimetypes.guess_extension(content_type) or ""
    return f"{temp_id}{suffix}"


def _run_preview(preview_job_id: str, page_url: str) -> None:
    job = _preview_jobs[preview_job_id]
    try:
        image_urls = fetch_page_image_urls(page_url)
    except httpx.HTTPError as exc:
        job.status = "failed"
        job.error = f"failed to fetch page: {exc}"
        return

    job.total = len(image_urls)
    if not image_urls:
        job.status = "succeeded"
        return

    job_dir = TEMP_DIR / preview_job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    with httpx.Client(headers=HEADERS, follow_redirects=True, timeout=20.0) as client:
        for url in image_urls:
            if job.cancel_requested:
                job.status = "cancelled"
                return
            result = download_image(client, url)
            job.progress += 1
            if result is None:
                continue
            _, content, content_type = result
            temp_id = uuid.uuid4().hex
            filename = _local_filename(temp_id, url, content_type)
            (job_dir / filename).write_bytes(content)
            job.images.append(
                TempImage(
                    id=temp_id, filename=filename, content_type=content_type, original_url=url
                )
            )

    job.status = "succeeded"


class PreviewIn(BaseModel):
    url: str


class StartJobOut(BaseModel):
    job_id: str


@router.post("/preview", response_model=StartJobOut)
async def start_preview(body: PreviewIn):
    """Downloads every image found on the page into a local temp directory
    and reports progress as a job — the picker UI loads previews from here
    (see /temp/{job_id}/{image_id}) rather than hotlinking the original
    remote URLs directly."""
    _clear_all_temp_dirs()
    job_id = uuid.uuid4().hex
    _preview_jobs[job_id] = PreviewJob()

    async def run_in_background():
        await run_in_threadpool(_run_preview, job_id, body.url)

    asyncio.create_task(run_in_background())
    return StartJobOut(job_id=job_id)


class PreviewImageOut(BaseModel):
    id: str
    temp_url: str


class PreviewJobOut(BaseModel):
    status: str
    progress: int
    total: int
    images: list[PreviewImageOut]
    error: str | None


def _preview_job_out(job_id: str, job: PreviewJob) -> PreviewJobOut:
    return PreviewJobOut(
        status=job.status,
        progress=job.progress,
        total=job.total,
        images=[
            PreviewImageOut(id=img.id, temp_url=f"/image-importer/temp/{job_id}/{img.id}")
            for img in job.images
        ],
        error=job.error,
    )


@router.get("/preview-jobs/{job_id}", response_model=PreviewJobOut)
def get_preview_job(job_id: str):
    job = _preview_jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="preview job not found")
    return _preview_job_out(job_id, job)


@router.post("/preview-jobs/{job_id}/cancel", response_model=PreviewJobOut)
def cancel_preview_job(job_id: str):
    job = _preview_jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="preview job not found")
    if job.status == "running":
        job.cancel_requested = True
    return _preview_job_out(job_id, job)


@router.post("/preview-jobs/{job_id}/discard")
def discard_preview_job(job_id: str):
    """Deletes a preview job's downloaded temp files without importing them
    — called when the user navigates away from the page, or pastes a
    different URL before saving anything from the current one."""
    _discard_preview_job(job_id)
    return {"discarded": True}


@router.get("/temp/{job_id}/{image_id}")
def get_temp_image(job_id: str, image_id: str):
    job = _preview_jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="preview job not found")
    image = next((img for img in job.images if img.id == image_id), None)
    if image is None:
        raise HTTPException(status_code=404, detail="image not found")
    path = TEMP_DIR / job_id / image.filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail="temp file not found")
    return Response(content=path.read_bytes(), media_type=image.content_type)


def _run_import(
    import_job_id: str, preview_job_id: str, image_ids: list[str], folder_id: int | None
) -> None:
    job = _import_jobs[import_job_id]
    try:
        preview_job = _preview_jobs.get(preview_job_id)
        if preview_job is None:
            job.status = "failed"
            job.error = "preview job not found (may have already been imported or discarded)"
            return

        images_by_id = {img.id: img for img in preview_job.images}
        selected = [images_by_id[i] for i in image_ids if i in images_by_id]
        job.total = len(selected)
        if not selected:
            job.status = "succeeded"
            return

        job_dir = TEMP_DIR / preview_job_id
        for image in selected:
            if job.cancel_requested:
                job.status = "cancelled"
                return
            try:
                content = (job_dir / image.filename).read_bytes()
            except OSError:
                job.skipped += 1
                job.progress += 1
                continue
            name = filename_from_url(image.original_url)
            try:
                record = file_manager_storage.save_file(
                    name=name, content=content, content_type=image.content_type, folder_id=folder_id
                )
            except NotFoundError as exc:
                job.status = "failed"
                job.error = str(exc)
                return
            job.saved_file_ids.append(record.id)
            job.progress += 1

        job.status = "succeeded"
    except Exception as exc:  # noqa: BLE001 -- surfaced via job.error, not swallowed
        job.status = "failed"
        job.error = str(exc)
    finally:
        # Whatever happened (succeeded, failed, cancelled, or an unexpected
        # exception above), the temp download is scratch space for this one
        # attempt — never left behind to accumulate across imports.
        _discard_preview_job(preview_job_id)


class StartImportIn(BaseModel):
    preview_job_id: str
    image_ids: list[str]
    folder_id: int | None = None


@router.post("/import", response_model=StartJobOut)
async def start_import(body: StartImportIn):
    job_id = uuid.uuid4().hex
    _import_jobs[job_id] = ImportJob()

    async def run_in_background():
        await run_in_threadpool(
            _run_import, job_id, body.preview_job_id, body.image_ids, body.folder_id
        )

    asyncio.create_task(run_in_background())
    return StartJobOut(job_id=job_id)


class ImportJobOut(BaseModel):
    status: str
    progress: int
    total: int
    saved_file_ids: list[int]
    skipped: int
    error: str | None


def _import_job_out(job: ImportJob) -> ImportJobOut:
    return ImportJobOut(
        status=job.status,
        progress=job.progress,
        total=job.total,
        saved_file_ids=job.saved_file_ids,
        skipped=job.skipped,
        error=job.error,
    )


@router.get("/jobs/{job_id}", response_model=ImportJobOut)
def get_import_job(job_id: str):
    job = _import_jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return _import_job_out(job)


@router.post("/jobs/{job_id}/cancel", response_model=ImportJobOut)
def cancel_import_job(job_id: str):
    job = _import_jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    if job.status == "running":
        job.cancel_requested = True
    return _import_job_out(job)
