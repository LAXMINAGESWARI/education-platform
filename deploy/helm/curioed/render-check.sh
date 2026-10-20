#!/bin/sh
# Lint the chart and fail if the render drifts from the k3s design.
set -eu

root="$(CDPATH= cd -- "$(dirname "$0")/../../.." && pwd)"
chart="${root}/deploy/helm/curioed"
out="${TMPDIR:-/tmp}/curioed-helm.yaml"
# ci is a render placeholder. It is not a published image.
test_tag="--set api.image.tag=ci --set web.image.tag=ci"

helm lint "${chart}" ${test_tag}
helm template curioed "${chart}" --namespace curioed ${test_tag} > "${out}"

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

def absent(kind: str) -> None:
    names = [name for (kind_name, name) in found if kind_name == kind]
    if names:
        raise SystemExit(f"{kind} rendered by default: {names}")

api = need("Deployment", "-api")
web = need("Deployment", "-web")
docling = need("Deployment", "-worker-docling")
tunnel = need("Deployment", "-cloudflared")
llm_names = [name for (kind_name, name) in found if kind_name == "Deployment" and name.endswith("-worker-llm")]
if llm_names:
    raise SystemExit(f"workers.llm.enabled defaults to false, but rendered {llm_names}")
hpa = need("HorizontalPodAutoscaler", "-api")
web_hpa = [name for (kind_name, name) in found if kind_name == "HorizontalPodAutoscaler" and name.endswith("-web")]
if web_hpa:
    raise SystemExit(f"web HPA is behind a flag, but rendered {web_hpa}")
job = need("Job", "-migrate")
seed_names = [name for (kind_name, name) in found if kind_name == "Job" and name.endswith("-seed")]
if seed_names:
    raise SystemExit(f"curriculumSeed.enabled defaults to false, but rendered {seed_names}")
route = need("IngressRoute", "curioed")
cluster = need("Cluster", "-postgres")
quota = need("ResourceQuota", "curioed")
limits = need("LimitRange", "curioed")
platform = need("ConfigMap", "-platform")
absent("NetworkPolicy")
absent("PriorityClass")
absent("ScaledObject")
absent("TriggerAuthentication")
absent("Namespace")

for label, doc, snippet in (
    ("api replicas", api, "replicas: 1"),
    ("api live probe", api, "path: /health/live"),
    ("api ready probe", api, "path: /health/ready"),
    ("api startup", api, "failureThreshold: 12"),
    ("api startup period", api, "periodSeconds: 5"),
    ("api concurrency", api, '"200"'),
    ("api memory limit", api, "memory: 2Gi"),
    ("api cpu request", api, "cpu: 250m"),
    ("docling memory", docling, "memory: 3Gi"),
    ("docling cpu", docling, 'cpu: "2"'),
    ("docling replicas", docling, "replicas: 1"),
    ("docling grace", docling, "terminationGracePeriodSeconds: 600"),
    ("web replicas", web, "replicas: 2"),
    ("web cap", web, "NET_BIND_SERVICE"),
    ("tunnel replicas", tunnel, "replicas: 2"),
    ("tunnel image", tunnel, "cloudflare/cloudflared:2026.10.1"),
    ("hpa min", hpa, "minReplicas: 1"),
    ("hpa max", hpa, "maxReplicas: 4"),
    ("hpa target", hpa, "averageUtilization: 65"),
    ("hpa scale up", hpa, "stabilizationWindowSeconds: 0"),
    ("hpa scale down", hpa, "stabilizationWindowSeconds: 300"),
    ("migrate hook", job, 'helm.sh/hook: "pre-install,pre-upgrade"'),
    ("migrate deadline", job, "activeDeadlineSeconds: 1800"),
    ("migrate backoff", job, "backoffLimit: 0"),
    ("migrate script", job, "migrate-wrapper.sh"),
    ("migrate memory", job, "memory: 256Mi"),
    ("pgvector image", cluster, "ghcr.io/cloudnative-pg/postgresql:16.15-standard-bookworm"),
    ("pgvector extension", cluster, "CREATE EXTENSION IF NOT EXISTS vector"),
    ("shared buffers", cluster, 'shared_buffers: "512MB"'),
    ("effective cache", cluster, 'effective_cache_size: "1GB"'),
    ("max connections", cluster, 'max_connections: "150"'),
    ("cluster keep", cluster, "helm.sh/resource-policy: keep"),
    ("progress flush", route, "flushInterval: 1ms"),
    ("health route", route, "PathPrefix(`/health`)"),
    ("quota requests", quota, "requests.memory: 8Gi"),
    ("quota limits", quota, "limits.memory: 14Gi"),
    ("limit default", limits, "memory: 256Mi"),
    ("platform env", platform, "AI_GATEWAY_FEATURES:"),
    ("guard env", platform, "GUARD_AUDIT_STORE_PREVIEW:"),
    ("jev env", platform, "JEV_MODEL:"),
):
    if snippet not in doc:
        raise SystemExit(f"{label}: missing {snippet}")

