#!/bin/sh
set -eu

mkdir -p "${UPLOAD_DIR:-/data/uploads}" "${HF_HOME:-/data/hf}" "${TORCHINDUCTOR_CACHE_DIR:-/data/torchinductor}"

secret="${JWT_SECRET:-}"
if [ -z "$secret" ] || [ "$secret" = "dev-only-change-me-use-32bytes-min!!" ] || [ "${#secret}" -lt 32 ]; then
  echo "Refusing to start: set JWT_SECRET to a unique value of at least 32 characters." >&2
  exit 1
fi

if [ "$#" -gt 0 ]; then
  exec "$@"
fi

exec uvicorn education_platform.main:app \
  --host 0.0.0.0 \
  --port 8000 \
  --proxy-headers \
  --forwarded-allow-ips="*"
