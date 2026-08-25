CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS controls (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('pending', 'accepted', 'rejected')),
    requested_by TEXT,
    created_at TEXT NOT NULL,
    accepted_at TEXT,
    worker_job_id TEXT,
    error TEXT
);

CREATE INDEX IF NOT EXISTS controls_state_created
ON controls(state, created_at, seq);
