"""Production scoring outcomes. Origin disk only. Not a publish. Not a public API.

Buckets:
- accepted: overlay review_status auto_pass or published
- uncertain: hashed snapshot still pending, or evidence scores near a threshold
- needs_human: overlay or job needs_human (SSI, uncaught retries, missing URL)
- failed: dead, ssi, not_plan, or a gate that stored a 90-day private reject copy instead of a public file

The worker appends one JSONL row per job. A private review API reads that log
and overlay documents.jsonl so humans can label packets while production runs.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json

from pipeline.evidence import Packet, score_packet
from pipeline.refresh import overlay_dir_from_env
from pipeline.reject import compact_reject, live_rejects, purge_expired

BUCKETS = ("accepted", "uncertain", "needs_human", "failed")
FAILED_JOBS = frozenset({"dead", "ssi", "not_plan", "not_file"})
OUTCOMES_NAME = "outcomes.jsonl"
GOLD_FIELDS = ("same_airport", "kind", "confirm", "explore", "publish")
SIGNAL_KEYS = (
    "at",
    "bucket",
    "id",
    "job_id",
    "job_kind",
    "document_id",
    "lid",
    "name",
    "state",
    "url",
    "label",
    "job_status",
    "review_status",
    "source",
    "scored",
    "gold",
    "shape",
    "reject_sha256",
    "reject_reason",
    "reject_expires_at",
    "reject_stored",
)
BANNED_SIGNAL_KEYS = frozenset({"excerpt", "body", "source_bytes", "text", "bytes"})


def outcomes_path(overlay_dir: Path | None = None) -> Path:
    return overlay_dir_from_env(overlay_dir) / OUTCOMES_NAME


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def bucket_for(
    *,
    job_status: str | None = None,
    review_status: str | None = None,
    scored: dict | None = None,
) -> str:
    """Map worker/overlay state onto the four feedback buckets."""
    status = (job_status or "").strip()
    review = (review_status or "").strip()
    if status in FAILED_JOBS:
        return "failed"
    if status == "needs_human" or review == "needs_human":
        return "needs_human"
    if review in {"auto_pass", "published"} or status in {"auto_pass", "published"}:
        return "accepted"
    if scored and _near_threshold(scored):
        return "uncertain"
    if review == "pending" or status in {"pending", "preserved"}:
        return "uncertain"
    if status == "live" or status == "moved":
        return "accepted"
    return "uncertain"


def _near_threshold(scored: dict) -> bool:
    confirm = float(scored.get("confirm_score") or 0.0)
    publish = float(scored.get("publish_score") or 0.0)
    explore = float(scored.get("explore_score") or 0.0)
    return any(abs(value) < 1.0 for value in (confirm - 2.5, publish - 3.5, explore - 2.5))


def score_job_signal(*, lid: str = "", name: str = "", url: str = "", label: str = "") -> dict:
    """URL+label evidence only. Does not open PDFs. Used as a production prior."""
    if not url:
        return {}
    scored = score_packet(Packet(lid=lid, name=name, url=url, label=label))
    return {
        "same_airport": scored["same_airport"],
        "kind": scored["kind"],
        "confirm": scored["confirm"],
        "explore": scored["explore"],
        "publish": scored["publish"],
        "confirm_score": scored["confirm_score"],
        "explore_score": scored["explore_score"],
        "publish_score": scored["publish_score"],
    }


def _json_safe(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return str(value)


def record_outcome(
    overlay_dir: Path | None,
    row: dict,
) -> dict:
    """Append one observation. Never raises into the worker drain."""
    try:
        scored = row.get("scored") if isinstance(row.get("scored"), dict) else None
        payload = {
            "at": utc_now(),
            "bucket": row.get("bucket") or bucket_for(
                job_status=row.get("job_status"),
                review_status=row.get("review_status"),
                scored=scored,
            ),
            **{key: value for key, value in row.items() if key != "bucket"},
        }
        payload["bucket"] = payload["bucket"] if payload["bucket"] in BUCKETS else "uncertain"
        payload = _json_safe(payload)
        dest = outcomes_path(overlay_dir)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with dest.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=True) + "\n")
    except Exception:
        return _json_safe(row)
    return payload


def load_outcomes(overlay_dir: Path | None = None) -> list[dict]:
    path = outcomes_path(overlay_dir)
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def outcome_stats(rows: list[dict] | None = None, overlay_dir: Path | None = None) -> dict:
    rows = rows if rows is not None else load_outcomes(overlay_dir)
    counts = {bucket: 0 for bucket in BUCKETS}
    labeled = 0
    for row in rows:
        bucket = row.get("bucket")
        if bucket in counts:
            counts[bucket] += 1
        if row.get("gold"):
            labeled += 1
    n = len(rows) or 1
    return {
        "n": len(rows),
        "counts": counts,
        "labeled": labeled,
        "rates": {bucket: counts[bucket] / n for bucket in BUCKETS},
    }


def export_gold_candidates(overlay_dir: Path | None = None) -> list[dict]:
    """Human-labeled outcomes that can be merged into score_gold.json."""
    out = []
    for row in load_outcomes(overlay_dir):
        gold = row.get("gold")
        url = row.get("url") or ""
        if not gold or not url.startswith("http"):
            continue
        if "example.com" in url or "/example/" in url:
            continue
        case = {
            "id": row.get("id") or row.get("document_id"),
            "lid": row.get("lid") or "",
            "name": row.get("name") or "",
            "state": row.get("state") or "",
            "url": url,
            "label": row.get("label") or "",
            "gold": gold,
            "from_outcome": row.get("at"),
        }
        if row.get("shape"):
            case["shape"] = row["shape"]
        out.append(case)
    return out


def compact_outcome(row: dict) -> dict:
    """Drop excerpts and full text. Keep the fields a scoring pass can use."""
    out = {}
    for key in SIGNAL_KEYS:
        value = row.get(key)
        if value in (None, ""):
            continue
        out[key] = value
    for key in BANNED_SIGNAL_KEYS:
        out.pop(key, None)
    gold = out.get("gold")
    if isinstance(gold, dict):
        out["gold"] = {field: gold[field] for field in GOLD_FIELDS if field in gold}
    return out


def gold_disagreements(rows: list[dict] | None = None, overlay_dir: Path | None = None) -> list[dict]:
    """Labeled outcomes whose stored scorer output does not match gold."""
    rows = rows if rows is not None else load_outcomes(overlay_dir)
    misses = []
    for row in rows:
        gold = row.get("gold")
        scored = row.get("scored")
        if not isinstance(gold, dict) or not isinstance(scored, dict):
            continue
        fail = [field for field in GOLD_FIELDS if field in gold and scored.get(field) != gold.get(field)]
        if not fail:
            continue
        compact = compact_outcome(row)
        compact["fail"] = fail
        misses.append(compact)
    return misses


def training_signals(
    overlay_dir: Path | None = None,
    *,
    per_bucket: int = 80,
    reject_dir: Path | None = None,
) -> dict:
    """Compact production feedback for local scoring work. No excerpts."""
    rows = load_outcomes(overlay_dir)
    by_bucket: dict[str, list[dict]] = {bucket: [] for bucket in BUCKETS}
    for row in rows:
        bucket = row.get("bucket")
        if bucket in by_bucket:
            by_bucket[bucket].append(compact_outcome(row))
    gold = export_gold_candidates(overlay_dir)
    rejects = []
    if reject_dir is not None:
        try:
            purge_expired(dest=reject_dir)
        except OSError:
            pass
        rejects = [compact_reject(row) for row in live_rejects(dest=reject_dir)]
    return {
        "stats": outcome_stats(rows),
        "gold": gold,
        "disagreements": gold_disagreements(rows),
        "accepted": by_bucket["accepted"][-min(20, per_bucket) :],
        "uncertain": by_bucket["uncertain"][-per_bucket:],
        "needs_human": by_bucket["needs_human"][-per_bucket:],
        "failed": by_bucket["failed"][-per_bucket:],
        "rejects": rejects[-per_bucket:],
    }
