from __future__ import annotations

import csv
import io
import os
import zipfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from catalog.airports import (
    current_apt_csv_url,
    merge_airports,
    parse_nasr_apt_zip,
    preserve_admitted,
)
from catalog.models import Airport
from catalog.npias import parse_appendix_a_bytes
from catalog.ourairports import apply_ourairports, parse_ourairports_csv
from catalog.seed import seed_catalog
from catalog.store import load_airports_overlay, write_airports_overlay
from pipeline.refresh_airports import maybe_refresh, npias_edition_from_url, should_refresh
from tests.test_grants import grant_bytes_for

ROOT = Path(__file__).resolve().parents[1]
PACIFIC = ZoneInfo("America/Los_Angeles")


def _nasr_zip(rows: list[dict], runways: list[dict] | None = None) -> bytes:
    fields = [
        "ARPT_ID",
        "ARPT_NAME",
        "CITY",
        "STATE_CODE",
        "COUNTY_NAME",
        "ICAO_ID",
        "LAT_DECIMAL",
        "LONG_DECIMAL",
        "ELEV",
        "OWNERSHIP_TYPE_CODE",
        "FACILITY_USE_CODE",
        "SITE_TYPE_CODE",
        "EFF_DATE",
        "FUEL_TYPES",
        "TRNS_STRG_HGR_FLAG",
        "TRNS_STRG_TIE_FLAG",
    ]
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({key: row.get(key, "") for key in fields})
    rwy_fields = ["ARPT_ID", "RWY_ID", "RWY_LEN", "RWY_WIDTH", "SURFACE_TYPE_CODE"]
    rwy_buf = io.StringIO()
    rwy_writer = csv.DictWriter(rwy_buf, fieldnames=rwy_fields, extrasaction="ignore")
    rwy_writer.writeheader()
    for row in runways or []:
        rwy_writer.writerow({key: row.get(key, "") for key in rwy_fields})
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr("APT_BASE.csv", buf.getvalue())
        archive.writestr("APT_RWY.csv", rwy_buf.getvalue())
    return out.getvalue()


def _npias_xlsx(rows: list[dict]) -> bytes:
    strings: list[str] = []
    cells: list[str] = []
    for index, row in enumerate(rows, start=2):
        values = [
            row["state_name"],
            row["city"],
            row["name"],
            row["lid"],
            row.get("ownership") or "",
            row.get("svc") or "",
            row.get("hub") or "",
            row.get("role") or "",
        ]
        row_cells = []
        for col_index, value in enumerate(values, start=1):
            strings.append(value)
            col = chr(64 + col_index)
            row_cells.append(f'<c r="{col}{index}" t="s"><v>{len(strings) - 1}</v></c>')
        cells.append(f'<row r="{index}">{"".join(row_cells)}</row>')
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


def _ourairports_csv() -> bytes:
    return (
        "id,ident,type,name,latitude_deg,longitude_deg,elevation_ft,continent,"
        "iso_country,iso_region,municipality,scheduled_service,gps_code,iata_code,"
        "local_code,home_link,wikipedia_link,keywords\n"
        "1,KPDX,large_airport,Portland Intl,45.58,-122.59,31,NA,US,US-OR,Portland,"
        "yes,KPDX,PDX,PDX,https://www.portofportland.com/PDX,,\n"
        "2,EGLL,large_airport,Heathrow,51.47,-0.45,83,EU,GB,GB-ENG,London,yes,"
        "EGLL,LHR,LHR,https://www.heathrow.com/,,\n"
        "3,PDX,closed,Old Field,45.5,-122.5,20,NA,US,US-OR,Portland,no,,,PDX,"
        "javascript:alert(1),,\n"
    ).encode("utf-8")


PDX_NASR = {
    "ARPT_ID": "PDX",
    "ARPT_NAME": "PORTLAND INTL",
    "CITY": "PORTLAND",
    "STATE_CODE": "OR",
    "COUNTY_NAME": "MULTNOMAH",
    "ICAO_ID": "KPDX",
    "LAT_DECIMAL": "45.5887",
    "LONG_DECIMAL": "-122.5968",
    "ELEV": "31",
    "OWNERSHIP_TYPE_CODE": "PU",
    "FACILITY_USE_CODE": "PU",
    "SITE_TYPE_CODE": "A",
    "EFF_DATE": "2026-08-06",
}


