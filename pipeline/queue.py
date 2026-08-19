"""On-disk serial queue. One JSON file per job."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
import uuid


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class QueueJob:
    kind: str
    document_id: str | None
    source_url: str | None
    airport_lid: str | None
    state: str | None = None
    issue_number: int | None = None
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    created_at: str = field(default_factory=_utc_now)
    report_type: str | None = None
    suggested_kind: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> QueueJob:
        return cls(
            kind=data["kind"],
            document_id=data.get("document_id"),
            source_url=data.get("source_url"),
            airport_lid=data.get("airport_lid"),
            state=data.get("state"),
            issue_number=data.get("issue_number"),
            id=data.get("id") or uuid.uuid4().hex[:12],
            created_at=data.get("created_at") or _utc_now(),
            report_type=data.get("report_type"),
            suggested_kind=data.get("suggested_kind"),
        )


class JobQueue:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.pending = root / "pending"
        self.active = root / "active"
        self.done = root / "done"
        self.pending.mkdir(parents=True, exist_ok=True)
        self.active.mkdir(parents=True, exist_ok=True)
        self.done.mkdir(parents=True, exist_ok=True)
        self._claimed: Path | None = None

    def _next_name(self) -> str:
        existing = [path.name for path in self.pending.glob("*.json")]
        existing += [path.name for path in self.active.glob("*.json")]
        existing += [path.name for path in self.done.glob("*.json")]
        numbers = []
        for name in existing:
            prefix = name.split("-", 1)[0]
            if prefix.isdigit():
                numbers.append(int(prefix))
        seq = max(numbers, default=0) + 1
        return f"{seq:06d}"

    def enqueue(self, job: QueueJob) -> QueueJob:
        name = f"{self._next_name()}-{job.id}.json"
        path = self.pending / name
        path.write_text(json.dumps(job.to_dict(), indent=2) + "\n", encoding="utf-8")
        return job

    def claim(self) -> QueueJob | None:
        """Move one job to active. A crash leaves it there so the next pass retries."""
        if self._claimed is not None:
            raise RuntimeError("complete the claimed job before claiming another")
        active = sorted(self.active.glob("*.json"))
        pending = sorted(self.pending.glob("*.json"))
        path = (active or pending or [None])[0]
        if path is None:
            return None
        if path.parent != self.active:
            dest = self.active / path.name
            path.replace(dest)
            path = dest
        self._claimed = path
        return QueueJob.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def complete(self) -> None:
        if self._claimed is None:
            return
        self._claimed.replace(self.done / self._claimed.name)
        self._claimed = None
