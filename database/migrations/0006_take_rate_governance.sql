-- Forge X append-only take-rate validation and decision governance

PRAGMA foreign_keys = ON;
BEGIN IMMEDIATE;

CREATE VIEW v_current_take_rate_decision AS
SELECT decision.*
FROM take_rate_decision decision
WHERE NOT EXISTS (
    SELECT 1
    FROM take_rate_decision newer
    WHERE newer.take_rate_validation_id = decision.take_rate_validation_id
      AND (newer.recorded_at_utc > decision.recorded_at_utc
           OR (newer.recorded_at_utc = decision.recorded_at_utc
               AND newer.take_rate_decision_id > decision.take_rate_decision_id))
);

CREATE INDEX ix_take_rate_decision_current
ON take_rate_decision(take_rate_validation_id, recorded_at_utc DESC, take_rate_decision_id DESC);

CREATE TRIGGER no_update_take_rate_validation
BEFORE UPDATE ON take_rate_validation
BEGIN SELECT RAISE(ABORT, 'take_rate_validation is immutable'); END;

CREATE TRIGGER no_delete_take_rate_validation
BEFORE DELETE ON take_rate_validation
BEGIN SELECT RAISE(ABORT, 'take_rate_validation cannot be deleted'); END;

CREATE TRIGGER no_update_take_rate_decision
BEFORE UPDATE ON take_rate_decision
BEGIN SELECT RAISE(ABORT, 'take_rate_decision is append-only'); END;

CREATE TRIGGER no_delete_take_rate_decision
BEFORE DELETE ON take_rate_decision
BEGIN SELECT RAISE(ABORT, 'take_rate_decision cannot be deleted'); END;

INSERT INTO schema_migration VALUES (
    '0006', 'take_rate_governance', '__FORGE_MIGRATION_SHA256__',
    strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'schema-prototype-0.6'
);

COMMIT;
