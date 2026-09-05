-- Forge X commodity database foundation
-- SQLite 3.37+ / SQLCipher-compatible SQL
-- Governing amounts use exact base-10 coefficient + scale; never REAL.

PRAGMA foreign_keys = ON;

BEGIN IMMEDIATE;

CREATE TABLE schema_migration (
    migration_version TEXT PRIMARY KEY,
    migration_name TEXT NOT NULL,
    migration_checksum TEXT NOT NULL,
    applied_at_utc TEXT NOT NULL,
    application_build TEXT NOT NULL
) STRICT;

CREATE TABLE commodity_database (
    database_id TEXT PRIMARY KEY,
    commodity_id TEXT NOT NULL,
    created_at_utc TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    database_instance_nonce TEXT NOT NULL UNIQUE,
    CHECK (length(database_id) = 36),
    CHECK (length(commodity_id) = 36)
) STRICT;

CREATE TABLE engine_version (
    engine_version_id TEXT PRIMARY KEY,
    build_id TEXT NOT NULL UNIQUE,
    source_revision TEXT,
    package_hash TEXT NOT NULL,
    recorded_at_utc TEXT NOT NULL,
    CHECK (length(engine_version_id) = 36)
) STRICT;

CREATE TABLE rule_version (
    rule_version_id TEXT PRIMARY KEY,
    rule_domain TEXT NOT NULL,
    semantic_version TEXT NOT NULL,
    rule_checksum TEXT NOT NULL,
    effective_from_utc TEXT NOT NULL,
    implementation_version TEXT NOT NULL,
    recorded_at_utc TEXT NOT NULL,
    UNIQUE (rule_domain, semantic_version),
    CHECK (length(rule_version_id) = 36)
) STRICT;

CREATE TABLE registry_cache_generation (
    cache_generation_id TEXT PRIMARY KEY,
    registry_publication_id TEXT NOT NULL,
    registry_version TEXT NOT NULL,
    publication_hash TEXT NOT NULL,
    signature_status TEXT NOT NULL CHECK (signature_status IN ('Verified', 'Invalid', 'Not Verified')),
    imported_at_utc TEXT NOT NULL,
    expires_at_utc TEXT,
    activation_status TEXT NOT NULL CHECK (activation_status IN ('Staged', 'Active', 'Superseded', 'Rejected')),
    UNIQUE (registry_publication_id),
    CHECK (expires_at_utc IS NULL OR expires_at_utc > imported_at_utc)
) STRICT;

CREATE TABLE registry_entity_cache (
    cache_row_id TEXT PRIMARY KEY,
    cache_generation_id TEXT NOT NULL REFERENCES registry_cache_generation(cache_generation_id),
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    entity_payload TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    UNIQUE (cache_generation_id, entity_type, entity_id)
) STRICT;

CREATE TABLE import_session (
    import_session_id TEXT PRIMARY KEY,
    import_context_type TEXT NOT NULL CHECK (import_context_type IN ('Sourcing Event', 'Historical Baseline', 'PCE Should Cost')),
    context_id TEXT,
    initiated_by_user_id TEXT NOT NULL,
    engine_version_id TEXT NOT NULL REFERENCES engine_version(engine_version_id),
    status TEXT NOT NULL CHECK (status IN ('Created', 'Discovering', 'Extracting', 'Awaiting Confirmation', 'Staging', 'Committing', 'Reconciling', 'Completed', 'Cancelling', 'Cancelled', 'Failed')),
    started_at_utc TEXT NOT NULL,
    completed_at_utc TEXT,
    CHECK (completed_at_utc IS NULL OR completed_at_utc >= started_at_utc)
) STRICT;

CREATE TABLE import_transaction (
    import_transaction_id TEXT PRIMARY KEY,
    import_session_id TEXT NOT NULL REFERENCES import_session(import_session_id),
    transaction_sequence INTEGER NOT NULL CHECK (transaction_sequence > 0),
    status TEXT NOT NULL CHECK (status IN ('Started', 'Committed', 'Rolled Back', 'Cancelled', 'Failed')),
    discovered_count INTEGER NOT NULL DEFAULT 0 CHECK (discovered_count >= 0),
    committed_count INTEGER NOT NULL DEFAULT 0 CHECK (committed_count >= 0),
    duplicate_count INTEGER NOT NULL DEFAULT 0 CHECK (duplicate_count >= 0),
    blocked_count INTEGER NOT NULL DEFAULT 0 CHECK (blocked_count >= 0),
    ignored_count INTEGER NOT NULL DEFAULT 0 CHECK (ignored_count >= 0),
    failed_count INTEGER NOT NULL DEFAULT 0 CHECK (failed_count >= 0),
    started_at_utc TEXT NOT NULL,
    completed_at_utc TEXT,
    UNIQUE (import_session_id, transaction_sequence)
) STRICT;

CREATE TABLE source_workbook (
    workbook_id TEXT PRIMARY KEY,
    import_transaction_id TEXT NOT NULL REFERENCES import_transaction(import_transaction_id),
    submitted_filename TEXT NOT NULL,
    source_locator TEXT,
    file_size_bytes INTEGER NOT NULL CHECK (file_size_bytes >= 0),
    file_hash_sha256 TEXT NOT NULL CHECK (length(file_hash_sha256) = 64),
    filesystem_modified_at_utc TEXT,
    displayed_document_date TEXT,
    recorded_at_utc TEXT NOT NULL
) STRICT;

CREATE TABLE source_worksheet (
    worksheet_id TEXT PRIMARY KEY,
    workbook_id TEXT NOT NULL REFERENCES source_workbook(workbook_id),
    worksheet_ordinal INTEGER NOT NULL CHECK (worksheet_ordinal >= 0),
    submitted_name TEXT NOT NULL,
    visibility TEXT NOT NULL CHECK (visibility IN ('Visible', 'Hidden', 'Very Hidden')),
    used_range TEXT,
    sheet_fingerprint TEXT NOT NULL,
    recorded_at_utc TEXT NOT NULL,
    UNIQUE (workbook_id, worksheet_ordinal)
) STRICT;

