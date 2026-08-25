CREATE TABLE IF NOT EXISTS continuations (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    dedupe_key TEXT NOT NULL,
    child_payload_json TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'pending'
        CHECK (state IN ('pending', 'materialized', 'cancelled')),
    child_job_id TEXT REFERENCES jobs(id),
    created_at TEXT NOT NULL,
    materialized_at TEXT,
    UNIQUE(parent_job_id, dedupe_key)
);

CREATE INDEX IF NOT EXISTS continuations_ready
ON continuations(state, parent_job_id, seq);

CREATE TABLE IF NOT EXISTS job_events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT REFERENCES jobs(id) ON DELETE CASCADE,
    attempt_number INTEGER,
    event_type TEXT NOT NULL,
    actor TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    details_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS job_events_job
ON job_events(job_id, seq);

CREATE TRIGGER IF NOT EXISTS job_events_no_update
BEFORE UPDATE ON job_events
BEGIN
    SELECT RAISE(ABORT, 'job events are append-only');
END;

CREATE TRIGGER IF NOT EXISTS job_events_no_delete
BEFORE DELETE ON job_events
BEGIN
    SELECT RAISE(ABORT, 'job events are append-only');
END;
