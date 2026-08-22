# Operations

Steady state should be boring: unattended-upgrades, a Monday reboot, a worker that drains the document queue continuously (one job at a time), a daily official-URL check, a monthly NASR/NPIAS/OurAirports/grant and overlay fact-sheet refresh (airport HTML extracts the sheet on each site generate), and an occasional GitHub issue or PR. After reboot the worker starts, sees current overlay files, and does not hit FAA again.

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
systemctl status aptplans-airports.timer
systemctl status aptplans-links.timer
systemctl list-timers aptplans-airports.timer aptplans-links.timer
journalctl -u aptplans-airports.service -n 100
journalctl -u aptplans-links.service -n 100
docker compose --env-file /home/aptplans/.env.production \
  --env-file /home/aptplans/.env.secrets \
  --env-file /home/aptplans/.env.search \
  -f docker/docker-compose.yml -f docker/docker-compose.prod.yml \
  logs --tail 100 worker
```

The document queue is drained by the Compose `worker` process, not a weekly timer. `aptplans-pipeline.timer` should be disabled. Origin Compose uses `.env.production`, `.env.secrets`, and `.env.search` (Meilisearch master key, written once by bootstrap). The worker polls `pending/` about once a minute when idle and lists GitHub intake issues at most hourly. Uncaught errors retry with backoff and stop after three attempts. One extra job by hand (waits if the worker is in a job):

```bash
cd /opt/aptplans
docker compose --env-file /home/aptplans/.env.production \
  --env-file /home/aptplans/.env.secrets \
  --env-file /home/aptplans/.env.search \
  -f docker/docker-compose.yml -f docker/docker-compose.prod.yml \
  exec -T worker python3 pipeline/run_once.py
```

Refresh the airport list by hand (NASR + NPIAS into overlay; does not live-fetch in CI). Terms: [FAA terms and systems](FAA.md).

```bash
docker compose --env-file /home/aptplans/.env.production \
  --env-file /home/aptplans/.env.secrets \
  -f docker/docker-compose.yml -f docker/docker-compose.prod.yml \
  exec -T worker python3 -m pipeline.refresh_airports --force
```

Jobs are serial on purpose. Do not scale the worker count to go faster. Backfill is allowed to take months.

The worker reaches Ollama at `http://ollama:11434` on the internal `llm` network. On origin, Ollama is not published on the host; check it with Compose exec, not curl to localhost. Local Compose binds `127.0.0.1:11434` for diagnostics.

```bash
docker compose --env-file /home/aptplans/.env.production \
  --env-file /home/aptplans/.env.secrets \
  -f docker/docker-compose.yml -f docker/docker-compose.prod.yml \
  exec ollama ollama list
```

Ollama keeps `bonsai-27b` loaded (`OLLAMA_KEEP_ALIVE=-1`). CD warms it after import; after a Monday reboot, `aptplans-ollama-warmup.service` loads it again. First load on CPU can take several minutes. Worker generate calls keep thinking off so unofficial notes are the paragraph, not chain-of-thought. Large plans are read in full, then sent as successive 32k windows with a reduce. Origin waits (`APTPLANS_LLM_TIMEOUT`, default 3600s per call).

Throughput is a KS-6 measurement, not a laptop one. Do it by hand: one Compose exec, no CI. Ollama is serial (`OLLAMA_NUM_PARALLEL=1`). The document worker may be in a generate; the flock will wait, or run this when logs show idle. Then:

```bash
cd /opt/aptplans
docker compose --env-file /home/aptplans/.env.production \
  --env-file /home/aptplans/.env.secrets \
  -f docker/docker-compose.yml -f docker/docker-compose.prod.yml \
  exec -T worker python3 -m pipeline.benchmark
```

Default is a short ping, then the same worker-shaped unofficial-note prompt twice: `think: false` then `think: true`, both uncapped. Read `prompt_tok_s`, `eval_tok_s`, `wall_s`, and the two responses. Thinking-on can run a long time. Wrapper: `scripts/host/benchmark-ollama.sh`. That module has to be on the origin checkout; until deploy, run the same logic with `exec -T worker python3 -` and a stdin script.

