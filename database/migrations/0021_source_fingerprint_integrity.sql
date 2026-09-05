-- Forge X layered source-fingerprint integrity
-- Source evidence identity and declared fingerprints are append-only.

PRAGMA foreign_keys = ON;

BEGIN IMMEDIATE;

CREATE INDEX idx_fingerprint_source_workbook
    ON fingerprint(entity_type, entity_id, purpose, algorithm);

CREATE INDEX idx_source_occurrence_logical_status
    ON source_occurrence(logical_fingerprint, terminal_status);

CREATE TRIGGER no_update_source_workbook
BEFORE UPDATE ON source_workbook
BEGIN
    SELECT RAISE(ABORT, 'source_workbook is immutable');
END;

CREATE TRIGGER no_delete_source_workbook
BEFORE DELETE ON source_workbook
BEGIN
    SELECT RAISE(ABORT, 'source_workbook cannot be deleted');
END;

CREATE TRIGGER no_update_source_worksheet
BEFORE UPDATE ON source_worksheet
BEGIN
    SELECT RAISE(ABORT, 'source_worksheet is immutable');
END;

CREATE TRIGGER no_delete_source_worksheet
BEFORE DELETE ON source_worksheet
BEGIN
    SELECT RAISE(ABORT, 'source_worksheet cannot be deleted');
END;

CREATE TRIGGER no_update_fingerprint
BEFORE UPDATE ON fingerprint
BEGIN
    SELECT RAISE(ABORT, 'fingerprint is immutable');
END;

CREATE TRIGGER no_delete_fingerprint
BEFORE DELETE ON fingerprint
BEGIN
    SELECT RAISE(ABORT, 'fingerprint cannot be deleted');
END;

INSERT INTO schema_migration VALUES (
    '0021', 'source_fingerprint_integrity', '__FORGE_MIGRATION_SHA256__',
    strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'schema-prototype-0.21'
);

COMMIT;