def test_nasr_zip_keeps_public_airports_not_private_or_heliports() -> None:
    data = _nasr_zip(
        [
            PDX_NASR,
            {**PDX_NASR, "ARPT_ID": "7S3", "ARPT_NAME": "STARKS TWIN OAKS", "FACILITY_USE_CODE": "PR"},
            {**PDX_NASR, "ARPT_ID": "59S", "ARPT_NAME": "PROSPECT STATE", "ICAO_ID": ""},
            {**PDX_NASR, "ARPT_ID": "61J", "SITE_TYPE_CODE": "H", "ARPT_NAME": "SOME HELIPORT"},
            {**PDX_NASR, "ARPT_ID": "2S1", "SITE_TYPE_CODE": "C", "ARPT_NAME": "SEAPLANE BASE"},
        ]
    )
    lids = {row["lid"] for row in parse_nasr_apt_zip(data)}
    assert lids == {"PDX", "59S", "2S1"}


def test_nasr_zip_attaches_runways_and_fuel() -> None:
    data = _nasr_zip(
        [
            {
                **PDX_NASR,
                "FUEL_TYPES": "A 100LL",
                "TRNS_STRG_HGR_FLAG": "Y",
                "TRNS_STRG_TIE_FLAG": "Y",
            }
        ],
        runways=[
            {"ARPT_ID": "PDX", "RWY_ID": "10R/28L", "RWY_LEN": "11000", "RWY_WIDTH": "150", "SURFACE_TYPE_CODE": "CONC-G"},
            {"ARPT_ID": "PDX", "RWY_ID": "H1", "RWY_LEN": "40", "RWY_WIDTH": "40"},
        ],
    )
    rows = {row["lid"]: row for row in parse_nasr_apt_zip(data)}
    assert rows["PDX"]["runways"] == [
        {"id": "10R/28L", "length_ft": 11000, "width_ft": 150, "surface": "CONC-G"}
    ]
    assert rows["PDX"]["fuel"] == "Jet A · 100LL"
    assert rows["PDX"]["hangar_storage"] is True
    merged = {airport.lid: airport for airport in merge_airports(list(rows.values()), [])}
    assert merged["PDX"].runways[0]["length_ft"] == 11000
    assert merged["PDX"].fuel == "Jet A · 100LL"


def test_nasr_keeps_row_when_lat_long_is_not_numeric() -> None:
    data = _nasr_zip([{**PDX_NASR, "LAT_DECIMAL": "N/A", "LONG_DECIMAL": "oops"}])
    rows = parse_nasr_apt_zip(data)
    assert rows[0]["lid"] == "PDX"
    assert rows[0]["latitude"] is None
    assert rows[0]["longitude"] is None


def test_merge_uses_nasr_as_superset_and_npias_as_likelihood() -> None:
    nasr = parse_nasr_apt_zip(_nasr_zip([PDX_NASR, {**PDX_NASR, "ARPT_ID": "59S", "ICAO_ID": ""}]))
    npias = parse_appendix_a_bytes(
        _npias_xlsx(
            [
                {
                    "state_name": "Oregon",
                    "city": "Portland",
                    "name": "Portland International",
                    "lid": "PDX",
                    "ownership": "PU",
                    "svc": "P",
                    "hub": "L",
                    "role": "",
                },
                {
                    "state_name": "Oregon",
                    "city": "Nowhere",
                    "name": "Only In Npias",
                    "lid": "ZZZ",
                    "ownership": "PU",
                    "svc": "GA",
                    "hub": "",
                    "role": "Local",
                },
            ]
        )
    )
    merged = {airport.lid: airport for airport in merge_airports(nasr, npias, npias_edition="2025-2029")}
    assert merged["PDX"].in_npias is True
    assert merged["PDX"].name == "Portland International"
    assert merged["PDX"].icao == "KPDX"
    assert merged["PDX"].npias_role == "large_hub"
    assert merged["PDX"].county == "Multnomah"
    assert merged["PDX"].elevation_ft == 31
    assert merged["PDX"].city == "Portland"
    assert merged["59S"].in_npias is False
    assert merged["59S"].npias_role is None
    assert merged["ZZZ"].in_npias is True
    assert "nasr" in merged["PDX"].sources
    assert "npias" in merged["PDX"].sources


