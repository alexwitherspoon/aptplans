from __future__ import annotations

import json
from pathlib import Path

from pipeline.discover_overlay import discover_next_airports
from pipeline.queue import JobQueue
from pipeline.search_plan import SearchHit


def test_discover_enqueues_fixture_hits(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("APTPLANS_CATALOG_OVERLAY", str(tmp_path))
    monkeypatch.setenv("APTPLANS_LIVE_SEARCH", "1")
    monkeypatch.setenv("APTPLANS_GEMINI_KEY", "")
    (tmp_path / "airports.jsonl").write_text(
        json.dumps(
            {
                "lid": "4S9",
                "name": "Mulino State Airport",
                "city": "Mulino",
                "state": "OR",
                "website": "https://example.com/mulino-4s9.aspx",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    def fake_search(query: str) -> list[SearchHit]:
        return [
            SearchHit(
                title="Mulino State Airport",
                url="https://example.com/mulino-4s9.aspx",
                snippet="ODAV airport page",
                query=query,
            ),
            SearchHit(
                title="ALP",
                url="https://www.oregon.gov/aviation/airports/Documents/4S9/ODA_Doc_4S9_ALP.pdf",
                snippet="",
                query=query,
            ),
        ]

    queue_dir = tmp_path / "queue"
    result = discover_next_airports(
        tmp_path,
        queue_dir,
        limit=1,
        search_fn=fake_search,
        escalate_fn=lambda *_args, **_kwargs: [],
    )
    assert result["airports"] == ["4S9"]
    assert result["explore_jobs"] == 1
    assert result["fetch_jobs"] == 1
    pending = list((queue_dir / "pending").glob("*.json"))
    assert len(pending) == 2
    kinds = set()
    for path in pending:
        kinds.add(json.loads(path.read_text(encoding="utf-8"))["kind"])
    assert kinds == {"explore", "fetch"}


def test_discover_cursor_advances(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("APTPLANS_CATALOG_OVERLAY", str(tmp_path))
    monkeypatch.setenv("APTPLANS_LIVE_SEARCH", "1")
    monkeypatch.setenv("APTPLANS_GEMINI_KEY", "")
    rows = [
        {"lid": "PDX", "name": "Portland", "city": "Portland", "state": "OR", "website": ""},
        {"lid": "TTD", "name": "Troutdale", "city": "Troutdale", "state": "OR", "website": ""},
    ]
    (tmp_path / "airports.jsonl").write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n",
        encoding="utf-8",
    )

    def fake_search(_query: str) -> list[SearchHit]:
        return []

    queue_dir = tmp_path / "queue"
    first = discover_next_airports(tmp_path, queue_dir, limit=1, search_fn=fake_search)
    second = discover_next_airports(tmp_path, queue_dir, limit=1, search_fn=fake_search)
    assert first["airports"] == ["PDX"]
    assert second["airports"] == ["TTD"]
    cursor = json.loads((tmp_path / "discovery_cursor.json").read_text(encoding="utf-8"))
    assert cursor["index"] == 0
    assert cursor["last_lids"] == ["TTD"]


def test_discover_skips_when_live_search_off(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("APTPLANS_CATALOG_OVERLAY", str(tmp_path))
    monkeypatch.delenv("APTPLANS_LIVE_SEARCH", raising=False)
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.setenv("CI", "true")
    result = discover_next_airports(tmp_path, tmp_path / "queue")
    assert result["skipped"] == "live_search_off"
    assert not list(JobQueue(tmp_path / "queue").pending.glob("*.json"))
