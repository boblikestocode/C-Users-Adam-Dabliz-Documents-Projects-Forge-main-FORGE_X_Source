-- Publication status is append-only; the publication identity and manifest never change.

PRAGMA foreign_keys = ON;
BEGIN IMMEDIATE;

CREATE TABLE publication_status_event (
    publication_status_event_id TEXT PRIMARY KEY,
    publication_id TEXT NOT NULL REFERENCES publication(publication_id),
    publication_status TEXT NOT NULL CHECK (publication_status IN ('Prepared', 'Locally Verified', 'Queued', 'Uploading', 'Remote Verification', 'Published', 'Retry Pending', 'Conflict', 'Failed')),
    status_reason TEXT NOT NULL,
    recorded_at_utc TEXT NOT NULL
) STRICT;

CREATE INDEX ix_publication_status_history
ON publication_status_event(publication_id, recorded_at_utc DESC, publication_status_event_id DESC);

CREATE VIEW v_current_publication_status AS
SELECT pse.*
FROM publication_status_event pse
WHERE NOT EXISTS (
    SELECT 1 FROM publication_status_event newer
    WHERE newer.publication_id = pse.publication_id
      AND (newer.recorded_at_utc > pse.recorded_at_utc
           OR (newer.recorded_at_utc = pse.recorded_at_utc
               AND newer.publication_status_event_id > pse.publication_status_event_id))
);

CREATE TRIGGER no_update_publication BEFORE UPDATE ON publication
BEGIN SELECT RAISE(ABORT, 'publication is immutable'); END;
CREATE TRIGGER no_delete_publication BEFORE DELETE ON publication
BEGIN SELECT RAISE(ABORT, 'publication cannot be deleted'); END;
CREATE TRIGGER no_update_publication_status BEFORE UPDATE ON publication_status_event
BEGIN SELECT RAISE(ABORT, 'publication_status_event is immutable'); END;
CREATE TRIGGER no_delete_publication_status BEFORE DELETE ON publication_status_event
BEGIN SELECT RAISE(ABORT, 'publication_status_event cannot be deleted'); END;

INSERT INTO schema_migration VALUES (
    '0005', 'publication_status_history', '__FORGE_MIGRATION_SHA256__',
    strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'schema-prototype-0.5'
);

COMMIT;
