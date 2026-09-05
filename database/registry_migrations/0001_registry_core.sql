-- Forge X central registry foundation
-- SQLite 3.37+ / SQLCipher-compatible SQL

PRAGMA foreign_keys = ON;
BEGIN IMMEDIATE;

CREATE TABLE schema_migration (
    migration_version TEXT PRIMARY KEY,
    migration_name TEXT NOT NULL,
    migration_checksum TEXT NOT NULL,
    applied_at_utc TEXT NOT NULL,
    application_build TEXT NOT NULL
) STRICT;

CREATE TABLE registry_identity (
    registry_id TEXT PRIMARY KEY CHECK (length(registry_id) = 36),
    created_at_utc TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    registry_instance_nonce TEXT NOT NULL UNIQUE
) STRICT;

CREATE TABLE organization_user (
    user_version_id TEXT PRIMARY KEY,
    stable_user_id TEXT NOT NULL,
    directory_object_id TEXT NOT NULL,
    display_name TEXT NOT NULL,
    email TEXT,
    active_flag INTEGER NOT NULL CHECK (active_flag IN (0, 1)),
    business_valid_from TEXT NOT NULL,
    business_valid_to TEXT,
    recorded_at_utc TEXT NOT NULL,
    supersedes_user_version_id TEXT REFERENCES organization_user(user_version_id),
    CHECK (business_valid_to IS NULL OR business_valid_to > business_valid_from),
    CHECK (supersedes_user_version_id IS NULL OR supersedes_user_version_id <> user_version_id)
) STRICT;

CREATE UNIQUE INDEX ux_user_active_directory_id
ON organization_user(directory_object_id)
WHERE business_valid_to IS NULL AND active_flag = 1;
CREATE INDEX ix_user_stable_history ON organization_user(stable_user_id, recorded_at_utc DESC);

CREATE TABLE buyer_code (
    buyer_code_version_id TEXT PRIMARY KEY,
    stable_buyer_code_id TEXT NOT NULL,
    normalized_code TEXT NOT NULL,
    displayed_code TEXT NOT NULL,
    buyer_name TEXT NOT NULL,
    active_flag INTEGER NOT NULL CHECK (active_flag IN (0, 1)),
    business_valid_from TEXT NOT NULL,
    business_valid_to TEXT,
    recorded_at_utc TEXT NOT NULL,
    supersedes_buyer_code_version_id TEXT REFERENCES buyer_code(buyer_code_version_id),
    CHECK (business_valid_to IS NULL OR business_valid_to > business_valid_from)
) STRICT;

CREATE UNIQUE INDEX ux_buyer_code_active ON buyer_code(normalized_code)
WHERE business_valid_to IS NULL AND active_flag = 1;

CREATE TABLE buyer_identity (
    buyer_identity_version_id TEXT PRIMARY KEY,
    stable_buyer_identity_id TEXT NOT NULL,
    stable_user_id TEXT NOT NULL,
    stable_buyer_code_id TEXT NOT NULL,
    business_valid_from TEXT NOT NULL,
    business_valid_to TEXT,
    recorded_at_utc TEXT NOT NULL,
    supersedes_buyer_identity_version_id TEXT REFERENCES buyer_identity(buyer_identity_version_id),
    CHECK (business_valid_to IS NULL OR business_valid_to > business_valid_from)
) STRICT;
CREATE INDEX ix_buyer_identity_user ON buyer_identity(stable_user_id, business_valid_from, business_valid_to);

CREATE TABLE role_definition (
    role_version_id TEXT PRIMARY KEY,
    stable_role_id TEXT NOT NULL,
    role_code TEXT NOT NULL,
    role_name TEXT NOT NULL,
    permission_payload TEXT NOT NULL,
    permission_payload_hash TEXT NOT NULL,
    business_valid_from TEXT NOT NULL,
    business_valid_to TEXT,
    recorded_at_utc TEXT NOT NULL,
    supersedes_role_version_id TEXT REFERENCES role_definition(role_version_id)
) STRICT;
CREATE UNIQUE INDEX ux_role_active_code ON role_definition(role_code) WHERE business_valid_to IS NULL;

