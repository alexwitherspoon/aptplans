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
from catalog.models import Airport, Document, Grant, State, visible_on_site
from catalog.seed import seed_catalog
from catalog.store import Catalog
from pipeline.refresh import ROOT, overlay_dir_from_env
from pipeline.textstore import (
    projection_matches,
    read_pages,
    text_dir,
    write_pages,
)

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
        "_generation",
    ],
    "filterableAttributes": [
        "type",
        "state",
        "kind",
        "completeness",
        "document_id",
        "outlook",
        "_generation",
    ],
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


def _wait_task(
    uid: int,
    timeout: float = 120.0,
    *,
    ignored_error_codes: frozenset[str] = frozenset(),
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        task = _request("GET", f"/tasks/{uid}")
        status = task.get("status")
        if status == "succeeded":
            return
        if status == "failed":
            error = task.get("error") or {}
            if str(error.get("code") or "") in ignored_error_codes:
                return
            raise RuntimeError(f"meilisearch task {uid} failed: {error}")
        time.sleep(0.05)
    raise RuntimeError(f"meilisearch task {uid} timed out")


def _enqueue(
    method: str,
    path: str,
    payload: dict | list | None = None,
    *,
    ignored_error_codes: frozenset[str] = frozenset(),
) -> None:
    body = _request(method, path, payload)
    uid = body.get("taskUid")
    if uid is not None:
        _wait_task(
            int(uid),
            ignored_error_codes=ignored_error_codes,
        )


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
            "review_status": updates.get("review_status") or "pending",
        }
    )


def ensure_index(index: str = INDEX) -> None:
    try:
        _enqueue("POST", "/indexes", {"uid": index, "primaryKey": "id"})
    except RuntimeError as exc:
        if "already exists" not in str(exc).lower() and " 409 " not in str(exc):
            raise
    _enqueue("PATCH", f"/indexes/{index}/settings", SETTINGS)


def has_page_docs(index: str = INDEX) -> bool:
    body = _request(
        "POST",
        f"/indexes/{index}/search",
        {"q": "", "filter": "type = page", "limit": 1},
    )
    hits = body.get("estimatedTotalHits") or len(body.get("hits") or [])
    return int(hits) > 0


def upsert(records: list[dict], *, index: str = INDEX) -> None:
    if not records:
        return
    for start in range(0, len(records), BATCH):
        _enqueue("POST", f"/indexes/{index}/documents", records[start : start + BATCH])


def delete_pages(document_id: str, *, index: str = INDEX) -> None:
    _enqueue(
        "POST",
        f"/indexes/{index}/documents/delete",
        {"filter": f'type = page AND document_id = "{document_id}"'},
    )


def remove_document(document_id: str, *, index: str = INDEX) -> None:
    """Remove every public search record derived from one document."""
    _enqueue(
        "POST",
        f"/indexes/{index}/documents/delete-batch",
        [f"document-{document_id}"],
    )
    delete_pages(document_id, index=index)


def sync_catalog(
    catalog: Catalog,
    *,
    index: str = INDEX,
    generation_id: str | None = None,
) -> None:
    visible_documents = [document for document in catalog.documents if visible_on_site(document)]
    records = [
        airport_record(airport, catalog.overview_for(airport.lid))
        for airport in catalog.airports
    ]
    records += [state_record(state) for state in catalog.states]
    records += [document_record(document) for document in visible_documents]
    records += [funding_record(grant) for grant in catalog.grants if grant.airport_lid]
    if generation_id:
        records = [{**record, "_generation": generation_id} for record in records]
    if index == INDEX:
        upsert(records)
    else:
        upsert(records, index=index)
    for document in catalog.documents:
        if not visible_on_site(document):
            if index == INDEX:
                remove_document(document.id)
            else:
                remove_document(document.id, index=index)
    log.info("search catalog records upserted=%s", len(records))


