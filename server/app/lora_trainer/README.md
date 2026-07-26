# lora-trainer

Trains a LoRA against SDXL from a set of images, entirely by dispatching to a
throwaway EC2 GPU instance — see `aws/README.md` for the account setup and
the full data-lifecycle/privacy design (nothing persists in AWS once a job
completes).

## `LORA_TRAINER_AMI_ID` is a custom prebuilt image, rebuilt by hand

Every job used to boot from the stock Deep Learning AMI and spend 15-20 min
per job doing `git clone sd-scripts` + `pip install -r requirements.txt` +
`pip install torchvision bitsandbytes` + downloading the ~6.6GB SDXL
checkpoint, before training could even start. Since only one job trains at
a time, all of that setup is now baked into a custom AMI instead, and
`ec2.py`'s user-data script just does `source venv/bin/activate` and starts
training directly — see `build_user_data`.

**To rebuild the AMI** (new sd-scripts version, dependency bump, etc.),
repeat this by hand via the EC2 console — there's no automation for it,
since it's a rare, deliberate action:

1. Launch a throwaway instance from the *current* `LORA_TRAINER_AMI_ID`
   (or a fresh stock Deep Learning AMI — see below), same instance type
   (`g6.2xlarge`), same subnet/security group/IAM profile as training uses,
   no key pair needed. **Root volume: 100GiB** — the default 40GiB isn't
   enough once the pip-installed CUDA/torch stack (several GB unpacked) and
   the checkpoint are both on disk; a too-small volume fails with
   `curl: (23) Failure writing output to destination` partway through the
   checkpoint download.
2. Give it this **user data** (a trimmed version of `build_user_data`,
   minus anything job-specific — the standalone git clone/pip
   install/checkpoint download steps `ec2.py` no longer runs itself):
   ```sh
   #!/bin/bash
   set -x
   exec > >(tee -a /home/ubuntu/build.log) 2>&1
   WORK=/home/ubuntu/lora-job
   mkdir -p "$WORK"
   git clone --depth 1 https://github.com/kohya-ss/sd-scripts.git "$WORK/sd-scripts"
   cd "$WORK/sd-scripts"
   python3 -m venv venv
   source venv/bin/activate
   pip install --upgrade pip
   pip install -r requirements.txt
   pip install torchvision bitsandbytes
   curl -L "https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0/resolve/main/sd_xl_base_1.0.safetensors" \
     -o "$WORK/sd_xl_base_1.0.safetensors"
   pip cache purge
   sudo apt-get clean
   shutdown -h now
   ```
   Leave "shutdown behavior" at its default (**Stop**, not Terminate) — the
   plain `shutdown -h now` above just stops the instance so it's ready to
   image, rather than destroying it.
3. Wait for it to reach `stopped` on its own (~15-20 min, the exact time
   this whole process is meant to eliminate from every real job).
4. **Actions → Image and templates → Create image**, name it something
   like `lora-trainer-prebuilt-v2`. Wait for the AMI to show `available`.
5. Terminate the builder instance — the AMI is independent at this point.
6. Update `LORA_TRAINER_AMI_ID` in `server/.env` to the new AMI id.

**If starting from a fresh stock Deep Learning AMI** instead of rebuilding
on top of the current custom one (e.g. to pick up newer NVIDIA
driver/CUDA), find the current one via:

```sh
aws ssm get-parameters-by-path \
  --path /aws/service/deeplearning/ami/x86_64 \
  --region us-east-1 --recursive \
  --query "Parameters[].Name" --output text | tr '\t' '\n' | sort
```

Pick the newest `oss-nvidia-driver-gpu-pytorch-<version>-ubuntu-22.04` entry
(22.04, not 24.04 — the venv depends on the AMI's *system* Python: 22.04
ships 3.10, a well-proven version for `kohya-ss/sd-scripts` and its
dependencies; 24.04's 3.12 is a less proven combination for some of those
ML packages), then resolve it to an AMI id:

```sh
aws ssm get-parameter \
  --name "/aws/service/deeplearning/ami/x86_64/<name-from-above>/latest/ami-id" \
  --region us-east-1 --query "Parameter.Value" --output text
```

## Where dataset images live before training

The Images step has two ways to add a photo, and they're stored very
differently:

- **"Add from file storage"** picks an existing file-manager/MinIO photo —
  identified by its real (positive) file-manager id.
