from __future__ import annotations

from pathlib import Path
import json

from pipeline.outcomes import (
    bucket_for,
    compact_outcome,
    export_gold_candidates,
    gold_disagreements,
    load_outcomes,
    outcome_stats,
    record_outcome,
    training_signals,
)
from pipeline.review_api import make_server
from pipeline.review_client import DEFAULT_URL, load_review_env, review_credentials
from pipeline.queue import JobQueue


def test_bucket_maps_job_and_review_status() -> None:
    assert bucket_for(job_status="ssi") == "failed"
    assert bucket_for(job_status="dead") == "failed"
    assert bucket_for(job_status="not_plan") == "failed"
    assert bucket_for(job_status="needs_human") == "needs_human"
    assert bucket_for(review_status="needs_human") == "needs_human"
    assert bucket_for(review_status="auto_pass") == "accepted"
    assert bucket_for(review_status="published") == "accepted"
    assert bucket_for(review_status="pending") == "uncertain"
    assert bucket_for(job_status="preserved") == "uncertain"


def test_record_and_export_gold_candidates(tmp_path: Path) -> None:
    overlay = tmp_path / "overlay"
    record_outcome(
        overlay,
        {
            "id": "lex-econ-human",
            "lid": "LEX",
            "name": "Blue Grass Airport",
            "url": "https://www.bluegrassairport.com/wp-content/uploads/2024/08/Economic-Impact-Study-Comprehensive-Report.pdf",
            "label": "Economic Impact Study",
            "gold": {
                "same_airport": True,
                "kind": "not_plan",
                "confirm": False,
                "explore": False,
                "publish": False,
            },
            "job_status": "labeled",
            "source": "human",
        },
    )
    record_outcome(overlay, {"url": "https://example.com/x.pdf", "job_status": "ssi"})
    rows = load_outcomes(overlay)
    assert len(rows) == 2
    stats = outcome_stats(overlay_dir=overlay)
    assert stats["n"] == 2
    assert stats["counts"]["failed"] == 1
    assert stats["labeled"] == 1
    gold = export_gold_candidates(overlay)
    assert len(gold) == 1
    assert gold[0]["lid"] == "LEX"
    assert "excerpt" not in gold[0]


def test_compact_outcome_drops_excerpts() -> None:
    compact = compact_outcome(
        {
            "url": "https://example.org/plan.pdf",
            "lid": "PDX",
            "excerpt": "secret body",
            "body": "full text",
            "gold": {"same_airport": True, "kind": "master_plan", "noise": 1},
            "bucket": "uncertain",
        }
    )
    assert "excerpt" not in compact
    assert "body" not in compact
    assert compact["gold"] == {"same_airport": True, "kind": "master_plan"}


def test_training_signals_and_disagreements(tmp_path: Path) -> None:
    overlay = tmp_path / "overlay"
    record_outcome(
        overlay,
        {
            "id": "pdx-miss",
            "lid": "PDX",
            "url": "https://cdn.portofportland.com/pdfs/PDX_Master_Plan.pdf",
            "label": "PDX Master Plan",
            "gold": {
                "same_airport": True,
                "kind": "master_plan",
                "confirm": True,
                "explore": False,
                "publish": True,
            },
            "scored": {
                "same_airport": True,
                "kind": "hub",
                "confirm": False,
                "explore": True,
                "publish": False,
            },
            "job_status": "labeled",
            "source": "human",
        },
    )
    record_outcome(overlay, {"url": "https://example.org/pending.pdf", "job_status": "pending"})
    payload = training_signals(overlay)
    assert payload["stats"]["labeled"] == 1
    assert payload["gold"][0]["lid"] == "PDX"
    misses = gold_disagreements(overlay_dir=overlay)
    assert misses[0]["fail"] == ["kind", "confirm", "explore", "publish"]
    assert payload["uncertain"]
    assert payload.get("rejects") == []


