"""Which airports live search may query. Default is Oregon. Not a publish.

Set APTPLANS_SEARCH_STATES=OR,WA to widen, or * for every overlay state.
Fixture replay (make eval-search) does not use this filter.
"""

from __future__ import annotations

from pathlib import Path
import os

from catalog.geo import US_STATES
from catalog.models import Airport
from catalog.store import load_airports_overlay
from pipeline.refresh import overlay_dir_from_env as overlay_dir

DEFAULT_STATES = frozenset({"OR"})
ALL_TOKENS = frozenset({"*", "ALL"})


def parse_search_states(raw: str | None = None) -> frozenset[str] | None:
    """Allowed state codes, or None for unrestricted."""
    if raw is None:
        raw = os.environ.get("APTPLANS_SEARCH_STATES")
    text = (raw or "").strip()
    if not text:
        return DEFAULT_STATES
    if text.upper() in ALL_TOKENS:
        return None
    codes = []
    for part in text.replace(";", ",").split(","):
        code = part.strip().upper()
        if not code:
            continue
        if code not in US_STATES:
            raise ValueError(f"unknown search state {code}")
        codes.append(code)
    return frozenset(codes) or DEFAULT_STATES


def in_search_scope(state: str, states: frozenset[str] | None) -> bool:
    """None means every state. Pass parse_search_states() for the configured set."""
    if states is None:
        return True
    return (state or "").strip().upper() in states


def scoped_overlay_airports(
    overlay_dir: Path | None = None,
    *,
    states: frozenset[str] | None,
    limit: int = 0,
) -> list[Airport]:
    """NASR overlay airports in the live search scope. Empty if overlay is missing."""
    rows = []
    for airport in load_airports_overlay(overlay_dir):
        if not in_search_scope(airport.state, states):
            continue
        rows.append(airport)
        if limit and len(rows) >= limit:
            break
    return rows


def case_from_airport(airport: Airport) -> dict:
    return {
        "airport_lid": airport.lid,
        "name": airport.name,
        "city": airport.city or "",
        "state": airport.state or "",
        "website": airport.website or "",
        "documents": [],
    }
