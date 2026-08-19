# Operations

Steady state should be boring: unattended-upgrades, a weekly timer that usually finds nothing, and an occasional GitHub issue or PR.

## Timer

```bash
systemctl status aptplans-pipeline.timer
systemctl list-timers aptplans-pipeline.timer
journalctl -u aptplans-pipeline.service -n 100
```

Run one job by hand:

```bash
cd /opt/aptplans
docker compose -f docker/docker-compose.yml -f docker/docker-compose.prod.yml --profile jobs run --rm --no-deps pipeline
```

Jobs are serial on purpose. Do not scale the worker count to go faster. Backfill is allowed to take months.

## Site rebuild

After a catalog change that should go live:

```bash
cd /opt/aptplans
git pull
make site
# publish dist/ to the Caddy docroot (SITE_PATH)
```

If HTML looks stale at the edge, purge Cloudflare for HTML/RSS only. Leave hashed `/files/` objects cached.

## Completeness and freshness

The public site should expose corpus counts and coverage status (`complete` / `link_only` / `missing`, and so on). Treat `complete` count over months as the success metric. Queue depth should sit near zero once backfill is done.

Official URL health belongs in the weekly poll: live, moved, dead, or replaced. Same URL plus a new SHA-256 is a new version.

## Crawler manners

User-Agent is `aptplans.org`. One request at a time per host. Honor robots.txt. Back off on errors. Matching the slow parse rate is a feature.

## Disk

PDFs live on origin RAID1. Watch used space on `/var/lib/aptplans/files`. Expected corpus is on the order of 0.25–1 TB, with headroom on an 8 TB mirror. There is no offsite replica of the bytes in the first deployment.

## Failures

Low-confidence parses, hash mismatches, or documents that do not look like a master plan / ALP / statute should not go live. Open or update a GitHub PR / issue and leave `review_status` as `needs_human`.

Skip filenames and appendices that look like SSI or security-restricted drawings.

## Logs

Compose uses json-file logging with rotation (`max-size` 10m). Pipeline output also lands in the systemd journal for the oneshot service.
