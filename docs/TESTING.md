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
- Fetch-hash-store, SSI and newsletter kind gates, and a one-job on-disk queue
- GitHub intake form fields (add, stale, wrong, outdated) close when resolved and mention `@alexwitherspoon` when a human is needed
- Builder writes airport, state, and document pages, RSS, sitemap, `status.json`, and bulk dumps
- Compose stack is `site`, `worker`, and `ollama`, with Ollama off the host network
- sshd drop-in allows only `aptplans` and disables remote root
- Worker compose interpolates empty defaults for `APTPLANS_FETCH_PROXY` and `INTAKE_GITHUB_TOKEN`

## What is not covered yet

Live crawls, live FAA NASR/NPIAS fetches, live USAspending posts, origin disk I/O, and Ollama summaries. Those stay off CI. Do not require the KS-6, origin PDFs, or model weights. A few official reference PDFs are committed for deterministic tests.

## Manual check

```bash
make site
make dev
```

Open http://127.0.0.1:8080 and confirm the home page, About, and stylesheet load.
