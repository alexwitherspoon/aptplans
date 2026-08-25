ALTER TABLE generations
ADD COLUMN dataset_state_json TEXT NOT NULL DEFAULT '{}';

CREATE TABLE IF NOT EXISTS audit_records (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    stream TEXT NOT NULL
        CHECK (stream IN ('classifications', 'outcomes')),
    event_key TEXT,
    occurred_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    generation_id TEXT REFERENCES generations(generation_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS audit_records_idempotency
ON audit_records(stream, event_key)
WHERE event_key IS NOT NULL;

CREATE INDEX IF NOT EXISTS audit_records_stream_seq
ON audit_records(stream, seq);

CREATE TRIGGER IF NOT EXISTS audit_records_no_update
BEFORE UPDATE ON audit_records
BEGIN
    SELECT RAISE(ABORT, 'audit records are append-only');
END;

CREATE TRIGGER IF NOT EXISTS audit_records_no_delete
BEFORE DELETE ON audit_records
BEGIN
    SELECT RAISE(ABORT, 'audit records are append-only');
END;
