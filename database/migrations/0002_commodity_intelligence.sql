-- Forge X commodity intelligence, projection, publication, and recovery layer

PRAGMA foreign_keys = ON;
BEGIN IMMEDIATE;

CREATE TABLE formula_evidence (
    formula_evidence_id TEXT PRIMARY KEY,
    observation_id TEXT NOT NULL REFERENCES pbd_observation(observation_id),
    source_datum_id TEXT NOT NULL REFERENCES source_datum(source_datum_id),
    submitted_formula_text TEXT NOT NULL,
    parsed_expression TEXT,
    parser_rule_version_id TEXT NOT NULL REFERENCES rule_version(rule_version_id),
    parse_status TEXT NOT NULL CHECK (parse_status IN ('Parsed', 'Unsupported', 'Invalid', 'Missing Dependency')),
    recorded_at_utc TEXT NOT NULL,
    UNIQUE (observation_id, source_datum_id, parser_rule_version_id)
) STRICT;

CREATE TABLE formula_integrity_event (
    formula_integrity_event_id TEXT PRIMARY KEY,
    observation_id TEXT NOT NULL REFERENCES pbd_observation(observation_id),
    formula_evidence_id TEXT REFERENCES formula_evidence(formula_evidence_id),
    integrity_rule_version_id TEXT NOT NULL REFERENCES rule_version(rule_version_id),
    classification TEXT NOT NULL CHECK (classification IN ('Reconciled', 'Formula Reconciliation Exception', 'Insufficient Evidence')),
    expected_coefficient TEXT,
    expected_scale INTEGER CHECK (expected_scale IS NULL OR expected_scale >= 0),
    recalculated_coefficient TEXT,
    recalculated_scale INTEGER CHECK (recalculated_scale IS NULL OR recalculated_scale >= 0),
    variance_coefficient TEXT,
    variance_scale INTEGER CHECK (variance_scale IS NULL OR variance_scale >= 0),
    currency_id TEXT,
    normalized_unit_id TEXT,
    evidence_payload TEXT NOT NULL,
    recorded_at_utc TEXT NOT NULL
) STRICT;

CREATE TABLE activity_measure (
    activity_measure_id TEXT PRIMARY KEY,
    supplier_activity_id TEXT NOT NULL REFERENCES supplier_activity(supplier_activity_id),
    measure_code TEXT NOT NULL,
    before_coefficient TEXT,
    before_scale INTEGER CHECK (before_scale IS NULL OR before_scale >= 0),
    after_coefficient TEXT,
    after_scale INTEGER CHECK (after_scale IS NULL OR after_scale >= 0),
    delta_coefficient TEXT,
    delta_scale INTEGER CHECK (delta_scale IS NULL OR delta_scale >= 0),
    normalized_unit_id TEXT,
    currency_id TEXT,
    comparability_status TEXT NOT NULL CHECK (comparability_status IN ('Comparable', 'Unit Alignment Required', 'Currency Alignment Required', 'Not Applicable')),
    recorded_at_utc TEXT NOT NULL,
    UNIQUE (supplier_activity_id, measure_code)
) STRICT;

CREATE TABLE take_rate_validation (
    take_rate_validation_id TEXT PRIMARY KEY,
    gst_baseline_id TEXT NOT NULL REFERENCES gst_baseline(gst_baseline_id),
    validation_rule_version_id TEXT NOT NULL REFERENCES rule_version(rule_version_id),
    validation_type TEXT NOT NULL,
    severity TEXT NOT NULL CHECK (severity IN ('Information', 'Review Required', 'Blocking Finalization')),
    affected_population TEXT NOT NULL,
    validation_result TEXT NOT NULL,
    recorded_at_utc TEXT NOT NULL
) STRICT;

