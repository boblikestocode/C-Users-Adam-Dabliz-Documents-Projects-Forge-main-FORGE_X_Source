-- Forge X append-only import, occurrence, and staging state ledgers

PRAGMA foreign_keys = ON;
BEGIN IMMEDIATE;

CREATE TABLE import_session_status_event (
    import_session_status_event_id TEXT PRIMARY KEY,
    import_session_id TEXT NOT NULL REFERENCES import_session(import_session_id),
    session_status TEXT NOT NULL CHECK (session_status IN
        ('Created', 'Discovering', 'Extracting', 'Awaiting Confirmation', 'Staging',
         'Committing', 'Reconciling', 'Completed', 'Cancelling', 'Cancelled', 'Failed')),
    status_detail TEXT NOT NULL,
    supersedes_status_event_id TEXT REFERENCES import_session_status_event(import_session_status_event_id),
    recorded_at_utc TEXT NOT NULL
) STRICT;

CREATE TABLE import_transaction_status_event (
    import_transaction_status_event_id TEXT PRIMARY KEY,
    import_transaction_id TEXT NOT NULL REFERENCES import_transaction(import_transaction_id),
    transaction_status TEXT NOT NULL CHECK (transaction_status IN
        ('Started', 'Committed', 'Rolled Back', 'Cancelled', 'Failed')),
    count_payload TEXT NOT NULL,
    status_detail TEXT NOT NULL,
    supersedes_status_event_id TEXT REFERENCES import_transaction_status_event(import_transaction_status_event_id),
    recorded_at_utc TEXT NOT NULL
) STRICT;

CREATE TABLE source_occurrence_status_event (
    source_occurrence_status_event_id TEXT PRIMARY KEY,
    occurrence_id TEXT NOT NULL REFERENCES source_occurrence(occurrence_id),
    occurrence_status TEXT NOT NULL CHECK (occurrence_status IN
        ('Pending', 'Committed', 'Duplicate', 'Blocked', 'Ignored', 'Failed')),
    status_detail TEXT NOT NULL,
    supersedes_status_event_id TEXT REFERENCES source_occurrence_status_event(source_occurrence_status_event_id),
    recorded_at_utc TEXT NOT NULL
) STRICT;

CREATE TABLE staged_observation_status_event (
    staged_observation_status_event_id TEXT PRIMARY KEY,
    staged_observation_id TEXT NOT NULL REFERENCES staged_observation(staged_observation_id),
    staged_status TEXT NOT NULL CHECK (staged_status IN
        ('Extracted', 'Needs Review', 'Ready to Commit', 'Committed', 'Blocked',
         'Duplicate', 'Ignored', 'Failed')),
    blocking_issue_count INTEGER NOT NULL CHECK (blocking_issue_count >= 0),
    status_detail TEXT NOT NULL,
    supersedes_status_event_id TEXT REFERENCES staged_observation_status_event(staged_observation_status_event_id),
    recorded_at_utc TEXT NOT NULL
) STRICT;

INSERT INTO import_session_status_event
SELECT 'initial-session-' || import_session_id, import_session_id, status,
       'Initial state migrated from import session', NULL,
       COALESCE(completed_at_utc, started_at_utc) FROM import_session;
INSERT INTO import_transaction_status_event
SELECT 'initial-transaction-' || import_transaction_id, import_transaction_id, status,
       json_object('discovered', discovered_count, 'committed', committed_count,
                   'duplicate', duplicate_count, 'blocked', blocked_count,
                   'ignored', ignored_count, 'failed', failed_count),
       'Initial state migrated from import transaction', NULL,
       COALESCE(completed_at_utc, started_at_utc) FROM import_transaction;
INSERT INTO source_occurrence_status_event
SELECT 'initial-occurrence-' || occurrence_id, occurrence_id, terminal_status,
       'Initial state migrated from source occurrence', NULL, recorded_at_utc
FROM source_occurrence;
INSERT INTO staged_observation_status_event
SELECT 'initial-staged-' || staged_observation_id, staged_observation_id, status,
       blocking_issue_count, 'Initial state migrated from staged observation', NULL,
       recorded_at_utc FROM staged_observation;

CREATE VIEW v_current_import_session_status AS
SELECT status.* FROM import_session_status_event status WHERE NOT EXISTS (
    SELECT 1 FROM import_session_status_event newer
    WHERE newer.supersedes_status_event_id = status.import_session_status_event_id);
CREATE VIEW v_current_import_transaction_status AS
SELECT status.* FROM import_transaction_status_event status WHERE NOT EXISTS (
    SELECT 1 FROM import_transaction_status_event newer
    WHERE newer.supersedes_status_event_id = status.import_transaction_status_event_id);
CREATE VIEW v_current_source_occurrence_status AS
SELECT status.* FROM source_occurrence_status_event status WHERE NOT EXISTS (
    SELECT 1 FROM source_occurrence_status_event newer
    WHERE newer.supersedes_status_event_id = status.source_occurrence_status_event_id);
CREATE VIEW v_current_staged_observation_status AS
SELECT status.* FROM staged_observation_status_event status WHERE NOT EXISTS (
    SELECT 1 FROM staged_observation_status_event newer
    WHERE newer.supersedes_status_event_id = status.staged_observation_status_event_id);

CREATE INDEX ix_import_session_status_current ON import_session_status_event(import_session_id, recorded_at_utc DESC);
CREATE INDEX ix_import_transaction_status_current ON import_transaction_status_event(import_transaction_id, recorded_at_utc DESC);
CREATE INDEX ix_occurrence_status_current ON source_occurrence_status_event(occurrence_id, recorded_at_utc DESC);
CREATE INDEX ix_staged_status_current ON staged_observation_status_event(staged_observation_id, recorded_at_utc DESC);

CREATE TRIGGER no_update_import_session_status BEFORE UPDATE ON import_session_status_event
BEGIN SELECT RAISE(ABORT, 'import_session_status_event is immutable'); END;
CREATE TRIGGER no_delete_import_session_status BEFORE DELETE ON import_session_status_event
BEGIN SELECT RAISE(ABORT, 'import_session_status_event cannot be deleted'); END;
CREATE TRIGGER no_update_import_transaction_status BEFORE UPDATE ON import_transaction_status_event
BEGIN SELECT RAISE(ABORT, 'import_transaction_status_event is immutable'); END;
CREATE TRIGGER no_delete_import_transaction_status BEFORE DELETE ON import_transaction_status_event
BEGIN SELECT RAISE(ABORT, 'import_transaction_status_event cannot be deleted'); END;
CREATE TRIGGER no_update_occurrence_status BEFORE UPDATE ON source_occurrence_status_event
BEGIN SELECT RAISE(ABORT, 'source_occurrence_status_event is immutable'); END;
CREATE TRIGGER no_delete_occurrence_status BEFORE DELETE ON source_occurrence_status_event
BEGIN SELECT RAISE(ABORT, 'source_occurrence_status_event cannot be deleted'); END;
CREATE TRIGGER no_update_staged_status BEFORE UPDATE ON staged_observation_status_event
BEGIN SELECT RAISE(ABORT, 'staged_observation_status_event is immutable'); END;
CREATE TRIGGER no_delete_staged_status BEFORE DELETE ON staged_observation_status_event
BEGIN SELECT RAISE(ABORT, 'staged_observation_status_event cannot be deleted'); END;

INSERT INTO schema_migration VALUES (
    '0029', 'import_state_history', '__FORGE_MIGRATION_SHA256__',
    strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'schema-prototype-0.29'
);

COMMIT;
