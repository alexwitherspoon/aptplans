"""Fetch FAA AIP grant histories into overlay grants.jsonl. CI must not call this live."""

from __future__ import annotations

import argparse
import logging
import os
import time
from pathlib import Path

from catalog.grants import (
    GRANT_HISTORIES_URL,
    apply_award_status,
    parse_aip_grants_bytes,
    xlsx_url_from_year_page,
    year_pages_from_listing,
)
from catalog.models import Grant
from catalog.store import load_grants_overlay, write_grants_overlay
from pipeline.datasets import dataset_should_refresh
from pipeline.fetch import fetch_bytes, post_json
from pipeline.grant_classify import enrich_grants, reclassify_grants_overlay
from pipeline.ollama import llm_calls_enabled
from pipeline.refresh import PAUSE_SECONDS, overlay_dir_from_env, overlay_grants_path
from pipeline.usaspending import fetch_award_status

log = logging.getLogger("aptplans.grants")


def refresh_grants(
    overlay_dir: Path,
    *,
    fetch=fetch_bytes,
    sleep=time.sleep,
    pause_seconds: float = PAUSE_SECONDS,
    post_json=None,
) -> int:
    listing, _ = fetch(GRANT_HISTORIES_URL, timeout=180)
    html = listing.decode("utf-8", errors="replace")
    pages = year_pages_from_listing(html)
    if not pages:
        raise ValueError("AIP grant histories listing has no fiscal year pages")
    grants: list[Grant] = []
    for year, page_url in pages:
        sleep(pause_seconds)
        year_html, _ = fetch(page_url, timeout=180)
        xlsx_url = xlsx_url_from_year_page(year_html.decode("utf-8", errors="replace"))
        if not xlsx_url:
            log.info("no AIP xlsx for FY %s", year)
            continue
        sleep(pause_seconds)
        data, _ = fetch(xlsx_url, timeout=180)
        parsed = parse_aip_grants_bytes(data, fiscal_year=year, source_url=xlsx_url)
        log.info("FY %s: %s grants from %s", year, len(parsed), xlsx_url)
        grants.extend(parsed)
    if not grants:
        raise ValueError("AIP grant histories produced no grant rows")
    prior = {
        grant.grant_number: grant
        for grant in load_grants_overlay(overlay_dir)
        if grant.grant_number
    }
    if post_json is not None:
        try:
            numbers = [grant.grant_number or "" for grant in grants]
            status = fetch_award_status(
                numbers, post_json=post_json, sleep=sleep, pause_seconds=pause_seconds
            )
            grants = apply_award_status(grants, status)
        except Exception:
            log.exception("USAspending outlays failed; keeping FAA award amounts")
    generate_fn = None
    if llm_calls_enabled():
        try:
            from pipeline.ollama import generate as ollama_generate

            generate_fn = ollama_generate
        except Exception:
            log.exception("Ollama unavailable; grant spend stays rule-based")
    grants = enrich_grants(
        grants,
        generate_fn=generate_fn,
        overlay_dir=overlay_dir,
        sleep=sleep,
        pause_seconds=pause_seconds if generate_fn else 0,
        prior_by_number=prior,
    )
    write_grants_overlay(overlay_dir, grants)
    log.info("wrote %s grants to %s", len(grants), overlay_grants_path(overlay_dir))
    return len(grants)


def maybe_refresh_grants(
    overlay_dir: Path,
    *,
    force: bool = False,
    fetch=fetch_bytes,
    sleep=time.sleep,
    post_json=None,
) -> int | None:
    path = overlay_grants_path(overlay_dir)
    if not force and not dataset_should_refresh(overlay_dir, "grants"):
        log.info("grant overlay is current: %s", path)
        return None
    return refresh_grants(overlay_dir, fetch=fetch, sleep=sleep, post_json=post_json)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Fetch FAA AIP grant histories into the catalog overlay")
    parser.add_argument("--force", action="store_true")
    parser.add_argument(
        "--reclassify-spend",
        action="store_true",
        help="Re-run grant spend classification on overlay grants without fetching FAA data",
    )
    parser.add_argument(
        "--reclassify-all",
        action="store_true",
        help="With --reclassify-spend, reclassify every grant row (not only ambiguous)",
    )
    args = parser.parse_args()
    overlay = overlay_dir_from_env()
    if args.reclassify_spend:
        reclassify_grants_overlay(overlay, force=args.reclassify_all, sleep=time.sleep)
        return 0
    maybe_refresh_grants(overlay, force=args.force, post_json=post_json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
