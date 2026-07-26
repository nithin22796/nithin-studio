import os
import time
from pathlib import Path

import boto3

AWS_REGION = os.environ["IMAGE_GENERATOR_AWS_REGION"]
AWS_ACCESS_KEY_ID = os.environ["IMAGE_GENERATOR_AWS_ACCESS_KEY_ID"]
AWS_SECRET_ACCESS_KEY = os.environ["IMAGE_GENERATOR_AWS_SECRET_ACCESS_KEY"]
SECURITY_GROUP_ID = os.environ["IMAGE_GENERATOR_SECURITY_GROUP_ID"]
SUBNET_ID = os.environ["IMAGE_GENERATOR_SUBNET_ID"]
# Deliberately the same AMI lora-trainer's training uses, not a dedicated
# IMAGE_GENERATOR_AMI_ID — it's explicitly the same image (already has
# sd-scripts' venv-adjacent installs, torch, diffusers, and the SDXL
# checkpoint on disk), so there's nothing app-specific to bake in separately.
AMI_ID = os.environ["LORA_TRAINER_AMI_ID"]
INSTANCE_TYPE = os.environ.get("IMAGE_GENERATOR_INSTANCE_TYPE", "g6.xlarge")
INFERENCE_PORT = int(os.environ.get("IMAGE_GENERATOR_INFERENCE_PORT", "8188"))
INFERENCE_TOKEN = os.environ["IMAGE_GENERATOR_INFERENCE_TOKEN"]
# Optional (unlike everything else here) — added after the fact so a running
# install doesn't break before `aws/setup.sh` has been re-run to create it.
# Without it, the instance boots with no way to remotely inspect its log if
# `instance_server.py` ever crashes or hangs (no S3, no SSH by design) — see
# `fetch_instance_log`.
INSTANCE_PROFILE_ARN = os.environ.get("IMAGE_GENERATOR_INSTANCE_PROFILE_ARN")

_INSTANCE_SERVER_SOURCE = (Path(__file__).parent / "instance_server.py").read_text()


def _client():
    return boto3.client(
        "ec2",
        region_name=AWS_REGION,
        aws_access_key_id=AWS_ACCESS_KEY_ID,
        aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
    )


def _ssm_client():
    return boto3.client(
        "ssm",
        region_name=AWS_REGION,
        aws_access_key_id=AWS_ACCESS_KEY_ID,
        aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
    )


def build_user_data() -> str:
    """Bash script the instance runs at boot: start a persistent inference
    server and then just... keep running. Unlike training's script, this
    never self-terminates on completion — there's no natural "done" moment,
    the app calls `terminate_instance` explicitly when the user clicks Stop.

    Note: the AMI's `sd-scripts/venv` doesn't actually work (`source
    venv/bin/activate` fails — see the lora-trainer README/session notes;
    packages ended up installed at the system Python level during the AMI
    build instead), so this deliberately doesn't try to activate it either —
    it just pip-installs the couple of extra packages needed (fastapi/
    uvicorn/python-multipart aren't part of sd-scripts' own requirements)
    straight into the system Python, same as everything already on the AMI.
    """
    return f"""#!/bin/bash
set -x
exec > >(tee -a /home/ubuntu/instance_server_boot.log) 2>&1

pip install fastapi uvicorn python-multipart

cat > /home/ubuntu/instance_server.py << 'INSTANCE_SERVER_EOF'
{_INSTANCE_SERVER_SOURCE}
INSTANCE_SERVER_EOF

export INFERENCE_TOKEN="{INFERENCE_TOKEN}"
export SDXL_CHECKPOINT="/home/ubuntu/lora-job/sd_xl_base_1.0.safetensors"
nohup python3 /home/ubuntu/instance_server.py --port {INFERENCE_PORT} \\
  > /home/ubuntu/instance_server.log 2>&1 &
"""


def launch_inference_instance(session_id: int) -> str:
    user_data = build_user_data()
    kwargs = {}
    if INSTANCE_PROFILE_ARN:
        kwargs["IamInstanceProfile"] = {"Arn": INSTANCE_PROFILE_ARN}
    response = _client().run_instances(
        ImageId=AMI_ID,
        InstanceType=INSTANCE_TYPE,
        MinCount=1,
        MaxCount=1,
        SubnetId=SUBNET_ID,
        SecurityGroupIds=[SECURITY_GROUP_ID],
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
                    {"Key": "App", "Value": "image-generator"},
                    {"Key": "SessionId", "Value": str(session_id)},
                ],
            }
        ],
        **kwargs,
    )
    return response["Instances"][0]["InstanceId"]


def describe_instance_state(instance_id: str) -> str:
    """Returns the instance's current state name, or 'terminated' if not found."""
    response = _client().describe_instances(InstanceIds=[instance_id])
    reservations = response.get("Reservations", [])
    if not reservations:
        return "terminated"
    return reservations[0]["Instances"][0]["State"]["Name"]


def instance_public_ip(instance_id: str) -> str | None:
    response = _client().describe_instances(InstanceIds=[instance_id])
    reservations = response.get("Reservations", [])
    if not reservations:
        return None
    return reservations[0]["Instances"][0].get("PublicIpAddress")


def terminate_instance(instance_id: str) -> None:
    _client().terminate_instances(InstanceIds=[instance_id])


def fetch_instance_log(instance_id: str, timeout_seconds: float = 30.0) -> str:
    """Pulls `instance_server.py`'s own log (and its boot-script log) off the
    instance via SSM Run Command — the only way to see what actually
    happened if the inference server crashed or hung, since this app has no
    S3 and no SSH. Requires `IMAGE_GENERATOR_INSTANCE_PROFILE_ARN` to be set
    and the instance to have finished registering with SSM (~30-60s after
    boot) — raises a plain exception with the reason if either isn't ready
    yet, since there's nothing more specific to catch it as."""
    if not INSTANCE_PROFILE_ARN:
        raise RuntimeError(
            "IMAGE_GENERATOR_INSTANCE_PROFILE_ARN isn't set — this instance has no SSM access"
        )
    ssm = _ssm_client()
    command = ssm.send_command(
        InstanceIds=[instance_id],
        DocumentName="AWS-RunShellScript",
        Parameters={
            "commands": [
                "echo '--- instance_server_boot.log ---'",
                "cat /home/ubuntu/instance_server_boot.log 2>&1 || true",
                "echo '--- instance_server.log ---'",
                "cat /home/ubuntu/instance_server.log 2>&1 || true",
            ]
        },
    )
    command_id = command["Command"]["CommandId"]

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            invocation = ssm.get_command_invocation(CommandId=command_id, InstanceId=instance_id)
        except ssm.exceptions.InvocationDoesNotExist:
            time.sleep(1)
            continue
        if invocation["Status"] in ("Pending", "InProgress", "Delayed"):
            time.sleep(1)
            continue
        return invocation.get("StandardOutputContent") or invocation.get(
            "StandardErrorContent", ""
        )
    raise RuntimeError("timed out waiting for the SSM command to complete")
