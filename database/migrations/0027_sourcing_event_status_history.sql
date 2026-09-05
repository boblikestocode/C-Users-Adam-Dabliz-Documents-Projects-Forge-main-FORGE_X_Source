-- Forge X append-only sourcing-event lifecycle

PRAGMA foreign_keys = ON;
BEGIN IMMEDIATE;

CREATE TABLE sourcing_event_status_event (
    sourcing_event_status_event_id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL REFERENCES sourcing_event(event_id),
    event_status TEXT NOT NULL CHECK (event_status IN ('Setup', 'Active', 'Finalized', 'Closed')),
    transition_reason TEXT NOT NULL,
    decided_by_user_id TEXT NOT NULL,
    supersedes_status_event_id TEXT REFERENCES sourcing_event_status_event(sourcing_event_status_event_id),
    recorded_at_utc TEXT NOT NULL,
    CHECK (supersedes_status_event_id IS NULL OR
           supersedes_status_event_id <> sourcing_event_status_event_id)
) STRICT;

INSERT INTO sourcing_event_status_event
SELECT 'initial-' || event_id, event_id, event_status,
       'Initial status migrated from sourcing event', primary_buyer_user_id,
       NULL, created_at_utc
FROM sourcing_event;

CREATE VIEW v_current_sourcing_event_status AS
SELECT status.* FROM sourcing_event_status_event status
WHERE NOT EXISTS (
    SELECT 1 FROM sourcing_event_status_event newer
    WHERE newer.event_id = status.event_id
      AND (newer.recorded_at_utc > status.recorded_at_utc OR
           (newer.recorded_at_utc = status.recorded_at_utc AND
            newer.sourcing_event_status_event_id > status.sourcing_event_status_event_id))
);

CREATE INDEX ix_sourcing_event_status_current
ON sourcing_event_status_event(event_id, recorded_at_utc DESC,
                               sourcing_event_status_event_id DESC);

CREATE TRIGGER no_update_sourcing_event BEFORE UPDATE ON sourcing_event
BEGIN SELECT RAISE(ABORT, 'sourcing_event is immutable'); END;
CREATE TRIGGER no_delete_sourcing_event BEFORE DELETE ON sourcing_event
BEGIN SELECT RAISE(ABORT, 'sourcing_event cannot be deleted'); END;
CREATE TRIGGER no_update_sourcing_event_status BEFORE UPDATE ON sourcing_event_status_event
BEGIN SELECT RAISE(ABORT, 'sourcing_event_status_event is immutable'); END;
CREATE TRIGGER no_delete_sourcing_event_status BEFORE DELETE ON sourcing_event_status_event
BEGIN SELECT RAISE(ABORT, 'sourcing_event_status_event cannot be deleted'); END;

INSERT INTO schema_migration VALUES (
    '0027', 'sourcing_event_status_history', '__FORGE_MIGRATION_SHA256__',
    strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'schema-prototype-0.27'
);

COMMIT;
