# Forge X Database Query and Index Plan

Version: 0.1  
Recorded: September 2, 2026  
Status: Initial benchmark plan; final indexes require measured validation

## 1. Performance objectives

At the approved full initial benchmark of at least 250,000 committed PBD observations and 10 million detailed records per commodity database:

- database validation and open completes within 5 seconds at p95, excluding SharePoint transfer or restore;
- common searches, filters, summaries, and drill-downs complete within 2 seconds at p95;
- full commodity governing recalculation completes within 60 seconds;
- 1,000 qualifying PBD worksheets complete import/staging within 15 minutes, excluding buyer confirmation;
- long operations report progress and cancel without partial governing transactions.

These targets are evaluated on a defined approved business-laptop configuration using cold, warm, and mixed-cache runs.

## 2. Query principles

- Index real access paths, not every foreign key or conceivable filter.
- Lead composite indexes with equality predicates, followed by range/order columns.
- Use narrow technical IDs in joins and keep large submitted text outside hot covering indexes.
- Resolve “current” state through narrow decision indexes or atomically built projections.
- Use explicit evidence manifests for reproducibility rather than rerunning historical population queries.
- Use derived projections for dashboards; use authoritative tables for drill-down and validation.
- Keep index creation and removal in versioned schema migrations.
- Capture representative query plans and p50/p95 latency before accepting an index change.

## 3. Critical query catalog

| ID | User/system question | Expected grain | Target |
|---|---|---|---|
| Q01 | Find sourcing event by exact source-package number | One event | <200 ms warm |
| Q02 | List events for authorized buyer/commodity with status and latest activity | Event summary | <2 s p95 |
| Q03 | Find exact part across current and historical supplier observations | Observation summary | <2 s p95 |
| Q04 | Open a supplier's active round and package coverage | Supplier/event summary | <2 s p95 |
| Q05 | Compare selected supplier rounds on common part scope | Part/supplier matrix | <2 s summary; drill-down paged |
| Q06 | Display complete source lineage for one result | Evidence graph | <2 s p95 |
| Q07 | Display supplier activity timeline by plant and commodity | Activity page | <2 s p95 |
| Q08 | Resolve applicable confirmed knowledge as-of an analysis date | Knowledge versions | <500 ms typical |
| Q09 | Display latest supplier profile and supporting evidence | Profile summary + page | <2 s p95 |
| Q10 | Reconcile every tab in an import session | Occurrence status | <2 s p95 |
| Q11 | Build analysis evidence manifest for selected active rounds | Evidence population | Included in 60 s recalculation target |
| Q12 | Reproduce a finalized snapshot | Frozen result population | <2 s summary |
| Q13 | Find open buyer actions by event, supplier, owner, or status | Action rows | <2 s p95 |
| Q14 | Verify publication readiness and audit anchor | One readiness report | <5 s open/integrity envelope |
| Q15 | Detect duplicate file, worksheet, and logical observation | Fingerprint lookup | Near constant-time per fingerprint |

## 4. Candidate authoritative indexes

Names are logical and may be shortened for the selected database engine.

### 4.1 Identity and registry cache

| Index | Columns | Purpose |
|---|---|---|
| `ux_registry_cache_entity_generation` | `(cache_generation_id, entity_type, entity_id)` unique | One cached entity per registry generation |
| `ix_registry_cache_entity_latest` | `(entity_type, entity_id, cache_generation_id DESC)` | Historical display and latest verified lookup |
| `ux_rule_domain_version` | `(domain, semantic_version)` unique | Pin exact rule version |

### 4.2 Import and source

