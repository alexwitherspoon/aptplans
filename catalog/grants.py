"""FAA AIP (and related) grant histories. Join on LocID. Git does not store the rows."""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import datetime, timedelta
from urllib.parse import urljoin

from catalog.models import Grant
from catalog.xlsx import rows_from_xlsx

GRANT_HISTORIES_URL = "https://www.faa.gov/airports/aip/grant_histories"
FAA_ORIGIN = "https://www.faa.gov"
DOT_AWARDING_AGENCY = "069"
_HREF_RE = re.compile(r"""href=["']([^"']+)["']""", re.I)
_YEAR_PAGE_RE = re.compile(r"/airports/aip/grant_histories/(20\d{2})(?:/|$|\?|#)")
_FY_RE = re.compile(r"(?:FY[_\s-]?|/)(20\d{2})", re.I)
_PLANNING_RE = re.compile(
    r"\bmaster plan\b|\bairport layout plan\b|\balp\b|\bplanning study\b",
    re.I,
)
_HEADER_ALIASES = {
    "lid": ("locid", "loc id", "location id", "airport id", "arpt id"),
    "state": ("state",),
    "description": (
        "project summary",
        "brief description of work",
        "brief description",
        "description of work",
        "project description",
        "description",
    ),
    "total": ("total amount", "aip federal funds", "federal funds", "total aip", "total"),
    "entitlement": ("entitlement",),
    "discretionary": ("discretionary",),
    "aig": ("aig", "aig amount"),
    "cares": ("cares amount", "cares"),
    "grant_number": ("grant number", "grant seq number", "grant sequence number", "grant seq"),
    "award_date": ("award date",),
}


