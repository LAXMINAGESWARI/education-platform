# Deploy on one Oracle Cloud ARM VM

The demo runs on a single Ubuntu VM (`VM.Standard.A1.Flex`, 4 OCPU, 24 GB). One
Compose project starts Postgres (pgvector), a one-shot migration, the API, the
Postgres worker, the static frontend, and Caddy. Redis is not part of this
stack: ingest and generation jobs are rows in Postgres, claimed with
`FOR UPDATE SKIP LOCKED`. There is no separate scheduler. The worker process is
`python -m education_platform.workers`.

Local development still uses the root `docker-compose.yml` Postgres container.
Nothing in that file changes.

## What is public

| URL | What it is |
| --- | --- |
| `https://DOMAIN/` | React app (nginx, behind Caddy) |
| `https://DOMAIN/api/v1/...` | FastAPI |
| `https://DOMAIN/health` | API health |
| `https://DOMAIN/docs` | OpenAPI UI |

`DOMAIN` is one hostname. Point a GoDaddy **A** record at the VM. The frontend
is built with an empty `VITE_API_BASE_URL`, so the browser calls `/api/v1` on
that same host. `CORS_ORIGINS` must include `https://DOMAIN`.

Only Caddy publishes ports **80** and **443**. Postgres, the API, the worker,
and nginx are on internal Docker networks.

## What you fill in by hand

Copy `deploy/.env.prod.example` to `deploy/.env`. Replace every `replace-with`
value and the example hostname. Passwords must be URL-safe (letters, digits,
hyphen).

| Variable | You provide |
| --- | --- |
| `DOMAIN` | The hostname, such as `school.example.com` |
| `ACME_EMAIL` | Your email, used by Let's Encrypt |
| `CORS_ORIGINS` | JSON list containing `https://DOMAIN` |
| `POSTGRES_PASSWORD` | Database password |
| `JWT_SECRET` | At least 32 random characters |
| `TEXT_TO_SQL_DB_PASSWORD` | Password for the read-only SQL role. Alembic reads it while migrating |
| `OPENROUTER_API_KEY` | Optional. Leave empty for stub policy summaries |
| `SEED_SYNTHETIC` | `true` the first time (seeds once). `always` rebuilds that school |

`DATABASE_URL` is not typed by hand. Compose sets it to
`postgresql+asyncpg://POSTGRES_USER:POSTGRES_PASSWORD@postgres:5432/POSTGRES_DB`.

After a synthetic seed, every account uses the password `demo1234`:

| Email | Role |
| --- | --- |
| `fatima.almansouri@alnoor.school` | Administrator |
| `meera.krishnan@alnoor.school` | Teacher record. The teacher screens are still fixture data |
| `student1@alnoor.school` | First generated student |

With the default school size, the planted declining student is
`student97@alnoor.school` (Aisha Rahman, identifier `S-00097`). The migrate log
prints her identifier. If you change the section or student counts, use the
`S-` number from that log as the email: `student{that number}@alnoor.school`.

`student@demo.school` is created only when `ENVIRONMENT=development`. Leave
`ENVIRONMENT=production` on the VM. The API still loads the markdown curriculum
into **POC Demo School** on startup when that tree is empty. The synthetic
school is a second institution, **Al Noor International**.

`SEED_SYNTHETIC=true` skips the seed when that institution already exists, so
later deploys do not wipe it. Set `SEED_SYNTHETIC=always` for one deploy when
you want a clean demo school, then set it back to `true`. Keep
`SYNTHETIC_SECTIONS_PER_GRADE` at 2 or more. The generator assigns one teacher
to two sections of the same grade.

## 1. Create the VM

In the Oracle Cloud console:

1. **Compute → Instances → Create instance**.
2. Image: **Canonical Ubuntu 24.04** (aarch64).
3. Shape: **VM.Standard.A1.Flex**, **4 OCPU**, **24 GB** memory. If the
   availability domain is out of capacity, try another one. Always Free A1
   capacity is often tight.
4. Networking: assign a **public IPv4**. Prefer a **reserved** public IP so the
   GoDaddy record does not change.
5. Add your SSH public key. The default user is `ubuntu`.
6. Boot volume: **100 GB** or more. The image includes PyTorch CPU and Docling.
   Always Free includes 200 GB of block storage total.
