#!/usr/bin/env bash
# Build images from a separate clone and helm-upgrade prod. Never --atomic.
set -euo pipefail

clone="${CLONE:-${HOME}/curioed-k8s-clone}"
state_file="${STATE_FILE:-${HOME}/.local/state/curioed-k8s-sha}"
lock_file="${LOCK_FILE:-${HOME}/.local/state/curioed-k8s.lock}"
namespace="${NAMESPACE:-curioed}"
release="${RELEASE:-curioed}"
forbidden="/home/aushakanuj/Documents/work/education-platform"

mkdir -p "$(dirname "${state_file}")" "$(dirname "${lock_file}")"
exec 9>"${lock_file}"
if ! flock -n 9; then
  echo "another autodeploy holds the lock" >&2
  exit 1
fi

if [[ ! -d "${clone}" ]]; then
  echo "clone does not exist: ${clone}" >&2
  exit 1
fi
clone="$(cd "${clone}" && pwd -P)"
if [[ "${clone}" == "${forbidden}" ]]; then
  echo "refusing to build ${forbidden}" >&2
  exit 1
fi

cd "${clone}"
echo "Fetching origin/main in ${clone}"
GIT_TERMINAL_PROMPT=0 git fetch origin main
sha="$(git rev-parse origin/main)"
if [[ -f "${state_file}" && "$(cat "${state_file}")" == "${sha}" ]]; then
  echo "Already deployed ${sha}"
  exit 0
fi

echo "Building api and web ${sha}"
export DOCKER_BUILDKIT=1
docker build -f deploy/Dockerfile.api -t "curioed-api:${sha}" .
docker build -f deploy/Dockerfile.web -t "curioed-web:${sha}" --build-arg VITE_API_BASE_URL= .
echo "Importing images into k3s"
docker save "curioed-api:${sha}" "curioed-web:${sha}" | sudo k3s ctr images import -

echo "Helm upgrade ${release} in ${namespace}"
helm upgrade --install "${release}" deploy/helm/curioed \
  --namespace "${namespace}" \
  --create-namespace \
  -f deploy/helm/curioed/values-prod.yaml \
  --set "api.image.tag=${sha}" \
  --set "web.image.tag=${sha}" \
  --wait \
  --timeout 15m

printf '%s\n' "${sha}" > "${state_file}"
echo "Deploy finished ${sha}. Check the API /health/ready"
