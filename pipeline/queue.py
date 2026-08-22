"""On-disk queue. One JSON file per job; airport slots pace parallel work."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
import uuid


MAX_ATTEMPTS = 3


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _normalize_lid(raw: str | None) -> str | None:
    lid = (raw or "").strip().upper()
    return lid or None


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

    def active_airport_lids(self) -> frozenset[str]:
        lids: set[str] = set()
        for path in self.active.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, TypeError):
                continue
            lid = _normalize_lid(data.get("airport_lid"))
            if lid:
                lids.add(lid)
        return frozenset(lids)

    def has_kind(self, kind: str) -> bool:
        for folder in (self.pending, self.active):
            for path in folder.glob("*.json"):
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError, TypeError):
                    continue
                if data.get("kind") == kind:
                    return True
        return False

    def _cursor_path(self) -> Path:
        return self.root / ".airport_cursor"

    def _read_cursor(self) -> str | None:
        path = self._cursor_path()
        if not path.is_file():
            return None
        try:
            return _normalize_lid(path.read_text(encoding="utf-8"))
        except OSError:
            return None

    def _write_cursor(self, lid: str | None) -> None:
        path = self._cursor_path()
        if not lid:
            if path.is_file():
                path.unlink()
            return
        path.write_text(lid + "\n", encoding="utf-8")

    def _pending_has_lid(self, lid: str) -> bool:
        for path in self.pending.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, TypeError):
                continue
            if _normalize_lid(data.get("airport_lid")) == lid:
                return True
        return False

    @staticmethod
    def _claimable(
        lid: str | None,
        active_lids: frozenset[str],
        airport_limit: int,
        focus_lid: str | None,
    ) -> bool:
        if not lid:
            return not active_lids
        if active_lids:
            if lid in active_lids:
                return True
            return len(active_lids) < airport_limit
        if airport_limit <= 1:
            if not lid:
                return not active_lids
            if focus_lid:
                return lid == focus_lid
            return not active_lids
        return True

    def _focus_lid(self, airport_limit: int, active_lids: frozenset[str]) -> str | None:
        if airport_limit <= 1:
            focus = self._read_cursor()
            if focus and not self._pending_has_lid(focus) and focus not in active_lids:
                self._write_cursor(None)
                focus = None
            if active_lids:
                return next(iter(active_lids))
            if focus:
                return focus
            pending = sorted(self.pending.glob("*.json"))
            if not pending:
                return None
            for path in pending:
                try:
                    focus = _normalize_lid(
                        json.loads(path.read_text(encoding="utf-8")).get("airport_lid")
                    )
                except (OSError, json.JSONDecodeError, TypeError):
                    continue
                if focus:
                    self._write_cursor(focus)
                    return focus
            return None

        pending = sorted(self.pending.glob("*.json"))
        if not pending:
            return None
        try:
            return _normalize_lid(
                json.loads(pending[0].read_text(encoding="utf-8")).get("airport_lid")
            )
        except (OSError, json.JSONDecodeError, TypeError):
            return None

    def _pick_pending(
        self,
        airport_limit: int,
        active_lids: frozenset[str],
        *,
        maintenance: bool = False,
    ) -> QueueJob | None:
        pending = sorted(self.pending.glob("*.json"))
        if not pending:
            return None
        focus_lid = self._focus_lid(airport_limit, active_lids) if not maintenance else None
        for path in pending:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, TypeError):
                continue
            lid = _normalize_lid(data.get("airport_lid"))
            if maintenance:
                if lid:
                    continue
            elif not lid:
                continue
            if self._claimable(lid, active_lids, airport_limit, focus_lid):
                job = self._activate(path)
                if airport_limit <= 1 and lid:
                    self._write_cursor(lid)
                return job
        return None

    def claim(self, *, airport_limit: int = 1) -> QueueJob | None:
        """Move one job to active. Respects airport concurrency slots."""
        active = sorted(self.active.glob("*.json"))
        active_lids = self.active_airport_lids()

        if len(active) == 1 and airport_limit == 1:
            return self._activate(active[0])

        if len(active_lids) >= airport_limit:
            return None

        picked = self._pick_pending(airport_limit, active_lids, maintenance=False)
        if picked is not None:
            return picked
        if not active_lids:
            picked = self._pick_pending(airport_limit, active_lids, maintenance=True)
            if picked is not None:
                return picked

        if len(active) == 1 and airport_limit == 1:
            return self._activate(active[0])
        return None

    def _activate(self, path: Path) -> QueueJob:
        if path.parent != self.active:
            dest = self.active / path.name
            path.replace(dest)
            path = dest
        job = QueueJob.from_dict(json.loads(path.read_text(encoding="utf-8")))
        job.attempts += 1
        path.write_text(json.dumps(job.to_dict(), indent=2) + "\n", encoding="utf-8")
        return job

    def complete(self, job: QueueJob) -> None:
        lid = _normalize_lid(job.airport_lid)
        for path in self.active.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, TypeError):
                continue
            if data.get("id") == job.id:
                path.replace(self.done / path.name)
                break
        else:
            return
        if lid and not self._pending_has_lid(lid) and lid not in self.active_airport_lids():
            if self._read_cursor() == lid:
                self._write_cursor(None)
