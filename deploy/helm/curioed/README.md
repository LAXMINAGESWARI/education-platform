# CurioEd Helm chart

Manifests for k3s. Rendering this chart does not deploy it. Compose production files are unchanged.

CloudNativePG and k3s Traefik are cluster prerequisites. This chart does not install them.

Image tags are `api.image.tag` and `web.image.tag`. Helm `required` rejects an empty tag. Never `latest`. Chart defaults leave both tags empty, so lint, unittests, and the default CI render pass `--set api.image.tag=ci --set web.image.tag=ci`. `ci` is not a published image. Prod and load-test values default to the images already imported on the PC: `curioed-api:6b7553c` and `curioed-web:6b7553c`, `pullPolicy: IfNotPresent`. `k8s-autodeploy.sh` overrides the prod tags with `--set`.

## Profiles

| File | Namespace | What it is |
| --- | --- | --- |
| `values.yaml` | chosen at install | Hardened defaults. API HPA 1–4 at 65% CPU. Web fixed at 2 replicas, HPA off. Docling on. LLM worker off. |
| `values-prod.yaml` | `curioed` | Compose replacement. API and web HPA 1–6 at 60%. Docling and the Compose-shaped LLM worker on. Real OpenRouter. Tunnel replicas 0 until cutover. |
| `values-loadtest.yaml` | `curioed-loadtest` | Same HPA shape, no workers, llm-mock, demo-data Job, k6 Job. |

## Pod security

The chart does not create a Namespace. Label the namespace before install:

```sh
kubectl label namespace curioed \
  pod-security.kubernetes.io/enforce=baseline \
  pod-security.kubernetes.io/warn=restricted \
  pod-security.kubernetes.io/audit=restricted \
  --overwrite
```

`enforce=baseline` is required. Restricted enforce does not fit the current images or CloudNativePG.

`runAsNonRoot` and `runAsUser` are set per workload.

