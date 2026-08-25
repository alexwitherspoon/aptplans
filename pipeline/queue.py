"""Transactional SQLite job ledger and operator control inbox."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import socket
import sqlite3
import uuid


MAX_ATTEMPTS = 3
DEFAULT_LEASE_SECONDS = 300
MIGRATIONS = Path(__file__).resolve().parent / "migrations"
PRIORITY_BY_KIND = {
    "review": 100,
    "pipeline_snapshot": 90,
    "site_build": 80,
    "check": 70,
    "vet": 65,
    "fetch": 60,
    "explore": 50,
    "overlay_refresh": 45,
    "link_check": 40,
    "discovery": 20,
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _after(seconds: float) -> str:
    value = datetime.now(timezone.utc) + timedelta(seconds=max(0.0, seconds))
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def _normalize_lid(raw: str | None) -> str | None:
    return (raw or "").strip().upper() or None


def _worker_id() -> str:
    return (
        os.environ.get("APTPLANS_WORKER_ID", "").strip()
        or f"{socket.gethostname()}:{os.getpid()}"
    )


def _lease_seconds(value: int | None = None) -> int:
    if value is not None:
        return max(30, int(value))
    try:
        return max(
            30,
            int(
                os.environ.get(
                    "APTPLANS_JOB_LEASE_SECONDS",
                    str(DEFAULT_LEASE_SECONDS),
                )
            ),
        )
    except ValueError:
        return DEFAULT_LEASE_SECONDS


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=30.0, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=FULL")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA busy_timeout=30000")
    return connection


def _migrate(connection: sqlite3.Connection, family: str) -> None:
    connection.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations "
        "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    applied = {
        int(row["version"])
        for row in connection.execute("SELECT version FROM schema_migrations")
    }
    for path in sorted((MIGRATIONS / family).glob("*.sql")):
        prefix = path.stem.split("_", 1)[0]
        if not prefix.isdigit() or int(prefix) in applied:
            continue
        version = int(prefix)
        connection.executescript(
            "BEGIN IMMEDIATE;\n"
            + path.read_text(encoding="utf-8")
            + "\nINSERT INTO schema_migrations(version, applied_at) "
            + f"VALUES ({version}, '{_utc_now()}');\nCOMMIT;\n"
        )


class JobRetry(Exception):
    """The ledger rescheduled a failed attempt for a later claim."""

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
    requested_review_status: str | None = None
    expected_content_sha256: str | None = None
    requested_by: str | None = None
    request_reason: str | None = None
    priority: int | None = None
    dedupe_key: str | None = None
    parent_job_id: str | None = None
    retry_class: str = "bounded"
    next_attempt_at: str | None = None
    lease_owner: str | None = None
    lease_token: str | None = None
    lease_expires_at: str | None = None
    progress: dict | None = field(default=None, repr=False, compare=False)
    last_error: str | None = None
    dead_letter_reason: str | None = None
    reject_record: dict | None = field(default=None, repr=False, compare=False)

    def to_dict(self) -> dict:
        data = asdict(self)
        data.pop("reject_record", None)
        return data

    @classmethod
    def from_dict(cls, data: dict) -> QueueJob:
        names = cls.__dataclass_fields__
        values = {key: value for key, value in data.items() if key in names}
        values["kind"] = data["kind"]
        values["document_id"] = data.get("document_id")
        values["source_url"] = data.get("source_url")
        values["airport_lid"] = data.get("airport_lid")
        values["id"] = data.get("id") or uuid.uuid4().hex[:12]
        values["created_at"] = data.get("created_at") or _utc_now()
        values["attempts"] = int(data.get("attempts") or 0)
        if data.get("priority") is not None:
            values["priority"] = int(data["priority"])
        if not isinstance(data.get("progress"), dict):
            values["progress"] = None
        return cls(**values)


def _payload(job: QueueJob) -> str:
    return json.dumps(job.to_dict(), ensure_ascii=True, separators=(",", ":"))


def _job_from_row(row: sqlite3.Row) -> QueueJob:
    data = json.loads(row["payload_json"])
    data.update(
        {
            "attempts": row["attempts"],
            "priority": row["priority"],
            "dedupe_key": row["dedupe_key"],
            "parent_job_id": row["parent_job_id"],
            "retry_class": row["retry_class"],
            "next_attempt_at": row["next_attempt_at"],
            "lease_owner": row["lease_owner"],
            "lease_token": row["lease_token"],
            "lease_expires_at": row["lease_expires_at"],
            "progress": (
                json.loads(row["progress_json"]) if row["progress_json"] else None
            ),
            "last_error": row["last_error"],
        }
    )
    return QueueJob.from_dict(data)


class JobQueue:
    """Durable queue API backed by ``jobs.sqlite3`` in WAL mode."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.path = self.root / "jobs.sqlite3"
        with _connect(self.path) as connection:
            _migrate(connection, "jobs")

    def _connection(self) -> sqlite3.Connection:
        return _connect(self.path)

    @staticmethod
    def _priority(job: QueueJob) -> int:
        return (
            int(job.priority)
            if job.priority is not None
            else PRIORITY_BY_KIND.get(job.kind, 30)
        )

    def enqueue(self, job: QueueJob) -> QueueJob:
        now = _utc_now()
        job.airport_lid = _normalize_lid(job.airport_lid)
        job.priority = self._priority(job)
        job.next_attempt_at = job.next_attempt_at or now
        if job.retry_class not in {"bounded", "continuous"}:
            raise ValueError(f"unknown retry class: {job.retry_class}")
        max_attempts = None if job.retry_class == "continuous" else MAX_ATTEMPTS
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    """
                    INSERT INTO jobs(
                        id, kind, state, payload_json, document_id, source_url,
                        airport_lid, issue_number, priority, dedupe_key,
                        parent_job_id, retry_class, max_attempts, attempts,
                        next_attempt_at, created_at, updated_at
                    ) VALUES (?, ?, 'pending', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)
                    """,
                    (
                        job.id,
                        job.kind,
                        _payload(job),
                        job.document_id,
                        job.source_url,
                        job.airport_lid,
                        job.issue_number,
                        job.priority,
                        job.dedupe_key,
                        job.parent_job_id,
                        job.retry_class,
                        max_attempts,
                        job.next_attempt_at,
                        job.created_at,
                        now,
                    ),
                )
                connection.execute("COMMIT")
                return job
            except sqlite3.IntegrityError:
                connection.execute("ROLLBACK")
                if not job.dedupe_key:
                    raise
                row = connection.execute(
                    """
                    SELECT * FROM jobs
                    WHERE dedupe_key=? AND state IN ('pending', 'leased')
                    ORDER BY seq LIMIT 1
                    """,
                    (job.dedupe_key,),
                ).fetchone()
                if row is None:
                    raise
                return _job_from_row(row)

    def has_issue(self, issue_number: int) -> bool:
        with self._connection() as connection:
            return (
                connection.execute(
                    "SELECT 1 FROM jobs WHERE issue_number=? LIMIT 1",
                    (issue_number,),
                ).fetchone()
                is not None
            )

    def get(self, job_id: str) -> QueueJob | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM jobs WHERE id=?",
                (job_id,),
            ).fetchone()
        return _job_from_row(row) if row is not None else None

    def has_kind(self, kind: str) -> bool:
        with self._connection() as connection:
            return (
                connection.execute(
                    """
                    SELECT 1 FROM jobs
                    WHERE kind=? AND state IN ('pending', 'leased') LIMIT 1
                    """,
                    (kind,),
                ).fetchone()
                is not None
            )

    def active_airport_lids(self) -> frozenset[str]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT DISTINCT airport_lid FROM jobs
                WHERE state='leased' AND airport_lid IS NOT NULL
                  AND lease_expires_at > ?
                """,
                (_utc_now(),),
            )
            return frozenset(str(row["airport_lid"]) for row in rows)

    def counts(self) -> dict[str, int]:
        result = {"pending": 0, "active": 0, "done": 0, "dead": 0}
        mapping = {
            "pending": "pending",
            "leased": "active",
            "succeeded": "done",
            "dead": "dead",
        }
        with self._connection() as connection:
            for row in connection.execute(
                "SELECT state, COUNT(*) AS n FROM jobs GROUP BY state"
            ):
                result[mapping[row["state"]]] = int(row["n"])
        return result

    def kinds(self, state: str) -> list[str]:
        db_state = {"active": "leased", "done": "succeeded"}.get(state, state)
        with self._connection() as connection:
            return [
                str(row["kind"])
                for row in connection.execute(
                    "SELECT kind FROM jobs WHERE state=? ORDER BY seq",
                    (db_state,),
                )
            ]

    def jobs(
        self,
        *,
        state: str | None = None,
        kind: str | None = None,
    ) -> list[QueueJob]:
        clauses: list[str] = []
        values: list[object] = []
        if state:
            clauses.append("state=?")
            values.append({"active": "leased", "done": "succeeded"}.get(state, state))
        if kind:
            clauses.append("kind=?")
            values.append(kind)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM jobs" + where + " ORDER BY seq",
                values,
            ).fetchall()
        return [_job_from_row(row) for row in rows]

    def pending_job(self, kind: str) -> QueueJob | None:
        rows = self.jobs(state="pending", kind=kind)
        return rows[0] if rows else None

    def update_pending(self, job: QueueJob) -> None:
        now = _utc_now()
        job.airport_lid = _normalize_lid(job.airport_lid)
        job.priority = self._priority(job)
        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE jobs SET payload_json=?, document_id=?, source_url=?,
                    airport_lid=?, issue_number=?, priority=?, dedupe_key=?,
                    parent_job_id=?, retry_class=?, updated_at=?
                WHERE id=? AND state='pending'
                """,
                (
                    _payload(job),
                    job.document_id,
                    job.source_url,
                    job.airport_lid,
                    job.issue_number,
                    job.priority,
                    job.dedupe_key,
                    job.parent_job_id,
                    job.retry_class,
                    now,
                    job.id,
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError(f"pending job unavailable: {job.id}")

    def _recover_expired(
        self,
        connection: sqlite3.Connection,
        now: str,
    ) -> int:
        rows = connection.execute(
            """
            SELECT id, attempts FROM jobs
            WHERE state='leased' AND lease_expires_at <= ?
            """,
            (now,),
        ).fetchall()
        for row in rows:
            connection.execute(
                """
                UPDATE attempts SET finished_at=?, outcome='lease_expired',
                    error='worker lease expired'
                WHERE job_id=? AND attempt_number=? AND finished_at IS NULL
                """,
                (now, row["id"], row["attempts"]),
            )
        cursor = connection.execute(
            """
            UPDATE jobs SET state='pending', lease_owner=NULL, lease_token=NULL,
                lease_expires_at=NULL, heartbeat_at=NULL, updated_at=?,
                last_error='worker lease expired'
            WHERE state='leased' AND lease_expires_at <= ?
            """,
            (now, now),
        )
        return int(cursor.rowcount)

    def recover_expired_leases(self) -> int:
        now = _utc_now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            count = self._recover_expired(connection, now)
            connection.execute("COMMIT")
        return count

    def claim(
        self,
        *,
        airport_limit: int = 1,
        worker_id: str | None = None,
        lease_seconds: int | None = None,
    ) -> QueueJob | None:
        owner = worker_id or _worker_id()
        now = _utc_now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._recover_expired(connection, now)
            connection.execute(
                """
                UPDATE jobs SET state='dead', completed_at=?, updated_at=?,
                    last_error='parent job did not succeed'
                WHERE state='pending' AND parent_job_id IN (
                    SELECT id FROM jobs WHERE state='dead'
                )
                """,
                (now, now),
            )
            leased = connection.execute(
                """
                SELECT airport_lid FROM jobs
                WHERE state='leased' AND lease_expires_at > ?
                """,
                (now,),
            ).fetchall()
            if len(leased) >= max(1, airport_limit) or (
                leased and any(row["airport_lid"] is None for row in leased)
            ):
                connection.execute("COMMIT")
                return None
            leased_lids = sorted(
                {
                    str(row["airport_lid"])
                    for row in leased
                    if row["airport_lid"] is not None
                }
            )
            exclusions = ""
            values: list[object] = [now]
            if leased_lids:
                marks = ",".join("?" for _ in leased_lids)
                exclusions = (
                    f" AND j.airport_lid IS NOT NULL"
                    f" AND j.airport_lid NOT IN ({marks})"
                )
                values.extend(leased_lids)

            query = (
                """
                SELECT j.* FROM jobs j
                WHERE j.state='pending' AND j.next_attempt_at <= ?
                  AND (
                    j.parent_job_id IS NULL OR EXISTS (
                        SELECT 1 FROM jobs p
                        WHERE p.id=j.parent_job_id AND p.state='succeeded'
                    )
                  )
                """
                + exclusions
                + """
                  {preference}
                ORDER BY (
                    j.priority
                    + CAST((julianday(?) - julianday(j.created_at)) * 24 AS INTEGER)
                ) DESC, j.seq
                LIMIT 1
                """
            )

            def pick(preference: str = "", extra: tuple[object, ...] = ()):
                return connection.execute(
                    query.format(preference=preference),
                    (*values, *extra, now),
                ).fetchone()

            row = None
            if not leased and airport_limit <= 1:
                row = pick("AND j.airport_lid IS NULL AND j.priority >= 80")
                if row is None:
                    cursor = connection.execute(
                        "SELECT value FROM queue_meta WHERE key='airport_cursor'"
                    ).fetchone()
                    focus = _normalize_lid(cursor["value"]) if cursor else None
                    if focus:
                        row = pick("AND j.airport_lid=?", (focus,))
                        if row is None:
                            connection.execute(
                                "DELETE FROM queue_meta WHERE key='airport_cursor'"
                            )
            if row is None:
                row = pick()
            if row is None:
                connection.execute("COMMIT")
                return None
            attempts = int(row["attempts"]) + 1
            token = uuid.uuid4().hex
            expires = _after(_lease_seconds(lease_seconds))
            cursor = connection.execute(
                """
                UPDATE jobs SET state='leased', attempts=?, lease_owner=?,
                    lease_token=?, lease_expires_at=?, heartbeat_at=?, updated_at=?
                WHERE id=? AND state='pending'
                """,
                (attempts, owner, token, expires, now, now, row["id"]),
            )
            if cursor.rowcount != 1:
                connection.execute("ROLLBACK")
                return None
            connection.execute(
                """
                INSERT INTO attempts(
                    job_id, attempt_number, worker_id, lease_token,
                    started_at, heartbeat_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (row["id"], attempts, owner, token, now, now),
            )
            if airport_limit <= 1 and row["airport_lid"]:
                connection.execute(
                    """
                    INSERT INTO queue_meta(key, value) VALUES ('airport_cursor', ?)
                    ON CONFLICT(key) DO UPDATE SET value=excluded.value
                    """,
                    (row["airport_lid"],),
                )
            connection.execute("COMMIT")
            claimed = connection.execute(
                "SELECT * FROM jobs WHERE id=?",
                (row["id"],),
            ).fetchone()
        return _job_from_row(claimed)

    def heartbeat(
        self,
        job: QueueJob,
        *,
        progress: dict | None = None,
        lease_seconds: int | None = None,
    ) -> None:
        now = _utc_now()
        expires = _after(_lease_seconds(lease_seconds))
        progress_json = json.dumps(progress, separators=(",", ":")) if progress else None
        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE jobs SET heartbeat_at=?, lease_expires_at=?,
                    progress_json=COALESCE(?, progress_json), updated_at=?
                WHERE id=? AND state='leased' AND lease_token=?
                """,
                (now, expires, progress_json, now, job.id, job.lease_token),
            )
            if cursor.rowcount != 1:
                raise RuntimeError(f"job lease lost: {job.id}")
            connection.execute(
                """
                UPDATE attempts SET heartbeat_at=?,
                    progress_json=COALESCE(?, progress_json)
                WHERE job_id=? AND attempt_number=? AND lease_token=?
                """,
                (now, progress_json, job.id, job.attempts, job.lease_token),
            )
        job.lease_expires_at = expires
        if progress is not None:
            job.progress = progress

    def retry(self, job: QueueJob, *, delay_seconds: float, error: str) -> None:
        self._finish_attempt(
            job,
            state="pending",
            outcome="retry",
            error=error,
            next_attempt_at=_after(delay_seconds),
        )

    def complete(self, job: QueueJob) -> None:
        self._finish_attempt(job, state="succeeded", outcome="succeeded")

    def dead_letter(self, job: QueueJob, *, error: str) -> None:
        self._finish_attempt(job, state="dead", outcome="dead", error=error)

    def _finish_attempt(
        self,
        job: QueueJob,
        *,
        state: str,
        outcome: str,
        error: str | None = None,
        next_attempt_at: str | None = None,
    ) -> None:
        now = _utc_now()
        completed = now if state in {"succeeded", "dead"} else None
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                UPDATE jobs SET state=?, next_attempt_at=COALESCE(?, next_attempt_at),
                    lease_owner=NULL, lease_token=NULL, lease_expires_at=NULL,
                    heartbeat_at=NULL, updated_at=?, completed_at=?, last_error=?
                WHERE id=? AND state='leased' AND lease_token=?
                """,
                (
                    state,
                    next_attempt_at,
                    now,
                    completed,
                    error,
                    job.id,
                    job.lease_token,
                ),
            )
            if cursor.rowcount != 1:
                connection.execute("ROLLBACK")
                raise RuntimeError(f"job lease lost: {job.id}")
            connection.execute(
                """
                UPDATE attempts SET finished_at=?, outcome=?, error=?
                WHERE job_id=? AND attempt_number=? AND lease_token=?
                """,
                (now, outcome, error, job.id, job.attempts, job.lease_token),
            )
            if job.airport_lid:
                remaining = connection.execute(
                    """
                    SELECT 1 FROM jobs
                    WHERE airport_lid=? AND state IN ('pending', 'leased')
                    LIMIT 1
                    """,
                    (_normalize_lid(job.airport_lid),),
                ).fetchone()
                if remaining is None:
                    connection.execute(
                        """
                        DELETE FROM queue_meta
                        WHERE key='airport_cursor' AND value=?
                        """,
                        (_normalize_lid(job.airport_lid),),
                    )
            connection.execute("COMMIT")

    def reschedule_now(self, job_id: str) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE jobs SET next_attempt_at=?, updated_at=?
                WHERE id=? AND state='pending'
                """,
                (_utc_now(), _utc_now(), job_id),
            )

    def integrity_check(self) -> str:
        with self._connection() as connection:
            row = connection.execute("PRAGMA integrity_check").fetchone()
            return str(row[0])

    def backup(self, destination: Path) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as source:
            target = sqlite3.connect(destination)
            try:
                source.backup(target)
            finally:
                target.close()
        return destination

    def ingest_controls(self, controls: ControlQueue | None = None) -> int:
        inbox = controls or ControlQueue(self.root)
        accepted = 0
        for command in inbox.pending():
            try:
                existing = self.get(command["id"])
                if existing is not None:
                    inbox.accept(command["id"], existing.id)
                    accepted += 1
                    continue
                job = QueueJob.from_dict(command["payload"])
                job.id = command["id"]
                self.enqueue(job)
                inbox.accept(command["id"], job.id)
                accepted += 1
            except Exception as exc:
                inbox.reject(command["id"], str(exc))
        return accepted