| Index | Columns | Purpose |
|---|---|---|
| `ix_source_workbook_file_hash` | `(file_hash, file_size)` | File duplicate detection |
| `ux_source_worksheet_ordinal` | `(workbook_id, ordinal)` unique | Complete workbook inventory |
| `ix_source_worksheet_fingerprint` | `(sheet_fingerprint)` | Unchanged-tab detection |
| `ix_occurrence_transaction_status` | `(transaction_id, terminal_status, occurrence_id)` | Import reconciliation |
| `ix_occurrence_logical_fingerprint` | `(logical_fingerprint, detection_rule_version)` | Logical duplicate detection |
| `ix_source_datum_location` | `(worksheet_id, row_number, column_number)` | Exact cell lineage |
| `ix_extraction_exception_open` | `(extraction_result_id, blocking_flag, exception_type)` | Staging review |
| `ux_fingerprint_scope` | `(entity_type, entity_id, purpose, algorithm, rule_version_id)` unique | One governed fingerprint per purpose/version |
| `ix_fingerprint_digest` | `(purpose, algorithm, digest)` | Duplicate/integrity match |

### 4.3 Observation evidence

| Index | Columns | Purpose |
|---|---|---|
| `ix_observation_part_date` | `(part_id, economic_date DESC, observation_id)` | Exact-part history |
| `ix_observation_supplier_plant_date` | `(supplier_id, plant_id, economic_date DESC, observation_id)` | Supplier/plant history |
| `ix_observation_context` | `(context_type, context_id, supplier_id, part_id)` | Event/baseline populations |
| `ix_observation_eligibility_current` | `(observation_id, analytical_role, recorded_at_utc DESC)` | Resolve eligibility |
| `ix_submitted_datum_field` | `(observation_id, field_code, submitted_datum_id)` | Semantic field retrieval |
| `ix_cost_element_category` | `(observation_id, element_type, section_id)` | Cost-structure drill-down |
| `ix_material_observation` | `(observation_id, material_line_id)` | Ordered material retrieval |
| `ix_operation_observation_sequence` | `(observation_id, operation_sequence, operation_line_id)` | Ordered operation retrieval |
| `ix_formula_integrity_observation` | `(observation_id, classification, rule_version_id)` | Formula exception analysis |

Large detail tables should be physically clustered or inserted in observation order when supported so one-observation drill-down reads adjacent pages.

### 4.4 Sourcing and rounds

| Index | Columns | Purpose |
|---|---|---|
| `ux_source_package_active_number` | `(commodity_id, normalized_package_number)` unique for non-retired identity | Direct event lookup |
| `ix_event_buyer_status_activity` | `(buyer_code_id, status, last_activity_at_utc DESC)` | Buyer event list |
| `ux_scope_version_number` | `(source_package_id, version_number)` unique | Version identity |
| `ix_event_part_scope_part` | `(scope_version_id, part_id, event_part_id)` | Current scope and coverage |
| `ux_gst_part_year_measure` | `(gst_baseline_id, event_part_id, program_year, measure_code)` unique | Target/FPV lookup |
| `ux_supplier_round_number` | `(event_id, supplier_id, round_number)` unique | Supplier-specific round identity |
| `ix_round_observation_part` | `(round_id, part_id, membership_status, membership_id)` | Package part resolution |
| `ix_round_observation_observation` | `(observation_id, round_id)` | Reverse lineage |
| `ix_round_activation_current` | `(event_id, supplier_id, recorded_at_utc DESC, activation_id)` | Latest active round decision |
| `ix_carry_forward_round_part` | `(target_round_id, event_part_id, recorded_at_utc DESC)` | Current omission treatment |
| `ix_round_conflict_open` | `(round_id, resolution_status, part_id)` | Conflicting submission review |

### 4.5 Supplier activity and knowledge

