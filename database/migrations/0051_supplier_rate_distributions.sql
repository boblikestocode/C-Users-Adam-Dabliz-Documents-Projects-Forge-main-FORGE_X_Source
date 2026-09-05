PRAGMA foreign_keys = ON;
BEGIN IMMEDIATE;

-- Confirmed comparability is separate from immutable submitted rate evidence.
CREATE TABLE supplier_rate_interpretation (
    interpretation_id TEXT PRIMARY KEY,
    submitted_datum_id TEXT NOT NULL REFERENCES submitted_datum(submitted_datum_id),
    region_code TEXT NOT NULL CHECK (length(trim(region_code)) > 0),
    rate_category_code TEXT NOT NULL CHECK (rate_category_code IN
        ('LABOR_RATE', 'FACTORY_OVERHEAD_PERCENT', 'HEAD_OFFICE_OVERHEAD_PERCENT',
         'R_AND_D_OVERHEAD_PERCENT', 'OTHER_OVERHEAD_PERCENT', 'PROFIT_PERCENT')),
    comparison_basis_code TEXT NOT NULL CHECK (length(trim(comparison_basis_code)) > 0),
    confirmed_by_user_id TEXT NOT NULL,
    confirmation_reason TEXT NOT NULL CHECK (length(trim(confirmation_reason)) > 0),
    supersedes_interpretation_id TEXT UNIQUE REFERENCES supplier_rate_interpretation(interpretation_id),
    recorded_at_utc TEXT NOT NULL
) STRICT;
CREATE UNIQUE INDEX ix_rate_initial_interpretation
ON supplier_rate_interpretation(submitted_datum_id) WHERE supersedes_interpretation_id IS NULL;
CREATE INDEX ix_rate_interpretation_cutoff
ON supplier_rate_interpretation(submitted_datum_id, recorded_at_utc);
CREATE TRIGGER rate_interpretation_chain BEFORE INSERT ON supplier_rate_interpretation
WHEN NEW.supersedes_interpretation_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM supplier_rate_interpretation prior
    WHERE prior.interpretation_id = NEW.supersedes_interpretation_id
      AND prior.submitted_datum_id = NEW.submitted_datum_id
      AND prior.recorded_at_utc <= NEW.recorded_at_utc
)
BEGIN SELECT RAISE(ABORT, 'Rate interpretation requires a non-regressing same-datum predecessor'); END;
CREATE TRIGGER no_update_supplier_rate_interpretation BEFORE UPDATE ON supplier_rate_interpretation
BEGIN SELECT RAISE(ABORT, 'supplier_rate_interpretation is immutable'); END;
CREATE TRIGGER no_delete_supplier_rate_interpretation BEFORE DELETE ON supplier_rate_interpretation
BEGIN SELECT RAISE(ABORT, 'supplier_rate_interpretation cannot be deleted'); END;

CREATE TABLE supplier_rate_distribution_run (
    distribution_run_id TEXT PRIMARY KEY,
    supplier_id TEXT NOT NULL,
    commodity_id TEXT NOT NULL,
    region_code TEXT NOT NULL,
    supplier_plant_id TEXT,
    evidence_cutoff_utc TEXT NOT NULL,
    calculation_rule_version_id TEXT NOT NULL REFERENCES rule_version(rule_version_id),
    engine_version_id TEXT NOT NULL REFERENCES engine_version(engine_version_id),
    population_version TEXT NOT NULL CHECK (population_version = 'Same Year Rates v1'),
    result_payload TEXT NOT NULL,
    manifest_hash TEXT NOT NULL CHECK (length(manifest_hash) = 64),
    recorded_at_utc TEXT NOT NULL
) STRICT;
CREATE TRIGGER no_update_supplier_rate_distribution BEFORE UPDATE ON supplier_rate_distribution_run
BEGIN SELECT RAISE(ABORT, 'supplier_rate_distribution_run is immutable'); END;
CREATE TRIGGER no_delete_supplier_rate_distribution BEFORE DELETE ON supplier_rate_distribution_run
BEGIN SELECT RAISE(ABORT, 'supplier_rate_distribution_run cannot be deleted'); END;

INSERT INTO schema_migration VALUES
('0051', 'supplier_rate_distributions', '__FORGE_MIGRATION_SHA256__',
 strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'schema-prototype-0.51');
COMMIT;