CREATE TABLE take_rate_decision (
    take_rate_decision_id TEXT PRIMARY KEY,
    take_rate_validation_id TEXT NOT NULL REFERENCES take_rate_validation(take_rate_validation_id),
    decision_code TEXT NOT NULL CHECK (decision_code IN ('Accepted Exception', 'Corrected Baseline', 'Still Under Review')),
    replacement_gst_baseline_id TEXT REFERENCES gst_baseline(gst_baseline_id),
    decided_by_user_id TEXT NOT NULL,
    decision_reason TEXT NOT NULL,
    recorded_at_utc TEXT NOT NULL,
    CHECK ((decision_code = 'Corrected Baseline') = (replacement_gst_baseline_id IS NOT NULL))
) STRICT;

CREATE TABLE scope_activation (
    scope_activation_id TEXT PRIMARY KEY,
    source_package_id TEXT NOT NULL REFERENCES source_package(source_package_id),
    scope_version_id TEXT NOT NULL REFERENCES scope_version(scope_version_id),
    activation_decision TEXT NOT NULL CHECK (activation_decision IN ('Activate', 'Deactivate')),
    decided_by_user_id TEXT NOT NULL,
    decision_reason TEXT NOT NULL,
    recorded_at_utc TEXT NOT NULL
) STRICT;

CREATE TABLE round_conflict (
    round_conflict_id TEXT PRIMARY KEY,
    quote_round_id TEXT NOT NULL REFERENCES supplier_quote_round(quote_round_id),
    event_part_id TEXT NOT NULL REFERENCES event_part(event_part_id),
    prior_round_observation_id TEXT NOT NULL REFERENCES round_observation(round_observation_id),
    new_round_observation_id TEXT NOT NULL REFERENCES round_observation(round_observation_id),
    conflict_type TEXT NOT NULL,
    detected_at_utc TEXT NOT NULL,
    CHECK (prior_round_observation_id <> new_round_observation_id)
) STRICT;

CREATE TABLE round_conflict_decision (
    round_conflict_decision_id TEXT PRIMARY KEY,
    round_conflict_id TEXT NOT NULL REFERENCES round_conflict(round_conflict_id),
    decision_code TEXT NOT NULL CHECK (decision_code IN ('New Replaces Earlier Within Round', 'Move New Submission to New Round', 'Keep Earlier Active', 'Exclude Both Pending Review')),
    target_quote_round_id TEXT REFERENCES supplier_quote_round(quote_round_id),
    decided_by_user_id TEXT NOT NULL,
    decision_reason TEXT NOT NULL,
    recorded_at_utc TEXT NOT NULL
) STRICT;

CREATE TABLE part_activation_exception (
    part_activation_exception_id TEXT PRIMARY KEY,
    round_activation_id TEXT NOT NULL REFERENCES round_activation(round_activation_id),
    event_part_id TEXT NOT NULL REFERENCES event_part(event_part_id),
    selected_round_observation_id TEXT REFERENCES round_observation(round_observation_id),
    exception_decision TEXT NOT NULL CHECK (exception_decision IN ('Activate Selected Record', 'Not Quoted', 'Exclude Pending Review')),
    decided_by_user_id TEXT NOT NULL,
    decision_reason TEXT NOT NULL,
    recorded_at_utc TEXT NOT NULL
) STRICT;

CREATE TABLE incumbency_assignment (
    incumbency_assignment_id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL REFERENCES sourcing_event(event_id),
    event_part_id TEXT REFERENCES event_part(event_part_id),
    supplier_id TEXT NOT NULL,
    assignment_status TEXT NOT NULL CHECK (assignment_status IN ('Incumbent', 'Not Incumbent', 'Unknown')),
    business_valid_from TEXT,
    business_valid_to TEXT,
    confirmed_by_user_id TEXT NOT NULL,
    recorded_at_utc TEXT NOT NULL,
    supersedes_assignment_id TEXT REFERENCES incumbency_assignment(incumbency_assignment_id),
    CHECK (business_valid_to IS NULL OR business_valid_to > business_valid_from)
) STRICT;

CREATE TABLE pce_model_membership (
    pce_model_membership_id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL REFERENCES sourcing_event(event_id),
    event_part_id TEXT NOT NULL REFERENCES event_part(event_part_id),
    observation_id TEXT NOT NULL UNIQUE REFERENCES pbd_observation(observation_id),
    targeted_supplier_id TEXT,
    region_code TEXT,
    recorded_at_utc TEXT NOT NULL
) STRICT;