def test_review_api_health_stats_and_label(tmp_path: Path) -> None:
    overlay = tmp_path / "overlay"
    overlay.mkdir()
    (overlay / "airports.jsonl").write_text(
        '{"lid":"PDX","name":"Portland","city":"Portland","state":"OR"}\n',
        encoding="utf-8",
    )
    (overlay / "grants.jsonl").write_text(
        '{"airport_lid":"PDX","level":"federal","obligated":1,"state":"OR"}\n',
        encoding="utf-8",
    )
    from pipeline.datasets import reconcile_catalog

    reconcile_catalog(overlay)
    token = "test-review-token"
    queue_dir = tmp_path / "queue"
    files_dir = tmp_path / "files"
    files_dir.mkdir()
    server = make_server(
        overlay,
        token,
        host="127.0.0.1",
        port=0,
        files_dir=files_dir,
        queue_dir=queue_dir,
    )
    host, port = server.server_address[:2]
    import threading
    from urllib.error import HTTPError
    from urllib.request import Request, urlopen

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://{host}:{port}"
    try:
        health = json.loads(urlopen(f"{base}/v1/health", timeout=2).read())
        assert health["ok"] is True
        denied = False
        try:
            urlopen(f"{base}/v1/stats", timeout=2)
        except HTTPError as exc:
            denied = exc.code == 401
        assert denied
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        stats = json.loads(
            urlopen(Request(f"{base}/v1/stats", headers=headers), timeout=2).read()
        )
        assert stats["n"] == 0
        evaluations = json.loads(
            urlopen(Request(f"{base}/v1/evaluations", headers=headers), timeout=2).read()
        )
        assert "grant_spend" in evaluations["tasks"]
        assert evaluations["stats"]["total"] == 0
        payload = json.dumps(
            {
                "id": "ttd-bound-human",
                "lid": "TTD",
                "url": "https://cdn.portofportland.com/pdfs/TTD-PAC-report.pdf",
                "label": "Shaping Our Future",
                "gold": {
                    "same_airport": True,
                    "kind": "master_plan",
                    "confirm": True,
                    "explore": False,
                    "publish": True,
                },
            }
        ).encode("utf-8")
        created = json.loads(
            urlopen(
                Request(f"{base}/v1/label", data=payload, headers=headers, method="POST"),
                timeout=2,
            ).read()
        )
        assert created["ok"] is True
        gold = json.loads(
            urlopen(Request(f"{base}/v1/gold", headers=headers), timeout=2).read()
        )
        assert gold["n"] == 1
        assert gold["cases"][0]["lid"] == "TTD"
        key_headers = {"X-Api-Key": token}
        signals = json.loads(
            urlopen(Request(f"{base}/v1/signals", headers=key_headers), timeout=2).read()
        )
        assert signals["stats"]["labeled"] == 1
        assert signals["gold"][0]["lid"] == "TTD"
        status = json.loads(
            urlopen(Request(f"{base}/v1/status", headers=headers), timeout=2).read()
        )
        assert status["ok"] is True
        assert status["summary"]["discovery_ready"] is True
        assert "datasets" in status
        assert "services" in status
        assert "queue" in status
        logs = json.loads(
            urlopen(Request(f"{base}/v1/logs", headers=headers), timeout=2).read()
        )
        assert "worker" in logs
        assert "outcomes" in logs
        queue = json.loads(
            urlopen(Request(f"{base}/v1/classification_queue", headers=headers), timeout=2).read()
        )
        assert queue["evaluation"] == "grant_spend"
        assert queue["n"] == 0
        grant_label = json.dumps(
            {
                "evaluation": "grant_spend",
                "grant_number": "G-TEST",
                "gold": {"spend_category": "other", "reason": "equipment"},
            }
        ).encode("utf-8")
        from catalog.models import Grant
        from catalog.store import write_grants_overlay

        write_grants_overlay(
            overlay,
            [Grant(airport_lid="PDX", grant_number="G-TEST", description="Zero Emissions Infrastructure")],
        )
        labeled = json.loads(
            urlopen(
                Request(f"{base}/v1/label", data=grant_label, headers=headers, method="POST"),
                timeout=2,
            ).read()
        )
        assert labeled["ok"] is True
        assert labeled["spend_category"] == "other"
        denied_again = False
        try:
            urlopen(Request(f"{base}/v1/signals", headers={"X-Api-Key": "wrong"}), timeout=2)
        except HTTPError as exc:
            denied_again = exc.code == 401
        assert denied_again
        from catalog.store import load_overlay, write_overlay_update

        document_sha = "d" * 64
        (files_dir / f"{document_sha}.pdf").write_bytes(b"%PDF-operator-payload")
        write_overlay_update(
            overlay,
            "pending-document",
            {
                "kind": "master_plan",
                "source_url": "https://example.com/plan.pdf",
                "completeness": "complete",
                "review_status": "pending",
                "content_sha256": document_sha,
            },
        )
        full_payload = urlopen(
            Request(
                f"{base}/v1/documents/pending-document/bytes",
                headers=headers,
            ),
            timeout=2,
        ).read()
        assert full_payload == b"%PDF-operator-payload"
        mutation = json.dumps({"review_status": "published"}).encode("utf-8")
        queued_review = json.loads(
            urlopen(
                Request(
                    f"{base}/v1/documents/pending-document",
                    data=mutation,
                    headers=headers,
                    method="PATCH",
                ),
                timeout=2,
            ).read()
        )
        assert queued_review["requested_review_status"] == "published"
        assert load_overlay(overlay)["pending-document"]["review_status"] == "pending"
        queued = JobQueue(queue_dir).claim()
        assert queued is not None
        assert queued.kind == "review"
        assert queued.document_id == "pending-document"
        assert queued.requested_review_status == "published"
    finally:
        server.shutdown()


