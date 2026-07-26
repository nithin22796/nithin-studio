# lora-trainer AWS setup (one-time)

This provisions the AWS resources `lora-trainer` needs. It is **not run by
the app itself** — review it, then run it yourself with the AWS CLI
configured for an account/user that can create S3 buckets, IAM
roles/users/policies, and security groups.

## What it creates

- **S3 bucket** (`nithin-studio-lora-trainer` by default) — private, blocks
  all public access, default SSE-S3 encryption, and a lifecycle rule that
  expires anything under `jobs/` after 1 day as a safety net in case the
  app's own cleanup ever fails to run.
- **`lora-trainer-ec2-role`** — the IAM role attached to each training
  instance. Scoped to only read/write this bucket's `jobs/*` prefix, and to
  terminate *itself* (via a tag condition), nothing else in your account.
- **`lora-trainer-orchestrator`** IAM user — the credentials the
  `nithin-studio` server itself uses to launch/monitor/terminate instances
  and manage the job's S3 objects. Scoped to: the training bucket, launching
  only `g6.2xlarge`/`g6.xlarge`/`g5.xlarge` instances from Amazon-owned *or*
  this account's own AMIs (needed once training moved to a custom prebuilt
  AMI — see `../README.md`'s AMI-baking section), and
  describe/terminate/tag on instances.
- **Security group** (`lora-trainer-no-inbound`) — no inbound rules at all.
  Training instances are launched with no SSH access; they pull their job
  from S3 and push results back to S3 over outbound HTTPS only.

## Why no SSH

The instance's user-data script (supplied per-job by the orchestrator) does
everything on boot: install training deps, pull the dataset from S3, run
training, push the result to S3, then self-terminate. There's nothing to log
into, so there's no inbound surface to leave open or forget about.

## Running it

```sh
cd server/app/lora_trainer/aws
BUCKET_NAME=nithin-studio-lora-trainer REGION=us-east-1 ./setup.sh
```

It's safe to re-run — each step checks whether its resource already exists
first. At the end it prints the values to put in `server/.env`
(`LORA_TRAINER_*`), including a **new IAM access key shown only once** —
save it immediately.

## Data lifecycle per job (enforced by the app, not this script)

1. Dataset images + captions uploaded to `s3://<bucket>/jobs/<job_id>/dataset/`.
2. Instance launches, trains, uploads the result to
   `s3://<bucket>/jobs/<job_id>/output/`, self-terminates.
3. Orchestrator downloads the result into file-manager, then deletes the
   entire `jobs/<job_id>/` prefix from S3 and verifies the instance is
   terminated (force-terminating on a timeout as a safety net).

End state: nothing from a training run persists in AWS once it completes —
only the downloaded `.safetensors` file, stored locally via file-manager.

## Tightening further (optional, not done by default)

- Restrict the security group's default allow-all outbound to just 443 if
  your training/pip mirrors don't need anything else.
- Use SSE-KMS with a customer-managed key instead of SSE-S3 if you want
  audit-logged key usage.
- Narrow `ec2:RunInstances`' AMI condition beyond "owned by Amazon" to a
  specific AMI ID once you've picked one, so a compromised orchestrator
  credential can't launch an arbitrary Amazon AMI.
