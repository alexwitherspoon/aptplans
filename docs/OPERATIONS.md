# Operations

Steady state should be boring: unattended-upgrades, a Monday reboot, a worker that drains the document queue continuously, a daily official-URL check, a monthly NASR/NPIAS/OurAirports/grant and fact-sheet refresh, and an occasional GitHub issue or PR. After reboot the worker reads dataset readiness from the current domain generation and does not hit FAA again when sources are current.

## Deploy

Pushes to `main` that pass [Test](../.github/workflows/test.yml) trigger [Deploy](../.github/workflows/deploy.yml). Manual runs are **Actions → Deploy**. Secrets are listed in [`.github/SETUP.md`](../.github/SETUP.md). Each deploy rsyncs the repo, writes secrets, then runs `scripts/host/remote-deploy.sh`, which calls idempotent `bootstrap.sh` before `docker compose up`. Bootstrap reinstalls all `systemd/aptplans-*.timer` units (except the legacy `aptplans-pipeline.timer`) so new timers land without a manual host step.

## Host updates and reboot

Unattended-upgrades install Debian and Docker packages as they appear. They do not reboot on their own. `aptplans-reboot.timer` reboots **Monday at 12:00 Pacific**.

```bash
systemctl list-timers aptplans-reboot.timer
journalctl -u aptplans-reboot.service -n 20
cat /var/log/unattended-upgrades/unattended-upgrades.log
```

## Timers

Maintenance jobs are enqueued by **systemd timers**; the worker transactionally drains eligible rows from the SQLite job ledger. **Boot** (worker container start) warms Ollama and refreshes stale FAA overlays when `APTPLANS_REFRESH_AIRPORTS=1`; it does not enqueue snapshot, site build, or discovery.

| Timer | Schedule (Pacific) | Job |
|-------|-------------------|-----|
| `aptplans-search` | Daily 04:00 | `discovery` |
| `aptplans-links` | Daily 04:10 | `link_check` |
| `aptplans-pipeline-snapshot` | Daily 04:20 | `pipeline_snapshot` |
| `aptplans-overview-refresh` | Daily 04:30 | `overview_refresh` |
| `aptplans-search-sync` | Daily 04:40 | `search_sync` |
| `aptplans-site-build` | Weekly Sun 05:00 | full `site_build` |
| `aptplans-intake` | Hourly | GitHub intake poll |
| `aptplans-airports` | Monthly 1st 03:00 | `overlay_refresh` |

When `overlay_refresh` completes, the worker chains grant classify, overview, search sync, site build, and discovery (dataset gates apply). Airport `fetch`/`vet` jobs still enqueue scoped `pipeline_snapshot` and `site_build` reactively.

```bash
systemctl list-timers 'aptplans-*'
journalctl -u aptplans-search.service -n 50
journalctl -u aptplans-pipeline-snapshot.service -n 50
docker compose --env-file /home/aptplans/.env.production \
  --env-file /home/aptplans/.env.secrets \
  --env-file /home/aptplans/.env.search \
  -f docker/docker-compose.yml -f docker/docker-compose.prod.yml \
  logs --tail 100 worker
```

The document queue is drained by the Compose `worker` process. `aptplans-pipeline.timer` should stay disabled. Origin Compose uses `.env.production`, `.env.secrets`, and `.env.search` (Meilisearch master key, written once by bootstrap). The worker polls `jobs.sqlite3` about once a minute when idle. Uncaught errors receive a durable retry time and ordinary work dead-letters after three attempts. Publication synchronization retries continuously. One extra job by hand:

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

The origin worker builds every public surface from one pinned domain generation. It stages HTML/RSS/JSON/sitemaps, the visible file projection, and a generation-tagged Meilisearch index under `/var/lib/aptplans/releases/<generation>`, validates them, then atomically advances `current`. Caddy mounts the release root and follows that symlink per request.

Public HTML, RSS, JSON, sitemaps, assets, and `/files/` currently send `no-store`. Review and vet transitions commit desired domain state, then synchronously attempt a complete release before leaving the active queue. A failed projection retries continuously while the prior release remains served; authoritative review state is not rolled back.

Rebuild after a bulk text restore or Meilisearch wipe by enqueueing a full release. Staging extracts missing page sidecars, builds a complete new search index, and swaps it only after validation:

```bash
docker compose --env-file /home/aptplans/.env.production \
  --env-file /home/aptplans/.env.secrets \
  --env-file /home/aptplans/.env.search \
  -f docker/docker-compose.yml -f docker/docker-compose.prod.yml \
  exec -T worker python3 -c 'from pipeline.site_build import enqueue_site_build; from pipeline.status import queue_dir_from_env; enqueue_site_build(queue_dir_from_env())'
```

Worker overlay and queue:

| Path | Contents |
| --- | --- |
| `/var/lib/aptplans/files` | private content-addressed source bytes |
| `/var/lib/aptplans/reject` | 90-day private copies of artifacts that failed a check (not Caddy) |
| `/var/lib/aptplans/text` | gated page JSONL (not served) |
| `/var/lib/aptplans/extractions` | immutable full extraction/OCR manifests and coordinates (not served) |
| `/var/lib/aptplans/search` | Meilisearch data (no host port) |
| `/var/lib/aptplans/catalog` | legacy cutover input and operational snapshots, not entity authority |
| `/var/lib/aptplans/queue` | jobs, domain generations, worker audit, and release journal |
| `/var/lib/aptplans/control` | API commands and human audit |
| `/var/lib/aptplans/releases` | validated immutable site/file generations |
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

