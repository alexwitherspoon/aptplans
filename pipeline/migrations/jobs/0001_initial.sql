PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS queue_meta (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS jobs (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('pending', 'leased', 'succeeded', 'dead')),
    payload_json TEXT NOT NULL,
    document_id TEXT,
    source_url TEXT,
    airport_lid TEXT,
    issue_number INTEGER,
    priority INTEGER NOT NULL DEFAULT 0,
    dedupe_key TEXT,
    parent_job_id TEXT REFERENCES jobs(id),
    retry_class TEXT NOT NULL DEFAULT 'bounded',
    max_attempts INTEGER,
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TEXT NOT NULL,
    lease_owner TEXT,
    lease_token TEXT,
    lease_expires_at TEXT,
    heartbeat_at TEXT,
    progress_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT,
    last_error TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS jobs_open_dedupe
ON jobs(dedupe_key)
WHERE dedupe_key IS NOT NULL AND state IN ('pending', 'leased');

CREATE INDEX IF NOT EXISTS jobs_claim_order
ON jobs(state, next_attempt_at, priority DESC, seq);

CREATE INDEX IF NOT EXISTS jobs_airport_state
ON jobs(airport_lid, state);

CREATE INDEX IF NOT EXISTS jobs_kind_state
ON jobs(kind, state);

CREATE TABLE IF NOT EXISTS attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    attempt_number INTEGER NOT NULL,
    worker_id TEXT NOT NULL,
    lease_token TEXT NOT NULL,
    started_at TEXT NOT NULL,
    heartbeat_at TEXT NOT NULL,
    finished_at TEXT,
    outcome TEXT,
    error TEXT,
    progress_json TEXT,
    UNIQUE(job_id, attempt_number)
);

