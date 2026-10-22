#!/usr/bin/env bash
# Stop Compose cloudflared only when the k8s tunnel has a Ready pod.
# Never starts or scales anything. If kubectl fails or nothing is Ready,
# docker is not touched.
set -euo pipefail
set +x

namespace="${NAMESPACE:-curioed}"
deployment="${DEPLOYMENT:-curioed-cloudflared}"
prefix="${COMPOSE_CONTAINER_PREFIX:-education-platform-prod-cloudflared}"
dry_run=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)
      dry_run=1
      shift
      ;;
    --namespace)
      namespace="$2"
      shift 2
      ;;
    --deployment)
      deployment="$2"
      shift 2
      ;;
    *)
      echo "unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

log() {
  printf '%s %s\n' "$(date -Is)" "$*"
}

kube() {
  if [[ -n "${KUBECONFIG:-}" ]]; then
    kubectl --kubeconfig "${KUBECONFIG}" "$@"
    return
  fi
  if [[ -r /etc/rancher/k3s/k3s.yaml ]]; then
    kubectl --kubeconfig /etc/rancher/k3s/k3s.yaml "$@"
    return
  fi
  if command -v k3s >/dev/null 2>&1; then
    sudo k3s kubectl "$@"
    return
  fi
  kubectl "$@"
}

if ! deploy_json="$(kube get deploy "${deployment}" --namespace "${namespace}" -o json)"; then
  log "WARNING: kubectl failed; compose was not touched"
  exit 0
fi

parsed="$(
  printf '%s' "${deploy_json}" | python3 -c '
import json, sys
doc = json.load(sys.stdin)
spec = doc.get("spec") or {}
status = doc.get("status") or {}
print(int(spec.get("replicas") or 0))
print(int(status.get("readyReplicas") or 0))
'
)"
desired="${parsed%%$'\n'*}"
ready="${parsed#*$'\n'}"
log "k8s ${namespace}/${deployment} desired=${desired} ready=${ready}"

if [[ "${ready}" -lt 1 ]]; then
  log "WARNING: k8s cloudflared is not Ready (ready=${ready}); compose was not touched"
  exit 0
fi

if ! names="$(docker ps --filter status=running --format '{{.Names}}')"; then
  log "WARNING: docker ps failed; no stop was issued"
  exit 0
fi

while IFS= read -r name; do
  [[ -z "${name}" ]] && continue
  case "${name}" in
    "${prefix}"*)
      log "compose running ${name}"
      if [[ "${dry_run}" -eq 1 ]]; then
        log "dry-run would docker stop ${name}"
      else
        docker stop "${name}" >/dev/null
        log "stopped ${name}"
      fi
      ;;
  esac
done <<< "${names}"

log "compose containers matching ${prefix}:"
docker ps -a --filter "name=^/${prefix}" --format '{{.Names}} {{.Status}}' || true
