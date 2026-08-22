"""Append-only audit log for rubric classifications."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

from pipeline.refresh import overlay_dir_from_env

CLASSIFICATIONS_NAME = "classifications.jsonl"


def classifications_path(overlay_dir: Path | None = None) -> Path:
    return overlay_dir_from_env(overlay_dir) / CLASSIFICATIONS_NAME


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


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
    path = classifications_path(overlay_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, separators=(",", ":")) + "\n")


def load_classifications(overlay_dir: Path | None = None) -> list[dict]:
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
    return rows


def classification_stats(overlay_dir: Path | None = None) -> dict:
    rows = load_classifications(overlay_dir)
    by_eval: dict[str, dict[str, int]] = {}
    by_classifier: dict[str, int] = {}
    for row in rows:
        name = str(row.get("evaluation") or "")
        category = str(row.get("category") or "")
        classifier = str(row.get("classifier") or "")
        by_eval.setdefault(name, {})
        by_eval[name][category] = by_eval[name].get(category, 0) + 1
        by_classifier[classifier] = by_classifier.get(classifier, 0) + 1
    return {
        "total": len(rows),
        "by_evaluation": by_eval,
        "by_classifier": by_classifier,
    }
