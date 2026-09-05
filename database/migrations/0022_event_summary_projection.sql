-- Forge X atomic, reproducible event-summary projection generations

PRAGMA foreign_keys = ON;
BEGIN IMMEDIATE;

CREATE TABLE event_summary_generation_manifest (
    projection_generation_id TEXT PRIMARY KEY,
    evidence_cutoff_utc TEXT NOT NULL,
    build_manifest_hash TEXT NOT NULL CHECK (length(build_manifest_hash) = 64),
    projected_row_count INTEGER NOT NULL CHECK (projected_row_count >= 0),
    generation_status TEXT NOT NULL CHECK (generation_status = 'Complete'),
    created_at_utc TEXT NOT NULL
) STRICT;

CREATE INDEX ix_event_summary_generation_cutoff
ON event_summary_generation_manifest(evidence_cutoff_utc DESC);

CREATE TRIGGER no_update_event_summary_generation
BEFORE UPDATE ON event_summary_generation_manifest
BEGIN SELECT RAISE(ABORT, 'event_summary_generation_manifest is immutable'); END;
CREATE TRIGGER no_delete_event_summary_generation
BEFORE DELETE ON event_summary_generation_manifest
BEGIN SELECT RAISE(ABORT, 'event_summary_generation_manifest cannot be deleted'); END;
CREATE TRIGGER no_update_event_summary_projection
BEFORE UPDATE ON event_summary_projection
BEGIN SELECT RAISE(ABORT, 'event_summary_projection is immutable'); END;
CREATE TRIGGER no_delete_event_summary_projection
BEFORE DELETE ON event_summary_projection
BEGIN SELECT RAISE(ABORT, 'event_summary_projection cannot be deleted'); END;

INSERT INTO schema_migration VALUES (
    '0022', 'event_summary_projection', '__FORGE_MIGRATION_SHA256__',
    strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'schema-prototype-0.22'
);

COMMIT;
