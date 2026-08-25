CREATE TABLE IF NOT EXISTS inference_results (
    task_key TEXT PRIMARY KEY
        CHECK (length(task_key) = 64),
    content_sha256 TEXT NOT NULL
        REFERENCES artifact_versions(content_sha256),
    extraction_manifest_key TEXT NOT NULL
        REFERENCES extraction_manifests(manifest_key),
    task_type TEXT NOT NULL,
    task_version TEXT NOT NULL,
    lane TEXT NOT NULL
        CHECK (lane IN ('easy_text', 'complex_text', 'vision')),
    model_digest TEXT NOT NULL
        CHECK (
            length(model_digest) = 71
            AND substr(model_digest, 1, 7) = 'sha256:'
            AND substr(model_digest, 8) NOT GLOB '*[^0-9a-f]*'
        ),
    options_sha256 TEXT NOT NULL
        CHECK (length(options_sha256) = 64),
    result_sha256 TEXT NOT NULL
        CHECK (length(result_sha256) = 64),
    envelope_sha256 TEXT NOT NULL
        CHECK (length(envelope_sha256) = 64),
    result_json TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    quality_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (
        content_sha256,
        extraction_manifest_key,
        task_type,
        task_version,
        lane,
        model_digest,
        options_sha256
    )
);

CREATE TRIGGER IF NOT EXISTS inference_results_no_update
BEFORE UPDATE ON inference_results
BEGIN
    SELECT RAISE(ABORT, 'inference results are immutable');
END;

CREATE TRIGGER IF NOT EXISTS inference_results_no_delete
BEFORE DELETE ON inference_results
BEGIN
    SELECT RAISE(ABORT, 'inference results are immutable');
END;

CREATE TABLE IF NOT EXISTS inference_checkpoints (
    task_key TEXT PRIMARY KEY
        CHECK (length(task_key) = 64),
    content_sha256 TEXT NOT NULL
        REFERENCES artifact_versions(content_sha256),
    extraction_manifest_key TEXT NOT NULL
        REFERENCES extraction_manifests(manifest_key),
    key_json TEXT NOT NULL,
    cursor INTEGER NOT NULL
        CHECK (cursor >= 0),
    state_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TRIGGER IF NOT EXISTS inference_checkpoints_cursor_monotonic
BEFORE UPDATE ON inference_checkpoints
WHEN NEW.cursor < OLD.cursor
BEGIN
    SELECT RAISE(ABORT, 'inference checkpoint cursor cannot regress');
END;
