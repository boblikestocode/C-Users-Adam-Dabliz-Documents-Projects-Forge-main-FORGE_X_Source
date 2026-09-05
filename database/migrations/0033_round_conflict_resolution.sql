-- Forge X append-only same-round conflict resolution

PRAGMA foreign_keys = ON;
BEGIN IMMEDIATE;

ALTER TABLE round_conflict_decision
ADD COLUMN supersedes_round_conflict_decision_id TEXT
    REFERENCES round_conflict_decision(round_conflict_decision_id);

CREATE VIEW v_current_round_conflict_decision AS
SELECT decision.* FROM round_conflict_decision decision
WHERE NOT EXISTS (
    SELECT 1 FROM round_conflict_decision newer
    WHERE newer.supersedes_round_conflict_decision_id = decision.round_conflict_decision_id
);

CREATE TRIGGER no_update_round_conflict BEFORE UPDATE ON round_conflict
BEGIN SELECT RAISE(ABORT, 'round_conflict is immutable'); END;
CREATE TRIGGER no_delete_round_conflict BEFORE DELETE ON round_conflict
BEGIN SELECT RAISE(ABORT, 'round_conflict cannot be deleted'); END;
CREATE TRIGGER no_update_round_conflict_decision BEFORE UPDATE ON round_conflict_decision
BEGIN SELECT RAISE(ABORT, 'round_conflict_decision is immutable'); END;
CREATE TRIGGER no_delete_round_conflict_decision BEFORE DELETE ON round_conflict_decision
BEGIN SELECT RAISE(ABORT, 'round_conflict_decision cannot be deleted'); END;

INSERT INTO schema_migration VALUES (
    '0033', 'round_conflict_resolution', '__FORGE_MIGRATION_SHA256__',
    strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'schema-prototype-0.33'
);

COMMIT;
