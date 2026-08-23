"""Origin health snapshot for the private review API. Disk only. No docker.sock."""

from __future__ import annotations

from pathlib import Path
import os

from pipeline.health import system_health


def queue_dir_from_env(override: Path | None = None) -> Path:
    if override is not None:
        return override
    raw = os.environ.get("APTPLANS_QUEUE", "").strip()
    if raw:
        return Path(raw)
    overlay = os.environ.get("APTPLANS_CATALOG_OVERLAY", "").strip()
    if overlay:
        return Path(overlay).parent / "queue"
    return Path(__file__).resolve().parents[1] / "data" / "queue"


def logs_dir_from_env(override: Path | None = None) -> Path:
    from pipeline.service_log import logs_dir_from_env as _logs_dir_from_env

    return _logs_dir_from_env(override)


def system_status(
    overlay_dir: Path,
    *,
    queue_dir: Path | None = None,
    reject_dir: Path | None = None,
    logs_dir: Path | None = None,
) -> dict:
    """Datasets, services, and pipeline metrics for GET /v1/status."""
    from pipeline.reject import reject_dir as reject_dir_from_env
    from pipeline.service_log import logs_dir_from_env as default_logs_dir

    return system_health(
        overlay_dir,
        queue_dir=queue_dir or queue_dir_from_env(),
        reject_dir=reject_dir or reject_dir_from_env(),
        logs_dir=logs_dir or default_logs_dir(),
    )


def service_logs(
    overlay_dir: Path,
    *,
    logs_dir: Path | None = None,
    n: int = 100,
) -> dict:
    from pipeline.outcomes import compact_outcome, load_outcomes
    from pipeline.service_log import DEFAULT_TAIL, MAX_TAIL, logs_dir_from_env, tail_jsonl, worker_log_path

    count = max(1, min(int(n), MAX_TAIL))
    logs = logs_dir or logs_dir_from_env()
    outcomes = [compact_outcome(row) for row in load_outcomes(overlay_dir)[-count:]]
    worker = tail_jsonl(worker_log_path(logs), count)
    return {
        "n": count,
        "worker": worker,
        "outcomes": outcomes,
    }
