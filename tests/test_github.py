from __future__ import annotations

from pipeline.github import GitHubIntake, Issue
from pipeline.intake import HUMAN_HANDLE, IntakeHint, resolve_intake


class FakeGitHub:
    def __init__(self) -> None:
        self.comments: list[tuple[int, str]] = []
        self.closed: list[int] = []

    def comment(self, number: int, body: str) -> None:
        self.comments.append((number, body))

    def close(self, number: int) -> None:
        self.closed.append(number)

    def open_intake_issues(self) -> list[Issue]:
        return []


def test_resolved_hint_comments_then_closes() -> None:
    client = FakeGitHub()
    hint = IntakeHint(
        report_type="add",
        kind="master_plan",
        airport_lid="4S9",
        state="OR",
        source_url="https://example.com/inventory.pdf",
        notes="",
    )
    outcome = resolve_intake(hint, status="preserved")
    GitHubIntake(client).apply(12, outcome)
    assert client.comments == [(12, outcome.comment)]
    assert client.closed == [12]
    assert HUMAN_HANDLE not in outcome.comment


def test_needs_human_mentions_maintainer_and_stays_open() -> None:
    client = FakeGitHub()
    hint = IntakeHint(
        report_type="add",
        kind="master_plan",
        airport_lid=None,
        state=None,
        source_url=None,
        notes="somewhere in Oregon",
    )
    outcome = resolve_intake(hint, status="needs_human")
    GitHubIntake(client).apply(9, outcome)
    assert HUMAN_HANDLE in outcome.comment
    assert client.comments == [(9, outcome.comment)]
    assert client.closed == []


def test_intake_ack_workflow_mentions_maintainer() -> None:
    from pathlib import Path

    text = (
        Path(__file__).resolve().parents[1]
        / ".github"
        / "workflows"
        / "intake-ack.yml"
    ).read_text(encoding="utf-8")
    assert "@alexwitherspoon" in text
    assert "hint" in text.lower()
    assert "intake" in text
