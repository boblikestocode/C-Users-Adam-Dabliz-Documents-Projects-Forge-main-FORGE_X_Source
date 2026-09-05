-- Complete the approved knowledge hierarchy with supplier-family and company-wide scope.

PRAGMA foreign_keys = ON;
BEGIN IMMEDIATE;

ALTER TABLE knowledge_scope RENAME TO knowledge_scope_v1;

CREATE TABLE knowledge_scope (
    knowledge_scope_id TEXT PRIMARY KEY,
    knowledge_version_id TEXT NOT NULL REFERENCES knowledge_version(knowledge_version_id),
    supplier_id TEXT,
    supplier_plant_id TEXT,
    supplier_family_id TEXT,
    commodity_id TEXT,
    part_id TEXT,
    functional_family_id TEXT REFERENCES functional_part_family(functional_family_id),
    process_code TEXT,
    region_code TEXT,
    program_id TEXT,
    company_wide_flag INTEGER NOT NULL DEFAULT 0 CHECK (company_wide_flag IN (0, 1)),
    scope_specificity INTEGER NOT NULL CHECK (scope_specificity BETWEEN 1 AND 6),
    scope_signature TEXT NOT NULL,
    recorded_at_utc TEXT NOT NULL,
    UNIQUE (knowledge_version_id, scope_signature),
    CHECK (
        (company_wide_flag = 1
         AND supplier_id IS NULL AND supplier_plant_id IS NULL
         AND supplier_family_id IS NULL AND commodity_id IS NULL
         AND part_id IS NULL AND functional_family_id IS NULL
         AND process_code IS NULL AND region_code IS NULL AND program_id IS NULL)
        OR
        (company_wide_flag = 0
         AND (supplier_id IS NOT NULL OR supplier_plant_id IS NOT NULL
              OR supplier_family_id IS NOT NULL OR commodity_id IS NOT NULL
              OR part_id IS NOT NULL OR functional_family_id IS NOT NULL
              OR process_code IS NOT NULL OR region_code IS NOT NULL
              OR program_id IS NOT NULL))
    )
) STRICT;

INSERT INTO knowledge_scope (
    knowledge_scope_id, knowledge_version_id, supplier_id,
    supplier_plant_id, commodity_id, part_id, functional_family_id,
    process_code, region_code, program_id, company_wide_flag,
    scope_specificity, scope_signature, recorded_at_utc
)
SELECT
    knowledge_scope_id, knowledge_version_id, supplier_id,
    supplier_plant_id, commodity_id, part_id, functional_family_id,
    process_code, region_code, program_id, 0,
    scope_specificity, scope_signature, recorded_at_utc
FROM knowledge_scope_v1;

DROP TABLE knowledge_scope_v1;

CREATE INDEX ix_knowledge_scope_signature
ON knowledge_scope(scope_signature, scope_specificity, knowledge_version_id);

CREATE INDEX ix_knowledge_scope_dimensions
ON knowledge_scope(
    supplier_id, supplier_plant_id, supplier_family_id, commodity_id,
    part_id, functional_family_id, region_code, company_wide_flag
);

INSERT INTO schema_migration VALUES (
    '0003', 'knowledge_scope', '__FORGE_MIGRATION_SHA256__',
    strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'schema-prototype-0.3'
);

COMMIT;