if "db_ping" in text or "readinessProbe:" in docling:
    raise SystemExit("worker probes must not ping the database")
if "livenessProbe:" in docling or "readinessProbe:" in docling:
    raise SystemExit("docling worker has no Service, so it has no probes")
if "runAsNonRoot: false" not in api:
    raise SystemExit("api runAsNonRoot stays false until Dockerfile.api has a USER line")
if "runAsUser:" in api:
    raise SystemExit("api must not set runAsUser while the image is root")
web = need("Deployment", "-web")
if "runAsNonRoot: false" not in web or "runAsUser:" in web:
    raise SystemExit("web nginx image is root and must not set runAsUser")
if "NET_BIND_SERVICE" not in web:
    raise SystemExit("web still needs NET_BIND_SERVICE to bind port 80")
tunnel = need("Deployment", "-cloudflared")
if "runAsNonRoot: true" not in tunnel or "runAsUser: 65532" not in tunnel:
    raise SystemExit("cloudflared image user is 65532")
docling = need("Deployment", "-worker-docling")
if "runAsNonRoot: false" not in docling or "runAsUser:" in docling:
    raise SystemExit("docling worker uses the root api image")
if "allowPrivilegeEscalation: false" not in api:
    raise SystemExit("api is missing allowPrivilegeEscalation: false")
if "type: RuntimeDefault" not in api:
    raise SystemExit("api is missing the RuntimeDefault seccomp profile")
if "readOnlyRootFilesystem: false" not in api:
    raise SystemExit("readOnlyRootFilesystem stays false")
if re.search(r"(?m)^kind: PodDisruptionBudget$", text) is None:
    raise SystemExit("web and cloudflared should render PDBs")
for doc in docs:
    if kind_of(doc) == "PodDisruptionBudget":
        if "maxUnavailable: 1" not in doc or "minAvailable:" in doc:
            raise SystemExit(f"PDB {name_of(doc)} must be maxUnavailable 1")
pdb_names = [name for (kind_name, name) in found if kind_name == "PodDisruptionBudget"]
if any(name.endswith("-api") for name in pdb_names):
    raise SystemExit("api minReplicas is 1, so it must not get a PDB")
if not any(name.endswith("-web") for name in pdb_names):
    raise SystemExit("web has 2 replicas and needs a PDB")
if not any(name.endswith("-cloudflared") for name in pdb_names):
    raise SystemExit("cloudflared has 2 replicas and needs a PDB")

for claim in ("-uploads", "-hf-cache", "-inductor-cache", "-backups"):
    pvc = need("PersistentVolumeClaim", claim)
    if "helm.sh/resource-policy: keep" not in pvc:
        raise SystemExit(f"{claim} PVC is missing the keep annotation")

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
if "OPENROUTER_API_KEY" not in api or "secretKeyRef:" not in api:
    raise SystemExit("OPENROUTER_API_KEY must stay a secretKeyRef")
