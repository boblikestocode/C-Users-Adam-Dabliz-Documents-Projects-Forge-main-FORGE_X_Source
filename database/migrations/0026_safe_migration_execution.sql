-- Forge X safe migration execution evidence

PRAGMA foreign_keys = ON;
BEGIN IMMEDIATE;

CREATE TABLE schema_migration_execution (
    schema_migration_execution_id TEXT PRIMARY KEY,
    from_version TEXT,
    to_version TEXT NOT NULL,
    applied_migration_count INTEGER NOT NULL CHECK (applied_migration_count > 0),
    preflight_database_hash TEXT NOT NULL CHECK (length(preflight_database_hash) = 64),
    recovery_checkpoint_path TEXT,
    recovery_checkpoint_hash TEXT,
    execution_status TEXT NOT NULL CHECK (execution_status = 'Verified'),
    started_at_utc TEXT NOT NULL,
    completed_at_utc TEXT NOT NULL,
    CHECK ((recovery_checkpoint_path IS NULL) = (recovery_checkpoint_hash IS NULL))
) STRICT;

CREATE TRIGGER no_update_schema_migration_execution
BEFORE UPDATE ON schema_migration_execution
BEGIN SELECT RAISE(ABORT, 'schema_migration_execution is immutable'); END;
CREATE TRIGGER no_delete_schema_migration_execution
BEFORE DELETE ON schema_migration_execution
BEGIN SELECT RAISE(ABORT, 'schema_migration_execution cannot be deleted'); END;

INSERT INTO schema_migration VALUES (
    '0026', 'safe_migration_execution', '__FORGE_MIGRATION_SHA256__',
    strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'schema-prototype-0.26'
);

COMMIT;
