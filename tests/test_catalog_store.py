from __future__ import annotations

from pathlib import Path

from catalog.seed import US_STATES, seed_catalog
from catalog.store import Catalog, completeness_for_airport, merge_overlay

ROOT = Path(__file__).resolve().parents[1]


def test_fifty_states_are_seeded() -> None:
    assert len(US_STATES) == 50
    assert US_STATES["OR"] == "Oregon"
    assert "DC" not in US_STATES


def test_seed_catalog_includes_reference_airports_and_states() -> None:
    catalog = seed_catalog(ROOT / "catalog")
    lids = {airport.lid for airport in catalog.airports}
    assert {"PDX", "TTD", "HIO", "4S9", "4S2", "DEN", "BVY"} <= lids
    assert len(catalog.states) == 50
    pdx_docs = [doc for doc in catalog.documents if doc.airport_lid == "PDX"]
    assert pdx_docs
    assert all(doc.completeness == "link_only" for doc in pdx_docs)
    assert catalog.airports_by_lid["PDX"].name.startswith("Portland")
    assert catalog.airports_by_lid["PDX"].city == "Portland"
    assert catalog.airports_by_lid["PDX"].county == "Multnomah"
    assert catalog.airports_by_lid["PDX"].website == "https://www.portofportland.com/PDX"
    oregon = catalog.states_by_code["OR"]
    assert oregon.agency == "Oregon Department of Aviation"
    assert oregon.agency_url == "https://www.oregon.gov/aviation"
    assert any(doc.id == "or-ors-836" for doc in catalog.documents)
    assert catalog.document("or-ors-836").kind == "statute"
    assert oregon.budget_url
    assert catalog.budgets_for_state("OR")
    assert catalog.budgets_for_state("OR")[0].total == 45874157
    oregon_grants = catalog.grants_for_state("OR")
    assert oregon_grants
    assert {grant.airport_lid for grant in oregon_grants} == {"PDX"}
    assert not catalog.grants_for_state("CO")


def test_seed_without_overlay_is_reference_airports_only() -> None:
    catalog = seed_catalog(ROOT / "catalog")
    lids = {airport.lid for airport in catalog.airports}
    assert lids == {"PDX", "TTD", "HIO", "4S9", "4S2", "DEN", "BVY"}
    assert catalog.airports_by_lid["PDX"].in_npias is True


