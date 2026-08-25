"""Gated native PDF text on origin disk. Not served by Caddy."""

from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAX_PAGE_CHARS = 8000


def text_dir(files_dir: Path | None = None) -> Path:
    raw = os.environ.get("APTPLANS_TEXT", "").strip()
    if raw:
        return Path(raw)
    if files_dir is not None:
        return files_dir.parent / "text"
    files = os.environ.get("APTPLANS_FILES", "").strip()
    if files:
        return Path(files).parent / "text"
    return ROOT / "data" / "text"


def pages_path(dest: Path, content_sha256: str) -> Path:
    return dest / f"{content_sha256}.jsonl"


def projection_path(dest: Path, content_sha256: str) -> Path:
    return dest / f"{content_sha256}.projection.json"


def _atomic_write(path: Path, payload: str) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write_pages(
    dest: Path,
    content_sha256: str,
    pages: list[str],
    *,
    manifest_key: str | None = None,
    manifest_sha256: str | None = None,
) -> list[dict]:
    """Store non-empty pages as JSONL. Page numbers stay 1-based from the PDF."""
    dest.mkdir(parents=True, exist_ok=True)
    rows = []
    for index, raw in enumerate(pages, start=1):
        text = " ".join((raw or "").split())
        if not text:
            continue
        rows.append({"page": index, "text": text[:MAX_PAGE_CHARS]})
    path = pages_path(dest, content_sha256)
    _atomic_write(
        path,
        "".join(json.dumps(row, ensure_ascii=True) + "\n" for row in rows),
    )
    if manifest_key and manifest_sha256:
        _atomic_write(
            projection_path(dest, content_sha256),
            json.dumps(
                {
                    "manifest_key": manifest_key,
                    "manifest_sha256": manifest_sha256,
                },
                ensure_ascii=True,
                sort_keys=True,
            )
            + "\n",
        )
    return rows


def projection_matches(
    dest: Path,
    content_sha256: str,
    *,
    manifest_key: str,
    manifest_sha256: str,
) -> bool:
    if not pages_path(dest, content_sha256).is_file():
        return False
    try:
        payload = json.loads(
            projection_path(dest, content_sha256).read_text(encoding="utf-8")
        )
        read_pages(dest, content_sha256)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    return payload == {
        "manifest_key": manifest_key,
        "manifest_sha256": manifest_sha256,
    }


def read_pages(dest: Path, content_sha256: str) -> list[dict]:
    path = pages_path(dest, content_sha256)
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        if isinstance(row, dict) and row.get("text"):
            rows.append(row)
    return rows
