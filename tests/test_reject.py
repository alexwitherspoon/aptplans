from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import hashlib
import json

from pipeline.queue import QueueJob
from pipeline.reject import (
    get_reject,
    live_rejects,
    purge_expired,
    read_reject_bytes,
    store_reject,
    training_case,
)
from pipeline.run_once import process_fetch
from pipeline.review_api import make_server
from pipeline.review_client import review_headers


def test_store_reject_keeps_bytes_off_public_files(tmp_path: Path) -> None:
    files = tmp_path / "files"
    files.mkdir()
    data = b"%PDF-1.4 newsletter"
    row = store_reject(
        reason="not_plan",
        url="https://example.com/Port_News_Fall.pdf",
        data=data,
        lid="4S9",
        files_dir=files,
    )
    digest = hashlib.sha256(data).hexdigest()
    assert row["sha256"] == digest
    assert row["stored"] is True
    assert row["reason"] == "not_plan"
    assert (tmp_path / "reject" / f"{digest}.pdf").read_bytes() == data
    assert list(files.iterdir()) == []
    case = training_case(row, source=f"data/score/review/rejects/{digest}.pdf")
    assert case is not None
    assert case["gold"]["kind"] == "not_plan"
    assert case["gold"]["publish"] is False
    assert case["source"].endswith(f"{digest}.pdf")


def test_gate_failure_stores_reject_not_catalog(tmp_path: Path) -> None:
    files = tmp_path / "files"
    overlay = tmp_path / "catalog"
    job = QueueJob(
        kind="fetch",
        document_id=None,
        source_url="https://example.com/Port_News_Fall.pdf",
        airport_lid="4S9",
        state="OR",
    )
    status = process_fetch(
        job,
        files,
        overlay,
        Path(__file__).resolve().parents[1] / "catalog",
        data=b"%PDF-1.4 news",
    )
    assert status == "not_plan"
    assert list(files.glob("*")) == []
    assert not (overlay / "documents.jsonl").is_file()
    digest = hashlib.sha256(b"%PDF-1.4 news").hexdigest()
    stored = tmp_path / "reject" / f"{digest}.pdf"
    assert stored.is_file()
    assert job.reject_record["sha256"] == digest


def test_ssi_is_rejected_privately(tmp_path: Path) -> None:
    files = tmp_path / "files"
    overlay = tmp_path / "catalog"
    job = QueueJob(
        kind="fetch",
        document_id=None,
        source_url="https://example.com/PDX_ALP_SSI_sheet.pdf",
        airport_lid="PDX",
        state="OR",
    )
    status = process_fetch(
        job,
        files,
        overlay,
        Path(__file__).resolve().parents[1] / "catalog",
        data=b"%PDF-1.4 ssi",
    )
    assert status == "ssi"
    assert list(files.glob("*")) == []
    assert list((tmp_path / "reject").glob("*.pdf"))
    case = training_case(job.reject_record)
    assert case is not None
    assert case["gold"]["publish"] is False


def test_purge_drops_expired_reject(tmp_path: Path) -> None:
    files = tmp_path / "files"
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    row = store_reject(
        reason="not_plan",
        url="https://example.com/old.pdf",
        data=b"%PDF-1.4 old",
        files_dir=files,
        now=now,
    )
    later = now + timedelta(days=91)
    summary = purge_expired(files_dir=files, now=later)
    assert summary["dropped"] == 1
    assert summary["kept"] == 0
    assert get_reject(row["sha256"], files_dir=files, now=later) is None
    assert read_reject_bytes(row["sha256"], files_dir=files, now=later) is None


def test_review_api_lists_and_sends_reject_bytes(tmp_path: Path) -> None:
    overlay = tmp_path / "overlay"
    overlay.mkdir()
    files = tmp_path / "files"
    data = b"%PDF-1.4 reject-body"
    row = store_reject(
        reason="not_plan",
        url="https://example.com/budget.pdf",
        data=data,
        lid="PDX",
        files_dir=files,
    )
    token = "test-review-token"
    server = make_server(
        overlay, token, host="127.0.0.1", port=0, reject_dir=tmp_path / "reject"
    )
    import threading
    from urllib.request import Request, urlopen

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    base = f"http://{host}:{port}"
    headers = review_headers(token)
    try:
        listed = json.loads(
            urlopen(Request(f"{base}/v1/rejects", headers=headers), timeout=2).read()
        )
        assert listed["n"] == 1
        assert listed["rejects"][0]["sha256"] == row["sha256"]
        assert listed["cases"][0]["gold"]["kind"] == "not_plan"
        signals = json.loads(
            urlopen(Request(f"{base}/v1/signals", headers=headers), timeout=2).read()
        )
        assert signals["rejects"][0]["sha256"] == row["sha256"]
        body = urlopen(
            Request(f"{base}/v1/rejects/{row['sha256']}/bytes", headers=headers),
            timeout=2,
        ).read()
        assert body == data
    finally:
        server.shutdown()


def test_duplicate_hash_does_not_reset_expiry(tmp_path: Path) -> None:
    files = tmp_path / "files"
    now = datetime(2026, 3, 1, tzinfo=timezone.utc)
    first = store_reject(
        reason="not_plan",
        url="https://example.com/a.pdf",
        data=b"%PDF-1.4 same",
        files_dir=files,
        now=now,
    )
    later = store_reject(
        reason="not_plan",
        url="https://example.com/a.pdf",
        data=b"%PDF-1.4 same",
        files_dir=files,
        now=now + timedelta(days=30),
    )
    assert later["expires_at"] == first["expires_at"]
    assert len(live_rejects(files_dir=files, now=now + timedelta(days=1))) == 1
