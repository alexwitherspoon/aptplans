"""Offline-safe maintenance commands for the job and control ledgers."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3

from pipeline.queue import ControlQueue, JobQueue
from pipeline.status import queue_dir_from_env


def _control_root(root: Path) -> Path:
    return Path(os.environ.get("APTPLANS_CONTROL_QUEUE") or root)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _check_file(path: Path) -> str:
    connection = sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True)
    try:
        result = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
        if result != "ok":
            return result
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            return "foreign_key_error"
        return "ok"
    finally:
        connection.close()


def _copy_indexed_extractions(
    jobs: Path,
    source_root: Path,
    destination: Path,
) -> dict[str, str]:
    checksums: dict[str, str] = {}
    connection = sqlite3.connect(
        f"file:{jobs}?mode=ro&immutable=1", uri=True
    )
    try:
        rows = connection.execute(
            "SELECT manifest_path, manifest_sha256 FROM extraction_manifests"
        ).fetchall()
    finally:
        connection.close()
    for raw_path, expected_sha256 in rows:
        relative = Path(str(raw_path))
        if relative.is_absolute() or ".." in relative.parts:
            raise RuntimeError("invalid extraction manifest path")
        source = source_root / relative
        if not source.is_file() or _sha256(source) != str(expected_sha256):
            raise RuntimeError(
                f"indexed extraction manifest is missing or corrupt: {relative}"
            )
        target = destination / "extractions" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        key = (Path("extractions") / relative).as_posix()
        checksums[key] = str(expected_sha256)
    return checksums


def integrity(root: Path) -> dict[str, str]:
    return {
        "jobs": JobQueue(root).integrity_check(),
        "control": ControlQueue(root).integrity_check(),
    }


def backup(
    root: Path,
    destination: Path,
    *,
    extraction_root: Path | None = None,
) -> dict[str, str]:
    from pipeline.extraction_store import extraction_dir

    destination.mkdir(parents=True, exist_ok=True)
    if (destination / "extractions").exists():
        shutil.rmtree(destination / "extractions")
    jobs = JobQueue(root).backup(destination / "jobs.sqlite3")
    control = ControlQueue(root).backup(destination / "control.sqlite3")
    result = {"jobs": _check_file(jobs), "control": _check_file(control)}
    if set(result.values()) != {"ok"}:
        raise RuntimeError(f"backup integrity failed: {result}")
    manifest = {
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "schema": 2,
        "integrity": result,
        "sha256": {
            "jobs.sqlite3": _sha256(jobs),
            "control.sqlite3": _sha256(control),
        },
    }
    manifest["sha256"].update(
        _copy_indexed_extractions(
            jobs,
            extraction_root or extraction_dir(),
            destination,
        )
    )
    (destination / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n",
        encoding="utf-8",
    )
    return result


def restore(
    root: Path,
    source: Path,
    *,
    confirmed_offline: bool,
    extraction_root: Path | None = None,
) -> dict[str, str]:
    from pipeline.extraction_store import extraction_dir

    if not confirmed_offline:
        raise ValueError("restore requires --confirm-offline")
    source_jobs = source / "jobs.sqlite3"
    source_control = source / "control.sqlite3"
    manifest_path = source / "manifest.json"
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        expected = manifest.get("sha256") or {}
        if any(
            Path(name).is_absolute() or ".." in Path(name).parts
            for name in expected
        ):
            raise RuntimeError("restore manifest contains an invalid path")
        actual = {
            name: _sha256(source / name)
            for name in expected
        }
        if expected and actual != expected:
            raise RuntimeError("restore source checksum mismatch")
    checks = {
        "jobs": _check_file(source_jobs),
        "control": _check_file(source_control),
    }
    if set(checks.values()) != {"ok"}:
        raise RuntimeError(f"restore source integrity failed: {checks}")
    source_extractions = source / "extractions"
    if source_extractions.is_dir():
        destination_extractions = extraction_root or extraction_dir()
        temporary = destination_extractions.with_name(
            f".{destination_extractions.name}.restore"
        )
        if temporary.exists():
            shutil.rmtree(temporary)
        shutil.copytree(source_extractions, temporary)
        if destination_extractions.exists():
            shutil.rmtree(destination_extractions)
        os.replace(temporary, destination_extractions)
    destinations = (
        (root, "jobs.sqlite3", source_jobs),
        (_control_root(root), "control.sqlite3", source_control),
    )
    for destination_root, name, source_path in destinations:
        destination_root.mkdir(parents=True, exist_ok=True)
        destination = destination_root / name
        temporary = destination_root / f".{name}.restore"
        shutil.copy2(source_path, temporary)
        os.replace(temporary, destination)
        for suffix in ("-wal", "-shm"):
            sidecar = destination_root / f"{name}{suffix}"
            if sidecar.exists():
                sidecar.unlink()
    return integrity(root)


def reset(root: Path, *, confirmed_preproduction: bool) -> dict[str, str]:
    if not confirmed_preproduction:
        raise ValueError("reset requires --confirm-preproduction-reset")
    for directory, name in (
        (root, "jobs.sqlite3"),
        (_control_root(root), "control.sqlite3"),
    ):
        for suffix in ("", "-wal", "-shm"):
            path = directory / f"{name}{suffix}"
            if path.exists():
                path.unlink()
    return integrity(root)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queue-dir", type=Path)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("integrity")
    backup_parser = subparsers.add_parser("backup")
    backup_parser.add_argument("destination", type=Path)
    backup_parser.add_argument("--extraction-dir", type=Path)
    restore_parser = subparsers.add_parser("restore")
    restore_parser.add_argument("source", type=Path)
    restore_parser.add_argument("--confirm-offline", action="store_true")
    restore_parser.add_argument("--extraction-dir", type=Path)
    reset_parser = subparsers.add_parser("reset")
    reset_parser.add_argument("--confirm-preproduction-reset", action="store_true")
    args = parser.parse_args()
    root = queue_dir_from_env(args.queue_dir)
    if args.command == "integrity":
        result = integrity(root)
    elif args.command == "backup":
        result = backup(
            root,
            args.destination,
            extraction_root=args.extraction_dir,
        )
    elif args.command == "restore":
        result = restore(
            root,
            args.source,
            confirmed_offline=args.confirm_offline,
            extraction_root=args.extraction_dir,
        )
    else:
        result = reset(
            root,
            confirmed_preproduction=args.confirm_preproduction_reset,
        )
    print(json.dumps(result, sort_keys=True))
    return 0 if set(result.values()) == {"ok"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
