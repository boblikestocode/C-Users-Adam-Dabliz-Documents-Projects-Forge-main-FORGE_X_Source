-- Forge X immutable historical-baseline completion summary
PRAGMA foreign_keys = ON;
BEGIN IMMEDIATE;
CREATE TABLE historical_baseline_completion_summary (
    historical_baseline_completion_summary_id TEXT PRIMARY KEY,
    import_transaction_id TEXT NOT NULL UNIQUE REFERENCES import_transaction(import_transaction_id),
    first_analyzed_date TEXT NOT NULL,
    imported_record_count INTEGER NOT NULL CHECK (imported_record_count >= 0),
    duplicate_count INTEGER NOT NULL CHECK (duplicate_count >= 0),
    incomplete_pbd_count INTEGER NOT NULL CHECK (incomplete_pbd_count >= 0),
    formula_exception_count INTEGER NOT NULL CHECK (formula_exception_count >= 0),
    staging_exception_count INTEGER NOT NULL CHECK (staging_exception_count >= 0),
    missing_field_payload TEXT NOT NULL,
    summary_hash TEXT NOT NULL CHECK (length(summary_hash) = 64),
    generated_at_utc TEXT NOT NULL
) STRICT;
CREATE TRIGGER historical_baseline_summary_no_update
BEFORE UPDATE ON historical_baseline_completion_summary
BEGIN SELECT RAISE(ABORT, 'historical_baseline_completion_summary is immutable'); END;
CREATE TRIGGER historical_baseline_summary_no_delete
BEFORE DELETE ON historical_baseline_completion_summary
BEGIN SELECT RAISE(ABORT, 'historical_baseline_completion_summary cannot be deleted'); END;
INSERT INTO schema_migration VALUES ('0041', 'historical_baseline_summary', '__FORGE_MIGRATION_SHA256__',
    strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'schema-prototype-0.41');
COMMIT;
