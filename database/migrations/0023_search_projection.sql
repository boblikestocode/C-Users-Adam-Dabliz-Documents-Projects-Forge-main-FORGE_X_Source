-- Forge X rebuildable full-text and faceted search projection

PRAGMA foreign_keys = ON;
BEGIN IMMEDIATE;

CREATE TABLE search_generation_manifest (
    search_generation_id TEXT PRIMARY KEY,
    evidence_cutoff_utc TEXT NOT NULL,
    build_manifest_hash TEXT NOT NULL CHECK (length(build_manifest_hash) = 64),
    document_count INTEGER NOT NULL CHECK (document_count >= 0),
    generation_status TEXT NOT NULL CHECK (generation_status = 'Complete'),
    created_at_utc TEXT NOT NULL
) STRICT;

CREATE TABLE search_document_projection (
    search_generation_id TEXT NOT NULL REFERENCES search_generation_manifest(search_generation_id),
    search_document_id TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    title TEXT NOT NULL,
    subtitle TEXT,
    searchable_text TEXT NOT NULL,
    commodity_id TEXT,
    event_id TEXT,
    supplier_id TEXT,
    supplier_plant_id TEXT,
    part_id TEXT,
    status TEXT,
    evidence_cutoff_utc TEXT NOT NULL,
    build_manifest_hash TEXT NOT NULL,
    PRIMARY KEY (search_generation_id, search_document_id),
    UNIQUE (search_generation_id, entity_type, entity_id)
) WITHOUT ROWID, STRICT;

CREATE VIRTUAL TABLE search_document_fts USING fts5(
    search_generation_id UNINDEXED,
    search_document_id UNINDEXED,
    searchable_text,
    tokenize = 'unicode61 remove_diacritics 2'
);

CREATE INDEX ix_search_document_facets
ON search_document_projection(
    search_generation_id, entity_type, commodity_id, event_id,
    supplier_id, supplier_plant_id, part_id, status, search_document_id
);

CREATE TRIGGER no_update_search_generation BEFORE UPDATE ON search_generation_manifest
BEGIN SELECT RAISE(ABORT, 'search_generation_manifest is immutable'); END;
CREATE TRIGGER no_delete_search_generation BEFORE DELETE ON search_generation_manifest
BEGIN SELECT RAISE(ABORT, 'search_generation_manifest cannot be deleted'); END;
CREATE TRIGGER no_update_search_document BEFORE UPDATE ON search_document_projection
BEGIN SELECT RAISE(ABORT, 'search_document_projection is immutable'); END;
CREATE TRIGGER no_delete_search_document BEFORE DELETE ON search_document_projection
BEGIN SELECT RAISE(ABORT, 'search_document_projection cannot be deleted'); END;

INSERT INTO schema_migration VALUES (
    '0023', 'search_projection', '__FORGE_MIGRATION_SHA256__',
    strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'schema-prototype-0.23'
);

COMMIT;
