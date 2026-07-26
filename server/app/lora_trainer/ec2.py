import os

import boto3

AWS_REGION = os.environ["LORA_TRAINER_AWS_REGION"]
S3_BUCKET = os.environ["LORA_TRAINER_S3_BUCKET"]
AWS_ACCESS_KEY_ID = os.environ["LORA_TRAINER_AWS_ACCESS_KEY_ID"]
AWS_SECRET_ACCESS_KEY = os.environ["LORA_TRAINER_AWS_SECRET_ACCESS_KEY"]
INSTANCE_PROFILE_ARN = os.environ["LORA_TRAINER_INSTANCE_PROFILE_ARN"]
SECURITY_GROUP_ID = os.environ["LORA_TRAINER_SECURITY_GROUP_ID"]
SUBNET_ID = os.environ["LORA_TRAINER_SUBNET_ID"]
AMI_ID = os.environ["LORA_TRAINER_AMI_ID"]
INSTANCE_TYPE = os.environ.get("LORA_TRAINER_INSTANCE_TYPE", "g6.2xlarge")


def _client():
    return boto3.client(
        "ec2",
        region_name=AWS_REGION,
        aws_access_key_id=AWS_ACCESS_KEY_ID,
        aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
    )


def build_user_data(job_id: int, trigger_word: str, steps: int, rank: int, alpha: int) -> str:
    """Bash script the instance runs at boot: train, upload the result, self-terminate.

    Assumes a prebuilt AMI (`LORA_TRAINER_AMI_ID`) that already has sd-scripts
    checked out with its venv installed and the SDXL checkpoint downloaded —
    see `aws/README.md` (or ask for the AMI-baking walkthrough) for how that
    image is built. This script only does what has to happen per-job: sync
    the dataset down and run training. If the AMI is ever rebuilt at a
    different path than `/home/ubuntu/lora-job`, update WORK below to match.
    """
    prefix = f"jobs/{job_id}"
    return f"""#!/bin/bash
set -x
WORK=/home/ubuntu/lora-job
OUTPUT="$WORK/output"
DATASET="$WORK/dataset/1_{trigger_word}"
LOG="$WORK/train.log"
mkdir -p "$DATASET" "$OUTPUT"
exec > >(tee -a "$LOG") 2>&1

upload_and_shutdown() {{
  local exit_code=$?
  aws s3 cp "$LOG" "s3://{S3_BUCKET}/{prefix}/output/train.log" || true
  if [ $exit_code -ne 0 ]; then
    aws s3 cp "$LOG" "s3://{S3_BUCKET}/{prefix}/output/error.log" || true
    aws s3api put-object --bucket {S3_BUCKET} --key "{prefix}/output/FAILED" || true
  fi
  shutdown -h now
}}
trap upload_and_shutdown EXIT

echo syncing_dataset | aws s3 cp - "s3://{S3_BUCKET}/{prefix}/output/phase" || true
aws s3 sync "s3://{S3_BUCKET}/{prefix}/dataset/" "$DATASET/"

cd "$WORK/sd-scripts"
source venv/bin/activate

echo loading_model | aws s3 cp - "s3://{S3_BUCKET}/{prefix}/output/phase" || true

accelerate launch --num_cpu_threads_per_process 4 sdxl_train_network.py \\
  --pretrained_model_name_or_path "$WORK/sd_xl_base_1.0.safetensors" \\
  --train_data_dir "$WORK/dataset" \\
  --output_dir "$OUTPUT" \\
  --output_name model \\
  --save_model_as safetensors \\
  --network_module networks.lora \\
  --network_dim {rank} \\
  --network_alpha {alpha} \\
  --max_train_steps {steps} \\
  --learning_rate 1e-4 \\
  --train_batch_size 1 \\
  --resolution 1024,1024 \\
  --enable_bucket \\
  --min_bucket_reso 256 \\
  --max_bucket_reso 2048 \\
  --bucket_reso_steps 64 \\
  --bucket_no_upscale \\
  --caption_extension .txt \\
  --mixed_precision fp16 \\
  --cache_latents \\
  --gradient_checkpointing \\
  --optimizer_type AdamW8bit &
TRAIN_PID=$!

# Periodically parse kohya's tqdm progress line out of the log (looks like
# "steps:  12%|...| 120/1000 [...]") and report the current step to S3, so
# the app can show a percentage instead of just "running" for the whole
# training run. Also re-upload the log itself each time, so a still-running
# job's log can be inspected via `GET /jobs/{{id}}/log` instead of only
# after it finishes. Best-effort — a missed or malformed read just means
# the step count/log doesn't advance for one interval, never a hard failure.
(
  while kill -0 "$TRAIN_PID" 2>/dev/null; do
    sleep 15
    LAST=$(grep -oE '[0-9]+/{steps}' "$LOG" | tail -n1)
    if [ -n "$LAST" ]; then
      echo "${{LAST%%/*}}" | aws s3 cp - "s3://{S3_BUCKET}/{prefix}/output/progress" || true
    fi
    aws s3 cp "$LOG" "s3://{S3_BUCKET}/{prefix}/output/train.log" || true
  done
) &
MONITOR_PID=$!

wait "$TRAIN_PID"
TRAIN_EXIT=$?
kill "$MONITOR_PID" 2>/dev/null || true

if [ $TRAIN_EXIT -ne 0 ]; then
  exit $TRAIN_EXIT
fi

aws s3 cp "$OUTPUT/model.safetensors" "s3://{S3_BUCKET}/{prefix}/output/model.safetensors"
"""


def launch_training_instance(
    job_id: int, trigger_word: str, steps: int, rank: int, alpha: int
) -> str:
    user_data = build_user_data(job_id, trigger_word, steps, rank, alpha)
    response = _client().run_instances(
        ImageId=AMI_ID,
        InstanceType=INSTANCE_TYPE,
        MinCount=1,
        MaxCount=1,
        SubnetId=SUBNET_ID,
        SecurityGroupIds=[SECURITY_GROUP_ID],
        IamInstanceProfile={"Arn": INSTANCE_PROFILE_ARN},
        InstanceInitiatedShutdownBehavior="terminate",
        UserData=user_data,
        BlockDeviceMappings=[
            {
                "DeviceName": "/dev/sda1",
                "Ebs": {"VolumeSize": 100, "DeleteOnTermination": True},
            }
        ],
        TagSpecifications=[
            {
                "ResourceType": "instance",
                "Tags": [
                    {"Key": "App", "Value": "lora-trainer"},
                    {"Key": "JobId", "Value": str(job_id)},
                ],
            }
        ],
    )
    return response["Instances"][0]["InstanceId"]


def describe_instance_state(instance_id: str) -> str:
    """Returns the instance's current state name, or 'terminated' if not found."""
    response = _client().describe_instances(InstanceIds=[instance_id])
    reservations = response.get("Reservations", [])
    if not reservations:
        return "terminated"
    return reservations[0]["Instances"][0]["State"]["Name"]


def terminate_instance(instance_id: str) -> None:
    _client().terminate_instances(InstanceIds=[instance_id])
