-- Forge X immutable baseline-to-future observation comparisons
PRAGMA foreign_keys = ON;
BEGIN IMMEDIATE;
CREATE TABLE historical_baseline_observation_comparison (
    historical_baseline_observation_comparison_id TEXT PRIMARY KEY,
    historical_baseline_completion_summary_id TEXT NOT NULL REFERENCES historical_baseline_completion_summary(historical_baseline_completion_summary_id),
    baseline_observation_id TEXT NOT NULL REFERENCES pbd_observation(observation_id),
    later_observation_id TEXT NOT NULL REFERENCES pbd_observation(observation_id),
    supplier_id TEXT NOT NULL,
    part_id TEXT NOT NULL,
    baseline_structure_category TEXT NOT NULL,
    later_structure_category TEXT NOT NULL,
    structure_trend TEXT NOT NULL CHECK (structure_trend IN ('Improved', 'Unchanged', 'Deteriorated')),
    added_field_payload TEXT NOT NULL,
    removed_field_payload TEXT NOT NULL,
    field_coverage_trend TEXT NOT NULL CHECK (field_coverage_trend IN ('Improved', 'Unchanged', 'Deteriorated', 'Mixed')),
    baseline_formula_exception_count INTEGER NOT NULL CHECK (baseline_formula_exception_count >= 0),
    later_formula_exception_count INTEGER NOT NULL CHECK (later_formula_exception_count >= 0),
    formula_integrity_trend TEXT NOT NULL CHECK (formula_integrity_trend IN ('Improved', 'Unchanged', 'Deteriorated')),
    comparison_rule_version_id TEXT NOT NULL REFERENCES rule_version(rule_version_id),
    comparison_hash TEXT NOT NULL CHECK (length(comparison_hash) = 64),
    generated_at_utc TEXT NOT NULL,
    UNIQUE (historical_baseline_completion_summary_id, later_observation_id),
    CHECK (baseline_observation_id <> later_observation_id)
) STRICT;
CREATE TRIGGER historical_baseline_comparison_no_update
BEFORE UPDATE ON historical_baseline_observation_comparison
BEGIN SELECT RAISE(ABORT, 'historical_baseline_observation_comparison is immutable'); END;
CREATE TRIGGER historical_baseline_comparison_no_delete
BEFORE DELETE ON historical_baseline_observation_comparison
BEGIN SELECT RAISE(ABORT, 'historical_baseline_observation_comparison cannot be deleted'); END;
INSERT INTO schema_migration VALUES ('0042', 'historical_baseline_comparison', '__FORGE_MIGRATION_SHA256__',
    strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'schema-prototype-0.42');
COMMIT;
