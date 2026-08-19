The serial worker is a Compose service in the same stack as Caddy and Ollama.

Crawlers identify themselves as `aptplans.org`. Jobs run one at a time. The systemd timer execs into the running worker:

```
docker compose -f docker/docker-compose.yml -f docker/docker-compose.prod.yml exec -T worker python3 pipeline/run_once.py
```

`run_once.py` claims one job, fetches (through PIA SOCKS when `APTPLANS_FETCH_PROXY` is set; fail closed, no origin-IP fallback), honors robots.txt, runs SSI and kind gates, stores `/var/lib/aptplans/files/{sha256}.pdf`, writes overlay completeness, and records a change event when the hash for that document id differs. On origin (`APTPLANS_LLM=1`) it then extracts native PDF text and asks Ollama for one unofficial paragraph. A crash before that leaves the job in `active/` so the next pass retries. GitHub comments use `INTAKE_GITHUB_TOKEN` on the origin IP. On origin, `APTPLANS_REFRESH_AIRPORTS=1`. The worker checks overlays when the container starts, and `run_once.py` checks again before a job. Fetch only if `airports.jsonl` or `grants.jsonl` is missing, empty, or not from this calendar month. Sequential requests, User-Agent `aptplans.org`, a short pause after boot and between hosts. After FAA grant workbooks, origin POSTs grant numbers as `award_ids` to USAspending `spending_by_award` in batches of 50, with a pause between batches. Not a forced re-download on every restart. CI must not run that against live FAA or USAspending. Tests inject tiny zip/xlsx fixtures.

Refresh airports and grants without a document job:

```
python3 -m pipeline.refresh_airports
```

CI must not run that against live FAA. Tests inject tiny zip/xlsx fixtures.

Seed known official PDFs onto the queue without fetching:

```
python3 -m pipeline.discover
```

The worker talks to Ollama at `http://ollama:11434` on the internal `llm` network. Context is 32k tokens. Prefer TOC plus allowlisted slices; if there is no TOC, send sequential viable chunks and reduce. Requests use `keep_alive: -1`. See [Architecture](../docs/ARCHITECTURE.md) (Model calls) and [Operations](../docs/OPERATIONS.md).
