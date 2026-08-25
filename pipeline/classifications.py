"""Append-only audit log for rubric classifications."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import os

from pipeline.refresh import overlay_dir_from_env

CLASSIFICATIONS_NAME = "classifications.jsonl"


def classifications_path(overlay_dir: Path | None = None) -> Path:
    return overlay_dir_from_env(overlay_dir) / CLASSIFICATIONS_NAME


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _at_release_cutoff(rows: list[dict]) -> list[dict]:
    cutoff = os.environ.get("APTPLANS_AUDIT_CUTOFF", "").strip()
    if not cutoff:
        return rows
    return [row for row in rows if str(row.get("at") or "") <= cutoff]


def record_classification(
    overlay_dir: Path,
    *,
    evaluation: str,
    input_id: str,
    category: str,
    classifier: str,
    reason: str = "",
) -> None:
    row = {
        "at": utc_now(),
        "evaluation": evaluation,
        "input_id": input_id,
        "category": category,
        "classifier": classifier,
        "reason": (reason or "")[:200],
    }
    if os.environ.get("APTPLANS_DOMAIN_STORE", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }:
        from pipeline.status import queue_dir_from_env

        root = queue_dir_from_env()
        if os.environ.get("APTPLANS_CONTROL_WRITER") == "1":
            from pipeline.queue import ControlQueue

            ControlQueue(root).append_audit("classifications", row)
        else:
            from pipeline.domain_store import DomainStore

            DomainStore(root).append_audit("classifications", row)
        return
    path = classifications_path(overlay_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, separators=(",", ":")) + "\n")


def load_classifications(overlay_dir: Path | None = None) -> list[dict]:
    if os.environ.get("APTPLANS_DOMAIN_STORE", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }:
        from pipeline.domain_store import DomainStore
        from pipeline.queue import ControlQueue
        from pipeline.status import queue_dir_from_env

        root = queue_dir_from_env()
        rows = DomainStore(root).audit_records("classifications")
        rows += ControlQueue(root).audit_records("classifications")
        rows.sort(key=lambda row: str(row.get("at") or ""))
        return _at_release_cutoff(rows)
    path = classifications_path(overlay_dir)
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
    return _at_release_cutoff(rows)


def classification_stats(overlay_dir: Path | None = None) -> dict:
    rows = load_classifications(overlay_dir)
    by_eval: dict[str, dict[str, int]] = {}
    by_classifier: dict[str, int] = {}
    month_prefix = datetime.now(timezone.utc).strftime("%Y-%m")
    month_total = 0
    for row in rows:
        name = str(row.get("evaluation") or "")
        category = str(row.get("category") or "")
        classifier = str(row.get("classifier") or "")
        by_eval.setdefault(name, {})
        by_eval[name][category] = by_eval[name].get(category, 0) + 1
        by_classifier[classifier] = by_classifier.get(classifier, 0) + 1
        if str(row.get("at") or "").startswith(month_prefix):
            month_total += 1
    return {
        "total": len(rows),
        "month_total": month_total,
        "by_evaluation": by_eval,
        "by_classifier": by_classifier,
    }
