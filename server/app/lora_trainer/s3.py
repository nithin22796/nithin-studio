import os

import boto3

AWS_REGION = os.environ["LORA_TRAINER_AWS_REGION"]
S3_BUCKET = os.environ["LORA_TRAINER_S3_BUCKET"]
AWS_ACCESS_KEY_ID = os.environ["LORA_TRAINER_AWS_ACCESS_KEY_ID"]
AWS_SECRET_ACCESS_KEY = os.environ["LORA_TRAINER_AWS_SECRET_ACCESS_KEY"]


def _client():
    return boto3.client(
        "s3",
        region_name=AWS_REGION,
        aws_access_key_id=AWS_ACCESS_KEY_ID,
        aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
    )


def _job_prefix(job_id: int) -> str:
    return f"jobs/{job_id}/"


def upload_dataset_image(job_id: int, filename: str, content: bytes, caption: str) -> None:
    client = _client()
    key_stem = f"{_job_prefix(job_id)}dataset/{filename}"
    client.put_object(Bucket=S3_BUCKET, Key=key_stem, Body=content)
    caption_key = f"{key_stem.rsplit('.', 1)[0]}.txt"
    client.put_object(Bucket=S3_BUCKET, Key=caption_key, Body=caption.encode("utf-8"))


def output_marker(job_id: int) -> str | None:
    """Return 'done', 'failed', or None if the job hasn't finished yet."""
    client = _client()
    prefix = f"{_job_prefix(job_id)}output/"
    response = client.list_objects_v2(Bucket=S3_BUCKET, Prefix=prefix)
    keys = {obj["Key"] for obj in response.get("Contents", [])}
    if f"{prefix}FAILED" in keys:
        return "failed"
    if f"{prefix}model.safetensors" in keys:
        return "done"
    return None


def read_progress(job_id: int) -> int | None:
    """Returns the current training step, or None if the instance hasn't
    reported one yet — see `ec2.py:build_user_data` for the side that writes
    this file every ~15s while `sdxl_train_network.py` is running."""
    client = _client()
    key = f"{_job_prefix(job_id)}output/progress"
    try:
        body = client.get_object(Bucket=S3_BUCKET, Key=key)["Body"].read().decode("utf-8").strip()
        return int(body)
    except (client.exceptions.NoSuchKey, ValueError):
        return None


def read_phase(job_id: int) -> str | None:
    """Returns the current coarse phase ('syncing_dataset', 'loading_model'),
    or None if nothing's been reported yet — see `ec2.py:build_user_data`.
    Superseded by `read_progress` once real per-step progress exists; this
    just covers the otherwise-silent gap before the first training step."""
    client = _client()
    key = f"{_job_prefix(job_id)}output/phase"
    try:
        return client.get_object(Bucket=S3_BUCKET, Key=key)["Body"].read().decode("utf-8").strip()
    except client.exceptions.NoSuchKey:
        return None


def read_current_log(job_id: int) -> str | None:
    """Returns whatever's been uploaded to train.log so far — re-uploaded
    every ~15s while training runs (see `ec2.py`), not just once at the end,
    so a running job's log can actually be inspected instead of only after
    it finishes. None if nothing's been uploaded yet."""
    client = _client()
    key = f"{_job_prefix(job_id)}output/train.log"
    try:
        return client.get_object(Bucket=S3_BUCKET, Key=key)["Body"].read().decode(
            "utf-8", errors="replace"
        )
    except client.exceptions.NoSuchKey:
        return None


def read_error_log(job_id: int) -> str:
    client = _client()
    key = f"{_job_prefix(job_id)}output/error.log"
    try:
        return client.get_object(Bucket=S3_BUCKET, Key=key)["Body"].read().decode(
            "utf-8", errors="replace"
        )
    except client.exceptions.NoSuchKey:
        return "training failed (no log available)"


def download_model(job_id: int) -> bytes:
    client = _client()
    key = f"{_job_prefix(job_id)}output/model.safetensors"
    return client.get_object(Bucket=S3_BUCKET, Key=key)["Body"].read()


def delete_job_prefix(job_id: int) -> None:
    client = _client()
    prefix = _job_prefix(job_id)
    paginator = client.get_paginator("list_objects_v2")
    keys = [
        {"Key": obj["Key"]}
        for page in paginator.paginate(Bucket=S3_BUCKET, Prefix=prefix)
        for obj in page.get("Contents", [])
    ]
    for i in range(0, len(keys), 1000):
        client.delete_objects(Bucket=S3_BUCKET, Delete={"Objects": keys[i : i + 1000]})
