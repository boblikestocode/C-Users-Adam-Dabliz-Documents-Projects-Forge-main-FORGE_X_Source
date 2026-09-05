-- Retain prior profile populations; new builds enforce regional lineage and age.
PRAGMA foreign_keys = ON;
BEGIN IMMEDIATE;

ALTER TABLE supplier_profile_reproduction_manifest
ADD COLUMN scope_population_version TEXT NOT NULL DEFAULT 'Legacy'
CHECK (scope_population_version IN ('Legacy', 'Regional Age v1'));

ALTER TABLE projection_generation_manifest
ADD COLUMN economic_age_population_version TEXT NOT NULL DEFAULT 'Legacy'
CHECK (economic_age_population_version IN ('Legacy', 'Cutoff Age v1'));

CREATE INDEX ix_location_observation_cutoff
ON location_measure_evidence(observation_id, recorded_at_utc);

INSERT INTO schema_migration VALUES
('0050', 'profile_scope_population', '__FORGE_MIGRATION_SHA256__',
 strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'schema-prototype-0.50');
COMMIT;
