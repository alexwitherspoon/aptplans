import importlib.util
import re
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
    assert "queued" in about.lower()
    assert "pipeline-panel" in about
    assert "published saved copies" in about.lower()
    assert css.is_file()
    assert "canonical" in index.lower() or 'rel="canonical"' in index
    assert (out / "robots.txt").is_file()
    robots = (out / "robots.txt").read_text(encoding="utf-8")
    assert "Sitemap:" in robots
    assert "Disallow: /data/" in robots
    assert "Disallow: /review/" in robots
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
    pdx_doc = (out / "documents" / "pdx-2045-existing-conditions" / "index.html").read_text(
        encoding="utf-8"
    )
    alp = (out / "documents" / "4s9-2019-alp" / "index.html").read_text(encoding="utf-8")
    assert "Official" in pdx_doc
    assert re.search(r'/js/preview\.js\?v=[0-9a-f]{12}', pdx_doc)
    assert "ODA_Doc_4S9_ALP.pdf" in alp
    assert 'class="file-frame"' not in alp
    assert "not an FAA" in pdx.lower() or "unofficial" in pdx.lower()
    assert "Funding" in pdx
    assert "class=\"featured\"" in pdx
    assert "year-bars" in pdx
    assert 'id="funding"' in pdx
    assert "No local municipal funding" in pdx
    assert "No state funding is listed yet for this airport" in pdx
    assert "Airport Layout Plan" in pdx
    assert "Master plan" in pdx
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
    assert "New documents" in home
    assert "Planned growth" in home
    assert "Planned decline" in home
    assert home.find("New documents") < home.find("Planned growth") < home.find("Planned decline")
    assert 'href="/airports/TTD/"' in home
    assert "Troutdale" in home
    assert 'href="/search/?outlook=growing"' in home
    assert 'href="/search/?outlook=declining"' in home
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
    assert "grant histories" in pdx
    assert "aip/grant_histories" in pdx
    assert "KPDX" in pdx
    assert "<span>KPDX</span>" not in pdx
    assert "Portland International Airport (" in pdx
    assert "FAA location identifier" in pdx
    assert "Multnomah County" in pdx
    assert "Portland, Multnomah County, Oregon" in pdx
    assert "Public-use" in pdx
    assert "publicly owned" in pdx
    assert "large hub" in pdx
    assert "portofportland.com/PDX" in pdx
    assert 'href="https://www.portofportland.com/PDX"' in pdx
    assert "Sections:" in pdx
    assert 'href="#plans"' in pdx
    assert 'href="#funding"' in pdx
    assert 'href="#law"' in pdx
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
    search_blob = (out / "data" / "search.json").read_text(encoding="utf-8")
    assert "planning outlook declining" in search_blob
    assert '"outlook": "declining"' in search_blob
    assert "/js/suggest.js" in index
    assert (out / "js" / "suggest.js").is_file()
    suggest = (out / "js" / "suggest.js").read_text(encoding="utf-8")
    assert "7700" not in suggest
    assert "Catalog titles first" in suggest
    assert "/data/search.json" in suggest
    assert "catalogUrl" in suggest
    assert 'getAttribute("data-cache")' in suggest
    assert "type = airport AND outlook" in suggest
    assert re.search(r'/css/styles\.css\?v=[0-9a-f]{12}', index)
    assert re.search(r'/js/suggest\.js\?v=[0-9a-f]{12}', index)
    assert re.search(r'data-cache="[0-9a-f]{12}"', index)
    search_page = (out / "search" / "index.html").read_text(encoding="utf-8")
    assert "/search/query" in search_page
    assert "/data/search.json" in search_page
    assert "catalogUrl" in search_page
    assert "remove building" in search_page
    assert "modify runway" in search_page
    assert "matchingStrategy" in search_page
    assert "kind IN [master_plan, alp, other]" in search_page
    assert 'name="outlook"' in search_page
    assert 'value="growing"' in search_page
    assert 'value="declining"' in search_page
    assert "type = airport AND outlook" in search_page
    assert "if (!q && !outlook) return;" in search_page
    assert "slice(0, 25)" not in search_page
    mulino = (out / "airports" / "4S9" / "index.html").read_text(encoding="utf-8")
    assert "Mulino State Airport Master Plan" in mulino
    assert "Mulino State Airport Layout Plan" in mulino
    assert 'href="/documents/4s9-2019-amp/"' in mulino
    assert 'href="/documents/4s9-2019-alp/"' in mulino
    assert "No Airport Layout Plan is listed yet" not in mulino
    assert "Chapter 5 Alternatives" in mulino
    assert "<h3><a href=\"/documents/4s9-2008-alternatives/\">Mulino Airport Master Plan Update Chapter 5 Alternatives</a></h3>" not in mulino
    assert "Earlier editions" in mulino
    assert "Mulino Airport Master Plan Update" in mulino
    assert "plan-brief" in mulino
    assert "Working toward" not in mulino
    assert "3,425" in mulino
    assert "14/32, 3,425 by 100 ft" in mulino
    assert "1 · Runway" not in mulino
    assert "260 ft" in mulino
    assert "T-hangars" in mulino
    pdx_page = (out / "airports" / "PDX" / "index.html").read_text(encoding="utf-8")
    assert "plan-brief" in pdx_page
    assert "Working toward" not in pdx_page
    assert "31 ft" in pdx_page
    assert "11,000" in pdx_page
    assert "10R/28L, 11,000 by 150 ft · Concrete" in pdx_page
    assert "10L/28R, 9,825 by 150 ft · Asphalt" in pdx_page
    pdx_brief = pdx_page.split('class="plan-brief', 1)[1].split("</aside>", 1)[0]
    assert "<br>" in pdx_brief
    assert "outlook-growing" in pdx_page
    ttd = (out / "airports" / "TTD" / "index.html").read_text(encoding="utf-8")
    assert "outlook-declining" in ttd
    hood = (out / "airports" / "4S2" / "index.html").read_text(encoding="utf-8")
    assert "638 ft" in hood
    assert "3,040" in hood
    assert "7/25, 3,040 by 75 ft · Asphalt" in hood
    assert "plan-brief-row" in ttd
    assert "has-outlook" in ttd
    outlook_html = ttd.split('class="outlook', 1)[1].split("</figure>", 1)[0]
    assert "Planning Outlook" in outlook_html
    assert "may not come to pass" in outlook_html
    assert "<p>" not in outlook_html
    assert "Alternative C" not in outlook_html
    ttd_doc = (out / "documents" / "ttd-2016-shaping-our-future" / "index.html").read_text(
        encoding="utf-8"
    )
    assert "outlook-declining" not in ttd_doc
    assert "Improve Terminal" in pdx_page or "Reconstruct Taxiway" in pdx_page
    assert "<dt></dt>" not in mulino
    assert "oregon.gov/aviation/airports/pages/mulino-4s9.aspx" in mulino
    amp_page = (out / "documents" / "4s9-2019-amp" / "index.html").read_text(encoding="utf-8")
    assert "Listed on" in amp_page
    assert "mulino-4s9.aspx" in amp_page
    assert "Existing Conditions Report" in pdx
    assert "Earlier editions" not in pdx


