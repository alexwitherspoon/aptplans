"""On-disk serial queue. One JSON file per job."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
import uuid


MAX_ATTEMPTS = 3


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class JobRetry(Exception):
    """Uncaught job error. Leave the file in active/ and wait before the next claim."""

    def __init__(self, attempts: int) -> None:
        self.attempts = attempts
        super().__init__(f"retry after attempt {attempts}")

    def delay_seconds(self) -> float:
        return min(3600.0, 60.0 * (2 ** max(self.attempts - 1, 0)))


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
    attempts: int = 0
    found_on: str | None = None
    part_of: str | None = None
    reject_record: dict | None = field(default=None, repr=False, compare=False)

    def to_dict(self) -> dict:
        data = asdict(self)
        data.pop("reject_record", None)
        return data

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
            attempts=int(data.get("attempts") or 0),
            found_on=data.get("found_on"),
            part_of=data.get("part_of"),
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

    def has_issue(self, issue_number: int) -> bool:
        for folder in (self.pending, self.active, self.done):
            for path in folder.glob("*.json"):
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError, TypeError):
                    continue
                if data.get("issue_number") == issue_number:
                    return True
        return False

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
        job = QueueJob.from_dict(json.loads(path.read_text(encoding="utf-8")))
        job.attempts += 1
        path.write_text(json.dumps(job.to_dict(), indent=2) + "\n", encoding="utf-8")
        return job

    def complete(self) -> None:
        if self._claimed is None:
            return
        self._claimed.replace(self.done / self._claimed.name)
        self._claimed = None