CREATE TABLE source_occurrence (
    occurrence_id TEXT PRIMARY KEY,
    worksheet_id TEXT NOT NULL REFERENCES source_worksheet(worksheet_id),
    region_locator TEXT,
    logical_fingerprint TEXT,
    detection_rule_version_id TEXT NOT NULL REFERENCES rule_version(rule_version_id),
    detection_result TEXT NOT NULL,
    terminal_status TEXT NOT NULL CHECK (terminal_status IN ('Pending', 'Committed', 'Duplicate', 'Blocked', 'Ignored', 'Failed')),
    duplicate_of_occurrence_id TEXT REFERENCES source_occurrence(occurrence_id),
    recorded_at_utc TEXT NOT NULL,
    CHECK ((terminal_status = 'Duplicate') = (duplicate_of_occurrence_id IS NOT NULL))
) STRICT;

CREATE TABLE source_datum (
    source_datum_id TEXT PRIMARY KEY,
    worksheet_id TEXT NOT NULL REFERENCES source_worksheet(worksheet_id),
    cell_or_range TEXT NOT NULL,
    submitted_lexeme TEXT,
    formula_text TEXT,
    cached_value_lexeme TEXT,
    context_hash TEXT,
    recorded_at_utc TEXT NOT NULL,
    UNIQUE (worksheet_id, cell_or_range)
) STRICT;

CREATE TABLE fingerprint (
    fingerprint_id TEXT PRIMARY KEY,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    purpose TEXT NOT NULL,
    algorithm TEXT NOT NULL,
    digest TEXT NOT NULL,
    rule_version_id TEXT REFERENCES rule_version(rule_version_id),
    recorded_at_utc TEXT NOT NULL,
    UNIQUE (entity_type, entity_id, purpose, algorithm, rule_version_id)
) STRICT;

CREATE TABLE staged_observation (
    staged_observation_id TEXT PRIMARY KEY,
    occurrence_id TEXT NOT NULL REFERENCES source_occurrence(occurrence_id),
    provisional_supplier_code TEXT,
    provisional_supplier_name TEXT,
    provisional_part_number TEXT,
    import_context_type TEXT NOT NULL,
    import_context_id TEXT,
    status TEXT NOT NULL CHECK (status IN ('Extracted', 'Needs Review', 'Ready to Commit', 'Committed', 'Blocked', 'Duplicate', 'Ignored', 'Failed')),
    blocking_issue_count INTEGER NOT NULL DEFAULT 0 CHECK (blocking_issue_count >= 0),
    recorded_at_utc TEXT NOT NULL,
    UNIQUE (occurrence_id)
) STRICT;

CREATE TABLE staging_issue (
    staging_issue_id TEXT PRIMARY KEY,
    staged_observation_id TEXT NOT NULL REFERENCES staged_observation(staged_observation_id),
    issue_type TEXT NOT NULL,
    source_datum_id TEXT REFERENCES source_datum(source_datum_id),
    issue_detail TEXT NOT NULL,
    blocking_flag INTEGER NOT NULL CHECK (blocking_flag IN (0, 1)),
    recorded_at_utc TEXT NOT NULL
) STRICT;

CREATE TABLE staging_resolution (
    staging_resolution_id TEXT PRIMARY KEY,
    staging_issue_id TEXT NOT NULL REFERENCES staging_issue(staging_issue_id),
    decision_code TEXT NOT NULL,
    selected_entity_type TEXT,
    selected_entity_id TEXT,
    selected_value TEXT,
    decided_by_user_id TEXT NOT NULL,
    decision_reason TEXT NOT NULL,
    recorded_at_utc TEXT NOT NULL
) STRICT;

CREATE TABLE pbd_observation (
    observation_id TEXT PRIMARY KEY,
    staged_observation_id TEXT NOT NULL UNIQUE REFERENCES staged_observation(staged_observation_id),
    occurrence_id TEXT NOT NULL UNIQUE REFERENCES source_occurrence(occurrence_id),
    observation_context TEXT NOT NULL CHECK (observation_context IN ('Sourcing Event', 'Historical Baseline', 'PCE Should Cost')),
    context_id TEXT,
    supplier_id TEXT,
    supplier_plant_id TEXT,
    part_id TEXT,
    submitted_supplier_name TEXT NOT NULL,
    submitted_part_number TEXT NOT NULL,
    submitted_part_description TEXT,
    economic_date TEXT,
    economic_date_precision TEXT CHECK (economic_date_precision IN ('Day', 'Month', 'Year', 'Unknown')),
    structure_category TEXT NOT NULL CHECK (structure_category IN ('Detailed and Reconciled', 'Valid Aggregate', 'Incomplete', 'Formula Reconciliation Exception')),
    recorded_at_utc TEXT NOT NULL
) STRICT;

CREATE TABLE submitted_datum (
    submitted_datum_id TEXT PRIMARY KEY,
    observation_id TEXT NOT NULL REFERENCES pbd_observation(observation_id),
    field_code TEXT NOT NULL,
    source_datum_id TEXT NOT NULL REFERENCES source_datum(source_datum_id),
    submitted_lexeme TEXT,
    decimal_coefficient TEXT,
    decimal_scale INTEGER CHECK (decimal_scale IS NULL OR decimal_scale >= 0),
    governing_1e4 INTEGER,
    text_value TEXT,
    date_value TEXT,
    boolean_value INTEGER CHECK (boolean_value IS NULL OR boolean_value IN (0, 1)),
    normalized_unit_id TEXT,
    currency_id TEXT,
    precision_status TEXT NOT NULL CHECK (precision_status IN ('Eligible', 'Blocked', 'Not Applicable')),
    recorded_at_utc TEXT NOT NULL,
    CHECK ((decimal_coefficient IS NULL AND decimal_scale IS NULL) OR (decimal_coefficient IS NOT NULL AND decimal_scale IS NOT NULL)),
    CHECK (decimal_coefficient IS NULL OR decimal_coefficient NOT GLOB '*[^0-9-]*'),
    UNIQUE (observation_id, field_code, source_datum_id)
) STRICT;

