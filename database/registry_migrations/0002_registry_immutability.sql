-- Registry business records are superseded by append-only versions, never rewritten.

PRAGMA foreign_keys = ON;
BEGIN IMMEDIATE;

DROP INDEX ux_commodity_active_code;
DROP INDEX ux_commodity_active_name;
DROP INDEX ux_part_active_number;
DROP INDEX ux_supplier_active_code;
DROP VIEW v_current_commodity;
DROP VIEW v_current_supplier;
DROP VIEW v_current_part;

CREATE VIEW v_current_commodity AS
SELECT c.* FROM commodity c
WHERE c.status = 'Active'
  AND NOT EXISTS (
      SELECT 1 FROM commodity newer
      WHERE newer.supersedes_commodity_version_id = c.commodity_version_id
  );

CREATE VIEW v_current_supplier AS
SELECT s.* FROM supplier s
WHERE s.status = 'Active'
  AND NOT EXISTS (
      SELECT 1 FROM supplier newer
      WHERE newer.supersedes_supplier_version_id = s.supplier_version_id
  );

CREATE VIEW v_current_part AS
SELECT p.* FROM part p
WHERE p.status = 'Active'
  AND NOT EXISTS (
      SELECT 1 FROM part newer
      WHERE newer.supersedes_part_version_id = p.part_version_id
  );

CREATE TRIGGER commodity_current_identity_guard
BEFORE INSERT ON commodity
BEGIN
    SELECT CASE WHEN EXISTS (
        SELECT 1 FROM v_current_commodity current
        WHERE (current.commodity_code = NEW.commodity_code
               OR current.normalized_name = NEW.normalized_name)
          AND current.stable_commodity_id <> NEW.stable_commodity_id
    ) THEN RAISE(ABORT, 'current commodity code or name already exists') END;
END;

CREATE TRIGGER supplier_current_identity_guard
BEFORE INSERT ON supplier
BEGIN
    SELECT CASE WHEN EXISTS (
        SELECT 1 FROM v_current_supplier current
        WHERE current.supplier_code = NEW.supplier_code
          AND current.stable_supplier_id <> NEW.stable_supplier_id
    ) THEN RAISE(ABORT, 'current supplier code already exists') END;
END;

CREATE TRIGGER part_current_identity_guard
BEFORE INSERT ON part
BEGIN
    SELECT CASE WHEN EXISTS (
        SELECT 1 FROM v_current_part current
        WHERE current.normalized_part_number = NEW.normalized_part_number
          AND current.stable_part_id <> NEW.stable_part_id
    ) THEN RAISE(ABORT, 'current part number already exists') END;
END;

CREATE TRIGGER no_update_commodity BEFORE UPDATE ON commodity BEGIN SELECT RAISE(ABORT, 'commodity is append-only; supersede it'); END;
CREATE TRIGGER no_delete_commodity BEFORE DELETE ON commodity BEGIN SELECT RAISE(ABORT, 'commodity cannot be deleted'); END;
CREATE TRIGGER no_update_supplier BEFORE UPDATE ON supplier BEGIN SELECT RAISE(ABORT, 'supplier is append-only; supersede it'); END;
CREATE TRIGGER no_delete_supplier BEFORE DELETE ON supplier BEGIN SELECT RAISE(ABORT, 'supplier cannot be deleted'); END;
CREATE TRIGGER no_update_supplier_plant BEFORE UPDATE ON supplier_plant BEGIN SELECT RAISE(ABORT, 'supplier_plant is append-only; supersede it'); END;
CREATE TRIGGER no_delete_supplier_plant BEFORE DELETE ON supplier_plant BEGIN SELECT RAISE(ABORT, 'supplier_plant cannot be deleted'); END;
CREATE TRIGGER no_update_part BEFORE UPDATE ON part BEGIN SELECT RAISE(ABORT, 'part is append-only; supersede it'); END;
CREATE TRIGGER no_delete_part BEFORE DELETE ON part BEGIN SELECT RAISE(ABORT, 'part cannot be deleted'); END;
CREATE TRIGGER no_update_program BEFORE UPDATE ON program BEGIN SELECT RAISE(ABORT, 'program is append-only; supersede it'); END;
CREATE TRIGGER no_delete_program BEFORE DELETE ON program BEGIN SELECT RAISE(ABORT, 'program cannot be deleted'); END;
CREATE TRIGGER no_update_unit BEFORE UPDATE ON unit_definition BEGIN SELECT RAISE(ABORT, 'unit_definition is append-only; supersede it'); END;
CREATE TRIGGER no_delete_unit BEFORE DELETE ON unit_definition BEGIN SELECT RAISE(ABORT, 'unit_definition cannot be deleted'); END;

INSERT INTO schema_migration VALUES (
    '0002', 'registry_immutability', '__FORGE_MIGRATION_SHA256__',
    strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'schema-prototype-0.2'
);

COMMIT;
