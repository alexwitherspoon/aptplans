from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from catalog.models import Airport, Grant
from pipeline.discovery_priority import (
    TIER_FUNDED_RECENT,
    TIER_FUNDED_STALE,
    TIER_NO_PLAN_FOUND,
    TIER_RECENT,
    TIER_STALE,
    discovery_tier,
    evaluated_recently,
    federal_obligation_by_lid,
    funded_obligation_by_lid,
    sort_airports_for_discovery,
)

NOW = datetime(2026, 8, 22, 12, 0, 0, tzinfo=timezone.utc)


def _airport(lid: str, *, in_npias: bool = False) -> Airport:
    return Airport(
        lid=lid,
        name=lid,
        city="City",
        state="OR",
        in_npias=in_npias,
    )


def test_funded_obligation_includes_federal_state_local() -> None:
    grants = [
        Grant(airport_lid="PDX", level="federal", obligated=100),
        Grant(airport_lid="EUG", level="state", amount=200),
        Grant(airport_lid="HIO", level="local", amount=50),
        Grant(airport_lid="ZZZ", level="other", amount=9999),
    ]
    assert funded_obligation_by_lid(grants) == {"PDX": 100, "EUG": 200, "HIO": 50}


def test_federal_obligation_by_lid_is_federal_only() -> None:
    grants = [
        Grant(airport_lid="PDX", level="federal", obligated=100),
        Grant(airport_lid="EUG", level="state", amount=999),
    ]
    assert federal_obligation_by_lid(grants) == {"PDX": 100}


def test_triage_funded_stale_before_unfunded_stale() -> None:
    funded = {"PDX": 100}
    assert (
        discovery_tier(
            _airport("PDX"),
            funded_by_lid=funded,
            status_rows={},
            published_lids=frozenset(),
            now=NOW,
        )
        == TIER_FUNDED_STALE
    )
    assert (
        discovery_tier(
            _airport("TTD"),
            funded_by_lid=funded,
            status_rows={},
            published_lids=frozenset(),
            now=NOW,
        )
        == TIER_STALE
    )


def test_triage_state_grant_counts_as_funded() -> None:
    funded = {"EUG": 200}
    assert (
        discovery_tier(
            _airport("EUG"),
            funded_by_lid=funded,
            status_rows={},
            published_lids=frozenset(),
            now=NOW,
        )
        == TIER_FUNDED_STALE
    )


def test_triage_stale_unfunded_before_funded_recent() -> None:
    funded = {"PDX": 100}
    recent = {"discovery_at": "2026-08-20T00:00:00Z"}
    assert (
        discovery_tier(
            _airport("PDX"),
            funded_by_lid=funded,
            status_rows={"PDX": recent},
            published_lids=frozenset(),
            recency_days=30,
            now=NOW,
        )
        == TIER_FUNDED_RECENT
    )
    assert (
        discovery_tier(
            _airport("TTD"),
            funded_by_lid=funded,
            status_rows={},
            published_lids=frozenset(),
            recency_days=30,
            now=NOW,
        )
        == TIER_STALE
    )


def test_evaluated_recently_uses_discovery_timestamp() -> None:
    assert evaluated_recently(
        {"discovery_at": "2026-08-20T00:00:00Z"},
        recency_days=30,
        now=NOW,
    )
    assert not evaluated_recently(
        {"discovery_at": "2026-06-01T00:00:00Z"},
        recency_days=30,
        now=NOW,
    )
    assert not evaluated_recently({}, recency_days=30, now=NOW)


def test_triage_deprioritizes_no_plan_found() -> None:
    tier = discovery_tier(
        _airport("PDX"),
        funded_by_lid={"PDX": 100},
        status_rows={
            "PDX": {
                "explored_at": "2026-01-01T00:00:00Z",
                "last_job_status": "dead",
            }
        },
        published_lids=frozenset(),
        now=NOW,
    )
    assert tier == TIER_NO_PLAN_FOUND


def test_sort_triage_funded_stale_first(tmp_path: Path) -> None:
    (tmp_path / "grants.jsonl").write_text(
        json.dumps(
            {
                "airport_lid": "ZZZ",
                "level": "local",
                "obligated": 500,
                "state": "OR",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    airports = sort_airports_for_discovery(
        [_airport("AAA"), _airport("ZZZ")],
        tmp_path,
        now=NOW,
    )
    assert [item.lid for item in airports] == ["ZZZ", "AAA"]


def test_sort_triage_prefers_stale_over_funded_recent(tmp_path: Path) -> None:
    (tmp_path / "grants.jsonl").write_text(
        "\n".join(
            json.dumps(row)
            for row in (
                {"airport_lid": "PDX", "level": "federal", "obligated": 1000, "state": "OR"},
                {"airport_lid": "TTD", "level": "state", "obligated": 10, "state": "OR"},
            )
        )
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / "pipeline_status.json").write_text(
        json.dumps({"PDX": {"discovery_at": "2026-08-20T00:00:00Z"}}) + "\n",
        encoding="utf-8",
    )
    airports = sort_airports_for_discovery(
        [_airport("PDX"), _airport("TTD")],
        tmp_path,
        recency_days=30,
        now=NOW,
    )
    assert [item.lid for item in airports] == ["TTD", "PDX"]


def test_sort_triage_orders_by_total_funding_within_tier(tmp_path: Path) -> None:
    (tmp_path / "grants.jsonl").write_text(
        "\n".join(
            json.dumps(row)
            for row in (
                {"airport_lid": "AAA", "level": "federal", "obligated": 100_000, "state": "OR"},
                {"airport_lid": "BBB", "level": "federal", "obligated": 10_000_000, "state": "OR"},
            )
        )
        + "\n",
        encoding="utf-8",
    )
    airports = sort_airports_for_discovery(
        [_airport("AAA", in_npias=True), _airport("BBB", in_npias=False)],
        tmp_path,
        now=NOW,
    )
    assert [item.lid for item in airports] == ["BBB", "AAA"]


def test_sort_triage_respects_disable_flag(tmp_path: Path) -> None:
    (tmp_path / "grants.jsonl").write_text(
        json.dumps({"airport_lid": "ZZZ", "level": "federal", "amount": 1}) + "\n",
        encoding="utf-8",
    )
    airports = sort_airports_for_discovery(
        [_airport("AAA"), _airport("ZZZ")],
        tmp_path,
        funded_first=False,
        now=NOW,
    )
    assert [item.lid for item in airports] == ["AAA", "ZZZ"]
