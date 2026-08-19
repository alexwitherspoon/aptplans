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
    oregon = catalog.states_by_code["OR"]
    assert oregon.agency == "Oregon Department of Aviation"
    assert oregon.agency_url == "https://www.oregon.gov/aviation"
    assert any(doc.id == "or-ors-836" for doc in catalog.documents)
    assert catalog.document("or-ors-836").kind == "statute"


def test_seed_without_overlay_is_reference_airports_only() -> None:
    catalog = seed_catalog(ROOT / "catalog")
    lids = {airport.lid for airport in catalog.airports}
    assert lids == {"PDX", "TTD", "HIO", "4S9", "4S2", "DEN", "BVY"}
    assert catalog.airports_by_lid["PDX"].in_npias is True


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


def test_load_and_write_roundtrip(tmp_path: Path) -> None:
    catalog = seed_catalog(ROOT / "catalog")
    out = tmp_path / "catalog"
    catalog.write(out)
    loaded = Catalog.load(out)
    assert len(loaded.airports) == len(catalog.airports)
    assert loaded.document("4s2-2018-alp-sheet").kind == "alp"


def test_change_event_schema_exists() -> None:
    schema = ROOT / "catalog" / "change_event.schema.json"
    assert schema.is_file()
    text = schema.read_text(encoding="utf-8")
    assert "entity_id" in text
    assert "review_status" in text
