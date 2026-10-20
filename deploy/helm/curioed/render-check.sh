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
tunnel = need("Deployment", "-cloudflared")
llm_names = [name for (kind_name, name) in found if kind_name == "Deployment" and name.endswith("-worker-llm")]
if llm_names:
    raise SystemExit(f"workers.llm.enabled defaults to false, but rendered {llm_names}")
hpa = need("HorizontalPodAutoscaler", "-api")
job = need("Job", "-migrate")
seed = need("Job", "-seed")
route = need("IngressRoute", "curioed")
cluster = need("Cluster", "-postgres")

for label, doc, snippet in (
    ("api replicas", api, "replicas: 2"),
    ("api live probe", api, "path: /health/live"),
    ("api ready probe", api, "path: /health/ready"),
    ("docling memory", docling, "memory: 6Gi"),
    ("docling replicas", docling, "replicas: 1"),
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
    ("seed hook", seed, 'helm.sh/hook: "pre-install,pre-upgrade"'),
    ("seed weight", seed, 'helm.sh/hook-weight: "10"'),
    ("seed delete", seed, 'helm.sh/hook-delete-policy: "before-hook-creation,hook-succeeded"'),
    ("seed deadline", seed, "activeDeadlineSeconds: 600"),
    ("seed backoff", seed, "backoffLimit: 0"),
    ("seed ttl", seed, "ttlSecondsAfterFinished: 600"),
    ("seed command", seed, "education_platform.modules.materials.seed"),
    ("seed database url", seed, "$(POSTGRES_PASSWORD)"),
    ("seed materials", seed, "MATERIALS_DIR"),
    ("pgvector image", cluster, "ghcr.io/cloudnative-pg/postgresql:16.15-standard-bookworm"),
    ("pgvector extension", cluster, "CREATE EXTENSION IF NOT EXISTS vector"),
    ("progress flush", route, "flushInterval: 1ms"),
    ("health route", route, "PathPrefix(`/health`)"),
):
    if snippet not in doc:
        raise SystemExit(f"{label}: missing {snippet}")

def hook_weight(doc: str, label: str) -> int:
    match = re.search(r'helm.sh/hook-weight: "(\d+)"', doc)
    if match is None:
        raise SystemExit(f"{label} hook is missing a weight")
    return int(match.group(1))

if hook_weight(seed, "seed") <= hook_weight(job, "migrate"):
    raise SystemExit("curriculum seed hook must run after the migrate hook")
if "volumeMounts:" in seed or "persistentVolumeClaim:" in seed:
    raise SystemExit("curriculum seed job must not mount backups or uploads")

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
if "SUPERUSER" in text or "ALTER ROLE" in text:
    raise SystemExit("chart still grants the database owner superuser")

print(f"rendered {len(docs)} documents")
PY

migrate_off="$(helm template curioed "${chart}" --namespace curioed --set migrate.enabled=false)"
if printf '%s\n' "$migrate_off" | grep -q 'migrate-wrapper.sh'; then
  echo "migrate.enabled=false still rendered the migrate hook" >&2
  exit 1
fi
if ! printf '%s\n' "$migrate_off" | grep -q 'education_platform.modules.materials.seed'; then
  echo "disabling migrate dropped the curriculum seed hook" >&2
  exit 1
fi

if helm template curioed "${chart}" --namespace curioed --set curriculumSeed.enabled=false | grep -q 'education_platform.modules.materials.seed'; then
  echo "curriculumSeed.enabled=false still rendered the seed hook" >&2
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

helm template curioed "${chart}" --namespace curioed --set workers.llm.enabled=true > "${out}.llm"
python3 - "${out}.llm" <<'PY'
import re
import sys

text = open(sys.argv[1], encoding="utf-8").read()
docs = [doc for doc in re.split(r"(?m)^---\s*$", text) if doc.strip()]
matches = []
for doc in docs:
    kind = re.search(r"(?m)^kind: (\S+)", doc)
    name = re.search(r"(?m)^  name: (\S+)", doc)
    if kind and name and kind.group(1) == "Deployment" and name.group(1).endswith("-worker-llm"):
        matches.append(doc)
if len(matches) != 1:
    raise SystemExit(f"workers.llm.enabled=true should render one LLM worker, found {len(matches)}")
doc = matches[0]
for snippet in ("replicas: 2", "memory: 1.5Gi"):
    if snippet not in doc:
        raise SystemExit(f"enabled LLM worker is missing {snippet}")
print("enabled LLM worker rendered")
PY

echo "helm lint and template checks passed"