| Index | Columns | Purpose |
|---|---|---|
| `ix_activity_supplier_scope_time` | `(supplier_id, plant_id, commodity_id, occurred_at_utc DESC, activity_id)` | Scoped timeline |
| `ix_activity_event_round_part` | `(event_id, round_id, part_id, activity_type)` | Round evolution |
| `ix_activity_type_time` | `(activity_type, occurred_at_utc DESC, supplier_id)` | Pattern population scans |
| `ix_knowledge_scope_lookup` | `(knowledge_type, supplier_id, plant_id, commodity_id, part_id, family_id, region_id)` | Candidate applicable assertions |
| `ix_knowledge_bitemporal` | `(knowledge_item_id, business_valid_from, business_valid_to, recorded_at_utc DESC)` | As-of reconstruction |
| `ix_knowledge_version_level_status` | `(level, status, knowledge_type, recorded_at_utc DESC)` | Governance queues |
| `ix_knowledge_evidence_reverse` | `(evidence_entity_type, evidence_entity_id, knowledge_version_id)` | Evidence-to-knowledge trace |
| `ix_knowledge_conflict_open` | `(status, knowledge_type, detected_at_utc)` | Resolution workflow |

The multi-dimensional knowledge index must be validated carefully because nullable dimensions can reduce usefulness. Benchmark alternatives using a computed `scope_signature` and separate indexes for the most common specificity levels.

### 4.6 Analysis, actions, and audit

| Index | Columns | Purpose |
|---|---|---|
| `ux_scenario_revision` | `(analysis_id, revision_number)` unique | Revision order |
| `ix_manifest_scenario_role` | `(scenario_revision_id, included_flag, analytical_role, evidence_entity_type)` | Reproduction and counts |
| `ix_manifest_evidence_reverse` | `(evidence_entity_type, evidence_entity_id, scenario_revision_id)` | Impact analysis |
| `ix_result_run_dimensions` | `(run_id, result_code, supplier_id, part_id, program_year, category_id)` | Result retrieval |
| `ix_lineage_result` | `(result_id, dependency_role, ordinal)` | Explain result |
| `ix_lineage_source_reverse` | `(source_entity_type, source_entity_id, result_id)` | Determine affected results |
| `ix_snapshot_analysis_time` | `(analysis_id, finalized_at_utc DESC)` | Snapshot history |
| `ix_action_event_status_owner` | `(event_id, current_status_projection, owner_user_id, supplier_id)` | Consolidated action list |
| `ix_action_version_latest` | `(action_id, recorded_at_utc DESC, action_version_id)` | Resolve current action state |
| `ux_audit_sequence` | `(recorded_sequence)` unique | Chain order |
| `ux_audit_event_hash` | `(event_hash)` unique | Tamper detection |
| `ix_audit_entity_reverse` | detail table `(entity_type, entity_id, audit_event_id)` | Entity audit history |
| `ix_publication_status_time` | `(status, initiated_at_utc, publication_id)` | Publication operations |

## 5. Derived projections

### 5.1 Event summary projection

One row per sourcing event containing source package, readable name, buyer, current scope version, supplier count, current coverage statuses, latest activity time, action counts, and latest finalized snapshot. Rebuilt incrementally from immutable changes and periodically verified through full rebuild.

### 5.2 Active round part projection

One row per event, supplier, and current-scope part containing the selected active observation, carry-forward state, coverage classification, exact piece price, currency/unit, source round, and evidence age. This projection drives sourcing comparison tables and avoids repeated resolution of activation history.

### 5.3 Supplier profile projection

One row per supplier/plant/commodity/region/profile generation and child rows for metrics/findings. It includes quote count and independent-event count separately, evidence cutoff, rule versions, and a manifest hash.

### 5.4 Part history projection

One row per exact part observation and a separate buyer-confirmed family-comparison row. Exact and functional-family evidence are never combined without a visible relationship type.

### 5.5 Knowledge-resolution projection

Optional cache keyed by knowledge type, normalized context signature, economic-date bucket, and registry/knowledge generation. Cache entries store the selected knowledge-version ID or conflict ID. They are performance aids only; finalized analyses pin the actual selected version.

### 5.6 Search document projection

A rebuildable full-text document containing normalized identifiers and searchable names/descriptions for events, source packages, parts, suppliers, plants, programs, actions, and knowledge. Search results resolve back to authoritative technical IDs.