def test_review_env_file_loads_token(tmp_path: Path, monkeypatch) -> None:
    import os

    monkeypatch.delenv("APTPLANS_REVIEW_TOKEN", raising=False)
    monkeypatch.delenv("APTPLANS_REVIEW_URL", raising=False)
    (tmp_path / ".env.review").write_text(
        "APTPLANS_REVIEW_TOKEN=local-review-key\n"
        "APTPLANS_REVIEW_URL=http://127.0.0.1:8787\n"
        "APTPLANS_SEARCH_KEY=should-not-load\n",
        encoding="utf-8",
    )
    load_review_env(tmp_path)
    token, url = review_credentials(tmp_path)
    assert token == "local-review-key"
    assert url == "http://127.0.0.1:8787"
    assert os.environ.get("APTPLANS_SEARCH_KEY") != "should-not-load"


def test_review_default_url_is_https_origin() -> None:
    assert DEFAULT_URL == "https://aptplans.org/review"


def test_redact_drops_proxy_and_bearer() -> None:
    from pipeline.service_log import redact

    assert "redacted" in redact("socks5h://user:pass@proxy.example:1080")
    assert "secret-token" not in redact("Authorization: Bearer secret-token")


def test_dotenv_loads_search_keys(tmp_path: Path, monkeypatch) -> None:
    import os

    monkeypatch.delenv("APTPLANS_SEARCH_KEY", raising=False)
    monkeypatch.delenv("APTPLANS_GEMINI_KEY", raising=False)
    monkeypatch.delenv("APTPLANS_SEARCH_PROVIDER", raising=False)
    (tmp_path / ".env").write_text(
        "APTPLANS_SEARCH_KEY=local-brave-key\n"
        "APTPLANS_GEMINI_KEY=local-gemini-key\n"
        "APTPLANS_SEARCH_PROVIDER=brave\n"
        "MEILI_MASTER_KEY=should-not-load\n",
        encoding="utf-8",
    )
    from pipeline.local_env import load_local_env

    load_local_env(tmp_path)
    assert os.environ["APTPLANS_SEARCH_KEY"] == "local-brave-key"
    assert os.environ["APTPLANS_GEMINI_KEY"] == "local-gemini-key"
    assert os.environ["APTPLANS_SEARCH_PROVIDER"] == "brave"
    assert os.environ.get("MEILI_MASTER_KEY") != "should-not-load"


def test_dotenv_does_not_override_empty_process_env(tmp_path: Path, monkeypatch) -> None:
    import os
    from pipeline.local_env import load_local_env

    monkeypatch.setenv("APTPLANS_GEMINI_KEY", "")
    (tmp_path / ".env").write_text("APTPLANS_GEMINI_KEY=from-file\n", encoding="utf-8")
    load_local_env(tmp_path)
    assert os.environ["APTPLANS_GEMINI_KEY"] == ""
