-- Non-compensating commodity-level supplier traffic-light conclusions.
PRAGMA foreign_keys = ON;
BEGIN IMMEDIATE;

CREATE TABLE supplier_traffic_light_assessment (
    supplier_traffic_light_assessment_id TEXT PRIMARY KEY,
    supplier_id TEXT NOT NULL,
    commodity_id TEXT NOT NULL,
    evidence_cutoff_utc TEXT NOT NULL,
    overall_status TEXT NOT NULL CHECK (overall_status IN ('Green', 'Yellow', 'Red', 'Gray')),
    governing_category_payload TEXT NOT NULL,
    professional_explanation TEXT NOT NULL,
    category_manifest_hash TEXT NOT NULL CHECK (length(category_manifest_hash) = 64),
    generated_at_utc TEXT NOT NULL,
    UNIQUE (supplier_id, commodity_id, evidence_cutoff_utc)
) STRICT;

CREATE TABLE supplier_traffic_light_category (
    supplier_traffic_light_category_id TEXT PRIMARY KEY,
    supplier_traffic_light_assessment_id TEXT NOT NULL
        REFERENCES supplier_traffic_light_assessment(supplier_traffic_light_assessment_id),
    category_code TEXT NOT NULL CHECK (category_code IN
        ('PBD Quality', 'Current Market Position', 'Labor', 'Burden', 'Overhead',
         'Profit', 'Purchased Components', 'Below the Line')),
    category_status TEXT NOT NULL CHECK (category_status IN ('Green', 'Yellow', 'Red', 'Gray')),
    confidence_classification TEXT NOT NULL CHECK (confidence_classification IN ('High', 'Medium', 'Low')),
    material_commercial_impact_flag INTEGER NOT NULL CHECK (material_commercial_impact_flag IN (0, 1)),
    recurrence_count INTEGER NOT NULL CHECK (recurrence_count >= 0),
    evidence_entity_type TEXT,
    evidence_entity_id TEXT,
    explanation TEXT NOT NULL,
    evidence_ordinal INTEGER NOT NULL CHECK (evidence_ordinal >= 0),
    UNIQUE (supplier_traffic_light_assessment_id, category_code),
    UNIQUE (supplier_traffic_light_assessment_id, evidence_ordinal),
    CHECK ((evidence_entity_type IS NULL) = (evidence_entity_id IS NULL)),
    CHECK (category_status <> 'Green' OR evidence_entity_id IS NOT NULL)
) STRICT;

CREATE TRIGGER no_update_supplier_traffic_light_assessment
BEFORE UPDATE ON supplier_traffic_light_assessment
BEGIN SELECT RAISE(ABORT, 'supplier traffic-light assessment is immutable'); END;
CREATE TRIGGER no_delete_supplier_traffic_light_assessment
BEFORE DELETE ON supplier_traffic_light_assessment
BEGIN SELECT RAISE(ABORT, 'supplier traffic-light assessment cannot be deleted'); END;
CREATE TRIGGER no_update_supplier_traffic_light_category
BEFORE UPDATE ON supplier_traffic_light_category
BEGIN SELECT RAISE(ABORT, 'supplier traffic-light category is immutable'); END;
CREATE TRIGGER no_delete_supplier_traffic_light_category
BEFORE DELETE ON supplier_traffic_light_category
BEGIN SELECT RAISE(ABORT, 'supplier traffic-light category cannot be deleted'); END;

INSERT INTO schema_migration VALUES
('0047', 'supplier_traffic_light', '__FORGE_MIGRATION_SHA256__',
 strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'schema-prototype-0.47');
COMMIT;