def sync_airports(
    catalog: Catalog,
    lids: list[str] | None = None,
    *,
    index: str = INDEX,
) -> None:
    """Refresh airport Meilisearch docs after fact sheets change."""
    if not configured():
        return
    wanted = set(lids) if lids else None
    records = [
        airport_record(airport, catalog.overview_for(airport.lid))
        for airport in catalog.airports
        if wanted is None or airport.lid in wanted
    ]
    if index == INDEX:
        upsert(records)
    else:
        upsert(records, index=index)


def backfill_text(
    catalog: Catalog,
    files_dir: Path | None = None,
    dest: Path | None = None,
    *,
    ledger_root: Path | None = None,
) -> int:
    """Backfill immutable extraction manifests and their search projection."""
    from pipeline.extraction_store import ExtractionStore, extraction_dir
    from pipeline.ocr import TesseractOcr, ocr_enabled
    from pipeline.status import queue_dir_from_env

    files_dir = files_dir or Path(os.environ.get("APTPLANS_FILES", ROOT / "data" / "files"))
    dest = dest or text_dir(files_dir)
    ledger_root = ledger_root or queue_dir_from_env()
    store = ExtractionStore(ledger_root, extraction_dir(files_dir))
    ocr = TesseractOcr() if ocr_enabled() else None
    written = 0
    for document in catalog.documents:
        if document.kind == "notice" or not document.content_sha256:
            continue
        pdf = files_dir / f"{document.content_sha256}.pdf"
        if not pdf.is_file():
            continue
        manifest = store.extract_pdf(
            pdf,
            content_sha256=document.content_sha256,
            ocr=ocr,
        )
        if not projection_matches(
            dest,
            document.content_sha256,
            manifest_key=manifest.manifest_key,
            manifest_sha256=manifest.manifest_sha256,
        ):
            write_pages(
                dest,
                document.content_sha256,
                manifest.page_text(),
                manifest_key=manifest.manifest_key,
                manifest_sha256=manifest.manifest_sha256,
            )
            written += 1
    if written:
        log.info("wrote %s text sidecars from preserved PDFs", written)
    return written


def sync_pages(
    catalog: Catalog,
    dest: Path | None = None,
    *,
    index: str = INDEX,
    generation_id: str | None = None,
) -> None:
    dest = dest or text_dir()
    records: list[dict] = []
    for document in catalog.documents:
        if (
            not visible_on_site(document)
            or document.kind == "notice"
            or not document.content_sha256
        ):
            continue
        for row in read_pages(dest, document.content_sha256):
            record = page_record(document, int(row["page"]), str(row["text"]))
            if generation_id:
                record["_generation"] = generation_id
            records.append(record)
            if len(records) >= BATCH:
                if index == INDEX:
                    upsert(records)
                else:
                    upsert(records, index=index)
                records = []
    if index == INDEX:
        upsert(records)
    else:
        upsert(records, index=index)
    log.info("search page records upserted")


def upsert_preserved(
    updates: dict,
    pages: list[dict] | None,
    *,
    dest: Path | None = None,
) -> None:
    if not configured():
        return
    document = document_from_updates(updates)
    ensure_index()
    if not visible_on_site(document):
        remove_document(document.id)
        return
    upsert([document_record(document)])
    if document.kind == "notice":
        return
    delete_pages(document.id)
    if pages is None and document.content_sha256:
        pages = read_pages(dest or text_dir(), document.content_sha256)
    pages = pages or []
    upsert([page_record(document, int(row["page"]), str(row["text"])) for row in pages])


def generation_index_uid(generation_id: str) -> str:
    safe = "".join(character for character in generation_id.lower() if character.isalnum())
    if not safe:
        raise ValueError("generation id cannot produce a search index uid")
    return f"aptplans_g_{safe[:24]}"


def _generation_document_count(index: str, generation_id: str) -> int:
    response = _request(
        "POST",
        f"/indexes/{index}/search",
        {
            "q": "",
            "filter": f'_generation = "{generation_id}"',
            "page": 1,
            "hitsPerPage": 1,
        },
    )
    if "totalHits" not in response:
        raise RuntimeError(
            "Meilisearch exact pagination response omitted totalHits"
        )
    return int(response["totalHits"])


