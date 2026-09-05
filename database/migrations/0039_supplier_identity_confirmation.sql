-- Forge X append-only supplier identity confirmation
PRAGMA foreign_keys = ON;
BEGIN IMMEDIATE;
CREATE TABLE observation_supplier_identity_confirmation (
    supplier_identity_confirmation_id TEXT PRIMARY KEY,
    observation_id TEXT NOT NULL REFERENCES pbd_observation(observation_id),
    confirmed_supplier_id TEXT NOT NULL CHECK (length(trim(confirmed_supplier_id)) > 0),
    confirmed_supplier_code TEXT NOT NULL CHECK (length(trim(confirmed_supplier_code)) > 0),
    prior_supplier_id TEXT,
    provisional_supplier_code TEXT,
    submitted_supplier_name TEXT NOT NULL,
    confirmation_reason TEXT NOT NULL CHECK (length(trim(confirmation_reason)) > 0),
    confirmed_by_user_id TEXT NOT NULL CHECK (length(trim(confirmed_by_user_id)) > 0),
    supersedes_confirmation_id TEXT REFERENCES observation_supplier_identity_confirmation(supplier_identity_confirmation_id),
    recorded_at_utc TEXT NOT NULL,
    CHECK (supersedes_confirmation_id IS NULL OR supersedes_confirmation_id <> supplier_identity_confirmation_id)
) STRICT;
CREATE VIEW v_current_observation_supplier_identity AS
SELECT confirmation.* FROM observation_supplier_identity_confirmation confirmation
WHERE NOT EXISTS (
    SELECT 1 FROM observation_supplier_identity_confirmation newer
    WHERE newer.supersedes_confirmation_id = confirmation.supplier_identity_confirmation_id
);
CREATE VIEW v_effective_pbd_observation AS
SELECT observation.observation_id, observation.staged_observation_id,
       observation.occurrence_id, observation.observation_context,
       observation.context_id,
       COALESCE(identity.confirmed_supplier_id, observation.supplier_id) AS supplier_id,
       observation.supplier_id AS originally_committed_supplier_id,
       identity.confirmed_supplier_code,
       observation.supplier_plant_id, observation.part_id,
       observation.submitted_supplier_name, observation.submitted_part_number,
       observation.submitted_part_description, observation.economic_date,
       observation.economic_date_precision, observation.structure_category,
       observation.recorded_at_utc
FROM pbd_observation observation
LEFT JOIN v_current_observation_supplier_identity identity
  ON identity.observation_id = observation.observation_id;
CREATE TRIGGER validate_supplier_identity_confirmation_insert
BEFORE INSERT ON observation_supplier_identity_confirmation
BEGIN
    SELECT CASE WHEN NEW.supersedes_confirmation_id IS NULL AND EXISTS (
        SELECT 1 FROM observation_supplier_identity_confirmation prior
        WHERE prior.observation_id = NEW.observation_id)
    THEN RAISE(ABORT, 'supplier identity confirmation must supersede current confirmation') END;
    SELECT CASE WHEN NEW.supersedes_confirmation_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM v_current_observation_supplier_identity prior
        WHERE prior.supplier_identity_confirmation_id = NEW.supersedes_confirmation_id
          AND prior.observation_id = NEW.observation_id)
    THEN RAISE(ABORT, 'supplier identity predecessor must be current') END;
    SELECT CASE WHEN EXISTS (
        SELECT 1 FROM v_current_observation_supplier_identity other
        WHERE other.confirmed_supplier_code = NEW.confirmed_supplier_code
          AND other.confirmed_supplier_id <> NEW.confirmed_supplier_id)
    THEN RAISE(ABORT, 'supplier code conflicts with another confirmed identity') END;
    SELECT CASE WHEN EXISTS (
        SELECT 1 FROM v_current_observation_supplier_identity other
        WHERE other.confirmed_supplier_id = NEW.confirmed_supplier_id
          AND other.confirmed_supplier_code <> NEW.confirmed_supplier_code)
    THEN RAISE(ABORT, 'supplier identity conflicts with another confirmed code') END;
END;
CREATE TRIGGER supplier_identity_confirmation_no_update
BEFORE UPDATE ON observation_supplier_identity_confirmation
BEGIN SELECT RAISE(ABORT, 'observation_supplier_identity_confirmation is immutable'); END;
CREATE TRIGGER supplier_identity_confirmation_no_delete
BEFORE DELETE ON observation_supplier_identity_confirmation
BEGIN SELECT RAISE(ABORT, 'observation_supplier_identity_confirmation cannot be deleted'); END;
INSERT INTO schema_migration VALUES ('0039', 'supplier_identity_confirmation', '__FORGE_MIGRATION_SHA256__',
    strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'schema-prototype-0.39');
COMMIT;
