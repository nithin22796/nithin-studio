import io
import os
from functools import lru_cache

from PIL import Image

from app.shared_models import MODELS_DIR, ModelMissingError

MODEL_NAME = "Salesforce/blip-image-captioning-base"


@lru_cache(maxsize=1)
def _pipeline():
    # Imported lazily so importing this module doesn't force torch/transformers
    # to load until captioning is actually used.
    # HF_HOME must be set before transformers is imported for the first time.
    hf_home = MODELS_DIR / "huggingface"
    os.environ.setdefault("HF_HOME", str(hf_home))
    # `local_files_only=True` below only affects that one call — huggingface_hub
    # can still attempt a network metadata/rate-limit check elsewhere during
    # import or model loading (that's the "unauthenticated requests to the HF
    # Hub" warning you may have seen). On a slow/unreachable network that call
    # can hang for minutes with zero visible progress. These two force fully
    # offline mode so no network call can happen at all, not just "prefer
    # local files."
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    import torch
    from transformers import BlipForConditionalGeneration, BlipProcessor

    device = "mps" if torch.backends.mps.is_available() else "cpu"
    try:
        processor = BlipProcessor.from_pretrained(MODEL_NAME, local_files_only=True)
        model = BlipForConditionalGeneration.from_pretrained(
            MODEL_NAME, local_files_only=True
        ).to(device)
    except OSError as exc:
        raise ModelMissingError(
            f"missing model '{MODEL_NAME}' in {hf_home}\n"
            f"Download it yourself, e.g.:\n"
            f"  HF_HOME={hf_home} hf download {MODEL_NAME}"
        ) from exc
    return processor, model, device


def caption_image(content: bytes) -> str:
    """Generate a caption locally — the image never leaves this machine."""
    processor, model, device = _pipeline()
    image = Image.open(io.BytesIO(content)).convert("RGB")
    inputs = processor(image, return_tensors="pt").to(device)
    # BLIP's plain greedy decoding tends to get "stuck" re-describing the same
    # salient feature on visually busy content (e.g. patterned fabric),
    # producing captions like "a blue sari sari" — these two params suppress
    # that repetition.
    output = model.generate(
        **inputs, max_new_tokens=40, no_repeat_ngram_size=3, repetition_penalty=1.3
    )
    return processor.decode(output[0], skip_special_tokens=True)
