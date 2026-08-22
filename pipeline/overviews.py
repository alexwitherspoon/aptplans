"""Airport fact sheets in overlay overviews.jsonl.

Search and the review API read this overlay. Generate when missing. Refresh at
least once a calendar month (Pacific), same cadence as NASR and AIP grants.
Airport HTML extracts the sheet on every site generate. Listed files first;
NASR fills runways, elevation, and fuel/storage when those files have no figure.
"""

from __future__ import annotations

import importlib.util
import logging
from datetime import datetime, timezone
from pathlib import Path

from catalog.models import visible_on_site
from catalog.seed import seed_catalog
from catalog.store import upsert_overview_overlay
from pipeline.brief import AirportOverview, airport_overview, overview_is_stale
from pipeline.refresh import ROOT

log = logging.getLogger("aptplans.overviews")


def _build_mod():
    spec = importlib.util.spec_from_file_location(
        "aptplans_build_overviews", ROOT / "site" / "build.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def lids_with_data(catalog) -> set[str]:
    lids: set[str] = set()
    for document in catalog.documents:
        if document.kind in {"master_plan", "alp"} and document.airport_lid:
            lids.add(document.airport_lid)
    for grant in catalog.grants:
        if grant.airport_lid:
            lids.add(grant.airport_lid)
    return lids


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def compute_overview_row(catalog, lid: str, build=None, *, overlay_dir=None) -> dict:
    import os

    build = build or _build_mod()
    docs = [
        document
        for document in catalog.documents_for_airport(lid)
        if visible_on_site(document)
    ]
    grants = catalog.grants_for_airport(lid)
    airport = catalog.airports_by_lid.get(lid)
    latest_alp, latest_plan, earlier = build.featured_and_earlier(docs)
    generate_fn = None
    from pipeline.ollama import llm_calls_enabled

    if llm_calls_enabled():
        from catalog.store import has_verified_plans

        if has_verified_plans(catalog, lid):
            try:
                from pipeline.ollama import generate as ollama_generate

                generate_fn = ollama_generate
            except Exception:
                log.exception("Ollama unavailable for overview %s", lid)
    overview = airport_overview(
        [latest_plan, latest_alp, *earlier],
        build.overview_grant_lines(grants),
        airport=airport,
        generate_fn=generate_fn,
        overlay_dir=overlay_dir,
    )
    stamp = _utc_now()
    if overview is None:
        overview = AirportOverview(facts=(), as_of=None)
    return overview.to_row(lid, stamp)


def upsert_overview_for(
    overlay_dir: Path,
    catalog_root: Path,
    lid: str,
    *,
    build=None,
) -> dict:
    catalog = seed_catalog(catalog_root, overlay_dir=overlay_dir)
    row = compute_overview_row(catalog, lid, build=build, overlay_dir=overlay_dir)
    upsert_overview_overlay(overlay_dir, row)
    catalog.overviews[lid] = row
    airport = catalog.airports_by_lid.get(lid)
    if airport is not None:
        from pipeline.search import upsert as search_upsert, airport_record, configured

        if configured():
            search_upsert([airport_record(airport, row)])
    log.info("overview %s facts=%s", lid, len(row.get("facts") or []))
    return row


def refresh_overviews(
    overlay_dir: Path,
    catalog_root: Path | None = None,
    *,
    now=None,
    force: bool = False,
) -> int:
    """Write missing sheets and those from a prior month. Extractive; no model."""
    root = catalog_root or ROOT / "catalog"
    catalog = seed_catalog(root, overlay_dir=overlay_dir)
    build = _build_mod()
    wrote = 0
    updated: list[str] = []
    for lid in sorted(lids_with_data(catalog)):
        current = catalog.overviews.get(lid)
        if not force and current and not overview_is_stale(current, now):
            continue
        row = compute_overview_row(catalog, lid, build=build, overlay_dir=overlay_dir)
        upsert_overview_overlay(overlay_dir, row)
        catalog.overviews[lid] = row
        wrote += 1
        updated.append(lid)
    if updated:
        from pipeline.search import sync_airports

        sync_airports(catalog, updated)
    log.info("refreshed %s airport overviews", wrote)
    return wrote