def test_page_overview_ignores_current_overlay() -> None:
    from catalog.models import visible_on_site
    from catalog.seed import seed_catalog

    build = _load_build()
    catalog = seed_catalog(ROOT / "catalog")
    catalog.overviews["PDX"] = {
        "airport_lid": "PDX",
        "generated_at": "2026-08-20T12:00:00Z",
        "as_of": "1999",
        "facts": [["Elevation", "1 ft"]],
        "upcoming": [],
        "trajectory": {
            "band": "declining",
            "position": -1.0,
            "note": "stale overlay",
            "needle_x": 8.0,
            "needle_y": 28.0,
        },
    }
    airport = catalog.airports_by_lid["PDX"]
    docs = [
        document
        for document in catalog.documents_for_airport("PDX")
        if visible_on_site(document)
    ]
    overview = build.page_overview(
        airport,
        [build.featured_work(docs, "master_plan"), build.featured_work(docs, "alp")],
        catalog.grants_for_airport("PDX"),
    )
    assert overview is not None
    facts = dict(overview.facts)
    assert facts["Elevation"] == "31 ft"
    assert overview.trajectory is not None
    assert overview.trajectory.band == "growing"


def test_bust_url_uses_content_hash() -> None:
    build = _load_build()
    versions = build.static_asset_versions()
    css = versions["/css/styles.css"]
    js = versions["/js/suggest.js"]
    assert len(css) == 12
    assert css != js
    assert build.bust_url("/css/styles.css", versions) == f"/css/styles.css?v={css}"
    assert build.bust_url("/missing.css", versions) == "/missing.css"


