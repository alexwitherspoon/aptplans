"""Strict one-time import from mutable overlays into domain generations."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pipeline.domain_store import DomainStore, entity_key
from pipeline.status import queue_dir_from_env


ENTITY_FILES = {
    "airports.jsonl": ("airports", "lid"),
    "grants.jsonl": ("grants", "grant_number"),
    "budgets.jsonl": ("budgets", "id"),
    "overviews.jsonl": ("overviews", "airport_lid"),
    "documents.jsonl": ("documents", "id"),
    "changes.jsonl": ("changes", "id"),
}
AUDIT_FILES = {
    "classifications.jsonl": "classifications",
    "outcomes.jsonl": "outcomes",
}


def _read_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    rows: list[dict] = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path.name}:{line_number}: invalid JSON") from exc
        if not isinstance(row, dict):
            raise ValueError(f"{path.name}:{line_number}: expected object")
        rows.append(row)
    return rows


def _in_state_scope(row: dict, state: str, airport_lids: set[str]) -> bool:
    row_state = str(row.get("state") or "").strip().upper()
    if row_state:
        return row_state == state
    lid = str(
        row.get("airport_lid")
        or row.get("lid")
        or ""
    ).strip().upper()
    return bool(lid) and lid in airport_lids


def import_overlays(
    overlay_dir: Path,
    ledger_root: Path,
    *,
    confirmed_preproduction: bool,
    state_scope: str | None = None,
) -> dict:
    if not confirmed_preproduction:
        raise ValueError("cutover requires --confirm-preproduction-cutover")
    store = DomainStore(ledger_root)
    current_id = store.current_generation_id()
    current = store.snapshot(current_id) if current_id is not None else None

    state_scope = (state_scope or "").strip().upper() or None
    source_rows = {
        filename: _read_jsonl(overlay_dir / filename)
        for filename in ENTITY_FILES
    }
    airport_lids = {
        str(row.get("lid") or "").strip().upper()
        for row in source_rows["airports.jsonl"]
        if not state_scope
        or str(row.get("state") or "").strip().upper() == state_scope
    }

    updates: dict[tuple[str, str], dict] = {}
    expected_counts: dict[str, int] = {}
    for filename, (entity_type, key_field) in ENTITY_FILES.items():
        rows = source_rows[filename]
        if state_scope:
            rows = [
                row
                for row in rows
                if _in_state_scope(row, state_scope, airport_lids)
            ]
        expected_counts[entity_type] = len(rows)
        seen: set[str] = set()
        for row in rows:
            key = entity_key(entity_type, row, key_field)
            if key in seen:
                raise ValueError(f"{filename}: duplicate key {key}")
            seen.add(key)
            updates[(entity_type, key)] = row

    datasets_path = overlay_dir / "datasets.json"
    dataset_state: dict[str, dict] = {}
    if datasets_path.is_file():
        payload = json.loads(datasets_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or not isinstance(
            payload.get("datasets") or {},
            dict,
        ):
            raise ValueError("datasets.json: invalid dataset catalog")
        dataset_state = dict(payload.get("datasets") or {})
        if state_scope:
            for entity_type, count in expected_counts.items():
                if entity_type not in dataset_state:
                    continue
                dataset_state[entity_type] = {
                    **dataset_state[entity_type],
                    "rows": count,
                    "scope": {"state": state_scope},
                }

    if current is not None and current.entities:
        if current.entities != updates:
            raise RuntimeError("domain store already contains different entities")
        snapshot = current
    else:
        snapshot = store.commit(
            updates,
            reason="pre-production domain cutover",
            actor="cutover",
            expected_generation_id=current_id,
            dataset_state=dataset_state,
        )

    audit_counts: dict[str, int] = {}
    for filename, stream in AUDIT_FILES.items():
        rows = _read_jsonl(overlay_dir / filename)
        if state_scope:
            rows = [
                row
                for row in rows
                if _in_state_scope(row, state_scope, airport_lids)
            ]
        for index, row in enumerate(rows, start=1):
            store.append_audit(
                stream,
                row,
                event_key=f"import:{filename}:{index}",
                generation_id=snapshot.generation_id,
            )
        audit_counts[stream] = len(rows)

    actual_counts = {
        entity_type: len(snapshot.rows(entity_type))
        for entity_type in expected_counts
    }
    if actual_counts != expected_counts:
        raise RuntimeError(
            f"domain import count mismatch: expected={expected_counts} actual={actual_counts}"
        )
    return {
        "generation_id": snapshot.generation_id,
        "entities": actual_counts,
        "audit": audit_counts,
        "state_scope": state_scope,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("overlay_dir", type=Path)
    parser.add_argument("--queue-dir", type=Path)
    parser.add_argument("--state")
    parser.add_argument("--confirm-preproduction-cutover", action="store_true")
    args = parser.parse_args()
    result = import_overlays(
        args.overlay_dir,
        queue_dir_from_env(args.queue_dir),
        confirmed_preproduction=args.confirm_preproduction_cutover,
        state_scope=args.state,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
