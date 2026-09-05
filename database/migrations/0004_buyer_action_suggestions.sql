-- Evidence-linked suggestions may assist buyers but never close actions automatically.

PRAGMA foreign_keys = ON;
BEGIN IMMEDIATE;

CREATE TABLE buyer_action_resolution_suggestion (
    suggestion_id TEXT PRIMARY KEY,
    stable_suggestion_id TEXT NOT NULL,
    buyer_action_id TEXT NOT NULL REFERENCES buyer_action(buyer_action_id),
    suggested_source_entity_type TEXT NOT NULL,
    suggested_source_entity_id TEXT NOT NULL,
    suggestion_rule_version_id TEXT NOT NULL REFERENCES rule_version(rule_version_id),
    suggestion_reason TEXT NOT NULL,
    suggestion_status TEXT NOT NULL CHECK (suggestion_status IN ('Pending Buyer Review', 'Accepted by Buyer', 'Dismissed by Buyer')),
    generated_at_utc TEXT NOT NULL,
    reviewed_by_user_id TEXT,
    reviewed_at_utc TEXT,
    review_reason TEXT,
    supersedes_suggestion_id TEXT REFERENCES buyer_action_resolution_suggestion(suggestion_id),
    CHECK (
        (suggestion_status = 'Pending Buyer Review'
         AND reviewed_by_user_id IS NULL AND reviewed_at_utc IS NULL)
        OR
        (suggestion_status <> 'Pending Buyer Review'
         AND reviewed_by_user_id IS NOT NULL AND reviewed_at_utc IS NOT NULL
         AND review_reason IS NOT NULL)
    )
) STRICT;

CREATE INDEX ix_action_suggestion_pending
ON buyer_action_resolution_suggestion(buyer_action_id, suggestion_status, generated_at_utc);

CREATE INDEX ix_action_suggestion_history
ON buyer_action_resolution_suggestion(stable_suggestion_id, generated_at_utc);

CREATE INDEX ix_action_suggestion_source
ON buyer_action_resolution_suggestion(suggested_source_entity_type, suggested_source_entity_id);

CREATE TRIGGER no_update_action_suggestion
BEFORE UPDATE ON buyer_action_resolution_suggestion
BEGIN SELECT RAISE(ABORT, 'buyer_action_resolution_suggestion is immutable; append a reviewed version'); END;

CREATE TRIGGER no_delete_action_suggestion
BEFORE DELETE ON buyer_action_resolution_suggestion
BEGIN SELECT RAISE(ABORT, 'buyer_action_resolution_suggestion cannot be deleted'); END;

INSERT INTO schema_migration VALUES (
    '0004', 'buyer_action_suggestions', '__FORGE_MIGRATION_SHA256__',
    strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'schema-prototype-0.4'
);

COMMIT;
