from __future__ import annotations

from pathlib import Path

from catalog.models import Document, looks_like_work_edition
from pipeline.discover import seed_explore_hubs
from pipeline.explore import (
    classify_link,
    confirm_jobs,
    explore_page,
    followup_explore_jobs,
    hub_document_kind,
)
from pipeline.gates import GateResult, evaluate_file, evaluate_payload, sniff_media
from pipeline.queue import JobQueue

PAGE = "https://www.oregon.gov/aviation/airports/pages/mulino-4s9.aspx"
AMP = "https://www.oregon.gov/aviation/airports/Documents/4S9/Master%20Plan/2019/Mulino%20Final%20AMP%20July%202019.pdf"
ALP = "https://www.oregon.gov/aviation/airports/Documents/4S9/ODA_Doc_4S9_ALP.pdf"
EA = "https://www.oregon.gov/aviation/Airports/Documents/4S9/Projects/Draft%20EA%206-11-2019%20completePart-1.pdf"
CH2 = "https://www.oregon.gov/aviation/Airports/Documents/4S9/Master%20Plan/2008/Chapter%202%20-%20Inventory.pdf"

HUB_WITH_LINKS = f"""<!DOCTYPE html>
<html><head><title>Aviation : Mulino State Airport [4S9]</title></head>
<body>
<h1>Mulino State Airport [4S9]</h1>
<p>Owned by the Oregon Department of Aviation.</p>
<a href="{AMP}">Master Plan</a>
<a href="{ALP}">Airport Diagram</a>
<a href="{CH2}">Chapter 2 Inventory</a>
<a href="{EA}">Draft Environmental Assessment</a>
<a href="https://content.govdelivery.com/accounts/ORAVIATION/bulletins/4157ec6">Seasonal airports newsletter</a>
<nav-heading-grid-webpart params="webPartId: 'x', webPartProperties: {{&quot;sharePointWebUrl&quot;:&quot;/aviation/airports&quot;,&quot;sharePointListUrl&quot;:&quot;/aviation/airports/Lists/Airport Links List&quot;,&quot;sharePointViewName&quot;:&quot;4S9- Mulino&quot;}}">
</nav-heading-grid-webpart>
</body></html>
"""

HUB_NO_PDF = """<!DOCTYPE html>
<html><head><title>Aviation : Mulino State Airport [4S9]</title></head>
<body>
<h1>Mulino State Airport [4S9]</h1>
<p>The Hamlet of Mulino is located 23 miles south of Portland. Runway 14/32 is 3,425 feet long.</p>
<a href="https://notams.aim.faa.gov/notamSearch/nsapp.html">NOTAMs</a>
<nav-heading-grid-webpart params="webPartProperties: {&quot;sharePointWebUrl&quot;:&quot;/aviation/airports&quot;,&quot;sharePointListUrl&quot;:&quot;/aviation/airports/Lists/Airport Links List&quot;,&quot;sharePointViewName&quot;:&quot;4S9- Mulino&quot;}">
</nav-heading-grid-webpart>
</body></html>
"""


def test_html_hub_is_not_a_pdf_payload() -> None:
    result = evaluate_file(PAGE, "", HUB_NO_PDF.encode())
    assert result == GateResult.NOT_FILE
    assert sniff_media(HUB_NO_PDF.encode()) == "html"
    assert evaluate_payload(PAGE, "", HUB_NO_PDF.encode(), allow_html=True) == GateResult.OK


def test_explore_mulino_links_keep_provenance() -> None:
    result = explore_page(HUB_WITH_LINKS, PAGE)
    assert result.title.startswith("Aviation")
    by_url = {item.url: item for item in result.links}
    assert by_url[AMP].kind_guess == "master_plan"
    assert by_url[AMP].found_on == PAGE
    assert by_url[ALP].kind_guess == "alp"
    assert by_url[CH2].role == "part"
    assert by_url[EA].role == "not_plan"
    jobs = confirm_jobs(result, airport_lid="4S9", state="OR")
    queued = {job.source_url for job in jobs}
    assert AMP in queued
    assert ALP in queued
    assert CH2 in queued
    assert EA not in queued
    assert all(job.found_on == PAGE for job in jobs)
    assert hub_document_kind(result) == "other"
    assert result.followups
    assert result.followups[0].view_name == "4S9- Mulino"
    assert "Airport Links List" in result.followups[0].url


def test_explore_mulino_without_pdf_still_captures_hub() -> None:
    result = explore_page(HUB_NO_PDF, PAGE)
    assert result.artifacts() == []
    assert result.packets()[0]["page_url"] == PAGE
    assert result.packets()[0]["artifact_url"] == ""
    assert confirm_jobs(result, airport_lid="4S9") == []
    assert result.followups[0].role == "followup"
    assert hub_document_kind(result) == "other"
    follows = followup_explore_jobs(result, airport_lid="4S9", state="OR")
    assert len(follows) == 1
    assert follows[0].kind == "explore"
    assert "Airport Links List" in (follows[0].source_url or "")
    assert follows[0].found_on == PAGE


def test_classify_airport_diagram_pdf_is_alp() -> None:
    hit = classify_link(ALP, "Airport Diagram", found_on=PAGE)
    assert hit.kind_guess == "alp"
    assert hit.media == "pdf"


def test_seed_explore_hubs_queues_mulino_website(tmp_path: Path) -> None:
    queue = JobQueue(tmp_path)
    count = seed_explore_hubs(queue, Path(__file__).resolve().parents[1] / "catalog")
    assert count >= 1
    claimed = queue.claim()
    assert claimed is not None
    found_mulino = False
    while claimed is not None:
        if claimed.airport_lid == "4S9":
            found_mulino = True
            assert claimed.kind == "explore"
            assert claimed.source_url.endswith("mulino-4s9.aspx")
            assert claimed.document_id == "4s9-site"
        queue.complete(claimed)
        claimed = queue.claim()
    assert found_mulino is True


def test_section_update_is_not_a_new_work_edition() -> None:
    amp = Document.from_dict(
        {
            "id": "4s9-2019-amp",
            "kind": "master_plan",
            "source_url": AMP,
            "completeness": "link_only",
            "title": "Mulino State Airport Master Plan",
            "airport_lid": "4S9",
        }
    )
    update = Document.from_dict(
        {
            "id": "4s9-2021-inventory-update",
            "kind": "master_plan",
            "source_url": "https://example.com/2021-inventory.pdf",
            "completeness": "link_only",
            "title": "Inventory update",
            "airport_lid": "4S9",
            "part_of": "4s9-2019-amp",
        }
    )
    assert looks_like_work_edition(amp) is True
    assert looks_like_work_edition(update) is False
