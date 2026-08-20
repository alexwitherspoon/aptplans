from __future__ import annotations

from catalog.models import (
    Document,
    find_same_content,
    looks_like_work_edition,
    prior_work_document,
    work_key,
)


def _doc(**kwargs) -> Document:
    payload = {
        "id": "x",
        "kind": "master_plan",
        "source_url": "https://example.com/plan.pdf",
        "completeness": "complete",
        "airport_lid": "PDX",
        "title": "Airport Master Plan",
    }
    payload.update(kwargs)
    return Document.from_dict(payload)


def test_work_key_is_lid_and_kind() -> None:
    assert work_key(_doc()) == ("PDX", "master_plan")
    assert work_key(_doc(kind="statute", airport_lid=None)) is None


def test_chapters_are_not_work_editions() -> None:
    chapter = _doc(id="4s9-2008-inventory", title="Inventory")
    assert looks_like_work_edition(chapter) is False
    assert looks_like_work_edition(_doc(title="Airport Master Plan")) is True


def test_prior_work_document_skips_chapters() -> None:
    inventory = _doc(id="a-inventory", title="Inventory", source_url="https://example.com/a.pdf")
    later = _doc(id="b-plan", title="Airport Master Plan", source_url="https://example.com/b.pdf")
    assert prior_work_document([inventory], later) is None


def test_prior_work_document_finds_earlier_edition() -> None:
    old = _doc(id="pdx-2010", title="Master Plan", edition="2010", source_url="https://example.com/2010.pdf")
    new = _doc(id="pdx-2024", title="Master Plan", edition="2024", source_url="https://example.com/2024.pdf")
    assert prior_work_document([old], new) is old


def test_find_same_content_matches_fingerprints() -> None:
    stored = _doc(id="one", text_sha256="aa", images_sha256="bb")
    assert find_same_content([stored], airport_lid="PDX", text_sha256="aa", images_sha256="bb") is stored
    assert find_same_content([stored], airport_lid="PDX", text_sha256="aa", images_sha256="cc") is None