CREATE TABLE cost_section (
    cost_section_id TEXT PRIMARY KEY,
    observation_id TEXT NOT NULL REFERENCES pbd_observation(observation_id),
    parent_section_id TEXT REFERENCES cost_section(cost_section_id),
    submitted_label TEXT NOT NULL,
    normalized_category_code TEXT,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    mapping_version_id TEXT,
    recorded_at_utc TEXT NOT NULL,
    UNIQUE (observation_id, ordinal)
) STRICT;

CREATE TABLE cost_element (
    cost_element_id TEXT PRIMARY KEY,
    cost_section_id TEXT NOT NULL REFERENCES cost_section(cost_section_id),
    element_type TEXT NOT NULL,
    amount_datum_id TEXT REFERENCES submitted_datum(submitted_datum_id),
    rate_datum_id TEXT REFERENCES submitted_datum(submitted_datum_id),
    basis_datum_id TEXT REFERENCES submitted_datum(submitted_datum_id),
    recorded_at_utc TEXT NOT NULL
) STRICT;

CREATE TABLE material_line (
    material_line_id TEXT PRIMARY KEY,
    observation_id TEXT NOT NULL REFERENCES pbd_observation(observation_id),
    line_ordinal INTEGER NOT NULL CHECK (line_ordinal >= 0),
    submitted_identity TEXT,
    submitted_description TEXT,
    quantity_datum_id TEXT REFERENCES submitted_datum(submitted_datum_id),
    rate_datum_id TEXT REFERENCES submitted_datum(submitted_datum_id),
    amount_datum_id TEXT REFERENCES submitted_datum(submitted_datum_id),
    recorded_at_utc TEXT NOT NULL,
    UNIQUE (observation_id, line_ordinal)
) STRICT;

CREATE TABLE operation_line (
    operation_line_id TEXT PRIMARY KEY,
    observation_id TEXT NOT NULL REFERENCES pbd_observation(observation_id),
    operation_sequence INTEGER NOT NULL CHECK (operation_sequence >= 0),
    submitted_operation TEXT NOT NULL,
    cycle_time_datum_id TEXT REFERENCES submitted_datum(submitted_datum_id),
    labor_rate_datum_id TEXT REFERENCES submitted_datum(submitted_datum_id),
    machine_rate_datum_id TEXT REFERENCES submitted_datum(submitted_datum_id),
    burden_datum_id TEXT REFERENCES submitted_datum(submitted_datum_id),
    amount_datum_id TEXT REFERENCES submitted_datum(submitted_datum_id),
    recorded_at_utc TEXT NOT NULL,
    UNIQUE (observation_id, operation_sequence)
) STRICT;

CREATE TABLE observation_eligibility (
    eligibility_id TEXT PRIMARY KEY,
    observation_id TEXT NOT NULL REFERENCES pbd_observation(observation_id),
    analytical_role TEXT NOT NULL,
    eligibility_status TEXT NOT NULL CHECK (eligibility_status IN ('Eligible', 'Excluded', 'Blocked', 'Historical Context Only')),
    reason_code TEXT NOT NULL,
    confirmed_by_user_id TEXT,
    supersedes_eligibility_id TEXT REFERENCES observation_eligibility(eligibility_id),
    recorded_at_utc TEXT NOT NULL,
    CHECK (supersedes_eligibility_id IS NULL OR supersedes_eligibility_id <> eligibility_id)
) STRICT;

CREATE TABLE sourcing_event (
    event_id TEXT PRIMARY KEY,
    commodity_id TEXT NOT NULL,
    readable_name TEXT NOT NULL,
    buyer_code_id TEXT NOT NULL,
    primary_buyer_user_id TEXT NOT NULL,
    event_status TEXT NOT NULL CHECK (event_status IN ('Setup', 'Active', 'Finalized', 'Closed')),
    created_at_utc TEXT NOT NULL
) STRICT;

CREATE TABLE source_package (
    source_package_id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL REFERENCES sourcing_event(event_id),
    normalized_package_number TEXT NOT NULL,
    displayed_package_number TEXT NOT NULL,
    package_role TEXT NOT NULL CHECK (package_role IN ('Primary', 'Revised', 'Supplemental', 'Replacement')),
    recorded_at_utc TEXT NOT NULL,
    UNIQUE (normalized_package_number)
) STRICT;

CREATE TABLE scope_version (
    scope_version_id TEXT PRIMARY KEY,
    source_package_id TEXT NOT NULL REFERENCES source_package(source_package_id),
    version_number INTEGER NOT NULL CHECK (version_number > 0),
    predecessor_scope_version_id TEXT REFERENCES scope_version(scope_version_id),
    change_reason TEXT NOT NULL,
    confirmed_by_user_id TEXT NOT NULL,
    confirmed_at_utc TEXT NOT NULL,
    UNIQUE (source_package_id, version_number)
) STRICT;

CREATE TABLE event_part (
    event_part_id TEXT PRIMARY KEY,
    scope_version_id TEXT NOT NULL REFERENCES scope_version(scope_version_id),
    part_id TEXT NOT NULL,
    submitted_part_number TEXT NOT NULL,
    submitted_description TEXT,
    vehicle_position TEXT,
    scope_action TEXT NOT NULL CHECK (scope_action IN ('Added', 'Removed', 'Replaced', 'Corrected', 'Unchanged')),
    predecessor_event_part_id TEXT REFERENCES event_part(event_part_id),
    recorded_at_utc TEXT NOT NULL,
    UNIQUE (scope_version_id, part_id, vehicle_position)
) STRICT;

