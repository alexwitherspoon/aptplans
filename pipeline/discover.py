"""Enqueue official URLs the catalog already knows. Does not fetch."""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlparse, unquote

from catalog.seed import seed_catalog
from pipeline.queue import JobQueue, QueueJob


def looks_like_pdf(url: str) -> bool:
    path = unquote(urlparse(url).path).lower()
    return path.endswith(".pdf")


def seed_reference_fetches(queue: JobQueue, catalog_root: Path) -> int:
    catalog = seed_catalog(catalog_root)
    queued = 0
    for document in catalog.documents:
        if document.completeness != "link_only":
            continue
        if not document.source_url or not looks_like_pdf(document.source_url):
            continue
        queue.enqueue(
            QueueJob(
                kind="fetch",
                document_id=document.id,
                source_url=document.source_url,
                airport_lid=document.airport_lid,
            )
        )
        queued += 1
    return queued


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    queue = JobQueue(Path(os.environ.get("APTPLANS_QUEUE", root / "data" / "queue")))
    count = seed_reference_fetches(queue, root / "catalog")
    print(f"queued {count} reference PDF fetches")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

