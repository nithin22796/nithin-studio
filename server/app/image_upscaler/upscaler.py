import io
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass, field
from functools import lru_cache

import cv2
import numpy as np
from PIL import Image

from app.image_upscaler import _torchvision_compat  # noqa: F401  (must run before basicsr imports)
from app.shared_models import MODELS_DIR, require_model

REALESRGAN_MODEL_URL = (
    "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.1.0/RealESRGAN_x4plus.pth"
)
GFPGAN_MODEL_URL = (
    "https://github.com/TencentARC/GFPGAN/releases/download/v1.3.4/GFPGANv1.4.pth"
)
# GFPGAN's FaceRestoreHelper needs these two auxiliary models (face detection +
# face parsing) on top of the two above — easy to miss since GFPGANer's
# convenience constructor pulls them in itself, hardcoded to its own download
# path. See _pipeline() for why we bypass that constructor.
DETECTION_MODEL_URL = (
    "https://github.com/xinntao/facexlib/releases/download/v0.1.0/detection_Resnet50_Final.pth"
)
PARSING_MODEL_URL = (
    "https://github.com/xinntao/facexlib/releases/download/v0.2.2/parsing_parsenet.pth"
)

# Output is always at least `scale`x the source's own resolution — never a
# fixed absolute target. A fixed "HD"/"4K" target was tried first, but modern
# phone cameras already shoot above 4K-equivalent pixel counts, so a fixed
# target was actively *shrinking* photos instead of enhancing them.
SCALE_FACTORS = (2, 4)


class JobCancelled(Exception):
    pass


@dataclass
class UpscaleResult:
    image: bytes
    faces_detected: int
    gfpgan_failures: list[str] = field(default_factory=list)


@lru_cache(maxsize=1)
def _pipeline():
    import torch
    from basicsr.archs.rrdbnet_arch import RRDBNet
    from facexlib.utils.face_restoration_helper import FaceRestoreHelper
    from gfpgan.archs.gfpganv1_clean_arch import GFPGANv1Clean
    from gfpgan.utils import GFPGANer
    from realesrgan import RealESRGANer

    bg_model_path = require_model("RealESRGAN_x4plus.pth", REALESRGAN_MODEL_URL)
    gfpgan_model_path = require_model("GFPGANv1.4.pth", GFPGAN_MODEL_URL)
    # Required so FaceRestoreHelper (below) finds them already present and
    # never attempts its own download.
    require_model("detection_Resnet50_Final.pth", DETECTION_MODEL_URL)
    require_model("parsing_parsenet.pth", PARSING_MODEL_URL)

    bg_model = RRDBNet(
        num_in_ch=3, num_out_ch=3, num_feat=64, num_block=23, num_grow_ch=32, scale=4
    )
    bg_upsampler = RealESRGANer(
        scale=4,
        model_path=bg_model_path,
        model=bg_model,
        tile=400,
        tile_pad=10,
        pre_pad=0,
        half=False,
        device="cpu",
    )

    # Not using GFPGANer(...) directly: its __init__ unconditionally builds a
    # FaceRestoreHelper with model_rootpath='gfpgan/weights' hardcoded, which
    # downloads the two auxiliary models before we'd ever get a chance to
    # redirect it. Build the object by hand instead, pointed at our shared,
    # pre-checked MODELS_DIR throughout.
    restorer = GFPGANer.__new__(GFPGANer)
    restorer.upscale = 4
    restorer.bg_upsampler = bg_upsampler
    restorer.device = "cpu"
    restorer.gfpgan = GFPGANv1Clean(
        out_size=512,
        num_style_feat=512,
        channel_multiplier=2,
        decoder_load_path=None,
        fix_decoder=False,
        num_mlp=8,
        input_is_latent=True,
        different_w=True,
        narrow=1,
        sft_half=True,
    )
    restorer.face_helper = FaceRestoreHelper(
        4,
        face_size=512,
        crop_ratio=(1, 1),
        det_model="retinaface_resnet50",
        save_ext="png",
        use_parse=True,
        device="cpu",
        model_rootpath=str(MODELS_DIR),
    )
    loadnet = torch.load(gfpgan_model_path, map_location=lambda storage, loc: storage)
    keyname = "params_ema" if "params_ema" in loadnet else "params"
    restorer.gfpgan.load_state_dict(loadnet[keyname], strict=True)
    restorer.gfpgan.eval()
    return restorer