def test_preserve_admitted_keeps_issue_airports() -> None:
    snapshot = merge_airports(
        parse_nasr_apt_zip(_nasr_zip([PDX_NASR])),
        [],
    )
    existing = [
        Airport(lid="PDX", name="old", city="Portland", state="OR"),
        Airport(lid="XYZ", name="Private Strip", city="X", state="OR", admitted=True, sources=["intake"]),
    ]
    kept = {airport.lid: airport for airport in preserve_admitted(snapshot, existing, set())}
    assert kept["PDX"].name != "old"
    assert kept["XYZ"].admitted is True


def test_current_apt_csv_url_from_listing() -> None:
    html = """
    <p>Subscription effective August 06, 2026</p>
    <a href="https://nfdc.faa.gov/webContent/28DaySub/extra/06_Aug_2026_APT_CSV.zip">APT CSV</a>
    """
    assert current_apt_csv_url(html).endswith("06_Aug_2026_APT_CSV.zip")
    dated = '<a href="/air_traffic/flight_info/aeronav/aero_data/NASR_Subscription/2026-08-06">Current</a>'
    assert current_apt_csv_url(dated).endswith("06_Aug_2026_APT_CSV.zip")


def test_should_refresh_missing_or_new_month(tmp_path: Path) -> None:
    path = tmp_path / "airports.jsonl"
    august = datetime(2026, 8, 18, 12, 0, tzinfo=PACIFIC)
    assert should_refresh(path, now=august) is True
    path.write_text("{}\n", encoding="utf-8")
    july = datetime(2026, 7, 2, 12, 0, tzinfo=PACIFIC).timestamp()
    os.utime(path, (july, july))
    assert should_refresh(path, now=august) is True
    now_ts = august.timestamp()
    os.utime(path, (now_ts, now_ts))
    assert should_refresh(path, now=august) is False


def test_should_refresh_empty_file(tmp_path: Path) -> None:
    august = datetime(2026, 8, 18, 12, 0, tzinfo=PACIFIC)
    empty = tmp_path / "grants.jsonl"
    empty.write_text("", encoding="utf-8")
    os.utime(empty, (august.timestamp(), august.timestamp()))
    assert should_refresh(empty, now=august) is True
    whitespace = tmp_path / "airports.jsonl"
    whitespace.write_text("\n\n", encoding="utf-8")
    os.utime(whitespace, (august.timestamp(), august.timestamp()))
    assert should_refresh(whitespace, now=august) is True


def test_refresh_writes_overlay_from_fixtures(tmp_path: Path) -> None:
    nasr = _nasr_zip([PDX_NASR, {**PDX_NASR, "ARPT_ID": "59S", "ICAO_ID": ""}])
    npias = _npias_xlsx(
        [
            {
                "state_name": "Oregon",
                "city": "Portland",
                "name": "Portland International",
                "lid": "PDX",
                "ownership": "PU",
                "svc": "P",
                "hub": "L",
                "role": "",
            }
        ]
    )
    listing = b'<a href="https://nfdc.faa.gov/webContent/28DaySub/extra/06_Aug_2026_APT_CSV.zip">z</a>'
    ourairports = _ourairports_csv()

    def fake_fetch(url: str, timeout: int = 60) -> tuple[bytes, int]:
        if "NASR_Subscription" in url:
            return listing, 200
        if url.endswith("_APT_CSV.zip"):
            return nasr, 200
        if "npias" in url.lower():
            return npias, 200
        if "ourairports" in url.lower() or "davidmegginson" in url.lower():
            return ourairports, 200
        found = grant_bytes_for(url)
        if found is not None:
            return found
        raise AssertionError(url)

    overlay = tmp_path / "overlay"
    write_airports_overlay(
        overlay,
        [Airport(lid="XYZ", name="Hinted", city="", state="OR", admitted=True, sources=["intake"])],
    )
    count = maybe_refresh(overlay, force=True, fetch=fake_fetch, sleep=lambda _s: None)
    assert count is not None
    airports = {item.lid: item for item in load_airports_overlay(overlay)}
    assert airports["PDX"].in_npias is True
    assert airports["PDX"].county == "Multnomah"
    assert airports["PDX"].elevation_ft == 31
    assert airports["PDX"].website == "https://www.portofportland.com/PDX"
    assert airports["PDX"].iata == "PDX"
    assert "ourairports" in airports["PDX"].sources
    assert airports["59S"].in_npias is False
    assert airports["XYZ"].admitted is True
    catalog = seed_catalog(ROOT / "catalog", overlay_dir=overlay)
    assert "PDX" in catalog.airports_by_lid
    assert "59S" in catalog.airports_by_lid
    assert "XYZ" in catalog.airports_by_lid


