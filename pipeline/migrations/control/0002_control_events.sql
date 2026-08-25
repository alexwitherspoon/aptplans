CREATE TABLE IF NOT EXISTS control_events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    command_id TEXT NOT NULL REFERENCES controls(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    actor TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    details_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS control_events_command
ON control_events(command_id, seq);

CREATE TRIGGER IF NOT EXISTS control_events_no_update
BEFORE UPDATE ON control_events
BEGIN
    SELECT RAISE(ABORT, 'control events are append-only');
END;

CREATE TRIGGER IF NOT EXISTS control_events_no_delete
BEFORE DELETE ON control_events
BEGIN
    SELECT RAISE(ABORT, 'control events are append-only');
END;
