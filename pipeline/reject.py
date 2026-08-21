"""Private 90-day store for artifacts that failed a check. Not a publish.

Bytes live next to the public file store, never under the Caddy /files/ mount.
The review API lists metadata and can send bytes over HTTPS with an API key.
After retention they are deleted. SSI-looking files stay off the public vhost.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import hashlib
import json
import os

from pipeline.files import store_bytes
from pipeline.gates import MAX_BYTES, filename_from_url, sniff_media

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = "reject.jsonl"
RETENTION_DAYS = 90
TRAIN_REASONS = frozenset({"not_plan", "not_file", "ssi"})
PUBLIC_KEYS = (
    "at",
    "expires_at",
    "sha256",
    "suffix",
    "bytes",
    "stored",
    "reason",
    "url",
    "label",
    "lid",
    "state",
    "job_id",
    "job_kind",
    "http_status",
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_time(raw: str | None) -> datetime | None:
    text = (raw or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def retention_days() -> int:
    raw = os.environ.get("APTPLANS_REJECT_DAYS", "").strip()
    if not raw:
        return RETENTION_DAYS
    return max(1, int(raw))


def reject_dir(files_dir: Path | None = None) -> Path:
    raw = os.environ.get("APTPLANS_REJECT", "").strip()
    if raw:
        return Path(raw)
    if files_dir is not None:
        return files_dir.parent / "reject"
    overlay = os.environ.get("APTPLANS_FILES", "").strip()
    if overlay:
        return Path(overlay).parent / "reject"
    return ROOT / "data" / "reject"


def manifest_path(dest: Path) -> Path:
    return dest / MANIFEST


def reject_file(dest: Path, sha256: str, suffix: str) -> Path:
    return dest / f"{sha256}{suffix}"


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def suffix_for(data: bytes, url: str = "") -> str:
    media = sniff_media(data) if data else ""
    if media == "pdf":
        return ".pdf"
    if media == "html":
        return ".html"
    name = filename_from_url(url).lower()
    if name.endswith(".pdf"):
        return ".pdf"
    if name.endswith(".html") or name.endswith(".htm") or name.endswith(".aspx"):
        return ".html"
    return ".bin"


def compact_reject(row: dict) -> dict:
    out = {}
    for key in PUBLIC_KEYS:
        value = row.get(key)
        if value in (None, ""):
            continue
        out[key] = value
    return out


def load_rejects(dest: Path | None = None, files_dir: Path | None = None) -> list[dict]:
    root = dest or reject_dir(files_dir)
    path = manifest_path(root)
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


def live_rejects(
    dest: Path | None = None,
    files_dir: Path | None = None,
    *,
    now: datetime | None = None,
) -> list[dict]:
    moment = now or utc_now()
    live = []
    for row in load_rejects(dest, files_dir):
        expires = parse_time(row.get("expires_at"))
        if expires is not None and expires <= moment:
            continue
        live.append(row)
    return live


def get_reject(
    sha256: str,
    dest: Path | None = None,
    files_dir: Path | None = None,
    *,
    now: datetime | None = None,
) -> dict | None:
    digest = (sha256 or "").strip().lower()
    for row in live_rejects(dest, files_dir, now=now):
        if (row.get("sha256") or "").lower() == digest:
            return row
    return None


def read_reject_bytes(
    sha256: str,
    dest: Path | None = None,
    files_dir: Path | None = None,
    *,
    now: datetime | None = None,
) -> tuple[dict, bytes] | None:
    row = get_reject(sha256, dest, files_dir, now=now)
    if row is None or not row.get("stored"):
        return None
    root = dest or reject_dir(files_dir)
    path = reject_file(root, row["sha256"], row.get("suffix") or ".bin")
    if not path.is_file():
        return None
    return row, path.read_bytes()


def training_case(row: dict, *, source: str | None = None) -> dict | None:
    """Gold-shaped packet for local scoring. No excerpts. Body comes from source bytes."""
    reason = row.get("reason") or ""
    url = row.get("url") or ""
    if reason not in TRAIN_REASONS or not url.startswith("http"):
        return None
    if not row.get("stored"):
        return None
    sha = row.get("sha256") or ""
    suffix = row.get("suffix") or ".bin"
    case = {
        "id": f"reject-{sha[:12]}" if sha else None,
        "lid": row.get("lid") or "",
        "name": "",
        "state": row.get("state") or "",
        "url": url,
        "label": row.get("label") or filename_from_url(url),
        "gold": {
            "same_airport": True,
            "kind": "not_plan",
            "confirm": False,
            "explore": False,
            "publish": False,
        },
        "from_outcome": row.get("at"),
        "reject_reason": reason,
        "reject_sha256": sha,
        "reject_expires_at": row.get("expires_at"),
    }
    if source:
        case["source"] = source
    elif sha:
        case["source"] = f"data/score/review/rejects/{sha}{suffix}"
    return case


def store_reject(
    *,
    reason: str,
    url: str = "",
    data: bytes = b"",
    lid: str = "",
    state: str = "",
    job_id: str = "",
    job_kind: str = "",
    http_status: int | None = None,
    files_dir: Path | None = None,
    dest: Path | None = None,
    now: datetime | None = None,
) -> dict:
    """Keep a failed artifact for analysis. Does not publish. Does not index search."""
    moment = now or utc_now()
    root = dest or reject_dir(files_dir)
    root.mkdir(parents=True, exist_ok=True)
    payload = data or b""
    too_large = len(payload) > MAX_BYTES
    store_bytes_ok = bool(payload) and not too_large
    digest = hashlib.sha256(payload).hexdigest() if store_bytes_ok else ""
    suffix = suffix_for(payload, url) if store_bytes_ok else ""
    if digest:
        existing = get_reject(digest, root, now=moment)
        if existing is not None:
            return existing
        store_bytes(payload, root, suffix=suffix)
    row = {
        "at": _iso(moment),
        "expires_at": _iso(moment + timedelta(days=retention_days())),
        "sha256": digest,
        "suffix": suffix,
        "bytes": len(payload),
        "stored": store_bytes_ok,
        "reason": reason,
        "url": url,
        "label": filename_from_url(url),
        "lid": lid,
        "state": state,
        "job_id": job_id,
        "job_kind": job_kind,
        "http_status": http_status,
    }
    with manifest_path(root).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, default=str) + "\n")
    return row


def purge_expired(
    dest: Path | None = None,
    files_dir: Path | None = None,
    *,
    now: datetime | None = None,
) -> dict:
    """Delete expired reject bytes and rewrite the manifest. Safe to run often."""
    root = dest or reject_dir(files_dir)
    moment = now or utc_now()
    rows = load_rejects(root)
    keep = []
    dropped = 0
    for row in rows:
        expires = parse_time(row.get("expires_at"))
        if expires is not None and expires <= moment:
            dropped += 1
            continue
        keep.append(row)
    live_names = {
        f"{row.get('sha256')}{row.get('suffix') or '.bin'}"
        for row in keep
        if row.get("stored") and row.get("sha256")
    }
    removed_files = 0
    if root.is_dir():
        for path in root.iterdir():
            if path.name == MANIFEST or not path.is_file():
                continue
            if path.name not in live_names:
                try:
                    path.unlink()
                    removed_files += 1
                except OSError:
                    continue
    path = manifest_path(root)
    if keep:
        path.write_text(
            "".join(json.dumps(row, default=str) + "\n" for row in keep),
            encoding="utf-8",
        )
    elif path.is_file():
        path.unlink()
    return {"kept": len(keep), "dropped": dropped, "removed_files": removed_files}


def main() -> int:
    summary = purge_expired()
    print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