@contextmanager
def _progress_hook(
    on_progress: Callable[[int, int], None] | None,
    should_cancel: Callable[[], bool] | None,
    gfpgan_failures: list[str],
):
    """RealESRGANer's tiled upsampling reports progress via bare `print(...)`
    calls ("Tile 3/88") — intercept them by shadowing `print` in that module's
    namespace for the duration of the call (Python resolves an unqualified
    name in the module's globals before builtins, so this is a safe, fully
    reversible way to observe progress without reimplementing the tiling
    loop). Since this hook runs synchronously between each tile, it's also
    the one place we can actually interrupt the loop — raising here aborts
    `tile_process`'s tile-by-tile loop immediately.

    Only covers the background-upsampling phase; a cancel requested during
    the initial face-detection/restoration phase won't take effect until the
    tile loop starts (there's no equivalent checkpoint in that phase).

    Also shadows `gfpgan.utils.print` the same way, to catch
    "Failed inference for GFPGAN: ..." — normally a silent print with a
    quiet fallback to the unrestored face crop. Collected into
    `gfpgan_failures` so callers can surface it instead of it disappearing.
    """
    import gfpgan.utils as gfpgan_utils
    import realesrgan.utils as realesrgan_utils

    original_realesrgan_print = getattr(realesrgan_utils, "print", print)
    original_gfpgan_print = getattr(gfpgan_utils, "print", print)

    def realesrgan_hook(*args, **kwargs):
        text = " ".join(str(a) for a in args)
        stripped = text.strip()
        if stripped.startswith("Tile"):
            try:
                current_str, total_str = stripped.split()[1].split("/")
                if on_progress:
                    on_progress(int(current_str), int(total_str))
            except (IndexError, ValueError):
                pass
        original_realesrgan_print(*args, **kwargs)
        if should_cancel and should_cancel():
            raise JobCancelled("cancelled")

    def gfpgan_hook(*args, **kwargs):
        text = " ".join(str(a) for a in args)
        if "Failed inference for GFPGAN" in text:
            gfpgan_failures.append(text.strip())
        original_gfpgan_print(*args, **kwargs)

    realesrgan_utils.print = realesrgan_hook
    gfpgan_utils.print = gfpgan_hook
    try:
        yield
    finally:
        realesrgan_utils.print = original_realesrgan_print
        gfpgan_utils.print = original_gfpgan_print


def upscale_image(
    content: bytes,
    scale: int,
    on_progress: Callable[[int, int], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> UpscaleResult:
    """Restore faces and upscale to `scale`x the source's own resolution —
    output is never smaller than the source, regardless of how large it
    already is. Runs entirely locally on CPU — the image never leaves this
    machine. `on_progress(current_tile, total_tiles)` is called as the
    background upsampler works through its tiles, if given. If
    `should_cancel()` returns True between tiles, raises `JobCancelled`.
    """
    if scale not in SCALE_FACTORS:
        raise ValueError(f"unsupported scale factor: {scale}")

    image = Image.open(io.BytesIO(content)).convert("RGB")
    bgr = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)

    restorer = _pipeline()
    restorer.upscale = scale
    restorer.face_helper.set_upscale_factor(scale)

    gfpgan_failures: list[str] = []
    with _progress_hook(on_progress, should_cancel, gfpgan_failures):
        cropped_faces, _, restored_bgr = restorer.enhance(
            bgr, has_aligned=False, only_center_face=False, paste_back=True
        )

    ok, buffer = cv2.imencode(".png", restored_bgr)
    if not ok:
        raise RuntimeError("failed to encode output image")
    return UpscaleResult(
        image=buffer.tobytes(),
        faces_detected=len(cropped_faces),
        gfpgan_failures=gfpgan_failures,
    )