CREATE TABLE knowledge_conflict (
    knowledge_conflict_id TEXT PRIMARY KEY,
    left_knowledge_version_id TEXT NOT NULL REFERENCES knowledge_version(knowledge_version_id),
    right_knowledge_version_id TEXT NOT NULL REFERENCES knowledge_version(knowledge_version_id),
    conflict_type TEXT NOT NULL,
    detection_rule_version_id TEXT NOT NULL REFERENCES rule_version(rule_version_id),
    conflict_status TEXT NOT NULL CHECK (conflict_status IN ('Open', 'Resolved', 'Accepted Coexistence')),
    detected_at_utc TEXT NOT NULL,
    CHECK (left_knowledge_version_id <> right_knowledge_version_id),
    UNIQUE (left_knowledge_version_id, right_knowledge_version_id, conflict_type)
) STRICT;

CREATE TABLE knowledge_resolution (
    knowledge_resolution_id TEXT PRIMARY KEY,
    knowledge_conflict_id TEXT NOT NULL REFERENCES knowledge_conflict(knowledge_conflict_id),
    decision_code TEXT NOT NULL CHECK (decision_code IN ('Select Left', 'Select Right', 'Create Superseding Version', 'Accept Scoped Coexistence')),
    selected_knowledge_version_id TEXT REFERENCES knowledge_version(knowledge_version_id),
    superseding_knowledge_version_id TEXT REFERENCES knowledge_version(knowledge_version_id),
    decided_by_user_id TEXT NOT NULL,
    decision_authority TEXT NOT NULL,
    decision_reason TEXT NOT NULL,
    recorded_at_utc TEXT NOT NULL
) STRICT;

CREATE TABLE knowledge_candidate (
    knowledge_candidate_id TEXT PRIMARY KEY,
    knowledge_type TEXT NOT NULL,
    proposed_scope_payload TEXT NOT NULL,
    proposed_value_payload TEXT NOT NULL,
    supporting_observation_count INTEGER NOT NULL CHECK (supporting_observation_count >= 0),
    supporting_event_count INTEGER NOT NULL CHECK (supporting_event_count >= 0),
    generation_rule_version_id TEXT NOT NULL REFERENCES rule_version(rule_version_id),
    candidate_status TEXT NOT NULL CHECK (candidate_status IN ('Suggested', 'Dismissed', 'Promoted')),
    generated_at_utc TEXT NOT NULL
) STRICT;

CREATE TABLE supplier_profile_run (
    supplier_profile_run_id TEXT PRIMARY KEY,
    supplier_id TEXT NOT NULL,
    supplier_plant_id TEXT,
    commodity_id TEXT NOT NULL,
    region_code TEXT,
    evidence_cutoff_utc TEXT NOT NULL,
    eligibility_rule_version_id TEXT NOT NULL REFERENCES rule_version(rule_version_id),
    calculation_rule_version_id TEXT NOT NULL REFERENCES rule_version(rule_version_id),
    engine_version_id TEXT NOT NULL REFERENCES engine_version(engine_version_id),
    build_manifest_hash TEXT NOT NULL,
    run_status TEXT NOT NULL CHECK (run_status IN ('Building', 'Complete', 'Failed', 'Cancelled')),
    started_at_utc TEXT NOT NULL,
    completed_at_utc TEXT,
    CHECK ((run_status = 'Complete') = (completed_at_utc IS NOT NULL))
) STRICT;

CREATE TABLE supplier_profile_metric (
    supplier_profile_metric_id TEXT PRIMARY KEY,
    supplier_profile_run_id TEXT NOT NULL REFERENCES supplier_profile_run(supplier_profile_run_id),
    metric_code TEXT NOT NULL,
    decimal_coefficient TEXT,
    decimal_scale INTEGER CHECK (decimal_scale IS NULL OR decimal_scale >= 0),
    text_value TEXT,
    evidence_count INTEGER NOT NULL CHECK (evidence_count >= 0),
    independent_event_count INTEGER NOT NULL CHECK (independent_event_count >= 0),
    confidence_classification TEXT NOT NULL,
    UNIQUE (supplier_profile_run_id, metric_code)
) STRICT;