7. Create the instance and note the public IP.

## 2. Open ports 80 and 443

Oracle checks two firewalls. Both must allow HTTP and HTTPS. Do not open 5432.

### Security list (and any network security group)

**Networking → Virtual cloud networks →** your VCN **→ Security Lists →** the
list on the instance subnet **→ Add Ingress Rules**:

| Source | Protocol | Destination port |
| --- | --- | --- |
| `0.0.0.0/0` | TCP | 80 |
| `0.0.0.0/0` | TCP | 443 |

If the subnet also has a network security group on the instance, add the same
two rules there.

### Ubuntu iptables

Oracle's Ubuntu image rejects new connections that are not SSH. SSH in, then:

```bash
sudo iptables -I INPUT -p tcp -m conntrack --ctstate NEW --dport 80 -j ACCEPT
sudo iptables -I INPUT -p tcp -m conntrack --ctstate NEW --dport 443 -j ACCEPT
sudo iptables -L INPUT -n --line-numbers
```

The two ACCEPT rules must appear before the REJECT rule. Save them:

```bash
echo iptables-persistent iptables-persistent/autosave_v4 boolean true | sudo debconf-set-selections
echo iptables-persistent iptables-persistent/autosave_v6 boolean true | sudo debconf-set-selections
sudo DEBIAN_FRONTEND=noninteractive apt-get update
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y iptables-persistent
sudo netfilter-persistent save
```

If `ufw` is active (`sudo ufw status`), also run `sudo ufw allow 80,443/tcp`.

## 3. Install Docker and clone

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker "$USER"
```

Log out and SSH back in so the `docker` group applies. Then:

```bash
sudo mkdir -p /opt/education-platform
sudo chown "$USER:$USER" /opt/education-platform
git clone https://github.com/aushakanuj/education-platform.git /opt/education-platform
cd /opt/education-platform
cp deploy/.env.prod.example deploy/.env
chmod 600 deploy/.env
nano deploy/.env
```

Private repository: create a separate **read-only** deploy key for `git clone`
and `git pull`. That key is not the GitHub Actions key from the next section.

```bash
ssh-keygen -t ed25519 -f ~/.ssh/github_read -N "" -C "vm-read"
cat ~/.ssh/github_read.pub
```

Add that public key under the repo **Settings → Deploy keys** (read-only). Then:

```bash
cat >> ~/.ssh/config <<'EOF'
Host github.com
  IdentityFile ~/.ssh/github_read
  IdentitiesOnly yes
EOF
chmod 600 ~/.ssh/config
git clone git@github.com:aushakanuj/education-platform.git /opt/education-platform
```

## 4. Start the stack

```bash
cd /opt/education-platform
./deploy/deploy.sh
```

The script is safe to run again. It fetches `main`, builds the images, migrates,
recreates the app containers, and prunes unused images. Postgres is not
recreated, so the data volume stays. The first build downloads PyTorch CPU and
Docling and takes a while. Later builds are incremental.

Images are built on the VM, so they are `linux/arm64`. Base images
(`python:3.12-slim-bookworm`, `node:20-bookworm-slim`, `nginx:1.27-alpine`,
`caddy:2-alpine`, `pgvector/pgvector:pg16`, `ghcr.io/astral-sh/uv`) publish
arm64 tags. The API image installs CPU PyTorch (`2.13.0+cpu`) instead of the
lockfile's NVIDIA libraries.

## 5. GoDaddy DNS

In the GoDaddy DNS panel for the domain, add an **A** record:

| Type | Name | Value | TTL |
| --- | --- | --- | --- |
| A | `@` | the VM public IP | 600 |

Use the host you put in `DOMAIN`. If `DOMAIN` is `school.example.com`, the name
is `school`, not `@`. Wait until `dig +short DOMAIN` prints the VM IP.

Caddy then requests a Let's Encrypt certificate over port 80. Until the A
record is visible, HTTPS fails and HTTP does not redirect to a certificate.

Check:

```bash
curl --fail --silent https://DOMAIN/health
curl --fail --silent --output /dev/null --write-out '%{http_code}\n' https://DOMAIN/
```

`/health` returns `{"status":"ok"}`. `/` returns `200` and the SPA. Sign in at
`https://DOMAIN/login` with the accounts above.