CREATE TABLE authority_assignment (
    authority_version_id TEXT PRIMARY KEY,
    stable_authority_id TEXT NOT NULL,
    stable_user_id TEXT NOT NULL,
    stable_role_id TEXT NOT NULL,
    scope_type TEXT NOT NULL CHECK (scope_type IN ('Company', 'Commodity', 'Event')),
    scope_id TEXT,
    business_valid_from TEXT NOT NULL,
    business_valid_to TEXT,
    approved_by_user_id TEXT NOT NULL,
    approval_reason TEXT NOT NULL,
    recorded_at_utc TEXT NOT NULL,
    supersedes_authority_version_id TEXT REFERENCES authority_assignment(authority_version_id),
    CHECK ((scope_type = 'Company' AND scope_id IS NULL) OR (scope_type <> 'Company' AND scope_id IS NOT NULL))
) STRICT;
CREATE INDEX ix_authority_user_scope ON authority_assignment(stable_user_id, scope_type, scope_id, business_valid_from, business_valid_to);

CREATE TABLE commodity (
    commodity_version_id TEXT PRIMARY KEY,
    stable_commodity_id TEXT NOT NULL,
    commodity_code TEXT NOT NULL,
    locked_name TEXT NOT NULL,
    normalized_name TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('Active', 'Inactive', 'Superseded')),
    business_valid_from TEXT NOT NULL,
    business_valid_to TEXT,
    approved_by_user_id TEXT NOT NULL,
    recorded_at_utc TEXT NOT NULL,
    supersedes_commodity_version_id TEXT REFERENCES commodity(commodity_version_id)
) STRICT;
CREATE UNIQUE INDEX ux_commodity_active_code ON commodity(commodity_code) WHERE business_valid_to IS NULL AND status = 'Active';
CREATE UNIQUE INDEX ux_commodity_active_name ON commodity(normalized_name) WHERE business_valid_to IS NULL AND status = 'Active';
CREATE INDEX ix_commodity_stable_history ON commodity(stable_commodity_id, recorded_at_utc DESC);

CREATE TABLE commodity_assignment (
    assignment_version_id TEXT PRIMARY KEY,
    stable_assignment_id TEXT NOT NULL,
    stable_commodity_id TEXT NOT NULL,
    stable_buyer_code_id TEXT NOT NULL,
    assignment_role TEXT NOT NULL CHECK (assignment_role IN ('Primary Buyer', 'Assigned Buyer', 'Manager', 'Read Only')),
    business_valid_from TEXT NOT NULL,
    business_valid_to TEXT,
    approved_by_user_id TEXT NOT NULL,
    recorded_at_utc TEXT NOT NULL,
    supersedes_assignment_version_id TEXT REFERENCES commodity_assignment(assignment_version_id)
) STRICT;
CREATE INDEX ix_commodity_assignment_access ON commodity_assignment(stable_buyer_code_id, stable_commodity_id, business_valid_from, business_valid_to);

CREATE TABLE vehicle_line (
    vehicle_line_version_id TEXT PRIMARY KEY,
    stable_vehicle_line_id TEXT NOT NULL,
    vehicle_line_code TEXT,
    vehicle_line_name TEXT NOT NULL,
    business_valid_from TEXT NOT NULL,
    business_valid_to TEXT,
    recorded_at_utc TEXT NOT NULL,
    supersedes_vehicle_line_version_id TEXT REFERENCES vehicle_line(vehicle_line_version_id)
) STRICT;
CREATE INDEX ix_vehicle_line_stable ON vehicle_line(stable_vehicle_line_id, recorded_at_utc DESC);

