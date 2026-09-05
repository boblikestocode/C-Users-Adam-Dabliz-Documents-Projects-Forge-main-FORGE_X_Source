-- Forge X governed supplier-family proposals and membership lifecycle
PRAGMA foreign_keys = ON;
BEGIN IMMEDIATE;
CREATE TABLE supplier_family_proposal (
    supplier_family_proposal_id TEXT PRIMARY KEY,
    stable_supplier_id TEXT NOT NULL,
    proposed_family_name TEXT,
    proposed_stable_family_id TEXT,
    proposal_reason TEXT NOT NULL CHECK (length(trim(proposal_reason)) > 0),
    proposed_by_user_id TEXT NOT NULL CHECK (length(trim(proposed_by_user_id)) > 0),
    proposed_at_utc TEXT NOT NULL,
    CHECK ((proposed_family_name IS NOT NULL) <> (proposed_stable_family_id IS NOT NULL))
) STRICT;
CREATE TABLE supplier_family_proposal_decision (
    supplier_family_proposal_decision_id TEXT PRIMARY KEY,
    supplier_family_proposal_id TEXT NOT NULL REFERENCES supplier_family_proposal(supplier_family_proposal_id),
    decision_status TEXT NOT NULL CHECK (decision_status IN ('Approved', 'Rejected')),
    stable_family_id TEXT,
    decision_reason TEXT NOT NULL CHECK (length(trim(decision_reason)) > 0),
    decided_by_master_user_id TEXT NOT NULL,
    decided_at_utc TEXT NOT NULL,
    UNIQUE (supplier_family_proposal_id),
    CHECK ((decision_status = 'Approved') = (stable_family_id IS NOT NULL))
) STRICT;
CREATE VIEW v_current_supplier_family AS
SELECT family.* FROM supplier_family family
WHERE NOT EXISTS (SELECT 1 FROM supplier_family newer
                  WHERE newer.supersedes_family_version_id = family.family_version_id);
CREATE VIEW v_current_supplier_family_member AS
SELECT member.* FROM supplier_family_member member
WHERE NOT EXISTS (SELECT 1 FROM supplier_family_member newer
                  WHERE newer.supersedes_membership_version_id = member.membership_version_id);
CREATE TRIGGER validate_supplier_family_insert BEFORE INSERT ON supplier_family
BEGIN
    SELECT CASE WHEN EXISTS (
        SELECT 1 FROM v_current_supplier_family current
        WHERE upper(trim(current.family_name)) = upper(trim(NEW.family_name))
          AND current.stable_family_id <> NEW.stable_family_id)
    THEN RAISE(ABORT, 'current supplier family name already exists') END;
END;
CREATE TRIGGER validate_supplier_family_member_insert BEFORE INSERT ON supplier_family_member
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM v_current_supplier WHERE stable_supplier_id = NEW.stable_supplier_id)
    THEN RAISE(ABORT, 'supplier family member requires current supplier') END;
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM v_current_supplier_family WHERE stable_family_id = NEW.stable_family_id)
    THEN RAISE(ABORT, 'supplier family member requires current family') END;
    SELECT CASE WHEN NEW.business_valid_to IS NULL AND EXISTS (
        SELECT 1 FROM v_current_supplier_family_member current
        WHERE current.stable_supplier_id = NEW.stable_supplier_id
          AND current.business_valid_to IS NULL)
    THEN RAISE(ABORT, 'supplier already has an active family membership') END;
END;
CREATE TRIGGER supplier_family_proposal_no_update BEFORE UPDATE ON supplier_family_proposal
BEGIN SELECT RAISE(ABORT, 'supplier_family_proposal is immutable'); END;
CREATE TRIGGER supplier_family_proposal_no_delete BEFORE DELETE ON supplier_family_proposal
BEGIN SELECT RAISE(ABORT, 'supplier_family_proposal cannot be deleted'); END;
CREATE TRIGGER supplier_family_decision_no_update BEFORE UPDATE ON supplier_family_proposal_decision
BEGIN SELECT RAISE(ABORT, 'supplier_family_proposal_decision is immutable'); END;
CREATE TRIGGER supplier_family_decision_no_delete BEFORE DELETE ON supplier_family_proposal_decision
BEGIN SELECT RAISE(ABORT, 'supplier_family_proposal_decision cannot be deleted'); END;
CREATE TRIGGER supplier_family_no_update BEFORE UPDATE ON supplier_family
BEGIN SELECT RAISE(ABORT, 'supplier_family is append-only'); END;
CREATE TRIGGER supplier_family_no_delete BEFORE DELETE ON supplier_family
BEGIN SELECT RAISE(ABORT, 'supplier_family cannot be deleted'); END;
CREATE TRIGGER supplier_family_member_no_update BEFORE UPDATE ON supplier_family_member
BEGIN SELECT RAISE(ABORT, 'supplier_family_member is append-only'); END;
CREATE TRIGGER supplier_family_member_no_delete BEFORE DELETE ON supplier_family_member
BEGIN SELECT RAISE(ABORT, 'supplier_family_member cannot be deleted'); END;
INSERT INTO schema_migration VALUES ('0004', 'supplier_family_governance', '__FORGE_MIGRATION_SHA256__',
    strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'registry-prototype-0.4');
COMMIT;