def stage_generation_index(
    catalog: Catalog,
    generation_id: str,
    *,
    dest: Path | None = None,
) -> tuple[str, int] | None:
    """Build and validate a generation-specific index without touching live search."""
    if not configured():
        return None
    index = generation_index_uid(generation_id)
    _enqueue(
        "DELETE",
        f"/indexes/{index}",
        ignored_error_codes=frozenset({"index_not_found"}),
    )
    ensure_index(index)
    backfill_text(catalog, dest=dest)
    sync_catalog(catalog, index=index, generation_id=generation_id)
    sync_pages(catalog, dest, index=index, generation_id=generation_id)
    stats = _request("GET", f"/indexes/{index}/stats")
    count = int(stats.get("numberOfDocuments") or 0)
    visible = sum(1 for document in catalog.documents if visible_on_site(document))
    minimum = len(catalog.airports) + len(catalog.states) + visible
    if count < minimum:
        raise RuntimeError(
            f"staged search index incomplete: {count} documents, expected at least {minimum}"
        )
    generation_count = _generation_document_count(
        index,
        generation_id,
    )
    if generation_count != count:
        raise RuntimeError(
            f"staged search generation mismatch: {generation_count} of {count} records"
        )
    return index, count


def remove_revoked_before_release(
    previous: Catalog | None,
    upcoming: Catalog,
    *,
    index: str = INDEX,
) -> list[str]:
    """Conservatively hide revocations before the corresponding site swap."""
    if not configured() or previous is None:
        return []
    old_visible = {
        document.id for document in previous.documents if visible_on_site(document)
    }
    new_visible = {
        document.id for document in upcoming.documents if visible_on_site(document)
    }
    revoked = sorted(old_visible - new_visible)
    for document_id in revoked:
        remove_document(document_id, index=index)
    return revoked


def index_generation_id(index: str) -> str | None:
    if not configured():
        return None
    try:
        result = _request(
            "POST",
            f"/indexes/{index}/search",
            {
                "q": "",
                "filter": "_generation IS NOT NULL",
                "attributesToRetrieve": ["_generation"],
                "limit": 1,
            },
        )
    except RuntimeError as exc:
        if "404" in str(exc):
            return None
        raise
    hits = result.get("hits") or []
    value = str(hits[0].get("_generation") if hits else "").strip()
    return value or None


def live_generation_id() -> str | None:
    return index_generation_id(INDEX)


def activate_generation_index(staged_index: str, generation_id: str) -> None:
    """Swap once, retaining a marker until durable activation is recorded."""
    if not configured():
        return
    if live_generation_id() == generation_id:
        return
    ensure_index(INDEX)
    _enqueue(
        "POST",
        "/swap-indexes",
        [{"indexes": [INDEX, staged_index]}],
    )
    if live_generation_id() != generation_id:
        raise RuntimeError("search index swap did not expose the expected generation")


def finalize_generation_index(staged_index: str, generation_id: str) -> None:
    if not configured():
        return
    if live_generation_id() != generation_id:
        raise RuntimeError("cannot finalize an inactive search generation")
    # The staged UID now contains the prior live index. Keep it for rollback;
    # generation-aware garbage collection removes it after a later release.


def reindex(
    catalog: Catalog | None = None,
    overlay_dir: Path | None = None,
    dest: Path | None = None,
) -> None:
    if os.environ.get("APTPLANS_DOMAIN_STORE") == "1":
        raise RuntimeError("domain mode search must activate through a full release")
    overlay = overlay_dir or overlay_dir_from_env()
    catalog = catalog or seed_catalog(ROOT / "catalog", overlay_dir=overlay)
    ensure_index()
    backfill_text(catalog, dest=dest)
    _enqueue("POST", f"/indexes/{INDEX}/documents/delete-all")
    sync_catalog(catalog)
    sync_pages(catalog, dest)
    from pipeline.public_files import reconcile_public_files

    reconcile_public_files(catalog)


def boot_sync() -> None:
    if os.environ.get("APTPLANS_DOMAIN_STORE") == "1":
        raise RuntimeError("domain mode search must activate through a full release")
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
    from pipeline.public_files import reconcile_public_files

    reconcile_public_files(catalog)
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
