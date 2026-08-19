# Deployment

Canonical URL: **https://aptplans.org**. `aptplans.com` must 301 there.

## DNS and TLS

1. Point nameservers for `aptplans.org` and `aptplans.com` at Cloudflare.
2. Orange-cloud `aptplans.org` (apex and www) to the origin A/AAAA record.
3. On `aptplans.com`, 301 apex and www to `https://aptplans.org`.
4. Origin is Caddy on the KS-6. Cloudflare can handle visitor TLS; keep origin HTTPS or authenticated origin pulls as configured in Cloudflare.

## Origin host

Target: Debian stable, Docker Engine, Compose plugin, unattended-upgrades.

Suggested layout on the RAID1 root:

| Path | Contents |
| --- | --- |
| `/opt/aptplans` | git clone of this repository |
| `/var/lib/aptplans/site` | generated `dist/` tree Caddy serves |
| `/var/lib/aptplans/files` | hashed PDFs and WARCs (not in git) |

```bash
git clone https://github.com/alexwitherspoon/aptplans.git /opt/aptplans
cd /opt/aptplans
cp docker/docker-compose.override.yml.example docker/docker-compose.override.yml
# edit host paths if they differ
```

Install the timer:

```bash
sudo cp systemd/aptplans-pipeline.service systemd/aptplans-pipeline.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now aptplans-pipeline.timer
```

Bring the site up:

```bash
cd /opt/aptplans
make site
# copy dist/ to SITE_PATH, or bind-mount it via compose override
docker compose -f docker/docker-compose.yml -f docker/docker-compose.prod.yml up -d site
```

The pipeline is not a long-running daemon. The timer starts it; the container should exit.

## Cache headers

Caddy already sets long `Cache-Control` on `/files/*`. After a catalog publish, purge HTML/RSS in Cloudflare if a change must be visible immediately. Hashed PDF URLs can stay cached.

## What not to deploy

Do not put PDFs, `.gguf` weights, or extracted text in git. Do not run a public LLM endpoint. Do not add Redis, Postgres, or a second orchestrator for the first deployment.