def test_abbr_and_place_helpers() -> None:
    from catalog.models import Airport

    build = _load_build()
    html = str(build.abbr("PDX", "lid"))
    assert html.startswith("<abbr title=")
    assert "FAA location identifier" in html
    assert "PDX</abbr>" in html
    assert build.website_label("https://www.portofportland.com/PDX") == "portofportland.com/PDX"
    assert build.county_label("Multnomah") == "Multnomah County"
    assert build.county_label("Hood River County") == "Hood River County"
    airport = Airport(
        lid="PDX",
        name="Portland International Airport",
        city="Portland",
        state="OR",
        county="Multnomah",
        npias_role="large_hub",
        ownership="PU",
        facility_use="PU",
    )
    assert build.place_line(airport, type("S", (), {"name": "Oregon"})()) == (
        "Portland, Multnomah County, Oregon"
    )
    labels = [fact["label"] for fact in build.identity_facts(airport)]
    assert labels == ["Public-use", "publicly owned", "large hub"]
    assert "passenger boardings" in str(build.abbr("large hub", "large_hub"))
    assert "scheduled passenger service" in str(build.abbr("commercial service", "commercial_service"))
    assert build.public_website("https://www.flypdx.com/") == "https://www.flypdx.com/"
    assert build.public_website("javascript:alert(1)") == ""
    assert build.county_label("Municipality of Anchorage") == "Municipality of Anchorage"


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
    assert "FY 2025" in pdx
    assert "$500,000" in pdx
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


def test_document_page_shows_file_loading_state(tmp_path, monkeypatch) -> None:
    from catalog.seed import seed_catalog

    monkeypatch.setenv("APTPLANS_DEV_PREVIEW", "1")
    build = _load_build()
    out = tmp_path / "dist"
    build.build(out, catalog=seed_catalog(ROOT / "catalog"))
    alp = (out / "documents" / "4s9-2019-alp" / "index.html").read_text(encoding="utf-8")
    assert "file-wait" in alp
    assert "file-fail" in alp
    assert "is-loading" in alp
    assert "Loading the file" in alp
    assert "could not be loaded" in alp
    assert 'data-src="' in alp
    assert "#zoom=page-width" in alp
    assert "/js/preview.js" in alp
    assert (out / "js" / "preview.js").is_file()


def test_document_preview_prefers_saved_copy(monkeypatch) -> None:
    from catalog.models import Document

    build = _load_build()
    official_pdf = Document(
        id="4s9-2019-alp",
        kind="alp",
        source_url="https://www.oregon.gov/aviation/airports/Documents/4S9/ODA_Doc_4S9_ALP.pdf",
        completeness="link_only",
        media="pdf",
    )
    preview = build.document_preview(official_pdf)
    assert preview is None
    monkeypatch.setenv("APTPLANS_DEV_PREVIEW", "1")
    preview = build.document_preview(official_pdf)
    assert preview == {
        "src": "/files/preview/4s9-2019-alp.pdf#zoom=page-width",
        "media": "pdf",
        "origin": "official",
    }
    saved = official_pdf.overlay({"preserved_url": "/files/abc.pdf", "completeness": "complete"})
    assert build.document_preview(saved) == {
        "src": "/files/abc.pdf#zoom=page-width",
        "media": "pdf",
        "origin": "saved",
    }
    assert build.pdf_embed_src("/files/abc.pdf") == "/files/abc.pdf#zoom=page-width"
    html = Document(
        id="hub",
        kind="master_plan",
        source_url="https://pdx2045.org/",
        completeness="link_only",
        media="html",
    )
    assert build.document_preview(html) is None
    assert build.document_preview(html.overlay({"preserved_url": "/files/hub.html"}))["origin"] == "saved"
    notice = Document(
        id="news",
        kind="notice",
        source_url="https://example.com/news.pdf",
        completeness="link_only",
        media="pdf",
    )
    assert build.document_preview(notice) is None
    dead = official_pdf.overlay({"source_status": "dead"})
    assert build.document_preview(dead) is None


def test_featured_work_prefers_whole_plan_over_chapter() -> None:
    from catalog.models import Document

    build = _load_build()
    hub = Document(
        id="pdx-2045-hub",
        kind="master_plan",
        source_url="https://pdx2045.org/",
        completeness="link_only",
        title="PDX 2045 Master Plan Update",
        edition="2023-2026",
        airport_lid="PDX",
    )
    chapter = Document(
        id="pdx-2045-existing-conditions",
        kind="master_plan",
        source_url="https://example.com/existing.pdf",
        completeness="link_only",
        title="PDX 2045 Existing Conditions Report",
        edition="January 2025",
        airport_lid="PDX",
    )
    work = build.featured_work([chapter, hub], "master_plan")
    assert work is not None
    assert work.hub == hub
    assert build.featured_work([chapter, hub], "alp") is None
    assert [doc.id for doc in work.parts] == ["pdx-2045-existing-conditions"]


