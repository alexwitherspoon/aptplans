from __future__ import annotations

import hashlib
from urllib.parse import urlparse

from catalog import (
    REFERENCE_FILES,
    load_embedded_fixtures,
    load_reference_cases,
    load_schema,
    load_shape_card,
)

REQUIRED_LIDS = {"PDX", "TTD", "4S9"}
REQUIRED_SCALES = {"large", "medium", "small"}
REQUIRED_REGIONS_BEYOND_NW = {"mountain", "new_england"}


def _cases() -> list[dict]:
    data = load_reference_cases()
    assert isinstance(data["cases"], list)
    return data["cases"]


def _documents() -> list[tuple[dict, dict]]:
    out: list[tuple[dict, dict]] = []
    for case in _cases():
        for doc in case["documents"]:
            out.append((case, doc))
    return out


def test_shape_card_is_ac_150_5070_6b() -> None:
    card = load_shape_card()
    assert card["circular"] == "AC 150/5070-6B"
    assert card["source_url"].startswith("https://www.faa.gov/")
    core = set(card["core_elements"])
    assert core == {
        "inventory",
        "forecasts",
        "facility_requirements",
        "alternatives",
        "alp",
        "implementation",
    }


def test_reference_set_covers_requested_airports_and_scales() -> None:
    cases = _cases()
    lids = {case["airport_lid"] for case in cases}
    scales = {case["scale"] for case in cases}
    regions = {case["region"] for case in cases}
    assert REQUIRED_LIDS <= lids
    assert REQUIRED_SCALES <= scales
    assert "northwest" in regions
    assert REQUIRED_REGIONS_BEYOND_NW <= regions
    names = {case["name"] for case in cases}
    assert any("Mulino" in name for name in names)
    assert any("Troutdale" in name for name in names)
    assert any("Portland International" in name for name in names)


def test_each_case_claims_the_faa_core_elements() -> None:
    core = set(load_shape_card()["core_elements"])
    for case in _cases():
        claimed = set(case["expected_elements"])
        missing = core - claimed
        assert not missing, f"{case['id']} missing FAA elements {sorted(missing)}"


def test_reference_documents_match_catalog_schema() -> None:
    schema = load_schema()
    allowed = set(schema["properties"])
    required = set(schema["required"])
    kind_enum = set(schema["properties"]["kind"]["enum"])
    completeness_enum = set(schema["properties"]["completeness"]["enum"])
    for case, doc in _documents():
        assert required <= set(doc), f"{doc.get('id')} missing required fields"
        extra = set(doc) - allowed
        assert not extra, f"{doc['id']} has unknown fields {sorted(extra)}"
        assert doc["kind"] in kind_enum
        assert doc["completeness"] in completeness_enum
        assert doc["completeness"] == "link_only"
        assert doc["review_status"] == "pending"
        assert doc["content_sha256"] is None
        assert doc["preserved_url"] is None
        assert doc["airport_lid"] == case["airport_lid"]
        assert doc["state"] == case["state"]


def test_reference_ids_are_unique_and_urls_are_https() -> None:
    case_ids = [case["id"] for case in _cases()]
    doc_ids = [doc["id"] for _, doc in _documents()]
    assert len(case_ids) == len(set(case_ids))
    assert len(doc_ids) == len(set(doc_ids))
    for _, doc in _documents():
        parsed = urlparse(doc["source_url"])
        assert parsed.scheme == "https", doc["source_url"]
        assert parsed.netloc, doc["source_url"]


def test_reference_set_includes_standalone_alps() -> None:
    kinds = {doc["kind"] for _, doc in _documents()}
    assert "master_plan" in kinds
    assert "alp" in kinds
    alp_lids = {doc["airport_lid"] for _, doc in _documents() if doc["kind"] == "alp"}
    assert "4S2" in alp_lids
    assert "4S9" in alp_lids
    assert "BVY" in alp_lids


def test_mulino_and_pdx_are_link_only_hints_not_complete() -> None:
    by_lid = {case["airport_lid"]: case for case in _cases()}
    for lid in ("PDX", "TTD", "4S9"):
        docs = by_lid[lid]["documents"]
        assert docs
        assert all(doc["completeness"] == "link_only" for doc in docs)


def test_mulino_2019_files_record_the_airport_page() -> None:
    by_id = {doc["id"]: doc for _, doc in _documents()}
    amp = by_id["4s9-2019-amp"]
    alp = by_id["4s9-2019-alp"]
    page = "https://www.oregon.gov/aviation/airports/pages/mulino-4s9.aspx"
    assert amp["found_on"] == page
    assert alp["found_on"] == page
    assert amp["publisher"] == "Oregon Department of Aviation"


def test_embedded_pdfs_match_manifest_hashes() -> None:
    docs = {doc["id"]: (case, doc) for case, doc in _documents()}
    embedded = load_embedded_fixtures()
    assert len(embedded) >= 8
    ids = {item["document_id"] for item in embedded}
    assert {
        "pdx-2045-existing-conditions",
        "ttd-2016-shaping-our-future",
        "4s9-2008-inventory",
        "4s2-2018-alp-sheet",
        "bvy-2022-alp",
    } <= ids
    kinds = set()
    lids = set()
    for item in embedded:
        case, doc = docs[item["document_id"]]
        path = REFERENCE_FILES.parent / item["path"]
        data = path.read_bytes()
        assert data.startswith(b"%PDF")
        assert len(data) == item["bytes"]
        assert hashlib.sha256(data).hexdigest() == item["sha256"]
        assert doc["completeness"] == "link_only"
        assert doc["content_sha256"] is None
        kinds.add(doc["kind"])
        lids.add(case["airport_lid"])
    assert "master_plan" in kinds
    assert "alp" in kinds
    assert {"PDX", "TTD", "4S9", "4S2", "BVY"} <= lids


def test_reference_hub_html_has_no_api_keys() -> None:
    from pipeline.sanitize import html_has_secrets

    html_paths = sorted(REFERENCE_FILES.glob("*.html"))
    assert html_paths, "expected committed hub HTML fixtures"
    offenders = [path.name for path in html_paths if html_has_secrets(path.read_text(encoding="utf-8", errors="replace"))]
    assert not offenders, f"redact third-party keys in {offenders}"
