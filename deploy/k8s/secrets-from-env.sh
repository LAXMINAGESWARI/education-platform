#!/usr/bin/env bash
# Create the four Secrets the chart references. Values stay off the command line.
set -euo pipefail
set +x

env_file="${ENV_FILE:-deploy/.env}"
namespace="${NAMESPACE:-curioed}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --env-file)
      env_file="$2"
      shift 2
      ;;
    --namespace)
      namespace="$2"
      shift 2
      ;;
    *)
      echo "unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

if [[ ! -f "${env_file}" ]]; then
  echo "missing env file" >&2
  exit 1
fi

env_get() {
  local key="$1"
  awk -F= -v key="${key}" '
    /^[[:space:]]*#/ { next }
    /^[[:space:]]*$/ { next }
    {
      name = $1
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", name)
      if (name == key) {
        sub(/^[^=]*=/, "")
        gsub(/^[[:space:]]+|[[:space:]]+$/, "")
        print
        exit
      }
    }
  ' "${env_file}"
}

strip_quotes() {
  local value="$1"
  local length="${#value}"
  if [[ "${length}" -ge 2 ]]; then
    local first="${value:0:1}"
    local last="${value:length-1:1}"
    if [[ "${first}" == "${last}" && ( "${first}" == '"' || "${first}" == "'" ) ]]; then
      value="${value:1:length-2}"
    fi
  fi
  printf '%s' "${value}"
}

require_value() {
  local key="$1"
  local value
  value="$(strip_quotes "$(env_get "${key}")")"
  if [[ -z "${value}" ]]; then
    echo "env file is missing ${key}" >&2
    exit 1
  fi
  printf '%s' "${value}"
}

umask 077
tmp="$(mktemp)"
trap 'rm -f "${tmp}"' EXIT

apply_secret() {
  local name="$1"
  local secret_type="${2:-}"
  if [[ -n "${secret_type}" ]]; then
    kubectl create secret generic "${name}" \
      --namespace "${namespace}" \
      --type "${secret_type}" \
      --from-env-file "${tmp}" \
      --dry-run=client \
      -o yaml | kubectl apply -f -
  else
    kubectl create secret generic "${name}" \
      --namespace "${namespace}" \
      --from-env-file "${tmp}" \
      --dry-run=client \
      -o yaml | kubectl apply -f -
  fi
  : > "${tmp}"
}

jwt="$(require_value JWT_SECRET)"
sql_password="$(require_value TEXT_TO_SQL_DB_PASSWORD)"
openrouter="$(strip_quotes "$(env_get OPENROUTER_API_KEY)")"
postgres_user="$(require_value POSTGRES_USER)"
postgres_password="$(require_value POSTGRES_PASSWORD)"
reader_user="$(strip_quotes "$(env_get TEXT_TO_SQL_DB_USER)")"
reader_user="${reader_user:-text_to_sql_reader}"
tunnel="$(require_value TUNNEL_TOKEN)"

{
  printf 'JWT_SECRET=%s\n' "${jwt}"
  printf 'TEXT_TO_SQL_DB_PASSWORD=%s\n' "${sql_password}"
  printf 'OPENROUTER_API_KEY=%s\n' "${openrouter}"
} > "${tmp}"
apply_secret curioed-app

{
  printf 'username=%s\n' "${postgres_user}"
  printf 'password=%s\n' "${postgres_password}"
} > "${tmp}"
apply_secret curioed-postgres-app kubernetes.io/basic-auth

{
  printf 'username=%s\n' "${reader_user}"
  printf 'password=%s\n' "${sql_password}"
} > "${tmp}"
apply_secret curioed-postgres-reader kubernetes.io/basic-auth

printf 'TUNNEL_TOKEN=%s\n' "${tunnel}" > "${tmp}"
apply_secret curioed-cloudflared

echo "secrets applied in ${namespace}"