def test_production_overlay_skips_git_reference_rows(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("APTPLANS_CATALOG_OVERLAY", str(tmp_path))
    monkeypatch.delenv("APTPLANS_DEV_PREVIEW", raising=False)
    monkeypatch.delenv("APTPLANS_REFERENCE_SEED", raising=False)
    catalog = seed_catalog(ROOT / "catalog", overlay_dir=tmp_path)
    assert catalog.airports == []
    assert catalog.grants == []
    assert catalog.budgets == []
    assert not any(doc.airport_lid for doc in catalog.documents)
    assert any(doc.id == "or-ors-836" for doc in catalog.documents)


def test_dev_preview_keeps_reference_rows_with_overlay(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("APTPLANS_CATALOG_OVERLAY", str(tmp_path))
    monkeypatch.setenv("APTPLANS_DEV_PREVIEW", "1")
    catalog = seed_catalog(ROOT / "catalog", overlay_dir=tmp_path)
    assert "PDX" in catalog.airports_by_lid


def test_reference_seed_override(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("APTPLANS_CATALOG_OVERLAY", str(tmp_path))
    monkeypatch.delenv("APTPLANS_DEV_PREVIEW", raising=False)
    monkeypatch.setenv("APTPLANS_REFERENCE_SEED", "1")
    catalog = seed_catalog(ROOT / "catalog", overlay_dir=tmp_path)
    assert "PDX" in catalog.airports_by_lid


def test_seed_overlay_adds_nasr_airports(tmp_path: Path) -> None:
    from catalog.models import Airport
    from catalog.store import write_airports_overlay

    write_airports_overlay(
        tmp_path,
        [Airport(lid="59S", name="Prospect State", city="Prospect", state="OR", in_npias=False, sources=["nasr"])],
    )
    catalog = seed_catalog(ROOT / "catalog", overlay_dir=tmp_path)
    assert "59S" in catalog.airports_by_lid
    assert catalog.airports_by_lid["59S"].in_npias is False
    assert "PDX" in catalog.airports_by_lid


def test_overlay_promotes_link_only_to_complete() -> None:
    catalog = seed_catalog(ROOT / "catalog")
    doc = next(item for item in catalog.documents if item.id == "4s9-2008-inventory")
    assert doc.completeness == "link_only"
    overlay = {
        "4s9-2008-inventory": {
            "content_sha256": "abc",
            "preserved_url": "/files/abc.pdf",
            "source_status": "live",
            "completeness": "complete",
            "review_status": "auto_pass",
        }
    }
    merged = merge_overlay(catalog, overlay)
    updated = merged.document("4s9-2008-inventory")
    assert updated.completeness == "complete"
    assert updated.content_sha256 == "abc"
    git_doc = catalog.document("4s9-2008-inventory")
    assert git_doc.completeness == "link_only"


def test_airport_completeness_prefers_alp_over_no_plan() -> None:
    from catalog.models import Airport, Document

    airport = Airport(
        lid="4S2",
        icao=None,
        iata=None,
        name="Ken Jernstedt Airfield",
        city="Hood River",
        state="OR",
        npias_role="local",
        latitude=None,
        longitude=None,
        website=None,
    )
    alp = Document(
        id="alp",
        kind="alp",
        airport_lid="4S2",
        state="OR",
        title="ALP",
        edition="2018",
        source_url="https://example.com/alp.pdf",
        source_retrieved_at=None,
        source_status="live",
        content_sha256="aaa",
        preserved_url="/files/aaa.pdf",
        ia_item=None,
        mirrors=[],
        license_or_rights="public_record",
        supersedes=None,
        review_status="auto_pass",
        completeness="complete",
        summary=None,
    )
    catalog = Catalog(airports=[airport], states=[], documents=[alp], changes=[])
    assert completeness_for_airport(catalog, "4S2") == "complete"


def test_hashed_airport_page_does_not_complete_the_plan() -> None:
    from catalog.models import Airport, Document

    airport = Airport(lid="4S9", name="Mulino", city="Mulino", state="OR")
    hub = Document(
        id="4s9-site",
        kind="other",
        source_url="https://www.oregon.gov/aviation/airports/pages/mulino-4s9.aspx",
        completeness="complete",
        airport_lid="4S9",
        media="html",
    )
    plan = Document(
        id="4s9-2019-amp",
        kind="master_plan",
        source_url="https://example.com/2019.pdf",
        completeness="link_only",
        airport_lid="4S9",
    )
    catalog = Catalog(airports=[airport], documents=[hub, plan])
    assert completeness_for_airport(catalog, "4S9") == "link_only"


def test_unvetted_snapshot_does_not_complete_or_list() -> None:
    from catalog.models import Airport, Document, visible_on_site

    airport = Airport(lid="BVY", name="Beverly", city="Beverly", state="MA")
    snap = Document(
        id="bvy-minutes",
        kind="master_plan",
        source_url="https://example.com/minutes.pdf",
        completeness="complete",
        review_status="pending",
        airport_lid="BVY",
    )
    catalog = Catalog(airports=[airport], documents=[snap])
    assert visible_on_site(snap) is False
    assert completeness_for_airport(catalog, "BVY") == "missing"


def test_load_and_write_roundtrip(tmp_path: Path) -> None:
    catalog = seed_catalog(ROOT / "catalog")
    out = tmp_path / "catalog"
    catalog.write(out)
    loaded = Catalog.load(out)
    assert len(loaded.airports) == len(catalog.airports)
    assert loaded.document("4s2-2018-alp-sheet").kind == "alp"
    assert loaded.budgets_for_state("OR")[0].id == "or-odav-lab-2025-27"


def test_change_event_schema_exists() -> None:
    schema = ROOT / "catalog" / "change_event.schema.json"
    assert schema.is_file()
    text = schema.read_text(encoding="utf-8")
    assert "entity_id" in text
    assert "review_status" in text
