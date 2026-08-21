"""Meilisearch client. Derived index; origin JSONL and overlay are the source of truth."""

from __future__ import annotations

import argparse
import json
import logging
import os
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from catalog.grants import grant_title
from catalog.models import Airport, Document, Grant, State
from catalog.seed import seed_catalog
from catalog.store import Catalog
from pipeline.parse import extract_pages
from pipeline.refresh import ROOT, overlay_dir_from_env
from pipeline.textstore import pages_path, read_pages, text_dir, write_pages

log = logging.getLogger("aptplans.search")

INDEX = "aptplans"
BATCH = 400

SETTINGS = {
    "searchableAttributes": ["lid", "title", "text", "summary"],
    "displayedAttributes": [
        "id",
        "type",
        "title",
        "url",
        "state",
        "lid",
        "kind",
        "completeness",
        "outlook",
        "page",
        "document_id",
        "summary",
        "text",
    ],
    "filterableAttributes": ["type", "state", "kind", "completeness", "document_id", "outlook"],
    "pagination": {"maxTotalHits": 100},
}

OUTLOOK_BANDS = ("declining", "growing", "maintaining")


def meili_url() -> str:
    return os.environ.get("MEILI_URL", "").strip().rstrip("/")


def meili_key() -> str:
    return os.environ.get("MEILI_MASTER_KEY", "").strip()


def configured() -> bool:
    return bool(meili_url() and meili_key())


def _request(method: str, path: str, payload: dict | list | None = None, timeout: int = 60):
    url = f"{meili_url()}{path}"
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(url, data=data, method=method)
    request.add_header("Authorization", f"Bearer {meili_key()}")
    if data is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read()
    except HTTPError as exc:
        detail = exc.read()[:300]
        raise RuntimeError(f"meilisearch {method} {path} failed: {exc.code} {detail!r}") from exc
    if not body:
        return {}
    return json.loads(body.decode("utf-8"))


