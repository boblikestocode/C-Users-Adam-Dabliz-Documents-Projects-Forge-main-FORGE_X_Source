-- Immutable prior/initial quote-version component movement.
PRAGMA foreign_keys = ON;
BEGIN IMMEDIATE;

CREATE TABLE quote_version_movement_generation (
    quote_version_movement_generation_id TEXT PRIMARY KEY,
    quote_version_candidate_id TEXT NOT NULL UNIQUE
        REFERENCES quote_version_candidate(quote_version_candidate_id),
    prior_candidate_id TEXT NOT NULL REFERENCES quote_version_candidate(quote_version_candidate_id),
    initial_candidate_id TEXT NOT NULL REFERENCES quote_version_candidate(quote_version_candidate_id),
    calculation_rule_version_id TEXT NOT NULL REFERENCES rule_version(rule_version_id),
    component_manifest_hash TEXT NOT NULL CHECK (length(component_manifest_hash) = 64),
    generated_at_utc TEXT NOT NULL,
    CHECK (quote_version_candidate_id <> prior_candidate_id),
    CHECK (quote_version_candidate_id <> initial_candidate_id)
) STRICT;

CREATE TABLE quote_version_component_movement (
    quote_version_component_movement_id TEXT PRIMARY KEY,
    quote_version_movement_generation_id TEXT NOT NULL
        REFERENCES quote_version_movement_generation(quote_version_movement_generation_id),
    comparison_basis TEXT NOT NULL CHECK (comparison_basis IN ('Prior Version', 'Initial Version')),
    field_code TEXT NOT NULL,
    current_submitted_datum_id TEXT REFERENCES submitted_datum(submitted_datum_id),
    baseline_submitted_datum_id TEXT REFERENCES submitted_datum(submitted_datum_id),
    comparison_status TEXT NOT NULL CHECK (comparison_status IN
        ('Comparable', 'Missing Current Evidence', 'Missing Baseline Evidence',
         'Missing Both', 'Unit Alignment Required', 'Currency Alignment Required')),
    current_coefficient TEXT,
    current_scale INTEGER CHECK (current_scale IS NULL OR current_scale >= 0),
    baseline_coefficient TEXT,
    baseline_scale INTEGER CHECK (baseline_scale IS NULL OR baseline_scale >= 0),
    delta_coefficient TEXT,
    delta_scale INTEGER CHECK (delta_scale IS NULL OR delta_scale >= 0),
    movement_percent_coefficient TEXT,
    movement_percent_scale INTEGER CHECK (movement_percent_scale IS NULL OR movement_percent_scale >= 0),
    normalized_unit_id TEXT,
    currency_id TEXT,
    UNIQUE (quote_version_movement_generation_id, comparison_basis, field_code),
    CHECK ((current_coefficient IS NULL) = (current_scale IS NULL)),
    CHECK ((baseline_coefficient IS NULL) = (baseline_scale IS NULL)),
    CHECK ((delta_coefficient IS NULL) = (delta_scale IS NULL)),
    CHECK ((movement_percent_coefficient IS NULL) = (movement_percent_scale IS NULL)),
    CHECK ((comparison_status = 'Comparable') = (delta_coefficient IS NOT NULL))
) STRICT;

CREATE TRIGGER no_update_quote_version_movement_generation
BEFORE UPDATE ON quote_version_movement_generation
BEGIN SELECT RAISE(ABORT, 'quote version movement generation is immutable'); END;
CREATE TRIGGER no_delete_quote_version_movement_generation
BEFORE DELETE ON quote_version_movement_generation
BEGIN SELECT RAISE(ABORT, 'quote version movement generation cannot be deleted'); END;
CREATE TRIGGER no_update_quote_version_component_movement
BEFORE UPDATE ON quote_version_component_movement
BEGIN SELECT RAISE(ABORT, 'quote version component movement is immutable'); END;
CREATE TRIGGER no_delete_quote_version_component_movement
BEFORE DELETE ON quote_version_component_movement
BEGIN SELECT RAISE(ABORT, 'quote version component movement cannot be deleted'); END;

INSERT INTO schema_migration VALUES
('0044', 'quote_version_movement', '__FORGE_MIGRATION_SHA256__',
 strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'schema-prototype-0.44');
COMMIT;