- **"Upload images"** never touches file-manager/MinIO at all. Fresh
  uploads are saved straight to local disk, under `LORA_TRAINER_UPLOADS_DIR`
  (`local_uploads.py`), and given a synthetic **negative** id — file-manager
  ids are always positive, so this one id space can be threaded through the
  whole wizard (duplicate groups, person crops, captions, removal) exactly
  like a real file-manager id, with no separate code path anywhere except
  the two functions that actually read bytes off an id
  (`router._dataset_image_content` / `_dataset_image_name`, which branch on
  sign) and the two that serve a preview URL for it (client's
  `api.datasetImageUrl`, mirroring `_dataset_image_content`'s dispatch).

Once `POST /lora-trainer/jobs` successfully uploads a dataset to S3 for
training, every local-upload id (negative) used in that job is deleted from
disk (`local_uploads.delete_upload`) — the local copy only ever exists as a
staging area between "you clicked Upload" and "the dataset reached S3".
Anything sourced from file-manager is left untouched, since that's the
user's actual organized photo library, not scratch space.

## How a job runs

1. `POST /lora-trainer/jobs` uploads the dataset (images + captions) to S3
   and launches a `g6.2xlarge` (24GB L4 GPU, 32GB system RAM) instance from
   the prebuilt `LORA_TRAINER_AMI_ID` image with a generated user-data
   script — see `ec2.py:build_user_data`. (`g6.xlarge`'s 16GB system RAM
   turned out to not be enough headroom for loading the SDXL UNet/VAE/text
   encoders alongside everything else — the instance was observed thrashing
   badly, with even trivial shell commands stalling for minutes, before it
   was bumped to `g6.2xlarge`.)
2. The instance syncs the dataset down and trains directly (sd-scripts,
   its venv, and the SDXL checkpoint already exist on the AMI — see "is a
   custom prebuilt image" above), uploads the resulting `.safetensors` (or
   an error log) back to S3, and self-terminates (`shutdown -h now`, which
   AWS turns into a real termination because the instance is launched with
   `InstanceInitiatedShutdownBehavior=terminate`).
3. `GET /lora-trainer/jobs` / `GET /lora-trainer/jobs/{id}` check S3 for a
   completion/failure marker every time they're called ("check on read").
   There's also `router.poll_running_jobs`, a background loop started from
   `main.py`'s lifespan that does the same check every 20s regardless of
   whether anyone has the wizard open, so a finished job still gets its EC2
   instance terminated and S3 prefix cleaned up even if nobody's watching.
   On success the model is downloaded into file-manager and the job's
   entire S3 prefix is deleted; on failure the log is captured as the job's
   error message. Either way, nothing is left in AWS afterward.

## Dataset guidance (large/varied datasets)

200–300 varied images (different pose/background/outfit) works well for
SDXL LoRA training — better coverage than the 15–50 image datasets most
guides assume, as long as they're genuinely in-focus/high-res. A few things
that matter more as dataset size grows:

- **Aspect-ratio bucketing is on** (`--enable_bucket`, `--bucket_reso_steps
  64`, `--bucket_no_upscale`, `--min/max_bucket_reso 256/2048`) — without
  this, every image gets center-cropped to a square, which would cut the
  legs/feet off any portrait-oriented full-body shot. This was a real bug
  in the first version of this script.
- **Steps scale with dataset size**: the client suggests `~15 steps per
  image` (clamped to 1000–6000) as a starting point, auto-updating as you
  add/remove images, until you manually override it — see
  `suggestSteps()` in `LoraTrainerApp.tsx`.
- **Include real close-ups** (face from a few angles, hands on their own),
  not just full-body shots — the model learns detail from images where
  that detail is large in frame.
- **Hands remain the hardest thing** for any SDXL-based model to render
  correctly, LoRA or not — more hand-containing training photos helps, but
  don't expect it to fully overcome the base model's own limitation there.

## Caveats — this has not been run against real GPU hardware yet

The `sdxl_train_network.py` invocation in `ec2.py` is a best-effort first
pass (learning rate, resolution, optimizer choice, etc.). Expect to debug
the actual training run once you try it for real — check
`s3://<bucket>/jobs/<id>/output/train.log` (uploaded even on failure) for
what went wrong. Also note first-run boot time is slow (~5-10+ min) since
the AMI install + checkpoint download happen fresh every job, per the
"Deep Learning AMI + boot-time install" tradeoff chosen over maintaining a
custom AMI.

## Duplicate detection

`duplicates.py` groups near-duplicate images via perceptual hashing
(`imagehash.phash`, Hamming distance ≤ 8) — catches burst-mode near-repeats,
not just byte-identical files. Runs as a background job
(`POST /duplicates` → `job_id`, `GET /duplicates/{job_id}` for the resulting
groups) since hashing 200–300 images can take a few seconds; same pattern as
image-upscaler's jobs, to avoid blocking the server. The client's
"Duplicates" step lets you remove images directly from each detected group.

The client wizard (`LoraTrainerApp.tsx`) is: Images → Duplicates → People →
Config → Captions → Train. Captions runs after Config specifically so it
can prefix each generated caption with the trigger word you just typed in
(`"<trigger_word>, <caption>"`), matching the standard LoRA captioning
convention.

## People

Every photo in the dataset is assumed to already contain the person being
trained — this step exists only to handle group shots, where a second face
in frame would otherwise pollute training. `people.py` uses a local
`insightface` (`buffalo_l`) model to detect faces and compute ArcFace
embeddings across the whole dataset, then greedily clusters those embeddings
into unique identities (same greedy-grouping approach as
`duplicates.find_duplicate_groups`, just with a running centroid). The
client shows one representative face crop per identity.

Clustering by embedding similarity isn't perfect — the same person shot
from a different angle or in profile can land in its own cluster instead of
merging with their frontal shots. Rather than chase a perfect similarity
threshold, the client lets you **select more than one identity tile**
(multi-select, then a "Next" button — not select-and-go) if more than one
of them is actually you. `people.best_match` then matches a face against
*any* of the selected reference embeddings, not just one.

For any photo with more than one detected face, the selected identity's
embedding(s) are matched against that photo's faces, and the photo is
cropped to a **person box** — a heuristic expansion of the matched face's
bounding box (wide enough for the shoulders, tall enough to keep a
full-body shot mostly intact) — so the other person isn't in the training
image at all. Single-face photos pass through untouched. There's no body
detector involved, so this is an approximation, not a precise person
segmentation.

**A photo can match more than once** — a collage-style photo containing the
trained person several times over (e.g. three separate shots composited
into one image) produces one crop per matched face
(`people.all_matches`, not just the single best match), and each crop
becomes its **own** training image rather than only one being kept. The
client's People step review lets you skip any individual crop; the actual
splitting into separate training entries happens client-side once crops
are confirmed (`LoraTrainerApp.tsx`'s `trainingEntries`), and each split
entry gets its own editable caption in the Captions step (though the
initial generated caption is the same for all of a photo's splits, since
it's captioned once per source photo, not per crop).

Both steps run as background jobs (`POST/GET /people/detect`,
`POST/GET /people/select`), same pattern as duplicate detection, since face
detection across 200–300 images is real CPU work — both jobs (and the
duplicate-check job) report incremental `progress` (0–1) as each image is
processed, which the client shows as a percentage ring, same visual
language as the upload step's progress ring.

**Model download is manual, not automatic**, same policy as every other
model in this app. If `server/models/buffalo_l/` doesn't have
`det_10g.onnx` and `w600k_r50.onnx`, the People step's background jobs fail
with a clear error and this command:

```sh
curl -L -o buffalo_l.zip \
  https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_l.zip
mkdir -p /Users/nithin/workspace/studio/nithin-studio/server/models/buffalo_l
unzip buffalo_l.zip -d /Users/nithin/workspace/studio/nithin-studio/server/models/buffalo_l
```

## Auto-captioning

`captioning.py` runs a local BLIP model (via `transformers`) on this
machine — images are never sent anywhere for captioning, only for the
training job itself (to your own S3 bucket).

The client's Captions step runs this as a background job across the whole
dataset (`POST /captions` → `job_id`, `GET /captions/{job_id}`, reporting
`progress` same as Duplicates/People) rather than one image at a time —
BLIP inference per image is real CPU work, so batching it avoids blocking
the server for 200–300 sequential requests. Each result gets the trigger
word prefixed client-side before being saved as that image's caption; you
can edit any caption by hand afterward in the same step.

**Model download is manual, not automatic.** This app never downloads
anything itself — if `Salesforce/blip-image-captioning-base` isn't already
present locally, both `/lora-trainer/caption` (single image) and
`/lora-trainer/captions` (batch job) fail with the exact command to fetch
it yourself:

```sh
HF_HOME=/Users/nithin/workspace/studio/nithin-studio/server/models/huggingface \
  hf download Salesforce/blip-image-captioning-base
```
