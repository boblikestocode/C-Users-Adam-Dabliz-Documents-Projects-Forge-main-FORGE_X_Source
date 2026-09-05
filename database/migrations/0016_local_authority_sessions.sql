-- Forge X local authority evidence and controlled access sessions

PRAGMA foreign_keys = ON;
BEGIN IMMEDIATE;

CREATE TABLE authority_verification (
    authority_verification_id TEXT PRIMARY KEY,
    stable_user_id TEXT NOT NULL,
    registered_device_id TEXT NOT NULL,
    authority_scope TEXT NOT NULL,
    authority_reference TEXT NOT NULL,
    registry_publication_id TEXT NOT NULL,
    write_authority_flag INTEGER NOT NULL CHECK (write_authority_flag IN (0, 1)),
    verified_online_at_utc TEXT NOT NULL,
    offline_expires_at_utc TEXT NOT NULL,
    recorded_at_utc TEXT NOT NULL,
    CHECK (offline_expires_at_utc > verified_online_at_utc)
) STRICT;

CREATE TABLE authority_verification_status_event (
    authority_verification_status_event_id TEXT PRIMARY KEY,
    authority_verification_id TEXT NOT NULL REFERENCES authority_verification(authority_verification_id),
    verification_status TEXT NOT NULL CHECK (verification_status IN ('Verified', 'Revoked')),
    status_reason TEXT NOT NULL,
    recorded_at_utc TEXT NOT NULL
) STRICT;

CREATE TABLE database_access_session (
    database_access_session_id TEXT PRIMARY KEY,
    database_id TEXT NOT NULL REFERENCES commodity_database(database_id),
    stable_user_id TEXT NOT NULL,
    registered_device_id TEXT NOT NULL,
    authority_verification_id TEXT REFERENCES authority_verification(authority_verification_id),
    access_mode TEXT NOT NULL CHECK (access_mode IN ('Read Write', 'Read Only')),
    session_status TEXT NOT NULL CHECK (session_status IN ('Active', 'Locked', 'Closed')),
    opened_at_utc TEXT NOT NULL,
    last_activity_at_utc TEXT NOT NULL,
    inactivity_expires_at_utc TEXT NOT NULL,
    terminal_reason TEXT,
    CHECK (inactivity_expires_at_utc > last_activity_at_utc),
    CHECK ((session_status = 'Active') = (terminal_reason IS NULL))
) STRICT;

CREATE INDEX ix_authority_user_device
ON authority_verification(stable_user_id, registered_device_id,
    verified_online_at_utc DESC);

CREATE INDEX ix_authority_status
ON authority_verification_status_event(authority_verification_id,
    recorded_at_utc DESC, authority_verification_status_event_id DESC);

CREATE INDEX ix_access_session_active
ON database_access_session(session_status, inactivity_expires_at_utc);

CREATE VIEW v_current_authority_verification_status AS
SELECT status.*
FROM authority_verification_status_event status
WHERE NOT EXISTS (
    SELECT 1 FROM authority_verification_status_event newer
    WHERE newer.authority_verification_id = status.authority_verification_id
      AND (newer.recorded_at_utc > status.recorded_at_utc
           OR (newer.recorded_at_utc = status.recorded_at_utc
               AND newer.rowid > status.rowid))
);

CREATE TRIGGER no_update_authority_verification BEFORE UPDATE ON authority_verification
BEGIN SELECT RAISE(ABORT, 'authority_verification is immutable'); END;
CREATE TRIGGER no_delete_authority_verification BEFORE DELETE ON authority_verification
BEGIN SELECT RAISE(ABORT, 'authority_verification cannot be deleted'); END;
CREATE TRIGGER no_update_authority_status BEFORE UPDATE ON authority_verification_status_event
BEGIN SELECT RAISE(ABORT, 'authority_verification_status_event is immutable'); END;
CREATE TRIGGER no_delete_authority_status BEFORE DELETE ON authority_verification_status_event
BEGIN SELECT RAISE(ABORT, 'authority_verification_status_event cannot be deleted'); END;

INSERT INTO schema_migration VALUES (
    '0016', 'local_authority_sessions', '__FORGE_MIGRATION_SHA256__',
    strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'schema-prototype-0.16'
);

COMMIT;
