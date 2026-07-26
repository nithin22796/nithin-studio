from fastapi import APIRouter, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

from app.file_manager import storage

router = APIRouter(prefix="/file-manager", tags=["file-manager"])

# `content_type` is whatever the uploading client claimed at upload time —
# never validated against the actual bytes — so serving arbitrary types
# `inline` lets a crafted upload (e.g. an SVG containing a <script> tag, or
# a plain file uploaded with Content-Type: text/html) execute as a document
# on this server's own origin when viewed. Only types that can't carry
# executable content are allowed to render inline; everything else is
# forced to `attachment` regardless of what the caller asked for.
_INLINE_SAFE_CONTENT_TYPES = {
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/webp",
    "application/pdf",
}


class FolderOut(BaseModel):
    id: int
    name: str
    parent_id: int | None


class FileOut(BaseModel):
    id: int
    name: str
    folder_id: int | None
    content_type: str
    size_bytes: int


class ContentsOut(BaseModel):
    folders: list[FolderOut]
    files: list[FileOut]


def _folder_out(folder: storage.Folder) -> FolderOut:
    return FolderOut(id=folder.id, name=folder.name, parent_id=folder.parent_id)


def _file_out(file: storage.FileRecord) -> FileOut:
    return FileOut(
        id=file.id,
        name=file.name,
        folder_id=file.folder_id,
        content_type=file.content_type,
        size_bytes=file.size_bytes,
    )


def _contents_out(folders: list[storage.Folder], files: list[storage.FileRecord]) -> ContentsOut:
    return ContentsOut(
        folders=[_folder_out(f) for f in folders], files=[_file_out(f) for f in files]
    )


@router.get("/items", response_model=ContentsOut)
def list_items(folder_id: int | None = None):
    try:
        folders, files = storage.list_contents(folder_id)
    except storage.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _contents_out(folders, files)


@router.get("/search", response_model=ContentsOut)
def search_items(q: str = Query(min_length=1)):
    folders, files = storage.search(q)
    return _contents_out(folders, files)


@router.get("/folders", response_model=list[FolderOut])
def list_all_folders():
    return [_folder_out(f) for f in storage.list_all_folders()]


class CreateFolderRequest(BaseModel):
    name: str
    parent_id: int | None = None


@router.post("/folders", response_model=FolderOut)
def create_folder(body: CreateFolderRequest):
    try:
        folder = storage.create_folder(body.name, body.parent_id)
    except storage.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _folder_out(folder)


@router.post("/folders/get-or-create", response_model=FolderOut)
def get_or_create_folder(body: CreateFolderRequest):
    try:
        folder = storage.get_or_create_folder(body.name, body.parent_id)
    except storage.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _folder_out(folder)


class RenameFolderRequest(BaseModel):
    name: str


@router.patch("/folders/{folder_id}", response_model=FolderOut)
def rename_folder(folder_id: int, body: RenameFolderRequest):
    try:
        folder = storage.rename_folder(folder_id, body.name)
    except storage.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _folder_out(folder)


@router.delete("/folders/{folder_id}", status_code=204)
def delete_folder(folder_id: int):
    try:
        storage.delete_folder(folder_id)
    except storage.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except storage.ConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/files", response_model=FileOut)
async def upload_file(file: UploadFile, folder_id: int | None = None):
    content = await file.read()
    try:
        record = storage.save_file(
            name=file.filename or "untitled",
            content=content,
            content_type=file.content_type or "application/octet-stream",
            folder_id=folder_id,
        )
    except storage.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _file_out(record)


@router.get("/files/{file_id}/content")
def download_file(file_id: int, disposition: str = "attachment"):
    try:
        record, body = storage.get_file_content(file_id)
    except storage.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    safe_disposition = (
        "inline"
        if disposition == "inline" and record.content_type in _INLINE_SAFE_CONTENT_TYPES
        else "attachment"
    )
    return Response(
        content=body,
        media_type=record.content_type,
        headers={"Content-Disposition": f'{safe_disposition}; filename="{record.name}"'},
    )


class RenameFileRequest(BaseModel):
    name: str


@router.patch("/files/{file_id}", response_model=FileOut)
def rename_file(file_id: int, body: RenameFileRequest):
    try:
        record = storage.rename_file(file_id, body.name)
    except storage.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _file_out(record)


class MoveFileRequest(BaseModel):
    folder_id: int | None = None


@router.post("/files/{file_id}/move", response_model=FileOut)
def move_file(file_id: int, body: MoveFileRequest):
    try:
        record = storage.move_file(file_id, body.folder_id)
    except storage.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _file_out(record)


@router.delete("/files/{file_id}", status_code=204)
def delete_file(file_id: int):
    try:
        storage.delete_file(file_id)
    except storage.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
