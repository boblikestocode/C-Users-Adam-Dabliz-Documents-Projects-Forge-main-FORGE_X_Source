-- Forge X canonical part identity and permanent commodity ownership
PRAGMA foreign_keys = ON;
BEGIN IMMEDIATE;

CREATE TABLE part_commodity_assignment (
    part_commodity_assignment_id TEXT PRIMARY KEY,
    stable_part_id TEXT NOT NULL UNIQUE,
    stable_commodity_id TEXT NOT NULL,
    approved_by_user_id TEXT NOT NULL,
    approval_reason TEXT NOT NULL,
    recorded_at_utc TEXT NOT NULL
) STRICT;

CREATE TRIGGER validate_canonical_part_insert
BEFORE INSERT ON part
WHEN length(NEW.normalized_part_number) <> 10
  OR NEW.normalized_part_number <> upper(NEW.normalized_part_number)
  OR NEW.normalized_part_number GLOB '*[^A-Z0-9]*'
  OR NEW.canonical_description IS NULL
  OR trim(NEW.canonical_description) = ''
BEGIN SELECT RAISE(ABORT, 'canonical part requires 10 uppercase alphanumeric characters and a description'); END;

CREATE TRIGGER validate_part_commodity_assignment
BEFORE INSERT ON part_commodity_assignment
WHEN NOT EXISTS (
        SELECT 1 FROM part WHERE stable_part_id = NEW.stable_part_id
    ) OR NOT EXISTS (
        SELECT 1 FROM commodity
        WHERE stable_commodity_id = NEW.stable_commodity_id
          AND status = 'Active' AND business_valid_to IS NULL
    ) OR trim(NEW.approval_reason) = ''
BEGIN SELECT RAISE(ABORT, 'part commodity assignment requires current part, active commodity, and reason'); END;

CREATE TRIGGER no_update_part_commodity_assignment
BEFORE UPDATE ON part_commodity_assignment
BEGIN SELECT RAISE(ABORT, 'part_commodity_assignment is immutable'); END;
CREATE TRIGGER no_delete_part_commodity_assignment
BEFORE DELETE ON part_commodity_assignment
BEGIN SELECT RAISE(ABORT, 'part_commodity_assignment cannot be deleted'); END;

INSERT INTO schema_migration VALUES (
    '0003', 'part_commodity_ownership', '__FORGE_MIGRATION_SHA256__',
    strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'registry-prototype-0.3'
);
COMMIT;