## 6. Projection publication pattern

1. Create a new generation row with status `Building` and a pinned evidence cutoff.
2. Populate projection rows without changing the active generation.
3. Validate counts, hashes, required references, and representative authoritative comparisons.
4. Mark the generation `Complete`.
5. Atomically append an activation decision or switch the small active-generation pointer.
6. Retain the prior generation until the replacement and recovery checkpoint are verified.

Users never query a partially built generation.

## 7. Pagination and result limits

- Use keyset pagination based on stable ordered columns and technical ID tie-breakers.
- Avoid large `OFFSET` scans for evidence, activity, actions, and part histories.
- Require bounded date/scope filters for high-volume exports.
- Stream exports from a consistent read snapshot and record the evidence cutoff and query manifest.
- Return summary first and load evidence details on demand.

## 8. Calculation execution

Full recalculation should:

1. Resolve and persist the selected scope, GST baseline, supplier rounds, PCE models, knowledge versions, and explicit exclusions.
2. Read observation facts in technical-ID batches ordered for locality.
3. Perform governing arithmetic in the approved base-10 calculation engine.
4. Write results and lineage to a new calculation-run generation.
5. Validate result counts, reconciliation totals, precision status, and output hash.
6. Commit the completed run atomically and leave failed/cancelled runs non-governing.

Do not perform governing decimal arithmetic through SQL binary floating-point functions.

## 9. Benchmark dataset

The synthetic acceptance population must include:

- at least 250,000 PBD observations and 10 million detail rows;
- single-PBD and multi-tab workbooks;
- exact duplicate files, unchanged tabs, changed tabs, and logical duplicates;
- detailed, valid aggregate, incomplete, and formula-exception structures;
- provisional and confirmed suppliers across multiple plants/regions;
- multiple events and many supplier-specific rounds with partial requotes;
- carry-forward, not-quoted, conflicts, additions, removals, and corrections;
- three-year eligibility boundaries and late-arriving knowledge;
- functional-family permissions and exact-part evidence;
- GST baseline versions, PCE models, and take-rate exceptions;
- profile generations, knowledge conflicts, audit events, publications, and restores.

Data distributions must be skewed realistically so one large supplier or event does not hide worst-case access patterns.

## 10. Performance test protocol

- Define CPU, memory, storage, operating system, encryption provider, and power mode of the reference laptop.
- Run a clean restored database before each benchmark suite.
- Measure cold start, warm cache, and mixed interactive workloads separately.
- Execute each critical query enough times to report median, p95, maximum, rows examined, rows returned, and plan fingerprint.
- Measure encryption/key-release time separately from integrity checks and application initialization.
- Measure import discovery, fingerprinting, extraction, validation, staging, and commit independently.
- Verify cancellation at multiple phases and confirm no partial governing state.
- Compare database size, index size, write amplification, and projection rebuild time.
- Fail acceptance when a faster plan weakens lineage, precision, immutability, or reproducibility.

## 11. Initial tuning sequence

1. Implement the normalized core with only uniqueness and critical foreign-key indexes.
2. Load the full synthetic dataset.
3. Capture baseline query plans and timings for Q01–Q15.
4. Add candidate composite indexes one query family at a time.
5. Introduce active-round, event-summary, and supplier-profile projections where authoritative joins cannot meet targets.
6. Test write/import impact and database size after each index.
7. Remove redundant indexes only through a verified migration.
8. Freeze the accepted index manifest with schema and benchmark versions.

## 12. Acceptance evidence

The design is not performance-approved until the repository contains:

- reference hardware specification;
- deterministic synthetic-data generator and seed manifest;
- schema and index manifest;
- critical query SQL and expected result assertions;
- captured query plans;
- benchmark results with p50/p95/max;
- import and recalculation phase timings;
- cancellation and recovery evidence;
- restored-database reproduction results.
