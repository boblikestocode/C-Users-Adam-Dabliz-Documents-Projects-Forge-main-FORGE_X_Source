-- Forge X exact cross-border operation matching

PRAGMA foreign_keys = ON;
BEGIN IMMEDIATE;

CREATE TABLE cross_border_operation_evidence (
    cross_border_operation_evidence_id TEXT PRIMARY KEY,
    observation_id TEXT NOT NULL REFERENCES pbd_observation(observation_id),
    operation_line_id TEXT NOT NULL REFERENCES operation_line(operation_line_id),
    region_code TEXT NOT NULL CHECK (region_code IN ('US', 'MX')),
    normalized_operation TEXT NOT NULL,
    equipment_identifier TEXT,
    normalized_equipment_description TEXT,
    equipment_size_or_capacity TEXT,
    process_type TEXT,
    unit_basis TEXT NOT NULL,
    burden_rate_coefficient TEXT NOT NULL,
    burden_rate_scale INTEGER NOT NULL CHECK (burden_rate_scale >= 0),
    burden_hours_coefficient TEXT NOT NULL,
    burden_hours_scale INTEGER NOT NULL CHECK (burden_hours_scale >= 0),
    normalized_rate_unit_id TEXT NOT NULL,
    currency_id TEXT NOT NULL,
    recorded_at_utc TEXT NOT NULL,
    UNIQUE (observation_id, operation_line_id)
) STRICT;

CREATE TABLE cross_border_operation_match (
    cross_border_operation_match_id TEXT PRIMARY KEY,
    cross_border_pair_id TEXT NOT NULL REFERENCES cross_border_exact_part_pair(cross_border_pair_id),
    us_operation_evidence_id TEXT NOT NULL REFERENCES cross_border_operation_evidence(cross_border_operation_evidence_id),
    mx_operation_evidence_id TEXT NOT NULL REFERENCES cross_border_operation_evidence(cross_border_operation_evidence_id),
    match_classification TEXT NOT NULL CHECK (match_classification IN ('Exact Match', 'Reference Only')),
    match_reason TEXT NOT NULL,
    burden_opportunity_coefficient TEXT,
    burden_opportunity_scale INTEGER CHECK (burden_opportunity_scale IS NULL OR burden_opportunity_scale >= 0),
    matching_rule_version_id TEXT NOT NULL REFERENCES rule_version(rule_version_id),
    recorded_at_utc TEXT NOT NULL,
    UNIQUE (cross_border_pair_id, us_operation_evidence_id, mx_operation_evidence_id),
    CHECK ((match_classification = 'Exact Match') = (burden_opportunity_coefficient IS NOT NULL)),
    CHECK ((burden_opportunity_coefficient IS NULL) = (burden_opportunity_scale IS NULL))
) STRICT;

CREATE INDEX ix_cross_border_operation_signature
ON cross_border_operation_evidence(region_code, normalized_operation,
    equipment_identifier, normalized_equipment_description,
    equipment_size_or_capacity, process_type, unit_basis);

CREATE TRIGGER no_update_cross_border_operation_evidence BEFORE UPDATE ON cross_border_operation_evidence
BEGIN SELECT RAISE(ABORT, 'cross_border_operation_evidence is immutable'); END;
CREATE TRIGGER no_delete_cross_border_operation_evidence BEFORE DELETE ON cross_border_operation_evidence
BEGIN SELECT RAISE(ABORT, 'cross_border_operation_evidence cannot be deleted'); END;
CREATE TRIGGER no_update_cross_border_operation_match BEFORE UPDATE ON cross_border_operation_match
BEGIN SELECT RAISE(ABORT, 'cross_border_operation_match is immutable'); END;
CREATE TRIGGER no_delete_cross_border_operation_match BEFORE DELETE ON cross_border_operation_match
BEGIN SELECT RAISE(ABORT, 'cross_border_operation_match cannot be deleted'); END;

INSERT INTO schema_migration VALUES (
    '0012', 'cross_border_operations', '__FORGE_MIGRATION_SHA256__',
    strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'schema-prototype-0.12'
);

COMMIT;
