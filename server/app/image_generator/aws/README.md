# image-generator AWS setup (one-time)

This provisions the AWS resources `image-generator` needs. It is **not run
by the app itself** — review it, then run it yourself with the AWS CLI
configured for an account/user that can create IAM users/policies and
security groups.

## What it creates

- **`image-generator-orchestrator`** IAM user — the credentials the
  `nithin-studio` server uses to launch/terminate the inference instance.
  Scoped to: launching only `g6.xlarge` instances from Amazon-owned or this
  account's own AMIs, terminating only instances tagged `App=image-generator`,
  describe/tag on instances generally, passing the `image-generator-ec2-role`
  role at launch, and issuing SSM Run Command calls for remote debugging.
  Separate from `lora-trainer-orchestrator` for a clean trust boundary.
- **`image-generator-ec2-role`** IAM role + **`image-generator-ec2-profile`**
  instance profile — attached to every inference instance at launch, with
  only the AWS-managed `AmazonSSMManagedInstanceCore` policy. This is the
  *only* AWS-facing permission the instance itself gets — it still never
  touches S3 or any other API. It exists purely so you can pull
  `instance_server.py`'s log via SSM if it ever crashes or hangs (see "Why
  an instance role" below).
- **Security group** (`image-generator-inference`) — **one inbound rule**:
  TCP on the inference port (default `8188`), restricted to your current
  public IP only. This is the one place in the whole project that opens any
  inbound access at all — see "Why one inbound rule" below.

## Why an instance role (SSM only)

This app has no S3 and no SSH by design (see "Why one inbound rule"), which
means if `instance_server.py` crashes or hangs partway through boot, there
was originally no way to see what happened short of guessing from
`describe-instances`/console-output. The `image-generator-ec2-role` fixes
that narrowly: it grants nothing but `AmazonSSMManagedInstanceCore`, which
lets the orchestrator run `GET /image-generator/sessions/{id}/log` (backed
by `ec2.fetch_instance_log`, an SSM Run Command that cats
`instance_server_boot.log` and `instance_server.log` off the instance).
There's still no S3 access, no other AWS permission, and no way to reach the
instance except through this one log-pulling path.

## Why one inbound rule (unlike lora-trainer's zero-inbound design)

Training instances pull their job from S3 and push results back to S3 over
outbound HTTPS only — nothing needs to reach *in*. This app is different:
you generate multiple images in one sitting without wanting to reload the
model each time, so a small inference server stays resident on the
instance for the whole session, and the studio server needs to reach it
directly. That means one inbound port has to be open.

Two layers keep this from being a real exposure:
1. **IP-restricted security group** — only your current public IP can even
   reach the port. If your IP changes (new location, ISP re-assigns it),
   generation will just fail to connect until you re-run `setup.sh` (or
   re-run its `authorize-security-group-ingress` step) with your new IP.
2. **Bearer token** (`IMAGE_GENERATOR_INFERENCE_TOKEN`) — every request to
   the instance's `/load-lora` and `/generate` endpoints must include this
   token, generated once and baked into the instance's boot script. Even an
   unexpected connection from within your IP range can't do anything
   without it.

## Running it

```sh
cd server/app/image_generator/aws
REGION=us-east-1 INFERENCE_PORT=8188 ./setup.sh
```

Safe to re-run for the IAM/user parts (each step checks whether its
resource already exists first). The security-group authorization step is
*meant* to be re-run whenever your IP changes — see above.

At the end it prints the values to put in `server/.env`
(`IMAGE_GENERATOR_*`), including a **new IAM access key shown only once** —
save it immediately. `IMAGE_GENERATOR_INFERENCE_TOKEN` isn't printed by this
script since it's not an AWS resource — generate your own (e.g.
`python3 -c "import secrets; print(secrets.token_hex(32))"`) and put it in
`.env` directly; it just needs to match between the orchestrator and
whatever gets baked into the instance's boot script (`ec2.py` reads the
same env var for both).

## Cost

`g6.xlarge` instances are launched fresh per session and terminated when
you click Stop (or when the app detects one failed to become ready) — same
lifecycle as training, just triggered manually instead of automatically.
Nothing is billed while a session isn't active. See the main
`lora_trainer/README.md`'s cost discussion for the per-hour rate; this app
deliberately uses the smaller/cheaper `g6.xlarge` rather than training's
`g6.2xlarge`, since plain inference doesn't have training's memory pressure.