CREATE TABLE gst_baseline (
    gst_baseline_id TEXT PRIMARY KEY,
    source_package_id TEXT NOT NULL REFERENCES source_package(source_package_id),
    scope_version_id TEXT NOT NULL REFERENCES scope_version(scope_version_id),
    baseline_version INTEGER NOT NULL CHECK (baseline_version > 0),
    import_session_id TEXT REFERENCES import_session(import_session_id),
    predecessor_baseline_id TEXT REFERENCES gst_baseline(gst_baseline_id),
    baseline_status TEXT NOT NULL CHECK (baseline_status IN ('Pending', 'Confirmed', 'Superseded', 'Rejected')),
    confirmed_by_user_id TEXT,
    confirmed_at_utc TEXT,
    recorded_at_utc TEXT NOT NULL,
    UNIQUE (source_package_id, baseline_version)
) STRICT;

CREATE TABLE gst_part_value (
    gst_part_value_id TEXT PRIMARY KEY,
    gst_baseline_id TEXT NOT NULL REFERENCES gst_baseline(gst_baseline_id),
    event_part_id TEXT NOT NULL REFERENCES event_part(event_part_id),
    program_year INTEGER NOT NULL CHECK (program_year BETWEEN 1900 AND 2200),
    measure_code TEXT NOT NULL CHECK (measure_code IN ('Piece Price Target', 'FPV', 'SFT', 'PST', 'ED&D')),
    submitted_lexeme TEXT NOT NULL,
    decimal_coefficient TEXT NOT NULL,
    decimal_scale INTEGER NOT NULL CHECK (decimal_scale >= 0),
    governing_1e4 INTEGER,
    normalized_unit_id TEXT NOT NULL,
    currency_id TEXT,
    precision_status TEXT NOT NULL CHECK (precision_status IN ('Eligible', 'Blocked')),
    source_datum_id TEXT REFERENCES source_datum(source_datum_id),
    recorded_at_utc TEXT NOT NULL,
    UNIQUE (gst_baseline_id, event_part_id, program_year, measure_code)
) STRICT;

CREATE TABLE supplier_quote_round (
    quote_round_id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL REFERENCES sourcing_event(event_id),
    supplier_id TEXT NOT NULL,
    round_number INTEGER NOT NULL CHECK (round_number > 0),
    round_description TEXT,
    supplier_submission_date TEXT,
    recorded_at_utc TEXT NOT NULL,
    UNIQUE (event_id, supplier_id, round_number)
) STRICT;

CREATE TABLE round_batch (
    round_batch_id TEXT PRIMARY KEY,
    quote_round_id TEXT NOT NULL REFERENCES supplier_quote_round(quote_round_id),
    import_transaction_id TEXT NOT NULL REFERENCES import_transaction(import_transaction_id),
    confirmed_by_user_id TEXT NOT NULL,
    confirmed_at_utc TEXT NOT NULL,
    UNIQUE (quote_round_id, import_transaction_id)
) STRICT;

CREATE TABLE round_observation (
    round_observation_id TEXT PRIMARY KEY,
    quote_round_id TEXT NOT NULL REFERENCES supplier_quote_round(quote_round_id),
    round_batch_id TEXT NOT NULL REFERENCES round_batch(round_batch_id),
    observation_id TEXT NOT NULL REFERENCES pbd_observation(observation_id),
    event_part_id TEXT NOT NULL REFERENCES event_part(event_part_id),
    membership_status TEXT NOT NULL CHECK (membership_status IN ('Submitted', 'Unchanged', 'Added', 'Removed', 'Omitted', 'Conflict')),
    recorded_at_utc TEXT NOT NULL,
    UNIQUE (quote_round_id, observation_id)
) STRICT;

CREATE TABLE round_activation (
    round_activation_id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL REFERENCES sourcing_event(event_id),
    supplier_id TEXT NOT NULL,
    quote_round_id TEXT NOT NULL REFERENCES supplier_quote_round(quote_round_id),
    activation_decision TEXT NOT NULL CHECK (activation_decision IN ('Activate', 'Deactivate')),
    decided_by_user_id TEXT NOT NULL,
    decision_reason TEXT NOT NULL,
    recorded_at_utc TEXT NOT NULL
) STRICT;

CREATE TABLE carry_forward_decision (
    carry_forward_decision_id TEXT PRIMARY KEY,
    target_quote_round_id TEXT NOT NULL REFERENCES supplier_quote_round(quote_round_id),
    event_part_id TEXT NOT NULL REFERENCES event_part(event_part_id),
    prior_observation_id TEXT REFERENCES pbd_observation(observation_id),
    decision_code TEXT NOT NULL CHECK (decision_code IN ('Carry Forward', 'Not Quoted', 'Buyer Review Required')),
    decided_by_user_id TEXT NOT NULL,
    decision_reason TEXT NOT NULL,
    recorded_at_utc TEXT NOT NULL,
    CHECK ((decision_code = 'Carry Forward') = (prior_observation_id IS NOT NULL))
) STRICT;

CREATE TABLE supplier_activity (
    supplier_activity_id TEXT PRIMARY KEY,
    supplier_id TEXT NOT NULL,
    supplier_plant_id TEXT,
    event_id TEXT REFERENCES sourcing_event(event_id),
    quote_round_id TEXT REFERENCES supplier_quote_round(quote_round_id),
    event_part_id TEXT REFERENCES event_part(event_part_id),
    activity_type TEXT NOT NULL CHECK (activity_type IN ('Price Change', 'Formula Change', 'Scope Change', 'Omission', 'Correction', 'Addition', 'Removal', 'Reaffirmation')),
    before_entity_type TEXT,
    before_entity_id TEXT,
    after_entity_type TEXT,
    after_entity_id TEXT,
    occurred_at_utc TEXT,
    recorded_at_utc TEXT NOT NULL,
    CHECK (before_entity_id IS NOT NULL OR after_entity_id IS NOT NULL)
) STRICT;