class ControlQueue:
    """API-writable command inbox kept separate from the worker job ledger."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.path = self.root / "control.sqlite3"
        with _connect(self.path) as connection:
            _migrate(connection, "control")

    def _connection(self) -> sqlite3.Connection:
        return _connect(self.path)

    def enqueue(self, job: QueueJob) -> QueueJob:
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO controls(
                    id, kind, payload_json, state, requested_by, created_at
                ) VALUES (?, ?, ?, 'pending', ?, ?)
                """,
                (
                    job.id,
                    job.kind,
                    _payload(job),
                    job.requested_by,
                    job.created_at,
                ),
            )
        return job

    def pending(self) -> list[dict]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM controls
                WHERE state='pending' ORDER BY seq
                """
            ).fetchall()
        return [
            {
                "id": row["id"],
                "kind": row["kind"],
                "payload": json.loads(row["payload_json"]),
            }
            for row in rows
        ]

    def accept(self, command_id: str, worker_job_id: str) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE controls SET state='accepted', accepted_at=?,
                    worker_job_id=?, error=NULL
                WHERE id=? AND state='pending'
                """,
                (_utc_now(), worker_job_id, command_id),
            )

    def reject(self, command_id: str, error: str) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE controls SET state='rejected', accepted_at=?, error=?
                WHERE id=? AND state='pending'
                """,
                (_utc_now(), error, command_id),
            )

    def counts(self) -> dict[str, int]:
        result = {"pending": 0, "accepted": 0, "rejected": 0}
        with self._connection() as connection:
            for row in connection.execute(
                "SELECT state, COUNT(*) AS n FROM controls GROUP BY state"
            ):
                result[str(row["state"])] = int(row["n"])
        return result

    def integrity_check(self) -> str:
        with self._connection() as connection:
            row = connection.execute("PRAGMA integrity_check").fetchone()
            return str(row[0])

    def backup(self, destination: Path) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as source:
            target = sqlite3.connect(destination)
            try:
                source.backup(target)
            finally:
                target.close()
        return destination
