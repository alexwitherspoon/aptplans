from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from catalog import REFERENCE_FILES
from catalog.models import Airport, Document
from catalog.seed import seed_catalog
from catalog.store import load_overviews_overlay
from pipeline.brief import (
    airport_overview,
    document_excerpt,
    excerpt_from_pages,
    extract_facts,
    extract_runways,
    extract_trajectory,
    format_runway_line,
    format_runways,
    overview_is_stale,
    pdf_fact_text,
    source_path_for,
    work_excerpt,
)
from pipeline.overviews import lids_with_data, refresh_overviews
from pipeline.parse import extract_text

ROOT = Path(__file__).resolve().parents[1]
PACIFIC = ZoneInfo("America/Los_Angeles")


def _load_build():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "aptplans_build", ROOT / "site" / "build.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_format_runway_line_drops_count_and_adds_surface() -> None:
    assert format_runway_line("7/25", 3040, 75, "ASPH") == "7/25, 3,040 by 75 ft · Asphalt"
    assert format_runway_line("14/32", 3425, 100) == "14/32, 3,425 by 100 ft"
    assert format_runway_line("10R/28L", 11000, 150, "CONC-G") == (
        "10R/28L, 11,000 by 150 ft · Concrete"
    )
    stacked = format_runways(
        [
            {"id": "10R/28L", "length_ft": 11000, "width_ft": 150, "surface": "CONC"},
            {"id": "10L/28R", "length_ft": 9825, "width_ft": 150, "surface": "ASPH"},
        ]
    )
    assert stacked == (
        "10R/28L, 11,000 by 150 ft · Concrete\n"
        "10L/28R, 9,825 by 150 ft · Asphalt"
    )


def test_extract_facts_from_mulino_inventory() -> None:
    text = extract_text((REFERENCE_FILES / "4s9-2008-inventory.pdf").read_bytes())
    facts = extract_facts(text)
    assert "Runways" in facts
    assert "3,425" in facts["Runways"]
    assert "100" in facts["Runways"]
    assert "14/32, 3,425 by 100 ft" in facts["Runways"]
    assert "1 ·" not in facts["Runways"]
    assert not facts["Runways"].startswith("Runway ")
    assert "Based aircraft" in facts
    assert "Hangars" in facts
    assert "Operations" in facts


def test_extract_facts_omits_missing_fields() -> None:
    assert extract_facts("This chapter states the study purpose only.") == {}
    assert extract_runways("The airport has hangars and a lounge.") is None


def test_excerpt_from_pages_reads_inventory_past_toc() -> None:
    pages = ["Table of contents " + ("...." * 30)] * 8
    pages.extend(["Narrative padding " + ("word " * 40)] * 16)
    pages.append(
        "The airport has two parallel runways, Runways 10R-28L and 10L-28R. "
        "Dimensions 11,000' x 150' 9,825' x 150'. The preferred alternative "
        "includes a runway extension and additional hangars for growth."
    )
    pages.extend(["Closing chapter " + ("word " * 40)] * 8)
    blob = excerpt_from_pages(pages)
    assert "11,000" in blob
    facts = extract_facts(blob)
    assert "Runways" in facts
    assert "11,000" in facts["Runways"]


def test_pdf_fact_text_reads_every_page_and_caches(tmp_path: Path, monkeypatch) -> None:
    pdf = tmp_path / "plan.pdf"
    pdf.write_bytes(b"%PDF-1.4 placeholder")
    pages = ["toc " + ("...." * 40)] * 10
    pages.extend(["padding " + ("word " * 40)] * 20)
    pages[22] = (
        "Runway 14/32 is 3,425 feet long and 100 feet wide. There are currently "
        "58 based aircraft. " + ("note " * 20)
    )
    pages.extend(["end " + ("word " * 40)] * 10)

    class _Page:
        def __init__(self, text: str) -> None:
            self._text = text

        def extract_text(self) -> str:
            return self._text

    class _Reader:
        def __init__(self, _source) -> None:
            self.pages = [_Page(text) for text in pages]

    monkeypatch.setattr("pypdf.PdfReader", _Reader)
    monkeypatch.setenv("APTPLANS_TEXT", str(tmp_path / "text"))
    blob = pdf_fact_text(pdf)
    assert "3,425" in blob
    again = pdf_fact_text(pdf)
    assert again == blob
    cache = list((tmp_path / "text" / "facts").glob("plan.pdf.*.txt"))
    assert cache


