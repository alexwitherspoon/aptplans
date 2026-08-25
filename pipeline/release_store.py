"""Versioned static releases with validation and atomic served-pointer swaps."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
from typing import Callable
import uuid

from pipeline.domain_store import DomainStore
from pipeline.queue import _connect, _utc_now


BuildRelease = Callable[[Path, Path], None]
DEFAULT_REQUIRED_SITE_PATHS = (
    "index.html",
    "status.json",
    "sitemap.xml",
    "data/search.json",
)


def _release_event(
    connection: sqlite3.Connection,
    generation_id: str,
    event_type: str,
    details: dict | None = None,
) -> None:
    connection.execute(
        """
        INSERT INTO release_events(
            generation_id, event_type, occurred_at, details_json
        ) VALUES (?, ?, ?, ?)
        """,
        (
            generation_id,
            event_type,
            _utc_now(),
            json.dumps(details or {}, ensure_ascii=True, separators=(",", ":")),
        ),
    )


def _inventory(root: Path) -> list[dict]:
    rows: list[dict] = []
    if not root.is_dir():
        return rows
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        if path.is_symlink():
            raise ValueError(f"release file must not be a symlink: {path}")
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        rows.append(
            {
                "path": path.relative_to(root).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": digest.hexdigest(),
            }
        )
    return rows


def _fsync_tree(root: Path) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_file():
            with path.open("rb") as handle:
                os.fsync(handle.fileno())
        elif path.is_dir():
            descriptor = os.open(path, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    descriptor = os.open(root, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class ReleaseStore:
    """Stages immutable site/files trees and swaps one relative symlink."""

    def __init__(
        self,
        ledger_root: Path,
        releases_root: Path,
        *,
        required_site_paths: tuple[str, ...] = DEFAULT_REQUIRED_SITE_PATHS,
    ) -> None:
        self.domain = DomainStore(ledger_root)
        self.path = self.domain.path
        self.root = Path(releases_root)
        self.required_site_paths = required_site_paths
        self.root.mkdir(parents=True, exist_ok=True)

    def _connection(self) -> sqlite3.Connection:
        return _connect(self.path)

    def _manifest(
        self,
        generation_id: str,
        release_dir: Path,
        *,
        metadata: dict | None = None,
    ) -> dict:
        site = release_dir / "site"
        public_files = release_dir / "public-files"
        for relative in self.required_site_paths:
            if not (site / relative).is_file():
                raise ValueError(f"release is missing site/{relative}")
        return {
            "generation_id": generation_id,
            "created_at": _utc_now(),
            "metadata": metadata or {},
            "site": _inventory(site),
            "public_files": _inventory(public_files),
        }

    def _validate_existing(
        self,
        generation_id: str,
        *,
        expected_sha256: str | None = None,
    ) -> dict:
        release_dir = self.root / generation_id
        saved = json.loads(
            (release_dir / "manifest.json").read_text(encoding="utf-8")
        )
        current = self._manifest(generation_id, release_dir)
        if (
            saved.get("generation_id") != generation_id
            or saved.get("site") != current["site"]
            or saved.get("public_files") != current["public_files"]
        ):
            raise ValueError("release manifest does not match files")
        encoded = json.dumps(
            saved,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        actual_sha = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        if expected_sha256 and actual_sha != expected_sha256:
            raise ValueError("release manifest checksum mismatch")
        return saved

    def stage(
        self,
        generation_id: str,
        build: BuildRelease,
        *,
        metadata: dict | None = None,
    ) -> dict:
        self.domain.snapshot(generation_id)
        existing = self.get(generation_id)
        final = self.root / generation_id
        if existing and existing["state"] in {
            "validated",
            "activating",
            "active",
            "superseded",
        }:
            return self._validate_existing(
                generation_id,
                expected_sha256=existing["manifest_sha256"],
            )
        if existing and existing["state"] == "failed" and final.exists():
            shutil.rmtree(final)
        now = _utc_now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO releases(generation_id, state, created_at)
                VALUES (?, 'building', ?)
                ON CONFLICT(generation_id) DO UPDATE SET
                    state='building', error=NULL
                """,
                (generation_id, now),
            )
            _release_event(connection, generation_id, "build_started")
            connection.execute("COMMIT")

        temporary = self.root / f".building-{generation_id}-{uuid.uuid4().hex}"
        temporary.mkdir(parents=False)
        (temporary / "site").mkdir()
        (temporary / "public-files").mkdir()
        try:
            build(temporary / "site", temporary / "public-files")
            manifest = self._manifest(
                generation_id,
                temporary,
                metadata=metadata,
            )
            encoded = json.dumps(
                manifest,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            )
            manifest_sha = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
            (temporary / "manifest.json").write_text(
                json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            _fsync_tree(temporary)
            if final.exists():
                shutil.rmtree(temporary)
                manifest = self._validate_existing(generation_id)
                encoded = json.dumps(
                    manifest,
                    ensure_ascii=True,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                manifest_sha = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
            else:
                os.replace(temporary, final)
                descriptor = os.open(self.root, os.O_RDONLY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
            with self._connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    """
                    UPDATE releases SET state='validated', manifest_sha256=?,
                        manifest_json=?, validated_at=?, error=NULL
                    WHERE generation_id=?
                    """,
                    (manifest_sha, encoded, _utc_now(), generation_id),
                )
                _release_event(
                    connection,
                    generation_id,
                    "build_validated",
                    {"manifest_sha256": manifest_sha},
                )
                connection.execute("COMMIT")
            return manifest
        except Exception as exc:
            if temporary.exists():
                shutil.rmtree(temporary)
            with self._connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    """
                    UPDATE releases SET state='failed', error=?
                    WHERE generation_id=?
                    """,
                    (f"{type(exc).__name__}: {exc}", generation_id),
                )
                _release_event(
                    connection,
                    generation_id,
                    "build_failed",
                    {"error": f"{type(exc).__name__}: {exc}"},
                )
                connection.execute("COMMIT")
            raise

    def activate(self, generation_id: str) -> None:
        release = self.get(generation_id)
        if release is None or release["state"] not in {
            "validated",
            "activating",
            "active",
        }:
            raise ValueError(f"release is not validated: {generation_id}")
        if not (self.root / generation_id / "manifest.json").is_file():
            raise ValueError(f"release files are missing: {generation_id}")
        self._validate_existing(
            generation_id,
            expected_sha256=release["manifest_sha256"],
        )
        if (
            release["state"] == "active"
            and self.current_generation_id() == generation_id
        ):
            return
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE releases SET state='activating'
                WHERE generation_id=? AND state='validated'
                """,
                (generation_id,),
            )
            _release_event(connection, generation_id, "activation_started")
            connection.execute("COMMIT")

        pointer = self.root / "current"
        temporary = self.root / f".current-{uuid.uuid4().hex}"
        temporary.symlink_to(generation_id, target_is_directory=True)
        os.replace(temporary, pointer)
        descriptor = os.open(self.root, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE releases SET state='superseded'
                WHERE state='active' AND generation_id<>?
                """,
                (generation_id,),
            )
            connection.execute(
                """
                UPDATE releases SET state='active', activated_at=?, error=NULL
                WHERE generation_id=?
                """,
                (_utc_now(), generation_id),
            )
            _release_event(connection, generation_id, "activated")
            connection.execute("COMMIT")

    def record_search_staged(
        self,
        generation_id: str,
        index_uid: str,
        document_count: int,
    ) -> None:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                UPDATE releases SET search_index_uid=?, search_state='staged',
                    search_document_count=?, error=NULL
                WHERE generation_id=? AND state IN ('validated', 'active')
                """,
                (index_uid, document_count, generation_id),
            )
            if cursor.rowcount != 1:
                connection.execute("ROLLBACK")
                raise ValueError(f"release is not ready for search: {generation_id}")
            _release_event(
                connection,
                generation_id,
                "search_staged",
                {"index_uid": index_uid, "documents": document_count},
            )
            connection.execute("COMMIT")

    def invalidate(self, generation_id: str, error: str) -> None:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE releases SET state='failed', error=?
                WHERE generation_id=? AND state<>'active'
                """,
                (error, generation_id),
            )
            _release_event(
                connection,
                generation_id,
                "validation_failed",
                {"error": error},
            )
            connection.execute("COMMIT")

    def record_search_active(self, generation_id: str) -> None:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                UPDATE releases SET search_state='active', error=NULL
                WHERE generation_id=? AND state='active'
                  AND search_state IN ('staged', 'active')
                """,
                (generation_id,),
            )
            if cursor.rowcount != 1:
                connection.execute("ROLLBACK")
                raise ValueError(f"release search cannot activate: {generation_id}")
            _release_event(connection, generation_id, "search_activated")
            connection.execute("COMMIT")

    def recover_activation(self) -> str | None:
        generation_id = self.current_generation_id()
        if generation_id is None:
            return None
        release = self.get(generation_id)
        if release is None or release["state"] not in {
            "validated",
            "activating",
            "active",
        }:
            raise RuntimeError(
                f"served pointer references unusable release: {generation_id}"
            )
        if release["state"] != "active":
            with self._connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    """
                    UPDATE releases SET state='superseded'
                    WHERE state='active' AND generation_id<>?
                    """,
                    (generation_id,),
                )
                connection.execute(
                    """
                    UPDATE releases SET state='active', activated_at=?
                    WHERE generation_id=?
                    """,
                    (_utc_now(), generation_id),
                )
                _release_event(connection, generation_id, "activation_recovered")
                connection.execute("COMMIT")
        return generation_id

    def current_generation_id(self) -> str | None:
        pointer = self.root / "current"
        if not pointer.is_symlink():
            return None
        target = os.readlink(pointer)
        if "/" in target or target.startswith("."):
            raise RuntimeError(f"unsafe release pointer: {target}")
        return target

    def get(self, generation_id: str) -> dict | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM releases WHERE generation_id=?",
                (generation_id,),
            ).fetchone()
        return dict(row) if row else None

    def events(self, generation_id: str) -> list[str]:
        with self._connection() as connection:
            return [
                str(row["event_type"])
                for row in connection.execute(
                    """
                    SELECT event_type FROM release_events
                    WHERE generation_id=? ORDER BY seq
                    """,
                    (generation_id,),
                )
            ]
