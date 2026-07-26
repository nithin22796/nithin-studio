"""Runs ON the EC2 instance (not part of the studio server process) — embedded
into `ec2.py:build_user_data` and started at boot. A small, persistent
inference server: loads the SDXL checkpoint once (lazily, on first real
request) and stays warm for the rest of the session, so only the very first
request after boot pays the model-load cost — every prompt after that is
just the actual generation time.

Every endpoint except `/health` requires `Authorization: Bearer <token>`,
checked against the `INFERENCE_TOKEN` env var baked into this instance's
user-data at launch — the one defense-in-depth layer on top of the security
group's IP restriction, since this is the only instance in this whole
project that accepts any inbound connection at all.
"""

import argparse
import io
import os
import threading

import torch
import uvicorn
from diffusers import StableDiffusionXLPipeline
from fastapi import FastAPI, File, Header, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

TOKEN = os.environ["INFERENCE_TOKEN"]
CHECKPOINT = os.environ["SDXL_CHECKPOINT"]
CURRENT_LORA_PATH = "/home/ubuntu/current_lora.safetensors"

app = FastAPI()

# Guards `_pipe` (lazy-loaded once) and every actual generation/LoRA-swap
# call — SDXL inference isn't safely reentrant across concurrent requests
# on one GPU, and this server is only ever meant to handle one prompt at a
# time anyway.
_lock = threading.Lock()
_pipe: StableDiffusionXLPipeline | None = None


def _require_token(authorization: str | None) -> None:
    if authorization != f"Bearer {TOKEN}":
        raise HTTPException(status_code=401, detail="unauthorized")


def _get_pipe() -> StableDiffusionXLPipeline:
    global _pipe
    if _pipe is None:
        pipe = StableDiffusionXLPipeline.from_single_file(CHECKPOINT, torch_dtype=torch.float16)
        pipe.to("cuda")
        _pipe = pipe
    return _pipe


@app.get("/health")
def health():
    return {"status": "ok", "model_loaded": _pipe is not None}


@app.post("/load-lora")
async def load_lora(file: UploadFile = File(...), authorization: str | None = Header(None)):
    _require_token(authorization)
    content = await file.read()
    with open(CURRENT_LORA_PATH, "wb") as f:
        f.write(content)
    with _lock:
        pipe = _get_pipe()
        pipe.unload_lora_weights()
        pipe.load_lora_weights(CURRENT_LORA_PATH)
    return {"loaded": True}


class GenerateRequest(BaseModel):
    prompt: str
    steps: int = 20


@app.post("/generate")
def generate(body: GenerateRequest, authorization: str | None = Header(None)):
    _require_token(authorization)
    with _lock:
        pipe = _get_pipe()
        image = pipe(prompt=body.prompt, num_inference_steps=body.steps).images[0]
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return Response(content=buffer.getvalue(), media_type="image/png")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8188)
    args = parser.parse_args()
    uvicorn.run(app, host="0.0.0.0", port=args.port)
