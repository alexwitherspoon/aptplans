# Local setup

You need Python 3.12+, Make, and Docker. On macOS Homebrew, `python3.12` is the usual binary. Local development is a Compose stack with a small host footprint: the repo, a venv for tests, and gitignored `data/` binds. Stop and remove containers when you are done.

```bash
git clone https://github.com/alexwitherspoon/aptplans.git
cd aptplans
python3.12 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
make test
```

Useful targets:

| Target | What it does |
| --- | --- |
| `make help` | List targets |
| `make site` | Write `dist/` |
| `make test` | pytest, then a site build |
| `make eval-search` | Replay the adaptive search ladder against committed fixtures (no network, not a publish) |
| `make eval-evidence` | Replay evidence weights against committed full gold sources (no network, not a publish) |
| `make train-evidence` | Score full originals (fixtures plus `data/score/`) and report case/field accuracy |
| `make review-api` | Local outcomes API on 127.0.0.1:8787 (token from `.env`; origin is HTTPS `/review/`) |
| `make pull-outcomes` | Pull origin/local review buckets into `data/score/review` using that key |
| `make dev` | Watch `site/` and `catalog/`, rebuild `dist/`, serve at http://127.0.0.1:8080 |
| `make up` | Build, then local Caddy in Docker on port 8080 |
| `make stack` | Build, then local Caddy, Meilisearch, worker, and Ollama |
| `make down` | Stop local Compose services |
| `make down-clean` | Stop Compose and remove named volumes (Ollama data) |
| `make pipeline` | Run one worker job (no Ollama, no live FAA) |
| `make links` | Check due official URLs (no live FAA) |
| `make model` | Download origin Bonsai 27B GGUF (~3.8 GB) and `ollama create bonsai-27b` |
| `make llm` | Warm local Bonsai and exercise unofficial-note / verify / `run_once` LLM paths |
| `make clean` | Delete `dist/` |

To score discovery locally without publishing, `make eval-search` replays recorded search hits. Live hub GETs (still signals, then confirm candidates only):

```bash
python3 scripts/eval_search_plan.py --lid 4S2 --explore
python3 scripts/eval_search_hints.py
```

Evidence gold stores labels and official URLs only. Full original bytes for training live in `catalog/references/files/` or gitignored `data/score/` after `python3 scripts/fetch_score_sources.py`. Do not paste excerpts into the JSON. `make eval-evidence` replays committed files. After a fetch, `python3 scripts/eval_evidence.py` includes the local cache.

The private review API uses `APTPLANS_REVIEW_TOKEN`. Brave Search uses `APTPLANS_SEARCH_KEY`. Optional Gemini escalate uses `APTPLANS_GEMINI_KEY`. On origin, CD copies those from GitHub Actions secrets into `/home/aptplans/.env.secrets`. On this laptop, paste the same values into a gitignored `.env` in the repo root:

```bash
cp .env.example .env
# paste APTPLANS_REVIEW_TOKEN, APTPLANS_SEARCH_KEY, and APTPLANS_GEMINI_KEY
# PIA_OPENVPN_USER, PIA_OPENVPN_PASSWORD, and optional PIA_SERVER_REGIONS for make stack-egress
# APTPLANS_REVIEW_URL=https://aptplans.org/review
```

Do not use `.env.search` for these keys. That filename is the origin Meilisearch master key. Do not commit `.env`.

Review pull (HTTPS + API key; Cursor agents use the same URL):

```bash
make pull-outcomes
python3 scripts/train_evidence.py --outcomes data/score/review/gold.json
```

`make pull-outcomes` writes `data/score/review/signals.json` (accepted / uncertain / needs_human / failed, no excerpts), labeled gold, `status.json`, `logs.json`, and 90-day reject files under `rejects/` for training against failures next to successes. That is the production feedback loop for scoring work in this IDE. Do not print the token. Caddy serves `/review/` on HTTPS only. `robots.txt` disallows `/review/`. Cloudflare must not cache it.

