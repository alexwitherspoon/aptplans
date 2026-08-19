"""Store hashed preservation copies. Filenames are the digest, not the airport."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path


@dataclass(frozen=True)
class StoredFile:
    sha256: str
    path: Path
    size: int


def store_bytes(data: bytes, dest_dir: Path, suffix: str = ".pdf") -> StoredFile:
    dest_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(data).hexdigest()
    path = dest_dir / f"{digest}{suffix}"
    if not path.exists():
        path.write_bytes(data)
    elif path.read_bytes() != data:
        raise ValueError(f"hash collision or corrupt file at {path}")
    return StoredFile(sha256=digest, path=path, size=len(data))