```bash
systemctl status aptplans-ollama-warmup.service
docker compose --env-file /home/aptplans/.env.production \
  --env-file /home/aptplans/.env.secrets \
  -f docker/docker-compose.yml -f docker/docker-compose.prod.yml \
  exec ollama ollama ps
```

On the KS-6 (EPYC 7351P, 4 NUMA nodes), production pins **NUMA 0** (`0-3,16-19`) to `site`, `search`, and `worker`, and **NUMA 1-3** (`4-15,20-31`) to Ollama. Local Docker on a laptop does not use those cpusets. Same weights, different hardware: laptop `make llm` duration is not an origin estimate. Confirm after deploy:

```bash
docker inspect aptplans-ollama-1 --format '{{.Name}} {{.HostConfig.CpusetCpus}}'
docker inspect aptplans-site-1 --format '{{.Name}} {{.HostConfig.CpusetCpus}}'
docker inspect aptplans-search-1 --format '{{.Name}} {{.HostConfig.CpusetCpus}}'
docker inspect aptplans-worker-1 --format '{{.Name}} {{.HostConfig.CpusetCpus}}'
```

## Site rebuild

CD rebuilds HTML on the GitHub runner from the git catalog and rsyncs `dist/` to `/var/lib/aptplans/site`. Origin then rebuilds again from git plus `/var/lib/aptplans/catalog` overlay so hashed completeness from the worker is not wiped. Caddy bind-mounts that directory.

If HTML looks stale at the edge after 24 hours, origin Cache-Control already caps shared caches at a day. Purge Cloudflare for HTML/RSS only when a same-day fix must land immediately. Leave hashed `/files/` objects cached.

Rebuild the public search index after a bulk text restore or a Meilisearch volume wipe. `--reindex` extracts missing page JSONL from hashed PDFs, then replaces the daemon index from overlay plus those sidecars. A worker boot does the same page backfill when the index has no page hits:

```bash
docker compose --env-file /home/aptplans/.env.production \
  --env-file /home/aptplans/.env.secrets \
  --env-file /home/aptplans/.env.search \
  -f docker/docker-compose.yml -f docker/docker-compose.prod.yml \
  exec -T worker python3 -m pipeline.search --reindex
```

Worker overlay and queue:

| Path | Contents |
| --- | --- |
| `/var/lib/aptplans/files` | hashed PDFs |
| `/var/lib/aptplans/reject` | 90-day private copies of artifacts that failed a check (not Caddy) |
| `/var/lib/aptplans/text` | gated page JSONL (not served) |
| `/var/lib/aptplans/search` | Meilisearch data (no host port) |
| `/var/lib/aptplans/catalog` | overlay JSONL (`airports.jsonl` from NASR+NPIAS, `grants.jsonl` from AIP histories, plus document completeness and hashes) |
| `/var/lib/aptplans/queue` | serial job JSON |
| `/var/lib/aptplans/logs` | redacted worker JSONL (`GET /review/v1/logs`) |

Seed known official PDFs onto the queue for local backfill only (does not fetch). Production is the default; this no-ops unless reference seed is enabled:

```bash
docker compose --env-file /home/aptplans/.env.production \
  --env-file /home/aptplans/.env.secrets \
  -f docker/docker-compose.yml -f docker/docker-compose.prod.yml \
  exec -T worker python3 -m pipeline.discover
```

## Completeness and freshness

The public site should expose corpus counts and coverage status (`complete` / `link_only` / `missing`, and so on). Treat `complete` count over months as the success metric. Queue depth should sit near zero once backfill is done.

