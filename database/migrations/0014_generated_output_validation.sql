-- Forge X generated-output contracts and append-only validation history

PRAGMA foreign_keys = ON;
BEGIN IMMEDIATE;

CREATE TABLE generated_output_artifact (
    generated_output_artifact_id TEXT PRIMARY KEY,
    finalized_snapshot_id TEXT NOT NULL REFERENCES finalized_snapshot(finalized_snapshot_id),
    artifact_type TEXT NOT NULL CHECK (
        artifact_type IN ('Buyer Workbook', 'Supporting Export')
    ),
    relative_filename TEXT NOT NULL,
    content_hash_sha256 TEXT NOT NULL CHECK (length(content_hash_sha256) = 64),
    size_bytes INTEGER NOT NULL CHECK (size_bytes > 0),
    output_contract_hash TEXT NOT NULL CHECK (length(output_contract_hash) = 64),
    evidence_manifest_hash TEXT NOT NULL CHECK (length(evidence_manifest_hash) = 64),
    calculation_output_manifest_hash TEXT NOT NULL CHECK (
        length(calculation_output_manifest_hash) = 64
    ),
    renderer_version TEXT NOT NULL,
    created_by_user_id TEXT NOT NULL,
    created_at_utc TEXT NOT NULL,
    UNIQUE (finalized_snapshot_id, artifact_type, content_hash_sha256)
) STRICT;

CREATE TABLE generated_output_validation_event (
    generated_output_validation_event_id TEXT PRIMARY KEY,
    generated_output_artifact_id TEXT NOT NULL REFERENCES generated_output_artifact(generated_output_artifact_id),
    validation_status TEXT NOT NULL CHECK (validation_status IN ('Verified', 'Failed')),
    observed_content_hash_sha256 TEXT NOT NULL CHECK (
        length(observed_content_hash_sha256) = 64
    ),
    validation_detail TEXT NOT NULL,
    validated_at_utc TEXT NOT NULL
) STRICT;

CREATE INDEX ix_generated_output_snapshot
ON generated_output_artifact(finalized_snapshot_id, artifact_type, created_at_utc DESC);

CREATE INDEX ix_generated_output_validation
ON generated_output_validation_event(generated_output_artifact_id, validated_at_utc DESC);

CREATE TRIGGER no_update_generated_output_artifact BEFORE UPDATE ON generated_output_artifact
BEGIN SELECT RAISE(ABORT, 'generated_output_artifact is immutable'); END;
CREATE TRIGGER no_delete_generated_output_artifact BEFORE DELETE ON generated_output_artifact
BEGIN SELECT RAISE(ABORT, 'generated_output_artifact cannot be deleted'); END;
CREATE TRIGGER no_update_generated_output_validation BEFORE UPDATE ON generated_output_validation_event
BEGIN SELECT RAISE(ABORT, 'generated_output_validation_event is immutable'); END;
CREATE TRIGGER no_delete_generated_output_validation BEFORE DELETE ON generated_output_validation_event
BEGIN SELECT RAISE(ABORT, 'generated_output_validation_event cannot be deleted'); END;

INSERT INTO schema_migration VALUES (
    '0014', 'generated_output_validation', '__FORGE_MIGRATION_SHA256__',
    strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'schema-prototype-0.14'
);

COMMIT;