def test_refresh_writes_nasr_when_ourairports_fails(tmp_path: Path) -> None:
    nasr = _nasr_zip([PDX_NASR])
    npias = _npias_xlsx(
        [
            {
                "state_name": "Oregon",
                "city": "Portland",
                "name": "Portland International",
                "lid": "PDX",
                "ownership": "PU",
                "svc": "P",
                "hub": "L",
                "role": "",
            }
        ]
    )
    listing = b'<a href="https://nfdc.faa.gov/webContent/28DaySub/extra/06_Aug_2026_APT_CSV.zip">z</a>'

    def fake_fetch(url: str, timeout: int = 60) -> tuple[bytes, int]:
        if "NASR_Subscription" in url:
            return listing, 200
        if url.endswith("_APT_CSV.zip"):
            return nasr, 200
        if "npias" in url.lower():
            return npias, 200
        if "ourairports" in url.lower() or "davidmegginson" in url.lower():
            raise PermissionError("robots.txt disallows OurAirports")
        found = grant_bytes_for(url)
        if found is not None:
            return found
        raise AssertionError(url)

    overlay = tmp_path / "overlay"
    count = maybe_refresh(overlay, force=True, fetch=fake_fetch, sleep=lambda _s: None)
    assert count is not None
    airports = {item.lid: item for item in load_airports_overlay(overlay)}
    assert airports["PDX"].in_npias is True
    assert airports["PDX"].county == "Multnomah"
    assert airports["PDX"].website is None


def test_npias_edition_from_url() -> None:
    assert npias_edition_from_url(
        "https://www.faa.gov/sites/faa.gov/files/airports/planning_capacity"
        "/npias/current/ARP-NPIAS-2025-2029-AppendixA.xlsx"
    ) == "2025-2029"


def test_ourairports_maps_us_lid_and_skips_foreign() -> None:
    rows = parse_ourairports_csv(_ourairports_csv())
    assert rows["PDX"]["website"] == "https://www.portofportland.com/PDX"
    assert rows["PDX"]["iata"] == "PDX"
    assert "LHR" not in rows


def test_ourairports_prefers_open_airport_over_closed() -> None:
    csv_text = (
        "id,ident,type,name,latitude_deg,longitude_deg,elevation_ft,continent,"
        "iso_country,iso_region,municipality,scheduled_service,gps_code,iata_code,"
        "local_code,home_link,wikipedia_link,keywords\n"
        "1,XX1,closed,Old,0,0,0,NA,US,US-OR,X,no,,,ABC,https://example.com/old,,\n"
        "2,KABC,small_airport,New,0,0,0,NA,US,US-OR,X,no,KABC,,ABC,https://example.com/new,,\n"
    )
    rows = parse_ourairports_csv(csv_text)
    assert rows["ABC"]["website"] == "https://example.com/new"


def test_ourairports_skips_non_http_home_link() -> None:
    csv_text = (
        "id,ident,type,name,latitude_deg,longitude_deg,elevation_ft,continent,"
        "iso_country,iso_region,municipality,scheduled_service,gps_code,iata_code,"
        "local_code,home_link,wikipedia_link,keywords\n"
        "1,KZZZ,small_airport,Z,0,0,0,NA,US,US-OR,Z,no,KZZZ,,ZZZ,javascript:alert(1),,\n"
    )
    assert parse_ourairports_csv(csv_text) == {}


def test_apply_ourairports_fills_blank_website() -> None:
    airport = Airport(lid="PDX", name="Portland", city="Portland", state="OR", sources=["nasr"])
    updated = apply_ourairports([airport], parse_ourairports_csv(_ourairports_csv()))[0]
    assert updated.website == "https://www.portofportland.com/PDX"
    assert updated.iata == "PDX"
    assert "ourairports" in updated.sources
    kept = apply_ourairports(
        [Airport(lid="PDX", name="Portland", city="Portland", state="OR", website="https://example.com/pdx")],
        parse_ourairports_csv(_ourairports_csv()),
    )[0]
    assert kept.website == "https://example.com/pdx"