Official URL health is a daily pass (`python3 -m pipeline.check`): live, moved, or dead. Live URLs are rechecked after 7 days; dead after 30. 5xx and robots denials are errors, not dead. A dead official URL with a preserved copy becomes `preserved_only`. Without a copy it becomes `missing` and the worker tries listed mirrors, then Wayback CDX when `APTPLANS_WAYBACK=1`. A moved URL queues a fetch of the new location. Same URL plus a new SHA-256 is a content version on the next fetch.

Run one link-check pass by hand:

```bash
docker compose --env-file /home/aptplans/.env.production \
  --env-file /home/aptplans/.env.secrets \
  -f docker/docker-compose.yml -f docker/docker-compose.prod.yml \
  exec -T -e APTPLANS_WAYBACK=1 worker python3 -m pipeline.check
```

## Crawler manners

User-Agent is `aptplans.org`. One request at a time per host. Honor robots.txt. Back off on errors. Matching the slow parse rate is a feature.

Production scrapes egress through the internal `egress` service (Gluetun + PIA OpenVPN). The worker uses `APTPLANS_FETCH_PROXY=http://egress:8888`; scrape targets must not see the origin host IP.

After deploy or egress changes, confirm VPN health and that fetches use the tunnel. On origin, set a shell alias once per session:

```bash
export COMPOSE_PROD='docker compose --env-file /home/aptplans/.env.production --env-file /home/aptplans/.env.secrets -f docker/docker-compose.yml -f docker/docker-compose.prod.yml'
```

**1. Egress container health**

```bash
$COMPOSE_PROD ps egress
$COMPOSE_PROD exec -T egress /gluetun-entrypoint healthcheck && echo egress_ok
$COMPOSE_PROD logs --tail=30 egress
```

`egress` should be `healthy`. Logs should show OpenVPN `Initialization Sequence Completed` and a public IP that is not the origin host.

**2. Tunnel IP vs origin host IP**

```bash
curl -fsS https://ifconfig.me/ip ; echo    # origin host (direct)
$COMPOSE_PROD exec -T worker python3 -c \
  "from pipeline.fetch import fetch_bytes; print(fetch_bytes('https://ifconfig.me/ip', timeout=20)[0].decode().strip())"
```

The worker IP must differ from the host IP. If they match, or the worker command errors, egress is not working.

**3. Known scrape targets (worker path through `APTPLANS_FETCH_PROXY`)**

```bash
$COMPOSE_PROD exec -T worker python3 <<'PY'
from catalog.airports import NASR_LISTING_URL
from catalog.grants import GRANT_HISTORIES_URL
from catalog.npias import NPIAS_SOURCE
from catalog.ourairports import OURAIRPORTS_CSV_URL
from pipeline.fetch import fetch_meta

targets = [
    ("nasr_listing", NASR_LISTING_URL),
    ("npias", NPIAS_SOURCE),
    ("ourairports", OURAIRPORTS_CSV_URL),
    ("grant_histories", GRANT_HISTORIES_URL),
]
for name, url in targets:
    status, final = fetch_meta(url, method="HEAD", timeout=60)
    print(f"{name}: {status} {final}")
PY
```

Expect `200` or `301`/`302` for each. Any `URLError`, `RuntimeError` (missing proxy), or timeout means the tunnel or target is broken.

If `egress` is unhealthy or VPN creds are missing, worker fetches fail closed rather than falling back to the host IP.

## Deploy and background jobs

CD restarts the worker and returns once Caddy answers. HTML rebuild, FAA overlay refresh, search sync, and LLM warm-up run as **queue jobs** (`site_build`, `overlay_refresh`, `grant_spend`, `budget_enrich`, `overview_refresh`, `search_sync`, `ollama_warm`). Inspect pending work:

```bash
ls -1 /var/lib/aptplans/queue/pending/
$COMPOSE_PROD logs --tail=80 worker
```

**Stuck `docker compose run` containers** from an old deploy (names like `aptplans-worker-run-*`) can be removed:

```bash
sudo docker rm -f $(sudo docker ps -aq --filter name=worker-run) 2>/dev/null || true
```

