#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "==> server: ruff check"
(
  cd "$ROOT/server"
  uv run ruff check .
)

echo "==> server: pytest"
(
  cd "$ROOT/server"

  set -a
  [ -f .env ] && source .env
  set +a

  export DATABASE_URL="postgresql://studio:changeme@localhost:5432/nithin_studio"
  export CLIENT_ORIGIN="http://localhost:5173"
  export FILE_MANAGER_DATABASE_URL="postgresql://studio:changeme@localhost:5432/file_manager"
  export FILE_MANAGER_S3_ENDPOINT_URL="http://localhost:9000"
  export FILE_MANAGER_S3_ACCESS_KEY="${FILE_MANAGER_S3_ACCESS_KEY:-studio}"
  export FILE_MANAGER_S3_SECRET_KEY="${FILE_MANAGER_S3_SECRET_KEY:-changeme123}"
  export FILE_MANAGER_S3_BUCKET="${FILE_MANAGER_S3_BUCKET:-file-manager}"
  export LORA_TRAINER_DATABASE_URL="postgresql://studio:changeme@localhost:5432/lora_trainer"
  uv run pytest -q
)

echo "==> client: tsc"
(
  cd "$ROOT/client"
  pnpm exec tsc -b
)

echo "==> client: eslint"
(
  cd "$ROOT/client"
  pnpm exec eslint .
)

echo "==> client: vitest"
(
  cd "$ROOT/client"
  pnpm exec vitest run
)

echo "==> all checks passed"