## Update and redeploy

On the VM:

```bash
cd /opt/education-platform
./deploy/deploy.sh
```

Or push to `main`. The GitHub Action SSHes in and runs that same script.
Migrations run before the API container is recreated. A failed migration leaves
the previous API container running.

Schema changes are not rolled back automatically. To return to an older git
revision after a bad release, restore a database backup first, then:

```bash
cd /opt/education-platform
git checkout <known-good-sha>
DEPLOY_SKIP_GIT=1 ./deploy/deploy.sh
```

`DEPLOY_SKIP_GIT=1` skips `git pull`. Use it when you already checked out the
commit you want. The next push to `main` checks `main` out again.

## Logs

```bash
cd /opt/education-platform
docker compose --env-file deploy/.env -f deploy/docker-compose.prod.yml logs -f --tail=200 api worker caddy
```

The worker is up when its log contains `Worker started`. It then polls Postgres.
A quiet log after that means the queue is empty, not that the process stopped.

```bash
docker compose --env-file deploy/.env -f deploy/docker-compose.prod.yml ps
```

## Back up the database

```bash
cd /opt/education-platform
docker compose --env-file deploy/.env -f deploy/docker-compose.prod.yml exec -T postgres \
  sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --no-owner --format=custom' \
  > "education-$(date +%F).dump"
```

Copy the dump off the VM. Restore into the database only when you mean to
replace the data:

```bash
docker compose --env-file deploy/.env -f deploy/docker-compose.prod.yml exec -T postgres \
  sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists --no-owner' \
  < education-YYYY-MM-DD.dump
```

Also copy the uploads volume if you need the PDF blobs:

```bash
docker compose --env-file deploy/.env -f deploy/docker-compose.prod.yml exec -T api \
  tar -C /data/uploads -czf - . > uploads.tgz
```

## Auto-redeploy on push

`.github/workflows/deploy.yml` runs on every push to `main` and on
**Run workflow**. It does not run on pull requests. If the secrets are missing,
a push is skipped with a notice (forks stay green). A manual run without the
secrets fails with an error.

### Deploy key that can SSH into the VM

On your laptop, not on the VM:

```bash
ssh-keygen -t ed25519 -f education_deploy -N "" -C "github-actions-deploy"
```

Append `education_deploy.pub` to the VM user's authorized keys:

```bash
ssh ubuntu@PUBLIC_IP 'mkdir -p ~/.ssh && chmod 700 ~/.ssh'
cat education_deploy.pub | ssh ubuntu@PUBLIC_IP 'cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys'
```

Confirm `ssh -i education_deploy ubuntu@PUBLIC_IP` opens a shell, then delete
the private key from the laptop after it is stored in GitHub. Do not commit it.

### GitHub secrets

Repo **Settings → Secrets and variables → Actions**:

| Secret | Value |
| --- | --- |
| `DEPLOY_HOST` | VM public IP or DNS name |
| `DEPLOY_USER` | `ubuntu` |
| `DEPLOY_SSH_KEY` | Full private key, including the `BEGIN` and `END` lines |
| `DEPLOY_PATH` | Optional. Default `/opt/education-platform` |
| `DEPLOY_PORT` | Optional. Default `22` |
| `DEPLOY_HEALTH_URL` | Optional. `https://DOMAIN/health`. The workflow curls it after SSH returns |

The first boot is still the manual `./deploy/deploy.sh` after you fill in
`deploy/.env`. Pushes after that rebuild on the VM. The script prunes unused
images and build cache older than 24 hours so the disk does not fill up.

## Local rehearsal

Caddy uses its internal CA for `localhost`, so you can boot the same Compose
file without a public DNS name:

```bash
cp deploy/.env.prod.example deploy/.env
# Set DOMAIN=localhost, ACME_EMAIL, CORS_ORIGINS=["https://localhost"],
# real passwords, and JWT_SECRET of at least 32 characters.
ALLOW_LOCAL_DOMAIN=1 DEPLOY_SKIP_GIT=1 ./deploy/deploy.sh
curl --insecure --fail --silent https://localhost/health
```

`docker compose -f deploy/docker-compose.prod.yml config` checks the file
without starting anything.