if "AI_GATEWAY_FEATURES" in api:
    raise SystemExit("platform env belongs in the ConfigMap, not inline on the api pod")

print(f"rendered {len(docs)} documents")
PY

migrate_off="$(helm template curioed "${chart}" --namespace curioed --set migrate.enabled=false ${test_tag})"
if printf '%s\n' "$migrate_off" | grep -q 'migrate-wrapper.sh'; then
  echo "migrate.enabled=false still rendered the migrate hook" >&2
  exit 1
fi
if printf '%s\n' "$migrate_off" | grep -q 'education_platform.modules.materials.seed'; then
  echo "default install rendered the curriculum seed hook" >&2
  exit 1
fi

seed_on="$(helm template curioed "${chart}" --namespace curioed --set curriculumSeed.enabled=true --set migrate.enabled=false ${test_tag})"
if printf '%s\n' "$seed_on" | grep -q 'migrate-wrapper.sh'; then
  echo "seed-only render still included migrate" >&2
  exit 1
fi
if ! printf '%s\n' "$seed_on" | grep -q 'education_platform.modules.materials.seed'; then
  echo "curriculumSeed.enabled=true did not render the seed hook" >&2
  exit 1
fi
if ! printf '%s\n' "$seed_on" | grep -q 'memory: 256Mi'; then
  echo "seed job is missing its resource request" >&2
  exit 1
fi
if ! printf '%s\n' "$seed_on" | grep -q 'cpu: 100m'; then
  echo "seed job is missing its cpu request" >&2
  exit 1
fi
if ! printf '%s\n' "$seed_on" | grep -q 'memory: 1Gi'; then
  echo "seed job is missing its memory limit" >&2
  exit 1
fi

if helm template curioed "${chart}" --namespace curioed --set cnpg.backup.enabled=true ${test_tag} > /dev/null 2>"${out}.err"; then
  echo "cnpg.backup.enabled=true should fail the render" >&2
  exit 1
fi
if ! grep -q "not supported yet" "${out}.err"; then
  echo "backup failure did not explain itself" >&2
  cat "${out}.err" >&2
  exit 1
fi

if helm template curioed "${chart}" --namespace curioed \
  --set networkPolicy.enabled=true \
  --set networkPolicy.postgres.mode=cidr \
  --set networkPolicy.postgres.cidr= \
  ${test_tag} \
  > /dev/null 2>"${out}.err"; then
  echo "empty postgres cidr should fail the render" >&2
  exit 1
fi

if helm template curioed "${chart}" --namespace curioed --set api.image.tag=latest --set web.image.tag=ci > /dev/null 2>"${out}.err"; then
  echo "latest image tag should fail the render" >&2
  exit 1
fi
if helm template curioed "${chart}" --namespace curioed > /dev/null 2>"${out}.err"; then
  echo "an empty image tag should fail the render" >&2
  exit 1
fi
if ! grep -q "image.tag is required" "${out}.err"; then
  echo "empty image tag did not say the tag is required" >&2
  cat "${out}.err" >&2
  exit 1
fi

helm template curioed "${chart}" --namespace curioed --set workers.llm.enabled=true ${test_tag} > "${out}.llm"
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
for snippet in ("replicas: 1", "memory: 1.5Gi", "terminationGracePeriodSeconds: 300"):
    if snippet not in doc:
        raise SystemExit(f"enabled LLM worker is missing {snippet}")
if "livenessProbe:" in doc or "readinessProbe:" in doc or "db_ping" in doc:
    raise SystemExit("LLM worker must not probe the database")
if "kind: ScaledObject" in text:
    raise SystemExit("KEDA stays off unless keda.llm.enabled is set")
print("enabled LLM worker rendered")
PY

