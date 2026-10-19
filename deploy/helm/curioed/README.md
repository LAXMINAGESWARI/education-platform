# CurioEd Helm chart

Manifests for a k3s install. Nothing in this chart, and nothing in `.github/workflows/images.yml`, deploys CurioEd. Docker Compose production (`deploy/docker-compose.prod.yml`, `deploy/selfhost.sh`, `.github/workflows/deploy.yml`) is unchanged.

The CloudNativePG operator and k3s Traefik are cluster prerequisites. This chart does not install them.

## What it runs

| Piece | Object | Notes |
| --- | --- | --- |
| api | Deployment + Service + HPA | CPU target 65%, min 2, max 4. The Deployment starts at 2 replicas. Probes: `/health/live`, `/health/ready` on port 8000. |
| web | Deployment + Service | nginx image, 2 replicas. |
| worker-docling | Deployment | 1 replica, memory limit 6Gi, Recreate. This is the only worker by default. |
| worker-llm | Deployment | Off unless `workers.llm.enabled=true`. Same process as docling until the app can select queues. |
| migrate | pre-install, pre-upgrade Job | Waits for Postgres, then `deploy/migrate.sh` (pg_dump, then Alembic, then promotion). |
| edge | Traefik IngressRoute | `/api` body limit 32MiB, 300s timeouts, SSE flush. `/sw.js` cache headers. |
| cloudflared | Deployment | 2 replicas, pinned tag, token from a Secret. |
| Postgres | CNPG Cluster | `16.15-standard-bookworm` (pgvector is in the standard image). `CREATE EXTENSION vector` at bootstrap. |

Uploads, the Hugging Face cache, and the inductor cache are ReadWriteOnce volumes. The API and the Docling worker share them and must land on the same node. The LLM worker, when enabled, joins that set.

A later `helm upgrade` writes the API replica count back to the HPA minimum. The HPA scales it up again from there.

## Secrets

The chart only stores Secret **names**. Create these yourself:

| Secret | Type | Keys |
| --- | --- | --- |
| `secrets.app.existingSecret` (`curioed-app`) | Opaque | `JWT_SECRET`, `TEXT_TO_SQL_DB_PASSWORD`, `OPENROUTER_API_KEY` (empty string is allowed; the key must exist) |
| `secrets.database.existingSecret` (`curioed-postgres-app`) | `kubernetes.io/basic-auth` | `username` = `education`, `password` URL-safe (letters, digits, hyphen) |
| `secrets.textToSql.existingSecret` (`curioed-postgres-reader`) | `kubernetes.io/basic-auth` | `username` = `text_to_sql_reader`. `password` must be set by hand to the same value as `TEXT_TO_SQL_DB_PASSWORD` in the app Secret. The chart does not copy one Secret into the other. |
| `secrets.tunnel.existingSecret` (`curioed-cloudflared`) | Opaque | `TUNNEL_TOKEN` |

In the Cloudflare dashboard, send the public hostname to `http://traefik.kube-system.svc.cluster.local:80`. Set `api.image.tag` and `web.image.tag` to the commit SHA published by the Images workflow. There is no `latest` tag.

## First install

Helm runs `pre-install` hooks before it creates the Cluster or the backup PVC. A migrate hook in this same release cannot see the database on a first install, so install in two steps:

```sh
helm install curioed deploy/helm/curioed -n curioed --create-namespace --set migrate.enabled=false
# wait until the CloudNativePG cluster is ready
helm upgrade curioed deploy/helm/curioed -n curioed
```

The upgrade runs the `pre-upgrade` hook: `pg_dump` to the backups PVC, then Alembic, while the previous pods keep serving. `backoffLimit` is 0 and the deadline is 1800s.

Bootstrap `postInitApplicationSQL` runs as the Postgres superuser in the application database and creates the `vector` extension. The `education` role stays a normal owner. Alembic's `CREATE EXTENSION IF NOT EXISTS vector` then finds the extension and does nothing.

## Text-to-SQL password

CNPG creates `text_to_sql_reader` from `secrets.textToSql.existingSecret`. Alembic skips `CREATE ROLE` when that role already exists, and it does not change the password. The API reads `TEXT_TO_SQL_DB_PASSWORD` from the app Secret. Set those two passwords to the same value yourself before the first upgrade. If they differ, text-to-SQL cannot log in.

## Backups

The only backup this chart takes is the pre-migration `pg_dump` from `deploy/migrate.sh`, written to the backups PVC. That dump is on the same disk as the database. Off-site backups (CloudNativePG Barman to object storage) are not in this chart. They are planned for phase 1. `cnpg.backup.enabled=true` fails the render so it cannot be mistaken for a working off-site backup.

## Check

```sh
deploy/helm/curioed/render-check.sh
```
