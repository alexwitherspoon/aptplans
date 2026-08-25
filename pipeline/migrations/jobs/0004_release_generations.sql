CREATE TABLE IF NOT EXISTS releases (
    generation_id TEXT PRIMARY KEY REFERENCES generations(generation_id),
    state TEXT NOT NULL
        CHECK (state IN (
            'building', 'validated', 'activating',
            'active', 'superseded', 'failed'
        )),
    manifest_sha256 TEXT,
    manifest_json TEXT,
    search_index_uid TEXT,
    search_state TEXT NOT NULL DEFAULT 'none'
        CHECK (search_state IN ('none', 'staged', 'active', 'failed')),
    search_document_count INTEGER,
    created_at TEXT NOT NULL,
    validated_at TEXT,
    activated_at TEXT,
    error TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS one_active_release
ON releases(state)
WHERE state='active';

CREATE TABLE IF NOT EXISTS release_events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    generation_id TEXT NOT NULL REFERENCES releases(generation_id),
    event_type TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    details_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS release_events_generation
ON release_events(generation_id, seq);

CREATE TRIGGER IF NOT EXISTS release_events_no_update
BEFORE UPDATE ON release_events
BEGIN
    SELECT RAISE(ABORT, 'release events are append-only');
END;

CREATE TRIGGER IF NOT EXISTS release_events_no_delete
BEFORE DELETE ON release_events
BEGIN
    SELECT RAISE(ABORT, 'release events are append-only');
END;
