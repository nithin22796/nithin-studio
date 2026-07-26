import os
from pathlib import Path

# Where you manually drop trained LoRA `.safetensors` files — no upload flow,
# no file-manager/MinIO involvement, just files placed on disk directly (same
# "no auto-download, you manage the files yourself" spirit as every other
# model directory in this app).
MODELS_DIR = Path(
    os.environ.get(
        "IMAGE_GENERATOR_MODELS_DIR",
        "/Users/nithin/workspace/studio/nithin-studio/server/data/my-models",
    )
)


class NotFoundError(Exception):
    pass


def list_models() -> list[str]:
    if not MODELS_DIR.is_dir():
        return []
    return sorted(p.name for p in MODELS_DIR.iterdir() if p.suffix == ".safetensors")


def get_model_path(name: str) -> Path:
    """Resolves a model filename to its path, guarding against path
    traversal (`name` ultimately ends up in an HTTP request body) — only a
    bare filename actually inside `MODELS_DIR` is ever accepted."""
    candidate = MODELS_DIR / Path(name).name
    if candidate.suffix != ".safetensors" or not candidate.is_file():
        raise NotFoundError(f"model not found: {name}")
    return candidate
