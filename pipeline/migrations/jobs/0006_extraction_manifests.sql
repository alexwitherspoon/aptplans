CREATE TABLE IF NOT EXISTS artifact_versions (
    content_sha256 TEXT PRIMARY KEY
        CHECK (length(content_sha256) = 64),
    media_type TEXT NOT NULL,
    byte_count INTEGER NOT NULL
        CHECK (byte_count >= 0),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS extraction_manifests (
    manifest_key TEXT PRIMARY KEY
        CHECK (length(manifest_key) = 64),
    content_sha256 TEXT NOT NULL
        REFERENCES artifact_versions(content_sha256),
    extractor_version TEXT NOT NULL,
    options_sha256 TEXT NOT NULL
        CHECK (length(options_sha256) = 64),
    status TEXT NOT NULL
        CHECK (status IN ('completed', 'partial', 'failed')),
    page_count INTEGER NOT NULL
        CHECK (page_count >= 0),
    manifest_sha256 TEXT NOT NULL
        CHECK (length(manifest_sha256) = 64),
    manifest_path TEXT NOT NULL,
    coordinates_available INTEGER NOT NULL
        CHECK (coordinates_available IN (0, 1)),
    quality_json TEXT NOT NULL,
    error_json TEXT,
    duration_ms INTEGER NOT NULL
        CHECK (duration_ms >= 0),
    created_at TEXT NOT NULL,
    UNIQUE(content_sha256, extractor_version, options_sha256)
);

CREATE INDEX IF NOT EXISTS extraction_manifests_artifact
ON extraction_manifests(content_sha256, created_at);

CREATE TRIGGER IF NOT EXISTS artifact_versions_no_update
BEFORE UPDATE ON artifact_versions
BEGIN
    SELECT RAISE(ABORT, 'artifact versions are immutable');
END;

CREATE TRIGGER IF NOT EXISTS artifact_versions_no_delete
BEFORE DELETE ON artifact_versions
BEGIN
    SELECT RAISE(ABORT, 'artifact versions are immutable');
END;

CREATE TRIGGER IF NOT EXISTS extraction_manifests_no_update
BEFORE UPDATE ON extraction_manifests
BEGIN
    SELECT RAISE(ABORT, 'extraction manifests are immutable');
END;

CREATE TRIGGER IF NOT EXISTS extraction_manifests_no_delete
BEFORE DELETE ON extraction_manifests
BEGIN
    SELECT RAISE(ABORT, 'extraction manifests are immutable');
END;
