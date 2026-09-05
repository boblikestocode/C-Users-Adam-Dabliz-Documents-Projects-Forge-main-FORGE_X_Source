-- Forge X append-only publication-attempt and remote-verification evidence

PRAGMA foreign_keys = ON;
BEGIN IMMEDIATE;

CREATE TABLE publication_attempt (
    publication_attempt_id TEXT PRIMARY KEY,
    publication_id TEXT NOT NULL REFERENCES publication(publication_id),
    sync_queue_item_id TEXT NOT NULL REFERENCES sync_queue_item(sync_queue_item_id),
    attempt_number INTEGER NOT NULL CHECK (attempt_number > 0),
    lease_owner TEXT NOT NULL,
    authority_verification_reference TEXT NOT NULL,
    authority_verified_online_at_utc TEXT NOT NULL,
    expected_predecessor_publication_id TEXT,
    observed_remote_publication_id TEXT,
    started_at_utc TEXT NOT NULL,
    UNIQUE (sync_queue_item_id, attempt_number)
) STRICT;

CREATE TABLE publication_attempt_event (
    publication_attempt_event_id TEXT PRIMARY KEY,
    publication_attempt_id TEXT NOT NULL REFERENCES publication_attempt(publication_attempt_id),
    attempt_status TEXT NOT NULL CHECK (
        attempt_status IN ('Uploading', 'Remote Verification', 'Retry Pending',
                           'Conflict', 'Failed', 'Published')
    ),
    observed_remote_hash TEXT,
    status_detail TEXT NOT NULL,
    recorded_at_utc TEXT NOT NULL
) STRICT;

CREATE INDEX ix_publication_attempt_queue
ON publication_attempt(sync_queue_item_id, attempt_number DESC);

CREATE INDEX ix_publication_attempt_event
ON publication_attempt_event(publication_attempt_id, recorded_at_utc,
    publication_attempt_event_id);

-- UUIDv7 identifiers generated within the same millisecond are not monotonic.
-- Resolve equal-timestamp append-only statuses by SQLite insertion order.
DROP VIEW v_current_publication_status;
CREATE VIEW v_current_publication_status AS
SELECT status.*
FROM publication_status_event status
WHERE NOT EXISTS (
    SELECT 1 FROM publication_status_event newer
    WHERE newer.publication_id = status.publication_id
      AND (newer.recorded_at_utc > status.recorded_at_utc
           OR (newer.recorded_at_utc = status.recorded_at_utc
               AND newer.rowid > status.rowid))
);

CREATE TRIGGER no_update_publication_attempt BEFORE UPDATE ON publication_attempt
BEGIN SELECT RAISE(ABORT, 'publication_attempt is immutable'); END;
CREATE TRIGGER no_delete_publication_attempt BEFORE DELETE ON publication_attempt
BEGIN SELECT RAISE(ABORT, 'publication_attempt cannot be deleted'); END;
CREATE TRIGGER no_update_publication_attempt_event BEFORE UPDATE ON publication_attempt_event
BEGIN SELECT RAISE(ABORT, 'publication_attempt_event is immutable'); END;
CREATE TRIGGER no_delete_publication_attempt_event BEFORE DELETE ON publication_attempt_event
BEGIN SELECT RAISE(ABORT, 'publication_attempt_event cannot be deleted'); END;

INSERT INTO schema_migration VALUES (
    '0015', 'publication_attempts', '__FORGE_MIGRATION_SHA256__',
    strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'schema-prototype-0.15'
);

COMMIT;
