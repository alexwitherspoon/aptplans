"""Shared overlay paths and staleness. FAA fetch lives in refresh_airports / refresh_grants."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

PACIFIC = ZoneInfo("America/Los_Angeles")
ROOT = Path(__file__).resolve().parents[1]
PAUSE_SECONDS = 2.0


def overlay_airports_path(overlay_dir: Path) -> Path:
    return overlay_dir / "airports.jsonl"


def overlay_grants_path(overlay_dir: Path) -> Path:
    return overlay_dir / "grants.jsonl"


def should_refresh(path: Path, now: datetime | None = None) -> bool:
    """True when missing, empty, or not written this calendar month (Pacific)."""
    clock = now or datetime.now(PACIFIC)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=PACIFIC)
    else:
        clock = clock.astimezone(PACIFIC)
    if not path.is_file() or path.stat().st_size == 0:
        return True
    if not any(line.strip() for line in path.read_text(encoding="utf-8").splitlines()):
        return True
    mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=PACIFIC)
    return (mtime.year, mtime.month) != (clock.year, clock.month)


def overlays_need_fetch(overlay_dir: Path, now: datetime | None = None) -> bool:
    return should_refresh(overlay_airports_path(overlay_dir), now) or should_refresh(
        overlay_grants_path(overlay_dir), now
    )
