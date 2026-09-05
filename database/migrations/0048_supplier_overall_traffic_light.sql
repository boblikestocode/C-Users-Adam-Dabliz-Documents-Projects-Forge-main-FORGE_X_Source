-- Immutable supplier-wide roll-up of retained commodity traffic lights.
PRAGMA foreign_keys = ON;
BEGIN IMMEDIATE;

CREATE TABLE supplier_overall_traffic_light_assessment (
    supplier_overall_traffic_light_assessment_id TEXT PRIMARY KEY,
    supplier_id TEXT NOT NULL,
    evidence_cutoff_utc TEXT NOT NULL,
    overall_status TEXT NOT NULL CHECK (overall_status IN ('Green', 'Yellow', 'Red', 'Gray')),
    governing_commodity_payload TEXT NOT NULL,
    professional_explanation TEXT NOT NULL,
    commodity_manifest_hash TEXT NOT NULL CHECK (length(commodity_manifest_hash) = 64),
    generated_at_utc TEXT NOT NULL,
    UNIQUE (supplier_id, evidence_cutoff_utc)
) STRICT;

CREATE TABLE supplier_overall_traffic_light_commodity (
    supplier_overall_traffic_light_commodity_id TEXT PRIMARY KEY,
    supplier_overall_traffic_light_assessment_id TEXT NOT NULL
        REFERENCES supplier_overall_traffic_light_assessment(supplier_overall_traffic_light_assessment_id),
    commodity_id TEXT NOT NULL,
    supplier_traffic_light_assessment_id TEXT NOT NULL
        REFERENCES supplier_traffic_light_assessment(supplier_traffic_light_assessment_id),
    commodity_status TEXT NOT NULL CHECK (commodity_status IN ('Green', 'Yellow', 'Red', 'Gray')),
    evidence_ordinal INTEGER NOT NULL CHECK (evidence_ordinal >= 0),
    UNIQUE (supplier_overall_traffic_light_assessment_id, commodity_id),
    UNIQUE (supplier_overall_traffic_light_assessment_id, supplier_traffic_light_assessment_id),
    UNIQUE (supplier_overall_traffic_light_assessment_id, evidence_ordinal)
) STRICT;

CREATE TRIGGER no_update_supplier_overall_traffic_light
BEFORE UPDATE ON supplier_overall_traffic_light_assessment
BEGIN SELECT RAISE(ABORT, 'supplier overall traffic-light assessment is immutable'); END;
CREATE TRIGGER no_delete_supplier_overall_traffic_light
BEFORE DELETE ON supplier_overall_traffic_light_assessment
BEGIN SELECT RAISE(ABORT, 'supplier overall traffic-light assessment cannot be deleted'); END;
CREATE TRIGGER no_update_supplier_overall_traffic_light_commodity
BEFORE UPDATE ON supplier_overall_traffic_light_commodity
BEGIN SELECT RAISE(ABORT, 'supplier overall traffic-light commodity is immutable'); END;
CREATE TRIGGER no_delete_supplier_overall_traffic_light_commodity
BEFORE DELETE ON supplier_overall_traffic_light_commodity
BEGIN SELECT RAISE(ABORT, 'supplier overall traffic-light commodity cannot be deleted'); END;

INSERT INTO schema_migration VALUES
('0048', 'supplier_overall_traffic_light', '__FORGE_MIGRATION_SHA256__',
 strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'schema-prototype-0.48');
COMMIT;