def _norm_header(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _money(value: str | None) -> int | None:
    text = (value or "").strip().replace(",", "").replace("$", "")
    if not text:
        return None
    try:
        return int(round(float(text)))
    except ValueError:
        return None


def _excel_date(value: str | None) -> str | None:
    text = (value or "").strip()
    if not text:
        return None
    try:
        serial = float(text)
    except ValueError:
        return None
    if serial < 20000 or serial > 80000:
        return None
    # Excel serial dates use the 1899-12-30 origin (Lotus 1900 leap-year bug).
    parsed = datetime(1899, 12, 30) + timedelta(days=int(serial))
    return parsed.strftime("%Y-%m-%d")


def _cell(row: dict[int, str], columns: dict[str, int], key: str) -> str:
    index = columns.get(key)
    if index is None:
        return ""
    return (row.get(index) or "").strip()


def _header_map(row: dict[int, str]) -> dict[str, int] | None:
    by_norm = {_norm_header(value): col for col, value in row.items() if value}
    found: dict[str, int] = {}
    for key, aliases in _HEADER_ALIASES.items():
        for alias in aliases:
            if alias in by_norm:
                found[key] = by_norm[alias]
                break
    if "lid" not in found:
        return None
    return found


def _fiscal_year(*values: str | None) -> int | None:
    for value in values:
        if not value:
            continue
        match = _FY_RE.search(value)
        if match:
            return int(match.group(1))
    return None


def _programs(entitlement: int | None, discretionary: int | None, aig: int | None, cares: int | None) -> list[str]:
    programs: list[str] = []
    if (entitlement or 0) or (discretionary or 0):
        programs.append("AIP")
    if aig:
        programs.append("AIG")
    if cares:
        programs.append("CARES")
    return programs or ["AIP"]


def is_planning(description: str) -> bool:
    return bool(_PLANNING_RE.search(description or ""))


def grant_title(description: str) -> str:
    text = " ".join((description or "").split())
    if not text:
        return "Grant"
    return text.split(",", 1)[0].strip() or text


def fain_from_grant_number(grant_number: str | None) -> str | None:
    if not grant_number:
        return None
    fain = re.sub(r"[^A-Za-z0-9]", "", grant_number)
    if len(fain) < 10:
        return None
    return fain


def usaspending_award_url(grant_number: str | None) -> str | None:
    """AIP grant numbers are the USAspending FAIN with hyphens removed. DOT is agency 069."""
    fain = fain_from_grant_number(grant_number)
    if not fain:
        return None
    return f"https://www.usaspending.gov/award/ASST_NON_{fain}_{DOT_AWARDING_AGENCY}"


def faa_year_summary_url(fiscal_year: int | None) -> str | None:
    if not fiscal_year:
        return None
    return f"{GRANT_HISTORIES_URL}/{fiscal_year}"


def year_pages_from_listing(html: str, origin: str = FAA_ORIGIN) -> list[tuple[int, str]]:
    found: dict[int, str] = {}
    for href in _HREF_RE.findall(html):
        match = _YEAR_PAGE_RE.search(href)
        if not match:
            continue
        year = int(match.group(1))
        found[year] = urljoin(origin + "/", href)
    return sorted(found.items())


def xlsx_url_from_year_page(html: str, origin: str = FAA_ORIGIN) -> str | None:
    hrefs = []
    for href in _HREF_RE.findall(html):
        if href.lower().endswith(".xlsx"):
            hrefs.append(urljoin(origin + "/", href))
    if not hrefs:
        return None
    # Prefer the all-grants workbook; by-state sheets duplicate those rows.
    ranked = sorted(
        hrefs,
        key=lambda url: (
            "by-state" in url.lower() or "by_state" in url.lower(),
            "grant" not in url.lower() and "aip" not in url.lower(),
            url,
        ),
    )
    return ranked[0]


def parse_aip_grants_bytes(
    data: bytes,
    *,
    fiscal_year: int | None = None,
    source_url: str | None = None,
) -> list[Grant]:
    rows = rows_from_xlsx(data)
    columns: dict[str, int] | None = None
    title = ""
    grants: list[Grant] = []
    for _index, row in sorted(rows.items()):
        if columns is None:
            mapped = _header_map(row)
            if mapped:
                columns = mapped
                continue
            title = " ".join(value for _, value in sorted(row.items()) if value)
            continue
        lid = _cell(row, columns, "lid").upper()
        if not lid or lid.startswith("*"):
            continue
        description = _cell(row, columns, "description")
        entitlement = _money(_cell(row, columns, "entitlement"))
        discretionary = _money(_cell(row, columns, "discretionary"))
        aig = _money(_cell(row, columns, "aig"))
        cares = _money(_cell(row, columns, "cares"))
        amount = _money(_cell(row, columns, "total"))
        if amount is None:
            parts = [entitlement, discretionary, aig, cares]
            amount = sum(part or 0 for part in parts) or None
        grant_number = _cell(row, columns, "grant_number") or None
        year = fiscal_year or _fiscal_year(grant_number, source_url, title)
        grants.append(
            Grant(
                airport_lid=lid,
                fiscal_year=year,
                amount=amount,
                description=description,
                grant_number=grant_number,
                award_date=_excel_date(_cell(row, columns, "award_date")),
                state=_cell(row, columns, "state").upper() or None,
                programs=_programs(entitlement, discretionary, aig, cares),
                is_planning=is_planning(description),
                source_url=source_url,
                level="federal",
                entity="FAA",
            )
        )
    return grants


def apply_award_status(grants: list[Grant], status_by_fain: dict[str, dict]) -> list[Grant]:
    updated: list[Grant] = []
    for grant in grants:
        fain = fain_from_grant_number(grant.grant_number)
        extra = status_by_fain.get(fain or "")
        if not extra:
            updated.append(grant)
            continue
        updated.append(
            replace(
                grant,
                obligated=extra.get("obligated"),
                outlayed=extra.get("outlayed"),
            )
        )
    return updated


def remaining_obligation(grant: Grant) -> int | None:
    if grant.outlayed is None:
        return None
    base = grant.obligated if grant.obligated is not None else grant.amount
    if base is None:
        return None
    return max(base - grant.outlayed, 0)
