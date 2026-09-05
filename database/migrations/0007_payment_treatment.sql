-- Forge X tooling and ED&D payment-treatment evidence

PRAGMA foreign_keys = ON;
BEGIN IMMEDIATE;

CREATE TABLE payment_cost_evidence (
    payment_cost_evidence_id TEXT PRIMARY KEY,
    observation_id TEXT NOT NULL REFERENCES pbd_observation(observation_id),
    source_datum_id TEXT NOT NULL REFERENCES source_datum(source_datum_id),
    cost_type TEXT NOT NULL CHECK (cost_type IN ('SFT', 'PST', 'ED&D')),
    program_year INTEGER NOT NULL CHECK (program_year BETWEEN 1900 AND 2200),
    submitted_wording TEXT NOT NULL,
    submitted_lexeme TEXT NOT NULL,
    decimal_coefficient TEXT NOT NULL,
    decimal_scale INTEGER NOT NULL CHECK (decimal_scale >= 0),
    normalized_unit_id TEXT NOT NULL,
    currency_id TEXT,
    recorded_at_utc TEXT NOT NULL,
    UNIQUE (observation_id, source_datum_id, cost_type, program_year)
) STRICT;

CREATE TABLE payment_treatment_version (
    payment_treatment_version_id TEXT PRIMARY KEY,
    payment_cost_evidence_id TEXT NOT NULL REFERENCES payment_cost_evidence(payment_cost_evidence_id),
    treatment_classification TEXT NOT NULL CHECK (treatment_classification IN (
        'Separate Lump Sum', 'Amortized in Piece Price',
        'Partially Amortized', 'Treatment Unconfirmed'
    )),
    upfront_coefficient TEXT,
    upfront_scale INTEGER CHECK (upfront_scale IS NULL OR upfront_scale >= 0),
    embedded_per_part_coefficient TEXT,
    embedded_per_part_scale INTEGER CHECK (embedded_per_part_scale IS NULL OR embedded_per_part_scale >= 0),
    allocation_basis_payload TEXT,
    treatment_rule_version_id TEXT NOT NULL REFERENCES rule_version(rule_version_id),
    confirmed_by_user_id TEXT,
    confirmation_reason TEXT,
    recorded_at_utc TEXT NOT NULL,
    supersedes_treatment_version_id TEXT REFERENCES payment_treatment_version(payment_treatment_version_id),
    CHECK ((upfront_coefficient IS NULL) = (upfront_scale IS NULL)),
    CHECK ((embedded_per_part_coefficient IS NULL) = (embedded_per_part_scale IS NULL)),
    CHECK (treatment_classification <> 'Partially Amortized'
           OR (upfront_coefficient IS NOT NULL AND embedded_per_part_coefficient IS NOT NULL
               AND allocation_basis_payload IS NOT NULL)),
    CHECK (treatment_classification = 'Treatment Unconfirmed'
           OR (confirmed_by_user_id IS NOT NULL AND confirmation_reason IS NOT NULL))
) STRICT;

CREATE UNIQUE INDEX ux_payment_treatment_superseded_once
ON payment_treatment_version(supersedes_treatment_version_id)
WHERE supersedes_treatment_version_id IS NOT NULL;

CREATE INDEX ix_payment_cost_observation
ON payment_cost_evidence(observation_id, cost_type);

CREATE VIEW v_current_payment_treatment AS
SELECT treatment.*
FROM payment_treatment_version treatment
WHERE NOT EXISTS (
    SELECT 1 FROM payment_treatment_version newer
    WHERE newer.supersedes_treatment_version_id = treatment.payment_treatment_version_id
);

CREATE TRIGGER no_update_payment_cost_evidence BEFORE UPDATE ON payment_cost_evidence
BEGIN SELECT RAISE(ABORT, 'payment_cost_evidence is immutable'); END;
CREATE TRIGGER no_delete_payment_cost_evidence BEFORE DELETE ON payment_cost_evidence
BEGIN SELECT RAISE(ABORT, 'payment_cost_evidence cannot be deleted'); END;
CREATE TRIGGER no_update_payment_treatment BEFORE UPDATE ON payment_treatment_version
BEGIN SELECT RAISE(ABORT, 'payment_treatment_version is immutable'); END;
CREATE TRIGGER no_delete_payment_treatment BEFORE DELETE ON payment_treatment_version
BEGIN SELECT RAISE(ABORT, 'payment_treatment_version cannot be deleted'); END;

INSERT INTO schema_migration VALUES (
    '0007', 'payment_treatment', '__FORGE_MIGRATION_SHA256__',
    strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'schema-prototype-0.7'
);

COMMIT;
