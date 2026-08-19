The serial worker is a Compose service in the same stack as Caddy and Ollama.

Crawlers identify themselves as `aptplans.org`. Jobs run one at a time. The systemd timer execs into the running worker:

```
docker compose -f docker/docker-compose.yml -f docker/docker-compose.prod.yml exec -T worker python3 pipeline/run_once.py
```

The worker talks to Ollama at `http://ollama:11434` on the internal `llm` network. Context is 32k tokens. Prefer TOC plus allowlisted slices; if there is no TOC, send sequential viable chunks and reduce. Requests use `keep_alive: -1`. See [Architecture](../docs/ARCHITECTURE.md) (Model calls) and [Operations](../docs/OPERATIONS.md).