all_on="${out}.all"
helm template curioed "${chart}" --namespace curioed \
  --set workers.llm.enabled=true \
  --set curriculumSeed.enabled=true \
  --set networkPolicy.enabled=true \
  --set priorityClass.enabled=true \
  --set keda.llm.enabled=true \
  --set keda.docling.enabled=true \
  --set web.autoscaling.enabled=true \
  ${test_tag} \
  > "${all_on}"

python3 - "${all_on}" <<'PY'
import re
import sys

text = open(sys.argv[1], encoding="utf-8").read()
docs = [doc for doc in re.split(r"(?m)^---\s*$", text) if doc.strip()]

def kind_of(doc: str) -> str:
    match = re.search(r"(?m)^kind: (\S+)", doc)
    return match.group(1) if match else ""

def name_of(doc: str) -> str:
    match = re.search(r"(?m)^  name: (\S+)", doc)
    return match.group(1) if match else ""

kinds = {kind_of(doc) for doc in docs}
for required in ("NetworkPolicy", "PriorityClass", "ScaledObject", "TriggerAuthentication"):
    if required not in kinds:
        raise SystemExit(f"all-on render is missing {required}")

llm = next(doc for doc in docs if kind_of(doc) == "ScaledObject" and name_of(doc).endswith("-worker-llm"))
docling = next(doc for doc in docs if kind_of(doc) == "ScaledObject" and "docling" in name_of(doc))
for snippet in ("generation_jobs", "feedback_summary_jobs", 'targetQueryValue: "1"', "minReplicaCount: 1", "maxReplicaCount: 4", "cooldownPeriod: 600"):
    if snippet not in llm:
        raise SystemExit(f"llm ScaledObject missing {snippet}")
if "curriculum_generation_jobs" in llm:
    raise SystemExit("curriculum_generation_jobs must stay out of the llm query")
for snippet in ("ingest_jobs", "locked_until", "maxReplicaCount: 1"):
    if snippet not in docling:
        raise SystemExit(f"docling ScaledObject missing {snippet}")
if "kind: HorizontalPodAutoscaler" not in text or not any(name_of(doc).endswith("-web") and kind_of(doc) == "HorizontalPodAutoscaler" for doc in docs):
    raise SystemExit("web.autoscaling.enabled should render the web HPA")
api_hpa = [doc for doc in docs if kind_of(doc) == "HorizontalPodAutoscaler" and name_of(doc).endswith("-api")]
if not api_hpa:
    raise SystemExit("all-on without the API Prometheus trigger should still render the API HPA")

def mem_mi(raw: str) -> float:
    value = raw.strip().strip("'\"")
    if value.endswith("Gi"):
        return float(value[:-2]) * 1024
    if value.endswith("Mi"):
        return float(value[:-2])
    raise SystemExit(f"unsupported memory quantity {raw}")

def resources_of(doc: str) -> list[tuple[float, float]]:
    rows = []
    patterns = (
        r"(?m)^ {8,12}resources:\n((?:^ {10,16}\S.*\n)*)",
        r"(?m)^  resources:\n((?:^ {4,8}\S.*\n)*)",
    )
    for pattern in patterns:
        for block in re.finditer(pattern, doc):
            body = block.group(0)
            request = re.search(r"requests:\n(?:.*\n)*?[ \t]*memory: (\S+)", body)
            limit = re.search(r"limits:\n(?:.*\n)*?[ \t]*memory: (\S+)", body)
            if request and limit:
                rows.append((mem_mi(request.group(1)), mem_mi(limit.group(1))))
    return rows

quota = next(doc for doc in docs if kind_of(doc) == "ResourceQuota")
requests_cap = mem_mi(re.search(r"requests\.memory: (\S+)", quota).group(1))
limits_cap = mem_mi(re.search(r"limits\.memory: (\S+)", quota).group(1))

