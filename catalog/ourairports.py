"""OurAirports public-domain identifiers. Used for IATA codes and official home pages."""

from __future__ import annotations

import csv
import io
from dataclasses import replace
from urllib.parse import urlparse

from catalog.models import Airport

OURAIRPORTS_CSV_URL = "https://davidmegginson.github.io/ourairports-data/airports.csv"
_TYPE_RANK = {
    "large_airport": 0,
    "medium_airport": 1,
    "small_airport": 2,
    "seaplane_base": 3,
    "balloonport": 6,
    "heliport": 8,
    "closed": 9,
}


def http_url(value: str | None) -> str | None:
    text = (value or "").strip()
    parsed = urlparse(text)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return text
    return None


def _lid_for_row(row: dict) -> str | None:
    if (row.get("iso_country") or "").strip().upper() != "US":
        return None
    local = (row.get("local_code") or "").strip().upper()
    if local:
        return local
    ident = (row.get("ident") or "").strip().upper()
    if ident.startswith("K") and len(ident) == 4:
        return ident[1:]
    if 3 <= len(ident) <= 4 and ident.isalnum():
        return ident
    return None


def _rank(row: dict) -> int:
    kind = (row.get("type") or "").strip().lower()
    return _TYPE_RANK.get(kind, 5)


def parse_ourairports_csv(data: bytes | str) -> dict[str, dict]:
    """Map FAA LID to website and IATA from the OurAirports airports.csv dump."""
    text = data.decode("utf-8", errors="replace") if isinstance(data, bytes) else data
    by_lid: dict[str, dict] = {}
    ranks: dict[str, int] = {}
    for row in csv.DictReader(io.StringIO(text)):
        lid = _lid_for_row(row)
        if not lid:
            continue
        website = http_url(row.get("home_link"))
        iata = (row.get("iata_code") or "").strip().upper() or None
        if not website and not iata:
            continue
        rank = _rank(row)
        current = by_lid.get(lid)
        if current is None or rank < ranks[lid]:
            by_lid[lid] = {"website": website, "iata": iata}
            ranks[lid] = rank
            continue
        if rank > ranks[lid]:
            continue
        if website and not current.get("website"):
            current["website"] = website
        if iata and not current.get("iata"):
            current["iata"] = iata
    return by_lid


def apply_ourairports(airports: list[Airport], rows: dict[str, dict]) -> list[Airport]:
    """Fill blank website and IATA. Does not replace NASR identity."""
    updated: list[Airport] = []
    for airport in airports:
        row = rows.get(airport.lid)
        if not row:
            updated.append(airport)
            continue
        website = airport.website or row.get("website")
        iata = airport.iata or row.get("iata")
        if website == airport.website and iata == airport.iata:
            updated.append(airport)
            continue
        sources = list(airport.sources)
        if "ourairports" not in sources:
            sources.append("ourairports")
        updated.append(replace(airport, website=website, iata=iata, sources=sources))
    return updated
