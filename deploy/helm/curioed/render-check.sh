#!/bin/sh
# Lint the chart and fail if the render drifts from the k3s design.
set -eu

root="$(CDPATH= cd -- "$(dirname "$0")/../../.." && pwd)"
chart="${root}/deploy/helm/curioed"
out="${TMPDIR:-/tmp}/curioed-helm.yaml"

helm lint "${chart}"
helm template curioed "${chart}" --namespace curioed > "${out}"

python3 - "${out}" <<'PY'
import re
import sys

text = open(sys.argv[1], encoding="utf-8").read()
docs = [doc for doc in re.split(r"(?m)^---\s*$", text) if doc.strip()]

def name_of(doc: str) -> str:
    match = re.search(r"(?m)^  name: (\S+)", doc)
    if match is None:
        return ""
    return match.group(1)

def kind_of(doc: str) -> str:
    match = re.search(r"(?m)^kind: (\S+)", doc)
    if match is None:
        return ""
    return match.group(1)

found: dict[tuple[str, str], str] = {}
for doc in docs:
    found[(kind_of(doc), name_of(doc))] = doc

def need(kind: str, suffix: str) -> str:
    matches = [doc for (kind_name, name), doc in found.items() if kind_name == kind and name.endswith(suffix)]
    if len(matches) != 1:
        names = [name for (kind_name, name) in found if kind_name == kind]
        raise SystemExit(f"expected one {kind} ending with {suffix}, found {names}")
    return matches[0]

api = need("Deployment", "-api")
web = need("Deployment", "-web")
docling = need("Deployment", "-worker-docling")
llm = need("Deployment", "-worker-llm")
tunnel = need("Deployment", "-cloudflared")
hpa = need("HorizontalPodAutoscaler", "-api")
job = need("Job", "-migrate")
route = need("IngressRoute", "curioed")
cluster = need("Cluster", "-postgres")

for label, doc, snippet in (
    ("api replicas", api, "replicas: 2"),
    ("api live probe", api, "path: /health/live"),
    ("api ready probe", api, "path: /health/ready"),
    ("docling memory", docling, "memory: 6Gi"),
    ("docling replicas", docling, "replicas: 1"),
    ("llm replicas", llm, "replicas: 2"),
    ("llm memory", llm, "memory: 1.5Gi"),
    ("web replicas", web, "replicas: 2"),
    ("tunnel replicas", tunnel, "replicas: 2"),
    ("tunnel image", tunnel, "cloudflare/cloudflared:2026.10.1"),
    ("hpa min", hpa, "minReplicas: 2"),
    ("hpa max", hpa, "maxReplicas: 4"),
    ("hpa target", hpa, "averageUtilization: 65"),
    ("hpa scale up", hpa, "stabilizationWindowSeconds: 0"),
    ("hpa scale down", hpa, "stabilizationWindowSeconds: 300"),
    ("migrate hook", job, 'helm.sh/hook: "pre-install,pre-upgrade"'),
    ("migrate deadline", job, "activeDeadlineSeconds: 1800"),
    ("migrate backoff", job, "backoffLimit: 0"),
    ("migrate script", job, "migrate-wrapper.sh"),
    ("pgvector image", cluster, "ghcr.io/cloudnative-pg/postgresql:16.15-standard-bookworm"),
    ("pgvector extension", cluster, "CREATE EXTENSION IF NOT EXISTS vector"),
    ("progress flush", route, "flushInterval: 1ms"),
    ("health route", route, "PathPrefix(`/health`)"),
):
    if snippet not in doc:
        raise SystemExit(f"{label}: missing {snippet}")

body = need("Middleware", "-api-body")
if "maxRequestBodyBytes: 33554432" not in body:
    raise SystemExit("api body limit is not 33554432 bytes")

configmap = need("ConfigMap", "-migrate")
if "/app/deploy/migrate.sh" not in configmap:
    raise SystemExit("migrate hook does not exec migrate.sh")
if "Backing up, then running Alembic." not in configmap:
    raise SystemExit("migrate hook does not describe the backup-then-alembic order")

if re.search(r"(?m)^kind: Secret$", text):
    raise SystemExit("chart rendered a Secret")
if "stringData:" in text or ":latest" in text:
    raise SystemExit("chart rendered secret data or a latest tag")
if "$(POSTGRES_PASSWORD)" not in text:
    raise SystemExit("DATABASE_URL does not reference the password from the Secret")
if "replace-with" in text or re.search(r"\d(?:\.\d+)?e[+-]\d", text):
    raise SystemExit("chart rendered a placeholder secret or a float-formatted number")

print(f"rendered {len(docs)} documents")
PY

if helm template curioed "${chart}" --namespace curioed --set migrate.enabled=false | grep -q '^kind: Job$'; then
  echo "migrate.enabled=false still rendered a Job" >&2
  exit 1
fi

if helm template curioed "${chart}" --namespace curioed --set cnpg.backup.enabled=true > /dev/null 2>"${out}.err"; then
  echo "cnpg.backup.enabled=true should fail the render" >&2
  exit 1
fi
if ! grep -q "not supported yet" "${out}.err"; then
  echo "backup failure did not explain itself" >&2
  cat "${out}.err" >&2
  exit 1
fi

echo "helm lint and template checks passed"