**`.env.secrets` sourcing errors** (for example `East: command not found`) mean an unquoted value has spaces. Redeploy from GitHub Actions (CD now quotes values) or fix `/home/aptplans/.env.secrets` manually, e.g. `PIA_SERVER_REGIONS="US East"`.

**Troubleshooting stuck `egress`**

Symptoms: `health: starting` forever, logs show `UDPv4 link remote` without `Initialization Sequence Completed`, or healthcheck DNS errors (`operation not permitted` on `1.1.1.1:53`). Worker stays down because it waits for healthy egress.

```bash
# 1. Confirm VPN creds are present (values not printed)
grep -c '^PIA_OPENVPN_' /home/aptplans/.env.secrets

# 2. Ensure Gluetun data dir exists and refresh PIA server list
sudo mkdir -p /var/lib/aptplans/egress
sudo chown aptplans:aptplans /var/lib/aptplans/egress
docker run --rm -v /var/lib/aptplans/egress:/gluetun qmcgaw/gluetun:v3.40.0 \
  update -enduser -providers "private internet access"

# 3. Add EGRESS_PATH to production env if missing, then recreate egress
grep -q '^EGRESS_PATH=' /home/aptplans/.env.production || \
  echo 'EGRESS_PATH=/var/lib/aptplans/egress' | sudo tee -a /home/aptplans/.env.production
$COMPOSE_PROD up -d --force-recreate egress
$COMPOSE_PROD logs -f egress   # wait for Initialization Sequence Completed

# 4. If UDP still fails, the origin network may block UDP 1197; production uses TCP OpenVPN.
```

After `egress` is healthy, `worker` starts automatically (`depends_on: service_healthy`).

## Disk

PDFs live on origin RAID1. Watch used space on `/var/lib/aptplans/files` and `/var/lib/aptplans/reject`. Expected corpus is on the order of 0.25-1 TB, with headroom on an 8 TB mirror. Reject copies age out after 90 days. There is no offsite replica of the bytes in the first deployment.

## Failures

Low-confidence unofficial wording can still go live when outer gates passed **and** the record was vetted (`review_status` auto_pass or published). A hashed snapshot stays `pending` until that vet step. Hash mismatches and SSI-looking files must not publish. Those bytes, plus newsletters and other kind-gate failures, are kept in `/var/lib/aptplans/reject` for 90 days so scoring work can pull them over the review API. They never go under Caddy `/files/`. Open or update a GitHub issue and leave `review_status` as `needs_human` only for integrity or safety cases that still need a human.

On origin, `https://aptplans.org/review` is the private review API (HTTPS, API key; Caddy `:443` only). CD writes `APTPLANS_REVIEW_TOKEN` from the GitHub Actions secret into `/home/aptplans/.env.secrets` so Compose interpolates it. A laptop or Cursor agent uses the same key in gitignored `.env` with `APTPLANS_REVIEW_URL=https://aptplans.org/review`. `GET /v1/status` and `GET /v1/logs` are the health loop. `GET /v1/stats`, `GET /v1/outcomes?bucket=uncertain`, `GET /v1/signals`, and `GET /v1/rejects` measure the live mix. `GET /v1/rejects/{sha256}/bytes` copies a failed artifact for local training (still not a publish). `POST /v1/label` records a gold packet without publishing. `PATCH /v1/documents/{id}` sets `review_status`. `make pull-outcomes` writes `data/score/review/` (including `rejects/` files) for scoring work; then `python3 scripts/train_evidence.py --outcomes data/score/review/gold.json`. Merge new official URLs plus labels into `catalog/references/score_gold.json` (no excerpts). Do not log the token.

Skip filenames and appendices that look like SSI or security-restricted drawings.

## Logs

Compose uses json-file logging with rotation (`max-size` 10m). The worker also appends redacted JSON lines under `/var/lib/aptplans/logs`. `GET https://aptplans.org/review/v1/logs` (API key) is the programmatic view. Pipeline output also lands in the systemd journal for the oneshot timers.
