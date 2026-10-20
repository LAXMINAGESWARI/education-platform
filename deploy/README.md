# Deploy

## Self-host on your PC (demo)

Cloudflare Tunnel reaches the demo with an outbound connection, so the home router can stay closed and the PC needs no public IP. `cloudflared` joins the Compose network and dials Caddy over plain HTTP. Cloudflare terminates TLS, so Caddy does not run ACME in this mode. The Oracle VM path (ports 80 and 443, Let's Encrypt) is the rest of this guide.

These steps are for a Linux x86_64 PC (Ubuntu or similar) with Docker Engine. The same `./deploy/selfhost.sh` command works on Apple Silicon: install Docker Desktop, then keep the Mac awake with `caffeinate -dims`.

The prod project name is `education-platform-prod`. Containers and volumes use that prefix. Root `docker compose` keeps the checkout directory name (usually `education-platform`) and its own Postgres volume. `./deploy/selfhost.sh` and `./deploy/deploy.sh` pass the prod name, so they do not attach to the dev database or remove dev containers with `--remove-orphans`.

No VM was deployed under the earlier name `education-platform`. If a PC already ran the prod file that way, it attached to the dev volume and the dev Postgres container. Leave `education-platform_postgres_data` in place, start dev again with `docker compose up -d postgres` from the repo root, and run `./deploy/selfhost.sh up`. That creates `education-platform-prod_postgres_data`. A `COMPOSE_PROJECT_NAME=education-platform-prod` workaround is optional on this version; the scripts set the project name.

### Windows

Use Docker Desktop with the WSL2 backend and run the script in the Ubuntu distro. Windows sleep pauses WSL, so set the power plan to never sleep while you share the link. A systemd unit inside WSL runs when systemd is enabled there. Otherwise let Docker Desktop start with Windows; the Compose services use `restart: unless-stopped`.

### 1. Install Docker Engine

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker "$USER"
sudo systemctl enable --now docker
```

Log out and back in so the `docker` group applies.

### 2. Fill in the env file

```bash
git clone https://github.com/aushakanuj/education-platform.git
cd education-platform
cp deploy/.env.prod.example deploy/.env
chmod 600 deploy/.env
```

Set `POSTGRES_PASSWORD`, `JWT_SECRET` (32 characters or more), and `TEXT_TO_SQL_DB_PASSWORD`. Passwords are letters, digits, and hyphen. Leave `VITE_API_BASE_URL` empty so the built app calls `/api/v1` on the tunnel host.

| Mode | Cloudflare account | What you set |
| --- | --- | --- |
| Quick | None. The URL is `https://<random>.trycloudflare.com` | Leave `CLOUDFLARE_TUNNEL_TOKEN` empty |
| Named | A hostname on your domain | `CLOUDFLARE_TUNNEL_TOKEN`, `DOMAIN`, and `CORS_ORIGINS` containing `https://DOMAIN` |

Quick mode sets `CORS_ORIGIN_REGEX` to `https://[A-Za-z0-9-]+\.trycloudflare\.com`. The hostname changes each time `cloudflared` starts, and that pattern matches the new origin. The page and the API are the same host, so the browser calls `/api/v1` on the URL `up` prints. Anyone who has the link can open the demo. The quick URL changes again after a restart.

Named mode leaves that regex empty and uses `CORS_ORIGINS` only.

Demo accounts after a synthetic seed use the password `demo1234`. The table is in [What you fill in by hand](#what-you-fill-in-by-hand) below.

### 3. Start and share

```bash
./deploy/selfhost.sh up
./deploy/selfhost.sh url
./deploy/selfhost.sh logs
./deploy/selfhost.sh down
```

`up` builds, migrates, and prints the public URL in quick mode. `down` stops the containers and keeps the Postgres volume.

### Keep the PC awake

```bash
sudo systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target
sudo mkdir -p /etc/systemd/logind.conf.d
sudo tee /etc/systemd/logind.conf.d/no-suspend.conf >/dev/null <<'EOF'
[Login]
HandleLidSwitch=ignore
HandleLidSwitchExternalPower=ignore
HandleLidSwitchDocked=ignore
IdleAction=ignore
EOF
sudo systemctl restart systemd-logind
```

Restarting `systemd-logind` can drop the current login. SSH back in afterward.

### Start again after a reboot

Compose sets `restart: unless-stopped` on the API, worker, Caddy, and `cloudflared`. With Docker enabled (`systemctl enable docker`), those containers come back when the PC boots.

To run the deploy script itself on boot (so migrate runs, not only existing containers):

```bash
sudo mkdir -p /opt/education-platform
# Or point the unit at the directory where you cloned the repo.
sudo cp deploy/education-platform-selfhost.service /etc/systemd/system/education-platform.service
sudo systemctl daemon-reload
sudo systemctl enable --now education-platform.service
```

Edit `WorkingDirectory`, `ExecStart`, and `ExecStop` if the clone is not `/opt/education-platform`. In quick mode, run `./deploy/selfhost.sh url` after boot because the public URL is new. A named tunnel stays on `https://DOMAIN`.

### Update this PC

The GitHub Action in `.github/workflows/deploy.yml` updates the Oracle VM. On the PC, pull and rebuild with:

```bash
./deploy/selfhost.sh update
```

### Named tunnel on a GoDaddy domain

In the Cloudflare dashboard: **Zero Trust → Networks → Tunnels → Create a tunnel**. Choose the Docker install, and put the token in `CLOUDFLARE_TUNNEL_TOKEN`. Add a public hostname for `DOMAIN`, service type **HTTP**, URL **`caddy:80`**. `caddy` is the Compose service name on the Docker network. Save, then `./deploy/selfhost.sh up`.

**Move the nameservers (free plan).** Add the domain in Cloudflare. At GoDaddy, set the domain's nameservers to the two Cloudflare assigns. When the zone is active, the public hostname you added on the tunnel creates the DNS record. That includes the apex when `DOMAIN` is the root of the domain.

**CNAME, leaving GoDaddy authoritative.** Cloudflare's partial setup (also called CNAME setup) proxies a hostname without a nameserver change. It is a Business or Enterprise feature. Create a CNAME at GoDaddy from that hostname to `{hostname}.cdn.cloudflare.net`, which is the target in [Cloudflare's tunnel FAQ](https://developers.cloudflare.com/cloudflare-one/faq/cloudflare-tunnels-faq/). The `<tunnel-id>.cfargotunnel.com` CNAME belongs in the Cloudflare DNS zone for the same account as the tunnel. Cloudflare creates it when the nameservers point at Cloudflare and you add the public hostname. On the free plan, moving the nameservers is the setup that publishes the hostname.

## Oracle Cloud VM

The demo runs on a single Ubuntu VM (`VM.Standard.A1.Flex`, 4 OCPU, 24 GB). One
Compose project starts Postgres (pgvector), a one-shot migration, the API, the
Postgres worker, the static frontend, and Caddy. Redis is not part of this
stack: ingest and generation jobs are rows in Postgres, claimed with
`FOR UPDATE SKIP LOCKED`. There is no separate scheduler. The worker process is
`python -m education_platform.workers`.

Local development still uses the root `docker-compose.yml` Postgres container.
Nothing in that file changes. The prod stack is the Compose project
`education-platform-prod`, so it does not share that dev volume or those
containers.

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
| `student1@alnoor.school` | First generated student (Adnan Farooq, Grade 6). Shown on the login page |

With the default school size, the planted declining student is
`student97@alnoor.school` (Aisha Rahman, Grade 8, identifier `S-00097`). The login page lists her as the second student. The migrate log
prints her identifier. If you change the section or student counts, use the
`S-` number from that log as the email: `student{that number}@alnoor.school`.

`student@demo.school` is created only when `ENVIRONMENT=development`. Leave
`ENVIRONMENT=production` on the VM. The API still loads the markdown curriculum
into **POC Demo School** on startup when that tree is empty. The synthetic
school is a second institution, **Al Noor International**.

`SEED_SYNTHETIC=true` skips the seed when that institution already exists, so
later deploys do not wipe it. Set `SEED_SYNTHETIC=always` in `deploy/.env`, run
`./deploy/selfhost.sh update` once, then set it back to `true`. Keep
`SYNTHETIC_SECTIONS_PER_GRADE` at 2 or more. The generator assigns one teacher
to two sections of the same grade.

`always` rebuilds only the institution named by `SYNTHETIC_INSTITUTION`
(default **Al Noor International**). It replaces that school's users, teaching
assignments, enrollments, attendance, quiz attempts, lesson-completion rows,
at-risk flags, and policy chats owned by those accounts, then writes the school
again. Grades, topics, and quizzes are kept and reused. Published lesson text
is rewritten for every seeded topic and subtopic; a curriculum PDF that had
been published as that lesson (slug `lesson`) is superseded. The old version
row and its file stay on disk. Knowledge-document uploads for that school,
including any QA- PDFs stored as knowledge documents, stay in the database and
on the uploads volume. Other institutions, including POC Demo School, are left
as they are.

A content-generation run or curriculum-generation job submitted by an Al Noor
account is not removed. If one exists, the rebuild stops on the user foreign
key and the transaction rolls back, so the previous school is unchanged.

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

`/health` checks Postgres and returns `{"status":"ok","db":"ok"}`, or HTTP 503
with `{"status":"degraded","db":"error"}` when that check fails. `/` returns
`200` and the SPA. Sign in at `https://DOMAIN/login` with the accounts above.

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
docker compose --project-name education-platform-prod --env-file deploy/.env -f deploy/docker-compose.prod.yml logs -f --tail=200 api worker caddy
```

The worker is up when its log contains `Worker started`. It then polls Postgres.
A quiet log after that means the queue is empty, not that the process stopped.

```bash
docker compose --project-name education-platform-prod --env-file deploy/.env -f deploy/docker-compose.prod.yml ps
```

`deploy/migrate.sh` writes a timestamped custom-format dump before it runs
migrations and before it promotes synthetic Core topics. The migrate service
mounts `${BACKUP_DIR:-./backups}` (relative to this directory) at `/backups`
in the container. The API image installs `postgresql-client-16` so `pg_dump`
matches `pgvector/pgvector:pg16`. The script prints both versions and stops,
without writing a dump, when the major versions differ. A failed, empty, or
unreadable dump is deleted and stops the script before migrations and
promotion. The file name is `education-YYYYmmddTHHMMSSZ.dump`.

Restore that file the same way as a manual dump, from the host directory that
holds it:

```bash
docker compose --project-name education-platform-prod --env-file deploy/.env -f deploy/docker-compose.prod.yml exec -T postgres \
  sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists --no-owner' \
  < deploy/backups/education-YYYYmmddTHHMMSSZ.dump
```

## Back up the database

```bash
cd /opt/education-platform
docker compose --project-name education-platform-prod --env-file deploy/.env -f deploy/docker-compose.prod.yml exec -T postgres \
  sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --no-owner --format=custom' \
  > "education-$(date +%F).dump"
```

Copy the dump off the VM. Restore into the database only when you mean to
replace the data:

```bash
docker compose --project-name education-platform-prod --env-file deploy/.env -f deploy/docker-compose.prod.yml exec -T postgres \
  sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists --no-owner' \
  < education-YYYY-MM-DD.dump
```

Also copy the uploads volume if you need the PDF blobs:

```bash
docker compose --project-name education-platform-prod --env-file deploy/.env -f deploy/docker-compose.prod.yml exec -T api \
  tar -C /data/uploads -czf - . > uploads.tgz
```

## Auto-redeploy on push

This workflow is for the Oracle VM. A PC demo updates with `./deploy/selfhost.sh update`.

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

`docker compose --project-name education-platform-prod -f deploy/docker-compose.prod.yml config` checks the file
without starting anything. The project name in that output is `education-platform-prod`.
