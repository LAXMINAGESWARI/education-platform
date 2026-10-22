#!/usr/bin/env bash
# Sample the node and the load-test HPAs. Abort if memory or prod health fails.
set -euo pipefail

namespace="${NAMESPACE:-curioed-loadtest}"
api_hpa="${API_HPA:-curioed-api}"
web_hpa="${WEB_HPA:-curioed-web}"
k6_job="${K6_JOB:-curioed-k6}"
cluster="${CLUSTER:-curioed-postgres}"
database="${DATABASE:-education}"
interval="${INTERVAL:-5}"
out="${OUT:-deploy/loadtest/collect.csv}"
floor_kib="${MEM_FLOOR_KIB:-$((3 * 1024 * 1024))}"

mkdir -p "$(dirname "${out}")"
if [[ ! -f "${out}" ]]; then
  echo "timestamp,api_current,api_desired,web_current,web_desired,mem_available_kib,pg_activity,k6_p50,k6_p95,k6_p99,pod_top,node_top" > "${out}"
fi

abort() {
  echo "$1" >&2
  kubectl delete job --namespace "${namespace}" "${k6_job}" --ignore-not-found
  exit 1
}

hpa_field() {
  local name="$1"
  local path="$2"
  local value
  value="$(kubectl get hpa --namespace "${namespace}" "${name}" -o "jsonpath=${path}" 2>/dev/null || true)"
  if [[ -z "${value}" ]]; then
    printf 'na'
  else
    printf '%s' "${value}"
  fi
}

pg_counts() {
  local pod
  pod="$(
    kubectl get pods --namespace "${namespace}" \
      -l "cnpg.io/cluster=${cluster},cnpg.io/instanceRole=primary" \
      -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true
  )"
  if [[ -z "${pod}" ]]; then
    printf 'unavailable'
    return
  fi
  kubectl exec --namespace "${namespace}" "${pod}" -c postgres -- \
    psql --username postgres --dbname "${database}" --no-align --tuples-only \
    --command "SELECT coalesce(state, 'null') || '=' || count(*) FROM pg_stat_activity GROUP BY state ORDER BY 1" \
    2>/dev/null | paste -sd ';' - || printf 'unavailable'
}

k6_percentile() {
  local name="$1"
  local logs
  logs="$(kubectl logs --namespace "${namespace}" "job/${k6_job}" --tail=200 2>/dev/null || true)"
  printf '%s\n' "${logs}" | python3 -c '
import json, sys
name = sys.argv[1]
raw = sys.stdin.read()
marker = "K6_STAGE_SUMMARY "
line = ""
for item in raw.splitlines():
    if marker in item:
        line = item.split(marker, 1)[1]
if not line:
    print("")
    raise SystemExit
try:
    payload = json.loads(line)
except json.JSONDecodeError:
    print("")
    raise SystemExit
values = []
for stage in payload.get("stages", []):
    value = stage.get(name)
    if isinstance(value, (int, float)):
        values.append(value)
print(max(values) if values else "")
' "${name}"
}

while true; do
  stamp="$(date -Is)"
  mem_kib="$(awk '/^MemAvailable:/ { print $2 }' /proc/meminfo)"
  if [[ -n "${PROD_HEALTH_URL:-}" ]]; then
    if ! curl --fail --silent --show-error --max-time 5 "${PROD_HEALTH_URL}" >/dev/null; then
      abort "prod health failed"
    fi
  fi
  if [[ "${mem_kib}" -lt "${floor_kib}" ]]; then
    abort "MemAvailable ${mem_kib} KiB is below ${floor_kib}"
  fi

  api_current="$(hpa_field "${api_hpa}" '{.status.currentReplicas}')"
  api_desired="$(hpa_field "${api_hpa}" '{.status.desiredReplicas}')"
  web_current="$(hpa_field "${web_hpa}" '{.status.currentReplicas}')"
  web_desired="$(hpa_field "${web_hpa}" '{.status.desiredReplicas}')"
  activity="$(pg_counts)"
  p50="$(k6_percentile p50)"
  p95="$(k6_percentile p95)"
  p99="$(k6_percentile p99)"
  pod_top="$(kubectl top pods --namespace "${namespace}" --no-headers 2>/dev/null | paste -sd ';' - || true)"
  node_top="$(kubectl top nodes --no-headers 2>/dev/null | paste -sd ';' - || true)"

  printf '%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,"%s","%s"\n' \
    "${stamp}" "${api_current}" "${api_desired}" "${web_current}" "${web_desired}" \
    "${mem_kib}" "${activity}" "${p50}" "${p95}" "${p99}" \
    "${pod_top//\"/}" "${node_top//\"/}" >> "${out}"
  sleep "${interval}"
done
