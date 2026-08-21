from __future__ import annotations

import os

from pipeline.pace import (
    DEFAULT_AIRPORT_CONCURRENCY,
    DEFAULT_JOB_PAUSE_SEC,
    airport_concurrency,
    job_pause_seconds,
)


def test_airport_concurrency_defaults(monkeypatch) -> None:
    monkeypatch.delenv("APTPLANS_AIRPORT_CONCURRENCY", raising=False)
    assert airport_concurrency() == DEFAULT_AIRPORT_CONCURRENCY


def test_airport_concurrency_env(monkeypatch) -> None:
    monkeypatch.setenv("APTPLANS_AIRPORT_CONCURRENCY", "3")
    assert airport_concurrency() == 3
    monkeypatch.setenv("APTPLANS_AIRPORT_CONCURRENCY", "0")
    assert airport_concurrency() == 1


def test_job_pause_defaults(monkeypatch) -> None:
    monkeypatch.delenv("APTPLANS_JOB_PAUSE_SEC", raising=False)
    assert job_pause_seconds() == DEFAULT_JOB_PAUSE_SEC


def test_job_pause_env(monkeypatch) -> None:
    monkeypatch.setenv("APTPLANS_JOB_PAUSE_SEC", "5")
    assert job_pause_seconds() == 5.0
    monkeypatch.setenv("APTPLANS_JOB_PAUSE_SEC", "-1")
    assert job_pause_seconds() == 0.0