Run the frozen clean-cutover benchmark with `make oregon-benchmark`. It hash-checks all eight committed Oregon plan PDFs plus eleven source/reference inputs, extracts the complete PDF set, verifies the official ODAV budget and FAA workbook, checks the Brookings image-only airport pages, reconciles reviewed funding lifecycle totals, and compares semantic digests from two independent empty domain/release roots. Pending replay documents must remain absent from public files, pages, and static search. The report deliberately returns `passed_with_known_gaps` until Brookings OCR quality and the self-hosted model lanes are measured on origin hardware. The faster `python3 -m pipeline.oregon_benchmark` is only a core smoke run; `--require-complete-corpus` fails while those modality gaps remain.

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

CD restarts the worker and returns once Caddy answers. HTML rebuild, FAA overlay refresh, search sync, and LLM warm-up run as **queue jobs** (`pipeline_snapshot`, `site_build`, `overlay_refresh`, `grant_spend`, `budget_enrich`, `overview_refresh`, `search_sync`, `ollama_warm`, `discovery`, `link_check`). `site_build` jobs carry a JSON scope (or `report_type=full`); the worker merges wider scopes into a **pending** ledger row only. Inspect work and verify both databases:

```bash
python3 -c "from pipeline.queue import JobQueue; from pipeline.status import queue_dir_from_env; print(JobQueue(queue_dir_from_env()).counts())"
python3 -m pipeline.ledger_ops integrity
$COMPOSE_PROD logs --tail=80 worker
```

The API mounts `jobs.sqlite3` read-only and writes review commands/human audit only to `/var/lib/aptplans/control/control.sqlite3`; the worker imports commands idempotently. Both use WAL mode. Back them up online and test an offline restore:

```bash
APTPLANS_CONTROL_QUEUE=/var/lib/aptplans/control \
  python3 -m pipeline.ledger_ops --queue-dir /var/lib/aptplans/queue \
  backup /var/backups/aptplans/ledger-$(date +%F)
$COMPOSE_PROD down
APTPLANS_CONTROL_QUEUE=/var/lib/aptplans/control-restore-test \
  python3 -m pipeline.ledger_ops --queue-dir /var/lib/aptplans/restore-test \
  restore /var/backups/aptplans/ledger-YYYY-MM-DD --confirm-offline
APTPLANS_CONTROL_QUEUE=/var/lib/aptplans/control-restore-test \
  python3 -m pipeline.ledger_ops --queue-dir /var/lib/aptplans/restore-test integrity
```

The coordinated pre-production JSONL-to-domain procedure is in [DEPLOYMENT.md](DEPLOYMENT.md). Do not initialize an empty domain ledger while legacy overlays still contain the only catalog copy, and do not enable generation publication before the import and first validated release.

**Discovery priority triage** (`pipeline/discovery_priority.py`) reorders scoped airports before each discovery pass. Every airport is still visited; only the order changes. Tiers (searched sooner → later):

1. Funded (federal, state, or local grants) and not evaluated within the recency window
2. Not evaluated within the recency window
3. Funded but evaluated recently
4. Evaluated recently
5. Prior pass found no plan
6. Already published on site

Within a tier, never-evaluated airports precede stale ones, then higher total grant dollars from the pinned domain generation, then NPIAS, then `(state, lid)`. Env: `APTPLANS_DISCOVERY_FUNDED_FIRST` (default on; legacy `APTPLANS_DISCOVERY_FEDERAL_FIRST`), `APTPLANS_DISCOVERY_RECENCY_DAYS` (default 30).

**System health** (`pipeline/health.py`, generation `dataset_state_json`) is the single readiness model for workers and `GET /v1/status`. Producers mark datasets `building` / `ready` / `failed`; consumers gate on that metadata from the same domain snapshot. `services` covers worker/search/LLM signals and `pipeline` holds queue, outcomes, and rejects.

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

On origin, `https://aptplans.org/review` is the private review API (HTTPS, API key; Caddy `:443` only). CD writes `APTPLANS_REVIEW_TOKEN` from the GitHub Actions secret into `/home/aptplans/.env.secrets` so Compose interpolates it. A laptop or Cursor agent uses the same key in gitignored `.env` with `APTPLANS_REVIEW_URL=https://aptplans.org/review`.

`GET /v1/status` and `GET /v1/logs` are the health loop. `GET /v1/stats`, `GET /v1/outcomes?bucket=uncertain`, `GET /v1/signals`, and `GET /v1/rejects` measure the live mix. `GET /v1/rejects/{sha256}/bytes` copies a failed artifact for local training (still not a publish), and `GET /v1/documents/{id}/bytes` returns a full preserved source only when explicitly requested with operator authentication. `POST /v1/label` records a gold packet without publishing. `PATCH /v1/documents/{id}` must include the reviewed `expected_content_sha256` for a preserved-document publication; a stale hash returns `409`. Accepted requests return `202` and queue the transition. The serial worker applies it and synchronizes `/files/`, search, and generated pages. `make pull-outcomes` writes `data/score/review/` (including `rejects/` files) for scoring work; then `python3 scripts/train_evidence.py --outcomes data/score/review/gold.json`. Merge new official URLs plus labels into `catalog/references/score_gold.json` (no excerpts). Do not log the token or full payload.

Skip filenames and appendices that look like SSI or security-restricted drawings.

## Logs

Compose uses json-file logging with rotation (`max-size` 10m). The worker also appends redacted JSON lines under `/var/lib/aptplans/logs`. `GET https://aptplans.org/review/v1/logs` (API key) is the programmatic view. Pipeline output also lands in the systemd journal for the oneshot timers.