CREATE TABLE supplier_profile_finding (
    supplier_profile_finding_id TEXT PRIMARY KEY,
    supplier_profile_run_id TEXT NOT NULL REFERENCES supplier_profile_run(supplier_profile_run_id),
    finding_type TEXT NOT NULL,
    finding_status TEXT NOT NULL,
    explanation_payload TEXT NOT NULL,
    evidence_count INTEGER NOT NULL CHECK (evidence_count >= 0),
    independent_event_count INTEGER NOT NULL CHECK (independent_event_count >= 0)
) STRICT;

CREATE TABLE profile_evidence_entry (
    profile_evidence_entry_id TEXT PRIMARY KEY,
    supplier_profile_run_id TEXT NOT NULL REFERENCES supplier_profile_run(supplier_profile_run_id),
    evidence_entity_type TEXT NOT NULL,
    evidence_entity_id TEXT NOT NULL,
    evidence_role TEXT NOT NULL,
    included_flag INTEGER NOT NULL CHECK (included_flag IN (0, 1)),
    exclusion_reason TEXT,
    UNIQUE (supplier_profile_run_id, evidence_entity_type, evidence_entity_id, evidence_role),
    CHECK (included_flag = 1 OR exclusion_reason IS NOT NULL)
) STRICT;

CREATE TABLE scenario_input (
    scenario_input_id TEXT PRIMARY KEY,
    scenario_revision_id TEXT NOT NULL REFERENCES scenario_revision(scenario_revision_id),
    input_code TEXT NOT NULL,
    submitted_lexeme TEXT,
    decimal_coefficient TEXT,
    decimal_scale INTEGER CHECK (decimal_scale IS NULL OR decimal_scale >= 0),
    text_value TEXT,
    normalized_unit_id TEXT,
    currency_id TEXT,
    source_entity_type TEXT,
    source_entity_id TEXT,
    confirmed_by_user_id TEXT,
    recorded_at_utc TEXT NOT NULL,
    UNIQUE (scenario_revision_id, input_code, source_entity_type, source_entity_id)
) STRICT;

CREATE TABLE snapshot_result (
    snapshot_result_id TEXT PRIMARY KEY,
    finalized_snapshot_id TEXT NOT NULL REFERENCES finalized_snapshot(finalized_snapshot_id),
    result_code TEXT NOT NULL,
    supplier_id TEXT,
    part_id TEXT,
    program_year INTEGER,
    category_code TEXT,
    decimal_coefficient TEXT,
    decimal_scale INTEGER CHECK (decimal_scale IS NULL OR decimal_scale >= 0),
    text_value TEXT,
    normalized_unit_id TEXT,
    currency_id TEXT,
    presentation_rule_version_id TEXT NOT NULL REFERENCES rule_version(rule_version_id)
) STRICT;

CREATE TABLE snapshot_action (
    snapshot_action_id TEXT PRIMARY KEY,
    finalized_snapshot_id TEXT NOT NULL REFERENCES finalized_snapshot(finalized_snapshot_id),
    buyer_action_id TEXT NOT NULL REFERENCES buyer_action(buyer_action_id),
    buyer_action_version_id TEXT NOT NULL REFERENCES buyer_action_version(buyer_action_version_id),
    frozen_status TEXT NOT NULL,
    frozen_payload TEXT NOT NULL,
    UNIQUE (finalized_snapshot_id, buyer_action_id)
) STRICT;

CREATE TABLE buyer_note (
    buyer_note_id TEXT PRIMARY KEY,
    parent_entity_type TEXT NOT NULL,
    parent_entity_id TEXT NOT NULL,
    note_type TEXT NOT NULL,
    note_text TEXT NOT NULL,
    authored_by_user_id TEXT NOT NULL,
    authored_at_utc TEXT NOT NULL,
    supersedes_note_id TEXT REFERENCES buyer_note(buyer_note_id)
) STRICT;

