"""Immutable domain generations stored beside the worker job ledger."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import uuid

from pipeline.queue import JobQueue, _connect, _utc_now


EntityKey = tuple[str, str]


def _canonical(payload: dict) -> tuple[str, str]:
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return encoded, hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def entity_key(entity_type: str, row: dict, key_field: str) -> str:
    raw_key = row.get(key_field)
    if entity_type == "grants" and not raw_key:
        identity = {
            key: row.get(key)
            for key in (
                "airport_lid",
                "fiscal_year",
                "award_date",
                "amount",
                "description",
                "source_url",
                "level",
                "entity",
            )
        }
        _encoded, raw_key = _canonical(identity)
    if raw_key is None or str(raw_key).strip() == "":
        raise ValueError(f"{entity_type} row is missing key field {key_field}")
    return str(raw_key)


@dataclass(frozen=True)
class DomainSnapshot:
    generation_id: str
    parent_generation_id: str | None
    committed_at: str
    dataset_state: dict[str, dict]
    entities: dict[EntityKey, dict]

    def rows(self, entity_type: str) -> list[dict]:
        return [
            dict(payload)
            for (row_type, _key), payload in sorted(self.entities.items())
            if row_type == entity_type
        ]

    def get(self, entity_type: str, entity_key: str) -> dict | None:
        payload = self.entities.get((entity_type, entity_key))
        return dict(payload) if payload is not None else None


class StaleGenerationError(RuntimeError):
    """The caller attempted to commit against an old domain generation."""


class DomainStore:
    """Worker-owned immutable snapshots in the shared jobs database."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.path = JobQueue(self.root).path

    def _connection(self) -> sqlite3.Connection:
        return _connect(self.path)

    def current_generation_id(self) -> str | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT value FROM domain_meta WHERE key='current_generation_id'"
            ).fetchone()
        return str(row["value"]) if row else None

    def commit(
        self,
        updates: dict[EntityKey, dict | None],
        *,
        reason: str,
        actor: str = "worker",
        expected_generation_id: str | None = None,
        dataset_state: dict[str, dict] | None = None,
    ) -> DomainSnapshot:
        """Commit a complete immutable generation with optimistic fencing."""
        generation_id = uuid.uuid4().hex
        now = _utc_now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current_row = connection.execute(
                "SELECT value FROM domain_meta WHERE key='current_generation_id'"
            ).fetchone()
            current = str(current_row["value"]) if current_row else None
            if expected_generation_id is not None and current != expected_generation_id:
                connection.execute("ROLLBACK")
                raise StaleGenerationError(
                    f"expected generation {expected_generation_id}, found {current}"
                )
            if dataset_state is None and current is not None:
                state_row = connection.execute(
                    """
                    SELECT dataset_state_json FROM generations
                    WHERE generation_id=?
                    """,
                    (current,),
                ).fetchone()
                dataset_state = json.loads(state_row["dataset_state_json"])
            dataset_state = dataset_state or {}
            connection.execute(
                """
                INSERT INTO generations(
                    generation_id, parent_generation_id, state,
                    reason, actor, created_at, dataset_state_json
                ) VALUES (?, ?, 'building', ?, ?, ?, ?)
                """,
                (
                    generation_id,
                    current,
                    reason,
                    actor,
                    now,
                    json.dumps(dataset_state, sort_keys=True, separators=(",", ":")),
                ),
            )
            if current is not None:
                connection.execute(
                    """
                    INSERT INTO generation_entities(
                        generation_id, entity_type, entity_key, version_id
                    )
                    SELECT ?, entity_type, entity_key, version_id
                    FROM generation_entities WHERE generation_id=?
                    """,
                    (generation_id, current),
                )
            for (entity_type, entity_key), payload in sorted(updates.items()):
                if payload is None:
                    connection.execute(
                        """
                        DELETE FROM generation_entities
                        WHERE generation_id=? AND entity_type=? AND entity_key=?
                        """,
                        (generation_id, entity_type, entity_key),
                    )
                    event_type = "entity_deleted"
                    details = {}
                else:
                    payload_json, payload_sha = _canonical(payload)
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO entity_versions(
                            entity_type, entity_key, payload_sha256,
                            payload_json, created_at
                        ) VALUES (?, ?, ?, ?, ?)
                        """,
                        (entity_type, entity_key, payload_sha, payload_json, now),
                    )
                    version = connection.execute(
                        """
                        SELECT id FROM entity_versions
                        WHERE entity_type=? AND entity_key=? AND payload_sha256=?
                        """,
                        (entity_type, entity_key, payload_sha),
                    ).fetchone()
                    connection.execute(
                        """
                        INSERT INTO generation_entities(
                            generation_id, entity_type, entity_key, version_id
                        ) VALUES (?, ?, ?, ?)
                        ON CONFLICT(generation_id, entity_type, entity_key)
                        DO UPDATE SET version_id=excluded.version_id
                        """,
                        (generation_id, entity_type, entity_key, version["id"]),
                    )
                    event_type = "entity_upserted"
                    details = {"payload_sha256": payload_sha}
                connection.execute(
                    """
                    INSERT INTO domain_events(
                        generation_id, event_type, entity_type, entity_key,
                        actor, occurred_at, details_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        generation_id,
                        event_type,
                        entity_type,
                        entity_key,
                        actor,
                        now,
                        json.dumps(details, separators=(",", ":")),
                    ),
                )
            connection.execute(
                """
                UPDATE generations SET state='committed', committed_at=?
                WHERE generation_id=? AND state='building'
                """,
                (now, generation_id),
            )
            connection.execute(
                """
                INSERT INTO domain_meta(key, value)
                VALUES ('current_generation_id', ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
                """,
                (generation_id,),
            )
            connection.execute(
                """
                INSERT INTO domain_events(
                    generation_id, event_type, actor, occurred_at, details_json
                ) VALUES (?, 'generation_committed', ?, ?, ?)
                """,
                (
                    generation_id,
                    actor,
                    now,
                    json.dumps({"reason": reason}, separators=(",", ":")),
                ),
            )
            connection.execute("COMMIT")
        return self.snapshot(generation_id)

    def snapshot(self, generation_id: str | None = None) -> DomainSnapshot:
        selected = generation_id or self.current_generation_id()
        if selected is None:
            return self.commit({}, reason="initialize domain store")
        with self._connection() as connection:
            generation = connection.execute(
                """
                SELECT generation_id, parent_generation_id, committed_at,
                    dataset_state_json
                FROM generations
                WHERE generation_id=? AND state='committed'
                """,
                (selected,),
            ).fetchone()
            if generation is None:
                raise KeyError(f"unknown committed generation: {selected}")
            rows = connection.execute(
                """
                SELECT ge.entity_type, ge.entity_key, ev.payload_json
                FROM generation_entities ge
                JOIN entity_versions ev ON ev.id=ge.version_id
                WHERE ge.generation_id=?
                ORDER BY ge.entity_type, ge.entity_key
                """,
                (selected,),
            ).fetchall()
        entities = {
            (str(row["entity_type"]), str(row["entity_key"])): json.loads(
                row["payload_json"]
            )
            for row in rows
        }
        return DomainSnapshot(
            generation_id=str(generation["generation_id"]),
            parent_generation_id=(
                str(generation["parent_generation_id"])
                if generation["parent_generation_id"]
                else None
            ),
            committed_at=str(generation["committed_at"]),
            dataset_state=json.loads(generation["dataset_state_json"]),
            entities=entities,
        )

    def patch(
        self,
        entity_type: str,
        entity_key: str,
        updates: dict,
        *,
        reason: str,
        actor: str = "worker",
        expected_generation_id: str | None = None,
    ) -> DomainSnapshot:
        current = self.snapshot()
        payload = current.get(entity_type, entity_key) or {}
        payload.update(updates)
        return self.commit(
            {(entity_type, entity_key): payload},
            reason=reason,
            actor=actor,
            expected_generation_id=expected_generation_id or current.generation_id,
        )

    def replace(
        self,
        entity_type: str,
        rows: list[dict],
        *,
        key_field: str,
        reason: str,
        actor: str = "worker",
        expected_generation_id: str | None = None,
    ) -> DomainSnapshot:
        current = self.snapshot()
        replacements: dict[str, dict] = {}
        for row in rows:
            key = entity_key(entity_type, row, key_field)
            if key in replacements:
                raise ValueError(f"duplicate {entity_type} key: {key}")
            replacements[key] = dict(row)
        old_keys = {
            key for (row_type, key) in current.entities if row_type == entity_type
        }
        updates: dict[EntityKey, dict | None] = {
            (entity_type, key): None for key in old_keys - replacements.keys()
        }
        updates.update(
            {(entity_type, key): payload for key, payload in replacements.items()}
        )
        return self.commit(
            updates,
            reason=reason,
            actor=actor,
            expected_generation_id=expected_generation_id or current.generation_id,
        )

    def append_audit(
        self,
        stream: str,
        payload: dict,
        *,
        event_key: str | None = None,
        generation_id: str | None = None,
    ) -> bool:
        if stream not in {"classifications", "outcomes"}:
            raise ValueError(f"unknown audit stream: {stream}")
        selected = generation_id or self.current_generation_id()
        with self._connection() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO audit_records(
                    stream, event_key, occurred_at, payload_json, generation_id
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    stream,
                    event_key,
                    _utc_now(),
                    json.dumps(
                        payload,
                        ensure_ascii=True,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    selected,
                ),
            )
        return bool(cursor.rowcount)

    def audit_records(self, stream: str) -> list[dict]:
        if stream not in {"classifications", "outcomes"}:
            raise ValueError(f"unknown audit stream: {stream}")
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT payload_json FROM audit_records
                WHERE stream=? ORDER BY seq
                """,
                (stream,),
            ).fetchall()
        return [json.loads(row["payload_json"]) for row in rows]

    def export_jsonl(
        self,
        destination: Path,
        *,
        generation_id: str | None = None,
        filenames: dict[str, str] | None = None,
    ) -> DomainSnapshot:
        snapshot = self.snapshot(generation_id)
        destination.mkdir(parents=True, exist_ok=True)
        final = destination / snapshot.generation_id
        if final.is_dir():
            return snapshot
        temporary = destination / (
            f".export-{snapshot.generation_id}-{uuid.uuid4().hex}"
        )
        temporary.mkdir()
        names = filenames or {}
        entity_types = sorted({entity_type for entity_type, _key in snapshot.entities})
        for entity_type in entity_types:
            path = temporary / names.get(entity_type, f"{entity_type}.jsonl")
            path.write_text(
                "".join(
                    json.dumps(row, ensure_ascii=True, sort_keys=True) + "\n"
                    for row in snapshot.rows(entity_type)
                ),
                encoding="utf-8",
            )
        manifest = temporary / "generation.json"
        manifest.write_text(
            json.dumps(
                {
                    "generation_id": snapshot.generation_id,
                    "parent_generation_id": snapshot.parent_generation_id,
                    "committed_at": snapshot.committed_at,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        for path in temporary.iterdir():
            if path.is_file():
                with path.open("rb") as handle:
                    os.fsync(handle.fileno())
        descriptor = os.open(temporary, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(temporary, final)
        pointer = destination / "current"
        pointer_tmp = destination / f".current-{uuid.uuid4().hex}"
        pointer_tmp.symlink_to(snapshot.generation_id, target_is_directory=True)
        os.replace(pointer_tmp, pointer)
        descriptor = os.open(destination, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        return snapshot
