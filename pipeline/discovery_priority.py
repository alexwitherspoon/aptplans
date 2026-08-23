"""Order live discovery to search high-signal airports first.

Safety and completeness are unchanged: every scoped airport stays in the
rotation. Sorting only changes which LID is visited next.

Priority (lowest tier number = searched sooner):
  1. Funded (federal, state, or local grants) + not evaluated within the recency window
  2. Not evaluated within the recency window (any airport)
  3. Funded but evaluated recently
  4. Evaluated recently (everyone else)
  5. Prior pass found no plan (re-search last)
  6. Already published on site (re-search last)

Within a tier: never-evaluated first, then higher total grant dollars (from overlay
``grants.jsonl``), then NPIAS, then ``(state, lid)``. Triage needs a populated
``grants.jsonl`` on the worker overlay; without it every airport is treated as unfunded.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from catalog.models import Airport, Grant
from catalog.seed import seed_catalog
from catalog.store import has_verified_plans, load_grants_overlay
from pipeline.pipeline_status import load_status
from pipeline.refresh import ROOT

# Lower tier numbers are searched sooner.
TIER_FUNDED_STALE = 0
TIER_STALE = 1
TIER_FUNDED_RECENT = 2
TIER_RECENT = 3
TIER_NO_PLAN_FOUND = 4
TIER_PUBLISHED = 5

# Backward-compatible aliases (federal-only naming from earlier passes).
TIER_FEDERAL_STALE = TIER_FUNDED_STALE
TIER_FEDERAL_RECENT = TIER_FUNDED_RECENT

_PLAN_FUNDING_LEVELS = frozenset({"federal", "state", "local"})
_NO_PLAN_STATUSES = frozenset({"dead", "not_plan", "ssi"})
_DEFAULT_RECENCY_DAYS = 30


def funded_first_enabled() -> bool:
    raw = os.environ.get("APTPLANS_DISCOVERY_FUNDED_FIRST")
    if raw is None or not str(raw).strip():
        raw = os.environ.get("APTPLANS_DISCOVERY_FEDERAL_FIRST", "1")
    return str(raw).strip().lower() not in {"0", "false", "no", "off"}


def federal_first_enabled() -> bool:
    """Alias for funded_first_enabled (legacy env name)."""
    return funded_first_enabled()


def discovery_recency_days() -> int:
    raw = os.environ.get("APTPLANS_DISCOVERY_RECENCY_DAYS", "").strip()
    if not raw:
        return _DEFAULT_RECENCY_DAYS
    try:
        return max(1, int(raw))
    except ValueError:
        return _DEFAULT_RECENCY_DAYS


def funded_obligation_by_lid(grants: list[Grant]) -> dict[str, int]:
    """Sum federal, state, and local grant dollars per airport LID."""
    totals: dict[str, int] = {}
    for grant in grants:
        level = (grant.level or "federal").lower()
        if level not in _PLAN_FUNDING_LEVELS:
            continue
        lid = (grant.airport_lid or "").strip().upper()
        if not lid:
            continue
        amount = int(grant.obligated or grant.amount or 0)
        totals[lid] = totals.get(lid, 0) + amount
    return totals


def federal_obligation_by_lid(grants: list[Grant]) -> dict[str, int]:
    """Legacy name: federal-only totals. Prefer funded_obligation_by_lid."""
    totals: dict[str, int] = {}
    for grant in grants:
        if (grant.level or "federal").lower() != "federal":
            continue
        lid = (grant.airport_lid or "").strip().upper()
        if not lid:
            continue
        amount = int(grant.obligated or grant.amount or 0)
        totals[lid] = totals.get(lid, 0) + amount
    return totals


def _parse_utc(raw: str) -> datetime | None:
    text = (raw or "").strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            return datetime.fromisoformat(text.replace("Z", "+00:00"))
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed
    except ValueError:
        return None


def last_evaluated_at(row: dict) -> datetime | None:
    """When discovery last ran for this airport (fallback: explore / any job)."""
    for key in ("discovery_at", "explored_at", "last_job_at"):
        when = _parse_utc(str(row.get(key) or ""))
        if when is not None:
            return when
    return None


def evaluated_recently(
    row: dict,
    *,
    recency_days: int,
    now: datetime | None = None,
) -> bool:
    when = last_evaluated_at(row)
    if when is None:
        return False
    now = now or datetime.now(timezone.utc)
    return when >= now - timedelta(days=recency_days)


def _no_plan_found(row: dict) -> bool:
    if row.get("snapshot_at"):
        return False
    if not row.get("explored_at"):
        return False
    return row.get("last_job_status") in _NO_PLAN_STATUSES


def discovery_tier(
    airport: Airport,
    *,
    funded_by_lid: dict[str, int],
    status_rows: dict[str, dict],
    published_lids: frozenset[str],
    recency_days: int | None = None,
    now: datetime | None = None,
) -> int:
    lid = airport.lid.strip().upper()
    if lid in published_lids:
        return TIER_PUBLISHED
    row = status_rows.get(lid) or {}
    if _no_plan_found(row):
        return TIER_NO_PLAN_FOUND
    recency_days = recency_days if recency_days is not None else discovery_recency_days()
    now = now or datetime.now(timezone.utc)
    funded = lid in funded_by_lid
    recent = evaluated_recently(row, recency_days=recency_days, now=now)
    if not recent:
        return TIER_FUNDED_STALE if funded else TIER_STALE
    return TIER_FUNDED_RECENT if funded else TIER_RECENT


def discovery_sort_key(
    airport: Airport,
    *,
    funded_by_lid: dict[str, int],
    status_rows: dict[str, dict],
    published_lids: frozenset[str],
    recency_days: int | None = None,
    now: datetime | None = None,
) -> tuple:
    lid = airport.lid.strip().upper()
    row = status_rows.get(lid) or {}
    recency_days = recency_days if recency_days is not None else discovery_recency_days()
    now = now or datetime.now(timezone.utc)
    tier = discovery_tier(
        airport,
        funded_by_lid=funded_by_lid,
        status_rows=status_rows,
        published_lids=published_lids,
        recency_days=recency_days,
        now=now,
    )
    never_rank = 0 if last_evaluated_at(row) is None else 1
    npias_rank = 0 if airport.in_npias else 1
    # Negative dollars: larger total funding sorts sooner within the same tier.
    funding_rank = -funded_by_lid.get(lid, 0)
    return (
        tier,
        never_rank,
        funding_rank,
        npias_rank,
        (airport.state or "").upper(),
        lid,
    )


def sort_airports_for_discovery(
    airports: list[Airport],
    overlay_dir: Path,
    *,
    funded_first: bool | None = None,
    federal_first: bool | None = None,
    recency_days: int | None = None,
    now: datetime | None = None,
) -> list[Airport]:
    """Return airports in discovery order (funded + stale first)."""
    if not airports:
        return []
    if funded_first is None:
        funded_first = federal_first if federal_first is not None else funded_first_enabled()
    if not funded_first:
        return sorted(airports, key=lambda item: ((item.state or "").upper(), item.lid.upper()))

    grants = load_grants_overlay(overlay_dir)
    funded_by_lid = funded_obligation_by_lid(grants)
    status_rows = load_status(overlay_dir)
    catalog = seed_catalog(ROOT / "catalog", overlay_dir=overlay_dir)
    published_lids = frozenset(
        airport.lid.upper()
        for airport in airports
        if has_verified_plans(catalog, airport.lid)
    )
    recency_days = recency_days if recency_days is not None else discovery_recency_days()
    now = now or datetime.now(timezone.utc)
    return sorted(
        airports,
        key=lambda airport: discovery_sort_key(
            airport,
            funded_by_lid=funded_by_lid,
            status_rows=status_rows,
            published_lids=published_lids,
            recency_days=recency_days,
            now=now,
        ),
    )