CREATE TABLE interpretation (
    interpretation_id TEXT PRIMARY KEY,
    subject_entity_type TEXT NOT NULL,
    subject_entity_id TEXT NOT NULL,
    interpretation_type TEXT NOT NULL,
    interpretation_payload TEXT NOT NULL,
    scope_payload TEXT NOT NULL,
    business_rationale TEXT NOT NULL,
    confirmed_by_user_id TEXT NOT NULL,
    recorded_at_utc TEXT NOT NULL,
    supersedes_interpretation_id TEXT REFERENCES interpretation(interpretation_id)
) STRICT;

CREATE TABLE local_checkpoint (
    local_checkpoint_id TEXT PRIMARY KEY,
    database_id TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    database_hash TEXT NOT NULL,
    audit_chain_anchor TEXT NOT NULL,
    verification_status TEXT NOT NULL CHECK (verification_status IN ('Pending', 'Verified', 'Failed')),
    created_at_utc TEXT NOT NULL,
    verified_at_utc TEXT
) STRICT;

CREATE TABLE publication (
    publication_id TEXT PRIMARY KEY,
    local_checkpoint_id TEXT NOT NULL REFERENCES local_checkpoint(local_checkpoint_id),
    destination_locator TEXT NOT NULL,
    publication_status TEXT NOT NULL CHECK (publication_status IN ('Prepared', 'Locally Verified', 'Queued', 'Uploading', 'Remote Verification', 'Published', 'Retry Pending', 'Conflict', 'Failed')),
    package_hash TEXT NOT NULL,
    audit_chain_anchor TEXT NOT NULL,
    digital_signature TEXT NOT NULL,
    initiated_at_utc TEXT NOT NULL,
    completed_at_utc TEXT
) STRICT;

CREATE TABLE publication_manifest_entry (
    publication_manifest_entry_id TEXT PRIMARY KEY,
    publication_id TEXT NOT NULL REFERENCES publication(publication_id),
    entry_type TEXT NOT NULL,
    relative_path TEXT NOT NULL,
    size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0),
    content_hash TEXT NOT NULL,
    version_payload TEXT NOT NULL,
    UNIQUE (publication_id, relative_path)
) STRICT;

CREATE TABLE sync_queue_item (
    sync_queue_item_id TEXT PRIMARY KEY,
    publication_id TEXT NOT NULL REFERENCES publication(publication_id),
    idempotency_key TEXT NOT NULL UNIQUE,
    queue_status TEXT NOT NULL CHECK (queue_status IN ('Queued', 'Leased', 'Retry Pending', 'Complete', 'Failed', 'Conflict')),
    retry_count INTEGER NOT NULL DEFAULT 0 CHECK (retry_count >= 0),
    lease_owner TEXT,
    lease_expires_at_utc TEXT,
    last_error TEXT,
    recorded_at_utc TEXT NOT NULL
) STRICT;

CREATE TABLE sync_conflict (
    sync_conflict_id TEXT PRIMARY KEY,
    local_publication_id TEXT NOT NULL REFERENCES publication(publication_id),
    remote_publication_id TEXT,
    conflict_type TEXT NOT NULL,
    conflict_status TEXT NOT NULL CHECK (conflict_status IN ('Open', 'Resolved', 'Accepted Exception')),
    detected_at_utc TEXT NOT NULL,
    resolution_payload TEXT
) STRICT;

CREATE TABLE restore_event (
    restore_event_id TEXT PRIMARY KEY,
    local_checkpoint_id TEXT REFERENCES local_checkpoint(local_checkpoint_id),
    publication_id TEXT REFERENCES publication(publication_id),
    target_instance_id TEXT NOT NULL,
    restore_type TEXT NOT NULL CHECK (restore_type IN ('Automated Test', 'Production Recovery', 'Migration Validation')),
    restore_status TEXT NOT NULL CHECK (restore_status IN ('Started', 'Verified', 'Failed')),
    integrity_result TEXT,
    audit_chain_result TEXT,
    schema_result TEXT,
    reproduction_result TEXT,
    started_at_utc TEXT NOT NULL,
    completed_at_utc TEXT,
    CHECK (local_checkpoint_id IS NOT NULL OR publication_id IS NOT NULL)
) STRICT;