def _wait_task(uid: int, timeout: float = 120.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        task = _request("GET", f"/tasks/{uid}")
        status = task.get("status")
        if status == "succeeded":
            return
        if status == "failed":
            raise RuntimeError(f"meilisearch task {uid} failed: {task.get('error')}")
        time.sleep(0.05)
    raise RuntimeError(f"meilisearch task {uid} timed out")


def _enqueue(method: str, path: str, payload: dict | list | None = None) -> None:
    body = _request(method, path, payload)
    uid = body.get("taskUid")
    if uid is not None:
        _wait_task(int(uid))


def outlook_band(overview: dict | None) -> str | None:
    if not overview or not isinstance(overview.get("trajectory"), dict):
        return None
    band = str(overview["trajectory"].get("band") or "").strip().lower()
    return band if band in OUTLOOK_BANDS else None


def outlook_search_text(band: str | None) -> str:
    if not band:
        return ""
    return f"planning outlook {band}"


def airport_record(
    airport: Airport,
    overview: dict | None = None,
    *,
    outlook: str | None = None,
) -> dict:
    band = outlook if outlook in OUTLOOK_BANDS else outlook_band(overview)
    return {
        "id": f"airport-{airport.lid}",
        "type": "airport",
        "title": f"{airport.lid} {airport.name}",
        "url": f"/airports/{airport.lid}/",
        "state": airport.state,
        "lid": airport.lid,
        "kind": None,
        "completeness": None,
        "outlook": band,
        "summary": "",
        "text": " ".join(
            part
            for part in (
                airport.city,
                airport.icao,
                airport.iata,
                airport.npias_role,
                outlook_search_text(band),
            )
            if part
        ),
        "document_id": None,
        "page": None,
    }


def state_record(state: State) -> dict:
    return {
        "id": f"state-{state.code}",
        "type": "state",
        "title": state.name,
        "url": f"/states/{state.code}/",
        "state": state.code,
        "lid": None,
        "kind": None,
        "completeness": None,
        "summary": "",
        "text": " ".join(part for part in (state.code, state.agency, "budget law awards") if part),
        "document_id": None,
        "page": None,
    }


def document_record(document: Document) -> dict:
    return {
        "id": f"document-{document.id}",
        "type": "document",
        "title": document.title or document.id,
        "url": f"/documents/{document.id}/",
        "state": document.state,
        "lid": document.airport_lid,
        "kind": document.kind,
        "completeness": document.completeness,
        "summary": document.summary or "",
        "text": " ".join(
            part
            for part in (document.id, document.kind, document.edition, document.summary)
            if part
        ),
        "document_id": document.id,
        "page": None,
    }


def page_record(document: Document, page: int, text: str) -> dict:
    pdf = document.preserved_url or ""
    url = f"{pdf}#page={page}" if pdf else f"/documents/{document.id}/"
    return {
        "id": f"page-{document.id}-{page}",
        "type": "page",
        "title": f"{document.title or document.id} p. {page}",
        "url": url,
        "state": document.state,
        "lid": document.airport_lid,
        "kind": document.kind,
        "completeness": document.completeness,
        "summary": document.summary or "",
        "text": text,
        "document_id": document.id,
        "page": page,
    }


def funding_record(grant: Grant) -> dict:
    number = grant.grant_number or f"{grant.airport_lid}-{grant.fiscal_year}-{grant_title(grant.description)}"
    slug = "".join(ch if ch.isalnum() else "-" for ch in number)[:80]
    return {
        "id": f"funding-{slug}",
        "type": "funding",
        "title": f"{grant.airport_lid} {grant_title(grant.description)}",
        "url": f"/airports/{grant.airport_lid}/#funding",
        "state": grant.state,
        "lid": grant.airport_lid,
        "kind": "funding",
        "completeness": None,
        "summary": "",
        "text": " ".join(
            part
            for part in (grant.grant_number, grant.description, " ".join(grant.programs or []))
            if part
        ),
        "document_id": None,
        "page": None,
    }


def document_from_updates(updates: dict) -> Document:
    return Document.from_dict(
        {
            "id": updates["id"],
            "kind": updates.get("kind") or "other",
            "source_url": updates.get("source_url") or "https://aptplans.org/",
            "completeness": updates.get("completeness") or "complete",
            "airport_lid": updates.get("airport_lid"),
            "state": updates.get("state"),
            "title": updates.get("title"),
            "edition": updates.get("edition"),
            "summary": updates.get("summary"),
            "preserved_url": updates.get("preserved_url"),
            "content_sha256": updates.get("content_sha256"),
        }
    )


def ensure_index() -> None:
    try:
        _enqueue("POST", "/indexes", {"uid": INDEX, "primaryKey": "id"})
    except RuntimeError as exc:
        if "already exists" not in str(exc).lower() and " 409 " not in str(exc):
            raise
    _enqueue("PATCH", f"/indexes/{INDEX}/settings", SETTINGS)


def has_page_docs() -> bool:
    body = _request(
        "POST",
        f"/indexes/{INDEX}/search",
        {"q": "", "filter": "type = page", "limit": 1},
    )
    hits = body.get("estimatedTotalHits") or len(body.get("hits") or [])
    return int(hits) > 0


def upsert(records: list[dict]) -> None:
    if not records:
        return
    for start in range(0, len(records), BATCH):
        _enqueue("POST", f"/indexes/{INDEX}/documents", records[start : start + BATCH])


def delete_pages(document_id: str) -> None:
    _enqueue(
        "POST",
        f"/indexes/{INDEX}/documents/delete",
        {"filter": f'type = page AND document_id = "{document_id}"'},
    )


def sync_catalog(catalog: Catalog) -> None:
    records = [
        airport_record(airport, catalog.overview_for(airport.lid))
        for airport in catalog.airports
    ]
    records += [state_record(state) for state in catalog.states]
    records += [document_record(document) for document in catalog.documents]
    records += [funding_record(grant) for grant in catalog.grants if grant.airport_lid]
    upsert(records)
    log.info("search catalog records upserted=%s", len(records))


def sync_airports(catalog: Catalog, lids: list[str] | None = None) -> None:
    """Refresh airport Meilisearch docs after fact sheets change."""
    if not configured():
        return
    wanted = set(lids) if lids else None
    records = [
        airport_record(airport, catalog.overview_for(airport.lid))
        for airport in catalog.airports
        if wanted is None or airport.lid in wanted
    ]
    upsert(records)


def backfill_text(
    catalog: Catalog,
    files_dir: Path | None = None,
    dest: Path | None = None,
) -> int:
    """Extract page JSONL for preserved PDFs that have no sidecar yet."""
    files_dir = files_dir or Path(os.environ.get("APTPLANS_FILES", ROOT / "data" / "files"))
    dest = dest or text_dir(files_dir)
    written = 0
    for document in catalog.documents:
        if document.kind == "notice" or not document.content_sha256:
            continue
        if pages_path(dest, document.content_sha256).is_file():
            continue
        pdf = files_dir / f"{document.content_sha256}.pdf"
        if not pdf.is_file():
            continue
        write_pages(dest, document.content_sha256, extract_pages(pdf.read_bytes()))
        written += 1
    if written:
        log.info("wrote %s text sidecars from preserved PDFs", written)
    return written


def sync_pages(catalog: Catalog, dest: Path | None = None) -> None:
    dest = dest or text_dir()
    records: list[dict] = []
    for document in catalog.documents:
        if document.kind == "notice" or not document.content_sha256:
            continue
        for row in read_pages(dest, document.content_sha256):
            records.append(page_record(document, int(row["page"]), str(row["text"])))
            if len(records) >= BATCH:
                upsert(records)
                records = []
    upsert(records)
    log.info("search page records upserted")


def upsert_preserved(updates: dict, pages: list[dict]) -> None:
    if not configured():
        return
    document = document_from_updates(updates)
    ensure_index()
    upsert([document_record(document)])
    if document.kind == "notice":
        return
    delete_pages(document.id)
    upsert([page_record(document, int(row["page"]), str(row["text"])) for row in pages])


def reindex(
    catalog: Catalog | None = None,
    overlay_dir: Path | None = None,
    dest: Path | None = None,
) -> None:
    overlay = overlay_dir or overlay_dir_from_env()
    catalog = catalog or seed_catalog(ROOT / "catalog", overlay_dir=overlay)
    ensure_index()
    backfill_text(catalog, dest=dest)
    _enqueue("POST", f"/indexes/{INDEX}/documents/delete-all")
    sync_catalog(catalog)
    sync_pages(catalog, dest)


def boot_sync() -> None:
    if not configured():
        log.info("search index off (MEILI_URL or MEILI_MASTER_KEY unset)")
        return
    overlay = overlay_dir_from_env()
    catalog = seed_catalog(ROOT / "catalog", overlay_dir=overlay)
    last_error = None
    for attempt in range(5):
        try:
            ensure_index()
            last_error = None
            break
        except (RuntimeError, URLError, OSError, TimeoutError) as exc:
            last_error = exc
            log.info("search not ready (%s); retry", exc)
            time.sleep(2)
    if last_error is not None:
        raise last_error
    sync_catalog(catalog)
    if not has_page_docs():
        log.info("search index has no page hits; indexing extracted text")
        backfill_text(catalog)
        sync_pages(catalog)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Rebuild the Meilisearch index from overlay and text sidecars")
    parser.add_argument("--reindex", action="store_true")
    args = parser.parse_args()
    if not configured():
        log.info("MEILI_URL or MEILI_MASTER_KEY unset; skip")
        return 0
    if args.reindex:
        reindex()
        return 0
    boot_sync()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
