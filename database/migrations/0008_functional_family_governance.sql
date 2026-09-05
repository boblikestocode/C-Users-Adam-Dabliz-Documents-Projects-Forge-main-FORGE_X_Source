-- Forge X buyer-confirmed functional-family governance

PRAGMA foreign_keys = ON;
BEGIN IMMEDIATE;

ALTER TABLE family_version ADD COLUMN supporting_context_payload TEXT NOT NULL DEFAULT '{}';

CREATE UNIQUE INDEX ux_family_version_superseded_once
ON family_version(supersedes_family_version_id)
WHERE supersedes_family_version_id IS NOT NULL;

CREATE VIEW v_current_family_version AS
SELECT version.*
FROM family_version version
WHERE NOT EXISTS (
    SELECT 1 FROM family_version newer
    WHERE newer.supersedes_family_version_id = version.family_version_id
);

CREATE TRIGGER no_update_functional_part_family BEFORE UPDATE ON functional_part_family
BEGIN SELECT RAISE(ABORT, 'functional_part_family is immutable'); END;
CREATE TRIGGER no_delete_functional_part_family BEFORE DELETE ON functional_part_family
BEGIN SELECT RAISE(ABORT, 'functional_part_family cannot be deleted'); END;
CREATE TRIGGER no_update_family_version BEFORE UPDATE ON family_version
BEGIN SELECT RAISE(ABORT, 'family_version is immutable'); END;
CREATE TRIGGER no_delete_family_version BEFORE DELETE ON family_version
BEGIN SELECT RAISE(ABORT, 'family_version cannot be deleted'); END;
CREATE TRIGGER no_update_family_member BEFORE UPDATE ON family_member
BEGIN SELECT RAISE(ABORT, 'family_member is immutable'); END;
CREATE TRIGGER no_delete_family_member BEFORE DELETE ON family_member
BEGIN SELECT RAISE(ABORT, 'family_member cannot be deleted'); END;
CREATE TRIGGER no_update_family_permission BEFORE UPDATE ON family_comparison_permission
BEGIN SELECT RAISE(ABORT, 'family_comparison_permission is immutable'); END;
CREATE TRIGGER no_delete_family_permission BEFORE DELETE ON family_comparison_permission
BEGIN SELECT RAISE(ABORT, 'family_comparison_permission cannot be deleted'); END;

INSERT INTO schema_migration VALUES (
    '0008', 'functional_family_governance', '__FORGE_MIGRATION_SHA256__',
    strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'schema-prototype-0.8'
);

COMMIT;