steady_req = 0.0
steady_lim = 0.0
peak_req = 0.0
peak_lim = 0.0
for doc in docs:
    kind = kind_of(doc)
    if kind not in ("Deployment", "Job", "Cluster"):
        continue
    rows = resources_of(doc)
    if not rows:
        raise SystemExit(f"{kind} {name_of(doc)} is missing container resources")
    if kind == "Job":
        copies = 1
        peak_copies = 1
    elif kind == "Cluster":
        copies = int(re.search(r"(?m)^  instances: (\d+)", doc).group(1))
        peak_copies = copies
    else:
        copies = int(re.search(r"(?m)^  replicas: (\d+)", doc).group(1))
        peak_copies = copies
        if name_of(doc).endswith("-api"):
            max_replicas = int(re.search(r"maxReplicas: (\d+)", text).group(1))
            peak_copies = max_replicas + 1
    per_req = sum(item[0] for item in rows)
    per_lim = sum(item[1] for item in rows)
    steady_req += per_req * copies
    steady_lim += per_lim * copies
    peak_req += per_req * peak_copies
    peak_lim += per_lim * peak_copies

print(
    f"quota steady requests {steady_req:.0f}Mi limits {steady_lim:.0f}Mi; "
    f"peak api max+surge requests {peak_req:.0f}Mi limits {peak_lim:.0f}Mi; "
    f"caps requests {requests_cap:.0f}Mi limits {limits_cap:.0f}Mi"
)
if steady_req > requests_cap or steady_lim > limits_cap:
    raise SystemExit("all-on steady render does not fit the ResourceQuota")
if peak_lim <= limits_cap:
    raise SystemExit("expected the api-at-max plus one surge pod to exceed limits.memory; the quota number changed")
print("all-on render fits steady state and reports the peak overage")
PY

if ! cmp -s "${chart}/files/k6-api.js" "${root}/deploy/loadtest/k6-api.js"; then
  echo "chart k6 script drifted from deploy/loadtest/k6-api.js" >&2
  exit 1
fi

helm template curioed "${chart}" --namespace curioed -f "${chart}/values-prod.yaml" > "${out}.prod"
python3 - "${out}.prod" <<'PY'
import re, sys
text = open(sys.argv[1], encoding="utf-8").read()
docs = [doc for doc in re.split(r"(?m)^---\s*$", text) if doc.strip()]

def kind_of(doc):
    match = re.search(r"(?m)^kind: (\S+)", doc)
    return match.group(1) if match else ""

def name_of(doc):
    match = re.search(r"(?m)^  name: (\S+)", doc)
    return match.group(1) if match else ""

caddy = next(doc for doc in docs if kind_of(doc) == "Service" and name_of(doc) == "caddy")
if "externalName: traefik.kube-system.svc.cluster.local" not in caddy or "port: 80" not in caddy:
    raise SystemExit("prod caddy alias is wrong")
tunnel = next(doc for doc in docs if kind_of(doc) == "Deployment" and name_of(doc).endswith("-cloudflared"))
if "replicas: 0" not in tunnel:
    raise SystemExit("prod cloudflared must stay at 0 replicas")
cluster = next(doc for doc in docs if kind_of(doc) == "Cluster")
for snippet in ("16.15-standard-trixie", 'max_connections: "300"', "text_to_sql_reader", "owner: education"):
    if snippet not in cluster:
        raise SystemExit(f"prod cluster missing {snippet}")
if "llm-mock" in text or "seed_demo.py" in text:
    raise SystemExit("prod rendered the load-test mock or demo seed")
if "curioed-api:6b7553c" not in text or "curioed-web:6b7553c" not in text:
    raise SystemExit("prod images should default to the imported 6b7553c tags")
quota = next(doc for doc in docs if kind_of(doc) == "ResourceQuota")
if "limits.memory: 16Gi" not in quota or "requests.memory: 9Gi" not in quota or "limits.cpu:" not in quota:
    raise SystemExit("prod quota is wrong")
if 'JEV_JUDGE_MODE: "off"' in text:
    raise SystemExit("prod must leave the lesson judge unchanged")

