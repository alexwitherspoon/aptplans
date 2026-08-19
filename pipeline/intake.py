"""Parse GitHub intake issues as hints. Never treat the body as catalog overrides."""

from __future__ import annotations

from dataclasses import dataclass
import re

HUMAN_HANDLE = "@alexwitherspoon"

REPORT_TYPES = {
    "Add a missing document": "add",
    "Official URL is stale or dead": "stale",
    "Listed document is wrong (wrong airport, kind, or file)": "wrong",
    "Edition is out of date": "outdated",
    "Other": "other",
}

KINDS = {
    "Airport master plan": "master_plan",
    "Airport Layout Plan (ALP)": "alp",
    "State aviation or land-use statute": "statute",
    "Dead or replaced official URL": "other",
    "Other planning document": "other",
}


@dataclass(frozen=True)
class IntakeHint:
    report_type: str
    kind: str
    airport_lid: str | None
    state: str | None
    source_url: str | None
    notes: str


@dataclass(frozen=True)
class IntakeOutcome:
    close: bool
    comment: str
    status: str


def _section(body: str, heading: str) -> str:
    pattern = rf"### {re.escape(heading)}\s*\n(.*?)(?=\n### |\Z)"
    match = re.search(pattern, body, flags=re.S)
    if not match:
        return ""
    return match.group(1).strip().strip("_").strip()


def _normalize_lid(value: str) -> str | None:
    text = value.strip()
    if not text or text == "_No response_":
        return None
    token = text.split()[0].strip(",.").upper()
    if token.startswith("K") and len(token) == 4 and token[1:].isalpha():
        return token[1:]
    return token


def parse_issue_body(body: str) -> IntakeHint:
    report_label = _section(body, "What should we do?")
    kind_label = _section(body, "What kind of document?")
    airport = _section(body, "Airport")
    state = _section(body, "State")
    url = _section(body, "Official URL")
    notes = _section(body, "Notes")
    report_type = REPORT_TYPES.get(report_label, "other")
    kind = KINDS.get(kind_label, "other")
    lid = _normalize_lid(airport) if airport else None
    state_code = state.strip().upper()[:2] if state and state != "_No response_" else None
    source_url = None
    if url and url != "_No response_":
        source_url = url.split()[0]
    return IntakeHint(
        report_type=report_type,
        kind=kind,
        airport_lid=lid,
        state=state_code,
        source_url=source_url,
        notes=notes,
    )


def hint_can_queue(hint: IntakeHint) -> bool:
    """True when form fields are enough to fetch. LID need not already be in the catalog."""
    if not hint.source_url:
        return False
    if hint.kind == "statute":
        return True
    return bool(hint.airport_lid)


def resolve_intake(hint: IntakeHint, status: str) -> IntakeOutcome:
    """Map a worker result to a public comment. Close only when the hint is spent."""
    if status == "preserved":
        return IntakeOutcome(
            close=True,
            status=status,
            comment=(
                "Hint checked. The worker fetched this URL and stored a preservation "
                "copy. After the next site publish, the airport or document page will "
                "show Official source and Archived copy. This does not make the site "
                "an official FAA or airport publication."
            ),
        )
    if status == "dead":
        return IntakeOutcome(
            close=True,
            status=status,
            comment=(
                "Hint checked. The official URL did not return a live file (dead or "
                "moved). Completeness stays missing until a working official URL is found."
            ),
        )
    if status == "not_plan":
        return IntakeOutcome(
            close=True,
            status=status,
            comment=(
                "Hint checked. The fetched file does not look like an airport master "
                "plan or Airport Layout Plan (for example a newsletter or news article). "
                "It was not ingested as the plan."
            ),
        )
    if status == "ssi":
        return IntakeOutcome(
            close=True,
            status=status,
            comment=(
                "Hint checked. The file looks like SSI or a security-restricted drawing. "
                "It was not stored or published."
            ),
        )
    reason = "The worker could not finish this hint from the form fields alone."
    if not hint.source_url:
        reason = "No official URL was provided."
    elif not hint.airport_lid and hint.kind != "statute":
        reason = "The airport (FAA LID) is unknown."
    return IntakeOutcome(
        close=False,
        status="needs_human",
        comment=(
            f"{reason} {HUMAN_HANDLE} please take a look. A human is needed before "
            "this hint can be queued or closed. Reply with an FAA LID and a working "
            "official URL if you have them."
        ),
    )
