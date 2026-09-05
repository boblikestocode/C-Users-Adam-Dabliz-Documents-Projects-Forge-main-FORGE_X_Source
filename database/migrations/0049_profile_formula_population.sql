-- Preserve legacy profile reproduction while governing new formula populations.
PRAGMA foreign_keys = ON;
BEGIN IMMEDIATE;

ALTER TABLE supplier_profile_reproduction_manifest
ADD COLUMN formula_population_version TEXT NOT NULL DEFAULT 'Legacy'
CHECK (formula_population_version IN ('Legacy', 'Economic Age and Cutoff v1'));

INSERT INTO schema_migration VALUES
('0049', 'profile_formula_population', '__FORGE_MIGRATION_SHA256__',
 strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'schema-prototype-0.49');
COMMIT;