def mem_mi(raw: str) -> float:
    value = raw.strip().strip("'\"")
    if value.endswith("Gi"):
        return float(value[:-2]) * 1024
    if value.endswith("Mi"):
        return float(value[:-2])
    raise SystemExit(f"unsupported memory quantity {raw}")

def resources_of(doc: str) -> list[tuple[float, float]]:
    rows = []
    patterns = (
        r"(?m)^ {8,12}resources:\n((?:^ {10,16}\S.*\n)*)",
        r"(?m)^  resources:\n((?:^ {4,8}\S.*\n)*)",
    )
    for pattern in patterns:
        for block in re.finditer(pattern, doc):
            body = block.group(0)
            request = re.search(r"requests:\n(?:.*\n)*?[ \t]*memory: (\S+)", body)
            limit = re.search(r"limits:\n(?:.*\n)*?[ \t]*memory: (\S+)", body)
            if request and limit:
                rows.append((mem_mi(request.group(1)), mem_mi(limit.group(1))))
    return rows

requests_cap = mem_mi(re.search(r"requests\.memory: (\S+)", quota).group(1))
limits_cap = mem_mi(re.search(r"limits\.memory: (\S+)", quota).group(1))
api_hpa = next(doc for doc in docs if kind_of(doc) == "HorizontalPodAutoscaler" and name_of(doc).endswith("-api"))
max_replicas = int(re.search(r"maxReplicas: (\d+)", api_hpa).group(1))
peak_req = 0.0
peak_lim = 0.0
for doc in docs:
    kind = kind_of(doc)
    if kind not in ("Deployment", "Job", "Cluster"):
        continue
    rows = resources_of(doc)
    if kind == "Job":
        copies = 1
    elif kind == "Cluster":
        copies = int(re.search(r"(?m)^  instances: (\d+)", doc).group(1))
    else:
        copies = int(re.search(r"(?m)^  replicas: (\d+)", doc).group(1))
        if name_of(doc).endswith("-api"):
            copies = max_replicas + 1
    peak_req += sum(item[0] for item in rows) * copies
    peak_lim += sum(item[1] for item in rows) * copies
if max_replicas != 6:
    raise SystemExit(f"prod api HPA max must stay 6, got {max_replicas}")
if peak_req > requests_cap or peak_lim > limits_cap:
    raise SystemExit(
        f"prod api max+surge does not fit: requests {peak_req:.0f}Mi/{requests_cap:.0f}Mi "
        f"limits {peak_lim:.0f}Mi/{limits_cap:.0f}Mi"
    )
print(f"prod peak fits: requests {peak_req:.0f}Mi limits {peak_lim:.0f}Mi")
PY

helm template curioed "${chart}" --namespace curioed-loadtest -f "${chart}/values-loadtest.yaml" > "${out}.load"
python3 - "${out}.load" <<'PY'
import re, sys
text = open(sys.argv[1], encoding="utf-8").read()
docs = [doc for doc in re.split(r"(?m)^---\s*$", text) if doc.strip()]

def kind_of(doc):
    match = re.search(r"(?m)^kind: (\S+)", doc)
    return match.group(1) if match else ""

def name_of(doc):
    match = re.search(r"(?m)^  name: (\S+)", doc)
    return match.group(1) if match else ""

names = {(kind_of(doc), name_of(doc)) for doc in docs}
if any(name.endswith("-worker-docling") for _, name in names):
    raise SystemExit("loadtest rendered docling")
if any(name.endswith("-worker-llm") for _, name in names):
    raise SystemExit("loadtest rendered the llm worker")
if not any(name.endswith("-llm-mock") for _, name in names):
    raise SystemExit("loadtest did not render the mock")
if "seed_demo.py" not in text or "http://curioed-llm-mock:8080/v1" not in text:
    raise SystemExit("loadtest mock wiring is missing")
if "JEV_DECISIONS_URL" not in text:
    raise SystemExit("loadtest did not set JEV_DECISIONS_URL")
if "curioed-api:6b7553c" not in text or "curioed-web:6b7553c" not in text:
    raise SystemExit("loadtest images should default to the imported 6b7553c tags")
