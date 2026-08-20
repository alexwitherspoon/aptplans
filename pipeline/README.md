The serial worker is a Compose service in the same stack as Caddy, Meilisearch, and Ollama.

The worker process drains the on-disk queue with concurrency 1: one fetch or check job, then the next when it finishes. Idle poll is `APTPLANS_WORKER_IDLE_SEC` (default 60). GitHub intake is listed at most hourly while the disk queue is empty (`APTPLANS_INTAKE_IDLE_SEC`, default 3600), and not under the flock. Crawlers identify themselves as `aptplans.org`. A crash leaves the job in `active/` so the loop retries it. After three uncaught failures the job completes as `needs_human`. Daily link checks and the monthly FAA refresh take the same flock so they wait instead of overlapping Ollama or overlay writes. The HTML rebuild runs after that lock drops, and `site/build.py` skips generate when the catalog and templates match the last output.

Manual extra job:

```
docker compose -f docker/docker-compose.yml -f docker/docker-compose.prod.yml exec -T worker python3 pipeline/run_once.py
```

`run_once.py` claims one job, fetches (through PIA SOCKS when `APTPLANS_FETCH_PROXY` is set; fail closed, no origin-IP fallback), honors robots.txt, runs SSI and kind gates, stores `/var/lib/aptplans/files/{sha256}.pdf`, writes gated native page text under `/var/lib/aptplans/text/`, writes overlay completeness, fingerprints text and embedded images, and records a change event when the byte hash for that document id differs. A text or drawing change is a content version; a wrapper-only replacement is stored with a note that the content is unchanged. Same content at a new URL reuses the record as a mirror. A later full plan or ALP for the same airport sets `supersedes`. On origin (`APTPLANS_LLM=1`) it then extracts native PDF text and asks Ollama for one unofficial paragraph. After overlay write it upserts Meilisearch (catalog fields plus page text). A crash before that leaves the job in `active/` so the next pass retries. GitHub comments use `INTAKE_GITHUB_TOKEN` on the origin IP. On origin, `APTPLANS_REFRESH_AIRPORTS=1`. The worker checks overlays when the container starts. The monthly timer does the same. Document jobs do not. Fetch only if `airports.jsonl` or `grants.jsonl` is missing, empty, or not from this calendar month. Sequential requests, User-Agent `aptplans.org`, a short pause after boot and between hosts. After FAA grant workbooks, origin POSTs grant numbers as `award_ids` to USAspending `spending_by_award` in batches of 50, with a pause between batches. Not a forced re-download on every restart. CI must not run that against live FAA or USAspending. Tests inject tiny zip/xlsx fixtures.

Search-engine query templates are in `pipeline/queries.py`, grouped by target (plan, statewide budget, state award list, CIP, PFC, law). They do not hit the network. Origin may later call one search API behind `APTPLANS_SEARCH_KEY`. CI must not. That key is for crawl discovery, not the public site. Visitors query Meilisearch through Caddy. Gated verification runs only on already-fetched excerpts and must return JSON. `verify_candidate` classifies plans and notices. `verify_finance` classifies budgets, award lists, CIPs, and similar; it does not return dollar amounts. The model does not search.

Rebuild the visitor index:

```
python3 -m pipeline.search --reindex
```

A daily link check (`python3 -m pipeline.check`) HEADs due official URLs (tiny GET if HEAD is unsupported), one host at a time. 404/410/451 mark the URL dead. A preserved copy stays; completeness becomes `preserved_only`. No copy becomes `missing`, then mirrors and optional Wayback CDX (`APTPLANS_WAYBACK=1`) may queue a fetch. Redirects to a new path mark `moved` and queue a fetch. 5xx is not dead. Live URLs are due again after 7 days; dead after 30. CI injects probes and must not hit the live web. `APTPLANS_CHECK_LIMIT` caps how many URLs one pass probes (default 40).

Refresh airports and grants without a document job:

```
python3 -m pipeline.refresh_airports
```

CI must not run that against live FAA. Tests inject tiny zip/xlsx fixtures.

Seed known official PDFs onto the queue without fetching:

```
python3 -m pipeline.discover
```

The worker talks to Ollama at `http://ollama:11434` on the internal `llm` network. Context is 32k tokens. Prefer TOC plus allowlisted slices; if there is no TOC, send sequential viable chunks and reduce. Requests use `keep_alive: -1` and `think: false`. Origin runs this on the KS-6 CPU pin; a laptop with the same GGUF is slower and is not a throughput proxy. See [Architecture](../docs/ARCHITECTURE.md) (Model calls) and [Operations](../docs/OPERATIONS.md).
