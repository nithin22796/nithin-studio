#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

cleanup() {
  echo "Stopping server and client..."
  kill "$SERVER_PID" "$CLIENT_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

(
  cd "$ROOT/server"

  # Load everything from .env first (AWS/lora-trainer vars are identical
  # between native dev and Docker — only the vars below need translating).
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
  uv run uvicorn app.main:app --reload --port 8000
) &
SERVER_PID=$!

(
  cd "$ROOT/client"
  pnpm dev
) &
CLIENT_PID=$!

wait "$SERVER_PID" "$CLIENT_PID"
