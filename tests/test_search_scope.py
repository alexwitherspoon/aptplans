from __future__ import annotations

from catalog.models import Airport
from pipeline.search_scope import (
    case_from_airport,
    in_search_scope,
    parse_search_states,
    scoped_overlay_airports,
)


def test_search_states_default_oregon(monkeypatch) -> None:
    monkeypatch.delenv("APTPLANS_SEARCH_STATES", raising=False)
    assert parse_search_states() == frozenset({"OR"})
    assert in_search_scope("OR", parse_search_states()) is True
    assert in_search_scope("WA", parse_search_states()) is False


def test_unknown_search_state_is_rejected() -> None:
    try:
        parse_search_states("ZZ")
    except ValueError as exc:
        assert "ZZ" in str(exc)
        return
    raise AssertionError("expected ValueError")


def test_search_states_widen_and_all(monkeypatch) -> None:
    monkeypatch.setenv("APTPLANS_SEARCH_STATES", "OR,WA")
    assert parse_search_states() == frozenset({"OR", "WA"})
    assert parse_search_states("*") is None
    assert in_search_scope("MA", None) is True
    assert in_search_scope("MA", parse_search_states("*")) is True


def test_scoped_overlay_keeps_oregon_only(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("APTPLANS_SEARCH_STATES", raising=False)
    (tmp_path / "airports.jsonl").write_text(
        '{"lid":"PDX","name":"Portland Intl","city":"Portland","state":"OR"}\n'
        '{"lid":"BVY","name":"Beverly","city":"Beverly","state":"MA"}\n'
        '{"lid":"HIO","name":"Hillsboro","city":"Hillsboro","state":"OR"}\n',
        encoding="utf-8",
    )
    rows = scoped_overlay_airports(tmp_path, states=parse_search_states())
    assert [row.lid for row in rows] == ["PDX", "HIO"]
    limited = scoped_overlay_airports(tmp_path, states=parse_search_states(), limit=1)
    assert [row.lid for row in limited] == ["PDX"]
    case = case_from_airport(Airport(lid="PDX", name="Portland Intl", city="Portland", state="OR"))
    assert case["airport_lid"] == "PDX"
    assert case["documents"] == []