CREATE TABLE functional_part_family (
    functional_family_id TEXT PRIMARY KEY,
    created_by_user_id TEXT NOT NULL,
    created_at_utc TEXT NOT NULL
) STRICT;

CREATE TABLE family_version (
    family_version_id TEXT PRIMARY KEY,
    functional_family_id TEXT NOT NULL REFERENCES functional_part_family(functional_family_id),
    version_number INTEGER NOT NULL CHECK (version_number > 0),
    readable_name TEXT NOT NULL,
    relationship_type TEXT NOT NULL,
    business_rationale TEXT NOT NULL,
    confirmed_by_user_id TEXT NOT NULL,
    business_valid_from TEXT,
    business_valid_to TEXT,
    recorded_at_utc TEXT NOT NULL,
    supersedes_family_version_id TEXT REFERENCES family_version(family_version_id),
    UNIQUE (functional_family_id, version_number),
    CHECK (business_valid_to IS NULL OR business_valid_to > business_valid_from)
) STRICT;

CREATE TABLE family_member (
    family_member_id TEXT PRIMARY KEY,
    family_version_id TEXT NOT NULL REFERENCES family_version(family_version_id),
    part_id TEXT NOT NULL,
    program_id TEXT,
    vehicle_position TEXT,
    member_role TEXT NOT NULL,
    recorded_at_utc TEXT NOT NULL,
    UNIQUE (family_version_id, part_id, program_id, vehicle_position)
) STRICT;

CREATE TABLE family_comparison_permission (
    comparison_permission_id TEXT PRIMARY KEY,
    family_version_id TEXT NOT NULL REFERENCES family_version(family_version_id),
    measure_or_category TEXT NOT NULL,
    comparison_eligibility TEXT NOT NULL CHECK (comparison_eligibility IN ('Governing', 'Directional', 'Not Comparable')),
    recorded_at_utc TEXT NOT NULL,
    UNIQUE (family_version_id, measure_or_category)
) STRICT;

CREATE TABLE knowledge_item (
    knowledge_item_id TEXT PRIMARY KEY,
    knowledge_type TEXT NOT NULL,
    created_by_user_id TEXT NOT NULL,
    created_at_utc TEXT NOT NULL
) STRICT;

CREATE TABLE knowledge_version (
    knowledge_version_id TEXT PRIMARY KEY,
    knowledge_item_id TEXT NOT NULL REFERENCES knowledge_item(knowledge_item_id),
    version_number INTEGER NOT NULL CHECK (version_number > 0),
    knowledge_level TEXT NOT NULL CHECK (knowledge_level IN ('Observed Evidence', 'Confirmed Knowledge', 'Corporate Standard')),
    structured_payload TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('Candidate', 'Active', 'Superseded', 'Expired', 'Rejected')),
    business_valid_from TEXT,
    business_valid_to TEXT,
    recorded_from_utc TEXT NOT NULL,
    recorded_to_utc TEXT,
    confirmed_by_user_id TEXT,
    approval_authority_type TEXT,
    business_rationale TEXT,
    supersedes_knowledge_version_id TEXT REFERENCES knowledge_version(knowledge_version_id),
    UNIQUE (knowledge_item_id, version_number),
    CHECK (business_valid_to IS NULL OR business_valid_to > business_valid_from),
    CHECK (recorded_to_utc IS NULL OR recorded_to_utc > recorded_from_utc),
    CHECK (knowledge_level = 'Observed Evidence' OR (confirmed_by_user_id IS NOT NULL AND business_rationale IS NOT NULL))
) STRICT;

CREATE TABLE knowledge_scope (
    knowledge_scope_id TEXT PRIMARY KEY,
    knowledge_version_id TEXT NOT NULL REFERENCES knowledge_version(knowledge_version_id),
    supplier_id TEXT,
    supplier_plant_id TEXT,
    commodity_id TEXT,
    part_id TEXT,
    functional_family_id TEXT REFERENCES functional_part_family(functional_family_id),
    process_code TEXT,
    region_code TEXT,
    program_id TEXT,
    scope_specificity INTEGER NOT NULL CHECK (scope_specificity BETWEEN 1 AND 6),
    scope_signature TEXT NOT NULL,
    recorded_at_utc TEXT NOT NULL,
    UNIQUE (knowledge_version_id, scope_signature),
    CHECK (supplier_id IS NOT NULL OR supplier_plant_id IS NOT NULL OR commodity_id IS NOT NULL OR part_id IS NOT NULL OR functional_family_id IS NOT NULL OR process_code IS NOT NULL OR region_code IS NOT NULL OR program_id IS NOT NULL)
) STRICT;

CREATE TABLE knowledge_evidence (
    knowledge_evidence_id TEXT PRIMARY KEY,
    knowledge_version_id TEXT NOT NULL REFERENCES knowledge_version(knowledge_version_id),
    evidence_entity_type TEXT NOT NULL,
    evidence_entity_id TEXT NOT NULL,
    evidence_role TEXT NOT NULL CHECK (evidence_role IN ('Supporting', 'Contradicting', 'Context')),
    recorded_at_utc TEXT NOT NULL,
    UNIQUE (knowledge_version_id, evidence_entity_type, evidence_entity_id, evidence_role)
) STRICT;

CREATE TABLE analysis (
    analysis_id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL REFERENCES sourcing_event(event_id),
    analysis_type TEXT NOT NULL,
    readable_name TEXT NOT NULL,
    created_by_user_id TEXT NOT NULL,
    created_at_utc TEXT NOT NULL
) STRICT;

