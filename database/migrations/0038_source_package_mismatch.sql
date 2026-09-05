-- Forge X source-package mismatch evidence and buyer decisions
PRAGMA foreign_keys = ON;
BEGIN IMMEDIATE;
CREATE TABLE source_package_mismatch (
    source_package_mismatch_id TEXT PRIMARY KEY,
    source_package_id TEXT NOT NULL REFERENCES source_package(source_package_id),
    source_datum_id TEXT NOT NULL REFERENCES source_datum(source_datum_id),
    submitted_normalized_package_number TEXT NOT NULL,
    submitted_displayed_package_number TEXT NOT NULL,
    event_normalized_package_number TEXT NOT NULL,
    event_displayed_package_number TEXT NOT NULL,
    mismatch_status TEXT NOT NULL CHECK (mismatch_status = 'Review Required'),
    detected_at_utc TEXT NOT NULL,
    UNIQUE (source_package_id, source_datum_id)
) STRICT;
CREATE TABLE source_package_mismatch_resolution (
    source_package_mismatch_resolution_id TEXT PRIMARY KEY,
    source_package_mismatch_id TEXT NOT NULL REFERENCES source_package_mismatch(source_package_mismatch_id),
    decision_code TEXT NOT NULL CHECK (decision_code IN ('Supplier Correction Required', 'Event Number Corrected')),
    decision_reason TEXT NOT NULL CHECK (length(trim(decision_reason)) > 0),
    decided_by_user_id TEXT NOT NULL CHECK (length(trim(decided_by_user_id)) > 0),
    supersedes_resolution_id TEXT REFERENCES source_package_mismatch_resolution(source_package_mismatch_resolution_id),
    recorded_at_utc TEXT NOT NULL,
    CHECK (supersedes_resolution_id IS NULL OR supersedes_resolution_id <> source_package_mismatch_resolution_id)
) STRICT;
CREATE VIEW v_current_source_package_mismatch_resolution AS
SELECT resolution.* FROM source_package_mismatch_resolution resolution
WHERE NOT EXISTS (SELECT 1 FROM source_package_mismatch_resolution newer
                  WHERE newer.supersedes_resolution_id = resolution.source_package_mismatch_resolution_id);
CREATE TRIGGER validate_source_package_mismatch_resolution_insert
BEFORE INSERT ON source_package_mismatch_resolution
BEGIN
    SELECT CASE WHEN NEW.supersedes_resolution_id IS NULL AND EXISTS (
        SELECT 1 FROM source_package_mismatch_resolution prior
        WHERE prior.source_package_mismatch_id = NEW.source_package_mismatch_id)
    THEN RAISE(ABORT, 'mismatch decision must supersede current resolution') END;
    SELECT CASE WHEN NEW.supersedes_resolution_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM v_current_source_package_mismatch_resolution prior
        WHERE prior.source_package_mismatch_resolution_id = NEW.supersedes_resolution_id
          AND prior.source_package_mismatch_id = NEW.source_package_mismatch_id)
    THEN RAISE(ABORT, 'mismatch resolution predecessor must be current') END;
END;
CREATE TRIGGER source_package_mismatch_no_update BEFORE UPDATE ON source_package_mismatch
BEGIN SELECT RAISE(ABORT, 'source_package_mismatch is immutable'); END;
CREATE TRIGGER source_package_mismatch_no_delete BEFORE DELETE ON source_package_mismatch
BEGIN SELECT RAISE(ABORT, 'source_package_mismatch cannot be deleted'); END;
CREATE TRIGGER source_package_mismatch_resolution_no_update BEFORE UPDATE ON source_package_mismatch_resolution
BEGIN SELECT RAISE(ABORT, 'source_package_mismatch_resolution is immutable'); END;
CREATE TRIGGER source_package_mismatch_resolution_no_delete BEFORE DELETE ON source_package_mismatch_resolution
BEGIN SELECT RAISE(ABORT, 'source_package_mismatch_resolution cannot be deleted'); END;
INSERT INTO schema_migration VALUES ('0038', 'source_package_mismatch', '__FORGE_MIGRATION_SHA256__',
    strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'schema-prototype-0.38');
COMMIT;
