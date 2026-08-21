from __future__ import annotations

from catalog.models import Document, visible_on_site
from pipeline.explore import confirm_jobs, explore_page
from pipeline.stages import (
    STAGES,
    review_after_snapshot,
    review_after_vet,
    source_family,
    worth_confirm,
)

PAGE = "https://beverlyairport.com/documents/"


def _doc(**kwargs) -> Document:
    payload = {
        "id": "x",
        "kind": "master_plan",
        "source_url": "https://example.com/plan.pdf",
        "completeness": "link_only",
        "review_status": "pending",
        "airport_lid": "BVY",
    }
    payload.update(kwargs)
    return Document.from_dict(payload)


def test_pipeline_stages_are_ordered() -> None:
    assert STAGES == ("signal", "explore", "confirm", "snapshot", "vet", "publish")


def test_curated_catalog_rows_are_visible_snapshots_are_not() -> None:
    assert visible_on_site(_doc()) is True
    assert visible_on_site(_doc(completeness="complete", review_status="pending")) is False
    assert visible_on_site(_doc(completeness="complete", review_status="auto_pass")) is True
    assert visible_on_site(_doc(completeness="complete", review_status="needs_human")) is False


def test_snapshot_is_not_a_publish() -> None:
    assert review_after_snapshot(None) == "pending"
    assert review_after_snapshot("published") == "published"
    assert review_after_vet(official_plan=True, same_airport=True, kind="master_plan") == "auto_pass"
    assert review_after_vet(official_plan=True, same_airport=True, kind="not_plan") == "pending"
    assert review_after_vet(official_plan=True, same_airport=True, kind="other") == "pending"


def test_worth_confirm_skips_minutes_and_unknown_pdfs() -> None:
    assert worth_confirm(role="part", kind_guess="chapter", label="Chapter 1 Introduction") is True
    assert worth_confirm(role="artifact", kind_guess="alp", label="Airport Layout Plan") is True
    assert worth_confirm(role="artifact", kind_guess="unknown", label="8-10-2026") is False
    assert worth_confirm(role="artifact", kind_guess="unknown", label="Commission minutes") is False
    assert worth_confirm(role="artifact", kind_guess="unknown", label="Privacy statement") is False


def test_document_dump_queues_plan_files_only() -> None:
    html = f"""<!DOCTYPE html><html><head><title>Airport Documents</title></head><body>
    <a href="https://beverlyairport.com/wp-content/uploads/2024/03/Chapter-1-Introduction.pdf">Chapter 1 Introduction</a>
    <a href="https://beverlyairport.com/wp-content/uploads/2024/03/ALP.pdf">Airport Layout Plan</a>
    <a href="https://beverlyairport.com/wp-content/uploads/2026/08/minutes.pdf">8-10-2026</a>
    <a href="https://beverlyairport.com/wp-content/uploads/privacy.pdf">Beverly Airport Privacy Statement</a>
    </body></html>"""
    result = explore_page(html, PAGE)
    jobs = confirm_jobs(result, airport_lid="BVY", state="MA")
    queued = {job.source_url for job in jobs}
    assert any("Chapter-1" in url for url in queued)
    assert any("ALP.pdf" in url for url in queued)
    assert not any("minutes" in (url or "") for url in queued)
    assert not any("privacy" in (url or "").lower() for url in queued)
    assert source_family(status=200, n_artifacts=144, page_url=PAGE, hub_kind="other") == "document_dump"


def test_source_family_marks_sharepoint_and_bot_wall() -> None:
    assert source_family(status=200, n_followups=2, n_artifacts=0, hub_kind="other") == "sharepoint_list"
    assert source_family(status=403, error="Forbidden") == "bot_wall"
    assert source_family(status=404, error="Not Found") == "dead"
    assert source_family(status=200, n_artifacts=0, hub_kind="other") == "facility_page"
    assert source_family(status=200, n_artifacts=16, hub_kind="master_plan") == "plan_hub"
