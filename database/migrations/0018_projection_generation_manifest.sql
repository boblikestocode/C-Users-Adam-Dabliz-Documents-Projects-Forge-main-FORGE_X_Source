-- Forge X atomic projection-generation manifests

PRAGMA foreign_keys = ON;
BEGIN IMMEDIATE;

CREATE TABLE projection_generation_manifest (
    projection_generation_id TEXT PRIMARY KEY,
    projection_type TEXT NOT NULL CHECK (projection_type IN ('Active Round Part')),
    event_id TEXT NOT NULL REFERENCES sourcing_event(event_id),
    evidence_cutoff_utc TEXT NOT NULL,
    build_manifest_hash TEXT NOT NULL CHECK (length(build_manifest_hash) = 64),
    projected_row_count INTEGER NOT NULL CHECK (projected_row_count >= 0),
    generation_status TEXT NOT NULL CHECK (generation_status IN ('Complete')),
    created_at_utc TEXT NOT NULL
) STRICT;

CREATE INDEX ix_projection_generation_event
ON projection_generation_manifest(projection_type, event_id, evidence_cutoff_utc DESC);

CREATE TRIGGER no_update_projection_generation_manifest
BEFORE UPDATE ON projection_generation_manifest
BEGIN SELECT RAISE(ABORT, 'projection_generation_manifest is immutable'); END;
CREATE TRIGGER no_delete_projection_generation_manifest
BEFORE DELETE ON projection_generation_manifest
BEGIN SELECT RAISE(ABORT, 'projection_generation_manifest cannot be deleted'); END;

INSERT INTO schema_migration VALUES (
    '0018', 'projection_generation_manifest', '__FORGE_MIGRATION_SHA256__',
    strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'schema-prototype-0.18'
);

COMMIT;
