import asyncio
import json
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from sse_starlette.sse import EventSourceResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import JSONResponse

from app.broadcast import broadcaster
from app.db import init_db, insert_activity, insert_alert, list_activity, list_alerts
from app.file_manager.db import init_db as init_file_manager_db
from app.file_manager.router import router as file_manager_router
from app.file_manager.storage import ensure_bucket as ensure_file_manager_bucket
from app.frame_extractor.router import router as frame_extractor_router
from app.image_generator.db import init_db as init_image_generator_db
from app.image_generator.router import poll_session_status
from app.image_generator.router import router as image_generator_router
from app.image_importer.router import router as image_importer_router
from app.image_upscaler.router import router as image_upscaler_router
from app.llm_chat.router import router as llm_chat_router
from app.lora_trainer.db import init_db as init_lora_trainer_db
from app.lora_trainer.router import poll_running_jobs
from app.lora_trainer.router import router as lora_trainer_router

CLIENT_ORIGIN = os.environ.get("CLIENT_ORIGIN", "http://localhost:5173")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    init_file_manager_db()
    ensure_file_manager_bucket()
    init_lora_trainer_db()
    init_image_generator_db()
    poll_task = asyncio.create_task(poll_running_jobs())
    session_poll_task = asyncio.create_task(poll_session_status())
    yield
    poll_task.cancel()
    session_poll_task.cancel()


app = FastAPI(title="nithin-studio server", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[CLIENT_ORIGIN],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(frame_extractor_router)
app.include_router(file_manager_router)
app.include_router(lora_trainer_router)
app.include_router(image_upscaler_router)
app.include_router(image_generator_router)
app.include_router(llm_chat_router)
app.include_router(image_importer_router)


def _source_from_path(path: str) -> str:
    parts = [p for p in path.split("/") if p]
    return parts[0] if parts else "nithin-studio"


async def _record_activity(request: Request, level: str, message: str) -> None:
    # Logging is a side effect, never allowed to break the response it's logging.
    try:
        source = _source_from_path(request.url.path)
        activity = insert_activity(source=source, level=level, message=message)
        await broadcaster.publish({**jsonable_encoder(activity), "kind": "activity"})
    except Exception:
        pass


@app.exception_handler(StarletteHTTPException)
async def handle_http_exception(request: Request, exc: StarletteHTTPException):
    if exc.status_code >= 400:
        level = "error" if exc.status_code >= 500 else "warning"
        await _record_activity(request, level, str(exc.detail))
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
        headers=getattr(exc, "headers", None),
    )


@app.exception_handler(Exception)
async def handle_unhandled_exception(request: Request, exc: Exception):
    await _record_activity(request, "error", str(exc))
    return JSONResponse(status_code=500, content={"detail": "internal server error"})


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/alerts")
def get_alerts():
    return jsonable_encoder(list_alerts())


@app.get("/activity")
def get_activity():
    return jsonable_encoder(list_activity())


def _extract_alert(payload: dict) -> tuple[str, str, str | None]:
    monitor = payload.get("monitor", {}) or {}
    heartbeat = payload.get("heartbeat", {}) or {}
    monitor_name = monitor.get("name", "unknown")
    status = "up" if heartbeat.get("status") == 1 else "down"
    message = payload.get("msg") or heartbeat.get("msg")
    return monitor_name, status, message


@app.post("/webhook/uptime-kuma")
async def uptime_kuma_webhook(request: Request):
    payload = await request.json()
    monitor_name, status, message = _extract_alert(payload)
    alert = insert_alert(monitor_name, status, message)
    await broadcaster.publish({**jsonable_encoder(alert), "kind": "alert"})
    return {"received": True}


@app.get("/events")
async def events():
    queue = broadcaster.subscribe()

    async def event_generator():
        try:
            while True:
                message = await queue.get()
                yield {"event": message.get("kind", "alert"), "data": json.dumps(message)}
        finally:
            broadcaster.unsubscribe(queue)

    return EventSourceResponse(event_generator())