def test_featured_work_groups_split_chapters_and_later_hub() -> None:
    from catalog.models import Document

    build = _load_build()

    def chapter(doc_id: str, title: str) -> Document:
        return Document(
            id=doc_id,
            kind="master_plan",
            source_url=f"https://example.com/{doc_id}.pdf",
            completeness="link_only",
            title=title,
            edition="2008",
            airport_lid="4S9",
        )

    chapters = [
        chapter("4s9-2008-strategic-analysis", "Mulino Airport Master Plan Update Chapter 1 Strategic Analysis"),
        chapter("4s9-2008-inventory", "Mulino Airport Master Plan Update Chapter 2 Inventory"),
        chapter("4s9-2008-alternatives", "Mulino Airport Master Plan Update Chapter 5 Alternatives"),
    ]
    split = build.featured_work(chapters, "master_plan")
    assert split is not None
    assert split.hub is None
    assert split.title == "Mulino Airport Master Plan Update"
    assert split.edition == "2008"
    assert [doc.id for doc in split.parts] == [
        "4s9-2008-strategic-analysis",
        "4s9-2008-inventory",
        "4s9-2008-alternatives",
    ]

    amp = Document(
        id="4s9-2019-amp",
        kind="master_plan",
        source_url="https://example.com/2019.pdf",
        completeness="link_only",
        title="Mulino State Airport Master Plan",
        edition="July 2019",
        airport_lid="4S9",
    )
    alp = Document(
        id="4s9-2019-alp",
        kind="alp",
        source_url="https://example.com/alp.pdf",
        completeness="link_only",
        title="Mulino State Airport Layout Plan",
        edition="2019",
        airport_lid="4S9",
    )
    latest = build.featured_work([*chapters, amp, alp], "master_plan")
    assert latest is not None
    assert latest.hub == amp
    assert latest.parts == ()
    earlier = build.edition_works([*chapters, amp], "master_plan")
    assert [work.study_year for work in earlier] == [2019, 2008]
    assert build.featured_work([*chapters, amp, alp], "alp").hub == alp


def test_featured_work_nests_section_update_under_same_edition() -> None:
    from catalog.models import Document

    build = _load_build()
    amp = Document(
        id="4s9-2019-amp",
        kind="master_plan",
        source_url="https://example.com/2019.pdf",
        completeness="link_only",
        title="Mulino State Airport Master Plan",
        edition="July 2019",
        airport_lid="4S9",
    )
    update = Document(
        id="4s9-2021-inventory-update",
        kind="master_plan",
        source_url="https://example.com/2021-inventory.pdf",
        completeness="link_only",
        title="Inventory update",
        edition="2021",
        airport_lid="4S9",
        part_of="4s9-2019-amp",
    )
    work = build.featured_work([amp, update], "master_plan")
    assert work is not None
    assert work.hub == amp
    assert work.study_year == 2019
    assert [doc.id for doc in work.parts] == ["4s9-2021-inventory-update"]
    assert len(build.edition_works([amp, update], "master_plan")) == 1


def test_year_bars_scale_to_peak_year() -> None:
    from catalog.models import Grant

    build = _load_build()
    bars = build.year_bars(
        [
            Grant(airport_lid="PDX", fiscal_year=2025, amount=100, description="A"),
            Grant(airport_lid="PDX", fiscal_year=2025, amount=50, description="B"),
            Grant(airport_lid="PDX", fiscal_year=2024, amount=50, description="C"),
        ]
    )
    assert [row["year"] for row in bars] == [2025, 2024]
    assert bars[0]["total"] == 150
    assert bars[0]["count"] == 2
    assert bars[0]["pct"] == 100
    assert bars[1]["pct"] == 33


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


def test_outlook_airport_lists_splits_growing_and_declining() -> None:
    from catalog.models import Airport

    build = _load_build()
    pdx = Airport(lid="PDX", name="Portland Intl", city="Portland", state="OR")
    ttd = Airport(lid="TTD", name="Troutdale", city="Troutdale", state="OR")
    hio = Airport(lid="HIO", name="Hillsboro", city="Hillsboro", state="OR")
    growing, declining = build.outlook_airport_lists(
        [ttd, pdx, hio],
        {"PDX": "growing", "TTD": "declining", "HIO": "maintaining"},
    )
    assert [airport.lid for airport in growing] == ["PDX"]
    assert [airport.lid for airport in declining] == ["TTD"]


def test_grant_title_uses_first_clause() -> None:
    build = _load_build()
    long_desc = "Reconstruct Taxiway, Rehabilitate Taxiway, Rehabilitate Taxiway"
    assert build.grant_title(long_desc) == "Reconstruct Taxiway"
    assert build.grant_brief(long_desc) == "Rehabilitate Taxiway, Rehabilitate Taxiway"
    assert build.grant_title("Improve Terminal") == "Improve Terminal"
    assert build.grant_brief("Improve Terminal") == ""
