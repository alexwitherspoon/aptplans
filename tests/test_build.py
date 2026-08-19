import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_build():
    spec = importlib.util.spec_from_file_location(
        "aptplans_build", ROOT / "site" / "build.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_build_writes_index_and_css(tmp_path: Path) -> None:
    from catalog.seed import seed_catalog

    build = _load_build()
    out = tmp_path / "dist"
    build.build(out, catalog=seed_catalog(ROOT / "catalog"))

    index = (out / "index.html").read_text(encoding="utf-8")
    about = (out / "about" / "index.html").read_text(encoding="utf-8")
    css = out / "css" / "styles.css"

    assert "Airport Layout Plan" in index
    assert "Airport Layout Plan" in about
    assert "not legal advice" in about.lower()
    assert css.is_file()
    assert "canonical" in index.lower() or 'rel="canonical"' in index
    assert (out / "robots.txt").is_file()
    assert "Sitemap:" in (out / "robots.txt").read_text(encoding="utf-8")
    sitemap = (out / "sitemap.xml").read_text(encoding="utf-8")
    assert "/airports/PDX/" in sitemap
    assert "/states/OR/" in sitemap
    pdx = (out / "airports" / "PDX" / "index.html").read_text(encoding="utf-8")
    assert "Portland" in pdx
    assert "Official" in (out / "documents" / "pdx-2045-existing-conditions" / "index.html").read_text(
        encoding="utf-8"
    )
    assert "not an FAA" in pdx.lower() or "unofficial" in pdx.lower()
    assert "Federal funding" in pdx
    assert "Airport Layout Plans" in pdx
    assert "Master plans" in pdx
    assert "State aviation law" in pdx
    assert "Oregon Department of Aviation" in pdx
    assert "Airports and Landing Fields" in pdx
    oregon = (out / "states" / "OR" / "index.html").read_text(encoding="utf-8")
    assert "Oregon Department of Aviation" in oregon
    assert "or-ors-836" in (out / "documents" / "or-ors-836" / "index.html").read_text(encoding="utf-8")
    home = (out / "index.html").read_text(encoding="utf-8")
    assert "state law records" in home
    sitemap = (out / "sitemap.xml").read_text(encoding="utf-8")
    assert "/feeds/laws.xml" in sitemap
    assert "AIP grant histories" in pdx
    assert "KPDX" in pdx
    assert "large hub" in pdx
    assert "official url is listed" in pdx.lower()
    assert "Official source" in pdx
    assert "pdx2045.org" in pdx
    assert "Reconstruct Taxiway" in pdx
    assert "$61,876,159" in pdx
    assert "24 Jul 2025" in pdx
    assert "Rehabilitate Taxiway" in pdx
    assert "USAspending award" in pdx
    assert "ASST_NON_34100480992025_069" in pdx
    assert "awarded" in pdx
    assert "$30,839,446 spent" in pdx
    assert "$31,090,807 still obligated" in pdx
    assert "$0 spent · $696,721 still obligated" in pdx
    assert "$6,711,334 spent · $1,109,209 still obligated" in pdx
    oregon = (out / "states" / "OR" / "index.html").read_text(encoding="utf-8")
    assert "PDX" in oregon
    status = (out / "status.json").read_text(encoding="utf-8")
    assert "airports" in status
    assert (out / "feeds" / "all.xml").is_file()
    assert (out / "data" / "catalog.csv").is_file()
    assert (out / "data" / "search.json").is_file()
    assert "NPIAS" in index


def test_build_airport_lists_grants(tmp_path: Path) -> None:
    from catalog.models import Grant
    from catalog.seed import seed_catalog
    from catalog.store import Catalog

    build = _load_build()
    base = seed_catalog(ROOT / "catalog")
    catalog = Catalog(
        airports=base.airports,
        states=base.states,
        documents=base.documents,
        changes=base.changes,
        grants=[
            Grant(
                airport_lid="PDX",
                fiscal_year=2025,
                amount=500000,
                description="Update Airport Master Plan Study",
                programs=["AIP"],
                is_planning=True,
                grant_number="3-41-0048-064-2025",
                award_date="2025-07-09",
                obligated=500000,
                outlayed=100000,
            ),
            Grant(
                airport_lid="PDX",
                fiscal_year=2024,
                amount=1200000,
                description="Reconstruct Taxiway",
                programs=["AIG"],
                grant_number="3-41-0048-094-2024",
                obligated=1200000,
                outlayed=800000,
            ),
        ],
    )
    out = tmp_path / "dist"
    build.build(out, catalog=catalog)
    pdx = (out / "airports" / "PDX" / "index.html").read_text(encoding="utf-8")
    assert "Update Airport Master Plan Study" in pdx
    assert "$500,000" in pdx
    assert "$1,200,000" in pdx
    assert "$1,700,000" in pdx
    assert "planning" in pdx
    assert "3-41-0048-064-2025" in pdx
    assert "FY 2025 $500,000" in pdx
    assert "9 Jul 2025" in pdx
    assert "Planning grants" not in pdx
    assert "planning grant" in pdx
    assert "https://www.usaspending.gov/award/ASST_NON_34100480642025_069" in pdx
    assert "https://www.faa.gov/airports/aip/grant_histories/2025" in pdx
    assert "$100,000 spent · $400,000 still obligated" in pdx
    assert "$900,000 spent" in pdx
    assert "$800,000 still obligated" in pdx


def test_grant_briefing_totals_planning_and_years() -> None:
    from catalog.models import Grant

    build = _load_build()
    stats = build.grant_briefing(
        [
            Grant(
                airport_lid="PDX",
                fiscal_year=2025,
                amount=100,
                is_planning=True,
                obligated=100,
                outlayed=40,
            ),
            Grant(
                airport_lid="PDX",
                fiscal_year=2024,
                amount=50,
                is_planning=False,
                obligated=50,
                outlayed=0,
            ),
        ]
    )
    assert stats["count"] == 2
    assert stats["total"] == 150
    assert stats["year_min"] == 2024
    assert stats["year_max"] == 2025
    assert stats["planning_count"] == 1
    assert stats["by_year"] == [(2025, 100), (2024, 50)]
    assert stats["spent"] == 40
    assert stats["remaining"] == 110
    assert stats["with_outlays"] == 2
    none = build.grant_briefing(
        [Grant(airport_lid="PDX", fiscal_year=2025, amount=100)]
    )
    assert none["spent"] is None
    assert none["remaining"] is None


def test_grant_title_uses_first_clause() -> None:
    build = _load_build()
    long_desc = "Reconstruct Taxiway, Rehabilitate Taxiway, Rehabilitate Taxiway"
    assert build.grant_title(long_desc) == "Reconstruct Taxiway"
    assert build.grant_brief(long_desc) == "Rehabilitate Taxiway, Rehabilitate Taxiway"
    assert build.grant_title("Improve Terminal") == "Improve Terminal"
    assert build.grant_brief("Improve Terminal") == ""
