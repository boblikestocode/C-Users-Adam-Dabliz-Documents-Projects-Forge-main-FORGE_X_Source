PRAGMA foreign_keys = ON;
BEGIN IMMEDIATE;

CREATE TABLE workbook_extraction_receipt (
    workbook_id TEXT PRIMARY KEY REFERENCES source_workbook(workbook_id),
    adapter_version TEXT NOT NULL,
    manifest_payload TEXT NOT NULL,
    manifest_hash TEXT NOT NULL CHECK (length(manifest_hash) = 64),
    recorded_at_utc TEXT NOT NULL
) STRICT;
CREATE TABLE worksheet_extraction_receipt (
    occurrence_id TEXT PRIMARY KEY REFERENCES source_occurrence(occurrence_id),
    workbook_id TEXT NOT NULL REFERENCES workbook_extraction_receipt(workbook_id),
    extraction_payload TEXT NOT NULL,
    extraction_hash TEXT NOT NULL CHECK (length(extraction_hash) = 64),
    recorded_at_utc TEXT NOT NULL
) STRICT;
CREATE INDEX ix_worksheet_extraction_workbook ON worksheet_extraction_receipt(workbook_id);
CREATE TRIGGER no_update_workbook_extraction BEFORE UPDATE ON workbook_extraction_receipt
BEGIN SELECT RAISE(ABORT, 'workbook_extraction_receipt is immutable'); END;
CREATE TRIGGER no_delete_workbook_extraction BEFORE DELETE ON workbook_extraction_receipt
BEGIN SELECT RAISE(ABORT, 'workbook_extraction_receipt cannot be deleted'); END;
CREATE TRIGGER no_update_worksheet_extraction BEFORE UPDATE ON worksheet_extraction_receipt
BEGIN SELECT RAISE(ABORT, 'worksheet_extraction_receipt is immutable'); END;
CREATE TRIGGER no_delete_worksheet_extraction BEFORE DELETE ON worksheet_extraction_receipt
BEGIN SELECT RAISE(ABORT, 'worksheet_extraction_receipt cannot be deleted'); END;

INSERT INTO schema_migration VALUES
('0052', 'workbook_extraction_receipts', '__FORGE_MIGRATION_SHA256__',
 strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'schema-prototype-0.52');
COMMIT;
