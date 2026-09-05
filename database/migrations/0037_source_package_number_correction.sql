-- Forge X source-package number correction history
PRAGMA foreign_keys = ON;
BEGIN IMMEDIATE;
CREATE TABLE source_package_number_correction (
    source_package_number_correction_id TEXT PRIMARY KEY,
    source_package_id TEXT NOT NULL REFERENCES source_package(source_package_id),
    original_normalized_package_number TEXT NOT NULL,
    original_displayed_package_number TEXT NOT NULL,
    corrected_normalized_package_number TEXT NOT NULL,
    corrected_displayed_package_number TEXT NOT NULL,
    correction_reason TEXT NOT NULL CHECK (length(trim(correction_reason)) > 0),
    corrected_by_user_id TEXT NOT NULL CHECK (length(trim(corrected_by_user_id)) > 0),
    supersedes_correction_id TEXT REFERENCES source_package_number_correction(source_package_number_correction_id),
    recorded_at_utc TEXT NOT NULL,
    CHECK (length(trim(corrected_normalized_package_number)) > 0),
    CHECK (length(trim(corrected_displayed_package_number)) > 0),
    CHECK (supersedes_correction_id IS NULL OR supersedes_correction_id <> source_package_number_correction_id)
) STRICT;

CREATE VIEW v_current_source_package_identity AS
SELECT package.source_package_id, package.event_id,
       COALESCE(correction.corrected_normalized_package_number, package.normalized_package_number) AS normalized_package_number,
       COALESCE(correction.corrected_displayed_package_number, package.displayed_package_number) AS displayed_package_number,
       package.package_role, package.recorded_at_utc,
       correction.source_package_number_correction_id,
       correction.recorded_at_utc AS corrected_at_utc
FROM source_package package
LEFT JOIN source_package_number_correction correction
  ON correction.source_package_id = package.source_package_id
 AND NOT EXISTS (
     SELECT 1 FROM source_package_number_correction newer
     WHERE newer.supersedes_correction_id = correction.source_package_number_correction_id
 );

CREATE TRIGGER validate_source_package_number_correction_insert
BEFORE INSERT ON source_package_number_correction
BEGIN
    SELECT CASE WHEN EXISTS (
        SELECT 1 FROM source_package other
        WHERE other.source_package_id <> NEW.source_package_id
          AND other.normalized_package_number = NEW.corrected_normalized_package_number
    ) THEN RAISE(ABORT, 'corrected source package number already exists') END;
    SELECT CASE WHEN EXISTS (
        SELECT 1 FROM source_package_number_correction other
        WHERE other.source_package_id <> NEW.source_package_id
          AND other.corrected_normalized_package_number = NEW.corrected_normalized_package_number
          AND NOT EXISTS (SELECT 1 FROM source_package_number_correction newer
                          WHERE newer.supersedes_correction_id = other.source_package_number_correction_id)
    ) THEN RAISE(ABORT, 'corrected source package number already exists') END;
    SELECT CASE WHEN NEW.supersedes_correction_id IS NULL AND EXISTS (
        SELECT 1 FROM source_package_number_correction prior
        WHERE prior.source_package_id = NEW.source_package_id
    ) THEN RAISE(ABORT, 'correction must supersede current source package correction') END;
    SELECT CASE WHEN NEW.supersedes_correction_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM source_package_number_correction prior
        WHERE prior.source_package_number_correction_id = NEW.supersedes_correction_id
          AND prior.source_package_id = NEW.source_package_id
          AND NOT EXISTS (SELECT 1 FROM source_package_number_correction newer
                          WHERE newer.supersedes_correction_id = prior.source_package_number_correction_id)
    ) THEN RAISE(ABORT, 'correction predecessor must be current for source package') END;
END;

CREATE TRIGGER source_package_number_correction_no_update
BEFORE UPDATE ON source_package_number_correction
BEGIN SELECT RAISE(ABORT, 'source_package_number_correction is append-only'); END;

CREATE TRIGGER source_package_number_correction_no_delete
BEFORE DELETE ON source_package_number_correction
BEGIN SELECT RAISE(ABORT, 'source_package_number_correction is append-only'); END;

INSERT INTO schema_migration VALUES ('0037', 'source_package_number_correction', '__FORGE_MIGRATION_SHA256__',
    strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'schema-prototype-0.37');
COMMIT;
