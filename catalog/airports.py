"""NASR is the airport universe. NPIAS marks which of those are more likely to have plans."""

from __future__ import annotations

import csv
import io
import re
import zipfile
from dataclasses import replace

from catalog.models import Airport

PUBLIC_USE = "PU"
SITE_TYPES_CONSIDERED = frozenset({"A", "C"})
NASR_LISTING_URL = (
    "https://www.faa.gov/air_traffic/flight_info/aeronav/aero_data/NASR_Subscription/"
)
NASR_APT_ZIP_BASE = "https://nfdc.faa.gov/webContent/28DaySub/extra/"
_MONTHS = {
    "january": "Jan",
    "february": "Feb",
    "march": "Mar",
    "april": "Apr",
    "may": "May",
    "june": "Jun",
    "july": "Jul",
    "august": "Aug",
    "september": "Sep",
    "october": "Oct",
    "november": "Nov",
    "december": "Dec",
}
_MONTH_NUM = {
    "01": "Jan",
    "02": "Feb",
    "03": "Mar",
    "04": "Apr",
    "05": "May",
    "06": "Jun",
    "07": "Jul",
    "08": "Aug",
    "09": "Sep",
    "10": "Oct",
    "11": "Nov",
    "12": "Dec",
}
_APT_ZIP_RE = re.compile(
    r"(\d{2})_(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)_(\d{4})_APT_CSV\.zip",
    re.I,
)
_EFFECTIVE_RE = re.compile(
    r"Subscription effective ([A-Za-z]+) (\d{1,2}), (\d{4})",
    re.I,
)
_DATE_PATH_RE = re.compile(r"NASR_Subscription/(\d{4})-(\d{2})-(\d{2})")
_HREF_RE = re.compile(r"""href=["']([^"']+)["']""", re.I)


def _float(value: str | None) -> float | None:
    text = (value or "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _blank(value: str | None) -> str | None:
    text = (value or "").strip()
    return text or None


def _display_name(nasr_name: str, npias_name: str | None) -> str:
    if npias_name:
        return npias_name
    if nasr_name.isupper():
        return nasr_name.title()
    return nasr_name


def apt_csv_zip_url(day: str, month_abbr: str, year: str) -> str:
    return f"{NASR_APT_ZIP_BASE}{int(day):02d}_{month_abbr}_{year}_APT_CSV.zip"


def current_apt_csv_url(listing_html: str) -> str:
    """Pick the current 28-day APT CSV zip from the NASR subscription listing."""
    found: list[tuple[str, str, str]] = []
    for href in _HREF_RE.findall(listing_html):
        match = _APT_ZIP_RE.search(href)
        if match:
            if href.startswith("http"):
                return href
            return NASR_APT_ZIP_BASE + match.group(0)
        date_match = _DATE_PATH_RE.search(href)
        if date_match:
            year, month, day = date_match.groups()
            found.append((day, _MONTH_NUM[month], year))
    effective = _EFFECTIVE_RE.search(listing_html)
    if effective:
        month_name, day, year = effective.groups()
        abbr = _MONTHS[month_name.lower()]
        return apt_csv_zip_url(day, abbr, year)
    if found:
        day, abbr, year = found[0]
        return apt_csv_zip_url(day, abbr, year)
    raise ValueError("NASR listing has no current APT CSV zip")


def parse_nasr_apt_zip(data: bytes) -> list[dict]:
    """Public-use airports and seaplane bases from APT_BASE.csv."""
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        name = next(
            (item for item in archive.namelist() if item.replace("\\", "/").endswith("APT_BASE.csv")),
            None,
        )
        if name is None:
            raise ValueError("NASR zip has no APT_BASE.csv")
        text = archive.read(name).decode("utf-8", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    records: list[dict] = []
    for row in reader:
        lid = (row.get("ARPT_ID") or "").strip().upper()
        if not lid:
            continue
        if (row.get("FACILITY_USE_CODE") or "").strip() != PUBLIC_USE:
            continue
        site = (row.get("SITE_TYPE_CODE") or row.get("SITE_TYPE") or "").strip().upper()
        if site not in SITE_TYPES_CONSIDERED:
            continue
        records.append(
            {
                "lid": lid,
                "name": (row.get("ARPT_NAME") or "").strip(),
                "city": (row.get("CITY") or "").strip(),
                "state": (row.get("STATE_CODE") or "").strip().upper(),
                "icao": _blank(row.get("ICAO_ID")),
                "latitude": _float(row.get("LAT_DECIMAL")),
                "longitude": _float(row.get("LONG_DECIMAL")),
                "ownership": _blank(row.get("OWNERSHIP_TYPE_CODE")),
                "facility_use": PUBLIC_USE,
                "site_type": site,
                "effective": _blank(row.get("EFF_DATE")),
            }
        )
    return records


def merge_airports(
    nasr: list[dict],
    npias: list[dict],
    *,
    nasr_effective: str | None = None,
    npias_edition: str | None = None,
) -> list[Airport]:
    """NASR public-use rows are the superset. NPIAS adds role; NPIAS-only LIDs stay in."""
    npias_by_lid = {row["lid"]: row for row in npias}
    by_lid: dict[str, Airport] = {}
    for row in nasr:
        npias_row = npias_by_lid.get(row["lid"])
        sources = ["nasr"]
        if npias_row:
            sources.append("npias")
        effective = nasr_effective or row.get("effective")
        by_lid[row["lid"]] = Airport(
            lid=row["lid"],
            name=_display_name(row.get("name") or row["lid"], npias_row["name"] if npias_row else None),
            city=(npias_row.get("city") if npias_row else None) or row.get("city") or "",
            state=row.get("state") or (npias_row["state"] if npias_row else ""),
            npias_role=npias_row.get("npias_role") if npias_row else None,
            icao=row.get("icao"),
            latitude=row.get("latitude"),
            longitude=row.get("longitude"),
            ownership=(npias_row.get("ownership") if npias_row else None) or row.get("ownership"),
            service_level=npias_row.get("service_level") if npias_row else None,
            in_npias=npias_row is not None,
            facility_use=row.get("facility_use"),
            nasr_effective=effective,
            npias_edition=npias_edition if npias_row else None,
            sources=sources,
        )
    for lid, npias_row in npias_by_lid.items():
        if lid in by_lid:
            continue
        by_lid[lid] = Airport(
            lid=lid,
            name=npias_row["name"],
            city=npias_row.get("city") or "",
            state=npias_row["state"],
            npias_role=npias_row.get("npias_role"),
            ownership=npias_row.get("ownership"),
            service_level=npias_row.get("service_level"),
            in_npias=True,
            npias_edition=npias_edition,
            sources=["npias"],
        )
    return sorted(by_lid.values(), key=lambda item: (item.state, item.lid))


def preserve_admitted(
    snapshot: list[Airport],
    existing: list[Airport],
    document_lids: set[str],
) -> list[Airport]:
    """Keep issue/plan airports that are not in this month's NASR+NPIAS snapshot."""
    by_lid = {airport.lid: airport for airport in snapshot}
    for old in existing:
        if old.lid in by_lid:
            continue
        if old.admitted or old.lid in document_lids:
            by_lid[old.lid] = replace(old, admitted=True)
    return sorted(by_lid.values(), key=lambda item: (item.state, item.lid))
