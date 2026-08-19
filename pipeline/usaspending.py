"""USAspending award amounts. CI must not call this live."""

from __future__ import annotations

import logging
import time

from catalog.grants import fain_from_grant_number

log = logging.getLogger("aptplans.usaspending")

SEARCH_URL = "https://api.usaspending.gov/api/v2/search/spending_by_award/"
BATCH = 50
GRANT_TYPES = ["02", "03", "04", "05"]


def _dollars(value) -> int | None:
    if value is None:
        return None
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None


def fetch_award_status(
    grant_numbers: list[str],
    *,
    post_json,
    sleep=time.sleep,
    pause_seconds: float = 2.0,
) -> dict[str, dict]:
    fains = []
    seen: set[str] = set()
    for number in grant_numbers:
        fain = fain_from_grant_number(number)
        if not fain or fain in seen:
            continue
        seen.add(fain)
        fains.append(fain)
    status: dict[str, dict] = {}
    for start in range(0, len(fains), BATCH):
        chunk = fains[start : start + BATCH]
        if start:
            sleep(pause_seconds)
        payload = {
            "filters": {"award_type_codes": GRANT_TYPES, "award_ids": chunk},
            "fields": ["Award ID", "Award Amount", "Total Outlays"],
            "limit": max(len(chunk), 1),
            "page": 1,
        }
        data = post_json(SEARCH_URL, payload, timeout=180)
        for row in data.get("results") or []:
            fain = str(row.get("Award ID") or "")
            if not fain:
                continue
            status[fain] = {
                "obligated": _dollars(row.get("Award Amount")),
                "outlayed": _dollars(row.get("Total Outlays")),
            }
    log.info("USAspending status for %s of %s grant numbers", len(status), len(fains))
    return status