`--llm` on `eval_search_plan.py` adds one gated Ollama query-hint round when a whole plan or ALP is still missing. The model never hits the search API.

Set `APTPLANS_LIVE_SEARCH=1` and `--provider brave` to hit live Brave Search from a laptop. Production uses `APP_ENV=production` and reads the token from `.env.secrets`. Live overlay search is Oregon-only until you change `APTPLANS_SEARCH_STATES` (default `OR`; use `OR,WA` or `*` to widen):

```bash
python3 scripts/eval_search_plan.py --overlay --provider brave --limit 10
```

Optional `APTPLANS_GEMINI_KEY` plus `--escalate` runs one Gemini packet search after Brave stalls. Do not scrape result pages. `--enqueue` writes explore/fetch jobs into `data/queue`; `make pipeline` snapshots them as `pending`. They do not appear on the public pages until vet.

`make dev` serves `dist/` at http://127.0.0.1:8080 without Docker and rebuilds when files under `site/` or `catalog/` change (reload the browser to see it). Hashed copies under `data/files` are served at `/files/`. Official PDFs that cannot be framed are fetched once into `data/files/preview/` (catalog URLs only) so the document page can show them. Prefer `make up` or `make stack` when you want the origin-shaped stack.

## Docker

```bash
make site
make up
```

`make up` is Caddy only. Type two characters in search (for example `PD`) and suggestions come from `/data/search.json`. Page-text hits need `make stack` so Meilisearch is up, then a refresh so `dist/` includes `/js/suggest.js`.

`make stack` starts the same four services origin runs. Worker and Meilisearch stay off host ports. Local Ollama is on the internal `llm` network **and** bound to `127.0.0.1:11434` so you can diagnose from the host (`curl http://127.0.0.1:11434/api/tags`). Origin does not publish that port. Bind mounts use `data/files`, `data/queue`, `data/catalog`, `data/text`, `data/search`, and `data/models` (gitignored). Local worker does **not** set `APTPLANS_REFRESH_AIRPORTS` or `APTPLANS_LLM`. Do not live-fetch FAA from a laptop. `make stack-egress` is the same stack plus a Gluetun PIA VPN container; the worker scrapes through `http://egress:8888` (needs `PIA_OPENVPN_*` in `.env`). `make model` downloads the same 1-bit Bonsai 27B GGUF origin uses and imports it into local Ollama. `make llm` warms Bonsai and runs unofficial-note, plan verify, finance verify, and one `run_once` preserve with `APTPLANS_LLM=1` against a fixture PDF. That proves the code paths, not origin speed: a laptop CPU is not the KS-6 (EPYC, NUMA cpuset, 12 llama threads). Smoke turns thinking off and may shrink context and `num_predict` so the laptop can finish. Origin leaves the caps unset and runs the full 32k Modelfile with thinking off on `/api/generate`. Do not size jobs, timeouts, or SLAs from laptop decode time. `python3 scripts/local_ollama.py compare` runs the same prompts with thinking on vs off. Think-on leaves `num_predict` unset so the completion can finish; that is a quality check, not a throughput check. Laptop duration is not origin duration.

```bash
make down          # stop containers
make down-clean    # also drop the Ollama named volume
```

Compose files live in `docker/`. `docker-compose.override.yml` is gitignored; copy the example if you need host-specific volume paths.

```bash
cp docker/docker-compose.override.yml.example docker/docker-compose.override.yml
```

## What not to put in this clone

This GitHub repository holds code and test fixtures only. PDFs, model weights, extracted full text, NASR overlays, and compiled statute texts stay out of git. Local crawls write under `data/files/`, `data/queue/`, `data/catalog/`, and `data/text/`. Local Meilisearch data goes under `data/search/`. Local model weights go under `data/models/`. Do not commit those paths.
