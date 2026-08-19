# Local setup

You need Python 3.12+ and Make. Docker is optional and only required for the Caddy path. On macOS Homebrew, `python3.12` is the usual binary.

```bash
git clone https://github.com/alexwitherspoon/aptplans.git
cd aptplans
python3.12 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
make test
make site
make dev
```

`make dev` serves `dist/` at http://127.0.0.1:8080

Useful targets:

| Target | What it does |
| --- | --- |
| `make help` | List targets |
| `make site` | Write `dist/` |
| `make test` | pytest, then a site build |
| `make dev` | Build and serve with Python |
| `make up` | Build, then local Caddy in Docker on port 8080 |
| `make down` | Stop local Compose services |
| `make pipeline` | Run one worker job (no Ollama) |
| `make clean` | Delete `dist/` |

## Docker (optional)

```bash
make site
make up
```

Compose files live in `docker/`. `docker-compose.override.yml` is gitignored; copy the example if you need host-specific volume paths.

```bash
cp docker/docker-compose.override.yml.example docker/docker-compose.override.yml
```

## What not to put in this clone

PDFs, model weights, and extracted full text stay out of git. Local crawls, if you run them, should write under a directory that `.gitignore` already covers (`data/files/`, `data/queue/`, `data/catalog/`, or `*.pdf`). Airport identity from FAA lives in `data/catalog/airports.jsonl` when you refresh locally (`python3 -m pipeline.refresh_airports`); do not commit it. Compose defines `site`, `worker`, and `ollama`. `make up` starts only `site`. Origin CD starts the full stack.