CREATE TABLE scenario_revision (
    scenario_revision_id TEXT PRIMARY KEY,
    analysis_id TEXT NOT NULL REFERENCES analysis(analysis_id),
    revision_number INTEGER NOT NULL CHECK (revision_number > 0),
    parent_revision_id TEXT REFERENCES scenario_revision(scenario_revision_id),
    scenario_name TEXT,
    scope_version_id TEXT NOT NULL REFERENCES scope_version(scope_version_id),
    gst_baseline_id TEXT REFERENCES gst_baseline(gst_baseline_id),
    assumptions_hash TEXT NOT NULL,
    created_by_user_id TEXT NOT NULL,
    created_at_utc TEXT NOT NULL,
    UNIQUE (analysis_id, revision_number)
) STRICT;

CREATE TABLE evidence_manifest_entry (
    evidence_manifest_entry_id TEXT PRIMARY KEY,
    scenario_revision_id TEXT NOT NULL REFERENCES scenario_revision(scenario_revision_id),
    evidence_entity_type TEXT NOT NULL,
    evidence_entity_id TEXT NOT NULL,
    included_flag INTEGER NOT NULL CHECK (included_flag IN (0, 1)),
    analytical_role TEXT NOT NULL,
    exclusion_reason TEXT,
    recorded_at_utc TEXT NOT NULL,
    UNIQUE (scenario_revision_id, evidence_entity_type, evidence_entity_id, analytical_role),
    CHECK (included_flag = 1 OR exclusion_reason IS NOT NULL)
) STRICT;

CREATE TABLE calculation_run (
    calculation_run_id TEXT PRIMARY KEY,
    scenario_revision_id TEXT NOT NULL REFERENCES scenario_revision(scenario_revision_id),
    engine_version_id TEXT NOT NULL REFERENCES engine_version(engine_version_id),
    calculation_rule_version_id TEXT NOT NULL REFERENCES rule_version(rule_version_id),
    status TEXT NOT NULL CHECK (status IN ('Started', 'Completed', 'Failed', 'Cancelled')),
    input_manifest_hash TEXT NOT NULL,
    output_manifest_hash TEXT,
    started_at_utc TEXT NOT NULL,
    completed_at_utc TEXT,
    CHECK ((status = 'Completed') = (output_manifest_hash IS NOT NULL))
) STRICT;

CREATE TABLE calculation_result (
    calculation_result_id TEXT PRIMARY KEY,
    calculation_run_id TEXT NOT NULL REFERENCES calculation_run(calculation_run_id),
    result_code TEXT NOT NULL,
    supplier_id TEXT,
    part_id TEXT,
    program_year INTEGER,
    category_code TEXT,
    decimal_coefficient TEXT,
    decimal_scale INTEGER CHECK (decimal_scale IS NULL OR decimal_scale >= 0),
    governing_1e4 INTEGER,
    normalized_unit_id TEXT,
    currency_id TEXT,
    confidence_classification TEXT,
    recorded_at_utc TEXT NOT NULL,
    CHECK ((decimal_coefficient IS NULL AND decimal_scale IS NULL) OR (decimal_coefficient IS NOT NULL AND decimal_scale IS NOT NULL))
) STRICT;

CREATE TABLE calculation_lineage (
    calculation_lineage_id TEXT PRIMARY KEY,
    calculation_result_id TEXT NOT NULL REFERENCES calculation_result(calculation_result_id),
    source_entity_type TEXT NOT NULL,
    source_entity_id TEXT NOT NULL,
    dependency_role TEXT NOT NULL,
    dependency_ordinal INTEGER NOT NULL CHECK (dependency_ordinal >= 0),
    recorded_at_utc TEXT NOT NULL,
    UNIQUE (calculation_result_id, source_entity_type, source_entity_id, dependency_role)
) STRICT;

CREATE TABLE finalized_snapshot (
    finalized_snapshot_id TEXT PRIMARY KEY,
    analysis_id TEXT NOT NULL REFERENCES analysis(analysis_id),
    scenario_revision_id TEXT NOT NULL REFERENCES scenario_revision(scenario_revision_id),
    calculation_run_id TEXT NOT NULL UNIQUE REFERENCES calculation_run(calculation_run_id),
    open_action_count INTEGER NOT NULL CHECK (open_action_count >= 0),
    evidence_manifest_hash TEXT NOT NULL,
    audit_chain_anchor TEXT NOT NULL,
    finalized_by_user_id TEXT NOT NULL,
    finalized_at_utc TEXT NOT NULL
) STRICT;

CREATE TABLE buyer_action (
    buyer_action_id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL REFERENCES sourcing_event(event_id),
    supplier_id TEXT,
    part_id TEXT,
    governing_entity_type TEXT NOT NULL,
    governing_entity_id TEXT NOT NULL,
    issue_type TEXT NOT NULL,
    created_by_user_id TEXT NOT NULL,
    created_at_utc TEXT NOT NULL
) STRICT;

CREATE TABLE buyer_action_version (
    buyer_action_version_id TEXT PRIMARY KEY,
    buyer_action_id TEXT NOT NULL REFERENCES buyer_action(buyer_action_id),
    action_status TEXT NOT NULL CHECK (action_status IN ('Open', 'Sent to Supplier', 'Supplier Response Received', 'Resolved', 'Accepted Exception')),
    financial_impact_coefficient TEXT,
    financial_impact_scale INTEGER CHECK (financial_impact_scale IS NULL OR financial_impact_scale >= 0),
    currency_id TEXT,
    required_supplier_action TEXT NOT NULL,
    owner_user_id TEXT NOT NULL,
    working_note TEXT,
    resolution_reason TEXT,
    supersedes_action_version_id TEXT REFERENCES buyer_action_version(buyer_action_version_id),
    recorded_by_user_id TEXT NOT NULL,
    recorded_at_utc TEXT NOT NULL,
    CHECK (action_status NOT IN ('Resolved', 'Accepted Exception') OR resolution_reason IS NOT NULL)
) STRICT;

