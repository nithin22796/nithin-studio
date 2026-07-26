# image-upscaler

Sharpens and upscales a photo locally: GFPGAN for face restoration,
Real-ESRGAN as its background upsampler. Runs entirely on this machine,
CPU-only — the image never leaves it.

Output is always `scale`x the **source's own resolution** (2x or 4x, chosen
per request) — there is deliberately no fixed "HD"/"4K" target. An earlier
version tried a fixed absolute target with letterboxing, but that actively
*shrank* photos: a portrait photo fit into a landscape "4K" (3840x2160)
canvas gets constrained by the shorter dimension, and separately, modern
phone cameras already shoot above 4K-equivalent pixel counts (a typical
12MP photo is ~12.2MP vs 4K's ~8.3MP) — so a fixed target was working
against the actual goal (enhance detail, never lose it) for most real
photos. Scale-relative-to-source guarantees the output is never smaller
than what you started with, regardless of source size or orientation.

Upscaling runs as a background job (`POST /upscale` returns a `job_id`
immediately, `GET /jobs/{job_id}` for status/progress) rather than blocking
the request — CPU inference here can take a while, and this is genuinely
heavy synchronous work that would otherwise tie up the whole server for
its duration. Requests are serialized (one upscale at a time) since the
model pipeline is a single shared, non-thread-safe instance.

## Diagnostics

Face restoration is silent by nature — if no face is detected, or GFPGAN's
own inference fails on a detected face, the original code just falls back
quietly (so a "restored" image might have had zero actual restoration
applied, indistinguishable at a glance from a real one). Both cases are now
caught (via shadowing GFPGAN/Real-ESRGAN's internal `print` calls) and
surfaced as `diagnostics` on the job, plus logged to the Activity feed —
check there first if a result looks like it wasn't actually enhanced.

## Model download is manual, not automatic

This app never downloads anything itself. If any model file is missing, the
job fails with the exact path and source URL in its `error`. **Four** files
are needed — it's easy to miss the last two, since GFPGAN's own convenience
API pulls them in via a separate library (`facexlib`) with no obvious
mention in its main docs:

- `server/models/RealESRGAN_x4plus.pth` — https://github.com/xinntao/Real-ESRGAN/releases/download/v0.1.0/RealESRGAN_x4plus.pth
- `server/models/GFPGANv1.4.pth` — https://github.com/TencentARC/GFPGAN/releases/download/v1.3.4/GFPGANv1.4.pth
- `server/models/detection_Resnet50_Final.pth` (face detection, used internally by GFPGAN) — https://github.com/xinntao/facexlib/releases/download/v0.1.0/detection_Resnet50_Final.pth
- `server/models/parsing_parsenet.pth` (face parsing, used internally by GFPGAN) — https://github.com/xinntao/facexlib/releases/download/v0.2.2/parsing_parsenet.pth

(`server/models/` is the shared location for all apps' model weights — see
`app/shared_models.py`.) `upscaler.py`'s `_pipeline()` builds the GFPGAN
object by hand rather than using its normal constructor specifically to
redirect these two auxiliary files here instead of GFPGAN's own hardcoded
`gfpgan/weights/` default — worth knowing if a future GFPGAN/facexlib
upgrade changes that internal structure and this needs revisiting.