| Workload | runAsNonRoot | runAsUser | Why |
| --- | --- | --- | --- |
| api, docling, llm worker, migrate, curriculum seed, demo-data | false | unset | `deploy/Dockerfile.api` has no `USER`. `python:3.12-slim-bookworm` image config User is empty. **TODO:** add a `USER` line in `Dockerfile.api` (aushak's OK) before turning this on. |
| web | false | unset | `nginx:1.27-alpine` has no `USER` and listens on port 80. `NET_BIND_SERVICE` is added back. |
| llm-mock | false | unset | `python:3.12.7-slim-bookworm` image config User is empty. |
| cloudflared | true | 65532 | `cloudflare/cloudflared:2026.10.1` image config User is `65532:65532`. |
| k6 | true | 12345 | `grafana/k6:0.57.0` adds uid 12345 and sets `USER k6`. |
| CNPG | not set by this chart | 26 via the operator | Image config User is 26. The operator sets `runAsNonRoot` and `runAsUser`. The chart does not override that. |

Other defaults: `allowPrivilegeEscalation: false`, capabilities `drop: [ALL]`, `seccompProfile: RuntimeDefault`. `readOnlyRootFilesystem` stays false. Turn it on only for a container that has a writable emptyDir for `/tmp` and, for the API, the Hugging Face cache. Nginx also writes under `/var`.

The curriculum seed Job is off by default. When enabled it requests 100m CPU and 256Mi, with limits of 1 CPU and 1Gi, same as the migrate hook. The LimitRange still covers any pod that forgets resources.

The CNPG Cluster and every PVC (uploads, hf-cache, inductor, backups) carry `helm.sh/resource-policy: keep`.

## API

CPU HPA is on by default (65%, min 1, max 4, scale-up window 0, scale-down 300). Raise `minReplicas` to 2 once the database is warm. Prod and load-test values use 1–6 at 60% and a 1 CPU / 1Gi limit. Requests stay 250m / 512Mi.

`uvicorn --limit-concurrency` defaults to 200. SSE streams count toward that cap.

The image entrypoint execs these args and skips its own JWT-length check and directory create. `JWT_SECRET` is still injected and checked by the application.

Probes: startup and liveness `GET /health/live` (startup about 60s: period 5, failure threshold 12). Readiness `GET /health/ready` with `timeoutSeconds: 2`. `/health/live` does not touch Postgres. MiniLM loads on first embed, not at process start.

A PodDisruptionBudget with `maxUnavailable: 1` is rendered only when the replica floor is at least 2. The default API floor is 1, so there is no API PDB. Web and cloudflared have one at their default of 2 replicas. Prod web HPA min is 1, so that profile does not render a web PDB.

## Workers

No liveness and no readiness. A database ping would kill a parse, and neither worker has a Service. A heartbeat file is still a TODO; this chart does not change application code to add one.

`terminationGracePeriodSeconds` is 600 for docling and 300 for the LLM worker. Both are above `WORKER_SHUTDOWN_GRACE_SECONDS` (25) plus 5 seconds. Once Coder's worker image is deployed, the process releases its lease on SIGTERM. Until that image is what the cluster runs, Python is PID 1 and ignores SIGTERM.

Compose (`deploy/docker-compose.prod.yml` service `worker`) is one container: image `education-platform-api`, command `python -m education_platform.workers`, the same env and uploads/HF/inductor volumes as the API, `stop_grace_period: 30s`, and a `db_ping.py` healthcheck. Prod `workers.llm` is that process (the ping is not copied). Prod also runs `worker-docling` at 1 replica, 3Gi request and limit, 2 CPU request and limit. Both still drain every queue until `WORKER_QUEUES` exists. KEDA stays off for that reason.

Docling memory: keep 3Gi if the largest PDF's peak RSS is under 2.5Gi. Otherwise use a 3Gi request and a 5Gi limit.

## Database pool

Set on the API and both workers. Text-to-SQL pool env is API only. Workers do not open that pool.

| Variable | App default | Chart value |
| --- | --- | --- |
| `DB_POOL_SIZE` | 5 | API 5, workers 2 |
| `DB_MAX_OVERFLOW` | 5 | API 5, workers 2 |
| `DB_POOL_TIMEOUT_SECONDS` | 30 | 30 |
| `DB_POOL_RECYCLE_SECONDS` | 1800 | 1800 |
| `TEXT_TO_SQL_DB_POOL_SIZE` | 5 | API 2 |
| `TEXT_TO_SQL_DB_MAX_OVERFLOW` | 5 | API 3 |
| `WORKER_SHUTDOWN_GRACE_SECONDS` | 25 | workers 25 |

These names come from Coder's PR #200 (head `1989159`). The running app ignores them until that PR merges. Worst case is about 30 connections per API pod once every engine is counted, so 180 at HPA max 6, plus the workers and one SSE `LISTEN` connection per stream. That fits under `max_connections` 300. There is no PgBouncer, because `LISTEN` needs a direct connection. Worker `terminationGracePeriodSeconds` stays at least 30: docling 600, llm 300.

`config.extraEnv` stays an empty map for later names. `config.extraSecretEnv` is a list of `secretKeyRef` entries. No secret values belong in git.

## Platform environment

Non-secret `AI_GATEWAY_*`, `JEV_*`, and `GUARD_*` values render into the platform ConfigMap from `config.platformEnv`. Names and defaults match `backend/src/education_platform/core/config.py`. Empty `JEV_JUDGE_MODE` and `JEV_MODE` match the unset default: the judge treats a blank string as unset.

`AI_GATEWAY_FEATURES`, `AI_GATEWAY_POLICY_CHAT`, `AI_GATEWAY_TEXT_TO_SQL`, `AI_GATEWAY_GENERATION`, `AI_GATEWAY_FEEDBACK`, `AI_GATEWAY_REVIEW_CHAT`, `AI_GATEWAY_AUTHORING`, `AI_GATEWAY_TIMEOUT_CHAT_ANSWER_SECONDS`, `AI_GATEWAY_TIMEOUT_SQL_GENERATE_SECONDS`, `AI_GATEWAY_TIMEOUT_GUARD_SECONDS`, `AI_GATEWAY_TIMEOUT_GENERATION_SECONDS`, `AI_GATEWAY_TIMEOUT_FEEDBACK_SECONDS`, `AI_GATEWAY_MAX_RETRIES`, `AI_GATEWAY_RETRY_BASE_SECONDS`, `AI_GATEWAY_RETRY_MAX_SECONDS`, `AI_GATEWAY_CIRCUIT_FAILURE_THRESHOLD`, `AI_GATEWAY_CIRCUIT_COOLDOWN_SECONDS`, `AI_GATEWAY_CONCURRENCY_INTERACTIVE`, `AI_GATEWAY_CONCURRENCY_GUARD`, `AI_GATEWAY_CONCURRENCY_GENERATION`, `AI_GATEWAY_CONCURRENCY_BATCH`, `JEV_DECISIONS_URL`, `JEV_GUARD_TIMEOUT_SECONDS`, `JEV_MODEL`, `JEV_TIMEOUT_SECONDS`, `JEV_MAX_RETRIES`, `JEV_JUDGE_MODE`, `JEV_MODE`, `JEV_NOUL_ACCEPT`, `JEV_NOUL_REJECT`, `JEV_CONFIDENCE_ACCEPT`, `JEV_CONFIDENCE_REJECT`, `JEV_COVERAGE_ACCEPT_SCORE`, `JEV_COVERAGE_REJECT_SCORE`, `GUARD_JEV_BLOCK_THRESHOLD`, `GUARD_JEV_MODEL`, `GUARD_AUDIT_STORE_PREVIEW`.

`OPENROUTER_API_KEY` stays a `secretKeyRef`.

## Secrets

The chart only stores names. Placeholders below are not real values.

```sh
kubectl create secret generic curioed-app -n curioed \
  --from-literal=JWT_SECRET='REPLACE_ME' \
  --from-literal=TEXT_TO_SQL_DB_PASSWORD='REPLACE_ME' \
  --from-literal=OPENROUTER_API_KEY='REPLACE_ME'

kubectl create secret generic curioed-postgres-app -n curioed \
  --type=kubernetes.io/basic-auth \
  --from-literal=username='REPLACE_ME' \
  --from-literal=password='REPLACE_ME'

kubectl create secret generic curioed-postgres-reader -n curioed \
  --type=kubernetes.io/basic-auth \
  --from-literal=username='REPLACE_ME' \
  --from-literal=password='REPLACE_ME'

kubectl create secret generic curioed-cloudflared -n curioed \
  --from-literal=TUNNEL_TOKEN='REPLACE_ME'
```

`curioed-postgres-reader` password must equal `TEXT_TO_SQL_DB_PASSWORD`. The chart does not copy one Secret into the other. `deploy/k8s/secrets-from-env.sh` creates the same four Secrets from `deploy/.env` without putting values on the process command line.

## Postgres

Default parameters fit a 2Gi limit: `shared_buffers` 512MB, `effective_cache_size` 1GB, `max_connections` 150. Prod raises `max_connections` to 300 and uses `ghcr.io/cloudnative-pg/postgresql:16.15-standard-trixie`. Bootstrap owner is `education`. The managed role is `text_to_sql_reader`.

`cnpg.backup.enabled` defaults to false. Turning it on fails the render. Off-site Barman is not wired. The migrate hook's `pg_dump` is the backup that runs before Alembic.

## Migrate and first install

The migrate Job is a `pre-install,pre-upgrade` hook. On a first install the Cluster does not exist yet, so install in two steps:

```sh
helm install curioed deploy/helm/curioed -n curioed --create-namespace \
  -f deploy/helm/curioed/values-prod.yaml \
  --set api.image.tag=REPLACE_SHA \
  --set web.image.tag=REPLACE_SHA \
  --set migrate.enabled=false
# wait until the CloudNativePG cluster is ready
helm upgrade curioed deploy/helm/curioed -n curioed \
  -f deploy/helm/curioed/values-prod.yaml \
  --set api.image.tag=REPLACE_SHA \
  --set web.image.tag=REPLACE_SHA
```

The load-test profile turns that hook off. Its demo-data Job waits for Postgres, runs `alembic upgrade head`, the curriculum seed, then `DEMO_SEED=1 python /app/backend/scripts/seed_demo.py`.

## KEDA

Off by default, and it must stay off until `WORKER_QUEUES` exists. Today every worker drains every queue.

The LLM query counts `generation_jobs` and `feedback_summary_jobs` in `queued` or `running`. Status values are the varchar values from `db/types.py` `str_enum`, not native enums. `curriculum_generation_jobs` is excluded because `run_forever()` never passes a curriculum runner (`workers/runner.py`). The docling query matches `workers/lease.py`: queued, or running with a null or expired `locked_until`.

`targetQueryValue` is `"1"`. LLM min 1, max 4, cooldown 600. Docling max 1. TriggerAuthentication reads the existing `curioed-postgres-app` Secret. A dedicated read-only role needs an Alembic `GRANT`, which is out of scope. No migration is added here.

When the API Prometheus trigger is enabled, the CPU HPA is not rendered.

## Caddy alias

The existing tunnel route is `curioed.online` to `http://caddy:80`. Prod renders a Service named exactly `caddy`, port 80, `type: ExternalName`, `externalName: traefik.kube-system.svc.cluster.local`.

ExternalName is a CNAME, not a proxy. The client connects to port 80 on the address that name resolves to. k3s Traefik's Service `traefik` in `kube-system` exposes port 80 named `web`, which is the HTTP entrypoint the IngressRoute uses. That is why the alias port is 80 and not a translated target. IngressRoutes match `Host(curioed.online)` from `domain`.

## LAN NodePort

A chart Service cannot select Traefik pods in `kube-system`. Selectors do not cross namespaces. `deploy/k8s/traefik-nodeport.yaml` is a k3s `HelmChartConfig` that sets `ports.web.nodePort: 30080` on the Traefik chart. It is not applied by this Helm release. Copy it to `/var/lib/rancher/k3s/server/manifests/traefik-config.yaml` only when you want `http://<pc-lan-ip>:30080`. Send `Host: curioed.online` if you are hitting the IngressRoute.

## Tunnel switch and rollback

Compose and k8s share one tunnel token. If both cloudflared processes connect, Cloudflare splits requests across two databases.

Prod keeps `cloudflared.replicas` at 0. The Deployment is a rolling update (`maxUnavailable: 0`, `maxSurge: 1`) with `--metrics 0.0.0.0:2000`, a `/ready` probe on that port, and `terminationGracePeriodSeconds: 40` (cloudflared's grace default is 30s). The image tag is `2026.10.1`. A PDB of `maxUnavailable: 1` appears only when replicas are at least 2. With network policy enabled, kubelet probes are allowed to port 2000 and nothing else is opened; cloudflared egress to Traefik in `kube-system` is already allowed. No extra Traefik middleware is added for SSE.

`deploy/k8s/curioed-tunnel-guard.sh` reads the k8s Deployment. If desired or ready replicas are above 0, it `docker stop`s running containers named `education-platform-prod-cloudflared*`. It does not remove them and it does not start or scale anything. If kubectl fails, it exits non-zero and does not touch Docker. `--dry-run` prints the plan. The sample oneshot unit is `curioed-tunnel-guard.service` (`After=` and `Wants=` `docker.service` and `k3s.service`). `curioed-tunnel-guard.timer` is optional.

Switch:

1. Keep k8s replicas at 0 and confirm `docker ps` shows the Compose connector.
2. Scale k8s to 2: `helm upgrade ... --set cloudflared.replicas=2`.
3. Run the guard (or let the timer run it). It stops the Compose containers.
4. Confirm Compose has 0 running connectors and k8s ready replicas are 2.

Rollback:

1. Scale k8s cloudflared back to 0 and wait until ready is 0.
2. `docker start` the Compose container yourself. The guard will not start it.
3. Confirm one Compose connector and zero k8s replicas.

## Load test

`deploy/loadtest/k6-api.js` ramps 2 minutes each at 10, 50, 100, 200, 300, and 400 requests per second, then idles 6 minutes. Thresholds `p95 < 800ms` and error rate under 1% are reported, not abort conditions.

Mix: 60% authenticated reads, 25% `/health/ready` plus the web index and `/favicon.png`, 15% `POST /api/v1/chats/{id}/messages`. `setup()` logs in demo users. The password is `demo1234`.

Laptop, after the NodePort manifest:

```sh
API_URL=http://192.0.2.10:30080 WEB_URL=http://192.0.2.10:30080 HOST=curioed.online \
  k6 run deploy/loadtest/k6-api.js
```

In cluster, `values-loadtest.yaml` runs the same script from a ConfigMap in a Job capped at 2 CPU and 1Gi, image `grafana/k6:0.57.0`. `API_URL` and `WEB_URL` are the in-cluster Service URLs.

`deploy/loadtest/collect.sh` appends a CSV every 5 seconds: HPA current and desired replicas, `kubectl top`, MemAvailable from `/proc/meminfo`, `pg_stat_activity` counts by state, and p50/p95/p99 from the k6 summary when the Job has printed one. Below 3 GiB MemAvailable, or if `PROD_HEALTH_URL` fails, it deletes the k6 Job and exits non-zero.

## Images on the PC

Build from a separate clone, never `/home/aushakanuj/Documents/work/education-platform`.

```sh
docker build -f deploy/Dockerfile.api -t curioed-api:demo .
docker build -f deploy/Dockerfile.web -t curioed-web:demo --build-arg VITE_API_BASE_URL= .
docker save curioed-api:demo curioed-web:demo | sudo k3s ctr images import -
```

`deploy/k8s/k8s-autodeploy.sh` refuses that working-tree path, takes a `flock`, fetches `origin/main`, and exits when the SHA matches the state file. Otherwise it builds, imports, and runs `helm upgrade --install` with `--wait --timeout 15m` and no `--atomic`.

## Restore checks

`deploy/k8s/restore.sh --dump FILE --counts FILE` runs `pg_restore -Fc` into the CNPG primary, then fails the process if any of these mismatch:

- extension `vector` exists
- roles `education` and `text_to_sql_reader` exist
- `text_to_sql_reader` can `SELECT` `institutions` and `user_roles`
- it cannot `SELECT` `users.password_hash` or `question_answer_keys`
- `alembic_version` equals `b3c4d5e6f7a8` (override with `--revision`)
- each `table,count` row in the counts file matches `count(*)`

## Quota

`limits.cpu` is omitted unless set. The chart default is memory requests 8Gi and limits 14Gi. Steady state with the API at its minimum fits. The API at max 4 plus one surge pod does not fit that 14Gi limit, and the chart default is not raised to hide it.

Prod sets cpu 8, requests 9Gi, limits 16Gi. Load-test sets cpu 8, requests 6Gi, limits 12Gi. Those limits overcommit on purpose. `collect.sh` aborting below 3Gi `MemAvailable` is the real guard, and prod and load-test never run at the same time because the load-test namespace is deleted before prod comes up. With the API HPA max still 6 and `maxSurge` 1, both profiles fit that peak. LimitRange defaults keep a container that forgot resources from being rejected. The numbers printed by `render-check.sh` are the ones to trust after a values change.

Load-test sets `JEV_JUDGE_MODE=off`. Prod leaves that value unset, which the app treats as shadow. The hardcoded decisions URL in `judge/client.py` is a Coder follow-up.

## Check

```sh
deploy/helm/curioed/render-check.sh
```
