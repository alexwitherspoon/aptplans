from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from catalog.grants import (
    apply_award_status,
    faa_year_summary_url,
    fain_from_grant_number,
    grant_spend_category,
    parse_aip_grants_bytes,
    remaining_obligation,
    usaspending_award_url,
    xlsx_url_from_year_page,
    year_pages_from_listing,
)
from catalog.models import Grant
from catalog.seed import seed_catalog
from catalog.store import write_grants_overlay
from pipeline.refresh_grants import maybe_refresh_grants
from pipeline.usaspending import fetch_award_status

ROOT = Path(__file__).resolve().parents[1]


def _xlsx(header: list[str], rows: list[list[str]]) -> bytes:
    strings: list[str] = []
    cells: list[str] = []
    title = "FY 2025 FAA Grant Detail Report"
    strings.append(title)
    cells.append(f'<row r="1"><c r="A1" t="s"><v>0</v></c></row>')
    header_cells = []
    for col_index, value in enumerate(header, start=1):
        strings.append(value)
        col = chr(64 + col_index)
        header_cells.append(f'<c r="{col}3" t="s"><v>{len(strings) - 1}</v></c>')
    cells.append(f'<row r="3">{"".join(header_cells)}</row>')
    for row_index, row in enumerate(rows, start=4):
        row_cells = []
        for col_index, value in enumerate(row, start=1):
            col = chr(64 + col_index)
            if value.replace(".", "", 1).isdigit():
                row_cells.append(f'<c r="{col}{row_index}"><v>{value}</v></c>')
            else:
                strings.append(value)
                row_cells.append(f'<c r="{col}{row_index}" t="s"><v>{len(strings) - 1}</v></c>')
        cells.append(f'<row r="{row_index}">{"".join(row_cells)}</row>')
    sst = "".join(f"<si><t>{item}</t></si>" for item in strings)
    shared = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f"{sst}</sst>"
    )
    sheet = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f"<sheetData>{''.join(cells)}</sheetData></worksheet>"
    )
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr("xl/sharedStrings.xml", shared)
        archive.writestr("xl/worksheets/sheet1.xml", sheet)
    return out.getvalue()


HEADER = [
    "State",
    "City",
    "Worksite",
    "LocID",
    "Sponsor",
    "Grant Number",
    "Award Date",
    "Entitlement",
    "Discretionary",
    "AIG",
    "CARES",
    "Total Amount",
    "Project Summary",
]

GRANT_LISTING_HTML = b'<a href="/airports/aip/grant_histories/2025">2025</a>'
GRANT_YEAR_HTML = b'<a href="/sites/faa.gov/files/2025-11/FY_2025_AIP_Grants.xlsx">xlsx</a>'

PDX_GRANT_ROW = [
    "OR",
    "Portland",
    "Portland Intl",
    "PDX",
    "Port of Portland",
    "3-41-0048-064-2025",
    "45847",
    "100",
    "0",
    "0",
    "0",
    "100",
    "Conduct Airport Master Plan Study",
]


def sample_grants_xlsx() -> bytes:
    return _xlsx(HEADER, [PDX_GRANT_ROW])


def grant_bytes_for(url: str) -> tuple[bytes, int] | None:
    if url.rstrip("/").endswith("grant_histories"):
        return GRANT_LISTING_HTML, 200
    if url.rstrip("/").endswith("/2025"):
        return GRANT_YEAR_HTML, 200
    if url.lower().endswith(".xlsx") and "npias" not in url.lower():
        return sample_grants_xlsx(), 200
    return None


def test_parse_aip_grants_joins_on_locid_and_flags_planning() -> None:
    data = _xlsx(
        HEADER,
        [
            [
                "OR",
                "Portland",
                "Portland Intl",
                "PDX",
                "Port of Portland",
                "3-41-0048-064-2025",
                "45847",
                "0",
                "500000",
                "0",
                "0",
                "500000",
                "Update Airport Master Plan Study",
            ],
            [
                "OR",
                "Portland",
                "Portland Intl",
                "PDX",
                "Port of Portland",
                "3-41-0048-065-2025",
                "45848",
                "0",
                "0",
                "1200000",
                "0",
                "1200000",
                "Reconstruct Taxiway",
            ],
        ],
    )
    grants = parse_aip_grants_bytes(
        data, fiscal_year=2025, source_url="https://www.faa.gov/example.xlsx"
    )
    assert len(grants) == 2
    plan = grants[0]
    assert plan.airport_lid == "PDX"
    assert plan.fiscal_year == 2025
    assert plan.amount == 500000
    assert plan.is_planning is True
    assert "AIP" in plan.programs
    taxi = grants[1]
    assert taxi.is_planning is False
    assert "AIG" in taxi.programs


