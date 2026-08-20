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

`make dev` serves `dist/` at http://127.0.0.1:8080 without Docker and rebuilds when files under `site/` or `catalog/` change (reload the browser to see it). Prefer `make up` or `make stack` when you want the origin-shaped stack.

## Docker

```bash
make site
make up
```

`make up` is Caddy only. Type two characters in search (for example `PD`) and suggestions come from `/data/search.json`. Page-text hits need `make stack` so Meilisearch is up, then a refresh so `dist/` includes `/js/suggest.js`.

`make stack` starts the same four services origin runs. Worker and Meilisearch stay off host ports. Local Ollama is on the internal `llm` network **and** bound to `127.0.0.1:11434` so you can diagnose from the host (`curl http://127.0.0.1:11434/api/tags`). Origin does not publish that port. Bind mounts use `data/files`, `data/queue`, `data/catalog`, `data/text`, `data/search`, and `data/models` (gitignored). Local worker does **not** set `APTPLANS_REFRESH_AIRPORTS` or `APTPLANS_LLM`. Do not live-fetch FAA from a laptop. `make model` downloads the same 1-bit Bonsai 27B GGUF origin uses and imports it into local Ollama. `make llm` warms Bonsai and runs unofficial-note, plan verify, finance verify, and one `run_once` preserve with `APTPLANS_LLM=1` against a fixture PDF. That proves the code paths, not origin speed: a laptop CPU is not the KS-6 (EPYC, NUMA cpuset, 12 llama threads). Smoke turns thinking off and may shrink context and `num_predict` so the laptop can finish. Origin leaves the caps unset and runs the full 32k Modelfile with thinking off on `/api/generate`. Do not size jobs, timeouts, or SLAs from laptop decode time. `python3 scripts/local_ollama.py compare` runs the same prompts with thinking on vs off. Think-on leaves `num_predict` unset so the completion can finish; that is a quality check, not a throughput check. Laptop duration is not origin duration.

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
