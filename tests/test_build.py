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
    assert "class=\"about-hero\"" in about
    assert "processing" in about.lower()
    assert "saved copies" in about.lower()
    assert css.is_file()
    assert "canonical" in index.lower() or 'rel="canonical"' in index
    assert (out / "robots.txt").is_file()
    robots = (out / "robots.txt").read_text(encoding="utf-8")
    assert "Sitemap:" in robots
    assert "Disallow: /data/" in robots
    sitemap = (out / "sitemap.xml").read_text(encoding="utf-8")
    assert "/airports/PDX/" in sitemap
    assert "/states/OR/" in sitemap
    assert "/search/" in sitemap
    assert "/feeds/airports/PDX.xml" in sitemap
    assert "/css/" not in sitemap
    assert "/data/" not in sitemap
    assert "SearchAction" in index
    assert '"@type":"WebSite"' in index
    assert 'property="og:title"' in index
    assert 'rel="sitemap"' in index
    pdx = (out / "airports" / "PDX" / "index.html").read_text(encoding="utf-8")
    assert '"@type":"Airport"' in pdx
    assert "BreadcrumbList" in pdx
    assert "Portland" in pdx
    assert "Official" in (out / "documents" / "pdx-2045-existing-conditions" / "index.html").read_text(
        encoding="utf-8"
    )
    assert "not an FAA" in pdx.lower() or "unofficial" in pdx.lower()
    assert "Funding" in pdx
    assert "Funding on file:" in pdx
    assert 'id="funding"' in pdx
    assert "No local municipal funding" in pdx
    assert "No state funding is listed yet for this airport" in pdx
    assert "Airport Layout Plans" in pdx
    assert "Master plans" in pdx
    assert "State aviation law" in pdx
    assert "Oregon Department of Aviation" in pdx
    assert "Airports and Landing Fields" in pdx
    oregon = (out / "states" / "OR" / "index.html").read_text(encoding="utf-8")
    assert "Oregon Department of Aviation" in oregon
    assert "Aviation budget" in oregon
    assert 'id="budget"' in oregon
    assert 'id="projects"' in oregon
    assert "$45,874,157" in oregon
    assert "Aviation System Action Program" in oregon
    assert "By fund type" in oregon
    assert "Projects and allocations" in oregon
    assert "Improve Terminal" in oregon
    assert "more award" in oregon
    assert "/airports/PDX/#funding" in oregon
    assert "No grants listed yet for this state" in (
        out / "states" / "CO" / "index.html"
    ).read_text(encoding="utf-8")
    assert "or-ors-836" in (out / "documents" / "or-ors-836" / "index.html").read_text(encoding="utf-8")
    inventory = (out / "documents" / "4s9-2008-inventory" / "index.html").read_text(encoding="utf-8")
    assert "Unofficial. Chapter 2 inventories Mulino" in inventory
    assert '"@type":"CreativeWork"' in inventory
    home = (out / "index.html").read_text(encoding="utf-8")
    assert "Send a link" in home
    assert "class=\"home-stats\"" not in home
    assert "class=\"state-index\"" not in home
    sitemap = (out / "sitemap.xml").read_text(encoding="utf-8")
    assert "/feeds/laws.xml" in sitemap
    assert "<loc>https://aptplans.org/feeds/</loc>" in sitemap
    assert "<loc>https://aptplans.org/feeds/laws.xml</loc>" in sitemap
    feeds_page = (out / "feeds" / "index.html").read_text(encoding="utf-8")
    assert "/feeds/all.xml" in feeds_page
    assert "/feeds/laws.xml" in feeds_page
    assert "/feeds/states/OR.xml" in feeds_page
    assert "/feeds/airports/PDX.xml" in feeds_page
    assert "Everything new" in feeds_page
    assert 'type="application/rss+xml"' in index
    assert 'href="/feeds/all.xml"' in index
    assert 'href="/feeds/airports/PDX.xml"' in pdx
    assert 'type="application/rss+xml"' in pdx
    assert 'href="/feeds/states/OR.xml"' in oregon
    assert 'href="/feeds/laws.xml"' in oregon
    assert "<link>https://aptplans.org/feeds/</link>" in (out / "feeds" / "all.xml").read_text(
        encoding="utf-8"
    )
    assert "AIP grant histories" in pdx
    assert "KPDX" in pdx
    assert "large hub" in pdx
    assert "official link listed" in pdx.lower()
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
    assert "$31,090,807 not yet spent" in pdx
    assert "$0 spent · $696,721 not yet spent" in pdx
    assert "$6,711,334 spent · $1,109,209 not yet spent" in pdx
    oregon = (out / "states" / "OR" / "index.html").read_text(encoding="utf-8")
    assert "PDX" in oregon
    status = (out / "status.json").read_text(encoding="utf-8")
    assert "airports" in status
    assert (out / "feeds" / "all.xml").is_file()
    assert (out / "data" / "catalog.csv").is_file()
    assert (out / "data" / "search.json").is_file()
    assert "/js/suggest.js" in index
    assert (out / "js" / "suggest.js").is_file()
    suggest = (out / "js" / "suggest.js").read_text(encoding="utf-8")
    assert "7700" not in suggest
    assert "Catalog titles first" in suggest
    assert "/data/search.json" in suggest
    search_page = (out / "search" / "index.html").read_text(encoding="utf-8")
    assert "/search/query" in search_page
    assert "/data/search.json" in search_page
    assert "remove building" in search_page
    assert "modify runway" in search_page
    assert "matchingStrategy" in search_page
    assert "kind IN [master_plan, alp, other]" in search_page
    assert "if (!q) return;" in search_page
    assert "slice(0, 25)" not in search_page


