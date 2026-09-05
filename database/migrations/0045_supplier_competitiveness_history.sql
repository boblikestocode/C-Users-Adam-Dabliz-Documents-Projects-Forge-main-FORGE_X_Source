-- Evidence-linked current-event competitiveness and immutable improvement assessments.
PRAGMA foreign_keys = ON;
BEGIN IMMEDIATE;

CREATE TABLE supplier_competitiveness_observation (
    supplier_competitiveness_observation_id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL REFERENCES sourcing_event(event_id),
    supplier_id TEXT NOT NULL,
    commodity_id TEXT NOT NULL,
    quote_population_id TEXT NOT NULL,
    competitiveness_status TEXT NOT NULL
        CHECK (competitiveness_status IN ('Green', 'Yellow', 'Red', 'Gray')),
    evidence_entity_type TEXT NOT NULL,
    evidence_entity_id TEXT NOT NULL,
    explanation_payload TEXT NOT NULL,
    confirmed_by_user_id TEXT NOT NULL,
    confirmed_at_utc TEXT NOT NULL,
    supersedes_observation_id TEXT UNIQUE
        REFERENCES supplier_competitiveness_observation(supplier_competitiveness_observation_id),
    CHECK (supersedes_observation_id IS NULL OR
           supersedes_observation_id <> supplier_competitiveness_observation_id)
) STRICT;

CREATE TABLE supplier_improvement_assessment (
    supplier_improvement_assessment_id TEXT PRIMARY KEY,
    supplier_id TEXT NOT NULL,
    commodity_id TEXT NOT NULL,
    evidence_cutoff_utc TEXT NOT NULL,
    assessment_status TEXT NOT NULL CHECK (assessment_status IN
        ('Insufficient Evidence', 'No Current Improvement', 'Recent Improvement',
         'Sustained Improvement', 'Consistently Competitive')),
    trailing_competitive_population_count INTEGER NOT NULL CHECK (trailing_competitive_population_count >= 0),
    evidence_manifest_hash TEXT NOT NULL CHECK (length(evidence_manifest_hash) = 64),
    generated_at_utc TEXT NOT NULL,
    UNIQUE (supplier_id, commodity_id, evidence_cutoff_utc)
) STRICT;

CREATE TABLE supplier_improvement_evidence (
    supplier_improvement_evidence_id TEXT PRIMARY KEY,
    supplier_improvement_assessment_id TEXT NOT NULL
        REFERENCES supplier_improvement_assessment(supplier_improvement_assessment_id),
    supplier_competitiveness_observation_id TEXT NOT NULL
        REFERENCES supplier_competitiveness_observation(supplier_competitiveness_observation_id),
    evidence_ordinal INTEGER NOT NULL CHECK (evidence_ordinal >= 0),
    UNIQUE (supplier_improvement_assessment_id, evidence_ordinal),
    UNIQUE (supplier_improvement_assessment_id, supplier_competitiveness_observation_id)
) STRICT;

CREATE TRIGGER validate_supplier_competitiveness_observation
BEFORE INSERT ON supplier_competitiveness_observation
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM sourcing_event event
        WHERE event.event_id = NEW.event_id
          AND event.commodity_id = NEW.commodity_id
    ) THEN RAISE(ABORT, 'competitiveness commodity must match sourcing event') END;
    SELECT CASE WHEN NEW.supersedes_observation_id IS NULL AND EXISTS (
        SELECT 1 FROM supplier_competitiveness_observation prior
        WHERE prior.event_id = NEW.event_id AND prior.supplier_id = NEW.supplier_id
          AND prior.quote_population_id = NEW.quote_population_id
          AND NOT EXISTS (SELECT 1 FROM supplier_competitiveness_observation newer
                          WHERE newer.supersedes_observation_id = prior.supplier_competitiveness_observation_id)
    ) THEN RAISE(ABORT, 'competitiveness correction must supersede current observation') END;
    SELECT CASE WHEN NEW.supersedes_observation_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM supplier_competitiveness_observation prior
        WHERE prior.supplier_competitiveness_observation_id = NEW.supersedes_observation_id
          AND prior.event_id = NEW.event_id AND prior.supplier_id = NEW.supplier_id
          AND prior.commodity_id = NEW.commodity_id
          AND prior.quote_population_id = NEW.quote_population_id
          AND NOT EXISTS (SELECT 1 FROM supplier_competitiveness_observation newer
                          WHERE newer.supersedes_observation_id = prior.supplier_competitiveness_observation_id)
    ) THEN RAISE(ABORT, 'competitiveness correction must directly supersede current scoped observation') END;
END;

CREATE TRIGGER no_update_supplier_competitiveness BEFORE UPDATE ON supplier_competitiveness_observation
BEGIN SELECT RAISE(ABORT, 'supplier competitiveness observation is immutable'); END;
CREATE TRIGGER no_delete_supplier_competitiveness BEFORE DELETE ON supplier_competitiveness_observation
BEGIN SELECT RAISE(ABORT, 'supplier competitiveness observation cannot be deleted'); END;
CREATE TRIGGER no_update_supplier_improvement BEFORE UPDATE ON supplier_improvement_assessment
BEGIN SELECT RAISE(ABORT, 'supplier improvement assessment is immutable'); END;
CREATE TRIGGER no_delete_supplier_improvement BEFORE DELETE ON supplier_improvement_assessment
BEGIN SELECT RAISE(ABORT, 'supplier improvement assessment cannot be deleted'); END;
CREATE TRIGGER no_update_supplier_improvement_evidence BEFORE UPDATE ON supplier_improvement_evidence
BEGIN SELECT RAISE(ABORT, 'supplier improvement evidence is immutable'); END;
CREATE TRIGGER no_delete_supplier_improvement_evidence BEFORE DELETE ON supplier_improvement_evidence
BEGIN SELECT RAISE(ABORT, 'supplier improvement evidence cannot be deleted'); END;

INSERT INTO schema_migration VALUES
('0045', 'supplier_competitiveness_history', '__FORGE_MIGRATION_SHA256__',
 strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'schema-prototype-0.45');
COMMIT;
