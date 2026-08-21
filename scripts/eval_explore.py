"""Explore an official airport page. Polite GET; not CI."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline.explore import confirm_jobs, explore_page, followup_explore_jobs, hub_document_kind
from pipeline.stages import source_family

DEFAULT_URL = "https://www.oregon.gov/aviation/airports/pages/mulino-4s9.aspx"


def fetch(url: str) -> str:
    req = Request(url, headers={"User-Agent": os.environ.get("APTPLANS_USER_AGENT") or "aptplans.org"})
    with urlopen(req, timeout=60) as resp:
        return resp.read().decode("utf-8", "replace")


def _catalog_sample() -> int:
    import time

    cases = json.loads((ROOT / "catalog" / "references" / "cases.json").read_text())["cases"]
    rows = []
    seen: set[str] = set()
    for case in cases:
        targets = [("website", case.get("website") or "")]
        for doc in case.get("documents") or []:
            src = doc.get("source_url") or ""
            if src and not src.lower().endswith(".pdf"):
                targets.append((doc["id"], src))
        for label, url in targets:
            if not url.startswith("http") or url in seen:
                continue
            seen.add(url)
            try:
                html = fetch(url)
            except (HTTPError, URLError, OSError, TimeoutError) as exc:
                status = getattr(exc, "code", None)
                rows.append(
                    {
                        "lid": case["airport_lid"],
                        "label": label,
                        "url": url,
                        "source_family": source_family(status=status, error=str(exc)),
                        "error": str(exc),
                        "n_confirm_jobs": 0,
                    }
                )
                time.sleep(0.8)
                continue
            result = explore_page(html, url)
            jobs = confirm_jobs(result, airport_lid=case["airport_lid"], state=case.get("state"))
            family = source_family(
                status=200,
                n_artifacts=len(result.artifacts()),
                n_followups=len(result.followups),
                hub_kind=hub_document_kind(result),
                page_url=url,
            )
            rows.append(
                {
                    "lid": case["airport_lid"],
                    "label": label,
                    "url": url,
                    "title": result.title,
                    "hub_kind": hub_document_kind(result),
                    "source_family": family,
                    "n_links": len(result.links),
                    "n_artifacts": len(result.artifacts()),
                    "n_followups": len(result.followups),
                    "n_confirm_jobs": len(jobs),
                }
            )
            time.sleep(0.8)
    print(json.dumps({"n": len(rows), "pages": rows}, indent=2))
    return 0


def _explore_url(lid: str, label: str, url: str) -> dict:
    try:
        html = fetch(url)
    except (HTTPError, URLError, OSError, TimeoutError) as exc:
        status = getattr(exc, "code", None)
        return {
            "lid": lid,
            "label": label,
            "url": url,
            "source_family": source_family(status=status, error=str(exc)),
            "error": str(exc),
            "n_confirm_jobs": 0,
        }
    result = explore_page(html, url)
    jobs = confirm_jobs(result, airport_lid=lid, state="")
    family = source_family(
        status=200,
        n_artifacts=len(result.artifacts()),
        n_followups=len(result.followups),
        hub_kind=hub_document_kind(result),
        page_url=url,
    )
    return {
        "lid": lid,
        "label": label,
        "url": url,
        "title": result.title,
        "hub_kind": hub_document_kind(result),
        "source_family": family,
        "n_links": len(result.links),
        "n_artifacts": len(result.artifacts()),
        "n_followups": len(result.followups),
        "n_confirm_jobs": len(jobs),
        "n_followup_explore": len(followup_explore_jobs(result, airport_lid=lid)),
    }


def _ourairports_sample(limit: int) -> int:
    """Explore a few OurAirports home_link values. Signals only; not CI."""
    import csv
    import io
    import time

    from catalog.ourairports import OURAIRPORTS_CSV_URL

    raw = fetch(OURAIRPORTS_CSV_URL)
    wanted = {"large_airport": [], "medium_airport": []}
    for row in csv.DictReader(io.StringIO(raw)):
        if (row.get("iso_country") or "").strip().upper() != "US":
            continue
        kind = (row.get("type") or "").strip().lower()
        if kind not in wanted:
            continue
        url = (row.get("home_link") or "").strip()
        if not url.startswith("http") or "wikipedia.org" in url.lower():
            continue
        iata = (row.get("iata_code") or "").strip().upper()
        if kind in {"large_airport", "medium_airport"} and not iata:
            continue
        lid = (row.get("local_code") or row.get("ident") or "").strip().upper()
        if not lid:
            continue
        wanted[kind].append((lid, url))
    per = max(1, limit // 2)
    picks: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for kind, bucket in wanted.items():
        for lid, url in bucket:
            if url in seen:
                continue
            seen.add(url)
            picks.append((lid, kind, url))
            if sum(1 for item in picks if item[1] == kind) >= per:
                break
    rows = []
    for lid, kind, url in picks[:limit]:
        row = _explore_url(lid, kind, url)
        rows.append(row)
        time.sleep(0.8)
    print(json.dumps({"n": len(rows), "note": "home_link explore; not a publish", "pages": rows}, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Explore a hub page without publishing")
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--lid", default="4S9")
    parser.add_argument("--state", default="OR")
    parser.add_argument("--catalog", action="store_true", help="Sample HTML hubs from cases.json")
    parser.add_argument(
        "--ourairports-sample",
        type=int,
        metavar="N",
        help="Explore N OurAirports home_link pages (not CI, not a publish)",
    )
    args = parser.parse_args()
    if args.catalog:
        return _catalog_sample()
    if args.ourairports_sample:
        return _ourairports_sample(args.ourairports_sample)
    html = fetch(args.url)
    result = explore_page(html, args.url)
    jobs = confirm_jobs(result, airport_lid=args.lid, state=args.state)
    family = source_family(
        status=200,
        n_artifacts=len(result.artifacts()),
        n_followups=len(result.followups),
        hub_kind=hub_document_kind(result),
        page_url=args.url,
    )
    payload = {
        "page_url": result.page_url,
        "title": result.title,
        "hub_kind": hub_document_kind(result),
        "source_family": family,
        "n_links": len(result.links),
        "n_artifacts": len(result.artifacts()),
        "n_followups": len(result.followups),
        "n_confirm_jobs": len(jobs),
        "artifacts": [
            {"url": item.url, "label": item.label, "kind": item.kind_guess, "found_on": item.found_on}
            for item in result.artifacts()[:12]
        ],
        "followups": [
            {"url": item.url, "label": item.label, "view": item.view_name}
            for item in result.followups
        ],
        "confirm_urls": [job.source_url for job in jobs],
        "note": "confirm_urls are fetch candidates, not a publish",
    }
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
