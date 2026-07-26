#!/usr/bin/env bash
# One-time AWS setup for image-generator. Review before running — this
# creates real resources in your AWS account, including a security group
# with ONE inbound rule (unlike lora-trainer's zero-inbound design — see
# README.md for why this app needs it). Requires the AWS CLI configured
# with credentials that can create IAM users/policies and security groups.
#
# Safe to re-run: each step checks whether its resource already exists,
# except the inbound rule authorization (re-running that after your IP
# changes is exactly the point — see README.md).

set -euo pipefail

REGION="${REGION:-us-east-1}"
INFERENCE_PORT="${INFERENCE_PORT:-8188}"
ORCHESTRATOR_USER="image-generator-orchestrator"
ORCHESTRATOR_POLICY_NAME="image-generator-orchestrator-policy"
SG_NAME="image-generator-inference"
EC2_ROLE_NAME="image-generator-ec2-role"
EC2_PROFILE_NAME="image-generator-ec2-profile"

ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

MY_IP="$(curl -s https://checkip.amazonaws.com)"

substitute() {
  sed -e "s/REGION/$REGION/g" \
      -e "s/ACCOUNT_ID/$ACCOUNT_ID/g" \
      "$1"
}

echo "== Account: $ACCOUNT_ID  Region: $REGION  Your IP: $MY_IP =="

# --- Orchestrator IAM user (used by the nithin-studio server's AWS creds) ---
if aws iam get-user --user-name "$ORCHESTRATOR_USER" >/dev/null 2>&1; then
  echo "[iam] orchestrator user already exists"
else
  aws iam create-user --user-name "$ORCHESTRATOR_USER"
  echo "[iam] orchestrator user created"
fi

substitute "$SCRIPT_DIR/orchestrator-policy.json" > "$TMP_DIR/orchestrator-policy.json"
aws iam put-user-policy --user-name "$ORCHESTRATOR_USER" \
  --policy-name "$ORCHESTRATOR_POLICY_NAME" \
  --policy-document "file://$TMP_DIR/orchestrator-policy.json"

echo "[iam] orchestrator policy attached"
echo "[iam] creating a new access key for $ORCHESTRATOR_USER — save this now, it is shown only once:"
aws iam create-access-key --user-name "$ORCHESTRATOR_USER" --output table

# --- Security group: ONE inbound rule (the inference port, from your IP only) ---
VPC_ID="$(aws ec2 describe-vpcs --filters Name=isDefault,Values=true --query 'Vpcs[0].VpcId' --output text --region "$REGION")"
SUBNET_ID="$(aws ec2 describe-subnets --filters Name=vpc-id,Values="$VPC_ID" --query 'Subnets[0].SubnetId' --output text --region "$REGION")"

SG_ID="$(aws ec2 describe-security-groups --filters Name=group-name,Values="$SG_NAME" Name=vpc-id,Values="$VPC_ID" \
  --query 'SecurityGroups[0].GroupId' --output text --region "$REGION" 2>/dev/null || echo "None")"

if [ "$SG_ID" = "None" ] || [ -z "$SG_ID" ]; then
  SG_ID="$(aws ec2 create-security-group --group-name "$SG_NAME" \
    --description "image-generator inference instances: one inbound port, IP-restricted" \
    --vpc-id "$VPC_ID" --region "$REGION" --query 'GroupId' --output text)"
  echo "[ec2] security group created: $SG_ID"
else
  echo "[ec2] security group already exists: $SG_ID"
fi

echo "[ec2] authorizing inbound TCP $INFERENCE_PORT from $MY_IP/32 (re-run this script if your IP changes)"
aws ec2 authorize-security-group-ingress --group-id "$SG_ID" --region "$REGION" \
  --protocol tcp --port "$INFERENCE_PORT" --cidr "$MY_IP/32" 2>/dev/null \
  || echo "[ec2] rule for $MY_IP/32 already present"

# --- EC2 instance role: SSM access only (for remote log-pulling when
# `instance_server.py` crashes/hangs — there's no S3 or SSH for this app to
# debug through otherwise). This is the only AWS-facing permission the
# instance itself gets; it still never touches S3 or any other API. ---
if aws iam get-role --role-name "$EC2_ROLE_NAME" >/dev/null 2>&1; then
  echo "[iam] ec2 role already exists"
else
  aws iam create-role --role-name "$EC2_ROLE_NAME" \
    --assume-role-policy-document '{
      "Version": "2012-10-17",
      "Statement": [{
        "Effect": "Allow",
        "Principal": {"Service": "ec2.amazonaws.com"},
        "Action": "sts:AssumeRole"
      }]
    }' >/dev/null
  echo "[iam] ec2 role created"
fi

aws iam attach-role-policy --role-name "$EC2_ROLE_NAME" \
  --policy-arn "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
echo "[iam] AmazonSSMManagedInstanceCore attached to $EC2_ROLE_NAME"

if aws iam get-instance-profile --instance-profile-name "$EC2_PROFILE_NAME" >/dev/null 2>&1; then
  echo "[iam] instance profile already exists"
else
  aws iam create-instance-profile --instance-profile-name "$EC2_PROFILE_NAME" >/dev/null
  aws iam add-role-to-instance-profile --instance-profile-name "$EC2_PROFILE_NAME" \
    --role-name "$EC2_ROLE_NAME"
  echo "[iam] instance profile created and role attached"
  echo "[iam] waiting a few seconds for the instance profile to propagate..."
  sleep 10
fi

EC2_PROFILE_ARN="$(aws iam get-instance-profile --instance-profile-name "$EC2_PROFILE_NAME" \
  --query 'InstanceProfile.Arn' --output text)"

cat <<EOF

== Done. Add these to server/.env ==
IMAGE_GENERATOR_AWS_REGION=$REGION
IMAGE_GENERATOR_SECURITY_GROUP_ID=$SG_ID
IMAGE_GENERATOR_SUBNET_ID=$SUBNET_ID
IMAGE_GENERATOR_AWS_ACCESS_KEY_ID=<from the create-access-key output above>
IMAGE_GENERATOR_AWS_SECRET_ACCESS_KEY=<from the create-access-key output above>
IMAGE_GENERATOR_INSTANCE_PROFILE_ARN=$EC2_PROFILE_ARN

IMAGE_GENERATOR_INFERENCE_TOKEN and IMAGE_GENERATOR_INFERENCE_PORT are
app-generated/chosen, not AWS resources — see server/.env.example.
LORA_TRAINER_AMI_ID is reused as-is; no separate AMI for this app.

If your home/office IP ever changes, re-run this script (or just re-run
the "authorize-security-group-ingress" step above with your new IP) —
the old IP's rule is left in place, so remove it manually via the console
or CLI if you want to tidy up stale entries.
EOF
