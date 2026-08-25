"""Offline-safe maintenance commands for the job and control ledgers."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import sqlite3

from pipeline.queue import ControlQueue, JobQueue
from pipeline.status import queue_dir_from_env


def _check_file(path: Path) -> str:
    connection = sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True)
    try:
        return str(connection.execute("PRAGMA integrity_check").fetchone()[0])
    finally:
        connection.close()


def integrity(root: Path) -> dict[str, str]:
    return {
        "jobs": JobQueue(root).integrity_check(),
        "control": ControlQueue(root).integrity_check(),
    }


def backup(root: Path, destination: Path) -> dict[str, str]:
    destination.mkdir(parents=True, exist_ok=True)
    jobs = JobQueue(root).backup(destination / "jobs.sqlite3")
    control = ControlQueue(root).backup(destination / "control.sqlite3")
    result = {"jobs": _check_file(jobs), "control": _check_file(control)}
    if set(result.values()) != {"ok"}:
        raise RuntimeError(f"backup integrity failed: {result}")
    manifest = {
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "schema": 1,
        "integrity": result,
    }
    (destination / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n",
        encoding="utf-8",
    )
    return result


def restore(root: Path, source: Path, *, confirmed_offline: bool) -> dict[str, str]:
    if not confirmed_offline:
        raise ValueError("restore requires --confirm-offline")
    source_jobs = source / "jobs.sqlite3"
    source_control = source / "control.sqlite3"
    checks = {
        "jobs": _check_file(source_jobs),
        "control": _check_file(source_control),
    }
    if set(checks.values()) != {"ok"}:
        raise RuntimeError(f"restore source integrity failed: {checks}")
    root.mkdir(parents=True, exist_ok=True)
    for name, source_path in (
        ("jobs.sqlite3", source_jobs),
        ("control.sqlite3", source_control),
    ):
        destination = root / name
        temporary = root / f".{name}.restore"
        shutil.copy2(source_path, temporary)
        os.replace(temporary, destination)
        for suffix in ("-wal", "-shm"):
            sidecar = root / f"{name}{suffix}"
            if sidecar.exists():
                sidecar.unlink()
    return integrity(root)


def reset(root: Path, *, confirmed_preproduction: bool) -> dict[str, str]:
    if not confirmed_preproduction:
        raise ValueError("reset requires --confirm-preproduction-reset")
    for name in ("jobs.sqlite3", "control.sqlite3"):
        for suffix in ("", "-wal", "-shm"):
            path = root / f"{name}{suffix}"
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
    restore_parser = subparsers.add_parser("restore")
    restore_parser.add_argument("source", type=Path)
    restore_parser.add_argument("--confirm-offline", action="store_true")
    reset_parser = subparsers.add_parser("reset")
    reset_parser.add_argument("--confirm-preproduction-reset", action="store_true")
    args = parser.parse_args()
    root = queue_dir_from_env(args.queue_dir)
    if args.command == "integrity":
        result = integrity(root)
    elif args.command == "backup":
        result = backup(root, args.destination)
    elif args.command == "restore":
        result = restore(root, args.source, confirmed_offline=args.confirm_offline)
    else:
        result = reset(
            root,
            confirmed_preproduction=args.confirm_preproduction_reset,
        )
    print(json.dumps(result, sort_keys=True))
    return 0 if set(result.values()) == {"ok"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