quota = next(doc for doc in docs if kind_of(doc) == "ResourceQuota")
if "limits.memory: 12Gi" not in quota or "requests.memory: 6Gi" not in quota:
    raise SystemExit("loadtest quota is wrong")
if 'JEV_JUDGE_MODE: "off"' not in text:
    raise SystemExit("loadtest must set JEV_JUDGE_MODE off")

def mem_mi(raw: str) -> float:
    value = raw.strip().strip("'\"")
    if value.endswith("Gi"):
        return float(value[:-2]) * 1024
    if value.endswith("Mi"):
        return float(value[:-2])
    raise SystemExit(f"unsupported memory quantity {raw}")

def resources_of(doc: str) -> list[tuple[float, float]]:
    rows = []
    patterns = (
        r"(?m)^ {8,12}resources:\n((?:^ {10,16}\S.*\n)*)",
        r"(?m)^  resources:\n((?:^ {4,8}\S.*\n)*)",
    )
    for pattern in patterns:
        for block in re.finditer(pattern, doc):
            body = block.group(0)
            request = re.search(r"requests:\n(?:.*\n)*?[ \t]*memory: (\S+)", body)
            limit = re.search(r"limits:\n(?:.*\n)*?[ \t]*memory: (\S+)", body)
            if request and limit:
                rows.append((mem_mi(request.group(1)), mem_mi(limit.group(1))))
    return rows

requests_cap = mem_mi(re.search(r"requests\.memory: (\S+)", quota).group(1))
limits_cap = mem_mi(re.search(r"limits\.memory: (\S+)", quota).group(1))
api_hpa = next(doc for doc in docs if kind_of(doc) == "HorizontalPodAutoscaler" and name_of(doc).endswith("-api"))
max_replicas = int(re.search(r"maxReplicas: (\d+)", api_hpa).group(1))
peak_req = 0.0
peak_lim = 0.0
for doc in docs:
    kind = kind_of(doc)
    if kind not in ("Deployment", "Job", "Cluster"):
        continue
    rows = resources_of(doc)
    if kind == "Job":
        copies = 1
    elif kind == "Cluster":
        copies = int(re.search(r"(?m)^  instances: (\d+)", doc).group(1))
    else:
        copies = int(re.search(r"(?m)^  replicas: (\d+)", doc).group(1))
        if name_of(doc).endswith("-api"):
            copies = max_replicas + 1
    peak_req += sum(item[0] for item in rows) * copies
    peak_lim += sum(item[1] for item in rows) * copies
if max_replicas != 6:
    raise SystemExit(f"loadtest api HPA max must stay 6, got {max_replicas}")
if peak_req > requests_cap or peak_lim > limits_cap:
    raise SystemExit(
        f"loadtest api max+surge does not fit: requests {peak_req:.0f}Mi/{requests_cap:.0f}Mi "
        f"limits {peak_lim:.0f}Mi/{limits_cap:.0f}Mi"
    )
print(f"loadtest peak fits: requests {peak_req:.0f}Mi limits {peak_lim:.0f}Mi")
k6 = next(doc for doc in docs if kind_of(doc) == "Job" and name_of(doc).endswith("-k6"))
if "runAsNonRoot: true" not in k6 or "runAsUser: 12345" not in k6:
    raise SystemExit("k6 image user is uid 12345")
mock = next(doc for doc in docs if kind_of(doc) == "Deployment" and name_of(doc).endswith("-llm-mock"))
if "runAsNonRoot: false" not in mock or "runAsUser:" in mock:
    raise SystemExit("llm-mock python image is root")
demo = next(doc for doc in docs if kind_of(doc) == "Job" and name_of(doc).endswith("-demo-data"))
if "runAsNonRoot: false" not in demo:
    raise SystemExit("demo-data uses the root api image")
print("loadtest values rendered")
PY

echo "helm lint and template checks passed"
