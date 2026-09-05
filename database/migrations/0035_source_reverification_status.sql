-- Forge X external source re-verification status history
PRAGMA foreign_keys = ON;
BEGIN IMMEDIATE;
CREATE TABLE source_workbook_availability_event (
    source_workbook_availability_event_id TEXT PRIMARY KEY,
    workbook_id TEXT NOT NULL REFERENCES source_workbook(workbook_id),
    availability_status TEXT NOT NULL CHECK (availability_status IN
        ('Unknown', 'Available Verified', 'Unavailable', 'Hash Mismatch')),
    observed_file_hash_sha256 TEXT CHECK (observed_file_hash_sha256 IS NULL OR length(observed_file_hash_sha256) = 64),
    verification_method TEXT NOT NULL,
    checked_by_user_id TEXT,
    status_detail TEXT NOT NULL,
    supersedes_availability_event_id TEXT REFERENCES source_workbook_availability_event(source_workbook_availability_event_id),
    checked_at_utc TEXT NOT NULL,
    CHECK ((availability_status IN ('Available Verified', 'Hash Mismatch')) = (observed_file_hash_sha256 IS NOT NULL)),
    CHECK (supersedes_availability_event_id IS NULL OR supersedes_availability_event_id <> source_workbook_availability_event_id)
) STRICT;
INSERT INTO source_workbook_availability_event
SELECT 'initial-availability-' || workbook_id, workbook_id, 'Unknown', NULL,
       'Migration', NULL, 'External source has not yet been re-verified', NULL, recorded_at_utc
FROM source_workbook;
CREATE VIEW v_current_source_workbook_availability AS
SELECT availability.* FROM source_workbook_availability_event availability
WHERE NOT EXISTS (SELECT 1 FROM source_workbook_availability_event newer
    WHERE newer.supersedes_availability_event_id = availability.source_workbook_availability_event_id);
CREATE VIEW v_source_workbook_reverification AS
SELECT workbook.workbook_id, workbook.submitted_filename, workbook.source_locator,
       workbook.file_hash_sha256, availability.availability_status,
       availability.observed_file_hash_sha256, availability.verification_method,
       availability.checked_by_user_id, availability.status_detail, availability.checked_at_utc
FROM source_workbook workbook JOIN v_current_source_workbook_availability availability
  ON availability.workbook_id = workbook.workbook_id;
CREATE INDEX ix_source_workbook_availability_current ON source_workbook_availability_event(workbook_id, checked_at_utc DESC);
CREATE TRIGGER no_update_source_workbook_availability BEFORE UPDATE ON source_workbook_availability_event
BEGIN SELECT RAISE(ABORT, 'source_workbook_availability_event is immutable'); END;
CREATE TRIGGER no_delete_source_workbook_availability BEFORE DELETE ON source_workbook_availability_event
BEGIN SELECT RAISE(ABORT, 'source_workbook_availability_event cannot be deleted'); END;
INSERT INTO schema_migration VALUES ('0035', 'source_reverification_status', '__FORGE_MIGRATION_SHA256__',
    strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'schema-prototype-0.35');
COMMIT;
