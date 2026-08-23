"""Job readiness gates backed by the overlay dataset catalog."""

from __future__ import annotations

from pathlib import Path

from pipeline.datasets import requirements_met
from pipeline.queue import JobQueue


def discovery_ready(
    overlay_dir: Path,
    queue: JobQueue | None = None,
) -> tuple[bool, str]:
    return requirements_met("discovery", overlay_dir, queue)


def grant_spend_ready(
    overlay_dir: Path,
    queue: JobQueue | None = None,
) -> tuple[bool, str]:
    return requirements_met("grant_spend", overlay_dir, queue)
