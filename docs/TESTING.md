# Testing

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
make test
```

`make test` runs pytest, then a full `site/build.py` into `dist/`.

CI (`.github/workflows/test.yml`) does the same on Python 3.12 for pushes and pull requests.

## What is covered now

- Catalog schema includes completeness states and change events
- Reference airports plus 50 state hubs (CI does not live-fetch FAA NASR or NPIAS)
- NASR public-use airports merge with NPIAS role flags from fixtures; a LID not in NPIAS can still be admitted from intake
- Worker cold start fetches FAA overlays only when files are missing or from a prior month (fixtures in tests; no live FAA or USAspending)
- Reference master plans and ALPs (PDX, TTD, Mulino, plus other regions) validate as `link_only` fixtures against AC 150/5070-6B elements
- Embedded reference PDFs under `catalog/references/files/` match committed SHA-256 values (no network)
- Fetch-hash-store, SSI and newsletter kind gates, robots.txt, native PDF text extract, content fingerprints, and a one-job on-disk queue
- Search query templates and gated verification JSON for plans and for finance sources (no live search APIs; finance verify does not return amounts)
- Ollama unofficial notes are opt-in (`APTPLANS_LLM=1` on origin). CI mocks generate and never talks to a model. Optional local `make llm` checks that Bonsai answers; it does not measure KS-6 speed.
- GitHub intake form fields (add, stale, wrong, outdated) close when resolved and mention `@alexwitherspoon` when a human is needed
- Builder writes airport, state, and document pages, RSS, a `/feeds/` index of the feed tree, a sitemap from those published URLs, `status.json`, and bulk dumps. Native pages advertise the matching feed with `rel="alternate"` and include JSON-LD plus Open Graph. A second build with the same inputs does not regenerate.
- Airport funding is grouped federal / state / local / other; state pages render a statewide aviation budget fixture and a LocID award preview
- Compose stack is `site`, `search`, `worker`, and `ollama`. The worker drains the queue continuously (concurrency 1). GitHub intake is at most hourly while idle. Uncaught jobs retry three times, then `needs_human`. Meilisearch and Ollama are off the host network on origin. CI does not start Meilisearch and leaves `MEILI_URL` unset. Local Compose binds `127.0.0.1:11434` for diagnostics.
- Gated native PDF page JSONL and Meilisearch record builders (no live daemon in CI)
- sshd drop-in allows only `aptplans` and disables remote root
- Worker compose interpolates empty defaults for `APTPLANS_FETCH_PROXY` and `INTAKE_GITHUB_TOKEN`
- Official URL checks mark live/moved/dead without live network in CI (injected probes)

## What is not covered yet

Live crawls, live FAA NASR/NPIAS fetches, live USAspending posts, origin disk I/O, and Ollama summaries. Those stay off CI. Do not require the KS-6, origin PDFs, or model weights. A few official reference PDFs are committed for deterministic tests.

## Manual check

```bash
make site
make up
```

`make stack` starts Caddy, Meilisearch, worker, and Ollama locally. `make up` is Caddy only; `/search/query` then falls back to `/data/search.json`. `make down` stops them. `make dev` watches `site/` and `catalog/` and rebuilds `dist/` without Docker.

Open http://127.0.0.1:8080 and confirm the home page, About, and stylesheet load.
