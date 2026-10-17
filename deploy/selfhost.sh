#!/usr/bin/env bash
# PC demo: build, migrate, and expose the stack through Cloudflare Tunnel.
# VM deploys keep using deploy.sh (public 80/443 and Let's Encrypt).
#
#   ./deploy/selfhost.sh up|down|logs|url|update
#
# Leave CLOUDFLARE_TUNNEL_TOKEN empty for a trycloudflare.com URL.
# Set it for a named tunnel (public hostname HTTP → caddy:80).
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$root"

env_file="deploy/.env"
quick_origin_regex='https://[A-Za-z0-9-]+\.trycloudflare\.com'

usage() {
  echo "Usage: ./deploy/selfhost.sh up|down|logs|url|update" >&2
  exit 1
}

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

if [[ $# -lt 1 ]]; then
  usage
fi
action="$1"
shift

case "$action" in
  up | down | logs | url | update) ;;
  *) usage ;;
esac

if [[ ! -f "$env_file" ]]; then
  echo "Missing $env_file. Copy deploy/.env.prod.example and fill it in." >&2
  exit 1
fi

compose_files=(-f deploy/docker-compose.prod.yml -f deploy/docker-compose.tunnel.yml)

compose() {
  docker compose --project-name education-platform-prod --env-file "$env_file" "${compose_files[@]}" "$@"
}

token="$(env_get CLOUDFLARE_TUNNEL_TOKEN || true)"
if [[ -n "$token" ]]; then
  mode="named"
  profile="named"
  tunnel_service="cloudflared-named"
else
  mode="quick"
  profile="quick"
  tunnel_service="cloudflared"
fi

prepare_runtime() {
  if grep -E '^[^#]*replace-with' "$env_file" >/dev/null; then
    echo "Replace every replace-with placeholder in $env_file." >&2
    exit 1
  fi

  require_var POSTGRES_USER >/dev/null
  require_var POSTGRES_PASSWORD >/dev/null
  require_var POSTGRES_DB >/dev/null
  require_var JWT_SECRET >/dev/null
  require_var TEXT_TO_SQL_DB_PASSWORD >/dev/null
  require_var ENVIRONMENT >/dev/null

  jwt="$(env_get JWT_SECRET)"
  if [[ "${#jwt}" -lt 32 || "$jwt" == "dev-only-change-me-use-32bytes-min!!" ]]; then
    echo "JWT_SECRET must be a unique value of at least 32 characters." >&2
    exit 1
  fi

  if [[ "$mode" == "named" ]]; then
    domain="$(require_var DOMAIN)"
    require_var CORS_ORIGINS >/dev/null
    case "$domain" in
      *example.com | localhost | 127.0.0.1)
        echo "Named tunnel DOMAIN must be the real hostname (got: ${domain})." >&2
        exit 1
        ;;
    esac
    cors="$(env_get CORS_ORIGINS)"
    if [[ "$cors" != *"$domain"* ]]; then
      echo "CORS_ORIGINS must include ${domain}." >&2
      exit 1
    fi
    if [[ -z "${CORS_ORIGIN_REGEX+x}" ]]; then
      export CORS_ORIGIN_REGEX="$(env_get CORS_ORIGIN_REGEX || true)"
    fi
    echo "Self-host mode: named tunnel for https://${domain}"
  else
    export CORS_ORIGIN_REGEX="$quick_origin_regex"
    echo "Self-host mode: quick tunnel (https://*.trycloudflare.com)"
  fi
}

tunnel_url_from_logs() {
  local logs
  logs="$(compose --profile "$profile" logs --no-color --tail=200 "$tunnel_service" 2>&1 || true)"
  printf '%s\n' "$logs" | grep -oE 'https://[A-Za-z0-9-]+\.trycloudflare\.com' | tail -n 1
}

print_quick_url() {
  local url=""
  local attempt
  for attempt in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22 23 24 25 26 27 28 29 30; do
    url="$(tunnel_url_from_logs || true)"
    if [[ -n "$url" ]]; then
      break
    fi
    sleep 2
  done
  if [[ -z "$url" ]]; then
    echo "cloudflared is up, and the trycloudflare.com URL is not in the logs yet." >&2
    echo "Run ./deploy/selfhost.sh url" >&2
    return 0
  fi
  printf '%s\n' "$url"
  if curl --fail --silent --show-error --max-time 20 "${url}/health"; then
    printf '\n'
  else
    echo "Stack is up. ${url}/health did not answer yet." >&2
  fi
}

bring_up() {
  prepare_runtime
  export DOCKER_BUILDKIT=1
  export COMPOSE_DOCKER_CLI_BUILD=1
  compose --profile "$profile" pull postgres caddy "$tunnel_service"
  compose --profile "$profile" build
  compose --profile "$profile" up -d --wait postgres
  compose --profile "$profile" up --force-recreate --no-deps --abort-on-container-exit --exit-code-from migrate migrate
  compose --profile "$profile" up -d --remove-orphans --wait --force-recreate --no-deps \
    api worker web caddy "$tunnel_service"
  if [[ "$mode" == "quick" ]]; then
    print_quick_url
  else
    echo "Named tunnel is up. Open https://$(env_get DOMAIN)/health once DNS points at Cloudflare."
  fi
}

case "$action" in
  down)
    compose --profile quick --profile named down --remove-orphans
    ;;
  logs)
    compose --profile quick --profile named logs -f --tail=200 "$@"
    ;;
  url)
    if [[ "$mode" == "named" ]]; then
      domain="$(require_var DOMAIN)"
      echo "https://${domain}"
      exit 0
    fi
    found="$(tunnel_url_from_logs || true)"
    if [[ -z "$found" ]]; then
      echo "No trycloudflare.com URL in the cloudflared logs. Run ./deploy/selfhost.sh up first." >&2
      exit 1
    fi
    printf '%s\n' "$found"
    ;;
  update)
    if [[ "${DEPLOY_SKIP_GIT:-0}" != "1" && "${SELFHOST_REEXECED:-0}" != "1" ]]; then
      export GIT_TERMINAL_PROMPT=0
      git pull --ff-only
      export SELFHOST_REEXECED=1
      exec "$root/deploy/selfhost.sh" update
    fi
    bring_up
    docker image prune -af
    if ! docker builder prune -f --filter until=24h; then
      echo "Builder cache prune was skipped. Image prune still ran." >&2
    fi
    ;;
  up)
    bring_up
    ;;
esac
