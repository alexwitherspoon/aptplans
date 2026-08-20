"""Exclusive lock so the worker loop, check, and refresh never overlap."""

from __future__ import annotations

from contextlib import contextmanager
import fcntl
import os
from pathlib import Path


@contextmanager
def worker_lock(queue_dir: Path):
    """Blocking flock. One document/check/refresh at a time on this host."""
    queue_dir.mkdir(parents=True, exist_ok=True)
    path = queue_dir / ".lock"
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