CREATE TABLE event_summary_projection (
    projection_generation_id TEXT NOT NULL,
    event_id TEXT NOT NULL REFERENCES sourcing_event(event_id),
    source_package_number TEXT NOT NULL,
    event_name TEXT NOT NULL,
    buyer_code_id TEXT NOT NULL,
    event_status TEXT NOT NULL,
    supplier_count INTEGER NOT NULL CHECK (supplier_count >= 0),
    open_action_count INTEGER NOT NULL CHECK (open_action_count >= 0),
    latest_activity_at_utc TEXT,
    evidence_cutoff_utc TEXT NOT NULL,
    build_manifest_hash TEXT NOT NULL,
    PRIMARY KEY (projection_generation_id, event_id)
) WITHOUT ROWID, STRICT;

CREATE TABLE active_round_part_projection (
    projection_generation_id TEXT NOT NULL,
    event_id TEXT NOT NULL REFERENCES sourcing_event(event_id),
    supplier_id TEXT NOT NULL,
    event_part_id TEXT NOT NULL REFERENCES event_part(event_part_id),
    quote_round_id TEXT REFERENCES supplier_quote_round(quote_round_id),
    observation_id TEXT REFERENCES pbd_observation(observation_id),
    coverage_status TEXT NOT NULL,
    piece_price_coefficient TEXT,
    piece_price_scale INTEGER CHECK (piece_price_scale IS NULL OR piece_price_scale >= 0),
    currency_id TEXT,
    normalized_unit_id TEXT,
    evidence_cutoff_utc TEXT NOT NULL,
    build_manifest_hash TEXT NOT NULL,
    PRIMARY KEY (projection_generation_id, event_id, supplier_id, event_part_id)
) WITHOUT ROWID, STRICT;

CREATE INDEX ix_formula_integrity_observation ON formula_integrity_event(observation_id, classification, integrity_rule_version_id);
CREATE INDEX ix_activity_measure_code ON activity_measure(measure_code, comparability_status, supplier_activity_id);
CREATE INDEX ix_take_rate_baseline_severity ON take_rate_validation(gst_baseline_id, severity, validation_type);
CREATE INDEX ix_scope_activation_current ON scope_activation(source_package_id, recorded_at_utc DESC, scope_activation_id);
CREATE INDEX ix_round_conflict_open ON round_conflict(quote_round_id, event_part_id, detected_at_utc);
CREATE INDEX ix_incumbency_event_part ON incumbency_assignment(event_id, event_part_id, recorded_at_utc DESC);
CREATE INDEX ix_knowledge_conflict_status ON knowledge_conflict(conflict_status, conflict_type, detected_at_utc);
CREATE INDEX ix_profile_scope_cutoff ON supplier_profile_run(supplier_id, supplier_plant_id, commodity_id, region_code, evidence_cutoff_utc DESC);
CREATE INDEX ix_profile_evidence_reverse ON profile_evidence_entry(evidence_entity_type, evidence_entity_id, supplier_profile_run_id);
CREATE INDEX ix_snapshot_result_dimensions ON snapshot_result(finalized_snapshot_id, result_code, supplier_id, part_id, program_year, category_code);
CREATE INDEX ix_buyer_note_parent ON buyer_note(parent_entity_type, parent_entity_id, authored_at_utc);
CREATE INDEX ix_publication_status ON publication(publication_status, initiated_at_utc, publication_id);
CREATE INDEX ix_sync_queue_status ON sync_queue_item(queue_status, lease_expires_at_utc, recorded_at_utc);
CREATE INDEX ix_event_summary_buyer ON event_summary_projection(projection_generation_id, buyer_code_id, event_status, latest_activity_at_utc DESC);
CREATE INDEX ix_active_round_part_supplier ON active_round_part_projection(projection_generation_id, event_id, supplier_id, coverage_status);

