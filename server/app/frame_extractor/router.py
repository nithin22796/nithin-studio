import io
import os
import shutil
import tempfile
import uuid
import zipfile
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.file_manager import storage as file_manager_storage
from app.frame_extractor.extraction import extract_frames

JOBS_DIR = Path(os.environ.get("FRAME_EXTRACTOR_JOBS_DIR", "./data/frame-extractor-jobs"))

router = APIRouter(prefix="/frame-extractor", tags=["frame-extractor"])


def _job_dir(job_id: str) -> Path:
    job_dir = (JOBS_DIR / job_id).resolve()
    if JOBS_DIR.resolve() not in job_dir.parents:
        raise HTTPException(status_code=400, detail="invalid job id")
    return job_dir


@router.post("/extract")
async def extract(
    file: UploadFile | None = None,
    file_manager_file_id: int | None = None,
    interval_seconds: float = 1.0,
):
    if interval_seconds <= 0:
        raise HTTPException(status_code=400, detail="interval_seconds must be positive")
    if file is None and file_manager_file_id is None:
        raise HTTPException(
            status_code=400, detail="file or file_manager_file_id is required"
        )

    job_id = uuid.uuid4().hex
    job_dir = _job_dir(job_id)

    if file_manager_file_id is not None:
        try:
            record, content = file_manager_storage.get_file_content(file_manager_file_id)
        except file_manager_storage.NotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        suffix = Path(record.name).suffix
        with tempfile.NamedTemporaryFile(suffix=suffix) as tmp:
            tmp.write(content)
            tmp.flush()
            try:
                filenames = extract_frames(Path(tmp.name), job_dir, interval_seconds)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
    else:
        assert file is not None
        with tempfile.NamedTemporaryFile(suffix=Path(file.filename or "").suffix) as tmp:
            shutil.copyfileobj(file.file, tmp)
            tmp.flush()
            try:
                filenames = extract_frames(Path(tmp.name), job_dir, interval_seconds)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc

    return {
        "job_id": job_id,
        "frame_count": len(filenames),
        "frames": [f"/frame-extractor/jobs/{job_id}/frames/{name}" for name in filenames],
    }


@router.get("/jobs/{job_id}/frames/{filename}")
def get_frame(job_id: str, filename: str):
    frame_path = _job_dir(job_id) / filename
    if not frame_path.is_file():
        raise HTTPException(status_code=404, detail="frame not found")
    return StreamingResponse(open(frame_path, "rb"), media_type="image/jpeg")


def _zip_of(frame_paths: list[Path]) -> io.BytesIO:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for frame_path in frame_paths:
            archive.write(frame_path, arcname=frame_path.name)
    buffer.seek(0)
    return buffer


@router.get("/jobs/{job_id}/download")
def download_frames(job_id: str):
    job_dir = _job_dir(job_id)
    if not job_dir.is_dir():
        raise HTTPException(status_code=404, detail="job not found")

    buffer = _zip_of(sorted(job_dir.glob("frame_*.jpg")))

    return StreamingResponse(
        buffer,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{job_id}-frames.zip"'},
    )


class DownloadSelectionRequest(BaseModel):
    filenames: list[str]


@router.post("/jobs/{job_id}/download")
def download_selected_frames(job_id: str, selection: DownloadSelectionRequest):
    job_dir = _job_dir(job_id)
    if not job_dir.is_dir():
        raise HTTPException(status_code=404, detail="job not found")
    if not selection.filenames:
        raise HTTPException(status_code=400, detail="no filenames provided")

    resolved_dir = job_dir.resolve()
    frame_paths = []
    for name in selection.filenames:
        candidate = (job_dir / name).resolve()
        if resolved_dir not in candidate.parents or not candidate.is_file():
            raise HTTPException(status_code=400, detail=f"invalid filename: {name}")
        frame_paths.append(candidate)

    buffer = _zip_of(frame_paths)

    return StreamingResponse(
        buffer,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{job_id}-selected-frames.zip"'
        },
    )
