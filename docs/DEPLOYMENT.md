# Deployment

Canonical URL: **https://aptplans.org**. `aptplans.com` 301s there.

CD from GitHub Actions is the supported path from a **bare Debian 13 (trixie)** install. The host stays thin: Docker Engine, UFW, fail2ban, unattended-upgrades. Caddy, Meilisearch, the worker, and Ollama run as one Compose stack.

## One-time: DNS and SSH

1. Point Cloudflare nameservers for `aptplans.org` and `aptplans.com`.
2. Orange-cloud `aptplans.org` (apex and www) to the KS-6 A/AAAA record.
3. 301 `aptplans.com` (apex and www) to `https://aptplans.org`.
4. Cloudflare SSL/TLS: **Full** until an Origin CA cert is in GitHub secrets, then **Full (strict)**.
5. From console or the image `debian` user, put the GitHub Actions public key in `/home/aptplans/.ssh/authorized_keys` (create `aptplans` first if the account is not there yet). Confirm `ssh aptplans@ORIGIN_IP` works. Remote root SSH is disabled.

`HOST` in GitHub secrets should be the **origin IP**, not `aptplans.org` (that name is proxied).

## GitHub secrets

See [`.github/SETUP.md`](../.github/SETUP.md). Required: `HOST`, `USER=aptplans`, `SSH_PRIVATE_KEY`. The public half of that key must already be in `/home/aptplans/.ssh/authorized_keys`.

## What CD does

On every successful `Test` run on `main` (or a manual **Deploy** dispatch):

1. Rsync this repo to `/opt/aptplans` (not `dist/`; HTML is built on origin).
2. Run [`scripts/host/remote-deploy.sh`](../scripts/host/remote-deploy.sh), which is idempotent:
   - Confirms Debian 13
   - Installs the small host package set (no Python, no Caddy, no nginx on the host)
   - Installs Docker Engine from Docker's Debian repo
   - Creates `aptplans` (docker group, passwordless sudo)
   - Timezone `America/Los_Angeles`
   - sshd drop-in (no passwords, `PermitRootLogin no`, `AllowUsers aptplans`)
   - sysctl hardening
   - UFW: deny inbound except 22/80/443
   - fail2ban on sshd (5 failures / 10 minutes → 7 day ban)
   - unattended-upgrades for Debian and Docker packages (installs automatically; **does not reboot by itself**)
   - systemd timer **Monday 12:00 Pacific** → reboot
   - weekly Docker prune (Sunday 02:00 Pacific)
   - always-on document worker (one job at a time; weekly pipeline timer disabled)
   - daily official-URL check (dead, moved, or live; Wayback rediscovery on origin)
   - monthly NASR/NPIAS airport, grant, and fact-sheet refresh (1st of the month, Pacific)
   - Origin TLS in `/var/lib/aptplans/tls` (Cloudflare Origin CA if secrets are set, otherwise self-signed)
   - Worker secrets in `/home/aptplans/.env.secrets` (PIA VPN creds for the `egress` service, intake GitHub token, review API token, Brave search key, and optional Gemini key if those Actions secrets are set)
   - `docker compose` up for the full stack: Caddy on 80/443, Meilisearch (no host port), worker, CPU Ollama
   - Waits for VPN egress and Caddy to answer on :80 (deploy succeeds when the stack is healthy, not when background work finishes)
   - Schedules Ollama GGUF import in the background when needed (`nohup provision-ollama.sh`)
   - Restarts the worker, which on boot only warms Ollama and refreshes stale FAA overlays. **Every deploy** runs `scripts/host/remote-deploy.sh`, which calls idempotent `bootstrap.sh` (systemd timers, directories, firewall, env files) before `docker compose up`. Systemd timers enqueue periodic maintenance; they never run long work inline.

A successful deploy means **services are up and serving** (possibly stale HTML until `site_build` runs). Long work runs asynchronously in the worker queue.

GitHub Actions SSH uses `ServerAliveInterval=30`. Values with spaces in `.env.secrets` (for example `PIA_SERVER_REGIONS="US East"`) are quoted by CD.

Host layout:

| Path | Contents |
| --- | --- |
| `/opt/aptplans` | rsynced git tree |
| `/var/lib/aptplans/releases` | immutable static/file generations plus atomic `current` symlink |
| `/var/lib/aptplans/files` | private content-addressed source bytes (not mounted by Caddy) |
| `/var/lib/aptplans/reject` | 90-day private failed artifacts (not in git; not on Caddy) |
| `/var/lib/aptplans/text` | gated native page JSONL (not in git; not on Caddy) |
| `/var/lib/aptplans/search` | Meilisearch data (not in git; no host port) |
| `/var/lib/aptplans/catalog` | legacy cutover input plus operational snapshots; not domain authority |
| `/var/lib/aptplans/queue` | WAL jobs, immutable domain generations, and release journal |
| `/var/lib/aptplans/control` | API-writable commands and human audit rows |
| `/var/lib/aptplans/logs` | redacted worker JSONL for the review API |
| `/var/lib/aptplans/tls` | origin certificate |
| `/var/lib/aptplans/ollama` | Ollama blobs (not in git) |
| `/var/lib/aptplans/models` | source GGUF used to `ollama create` |
| `/var/lib/aptplans/egress` | Gluetun VPN state and server list (not in git) |
| `/home/aptplans/.env.production` | Compose paths (rewritten each bootstrap) |
| `/home/aptplans/.env.secrets` | PIA VPN (`PIA_OPENVPN_*`) + intake GitHub token + review token + Brave/Gemini search keys (CD; bootstrap does not overwrite) |
| `/home/aptplans/.env.search` | Meilisearch master key (bootstrap writes once; CD does not overwrite) |

## One-time pre-production domain cutover

Do not deploy the domain ledger, review readers, and release paths independently. Stop all writers, back up both ledgers and the overlay, import strictly, build the first complete generation, then restart the stack:

```bash
cd /opt/aptplans
docker compose --env-file /home/aptplans/.env.production \
  --env-file /home/aptplans/.env.secrets \
  --env-file /home/aptplans/.env.search \
  -f docker/docker-compose.yml -f docker/docker-compose.prod.yml \
  stop worker review site
sudo install -d -m 0750 -o "$(id -un)" -g "$(id -gn)" /var/backups/aptplans
APTPLANS_CONTROL_QUEUE=/var/lib/aptplans/control \
  python3 -m pipeline.ledger_ops --queue-dir /var/lib/aptplans/queue \
  backup /var/backups/aptplans/pre-domain
tar -C /var/lib/aptplans -czf /var/backups/aptplans/pre-domain-overlay.tgz catalog
docker compose --env-file /home/aptplans/.env.production \
  --env-file /home/aptplans/.env.secrets \
  --env-file /home/aptplans/.env.search \
  -f docker/docker-compose.yml -f docker/docker-compose.prod.yml \
  run --rm --no-deps worker python3 -m pipeline.domain_cutover \
  /var/lib/aptplans/catalog --queue-dir /var/lib/aptplans/queue \
  --confirm-preproduction-cutover
docker compose --env-file /home/aptplans/.env.production \
  --env-file /home/aptplans/.env.secrets \
  --env-file /home/aptplans/.env.search \
  -f docker/docker-compose.yml -f docker/docker-compose.prod.yml up -d search
docker compose --env-file /home/aptplans/.env.production \
  --env-file /home/aptplans/.env.secrets \
  --env-file /home/aptplans/.env.search \
  -f docker/docker-compose.yml -f docker/docker-compose.prod.yml \
  run --rm --no-deps worker python3 -c \
  'from pipeline.site_build import run_site_build; raise SystemExit(run_site_build() != "built")'
sudo /opt/aptplans/scripts/host/remote-deploy.sh
```

The import rejects malformed JSON, duplicate entity keys, and ambiguous grant identities before committing. Keep the old entity JSONL files unchanged through the observation window; production readers and writers do not fall back to them.

## Manual deploy

Only needed if GitHub cannot reach the box:

```bash
sudo /opt/aptplans/scripts/host/remote-deploy.sh
```

## Reboots

Kernel and Docker Engine updates land during the week via unattended-upgrades. The host reboots **Monday at 12:00 America/Los_Angeles** (Pacific Time, PST or PDT). `site`, `worker`, and `ollama` use `restart: unless-stopped`. The worker drains renewable SQLite leases with one airport in flight by default (`APTPLANS_AIRPORT_CONCURRENCY`).

```bash
systemctl list-timers 'aptplans-*'
```

## What not to install on the host

Python, Caddy, nginx, certbot, Redis, Postgres, Kubernetes, or a host-level Ollama package. Inference is the `ollama` Compose service. If it is not Docker, UFW, fail2ban, or unattended-upgrades, it does not belong on the base OS.
