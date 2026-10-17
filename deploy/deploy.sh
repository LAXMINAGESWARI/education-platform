#!/usr/bin/env bash
# Pull, build, migrate, and recreate the app containers. Safe to run twice.
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$root"

env_file="deploy/.env"
if [[ ! -f "$env_file" ]]; then
  echo "Missing $env_file. Copy deploy/.env.prod.example and fill it in." >&2
  exit 1
fi

env_get() {
  awk -F= -v key="$1" '
    $0 ~ /^[[:space:]]*#/ { next }
    $1 == key {
      sub(/^[^=]*=/, "")
      print
      exit
    }
  ' "$env_file"
}

require_var() {
  local name="$1"
  local value
  value="$(env_get "$name")"
  if [[ -z "$value" ]]; then
    echo "$env_file is missing $name." >&2
    exit 1
  fi
  printf '%s' "$value"
}

if grep -E '^[^#]*replace-with' "$env_file" >/dev/null; then
  echo "Replace every replace-with placeholder in $env_file." >&2
  exit 1
fi

domain="$(require_var DOMAIN)"
require_var ACME_EMAIL >/dev/null
require_var POSTGRES_USER >/dev/null
require_var POSTGRES_PASSWORD >/dev/null
require_var POSTGRES_DB >/dev/null
require_var JWT_SECRET >/dev/null
require_var TEXT_TO_SQL_DB_PASSWORD >/dev/null
require_var ENVIRONMENT >/dev/null
require_var CORS_ORIGINS >/dev/null

case "$domain" in
  *example.com)
    echo "Set DOMAIN to the real hostname (got: ${domain})." >&2
    exit 1
    ;;
  localhost | 127.0.0.1)
    if [[ "${ALLOW_LOCAL_DOMAIN:-0}" != "1" ]]; then
      echo "DOMAIN=${domain} is only for a local TLS rehearsal. Set ALLOW_LOCAL_DOMAIN=1 to continue." >&2
      exit 1
    fi
    ;;
esac

cors="$(env_get CORS_ORIGINS)"
if [[ "$cors" != *"$domain"* ]]; then
  echo "CORS_ORIGINS must include ${domain} so it matches the site Caddy serves." >&2
  exit 1
fi

jwt="$(env_get JWT_SECRET)"
if [[ "${#jwt}" -lt 32 || "$jwt" == "dev-only-change-me-use-32bytes-min!!" ]]; then
  echo "JWT_SECRET must be a unique value of at least 32 characters." >&2
  exit 1
fi

if [[ "${DEPLOY_SKIP_GIT:-0}" != "1" && "${DEPLOY_REEXECED:-0}" != "1" ]]; then
  export GIT_TERMINAL_PROMPT=0
  git fetch origin main
  git checkout main
  git pull --ff-only origin main
  export DEPLOY_REEXECED=1
  exec "$root/deploy/deploy.sh"
fi

export DOCKER_BUILDKIT=1
export COMPOSE_DOCKER_CLI_BUILD=1
compose=(docker compose --env-file "$env_file" -f deploy/docker-compose.prod.yml)

"${compose[@]}" pull postgres caddy
"${compose[@]}" build
"${compose[@]}" up -d --wait postgres
"${compose[@]}" up --force-recreate --no-deps --abort-on-container-exit --exit-code-from migrate migrate
"${compose[@]}" up -d --remove-orphans --wait --force-recreate --no-deps api worker web caddy

docker image prune -af
if ! docker builder prune -f --filter until=24h; then
  echo "Builder cache prune was skipped. Image prune still ran." >&2
fi

echo "Deploy finished. Check https://${domain}/health"
