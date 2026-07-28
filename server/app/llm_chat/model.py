import os
from functools import lru_cache

from app.shared_models import ModelMissingError

# Not under shared_models.MODELS_DIR — this is a full standalone HF-format
# checkpoint (~10GB) cloned straight from its own git repo, not a single
# weights file shared across app modules like buffalo_l/BLIP are.
_DEFAULT_MODEL_PATH = "/Users/nithin/workspace/studio/Llama-3.2-3B-Instruct-uncensored"
MODEL_PATH = os.environ.get("LLM_CHAT_MODEL_PATH", _DEFAULT_MODEL_PATH)

Message = dict[str, str]  # {"role": "user" | "assistant", "content": "..."}


@lru_cache(maxsize=1)
def _pipeline():
    # Imported lazily so importing this module doesn't force torch/transformers
    # to load until the chat endpoint is actually hit.
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    device = "mps" if torch.backends.mps.is_available() else "cpu"
    try:
        tokenizer = AutoTokenizer.from_pretrained(MODEL_PATH, local_files_only=True)
        model = AutoModelForCausalLM.from_pretrained(
            MODEL_PATH, local_files_only=True, dtype=torch.bfloat16
        ).to(device)
    except OSError as exc:
        raise ModelMissingError(
            f"missing/incomplete model at {MODEL_PATH}\n"
            "If this is a fresh git clone, the .safetensors files are likely "
            "still git-lfs pointer stubs — run inside that folder:\n"
            "  git lfs install && git lfs pull"
        ) from exc
    return tokenizer, model, device


def generate_reply(history: list[Message], max_new_tokens: int = 512) -> str:
    """Runs one turn of chat completion. `history` is the full conversation
    so far (oldest first), ending with the latest user message — the caller
    (client) is responsible for keeping/sending history; nothing is
    persisted server-side.
    """
    tokenizer, model, device = _pipeline()
    # `return_dict=True` is explicit here because this installed transformers
    # version returns a BatchEncoding (not a bare tensor) from
    # apply_chat_template regardless of return_tensors — passing that dict
    # straight as `input_ids` to generate() breaks inside its internals
    # (BatchEncoding has no `.shape`). Unpacking input_ids/attention_mask by
    # name and passing them as kwargs avoids relying on which shape the
    # installed version happens to return.
    inputs = tokenizer.apply_chat_template(
        history, add_generation_prompt=True, return_tensors="pt", return_dict=True
    ).to(device)
    output = model.generate(
        **inputs,
        max_new_tokens=max_new_tokens,
        do_sample=True,
        temperature=0.7,
        top_p=0.9,
        pad_token_id=tokenizer.eos_token_id,
    )
    new_tokens = output[0][inputs["input_ids"].shape[-1] :]
    return tokenizer.decode(new_tokens, skip_special_tokens=True).strip()