def test_document_excerpt_reads_large_pdfs(tmp_path: Path, monkeypatch) -> None:
    pdf = tmp_path / "big-plan.pdf"
    pdf.write_bytes(b"%PDF-1.4 placeholder")
    doc = Document(
        id="big-plan",
        kind="master_plan",
        source_url="https://example.com/plan.pdf",
        completeness="link_only",
    )
    monkeypatch.setattr("pipeline.brief.source_path_for", lambda _doc: pdf)
    monkeypatch.setenv("APTPLANS_TEXT", str(tmp_path / "text"))

    class _Page:
        def extract_text(self) -> str:
            return (
                "The preferred alternative includes a runway extension and additional "
                "hangars so the airport can expand capacity. " + ("text " * 30)
            )

    class _Reader:
        def __init__(self, _source) -> None:
            self.pages = [_Page(), _Page(), _Page()]

    monkeypatch.setattr("pypdf.PdfReader", _Reader)
    excerpt = document_excerpt(doc)
    assert "runway extension" in excerpt
    assert extract_trajectory(excerpt) is not None


def test_extract_runways_from_characteristic_table() -> None:
    text = (
        "PDX has two parallel runways, Runways 10R-28L (south runway) and 10L-28R "
        "(north runway), and a crosswind runway, Runway 3-21. "
        "Table 3-1 Runway Characteristics Dimensions 11,000' x 150' 9,825' x 150' "
        "6,000' x 150'."
    )
    value = extract_runways(text)
    assert value is not None
    assert "11,000" in value
    assert "10R/28L" in value or "10R-28L" in value.replace("/", "-")


def test_extract_trajectory_ttd_is_declining() -> None:
    text = extract_text((REFERENCE_FILES / "ttd-2016-shaping-our-future.pdf").read_bytes())
    outlook = extract_trajectory(text)
    assert outlook is not None
    assert outlook.band == "declining"
    assert outlook.position < -0.35
    assert outlook.needle_x < 100


def test_extract_trajectory_omits_purpose_only() -> None:
    assert extract_trajectory("This chapter states the study purpose only.") is None


def test_extract_trajectory_growth_and_maintain_patterns() -> None:
    grow = extract_trajectory(
        "The preferred alternative includes a runway extension and additional hangars "
        "so the airport can expand capacity for based aircraft over the next twenty years."
    )
    assert grow is not None
    assert grow.band == "growing"
    assert grow.position > 0.35
    hold = extract_trajectory(
        "The preferred alternative is to maintain existing length. There are no plans "
        "to extend the runway. Reconstruct the runway pavement and continue to operate "
        "as a reliever airport serving the same class of aircraft."
    )
    assert hold is not None
    assert hold.band == "maintaining"
    assert abs(hold.position) <= 0.35
    assert extract_trajectory(
        "Runway 7/25 is a 4,500-foot runway. The inventory chapter lists hangars "
        "and the existing terminal building only."
    ) is None
    denied = extract_trajectory(
        "There are absolutely no plans to extend the runway beyond its present length. "
        "The city will maintain existing facilities and reconstruct pavement as needed "
        "so the airport can continue to operate for general aviation."
    )
    assert denied is None or denied.band == "maintaining"
    assert denied is None or denied.position <= 0.35


def test_extract_trajectory_uses_common_amp_idioms() -> None:
    grow = extract_trajectory(
        "The preferred development alternative includes T-hangar development of one row "
        "and a proposed apron expansion with taxilane extensions to serve new hangars "
        "so the airport can expand capacity over the next twenty years."
    )
    assert grow is not None
    assert grow.band == "growing"
    hyphen = extract_trajectory(
        "PAC input, crafting a preferred alter-\nnative with project phasing and "
        "future hangar development on the north side of the airfield."
    )
    assert hyphen is not None
    assert hyphen.band == "growing"
    assert extract_trajectory(
        "The inventory lists existing hangar development areas and the corporate "
        "hangar development located closest to Runway 20. This chapter states "
        "the study purpose only."
    ) is None
    navaid = extract_trajectory(
        "The FAA is currently considering decommissioning the Banks NDB. "
        "The Navy decommissioned the facility on August 1 after the war. "
        "Reconstruct the runway pavement and continue to operate as a reliever."
    )
    assert navaid is None or navaid.band != "declining"


def test_airport_overview_omits_grant_project_list() -> None:
    overview = airport_overview([], grant_lines=["FY 2025 Improve Terminal"])
    assert overview is None or all(
        "Improve Terminal" not in value for _label, value in overview.facts
    )