CREATE VIEW v_current_event_scope AS
SELECT sa.*
FROM scope_activation sa
WHERE sa.activation_decision = 'Activate'
  AND NOT EXISTS (
      SELECT 1 FROM scope_activation newer
      WHERE newer.source_package_id = sa.source_package_id
        AND (newer.recorded_at_utc > sa.recorded_at_utc
             OR (newer.recorded_at_utc = sa.recorded_at_utc AND newer.scope_activation_id > sa.scope_activation_id))
  );

CREATE VIEW v_latest_supplier_profile AS
SELECT spr.*
FROM supplier_profile_run spr
WHERE spr.run_status = 'Complete'
  AND NOT EXISTS (
      SELECT 1 FROM supplier_profile_run newer
      WHERE newer.supplier_id = spr.supplier_id
        AND COALESCE(newer.supplier_plant_id, '') = COALESCE(spr.supplier_plant_id, '')
        AND newer.commodity_id = spr.commodity_id
        AND COALESCE(newer.region_code, '') = COALESCE(spr.region_code, '')
        AND newer.run_status = 'Complete'
        AND (newer.evidence_cutoff_utc > spr.evidence_cutoff_utc
             OR (newer.evidence_cutoff_utc = spr.evidence_cutoff_utc
                 AND newer.supplier_profile_run_id > spr.supplier_profile_run_id))
  );

CREATE TRIGGER no_update_formula_evidence BEFORE UPDATE ON formula_evidence BEGIN SELECT RAISE(ABORT, 'formula_evidence is immutable'); END;
CREATE TRIGGER no_delete_formula_evidence BEFORE DELETE ON formula_evidence BEGIN SELECT RAISE(ABORT, 'formula_evidence cannot be deleted'); END;
CREATE TRIGGER no_update_activity_measure BEFORE UPDATE ON activity_measure BEGIN SELECT RAISE(ABORT, 'activity_measure is immutable'); END;
CREATE TRIGGER no_delete_activity_measure BEFORE DELETE ON activity_measure BEGIN SELECT RAISE(ABORT, 'activity_measure cannot be deleted'); END;
CREATE TRIGGER no_update_snapshot_result BEFORE UPDATE ON snapshot_result BEGIN SELECT RAISE(ABORT, 'snapshot_result is immutable'); END;
CREATE TRIGGER no_delete_snapshot_result BEFORE DELETE ON snapshot_result BEGIN SELECT RAISE(ABORT, 'snapshot_result cannot be deleted'); END;
CREATE TRIGGER no_update_snapshot_action BEFORE UPDATE ON snapshot_action BEGIN SELECT RAISE(ABORT, 'snapshot_action is immutable'); END;
CREATE TRIGGER no_delete_snapshot_action BEFORE DELETE ON snapshot_action BEGIN SELECT RAISE(ABORT, 'snapshot_action cannot be deleted'); END;
CREATE TRIGGER no_update_buyer_note BEFORE UPDATE ON buyer_note BEGIN SELECT RAISE(ABORT, 'buyer_note is append-only; supersede it'); END;
CREATE TRIGGER no_delete_buyer_note BEFORE DELETE ON buyer_note BEGIN SELECT RAISE(ABORT, 'buyer_note cannot be deleted'); END;
CREATE TRIGGER no_update_publication_manifest BEFORE UPDATE ON publication_manifest_entry BEGIN SELECT RAISE(ABORT, 'publication_manifest_entry is immutable'); END;
CREATE TRIGGER no_delete_publication_manifest BEFORE DELETE ON publication_manifest_entry BEGIN SELECT RAISE(ABORT, 'publication_manifest_entry cannot be deleted'); END;
CREATE TRIGGER no_update_restore_event BEFORE UPDATE ON restore_event BEGIN SELECT RAISE(ABORT, 'restore_event is immutable'); END;
CREATE TRIGGER no_delete_restore_event BEFORE DELETE ON restore_event BEGIN SELECT RAISE(ABORT, 'restore_event cannot be deleted'); END;

INSERT INTO schema_migration VALUES (
    '0002', 'commodity_intelligence', '__FORGE_MIGRATION_SHA256__',
    strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'schema-prototype-0.2'
);

COMMIT;