CREATE TABLE audit_event (
    audit_event_id TEXT PRIMARY KEY,
    recorded_sequence INTEGER NOT NULL UNIQUE CHECK (recorded_sequence > 0),
    event_type TEXT NOT NULL,
    actor_user_id TEXT,
    effective_authority TEXT,
    occurred_at_utc TEXT NOT NULL,
    display_timezone TEXT NOT NULL,
    workstation_session TEXT,
    application_version TEXT NOT NULL,
    action_method TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    reason_text TEXT,
    event_payload TEXT NOT NULL,
    prior_event_hash TEXT,
    event_hash TEXT NOT NULL UNIQUE,
    CHECK ((recorded_sequence = 1 AND prior_event_hash IS NULL) OR (recorded_sequence > 1 AND prior_event_hash IS NOT NULL))
) STRICT;

CREATE TABLE audit_event_detail (
    audit_event_detail_id TEXT PRIMARY KEY,
    audit_event_id TEXT NOT NULL REFERENCES audit_event(audit_event_id),
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    field_path TEXT,
    before_payload TEXT,
    after_payload TEXT,
    recorded_at_utc TEXT NOT NULL,
    CHECK (before_payload IS NOT NULL OR after_payload IS NOT NULL)
) STRICT;

CREATE INDEX ix_registry_cache_entity_latest ON registry_entity_cache(entity_type, entity_id, cache_generation_id DESC);
CREATE INDEX ix_source_workbook_hash ON source_workbook(file_hash_sha256, file_size_bytes);
CREATE INDEX ix_source_worksheet_fingerprint ON source_worksheet(sheet_fingerprint);
CREATE INDEX ix_occurrence_transaction_status ON source_occurrence(worksheet_id, terminal_status, occurrence_id);
CREATE INDEX ix_occurrence_logical_fingerprint ON source_occurrence(logical_fingerprint, detection_rule_version_id);
CREATE INDEX ix_source_datum_location ON source_datum(worksheet_id, cell_or_range);
CREATE INDEX ix_fingerprint_digest ON fingerprint(purpose, algorithm, digest);
CREATE INDEX ix_observation_part_date ON pbd_observation(part_id, economic_date DESC, observation_id);
CREATE INDEX ix_observation_supplier_plant_date ON pbd_observation(supplier_id, supplier_plant_id, economic_date DESC, observation_id);
CREATE INDEX ix_observation_context ON pbd_observation(observation_context, context_id, supplier_id, part_id);
CREATE INDEX ix_eligibility_latest ON observation_eligibility(observation_id, analytical_role, recorded_at_utc DESC);
CREATE INDEX ix_submitted_datum_field ON submitted_datum(observation_id, field_code, submitted_datum_id);
CREATE INDEX ix_cost_element_section_type ON cost_element(cost_section_id, element_type);
CREATE INDEX ix_material_observation ON material_line(observation_id, line_ordinal);
CREATE INDEX ix_operation_observation ON operation_line(observation_id, operation_sequence);
CREATE INDEX ix_event_buyer_status ON sourcing_event(buyer_code_id, event_status, created_at_utc DESC);
CREATE INDEX ix_event_part_scope ON event_part(scope_version_id, part_id, event_part_id);
CREATE INDEX ix_gst_baseline_scope ON gst_baseline(source_package_id, baseline_status, baseline_version DESC);
CREATE INDEX ix_round_observation_part ON round_observation(quote_round_id, event_part_id, membership_status, round_observation_id);
CREATE INDEX ix_round_activation_current ON round_activation(event_id, supplier_id, recorded_at_utc DESC, round_activation_id);
CREATE INDEX ix_carry_forward_current ON carry_forward_decision(target_quote_round_id, event_part_id, recorded_at_utc DESC);
CREATE INDEX ix_activity_supplier_scope_time ON supplier_activity(supplier_id, supplier_plant_id, occurred_at_utc DESC, supplier_activity_id);
CREATE INDEX ix_activity_event_round_part ON supplier_activity(event_id, quote_round_id, event_part_id, activity_type);
CREATE INDEX ix_family_member_part ON family_member(part_id, family_version_id);
CREATE INDEX ix_knowledge_scope_signature ON knowledge_scope(scope_signature, scope_specificity, knowledge_version_id);
CREATE INDEX ix_knowledge_scope_dimensions ON knowledge_scope(supplier_id, supplier_plant_id, commodity_id, part_id, functional_family_id, region_code);
CREATE INDEX ix_knowledge_bitemporal ON knowledge_version(knowledge_item_id, business_valid_from, business_valid_to, recorded_from_utc DESC);
CREATE INDEX ix_knowledge_evidence_reverse ON knowledge_evidence(evidence_entity_type, evidence_entity_id, knowledge_version_id);
CREATE INDEX ix_manifest_scenario_role ON evidence_manifest_entry(scenario_revision_id, included_flag, analytical_role, evidence_entity_type);
CREATE INDEX ix_manifest_evidence_reverse ON evidence_manifest_entry(evidence_entity_type, evidence_entity_id, scenario_revision_id);
CREATE INDEX ix_result_run_dimensions ON calculation_result(calculation_run_id, result_code, supplier_id, part_id, program_year, category_code);
CREATE INDEX ix_lineage_result ON calculation_lineage(calculation_result_id, dependency_role, dependency_ordinal);
CREATE INDEX ix_lineage_source_reverse ON calculation_lineage(source_entity_type, source_entity_id, calculation_result_id);
CREATE INDEX ix_action_event ON buyer_action(event_id, supplier_id, part_id, issue_type);
CREATE INDEX ix_action_version_latest ON buyer_action_version(buyer_action_id, recorded_at_utc DESC, buyer_action_version_id);
CREATE INDEX ix_audit_entity_reverse ON audit_event_detail(entity_type, entity_id, audit_event_id);

CREATE VIEW v_latest_observation_eligibility AS
SELECT e.*
FROM observation_eligibility e
WHERE NOT EXISTS (
    SELECT 1
    FROM observation_eligibility newer
    WHERE newer.supersedes_eligibility_id = e.eligibility_id
);

