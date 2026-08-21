"""Enqueue official URLs the catalog already knows. Does not fetch."""

from __future__ import annotations

import os
from pathlib import Path

from catalog.seed import seed_catalog
from pipeline.gates import looks_like_pdf
from pipeline.queue import JobQueue, QueueJob
from pipeline.search_scope import in_search_scope, parse_search_states


def seed_explore_hubs(queue: JobQueue, catalog_root: Path, overlay_dir: Path | None = None) -> int:
    """Queue airport website HTML as explore jobs. PDFs are not required.

    Not called from ``main``. A bulk NASR website crawl would snapshot every
    homepage; hubs are seeded from signals (search, intake, a chosen website).
    Live scope follows APTPLANS_SEARCH_STATES (default Oregon).
    """
    catalog = seed_catalog(catalog_root, overlay_dir=overlay_dir)
    allowed = parse_search_states()
    queued = 0
    seen: set[str] = set()
    for airport in catalog.airports:
        if not in_search_scope(airport.state, allowed):
            continue
        website = (airport.website or "").strip()
        if not website.startswith("http") or website in seen:
            continue
        seen.add(website)
        queue.enqueue(
            QueueJob(
                kind="explore",
                document_id=f"{airport.lid.lower()}-site",
                source_url=website,
                airport_lid=airport.lid,
                state=airport.state,
                suggested_kind="other",
                found_on=website,
            )
        )
        queued += 1
    return queued


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

