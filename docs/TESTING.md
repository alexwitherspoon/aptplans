# Testing

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
make ci
```

`make ci` runs pytest, then a full `site/build.py` into `dist/` with `APTPLANS_DEV_PREVIEW=1`. `make test` is an alias. `make test-unit` is pytest only (reference fixtures come from `tests/conftest.py`).

CI (`.github/workflows/test.yml`) runs `make ci` on Python 3.12 for pushes and pull requests.

## What is covered now

- Catalog schema includes completeness states and change events
- Reference airports plus 50 state hubs when reference seed is enabled (`APTPLANS_REFERENCE_SEED=1`; pytest sets this in `tests/conftest.py`). Production is the default. CI does not live-fetch FAA NASR, NPIAS, or OurAirports
- NASR public-use airports merge with NPIAS role flags from fixtures; a LID not in NPIAS can still be admitted from intake
- Worker cold start fetches FAA overlays only when files are missing or from a prior month (fixtures in tests; no live FAA or USAspending)
- Reference master plans and ALPs (PDX, TTD, Mulino, plus other regions) validate as `link_only` fixtures against AC 150/5070-6B elements
- Embedded reference PDFs under `catalog/references/files/` match committed SHA-256 values (no network)
- Fetch-hash-store, SSI and newsletter kind gates, robots.txt, native PDF text extract, content fingerprints, and a one-job on-disk queue
- Search query templates, an adaptive search ladder (open query then host lock then fill missing kinds), gated LLM follow-up **query hints** (`evaluate_search_hints`; no invented URLs, no fetches), and gated verification JSON for plans and for finance sources (no live search APIs; finance verify does not return amounts). `make eval-search` replays recorded hits. Gemini escalate parses fixture payloads into destination URLs only (`tests/test_search_client.py`); CI does not call Brave or Gemini. Live hint scoring is `scripts/eval_search_hints.py` (not CI).
- Evidence weights on full official PDFs and hub HTML (`score_gold.json` labels plus `catalog/references/files/` or local `data/score/`). Gold JSON does not store excerpts. Named checks cover identity (LID, name tokens, host), kind (AMP/ALP/chapter/hub/notice/not-plan, PFC, statewide SASP, NEPA), and confirm/explore/publish. `make eval-evidence` replays committed files. `make train-evidence` scores the local cache (not CI). `scripts/train_evidence.py --fit` searches weights. Extracted text is cached under `data/score/extract/`.
- Private review API (`pipeline/review_api.py`, tests in `tests/test_outcomes.py` and `tests/test_reject.py`): worker jobs append `outcomes.jsonl` buckets accepted / uncertain / needs_human / failed. Failed artifacts are stored 90 days under origin `reject/` (not Caddy `/files/`). Origin serves the API at `https://aptplans.org/review` (Caddy `:443`, token except `GET /v1/health`). `GET /v1/status` and `GET /v1/logs` are token-only. `GET /v1/rejects` and `/v1/rejects/{sha}/bytes` are token-only. CI does not start the API. Auth is `Authorization: Bearer` or `X-Api-Key` from gitignored `.env`. `GET /v1/signals` is the compact training dump. `POST /v1/label` is the human gold path. `make pull-outcomes` writes `data/score/review/` (gold, status, logs, plus reject files) for local scoring; `scripts/train_evidence.py --outcomes` includes those labeled cases and auto-labeled gate failures. `scripts/export_outcome_gold.py` prints merge candidates.
- Hub HTML explore: labeled links, SharePoint list/view follow-ups, provenance (`found_on`), and grouping (`part_of`). Live page GETs stay off CI (`scripts/eval_explore.py --catalog` / `--ourairports-sample`, `scripts/eval_search_hits.py`)
- Pipeline stages: signal, explore, confirm, snapshot, vet, publish. Pending snapshots are not listed on the public site. Document dumps do not queue meeting minutes.
- Ollama unofficial notes are opt-in (`APTPLANS_LLM=1` on origin). CI mocks generate and never talks to a model. Optional local `make llm` checks that Bonsai answers; it does not measure KS-6 speed.
- GitHub intake form fields (add, stale, wrong, outdated) close when resolved and mention `@alexwitherspoon` when a human is needed
- Builder writes airport, state, and document pages, RSS, a `/feeds/` index of the feed tree, a sitemap from those published URLs, `status.json`, and bulk dumps. The home page lists recently recorded files plus airports whose listed plans involve growth or decline. Airport pages may include an unofficial fact sheet of the latest listed plan, with NASR filling runway dimensions, elevation, and fuel/storage when those files have no figure (empty fields omitted). Each HTML generate extracts that sheet from cached plan text; overlay `overviews.jsonl` still refreshes at least monthly for search and review. CSS and JS links include a content-hash `?v=` query. Document pages embed a saved PDF or HTML copy when one exists. Local `make dev` may also embed a catalog-gated official PDF from `/files/preview/`. Native pages advertise the matching feed with `rel="alternate"` and include JSON-LD plus Open Graph. A second build with the same inputs does not regenerate.
- Airport funding is grouped federal / state / local / other; state pages render a statewide aviation budget fixture and a LocID award preview
- Compose stack is `site`, `search`, `worker`, and `ollama`. The worker drains the queue continuously (concurrency 1). GitHub intake is at most hourly while idle. Uncaught jobs retry three times, then `needs_human`. Meilisearch and Ollama are off the host network on origin. CI does not start Meilisearch and leaves `MEILI_URL` unset. Local Compose binds `127.0.0.1:11434` for diagnostics.
- Gated native PDF page JSONL and Meilisearch record builders (no live daemon in CI)
- sshd drop-in allows only `aptplans` and disables remote root
- Worker compose interpolates empty defaults for `APTPLANS_FETCH_PROXY`, `INTAKE_GITHUB_TOKEN`, `APTPLANS_SEARCH_KEY`, and `APTPLANS_GEMINI_KEY`. `APTPLANS_SEARCH_STATES` defaults to `OR`. Brave and Gemini billed spend each default to `$25/month`. Review compose interpolates `APTPLANS_REVIEW_TOKEN`. CD copies those from GitHub Actions secrets into origin `.env.secrets` without logging values.
- Official URL checks mark live/moved/dead without live network in CI (injected probes)

## What is not covered yet

Live crawls, live FAA NASR/NPIAS fetches, live USAspending posts, origin disk I/O, and Ollama summaries. Those stay off CI. Do not require the KS-6, origin PDFs, or model weights. A few official reference PDFs are committed for deterministic tests.

## Manual check

```bash
make site
make up
```

`make stack` starts Caddy, Meilisearch, worker, and Ollama locally. `make up` is Caddy only; `/search/query` then falls back to `/data/search.json`. `make down` stops them. `make dev` watches `site/`, `catalog/`, and `pipeline/` and rebuilds `dist/` without Docker.

Open http://127.0.0.1:8080 and confirm the home page, About, and stylesheet load.