def test_parse_skips_statewide_star_locid() -> None:
    data = _xlsx(
        HEADER,
        [
            [
                "AK",
                "Statewide",
                "Statewide",
                "*AKS",
                "State of Alaska",
                "3-02-0000-001-2025",
                "45847",
                "0",
                "0",
                "0",
                "0",
                "50",
                "Statewide planning",
            ],
            PDX_GRANT_ROW,
        ],
    )
    grants = parse_aip_grants_bytes(data, fiscal_year=2025)
    assert [grant.airport_lid for grant in grants] == ["PDX"]


def test_parse_prefers_total_amount_and_cares_amount_headers() -> None:
    header = [
        "LocID",
        "Total",
        "Total Amount",
        "CARES Amount",
        "Project Summary",
    ]
    data = _xlsx(
        header,
        [
            ["PDX", "1", "500000", "500000", "Reconstruct Taxiway"],
        ],
    )
    grants = parse_aip_grants_bytes(data, fiscal_year=2021)
    assert grants[0].amount == 500000
    assert grants[0].programs == ["CARES"]


def test_usaspending_url_strips_hyphens_from_grant_number() -> None:
    assert (
        usaspending_award_url("3-41-0048-099-2025")
        == "https://www.usaspending.gov/award/ASST_NON_34100480992025_069"
    )
    assert usaspending_award_url(None) is None
    assert usaspending_award_url("PDX") is None
    assert faa_year_summary_url(2025) == "https://www.faa.gov/airports/aip/grant_histories/2025"


def test_year_pages_and_xlsx_urls() -> None:
    listing = """
    <a href="/airports/aip/grant_histories/2025">2025</a>
    <a href="/airports/aip/grant_histories/2024">2024</a>
    """
    pages = year_pages_from_listing(listing)
    assert [year for year, _url in pages] == [2024, 2025]
    year_html = """
    <a href="/sites/faa.gov/files/2025-11/FY_2025_AIP_Grants.xlsx">xlsx</a>
    <a href="/sites/faa.gov/files/2025-11/FY_2025_AIP_Grants.pdf">pdf</a>
    """
    assert xlsx_url_from_year_page(year_html).endswith("FY_2025_AIP_Grants.xlsx")


def test_refresh_grants_writes_overlay(tmp_path: Path) -> None:
    def fake_fetch(url: str, timeout: int = 60) -> tuple[bytes, int]:
        found = grant_bytes_for(url)
        if found is None:
            raise AssertionError(url)
        return found

    overlay = tmp_path / "overlay"
    count = maybe_refresh_grants(overlay, force=True, fetch=fake_fetch, sleep=lambda _s: None)
    assert count == 1
    catalog = seed_catalog(ROOT / "catalog", overlay_dir=overlay)
    grants = catalog.grants_for_airport("PDX")
    assert grants
    assert grants[0].is_planning is True
    assert grants[0].amount == 100
    assert grants[0].obligated is None
    assert grants[0].outlayed is None


def test_refresh_grants_empty_listing_does_not_write(tmp_path: Path) -> None:
    def fake_fetch(url: str, timeout: int = 60) -> tuple[bytes, int]:
        return b"<html></html>", 200

    overlay = tmp_path / "overlay"
    with pytest.raises(ValueError, match="no fiscal year pages"):
        maybe_refresh_grants(overlay, force=True, fetch=fake_fetch, sleep=lambda _s: None)
    assert not (overlay / "grants.jsonl").exists()


def test_seed_loads_grants_overlay(tmp_path: Path) -> None:
    write_grants_overlay(
        tmp_path,
        [Grant(airport_lid="PDX", fiscal_year=2024, amount=10, description="Reseal Runway")],
    )
    catalog = seed_catalog(ROOT / "catalog", overlay_dir=tmp_path)
    assert catalog.grants_for_airport("PDX")[0].description == "Reseal Runway"
    assert catalog.airports_by_lid["PDX"].name.startswith("Portland")


