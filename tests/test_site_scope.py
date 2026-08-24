from __future__ import annotations

from pathlib import Path

import pytest

from catalog.seed import seed_catalog
from pipeline.queue import JobQueue, QueueJob
from pipeline.site_scope import (
    apply_scope_to_job,
    merge_scopes,
    scope_about,
    scope_after_airport_job,
    scope_after_link_check,
    scope_for_airport,
    scope_from_job,
)
from pipeline.site_build import enqueue_site_build

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("kind", ["vet", "review"])
def test_scope_after_review_change_includes_index_and_data(kind: str) -> None:
    catalog = seed_catalog(ROOT / "catalog")
    job = QueueJob(
        kind=kind,
        document_id="4s9-2019-alp",
        source_url="https://example.com/x.pdf",
        airport_lid="4S9",
    )
    scope = scope_after_airport_job(job, catalog)
    assert scope is not None
    assert scope.wants_airport("4S9")
    assert scope.include_index
    assert scope.include_airports_index
    assert scope.include_data


def test_scope_after_fetch_is_airport_only() -> None:
    catalog = seed_catalog(ROOT / "catalog")
    job = QueueJob(
        kind="fetch",
        document_id="4s9-2019-alp",
        source_url="https://example.com/x.pdf",
        airport_lid="4S9",
    )
    scope = scope_after_airport_job(job, catalog)
    assert scope is not None
    assert scope.wants_airport("4S9")
    assert not scope.include_index
    assert scope.include_data


def test_scope_round_trip_on_queue_job() -> None:
    catalog = seed_catalog(ROOT / "catalog")
    scope = scope_for_airport(catalog, "PDX", include_index=True, include_data=True)
    job = QueueJob(kind="site_build", document_id=None, source_url=None, airport_lid=None)
    apply_scope_to_job(job, scope)
    restored = scope_from_job(job, catalog)
    assert restored is not None
    assert restored.airport_lids == scope.airport_lids
    assert restored.include_index
    assert restored.include_data


def test_merge_scopes_widens_partial_jobs() -> None:
    catalog = seed_catalog(ROOT / "catalog")
    left = scope_for_airport(catalog, "4S9")
    right = scope_about()
    merged = merge_scopes(left, right)
    assert merged is not None
    assert merged.wants_airport("4S9")
    assert merged.include_about


def test_enqueue_site_build_merges_pending_scope(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("APTPLANS_QUEUE", str(tmp_path / "queue"))
    catalog = seed_catalog(ROOT / "catalog")
    first = scope_for_airport(catalog, "4S9")
    second = scope_about()
    assert enqueue_site_build(tmp_path / "queue", scope=first) is True
    assert enqueue_site_build(tmp_path / "queue", scope=second) is True
    queue = JobQueue(tmp_path / "queue")
    pending = scope_from_job(
        QueueJob.from_dict(
            __import__("json").loads(next((tmp_path / "queue" / "pending").glob("*.json")).read_text())
        ),
        catalog,
    )
    assert pending is not None
    assert pending.wants_airport("4S9")
    assert pending.include_about


def test_enqueue_site_build_ignores_active_job(tmp_path: Path, monkeypatch) -> None:
    import json

    monkeypatch.setenv("APTPLANS_QUEUE", str(tmp_path / "queue"))
    catalog = seed_catalog(ROOT / "catalog")
    queue = JobQueue(tmp_path / "queue")
    active_job = QueueJob(kind="site_build", document_id=None, source_url=None, airport_lid=None)
    apply_scope_to_job(active_job, scope_for_airport(catalog, "PDX"))
    active_path = queue.active / f"{active_job.id}.json"
    active_path.parent.mkdir(parents=True, exist_ok=True)
    active_path.write_text(json.dumps(active_job.to_dict(), indent=2) + "\n", encoding="utf-8")

    active_body = active_path.read_text(encoding="utf-8")
    assert enqueue_site_build(tmp_path / "queue", scope=scope_about()) is True
    pending_paths = list((tmp_path / "queue" / "pending").glob("*.json"))
    assert len(pending_paths) == 1
    pending = scope_from_job(QueueJob.from_dict(json.loads(pending_paths[0].read_text())), catalog)
    assert pending is not None
    assert pending.include_about
    assert active_path.read_text(encoding="utf-8") == active_body


def test_link_check_scope_refreshes_list_pages() -> None:
    scope = scope_after_link_check()
    assert scope.include_index
    assert scope.include_airports_index
    assert scope.include_data
