-- Forge X automated restore validation and recurring recovery policy

PRAGMA foreign_keys = ON;
BEGIN IMMEDIATE;

CREATE TABLE recovery_test_policy_version (
    recovery_test_policy_version_id TEXT PRIMARY KEY,
    interval_hours INTEGER NOT NULL CHECK (interval_hours BETWEEN 1 AND 8760),
    policy_reason TEXT NOT NULL,
    approved_by_user_id TEXT NOT NULL,
    supersedes_policy_version_id TEXT REFERENCES recovery_test_policy_version(recovery_test_policy_version_id),
    recorded_at_utc TEXT NOT NULL,
    CHECK (supersedes_policy_version_id IS NULL OR
           supersedes_policy_version_id <> recovery_test_policy_version_id)
) STRICT;

CREATE VIEW v_current_recovery_test_policy AS
SELECT policy.* FROM recovery_test_policy_version policy
WHERE NOT EXISTS (
    SELECT 1 FROM recovery_test_policy_version newer
    WHERE newer.supersedes_policy_version_id = policy.recovery_test_policy_version_id
);

CREATE INDEX ix_restore_checkpoint_status
ON restore_event(local_checkpoint_id, restore_type, restore_status,
                 completed_at_utc DESC, restore_event_id DESC);

CREATE TRIGGER no_update_recovery_policy BEFORE UPDATE ON recovery_test_policy_version
BEGIN SELECT RAISE(ABORT, 'recovery_test_policy_version is immutable'); END;
CREATE TRIGGER no_delete_recovery_policy BEFORE DELETE ON recovery_test_policy_version
BEGIN SELECT RAISE(ABORT, 'recovery_test_policy_version cannot be deleted'); END;

INSERT INTO schema_migration VALUES (
    '0025', 'recovery_validation_policy', '__FORGE_MIGRATION_SHA256__',
    strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'schema-prototype-0.25'
);

COMMIT;