CREATE VIEW v_active_supplier_round AS
SELECT ra.*
FROM round_activation ra
WHERE ra.activation_decision = 'Activate'
  AND NOT EXISTS (
      SELECT 1
      FROM round_activation newer
      WHERE newer.event_id = ra.event_id
        AND newer.supplier_id = ra.supplier_id
        AND (newer.recorded_at_utc > ra.recorded_at_utc
             OR (newer.recorded_at_utc = ra.recorded_at_utc AND newer.round_activation_id > ra.round_activation_id))
  );

CREATE VIEW v_current_buyer_action AS
SELECT av.*
FROM buyer_action_version av
WHERE NOT EXISTS (
    SELECT 1
    FROM buyer_action_version newer
    WHERE newer.supersedes_action_version_id = av.buyer_action_version_id
);

CREATE VIEW v_source_tab_reconciliation AS
SELECT
    it.import_transaction_id,
    COUNT(so.occurrence_id) AS occurrence_count,
    SUM(CASE WHEN so.terminal_status = 'Committed' THEN 1 ELSE 0 END) AS committed_count,
    SUM(CASE WHEN so.terminal_status = 'Duplicate' THEN 1 ELSE 0 END) AS duplicate_count,
    SUM(CASE WHEN so.terminal_status = 'Blocked' THEN 1 ELSE 0 END) AS blocked_count,
    SUM(CASE WHEN so.terminal_status = 'Ignored' THEN 1 ELSE 0 END) AS ignored_count,
    SUM(CASE WHEN so.terminal_status = 'Failed' THEN 1 ELSE 0 END) AS failed_count,
    SUM(CASE WHEN so.terminal_status = 'Pending' THEN 1 ELSE 0 END) AS pending_count
FROM import_transaction it
LEFT JOIN source_workbook sw ON sw.import_transaction_id = it.import_transaction_id
LEFT JOIN source_worksheet ws ON ws.workbook_id = sw.workbook_id
LEFT JOIN source_occurrence so ON so.worksheet_id = ws.worksheet_id
GROUP BY it.import_transaction_id;

-- Database-enforced immutability for the highest-risk evidence and result tables.
CREATE TRIGGER no_update_source_datum BEFORE UPDATE ON source_datum BEGIN SELECT RAISE(ABORT, 'source_datum is immutable'); END;
CREATE TRIGGER no_delete_source_datum BEFORE DELETE ON source_datum BEGIN SELECT RAISE(ABORT, 'source_datum cannot be deleted'); END;
CREATE TRIGGER no_update_pbd_observation BEFORE UPDATE ON pbd_observation BEGIN SELECT RAISE(ABORT, 'pbd_observation is immutable'); END;
CREATE TRIGGER no_delete_pbd_observation BEFORE DELETE ON pbd_observation BEGIN SELECT RAISE(ABORT, 'pbd_observation cannot be deleted'); END;
CREATE TRIGGER no_update_submitted_datum BEFORE UPDATE ON submitted_datum BEGIN SELECT RAISE(ABORT, 'submitted_datum is immutable'); END;
CREATE TRIGGER no_delete_submitted_datum BEFORE DELETE ON submitted_datum BEGIN SELECT RAISE(ABORT, 'submitted_datum cannot be deleted'); END;
CREATE TRIGGER no_update_supplier_activity BEFORE UPDATE ON supplier_activity BEGIN SELECT RAISE(ABORT, 'supplier_activity is immutable'); END;
CREATE TRIGGER no_delete_supplier_activity BEFORE DELETE ON supplier_activity BEGIN SELECT RAISE(ABORT, 'supplier_activity cannot be deleted'); END;
CREATE TRIGGER no_update_knowledge_version BEFORE UPDATE ON knowledge_version BEGIN SELECT RAISE(ABORT, 'knowledge_version is immutable; supersede it'); END;
CREATE TRIGGER no_delete_knowledge_version BEFORE DELETE ON knowledge_version BEGIN SELECT RAISE(ABORT, 'knowledge_version cannot be deleted'); END;
CREATE TRIGGER no_update_calculation_result BEFORE UPDATE ON calculation_result BEGIN SELECT RAISE(ABORT, 'calculation_result is immutable'); END;
CREATE TRIGGER no_delete_calculation_result BEFORE DELETE ON calculation_result BEGIN SELECT RAISE(ABORT, 'calculation_result cannot be deleted'); END;
CREATE TRIGGER no_update_finalized_snapshot BEFORE UPDATE ON finalized_snapshot BEGIN SELECT RAISE(ABORT, 'finalized_snapshot is immutable'); END;
CREATE TRIGGER no_delete_finalized_snapshot BEFORE DELETE ON finalized_snapshot BEGIN SELECT RAISE(ABORT, 'finalized_snapshot cannot be deleted'); END;
CREATE TRIGGER no_update_audit_event BEFORE UPDATE ON audit_event BEGIN SELECT RAISE(ABORT, 'audit_event is immutable'); END;
CREATE TRIGGER no_delete_audit_event BEFORE DELETE ON audit_event BEGIN SELECT RAISE(ABORT, 'audit_event cannot be deleted'); END;
CREATE TRIGGER no_update_audit_detail BEFORE UPDATE ON audit_event_detail BEGIN SELECT RAISE(ABORT, 'audit_event_detail is immutable'); END;
CREATE TRIGGER no_delete_audit_detail BEFORE DELETE ON audit_event_detail BEGIN SELECT RAISE(ABORT, 'audit_event_detail cannot be deleted'); END;

INSERT INTO schema_migration (
    migration_version,
    migration_name,
    migration_checksum,
    applied_at_utc,
    application_build
) VALUES (
    '0001',
    'commodity_core',
    '__FORGE_MIGRATION_SHA256__',
    strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
    'schema-prototype-0.1'
);

COMMIT;
