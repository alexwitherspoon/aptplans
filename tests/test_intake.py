from __future__ import annotations

from pathlib import Path

from pipeline.intake import (
    HUMAN_HANDLE,
    IntakeHint,
    IntakeOutcome,
    hint_can_queue,
    parse_issue_body,
    resolve_intake,
)

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / ".github" / "ISSUE_TEMPLATE" / "missing-document.yml"


def test_hint_can_queue_lid_that_is_not_in_npias() -> None:
    hint = IntakeHint(
        report_type="add",
        kind="alp",
        airport_lid="XYZ",
        state="OR",
        source_url="https://example.com/alp.pdf",
        notes="",
    )
    assert hint_can_queue(hint) is True
    assert hint_can_queue(
        IntakeHint(
            report_type="add",
            kind="master_plan",
            airport_lid=None,
            state="OR",
            source_url="https://example.com/plan.pdf",
            notes="",
        )
    ) is False


def test_issue_template_covers_add_and_corrections() -> None:
    text = TEMPLATE.read_text(encoding="utf-8")
    assert "Add a missing document" in text
    assert "stale" in text.lower()
    assert "wrong" in text.lower()
    assert "intake" in text
    assert "official_url" in text
    assert "report_type" in text


def test_parse_add_hint() -> None:
    body = """
### What should we do?
Add a missing document

### What kind of document?
Airport master plan

### Airport
PDX

### State
OR

### Official URL
https://pdx2045.org/

### Notes
PDX 2045 hub
"""
    hint = parse_issue_body(body)
    assert hint == IntakeHint(
        report_type="add",
        kind="master_plan",
        airport_lid="PDX",
        state="OR",
        source_url="https://pdx2045.org/",
        notes="PDX 2045 hub",
    )


def test_parse_stale_and_wrong_hints() -> None:
    stale = parse_issue_body(
        """
### What should we do?
Official URL is stale or dead

### What kind of document?
Dead or replaced official URL

### Airport
TTD

### State
OR

### Official URL
https://example.com/old.pdf

### Notes
404s now
"""
    )
    assert stale.report_type == "stale"
    wrong = parse_issue_body(
        """
### What should we do?
Listed document is wrong (wrong airport, kind, or file)

### What kind of document?
Airport Layout Plan (ALP)

### Airport
4S9

### State
OR

### Official URL
https://example.com/newsletter.pdf

### Notes
This is a newsletter
"""
    )
    assert wrong.report_type == "wrong"
    assert wrong.kind == "alp"


def test_resolve_add_success_closes_without_human() -> None:
    hint = IntakeHint(
        report_type="add",
        kind="master_plan",
        airport_lid="PDX",
        state="OR",
        source_url="https://pdx2045.org/",
        notes="",
    )
    outcome = resolve_intake(hint, status="preserved")
    assert outcome.close is True
    assert HUMAN_HANDLE not in outcome.comment
    assert "Official" in outcome.comment or "preserved" in outcome.comment.lower()


def test_resolve_unknown_airport_needs_human() -> None:
    hint = IntakeHint(
        report_type="add",
        kind="master_plan",
        airport_lid=None,
        state=None,
        source_url=None,
        notes="maybe an airport in Oregon",
    )
    outcome = resolve_intake(hint, status="needs_human")
    assert outcome.close is False
    assert HUMAN_HANDLE in outcome.comment
    assert isinstance(outcome, IntakeOutcome)