CREATE TABLE program (
    program_version_id TEXT PRIMARY KEY,
    stable_program_id TEXT NOT NULL,
    stable_vehicle_line_id TEXT,
    program_code TEXT NOT NULL,
    program_name TEXT NOT NULL,
    planned_start_date TEXT,
    planned_end_date TEXT,
    business_valid_from TEXT NOT NULL,
    business_valid_to TEXT,
    recorded_at_utc TEXT NOT NULL,
    supersedes_program_version_id TEXT REFERENCES program(program_version_id)
) STRICT;
CREATE UNIQUE INDEX ux_program_active_code ON program(program_code) WHERE business_valid_to IS NULL;

CREATE TABLE program_relationship (
    relationship_version_id TEXT PRIMARY KEY,
    stable_relationship_id TEXT NOT NULL,
    from_program_id TEXT NOT NULL,
    to_program_id TEXT NOT NULL,
    relationship_type TEXT NOT NULL CHECK (relationship_type IN ('Predecessor', 'Successor', 'Related')),
    business_rationale TEXT NOT NULL,
    business_valid_from TEXT NOT NULL,
    business_valid_to TEXT,
    confirmed_by_user_id TEXT NOT NULL,
    recorded_at_utc TEXT NOT NULL,
    supersedes_relationship_version_id TEXT REFERENCES program_relationship(relationship_version_id),
    CHECK (from_program_id <> to_program_id)
) STRICT;

CREATE TABLE part (
    part_version_id TEXT PRIMARY KEY,
    stable_part_id TEXT NOT NULL,
    normalized_part_number TEXT NOT NULL,
    displayed_part_number TEXT NOT NULL,
    canonical_description TEXT,
    status TEXT NOT NULL CHECK (status IN ('Active', 'Inactive', 'Superseded')),
    business_valid_from TEXT NOT NULL,
    business_valid_to TEXT,
    confirmed_by_user_id TEXT NOT NULL,
    recorded_at_utc TEXT NOT NULL,
    supersedes_part_version_id TEXT REFERENCES part(part_version_id)
) STRICT;
CREATE UNIQUE INDEX ux_part_active_number ON part(normalized_part_number) WHERE business_valid_to IS NULL AND status = 'Active';
CREATE INDEX ix_part_stable_history ON part(stable_part_id, recorded_at_utc DESC);

CREATE TABLE part_program_applicability (
    applicability_version_id TEXT PRIMARY KEY,
    stable_applicability_id TEXT NOT NULL,
    stable_part_id TEXT NOT NULL,
    stable_program_id TEXT NOT NULL,
    vehicle_position TEXT,
    business_valid_from TEXT NOT NULL,
    business_valid_to TEXT,
    confirmed_by_user_id TEXT NOT NULL,
    recorded_at_utc TEXT NOT NULL,
    supersedes_applicability_version_id TEXT REFERENCES part_program_applicability(applicability_version_id)
) STRICT;
CREATE INDEX ix_part_program_lookup ON part_program_applicability(stable_program_id, stable_part_id, business_valid_from, business_valid_to);

CREATE TABLE supplier (
    supplier_version_id TEXT PRIMARY KEY,
    stable_supplier_id TEXT NOT NULL,
    supplier_code TEXT NOT NULL,
    legal_name TEXT,
    display_name TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('Active', 'Inactive', 'Superseded')),
    business_valid_from TEXT NOT NULL,
    business_valid_to TEXT,
    confirmed_by_user_id TEXT NOT NULL,
    recorded_at_utc TEXT NOT NULL,
    supersedes_supplier_version_id TEXT REFERENCES supplier(supplier_version_id)
) STRICT;
CREATE UNIQUE INDEX ux_supplier_active_code ON supplier(supplier_code) WHERE business_valid_to IS NULL AND status = 'Active';
CREATE INDEX ix_supplier_stable_history ON supplier(stable_supplier_id, recorded_at_utc DESC);

