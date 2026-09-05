-- Forge X immutable checkpoint verification and reproduction manifests

PRAGMA foreign_keys = ON;
BEGIN IMMEDIATE;

CREATE TABLE local_checkpoint_verification_manifest (
    local_checkpoint_verification_manifest_id TEXT PRIMARY KEY,
    local_checkpoint_id TEXT NOT NULL UNIQUE REFERENCES local_checkpoint(local_checkpoint_id),
    manifest_payload TEXT NOT NULL,
    manifest_hash TEXT NOT NULL CHECK (length(manifest_hash) = 64),
    representative_reproduction_hash TEXT NOT NULL CHECK (
        length(representative_reproduction_hash) = 64
    ),
    audit_event_sequence INTEGER NOT NULL CHECK (audit_event_sequence >= 0),
    record_count_payload TEXT NOT NULL,
    registry_version_payload TEXT NOT NULL,
    recorded_at_utc TEXT NOT NULL
) STRICT;

CREATE TRIGGER no_update_checkpoint_verification_manifest
BEFORE UPDATE ON local_checkpoint_verification_manifest
BEGIN SELECT RAISE(ABORT, 'local_checkpoint_verification_manifest is immutable'); END;
CREATE TRIGGER no_delete_checkpoint_verification_manifest
BEFORE DELETE ON local_checkpoint_verification_manifest
BEGIN SELECT RAISE(ABORT, 'local_checkpoint_verification_manifest cannot be deleted'); END;

INSERT INTO schema_migration VALUES (
    '0017', 'checkpoint_verification_manifest', '__FORGE_MIGRATION_SHA256__',
    strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'schema-prototype-0.17'
);

COMMIT;
