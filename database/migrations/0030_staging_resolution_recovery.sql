-- Forge X append-only staging resolution and blocked-observation recovery

PRAGMA foreign_keys = ON;
BEGIN IMMEDIATE;

ALTER TABLE staging_resolution
ADD COLUMN supersedes_staging_resolution_id TEXT
    REFERENCES staging_resolution(staging_resolution_id);

CREATE VIEW v_current_staging_resolution AS
SELECT resolution.*
FROM staging_resolution resolution
WHERE NOT EXISTS (
    SELECT 1 FROM staging_resolution newer
    WHERE newer.supersedes_staging_resolution_id = resolution.staging_resolution_id
);

CREATE INDEX ix_staging_resolution_current
ON staging_resolution(staging_issue_id, recorded_at_utc DESC);

CREATE TRIGGER no_update_staging_issue BEFORE UPDATE ON staging_issue
BEGIN SELECT RAISE(ABORT, 'staging_issue is immutable'); END;
CREATE TRIGGER no_delete_staging_issue BEFORE DELETE ON staging_issue
BEGIN SELECT RAISE(ABORT, 'staging_issue cannot be deleted'); END;
CREATE TRIGGER no_update_staging_resolution BEFORE UPDATE ON staging_resolution
BEGIN SELECT RAISE(ABORT, 'staging_resolution is immutable'); END;
CREATE TRIGGER no_delete_staging_resolution BEFORE DELETE ON staging_resolution
BEGIN SELECT RAISE(ABORT, 'staging_resolution cannot be deleted'); END;

INSERT INTO schema_migration VALUES (
    '0030', 'staging_resolution_recovery', '__FORGE_MIGRATION_SHA256__',
    strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'schema-prototype-0.30'
);

COMMIT;
