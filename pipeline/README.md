The serial worker is a Compose service in the same stack as Caddy, Meilisearch, and Ollama.

The worker drains the WAL-mode SQLite job ledger with airport-scoped leases (default one airport at a time: finish PDX explore/fetch/vet before starting TTD). `APTPLANS_AIRPORT_CONCURRENCY` raises how many distinct airports may be leased; `APTPLANS_JOB_LEASE_SECONDS` controls the renewable lease (default 300 seconds), and the worker heartbeats long jobs. `APTPLANS_JOB_PAUSE_SEC` (default 2) sleeps after each successful job to pace Brave and Ollama. Idle poll is `APTPLANS_WORKER_IDLE_SEC` (default 60). Periodic maintenance is enqueued by **systemd timers** (discovery, link check, snapshot, overview refresh, search sync, weekly site build, hourly GitHub intake). **Boot** (worker container start) only warms Ollama and refreshes stale FAA overlays. Timers and `python3 -m pipeline.boot_jobs` enqueue jobs only. After airport jobs complete, `pipeline_snapshot` and scoped `site_build` jobs refresh `pipeline.json` and static HTML (`site/build.py` skips generate when inputs are unchanged). Operator review commands enter through a separate SQLite control inbox and are imported only by the worker.

Manual extra job:

```
docker compose -f docker/docker-compose.yml -f docker/docker-compose.prod.yml exec -T worker python3 pipeline/run_once.py
```

`run_once.py` claims one leased job, fetches through the fail-closed VPN proxy, applies SSI and kind gates, stores approved source bytes privately by SHA-256, writes gated page sidecars, and commits an immutable domain generation containing completeness, hashes, and change events. A site-build job pins one generation, stages all static/file/search projections, validates them, and atomically activates them. A projection crash leaves the prior release served and retries the desired generation. A text or drawing change is a content version; a wrapper-only replacement records unchanged content. On origin (`APTPLANS_LLM=1`) Ollama produces an unofficial paragraph from extracted native text. Monthly FAA and USAspending refreshes use generation dataset readiness instead of operational JSONL file presence.

Search-engine query templates are in `pipeline/queries.py`, grouped by target (plan, statewide budget, state award list, CIP, PFC, law). They do not hit the network. `pipeline/search_plan.py` runs a short series: open web (name + LID + master plan), then `site:` on hosts the hits used, then fill a missing ALP or bound AMP. When a whole plan or ALP is still missing, one gated model pass may propose extra queries from those packets. Hits stay signals. The model does not search, browse, or invent URLs. Origin may call Brave Search behind `APTPLANS_SEARCH_KEY` (default provider brave). After that ladder stalls, one Gemini grounded prompt may add destination-URL packets if `APTPLANS_GEMINI_KEY` is set. CI must not. Replay fixtures with `make eval-search`. GET hubs with `python3 scripts/eval_search_plan.py --lid 4S2 --explore`. `--enqueue` plus `make pipeline` snapshots into `data/` with `review_status: pending`; that is not a publish. Metered calls are tracked in overlay `search_meter.json` (`pipeline/meter.py`): a local ledger per provider with budget caps derived from `APTPLANS_BRAVE_*` and `APTPLANS_GEMINI_*`, plus Brave `X-RateLimit-*` cloud observations when available. When the local fuse is spent, `wait_for_meter_budget()` sleeps until the monthly window resets (or Brave's reset header) before the next live search. Those keys are for crawl discovery, not the public site. Visitors query Meilisearch through Caddy. Gated verification runs only on already-fetched excerpts and must return JSON. `verify_candidate` classifies plans and notices. `verify_finance` classifies budgets, award lists, CIPs, and similar; it does not return dollar amounts. The model does not search.

Rebuild the visitor index through a complete release:

```
python3 -c 'from pipeline.site_build import enqueue_site_build; from pipeline.status import queue_dir_from_env; enqueue_site_build(queue_dir_from_env())'
```

A daily link check (`python3 -m pipeline.check --enqueue` on the timer; `python3 -m pipeline.check` for a manual pass) HEADs due official URLs (tiny GET if HEAD is unsupported), one host at a time. 404/410/451 mark the URL dead. A preserved copy stays; completeness becomes `preserved_only`. No copy becomes `missing`, then mirrors and optional Wayback CDX (`APTPLANS_WAYBACK=1`) may queue a fetch. Redirects to a new path mark `moved` and queue a fetch. 5xx is not dead. Live URLs are due again after 7 days; dead after 30. CI injects probes and must not hit the live web. `APTPLANS_CHECK_LIMIT` caps how many URLs one pass probes (default 40).

Refresh airports, grants, and unofficial airport fact sheets without a document job (manual; monthly timer enqueues `overlay_refresh`):

```
python3 -m pipeline.refresh_airports
python3 -m pipeline.boot_jobs --monthly   # enqueue only
```

Fact-sheet entities generate when missing and again if their dataset state is stale, for search and the review API. Airport HTML extracts the same sheet on every release build (cached PDF text plus regex; no model). Listed files first; NASR fills runway dimensions, elevation, and fuel/storage when those files have no figure. Grants stay on Funding. CI must not run that against live FAA. Tests inject tiny zip/xlsx fixtures.

Seed known official PDFs onto the queue without fetching. Reference seed must be enabled (`APTPLANS_DEV_PREVIEW=1` or `APTPLANS_REFERENCE_SEED=1`); production no-ops by default:

```
python3 -m pipeline.discover
```

The worker talks to Ollama at `http://ollama:11434` on the internal `llm` network. Context is 32k tokens per generate() call. The worker reads the whole file, then sends sequential chunks and reduces. Requests use `keep_alive: -1` and `think: false`. Origin runs this on the KS-6 CPU pin and can wait; a laptop with the same GGUF is slower and is not a throughput proxy. See [Architecture](../docs/ARCHITECTURE.md) (Model calls) and [Operations](../docs/OPERATIONS.md).
