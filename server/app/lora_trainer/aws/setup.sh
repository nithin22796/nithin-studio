#!/usr/bin/env bash
# One-time AWS setup for lora-trainer. Review before running — this creates
# real resources in your AWS account. Requires the AWS CLI configured with
# credentials that can create S3 buckets, IAM roles/users/policies, and
# security groups.
#
# Safe to re-run: each step checks whether its resource already exists.

set -euo pipefail

BUCKET_NAME="${BUCKET_NAME:-nithin-studio-lora-trainer}"
REGION="${REGION:-us-east-1}"
ROLE_NAME="lora-trainer-ec2-role"
PROFILE_NAME="lora-trainer-ec2-profile"
ORCHESTRATOR_USER="lora-trainer-orchestrator"
ORCHESTRATOR_POLICY_NAME="lora-trainer-orchestrator-policy"
SG_NAME="lora-trainer-no-inbound"

ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

substitute() {
  sed -e "s/BUCKET_NAME/$BUCKET_NAME/g" \
      -e "s/REGION/$REGION/g" \
      -e "s/ACCOUNT_ID/$ACCOUNT_ID/g" \
      "$1"
}

echo "== Account: $ACCOUNT_ID  Region: $REGION  Bucket: $BUCKET_NAME =="

# --- S3 bucket ---
if aws s3api head-bucket --bucket "$BUCKET_NAME" 2>/dev/null; then
  echo "[s3] bucket already exists"
else
  if [ "$REGION" = "us-east-1" ]; then
    aws s3api create-bucket --bucket "$BUCKET_NAME" --region "$REGION"
  else
    aws s3api create-bucket --bucket "$BUCKET_NAME" --region "$REGION" \
      --create-bucket-configuration LocationConstraint="$REGION"
  fi
  echo "[s3] bucket created"
fi

aws s3api put-public-access-block --bucket "$BUCKET_NAME" \
  --public-access-block-configuration \
  BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true

aws s3api put-bucket-encryption --bucket "$BUCKET_NAME" \
  --server-side-encryption-configuration \
  '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'

aws s3api put-bucket-lifecycle-configuration --bucket "$BUCKET_NAME" \
  --lifecycle-configuration "file://$SCRIPT_DIR/bucket-lifecycle.json"

echo "[s3] public access blocked, default encryption + 1-day expiry lifecycle set"

# --- EC2 instance role ---
if aws iam get-role --role-name "$ROLE_NAME" >/dev/null 2>&1; then
  echo "[iam] instance role already exists"
else
  aws iam create-role --role-name "$ROLE_NAME" \
    --assume-role-policy-document \
    '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ec2.amazonaws.com"},"Action":"sts:AssumeRole"}]}'
  echo "[iam] instance role created"
fi

substitute "$SCRIPT_DIR/instance-role-policy.json" > "$TMP_DIR/instance-role-policy.json"
aws iam put-role-policy --role-name "$ROLE_NAME" \
  --policy-name lora-trainer-instance-policy \
  --policy-document "file://$TMP_DIR/instance-role-policy.json"

if aws iam get-instance-profile --instance-profile-name "$PROFILE_NAME" >/dev/null 2>&1; then
  echo "[iam] instance profile already exists"
else
  aws iam create-instance-profile --instance-profile-name "$PROFILE_NAME"
  aws iam add-role-to-instance-profile --instance-profile-name "$PROFILE_NAME" --role-name "$ROLE_NAME"
  echo "[iam] instance profile created and role attached"
  echo "[iam] waiting for instance profile to propagate..."
  sleep 10
fi

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

# --- Security group: no inbound rules (default VPC gets allow-all-outbound automatically) ---
VPC_ID="$(aws ec2 describe-vpcs --filters Name=isDefault,Values=true --query 'Vpcs[0].VpcId' --output text --region "$REGION")"
SUBNET_ID="$(aws ec2 describe-subnets --filters Name=vpc-id,Values="$VPC_ID" --query 'Subnets[0].SubnetId' --output text --region "$REGION")"

SG_ID="$(aws ec2 describe-security-groups --filters Name=group-name,Values="$SG_NAME" Name=vpc-id,Values="$VPC_ID" \
  --query 'SecurityGroups[0].GroupId' --output text --region "$REGION" 2>/dev/null || echo "None")"

if [ "$SG_ID" = "None" ] || [ -z "$SG_ID" ]; then
  SG_ID="$(aws ec2 create-security-group --group-name "$SG_NAME" \
    --description "lora-trainer training instances: no inbound access" \
    --vpc-id "$VPC_ID" --region "$REGION" --query 'GroupId' --output text)"
  echo "[ec2] security group created: $SG_ID"
else
  echo "[ec2] security group already exists: $SG_ID"
fi

cat <<EOF

== Done. Add these to server/.env ==
LORA_TRAINER_AWS_REGION=$REGION
LORA_TRAINER_S3_BUCKET=$BUCKET_NAME
LORA_TRAINER_INSTANCE_PROFILE_ARN=arn:aws:iam::$ACCOUNT_ID:instance-profile/$PROFILE_NAME
LORA_TRAINER_SECURITY_GROUP_ID=$SG_ID
LORA_TRAINER_SUBNET_ID=$SUBNET_ID
LORA_TRAINER_AWS_ACCESS_KEY_ID=<from the create-access-key output above>
LORA_TRAINER_AWS_SECRET_ACCESS_KEY=<from the create-access-key output above>
EOF
