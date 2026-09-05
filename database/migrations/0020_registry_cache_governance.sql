-- Forge X registry-cache activation history and scenario pinning

PRAGMA foreign_keys = ON;
BEGIN IMMEDIATE;

CREATE TABLE registry_cache_activation_event (
    registry_cache_activation_event_id TEXT PRIMARY KEY,
    cache_generation_id TEXT NOT NULL REFERENCES registry_cache_generation(cache_generation_id),
    activation_status TEXT NOT NULL CHECK (
        activation_status IN ('Active', 'Superseded', 'Rejected')
    ),
    activation_reason TEXT NOT NULL,
    recorded_at_utc TEXT NOT NULL
) STRICT;

CREATE TABLE scenario_registry_cache_pin (
    scenario_registry_cache_pin_id TEXT PRIMARY KEY,
    scenario_revision_id TEXT NOT NULL UNIQUE REFERENCES scenario_revision(scenario_revision_id),
    cache_generation_id TEXT NOT NULL REFERENCES registry_cache_generation(cache_generation_id),
    registry_publication_id TEXT NOT NULL,
    registry_version TEXT NOT NULL,
    publication_hash TEXT NOT NULL CHECK (length(publication_hash) = 64),
    pinned_at_utc TEXT NOT NULL
) STRICT;

CREATE INDEX ix_registry_cache_activation_current
ON registry_cache_activation_event(cache_generation_id, recorded_at_utc DESC,
    registry_cache_activation_event_id DESC);

CREATE VIEW v_current_registry_cache_status AS
SELECT status.*
FROM registry_cache_activation_event status
WHERE NOT EXISTS (
    SELECT 1 FROM registry_cache_activation_event newer
    WHERE newer.cache_generation_id = status.cache_generation_id
      AND (newer.recorded_at_utc > status.recorded_at_utc
           OR (newer.recorded_at_utc = status.recorded_at_utc
               AND newer.rowid > status.rowid))
);

CREATE VIEW v_current_active_registry_cache AS
SELECT generation.*
FROM registry_cache_generation generation
JOIN v_current_registry_cache_status status
  ON status.cache_generation_id = generation.cache_generation_id
WHERE status.activation_status = 'Active';

CREATE TRIGGER no_update_registry_cache_generation BEFORE UPDATE ON registry_cache_generation
BEGIN SELECT RAISE(ABORT, 'registry_cache_generation is immutable'); END;
CREATE TRIGGER no_delete_registry_cache_generation BEFORE DELETE ON registry_cache_generation
BEGIN SELECT RAISE(ABORT, 'registry_cache_generation cannot be deleted'); END;
CREATE TRIGGER no_update_registry_entity_cache BEFORE UPDATE ON registry_entity_cache
BEGIN SELECT RAISE(ABORT, 'registry_entity_cache is immutable'); END;
CREATE TRIGGER no_delete_registry_entity_cache BEFORE DELETE ON registry_entity_cache
BEGIN SELECT RAISE(ABORT, 'registry_entity_cache cannot be deleted'); END;
CREATE TRIGGER no_update_registry_cache_activation BEFORE UPDATE ON registry_cache_activation_event
BEGIN SELECT RAISE(ABORT, 'registry_cache_activation_event is immutable'); END;
CREATE TRIGGER no_delete_registry_cache_activation BEFORE DELETE ON registry_cache_activation_event
BEGIN SELECT RAISE(ABORT, 'registry_cache_activation_event cannot be deleted'); END;
CREATE TRIGGER no_update_scenario_registry_pin BEFORE UPDATE ON scenario_registry_cache_pin
BEGIN SELECT RAISE(ABORT, 'scenario_registry_cache_pin is immutable'); END;
CREATE TRIGGER no_delete_scenario_registry_pin BEFORE DELETE ON scenario_registry_cache_pin
BEGIN SELECT RAISE(ABORT, 'scenario_registry_cache_pin cannot be deleted'); END;

INSERT INTO schema_migration VALUES (
    '0020', 'registry_cache_governance', '__FORGE_MIGRATION_SHA256__',
    strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'schema-prototype-0.20'
);

COMMIT;
