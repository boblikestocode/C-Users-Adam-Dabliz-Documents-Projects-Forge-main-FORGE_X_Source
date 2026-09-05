-- Forge X rebuildable supplier-family executive profile projection
PRAGMA foreign_keys = ON;
BEGIN IMMEDIATE;
CREATE TABLE supplier_family_profile_run (
    supplier_family_profile_run_id TEXT PRIMARY KEY,
    stable_family_id TEXT NOT NULL,
    family_name TEXT NOT NULL,
    registry_cache_generation_id TEXT NOT NULL REFERENCES registry_cache_generation(cache_generation_id),
    commodity_id TEXT NOT NULL,
    evidence_cutoff_utc TEXT NOT NULL,
    active_window_start_utc TEXT NOT NULL,
    eligibility_rule_version_id TEXT NOT NULL REFERENCES rule_version(rule_version_id),
    calculation_rule_version_id TEXT NOT NULL REFERENCES rule_version(rule_version_id),
    engine_version_id TEXT NOT NULL REFERENCES engine_version(engine_version_id),
    build_manifest_hash TEXT NOT NULL CHECK (length(build_manifest_hash) = 64),
    metric_manifest_hash TEXT NOT NULL CHECK (length(metric_manifest_hash) = 64),
    finding_manifest_hash TEXT NOT NULL CHECK (length(finding_manifest_hash) = 64),
    run_status TEXT NOT NULL CHECK (run_status = 'Complete'),
    started_at_utc TEXT NOT NULL,
    completed_at_utc TEXT NOT NULL
) STRICT;
CREATE TABLE supplier_family_profile_member (
    supplier_family_profile_member_id TEXT PRIMARY KEY,
    supplier_family_profile_run_id TEXT NOT NULL REFERENCES supplier_family_profile_run(supplier_family_profile_run_id),
    supplier_id TEXT NOT NULL,
    supplier_profile_run_id TEXT NOT NULL REFERENCES supplier_profile_run(supplier_profile_run_id),
    UNIQUE (supplier_family_profile_run_id, supplier_id),
    UNIQUE (supplier_family_profile_run_id, supplier_profile_run_id)
) STRICT;
CREATE TABLE supplier_family_profile_metric (
    supplier_family_profile_metric_id TEXT PRIMARY KEY,
    supplier_family_profile_run_id TEXT NOT NULL REFERENCES supplier_family_profile_run(supplier_family_profile_run_id),
    metric_code TEXT NOT NULL,
    decimal_coefficient TEXT NOT NULL,
    decimal_scale INTEGER NOT NULL CHECK (decimal_scale >= 0),
    evidence_count INTEGER NOT NULL CHECK (evidence_count >= 0),
    independent_event_count INTEGER NOT NULL CHECK (independent_event_count >= 0),
    confidence_classification TEXT NOT NULL,
    UNIQUE (supplier_family_profile_run_id, metric_code)
) STRICT;
CREATE TABLE supplier_family_profile_finding (
    supplier_family_profile_finding_id TEXT PRIMARY KEY,
    supplier_family_profile_run_id TEXT NOT NULL REFERENCES supplier_family_profile_run(supplier_family_profile_run_id),
    finding_type TEXT NOT NULL,
    finding_status TEXT NOT NULL,
    explanation_payload TEXT NOT NULL,
    evidence_count INTEGER NOT NULL CHECK (evidence_count >= 0),
    independent_event_count INTEGER NOT NULL CHECK (independent_event_count >= 0)
) STRICT;
CREATE VIEW v_latest_supplier_family_profile AS
SELECT run.* FROM supplier_family_profile_run run
WHERE NOT EXISTS (
    SELECT 1 FROM supplier_family_profile_run newer
    WHERE newer.stable_family_id = run.stable_family_id
      AND newer.commodity_id = run.commodity_id
      AND (newer.completed_at_utc > run.completed_at_utc OR
           (newer.completed_at_utc = run.completed_at_utc AND
            newer.supplier_family_profile_run_id > run.supplier_family_profile_run_id))
);
CREATE TRIGGER supplier_family_profile_run_no_update BEFORE UPDATE ON supplier_family_profile_run
BEGIN SELECT RAISE(ABORT, 'supplier_family_profile_run is immutable'); END;
CREATE TRIGGER supplier_family_profile_run_no_delete BEFORE DELETE ON supplier_family_profile_run
BEGIN SELECT RAISE(ABORT, 'supplier_family_profile_run cannot be deleted'); END;
CREATE TRIGGER supplier_family_profile_member_no_update BEFORE UPDATE ON supplier_family_profile_member
BEGIN SELECT RAISE(ABORT, 'supplier_family_profile_member is immutable'); END;
CREATE TRIGGER supplier_family_profile_member_no_delete BEFORE DELETE ON supplier_family_profile_member
BEGIN SELECT RAISE(ABORT, 'supplier_family_profile_member cannot be deleted'); END;
CREATE TRIGGER supplier_family_profile_metric_no_update BEFORE UPDATE ON supplier_family_profile_metric
BEGIN SELECT RAISE(ABORT, 'supplier_family_profile_metric is immutable'); END;
CREATE TRIGGER supplier_family_profile_metric_no_delete BEFORE DELETE ON supplier_family_profile_metric
BEGIN SELECT RAISE(ABORT, 'supplier_family_profile_metric cannot be deleted'); END;
CREATE TRIGGER supplier_family_profile_finding_no_update BEFORE UPDATE ON supplier_family_profile_finding
BEGIN SELECT RAISE(ABORT, 'supplier_family_profile_finding is immutable'); END;
CREATE TRIGGER supplier_family_profile_finding_no_delete BEFORE DELETE ON supplier_family_profile_finding
BEGIN SELECT RAISE(ABORT, 'supplier_family_profile_finding cannot be deleted'); END;
INSERT INTO schema_migration VALUES ('0040', 'supplier_family_profile', '__FORGE_MIGRATION_SHA256__',
    strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'schema-prototype-0.40');
COMMIT;
