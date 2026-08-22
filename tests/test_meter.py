from __future__ import annotations

import json

from pipeline.meter import (
    brave_query_cap,
    budget_wait_seconds,
    charge_local,
    commit_brave_search,
    gemini_query_cap,
    ledger_summary,
    load_ledger,
    load_search_meter,
    parse_brave_ratelimit,
    reconcile,
    record_brave_cloud,
)


def test_legacy_meter_migrates_on_load(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("APTPLANS_CATALOG_OVERLAY", str(tmp_path))
    path = tmp_path / "search_meter.json"
    path.write_text(
        json.dumps({"month": load_ledger()["month"], "brave": 7, "gemini": 2}) + "\n",
        encoding="utf-8",
    )
    ledger = load_ledger()
    assert ledger["providers"]["brave"]["local"]["charged"] == 7
    assert ledger["providers"]["gemini"]["local"]["charged"] == 2


def test_charge_local_respects_cap(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("APTPLANS_CATALOG_OVERLAY", str(tmp_path))
    monkeypatch.setenv("APTPLANS_GEMINI_MONTHLY_CAP", "1")
    assert charge_local("gemini") is True
    assert charge_local("gemini") is False
    assert load_search_meter()["gemini"] == 1


def test_brave_query_cap_defaults(monkeypatch) -> None:
    monkeypatch.delenv("APTPLANS_SEARCH_MONTHLY_CAP", raising=False)
    monkeypatch.delenv("APTPLANS_BRAVE_MONTHLY_BUDGET_USD", raising=False)
    monkeypatch.delenv("APTPLANS_BRAVE_MONTHLY_CREDIT_USD", raising=False)
    monkeypatch.delenv("APTPLANS_BRAVE_USD_PER_1K", raising=False)
    assert brave_query_cap() == 6000


def test_gemini_query_cap_defaults(monkeypatch) -> None:
    monkeypatch.delenv("APTPLANS_GEMINI_MONTHLY_CAP", raising=False)
    monkeypatch.delenv("APTPLANS_GEMINI_MONTHLY_BUDGET_USD", raising=False)
    monkeypatch.delenv("APTPLANS_GEMINI_USD_PER_1K", raising=False)
    monkeypatch.delenv("APTPLANS_GEMINI_FREE_PROMPTS", raising=False)
    monkeypatch.delenv("APTPLANS_GEMINI_QUERIES_PER_PROMPT", raising=False)
    assert gemini_query_cap() == 5446


def test_parse_brave_ratelimit_monthly_window() -> None:
    headers = {
        "X-RateLimit-Policy": "1;w=1, 15000;w=2592000",
        "X-RateLimit-Limit": "1, 15000",
        "X-RateLimit-Remaining": "1, 14523",
        "X-RateLimit-Reset": "1, 1234567",
    }
    cloud = parse_brave_ratelimit(headers)
    assert cloud is not None
    assert cloud["monthly_limit"] == 15000
    assert cloud["monthly_remaining"] == 14523
    assert cloud["monthly_used"] == 477


def test_commit_brave_search_is_atomic(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("APTPLANS_CATALOG_OVERLAY", str(tmp_path))
    headers = {
        "X-RateLimit-Policy": "1;w=1, 15000;w=2592000",
        "X-RateLimit-Limit": "1, 15000",
        "X-RateLimit-Remaining": "1, 14523",
        "X-RateLimit-Reset": "1, 1234567",
    }
    assert commit_brave_search(headers) is True
    row = reconcile("brave")
    assert row["local_charged"] == 1
    assert row["cloud_used"] == 477


def test_budget_wait_seconds_uses_cloud_reset(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("APTPLANS_CATALOG_OVERLAY", str(tmp_path))
    monkeypatch.setenv("APTPLANS_SEARCH_MONTHLY_CAP", "1")
    headers = {
        "X-RateLimit-Policy": "1;w=1, 15000;w=2592000",
        "X-RateLimit-Limit": "1, 15000",
        "X-RateLimit-Remaining": "1, 14523",
        "X-RateLimit-Reset": "1, 600",
    }
    assert commit_brave_search(headers) is True
    assert budget_wait_seconds("brave") == 600.0


def test_record_brave_cloud_and_reconcile(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("APTPLANS_CATALOG_OVERLAY", str(tmp_path))
    charge_local("brave")
    charge_local("brave")
    record_brave_cloud(
        {
            "X-RateLimit-Policy": "1;w=1, 15000;w=2592000",
            "X-RateLimit-Limit": "1, 15000",
            "X-RateLimit-Remaining": "1, 14523",
            "X-RateLimit-Reset": "1, 1234567",
        }
    )
    row = reconcile("brave")
    assert row["local_charged"] == 2
    assert row["cloud_used"] == 477
    assert row["drift"] == -475

    summary = ledger_summary()
    assert summary["providers"]["brave"]["local_cap"] == brave_query_cap()
