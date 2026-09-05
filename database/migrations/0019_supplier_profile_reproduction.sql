-- Forge X immutable supplier-profile reproduction manifests

PRAGMA foreign_keys = ON;
BEGIN IMMEDIATE;

CREATE TABLE supplier_profile_reproduction_manifest (
    supplier_profile_reproduction_manifest_id TEXT PRIMARY KEY,
    supplier_profile_run_id TEXT NOT NULL UNIQUE REFERENCES supplier_profile_run(supplier_profile_run_id),
    active_window_start_utc TEXT NOT NULL,
    metric_manifest_hash TEXT NOT NULL CHECK (length(metric_manifest_hash) = 64),
    finding_manifest_hash TEXT NOT NULL CHECK (length(finding_manifest_hash) = 64),
    recorded_at_utc TEXT NOT NULL
) STRICT;

CREATE TRIGGER no_update_supplier_profile_reproduction_manifest
BEFORE UPDATE ON supplier_profile_reproduction_manifest
BEGIN SELECT RAISE(ABORT, 'supplier_profile_reproduction_manifest is immutable'); END;
CREATE TRIGGER no_delete_supplier_profile_reproduction_manifest
BEFORE DELETE ON supplier_profile_reproduction_manifest
BEGIN SELECT RAISE(ABORT, 'supplier_profile_reproduction_manifest cannot be deleted'); END;

INSERT INTO schema_migration VALUES (
    '0019', 'supplier_profile_reproduction', '__FORGE_MIGRATION_SHA256__',
    strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'schema-prototype-0.19'
);

COMMIT;
