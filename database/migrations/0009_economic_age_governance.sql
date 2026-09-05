-- Forge X economic-date confirmation and rolling-window governance

PRAGMA foreign_keys = ON;
BEGIN IMMEDIATE;

ALTER TABLE observation_eligibility
ADD COLUMN eligibility_rule_version_id TEXT REFERENCES rule_version(rule_version_id);

CREATE TABLE economic_date_confirmation (
    economic_date_confirmation_id TEXT PRIMARY KEY,
    observation_id TEXT NOT NULL REFERENCES pbd_observation(observation_id),
    confirmed_economic_date TEXT NOT NULL,
    date_precision TEXT NOT NULL CHECK (date_precision IN ('Day', 'Month', 'Year')),
    assignment_method TEXT NOT NULL CHECK (assignment_method IN ('Individual', 'Batch')),
    assignment_batch_id TEXT,
    confirmed_by_user_id TEXT NOT NULL,
    confirmation_reason TEXT NOT NULL,
    recorded_at_utc TEXT NOT NULL,
    supersedes_confirmation_id TEXT REFERENCES economic_date_confirmation(economic_date_confirmation_id),
    CHECK ((assignment_method = 'Batch') = (assignment_batch_id IS NOT NULL))
) STRICT;

CREATE UNIQUE INDEX ux_economic_date_superseded_once
ON economic_date_confirmation(supersedes_confirmation_id)
WHERE supersedes_confirmation_id IS NOT NULL;

CREATE INDEX ix_economic_date_observation
ON economic_date_confirmation(observation_id, recorded_at_utc DESC);

CREATE VIEW v_current_economic_date_confirmation AS
SELECT confirmation.*
FROM economic_date_confirmation confirmation
WHERE NOT EXISTS (
    SELECT 1 FROM economic_date_confirmation newer
    WHERE newer.supersedes_confirmation_id = confirmation.economic_date_confirmation_id
);

CREATE UNIQUE INDEX ux_observation_eligibility_superseded_once
ON observation_eligibility(supersedes_eligibility_id)
WHERE supersedes_eligibility_id IS NOT NULL;

CREATE TRIGGER no_update_economic_date_confirmation BEFORE UPDATE ON economic_date_confirmation
BEGIN SELECT RAISE(ABORT, 'economic_date_confirmation is immutable'); END;
CREATE TRIGGER no_delete_economic_date_confirmation BEFORE DELETE ON economic_date_confirmation
BEGIN SELECT RAISE(ABORT, 'economic_date_confirmation cannot be deleted'); END;
CREATE TRIGGER no_update_observation_eligibility BEFORE UPDATE ON observation_eligibility
BEGIN SELECT RAISE(ABORT, 'observation_eligibility is immutable'); END;
CREATE TRIGGER no_delete_observation_eligibility BEFORE DELETE ON observation_eligibility
BEGIN SELECT RAISE(ABORT, 'observation_eligibility cannot be deleted'); END;

INSERT INTO schema_migration VALUES (
    '0009', 'economic_age_governance', '__FORGE_MIGRATION_SHA256__',
    strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'schema-prototype-0.9'
);

COMMIT;
