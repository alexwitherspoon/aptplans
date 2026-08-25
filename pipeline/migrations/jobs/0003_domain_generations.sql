CREATE TABLE IF NOT EXISTS generations (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    generation_id TEXT NOT NULL UNIQUE,
    parent_generation_id TEXT REFERENCES generations(generation_id),
    state TEXT NOT NULL DEFAULT 'building'
        CHECK (state IN ('building', 'committed')),
    reason TEXT NOT NULL,
    actor TEXT NOT NULL,
    created_at TEXT NOT NULL,
    committed_at TEXT
);

CREATE TABLE IF NOT EXISTS entity_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type TEXT NOT NULL,
    entity_key TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(entity_type, entity_key, payload_sha256)
);

CREATE TABLE IF NOT EXISTS generation_entities (
    generation_id TEXT NOT NULL REFERENCES generations(generation_id),
    entity_type TEXT NOT NULL,
    entity_key TEXT NOT NULL,
    version_id INTEGER NOT NULL REFERENCES entity_versions(id),
    PRIMARY KEY(generation_id, entity_type, entity_key)
);

CREATE INDEX IF NOT EXISTS generation_entities_type
ON generation_entities(generation_id, entity_type, entity_key);

CREATE TABLE IF NOT EXISTS domain_meta (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS domain_events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    generation_id TEXT NOT NULL REFERENCES generations(generation_id),
    event_type TEXT NOT NULL,
    entity_type TEXT,
    entity_key TEXT,
    actor TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    details_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS domain_events_generation
ON domain_events(generation_id, seq);

CREATE TRIGGER IF NOT EXISTS generations_no_update
BEFORE UPDATE ON generations
WHEN OLD.state='committed'
BEGIN
    SELECT RAISE(ABORT, 'committed generations are immutable');
END;

CREATE TRIGGER IF NOT EXISTS generations_no_delete
BEFORE DELETE ON generations
BEGIN
    SELECT RAISE(ABORT, 'committed generations are immutable');
END;

CREATE TRIGGER IF NOT EXISTS entity_versions_no_update
BEFORE UPDATE ON entity_versions
BEGIN
    SELECT RAISE(ABORT, 'entity versions are immutable');
END;

CREATE TRIGGER IF NOT EXISTS entity_versions_no_delete
BEFORE DELETE ON entity_versions
BEGIN
    SELECT RAISE(ABORT, 'entity versions are immutable');
END;

CREATE TRIGGER IF NOT EXISTS generation_entities_no_update
BEFORE UPDATE ON generation_entities
WHEN EXISTS (
    SELECT 1 FROM generations
    WHERE generation_id=OLD.generation_id AND state='committed'
)
BEGIN
    SELECT RAISE(ABORT, 'generation membership is immutable');
END;

CREATE TRIGGER IF NOT EXISTS generation_entities_no_delete
BEFORE DELETE ON generation_entities
WHEN EXISTS (
    SELECT 1 FROM generations
    WHERE generation_id=OLD.generation_id AND state='committed'
)
BEGIN
    SELECT RAISE(ABORT, 'generation membership is immutable');
END;

CREATE TRIGGER IF NOT EXISTS domain_events_no_update
BEFORE UPDATE ON domain_events
BEGIN
    SELECT RAISE(ABORT, 'domain events are append-only');
END;

CREATE TRIGGER IF NOT EXISTS domain_events_no_delete
BEFORE DELETE ON domain_events
BEGIN
    SELECT RAISE(ABORT, 'domain events are append-only');
END;
