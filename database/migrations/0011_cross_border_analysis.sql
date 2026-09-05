-- Forge X exact-part cross-border evidence and reconstruction

PRAGMA foreign_keys = ON;
BEGIN IMMEDIATE;

CREATE TABLE location_measure_evidence (
    location_measure_evidence_id TEXT PRIMARY KEY,
    observation_id TEXT NOT NULL REFERENCES pbd_observation(observation_id),
    source_datum_id TEXT NOT NULL REFERENCES source_datum(source_datum_id),
    region_code TEXT NOT NULL CHECK (region_code IN ('US', 'MX')),
    measure_code TEXT NOT NULL CHECK (measure_code IN (
        'PIECE_PRICE', 'RAW_MATERIAL', 'PURCHASED_COMPONENTS', 'LABOR_HOURS',
        'LABOR_RATE', 'LABOR_DOLLARS', 'BURDEN_DOLLARS', 'OTHER_PHYSICAL_COST',
        'OVERHEAD_PERCENT', 'PROFIT_PERCENT', 'TOTAL_CONVERSION_COST'
    )),
    submitted_lexeme TEXT NOT NULL,
    decimal_coefficient TEXT NOT NULL,
    decimal_scale INTEGER NOT NULL CHECK (decimal_scale >= 0),
    normalized_unit_id TEXT NOT NULL,
    currency_id TEXT,
    calculation_basis_code TEXT,
    evidence_status TEXT NOT NULL CHECK (evidence_status IN ('Valid', 'Reference Only', 'Blocked')),
    recorded_at_utc TEXT NOT NULL,
    UNIQUE (observation_id, measure_code)
) STRICT;

CREATE TABLE cross_border_exact_part_pair (
    cross_border_pair_id TEXT PRIMARY KEY,
    part_id TEXT NOT NULL,
    us_observation_id TEXT NOT NULL REFERENCES pbd_observation(observation_id),
    mx_observation_id TEXT NOT NULL REFERENCES pbd_observation(observation_id),
    pairing_rule_version_id TEXT NOT NULL REFERENCES rule_version(rule_version_id),
    recorded_at_utc TEXT NOT NULL,
    CHECK (us_observation_id <> mx_observation_id),
    UNIQUE (us_observation_id, mx_observation_id, pairing_rule_version_id)
) STRICT;

CREATE TABLE cross_border_reconstruction (
    cross_border_reconstruction_id TEXT PRIMARY KEY,
    cross_border_pair_id TEXT NOT NULL REFERENCES cross_border_exact_part_pair(cross_border_pair_id),
    benchmark_evidence_id TEXT REFERENCES location_measure_evidence(location_measure_evidence_id),
    calculation_rule_version_id TEXT NOT NULL REFERENCES rule_version(rule_version_id),
    engine_version_id TEXT NOT NULL REFERENCES engine_version(engine_version_id),
    confidence_classification TEXT NOT NULL CHECK (confidence_classification IN (
        'Confirmed', 'Directional Estimate - Low Confidence'
    )),
    input_manifest_payload TEXT NOT NULL,
    missing_evidence_payload TEXT NOT NULL,
    result_payload TEXT NOT NULL,
    calculated_at_utc TEXT NOT NULL
) STRICT;

CREATE INDEX ix_location_measure_benchmark
ON location_measure_evidence(region_code, measure_code, evidence_status,
                             normalized_unit_id, currency_id);
CREATE INDEX ix_cross_border_pair_part
ON cross_border_exact_part_pair(part_id, recorded_at_utc);

CREATE TRIGGER no_update_location_measure BEFORE UPDATE ON location_measure_evidence
BEGIN SELECT RAISE(ABORT, 'location_measure_evidence is immutable'); END;
CREATE TRIGGER no_delete_location_measure BEFORE DELETE ON location_measure_evidence
BEGIN SELECT RAISE(ABORT, 'location_measure_evidence cannot be deleted'); END;
CREATE TRIGGER no_update_cross_border_pair BEFORE UPDATE ON cross_border_exact_part_pair
BEGIN SELECT RAISE(ABORT, 'cross_border_exact_part_pair is immutable'); END;
CREATE TRIGGER no_delete_cross_border_pair BEFORE DELETE ON cross_border_exact_part_pair
BEGIN SELECT RAISE(ABORT, 'cross_border_exact_part_pair cannot be deleted'); END;
CREATE TRIGGER no_update_cross_border_reconstruction BEFORE UPDATE ON cross_border_reconstruction
BEGIN SELECT RAISE(ABORT, 'cross_border_reconstruction is immutable'); END;
CREATE TRIGGER no_delete_cross_border_reconstruction BEFORE DELETE ON cross_border_reconstruction
BEGIN SELECT RAISE(ABORT, 'cross_border_reconstruction cannot be deleted'); END;

INSERT INTO schema_migration VALUES (
    '0011', 'cross_border_analysis', '__FORGE_MIGRATION_SHA256__',
    strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'schema-prototype-0.11'
);

COMMIT;