def test_sitemap_xml_lists_pages_and_optional_lastmod() -> None:
    build = _load_build()
    xml = build._sitemap_xml({"/": None, "/airports/PDX/": "2026-08-06", "/feeds/all.xml": None})
    assert xml.index("/airports/PDX/") < xml.index("/feeds/all.xml")
    assert "<lastmod>2026-08-06</lastmod>" in xml
    assert xml.count("<lastmod>") == 1
    assert "<loc>https://aptplans.org/</loc>" in xml


def test_sitemap_day_picks_latest_iso_date() -> None:
    build = _load_build()
    assert build._sitemap_day(None, "2020-01-01T12:00:00Z", "2021-06-15") == "2021-06-15"
    assert build._sitemap_day("", None) is None


def test_build_skips_when_inputs_match(tmp_path: Path) -> None:
    from catalog.seed import seed_catalog

    build = _load_build()
    catalog = seed_catalog(ROOT / "catalog")
    out = tmp_path / "dist"
    assert build.build(out, catalog=catalog) is True
    index = out / "index.html"
    stamp = index.stat().st_mtime_ns
    generated = (out / "status.json").read_text(encoding="utf-8")
    assert build.build(out, catalog=catalog) is False
    assert index.stat().st_mtime_ns == stamp
    assert (out / "status.json").read_text(encoding="utf-8") == generated


def test_build_generates_when_catalog_changes(tmp_path: Path) -> None:
    from catalog.models import Grant
    from catalog.seed import seed_catalog
    from catalog.store import Catalog

    build = _load_build()
    base = seed_catalog(ROOT / "catalog")
    out = tmp_path / "dist"
    assert build.build(out, catalog=base) is True
    catalog = Catalog(
        airports=base.airports,
        states=base.states,
        documents=base.documents,
        changes=base.changes,
        grants=list(base.grants)
        + [
            Grant(
                airport_lid="PDX",
                fiscal_year=2026,
                amount=1,
                description="Skip-check grant",
            )
        ],
        budgets=base.budgets,
    )
    assert build.build(out, catalog=catalog) is True
    assert "Skip-check grant" in (out / "airports" / "PDX" / "index.html").read_text(
        encoding="utf-8"
    )


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
    assert "$100,000 spent · $400,000 not yet spent" in pdx
    assert "$900,000 spent" in pdx
    assert "$800,000 not yet spent" in pdx


def test_funding_sections_group_by_level() -> None:
    from catalog.models import Grant

    build = _load_build()
    sections = {item["level"]: item for item in build.funding_sections(
        [
            Grant(airport_lid="PDX", amount=100, level="federal"),
            Grant(airport_lid="PDX", amount=40, level="state", entity="Oregon Department of Aviation"),
            Grant(airport_lid="PDX", amount=10, level="local", entity="City of Portland"),
        ]
    )}
    assert sections["federal"]["stats"]["total"] == 100
    assert sections["state"]["stats"]["total"] == 40
    assert sections["local"]["stats"]["total"] == 10
    assert sections["other"]["grants"] == []


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


def test_project_groups_sort_by_lid() -> None:
    from catalog.models import Airport, Grant
    from catalog.store import Catalog

    build = _load_build()
    catalog = Catalog(
        airports=[
            Airport(lid="PDX", name="Portland Intl", city="Portland", state="OR"),
            Airport(lid="HIO", name="Hillsboro", city="Hillsboro", state="OR"),
        ]
    )
    groups = build.project_groups(
        catalog,
        [
            Grant(airport_lid="PDX", amount=100, description="Taxiway"),
            Grant(airport_lid="HIO", amount=40, description="Apron"),
        ],
    )
    assert [item["lid"] for item in groups] == ["HIO", "PDX"]
    assert groups[0]["stats"]["total"] == 40
    assert groups[1]["name"] == "Portland Intl"
    assert groups[0]["more"] == 0


def test_airport_brief_states_plans_and_funding() -> None:
    from catalog.models import Airport, Document, Grant, State

    build = _load_build()
    airport = Airport(lid="PDX", name="Portland Intl", city="Portland", state="OR")
    lines = build.airport_brief(
        airport=airport,
        documents=[
            Document(
                id="alp",
                kind="alp",
                source_url="https://example.com/alp.pdf",
                completeness="link_only",
            )
        ],
        funding=build.funding_sections(
            [Grant(airport_lid="PDX", amount=100, description="Taxiway")]
        ),
        state=State(code="OR", name="Oregon"),
    )
    assert any("Airport Layout Plan is listed" in line for line in lines)
    assert any("1 federal grant totaling $100" in line for line in lines)
    assert any("Oregon aviation law" in line for line in lines)


def test_grant_title_uses_first_clause() -> None:
    build = _load_build()
    long_desc = "Reconstruct Taxiway, Rehabilitate Taxiway, Rehabilitate Taxiway"
    assert build.grant_title(long_desc) == "Reconstruct Taxiway"
    assert build.grant_brief(long_desc) == "Rehabilitate Taxiway, Rehabilitate Taxiway"
    assert build.grant_title("Improve Terminal") == "Improve Terminal"
    assert build.grant_brief("Improve Terminal") == ""