def test_seed_uses_reference_grants_without_overlay() -> None:
    catalog = seed_catalog(ROOT / "catalog")
    grants = catalog.grants_for_airport("PDX")
    assert grants
    assert any(grant.grant_number == "3-41-0048-099-2025" for grant in grants)
    assert sum(grant.amount or 0 for grant in grants) == 61876159
    taxi = next(grant for grant in grants if grant.grant_number == "3-41-0048-094-2024")
    assert taxi.obligated == 7820543
    assert taxi.outlayed == 6711334
    assert remaining_obligation(taxi) == 1109209


def test_fain_and_remaining_obligation() -> None:
    assert fain_from_grant_number("3-41-0048-064-2025") == "34100480642025"
    grant = Grant(airport_lid="PDX", amount=100, obligated=110, outlayed=10)
    assert remaining_obligation(grant) == 100
    assert remaining_obligation(Grant(airport_lid="PDX", amount=100, outlayed=0)) == 100
    assert remaining_obligation(Grant(airport_lid="PDX", amount=100)) is None


def test_grant_spend_category() -> None:
    assert grant_spend_category(
        Grant(airport_lid="PDX", description="Reconstruct Taxiway", is_planning=False)
    ) == "maintenance"
    assert grant_spend_category(
        Grant(airport_lid="PDX", description="Rehabilitate Runway 10L/28R", is_planning=False)
    ) == "maintenance"
    assert grant_spend_category(
        Grant(airport_lid="PDX", description="Construct New Runway 3", is_planning=False)
    ) == "growth"
    assert grant_spend_category(
        Grant(airport_lid="PDX", description="Expand Terminal Concourse", is_planning=False)
    ) == "growth"
    assert grant_spend_category(
        Grant(
            airport_lid="PDX",
            description="Update Airport Master Plan Study",
            is_planning=True,
        )
    ) == "planning"
    assert grant_spend_category(
        Grant(airport_lid="PDX", description="Zero Emissions Infrastructure", is_planning=False)
    ) == "other"


def test_apply_award_status_merges_usaspending_fields() -> None:
    grants = apply_award_status(
        [Grant(airport_lid="PDX", amount=100, grant_number="3-41-0048-064-2025")],
        {"34100480642025": {"obligated": 100, "outlayed": 40}},
    )
    assert grants[0].obligated == 100
    assert grants[0].outlayed == 40
    assert remaining_obligation(grants[0]) == 60


def test_fetch_award_status_posts_fains() -> None:
    posted: list[dict] = []

    def fake_post(url: str, payload: dict, timeout: int = 180) -> dict:
        posted.append(payload)
        return {
            "results": [
                {"Award ID": "34100480642025", "Award Amount": 100.4, "Total Outlays": 40.2}
            ]
        }

    status = fetch_award_status(
        ["3-41-0048-064-2025", "3-41-0048-064-2025"],
        post_json=fake_post,
        sleep=lambda _s: None,
    )
    assert posted[0]["filters"]["award_ids"] == ["34100480642025"]
    assert status["34100480642025"] == {"obligated": 100, "outlayed": 40}


def test_refresh_grants_merges_usaspending_when_post_json_given(tmp_path: Path) -> None:
    def fake_fetch(url: str, timeout: int = 60) -> tuple[bytes, int]:
        found = grant_bytes_for(url)
        if found is None:
            raise AssertionError(url)
        return found

    def fake_post(url: str, payload: dict, timeout: int = 180) -> dict:
        assert "34100480642025" in payload["filters"]["award_ids"]
        return {
            "results": [
                {"Award ID": "34100480642025", "Award Amount": 100, "Total Outlays": 25}
            ]
        }

    overlay = tmp_path / "overlay"
    count = maybe_refresh_grants(
        overlay,
        force=True,
        fetch=fake_fetch,
        sleep=lambda _s: None,
        post_json=fake_post,
    )
    assert count == 1
    catalog = seed_catalog(ROOT / "catalog", overlay_dir=overlay)
    grant = catalog.grants_for_airport("PDX")[0]
    assert grant.grant_number == "3-41-0048-064-2025"
    assert grant.obligated == 100
    assert grant.outlayed == 25
    assert remaining_obligation(grant) == 75
