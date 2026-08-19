The serial fetch / parse / publish job runs from this directory on the origin host.

Crawlers identify themselves as `aptplans.org`. Jobs run one at a time from a systemd timer:

```
docker compose -f docker/docker-compose.yml -f docker/docker-compose.prod.yml --profile jobs run --rm pipeline
```

See [Architecture](../docs/ARCHITECTURE.md) and [Operations](../docs/OPERATIONS.md).
