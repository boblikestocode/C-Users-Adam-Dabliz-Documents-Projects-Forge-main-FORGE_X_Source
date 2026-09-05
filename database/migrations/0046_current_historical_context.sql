-- Immutable current-event versus historical-profile context.
PRAGMA foreign_keys = ON;
BEGIN IMMEDIATE;

CREATE TABLE supplier_current_historical_context (
    supplier_current_historical_context_id TEXT PRIMARY KEY,
    supplier_competitiveness_observation_id TEXT NOT NULL
        REFERENCES supplier_competitiveness_observation(supplier_competitiveness_observation_id),
    supplier_profile_run_id TEXT NOT NULL REFERENCES supplier_profile_run(supplier_profile_run_id),
    current_event_status TEXT NOT NULL CHECK (current_event_status IN ('Green', 'Yellow', 'Red', 'Gray')),
    historical_profile_status TEXT NOT NULL CHECK (historical_profile_status IN ('Red', 'No Governing Red')),
    historical_risk_notice_payload TEXT,
    context_manifest_hash TEXT NOT NULL CHECK (length(context_manifest_hash) = 64),
    generated_at_utc TEXT NOT NULL,
    UNIQUE (supplier_competitiveness_observation_id, supplier_profile_run_id),
    CHECK ((current_event_status = 'Green' AND historical_profile_status = 'Red') =
           (historical_risk_notice_payload IS NOT NULL))
) STRICT;

CREATE TABLE supplier_current_historical_finding (
    supplier_current_historical_finding_id TEXT PRIMARY KEY,
    supplier_current_historical_context_id TEXT NOT NULL
        REFERENCES supplier_current_historical_context(supplier_current_historical_context_id),
    supplier_profile_finding_id TEXT NOT NULL REFERENCES supplier_profile_finding(supplier_profile_finding_id),
    evidence_ordinal INTEGER NOT NULL CHECK (evidence_ordinal >= 0),
    UNIQUE (supplier_current_historical_context_id, evidence_ordinal),
    UNIQUE (supplier_current_historical_context_id, supplier_profile_finding_id)
) STRICT;

CREATE TRIGGER no_update_supplier_current_historical_context
BEFORE UPDATE ON supplier_current_historical_context
BEGIN SELECT RAISE(ABORT, 'supplier current/historical context is immutable'); END;
CREATE TRIGGER no_delete_supplier_current_historical_context
BEFORE DELETE ON supplier_current_historical_context
BEGIN SELECT RAISE(ABORT, 'supplier current/historical context cannot be deleted'); END;
CREATE TRIGGER no_update_supplier_current_historical_finding
BEFORE UPDATE ON supplier_current_historical_finding
BEGIN SELECT RAISE(ABORT, 'supplier current/historical finding is immutable'); END;
CREATE TRIGGER no_delete_supplier_current_historical_finding
BEFORE DELETE ON supplier_current_historical_finding
BEGIN SELECT RAISE(ABORT, 'supplier current/historical finding cannot be deleted'); END;

INSERT INTO schema_migration VALUES
('0046', 'current_historical_context', '__FORGE_MIGRATION_SHA256__',
 strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'schema-prototype-0.46');
COMMIT;
