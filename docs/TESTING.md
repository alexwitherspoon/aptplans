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

- Catalog schema loads and includes the completeness states
- Builder writes `index.html`, `about/index.html`, and CSS into an output directory
- Compose stack is `site`, `worker`, and `ollama`, with Ollama off the host network
- Worker one-shot entry returns success while the catalog is empty

## What is not covered yet

Crawlers, hash-verify, RSS, and origin disk I/O. Add tests next to those modules when they exist. Do not require the KS-6, PDFs, or model weights for CI. Ollama config is checked as JSON and Compose isolation only.

## Manual check

```bash
make site
make dev
```

Open http://127.0.0.1:8080 and confirm the home page, About, and stylesheet load.