def test_airport_overview_fills_nasr_when_plans_are_silent() -> None:
    airport = Airport(
        lid="ZZZ",
        name="Test Field",
        city="Test",
        state="OR",
        elevation_ft=221,
        nasr_effective="2026-08-06",
        runways=[{"id": "10R/28L", "length_ft": 11000, "width_ft": 150, "surface": "ASPH"}],
        fuel="Jet A · 100LL",
        hangar_storage=True,
        tiedown_storage=True,
        sources=["nasr"],
    )
    overview = airport_overview([], grant_lines=None, airport=airport)
    assert overview is not None
    facts = dict(overview.facts)
    assert facts["Runways"] == "10R/28L, 11,000 by 150 ft · Asphalt"
    assert "10R/28L" in facts["Runways"]
    assert facts["Elevation"] == "221 ft"
    assert "Jet A" in facts["Facilities"]
    assert "hangar storage" in facts["Facilities"]
    assert overview.as_of == "2026-08-06"


def test_airport_overview_prefers_plan_runways_over_nasr() -> None:
    catalog = seed_catalog(ROOT / "catalog")
    build = _load_build()
    docs = [doc for doc in catalog.documents_for_airport("4S9") if "2008" in doc.id]
    work = build.featured_work(docs, "master_plan")
    airport = Airport(
        lid="4S9",
        name="Mulino",
        city="Mulino",
        state="OR",
        elevation_ft=259,
        runways=[{"id": "07/25", "length_ft": 9999, "width_ft": 150}],
        sources=["nasr"],
    )
    overview = airport_overview([work], airport=airport)
    assert overview is not None
    facts = dict(overview.facts)
    assert "3,425" in facts["Runways"]
    assert "9999" not in facts["Runways"]
    assert facts["Elevation"] == "259 ft"


def test_airport_overview_fills_runways_when_plan_only_counted_them() -> None:
    work = type(
        "Work",
        (),
        {
            "hub": type(
                "Doc",
                (),
                {
                    "id": "count-only",
                    "summary": "The airport has two runways and an FBO.",
                    "content_sha256": None,
                },
            )(),
            "parts": (),
            "kind": "master_plan",
            "edition": "2025",
            "study_year": None,
        },
    )()
    airport = Airport(
        lid="PDX",
        name="Portland International Airport",
        city="Portland",
        state="OR",
        elevation_ft=31,
        runways=[{"id": "10R/28L", "length_ft": 11000, "width_ft": 150, "surface": "ASPH"}],
        sources=["nasr"],
    )
    overview = airport_overview([work], airport=airport)
    assert overview is not None
    facts = dict(overview.facts)
    assert facts["Runways"] == "10R/28L, 11,000 by 150 ft · Asphalt"
    assert facts["Elevation"] == "31 ft"


def test_work_excerpt_includes_committed_unofficial_summary() -> None:
    catalog = seed_catalog(ROOT / "catalog")
    build = _load_build()
    docs = [doc for doc in catalog.documents_for_airport("4S9") if "2008" in doc.id]
    work = build.featured_work(docs, "master_plan")
    excerpt = work_excerpt(work)
    assert "Mulino" in excerpt or "Clackamas" in excerpt
    intro = Document(
        id="bvy-2022-introduction",
        kind="master_plan",
        source_url="https://example.com/x.pdf",
        completeness="link_only",
    )
    assert source_path_for(intro) is not None


def test_overview_is_stale_missing_or_prior_month() -> None:
    august = datetime(2026, 8, 20, tzinfo=PACIFIC)
    assert overview_is_stale(None, now=august) is True
    assert overview_is_stale({"generated_at": "2026-07-02T12:00:00Z"}, now=august) is True
    assert overview_is_stale({"generated_at": "2026-08-01T12:00:00Z"}, now=august) is False


def test_refresh_overviews_writes_missing_and_skips_current_month(tmp_path: Path) -> None:
    overlay = tmp_path / "overlay"
    overlay.mkdir()
    catalog = seed_catalog(ROOT / "catalog")
    assert "4S9" in lids_with_data(catalog)
    assert "PDX" in lids_with_data(catalog)
    wrote = refresh_overviews(overlay, ROOT / "catalog")
    assert wrote > 0
    rows = load_overviews_overlay(overlay)
    assert "4S9" in rows
    mulino = rows["4S9"]
    blob = " ".join(value for _label, value in mulino.get("facts") or [])
    assert "3,425" in blob
    pdx = rows["PDX"]
    assert pdx.get("facts") or pdx.get("trajectory")
    assert not (pdx.get("upcoming") or [])
    again = refresh_overviews(overlay, ROOT / "catalog")
    assert again == 0
