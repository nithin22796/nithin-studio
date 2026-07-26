"""A single shared location for ML model weights, so multiple app modules
reuse the same files on disk instead of each keeping its own copy.

Nothing here ever downloads anything. If a model is missing, code should
raise `ModelMissingError` (see `require_model`) with clear instructions —
the user downloads it themselves and places it in `MODELS_DIR`.
"""

import os
from pathlib import Path

_DEFAULT_MODELS_DIR = "/Users/nithin/workspace/studio/nithin-studio/server/models"
MODELS_DIR = Path(os.environ.get("NITHIN_STUDIO_MODELS_DIR", _DEFAULT_MODELS_DIR))


class ModelMissingError(Exception):
    pass


def require_model(filename: str, download_url: str | None = None) -> str:
    """Return the local path to `filename` in the shared models directory.

    Never downloads anything — raises `ModelMissingError` with the file's
    expected location (and source URL, if given) if it isn't there yet.
    """
    path = MODELS_DIR / filename
    if not path.is_file():
        source = f" Download it from:\n  {download_url}\n" if download_url else ""
        raise ModelMissingError(f"missing model file: {path}\n{source}Save it to: {MODELS_DIR}/")
    return str(path)