CREATE TABLE supplier_alias (
    alias_version_id TEXT PRIMARY KEY,
    stable_alias_id TEXT NOT NULL,
    stable_supplier_id TEXT NOT NULL,
    submitted_alias TEXT NOT NULL,
    normalized_alias TEXT NOT NULL,
    business_valid_from TEXT NOT NULL,
    business_valid_to TEXT,
    confirmed_by_user_id TEXT NOT NULL,
    recorded_at_utc TEXT NOT NULL,
    supersedes_alias_version_id TEXT REFERENCES supplier_alias(alias_version_id)
) STRICT;
CREATE INDEX ix_supplier_alias_lookup ON supplier_alias(normalized_alias, business_valid_from, business_valid_to);

CREATE TABLE supplier_plant (
    plant_version_id TEXT PRIMARY KEY,
    stable_plant_id TEXT NOT NULL,
    stable_supplier_id TEXT NOT NULL,
    plant_code TEXT,
    plant_name TEXT NOT NULL,
    country_code TEXT,
    region_code TEXT,
    location_evidence TEXT,
    business_valid_from TEXT NOT NULL,
    business_valid_to TEXT,
    confirmed_by_user_id TEXT NOT NULL,
    recorded_at_utc TEXT NOT NULL,
    supersedes_plant_version_id TEXT REFERENCES supplier_plant(plant_version_id)
) STRICT;
CREATE INDEX ix_plant_supplier_region ON supplier_plant(stable_supplier_id, region_code, business_valid_from, business_valid_to);

CREATE TABLE supplier_family (
    family_version_id TEXT PRIMARY KEY,
    stable_family_id TEXT NOT NULL,
    family_name TEXT NOT NULL,
    business_valid_from TEXT NOT NULL,
    business_valid_to TEXT,
    approved_by_master_user_id TEXT NOT NULL,
    approval_reason TEXT NOT NULL,
    recorded_at_utc TEXT NOT NULL,
    supersedes_family_version_id TEXT REFERENCES supplier_family(family_version_id)
) STRICT;

CREATE TABLE supplier_family_member (
    membership_version_id TEXT PRIMARY KEY,
    stable_membership_id TEXT NOT NULL,
    stable_family_id TEXT NOT NULL,
    stable_supplier_id TEXT NOT NULL,
    business_valid_from TEXT NOT NULL,
    business_valid_to TEXT,
    approved_by_master_user_id TEXT NOT NULL,
    approval_reason TEXT NOT NULL,
    recorded_at_utc TEXT NOT NULL,
    supersedes_membership_version_id TEXT REFERENCES supplier_family_member(membership_version_id)
) STRICT;
CREATE INDEX ix_family_member_supplier ON supplier_family_member(stable_supplier_id, business_valid_from, business_valid_to);

CREATE TABLE unit_definition (
    unit_version_id TEXT PRIMARY KEY,
    stable_unit_id TEXT NOT NULL,
    unit_code TEXT NOT NULL,
    unit_category TEXT NOT NULL,
    canonical_label TEXT NOT NULL,
    active_flag INTEGER NOT NULL CHECK (active_flag IN (0, 1)),
    business_valid_from TEXT NOT NULL,
    business_valid_to TEXT,
    recorded_at_utc TEXT NOT NULL,
    supersedes_unit_version_id TEXT REFERENCES unit_definition(unit_version_id)
) STRICT;
CREATE UNIQUE INDEX ux_unit_active_code ON unit_definition(unit_code) WHERE business_valid_to IS NULL AND active_flag = 1;

CREATE TABLE currency_definition (
    currency_version_id TEXT PRIMARY KEY,
    stable_currency_id TEXT NOT NULL,
    iso_code TEXT NOT NULL CHECK (length(iso_code) = 3),
    display_name TEXT NOT NULL,
    active_flag INTEGER NOT NULL CHECK (active_flag IN (0, 1)),
    business_valid_from TEXT NOT NULL,
    business_valid_to TEXT,
    recorded_at_utc TEXT NOT NULL,
    supersedes_currency_version_id TEXT REFERENCES currency_definition(currency_version_id)
) STRICT;
CREATE UNIQUE INDEX ux_currency_active_code ON currency_definition(iso_code) WHERE business_valid_to IS NULL AND active_flag = 1;

