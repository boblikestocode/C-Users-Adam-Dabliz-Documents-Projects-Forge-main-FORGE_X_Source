-- Forge X exact-part and buyer-confirmed family history projection

PRAGMA foreign_keys = ON;
BEGIN IMMEDIATE;

CREATE TABLE part_history_generation_manifest (
    part_history_generation_id TEXT PRIMARY KEY,
    anchor_part_id TEXT NOT NULL,
    evidence_cutoff_utc TEXT NOT NULL,
    build_manifest_hash TEXT NOT NULL CHECK (length(build_manifest_hash) = 64),
    projected_row_count INTEGER NOT NULL CHECK (projected_row_count >= 0),
    generation_status TEXT NOT NULL CHECK (generation_status = 'Complete'),
    created_at_utc TEXT NOT NULL
) STRICT;

CREATE TABLE part_history_projection (
    part_history_generation_id TEXT NOT NULL REFERENCES part_history_generation_manifest(part_history_generation_id),
    part_history_row_id TEXT NOT NULL,
    anchor_part_id TEXT NOT NULL,
    evidence_part_id TEXT NOT NULL,
    observation_id TEXT NOT NULL REFERENCES pbd_observation(observation_id),
    submitted_datum_id TEXT NOT NULL REFERENCES submitted_datum(submitted_datum_id),
    evidence_class TEXT NOT NULL CHECK (evidence_class IN ('Exact Part', 'Buyer-Confirmed Functional Family')),
    comparison_field TEXT NOT NULL,
    comparison_eligibility TEXT NOT NULL CHECK (comparison_eligibility IN ('Governing', 'Directional')),
    functional_family_id TEXT REFERENCES functional_part_family(functional_family_id),
    family_version_id TEXT REFERENCES family_version(family_version_id),
    relationship_type TEXT,
    business_rationale TEXT,
    supplier_id TEXT,
    supplier_plant_id TEXT,
    event_id TEXT,
    economic_date TEXT,
    decimal_coefficient TEXT NOT NULL,
    decimal_scale INTEGER NOT NULL CHECK (decimal_scale >= 0),
    currency_id TEXT,
    normalized_unit_id TEXT,
    evidence_cutoff_utc TEXT NOT NULL,
    build_manifest_hash TEXT NOT NULL,
    PRIMARY KEY (part_history_generation_id, part_history_row_id),
    CHECK ((evidence_class = 'Exact Part' AND anchor_part_id = evidence_part_id
            AND functional_family_id IS NULL AND family_version_id IS NULL
            AND relationship_type IS NULL AND business_rationale IS NULL)
        OR (evidence_class = 'Buyer-Confirmed Functional Family'
            AND anchor_part_id <> evidence_part_id AND functional_family_id IS NOT NULL
            AND family_version_id IS NOT NULL AND relationship_type IS NOT NULL
            AND business_rationale IS NOT NULL))
) WITHOUT ROWID, STRICT;

CREATE INDEX ix_part_history_lookup
ON part_history_projection(part_history_generation_id, evidence_class,
                           comparison_field, economic_date DESC, observation_id);

CREATE TRIGGER no_update_part_history_generation BEFORE UPDATE ON part_history_generation_manifest
BEGIN SELECT RAISE(ABORT, 'part_history_generation_manifest is immutable'); END;
CREATE TRIGGER no_delete_part_history_generation BEFORE DELETE ON part_history_generation_manifest
BEGIN SELECT RAISE(ABORT, 'part_history_generation_manifest cannot be deleted'); END;
CREATE TRIGGER no_update_part_history_projection BEFORE UPDATE ON part_history_projection
BEGIN SELECT RAISE(ABORT, 'part_history_projection is immutable'); END;
CREATE TRIGGER no_delete_part_history_projection BEFORE DELETE ON part_history_projection
BEGIN SELECT RAISE(ABORT, 'part_history_projection cannot be deleted'); END;

INSERT INTO schema_migration VALUES (
    '0024', 'part_history_projection', '__FORGE_MIGRATION_SHA256__',
    strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'schema-prototype-0.24'
);

COMMIT;
