# Operations

Steady state should be boring: unattended-upgrades, a Monday reboot, a weekly pipeline timer that usually finds nothing, and an occasional GitHub issue or PR.

## Deploy

Pushes to `main` that pass [Test](../.github/workflows/test.yml) trigger [Deploy](../.github/workflows/deploy.yml). Manual runs are **Actions → Deploy**. Secrets are listed in [`.github/SETUP.md`](../.github/SETUP.md).

## Host updates and reboot

Unattended-upgrades install Debian and Docker packages as they appear. They do not reboot on their own. `aptplans-reboot.timer` reboots **Monday at 12:00 Pacific**.

```bash
systemctl list-timers aptplans-reboot.timer
journalctl -u aptplans-reboot.service -n 20
cat /var/log/unattended-upgrades/unattended-upgrades.log
```

## Timer

```bash
systemctl status aptplans-pipeline.timer
systemctl list-timers aptplans-pipeline.timer
journalctl -u aptplans-pipeline.service -n 100
```

Run one job by hand:

```bash
cd /opt/aptplans
docker compose --env-file /home/aptplans/.env.production \
  --env-file /home/aptplans/.env.secrets \
  -f docker/docker-compose.yml -f docker/docker-compose.prod.yml \
  exec -T worker python3 pipeline/run_once.py
```

Jobs are serial on purpose. Do not scale the worker count to go faster. Backfill is allowed to take months.

The stack is `site`, `worker`, and `ollama`. The worker reaches Ollama at `http://ollama:11434` on the internal `llm` network. Ollama is not published on the host. Check it with Compose exec, not curl to localhost:

```bash
docker compose --env-file /home/aptplans/.env.production \
  --env-file /home/aptplans/.env.secrets \
  -f docker/docker-compose.yml -f docker/docker-compose.prod.yml \
  exec ollama ollama list
```

Ollama keeps `bonsai-27b` loaded (`OLLAMA_KEEP_ALIVE=-1`). CD warms it after import; after a Monday reboot, `aptplans-ollama-warmup.service` loads it again. First load on CPU can take several minutes.

```bash
systemctl status aptplans-ollama-warmup.service
docker compose --env-file /home/aptplans/.env.production \
  --env-file /home/aptplans/.env.secrets \
  -f docker/docker-compose.yml -f docker/docker-compose.prod.yml \
  exec ollama ollama ps
```

On the KS-6 (EPYC 7351P, 4 NUMA nodes), production pins **NUMA 0** (`0-3,16-19`) to `site` and `worker`, and **NUMA 1-3** (`4-15,20-31`) to Ollama. Confirm after deploy:

```bash
docker inspect aptplans-ollama-1 --format '{{.Name}} {{.HostConfig.CpusetCpus}}'
docker inspect aptplans-site-1 --format '{{.Name}} {{.HostConfig.CpusetCpus}}'
docker inspect aptplans-worker-1 --format '{{.Name}} {{.HostConfig.CpusetCpus}}'
```

## Site rebuild

CD rebuilds HTML on the GitHub runner and rsyncs `dist/` to `/var/lib/aptplans/site`. A push to `main` (after tests) is the usual publish path. Caddy bind-mounts that directory, so most deploys do not need a container rebuild.

If HTML looks stale at the edge, purge Cloudflare for HTML/RSS only. Leave hashed `/files/` objects cached.

## Completeness and freshness

The public site should expose corpus counts and coverage status (`complete` / `link_only` / `missing`, and so on). Treat `complete` count over months as the success metric. Queue depth should sit near zero once backfill is done.

Official URL health belongs in the weekly poll: live, moved, dead, or replaced. Same URL plus a new SHA-256 is a new version.

## Crawler manners

User-Agent is `aptplans.org`. One request at a time per host. Honor robots.txt. Back off on errors. Matching the slow parse rate is a feature.

## Disk

PDFs live on origin RAID1. Watch used space on `/var/lib/aptplans/files`. Expected corpus is on the order of 0.25–1 TB, with headroom on an 8 TB mirror. There is no offsite replica of the bytes in the first deployment.

## Failures

Low-confidence unofficial wording can still go live when outer gates passed. Hash mismatches and SSI-looking files must not. Open or update a GitHub issue and leave `review_status` as `needs_human` only for those integrity or safety cases.

Skip filenames and appendices that look like SSI or security-restricted drawings.

## Logs

Compose uses json-file logging with rotation (`max-size` 10m). Pipeline output also lands in the systemd journal for the oneshot service.