CREATE TABLE master_data_request (
    request_id TEXT PRIMARY KEY,
    request_type TEXT NOT NULL,
    subject_entity_type TEXT NOT NULL,
    subject_entity_id TEXT,
    proposed_payload TEXT NOT NULL,
    requested_by_user_id TEXT NOT NULL,
    request_reason TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('Open', 'Under Review', 'Approved', 'Rejected', 'Withdrawn')),
    requested_at_utc TEXT NOT NULL
) STRICT;

CREATE TABLE master_data_decision (
    decision_id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL REFERENCES master_data_request(request_id),
    decision TEXT NOT NULL CHECK (decision IN ('Approve', 'Reject')),
    decided_by_user_id TEXT NOT NULL,
    decision_reason TEXT NOT NULL,
    decided_at_utc TEXT NOT NULL,
    UNIQUE (request_id)
) STRICT;

CREATE TABLE registry_audit_event (
    audit_event_id TEXT PRIMARY KEY,
    recorded_sequence INTEGER NOT NULL UNIQUE CHECK (recorded_sequence > 0),
    event_type TEXT NOT NULL,
    actor_user_id TEXT,
    occurred_at_utc TEXT NOT NULL,
    application_version TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    reason_text TEXT,
    event_payload TEXT NOT NULL,
    prior_event_hash TEXT,
    event_hash TEXT NOT NULL UNIQUE,
    CHECK ((recorded_sequence = 1 AND prior_event_hash IS NULL) OR (recorded_sequence > 1 AND prior_event_hash IS NOT NULL))
) STRICT;

CREATE TABLE registry_publication (
    publication_id TEXT PRIMARY KEY,
    registry_version INTEGER NOT NULL UNIQUE CHECK (registry_version > 0),
    schema_version TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    audit_chain_anchor TEXT NOT NULL,
    signature TEXT NOT NULL,
    prior_publication_id TEXT REFERENCES registry_publication(publication_id),
    created_at_utc TEXT NOT NULL
) STRICT;

CREATE VIEW v_current_commodity AS
SELECT c.* FROM commodity c
WHERE c.business_valid_to IS NULL AND c.status = 'Active';

CREATE VIEW v_current_supplier AS
SELECT s.* FROM supplier s
WHERE s.business_valid_to IS NULL AND s.status = 'Active';

CREATE VIEW v_current_part AS
SELECT p.* FROM part p
WHERE p.business_valid_to IS NULL AND p.status = 'Active';

CREATE TRIGGER no_update_registry_audit BEFORE UPDATE ON registry_audit_event BEGIN SELECT RAISE(ABORT, 'registry_audit_event is immutable'); END;
CREATE TRIGGER no_delete_registry_audit BEFORE DELETE ON registry_audit_event BEGIN SELECT RAISE(ABORT, 'registry_audit_event cannot be deleted'); END;
CREATE TRIGGER no_update_registry_publication BEFORE UPDATE ON registry_publication BEGIN SELECT RAISE(ABORT, 'registry_publication is immutable'); END;
CREATE TRIGGER no_delete_registry_publication BEFORE DELETE ON registry_publication BEGIN SELECT RAISE(ABORT, 'registry_publication cannot be deleted'); END;
CREATE TRIGGER no_update_master_decision BEFORE UPDATE ON master_data_decision BEGIN SELECT RAISE(ABORT, 'master_data_decision is immutable'); END;
CREATE TRIGGER no_delete_master_decision BEFORE DELETE ON master_data_decision BEGIN SELECT RAISE(ABORT, 'master_data_decision cannot be deleted'); END;

INSERT INTO schema_migration VALUES (
    '0001', 'registry_core', '__FORGE_MIGRATION_SHA256__',
    strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'schema-prototype-0.1'
);

COMMIT;
