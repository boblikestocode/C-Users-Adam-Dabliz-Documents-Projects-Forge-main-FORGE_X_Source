from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from database.migration_runner import DEFAULT_MIGRATIONS, file_checksum, migration_files

from .audit import canonical_json, verify_audit_chain
from .decimals import ExactDecimal
from .competitiveness import (current_historical_context_payload,
                              derive_improvement_assessment,
                              improvement_evidence_rows)
from .outputs import build_output_contract, required_output_filename_prefix
from .piece_price import projection_source_rows
from .profiles import (derive_profile_outputs, profile_activity_rows,
                       profile_formula_exception_rows)
from .quote_versions import (DEFAULT_MOVEMENT_FIELDS, _candidate_values,
                             _movement_row)
from .registry_cache import registry_cache_manifest
from .summary_projections import event_summary_source_rows
from .traffic_lights import (TrafficLightCategoryInput, _validate_evidence,
                             derive_supplier_overall_traffic_light,
                             derive_traffic_light)
from .search import search_source_rows
from .part_history import part_history_source_rows
from .historical_baseline import historical_baseline_summary_payload


@dataclass(frozen=True)
class IntegrityFinding:
    severity: str
    code: str
    entity_type: str | None
    entity_id: str | None
    detail: str


@dataclass(frozen=True)
class AnalysisReadiness:
    can_proceed: bool
    blocking_findings: tuple[IntegrityFinding, ...]
    advisory_findings: tuple[IntegrityFinding, ...]


POLYMORPHIC_ENTITIES = {
    "PBD Observation": ("pbd_observation", "observation_id"),
    "Supplier Activity": ("supplier_activity", "supplier_activity_id"),
    "GST Baseline": ("gst_baseline", "gst_baseline_id"),
    "PCE Model": ("pce_model_membership", "pce_model_membership_id"),
    "Knowledge Version": ("knowledge_version", "knowledge_version_id"),
    "Quote Round": ("supplier_quote_round", "quote_round_id"),
    "Formula Integrity Event": ("formula_integrity_event", "formula_integrity_event_id"),
    "Finalized Snapshot": ("finalized_snapshot", "finalized_snapshot_id"),
    "Round Observation": ("round_observation", "round_observation_id"),
    "Submitted Datum": ("submitted_datum", "submitted_datum_id"),
    "Cross Border Reconstruction": ("cross_border_reconstruction", "cross_border_reconstruction_id"),
    "Cross Border Operation Match": ("cross_border_operation_match", "cross_border_operation_match_id"),
}


def _finding(code: str, detail: str, entity_type: str | None = None, entity_id: str | None = None, severity: str = "Error") -> IntegrityFinding:
    return IntegrityFinding(severity, code, entity_type, entity_id, detail)


def verify_migration_checksums(
    connection: sqlite3.Connection,
    migrations_dir: Path = DEFAULT_MIGRATIONS,
) -> list[IntegrityFinding]:
    findings: list[IntegrityFinding] = []
    applied = {
        row[0]: row[1]
        for row in connection.execute(
            "SELECT migration_version, migration_checksum FROM schema_migration"
        )
    }
    for path in migration_files(migrations_dir):
        version = path.name.split("_", 1)[0]
        expected = file_checksum(path)
        if version not in applied:
            findings.append(_finding("MIGRATION_MISSING", f"Migration {path.name} has not been applied", "Schema Migration", version))
        elif applied[version] != expected:
            findings.append(_finding("MIGRATION_CHECKSUM_MISMATCH", f"Migration {path.name} differs from the applied checksum", "Schema Migration", version))
    for version in sorted(applied.keys() - {path.name.split('_', 1)[0] for path in migration_files(migrations_dir)}):
        findings.append(_finding("MIGRATION_FILE_MISSING", f"Applied migration {version} is missing from the migration package", "Schema Migration", version))
    return findings


def verify_polymorphic_references(connection: sqlite3.Connection) -> list[IntegrityFinding]:
    findings: list[IntegrityFinding] = []
    sources = (
        ("evidence_manifest_entry", "evidence_manifest_entry_id", "evidence_entity_type", "evidence_entity_id"),
        ("calculation_lineage", "calculation_lineage_id", "source_entity_type", "source_entity_id"),
        ("knowledge_evidence", "knowledge_evidence_id", "evidence_entity_type", "evidence_entity_id"),
    )
    for source_table, source_key, type_column, id_column in sources:
        for row in connection.execute(
            f"SELECT {source_key}, {type_column}, {id_column} FROM {source_table}"
        ):
            source_id, entity_type, entity_id = map(str, row)
            location = POLYMORPHIC_ENTITIES.get(entity_type)
            if location is None:
                findings.append(_finding("UNSUPPORTED_POLYMORPHIC_TYPE", f"{source_table} uses unsupported entity type {entity_type}", source_table, source_id))
                continue
            table, key = location
            if connection.execute(f"SELECT 1 FROM {table} WHERE {key} = ?", (entity_id,)).fetchone() is None:
                findings.append(_finding("BROKEN_POLYMORPHIC_REFERENCE", f"{entity_type} {entity_id} does not exist", source_table, source_id))
    return findings


def verify_exact_decimals(connection: sqlite3.Connection) -> list[IntegrityFinding]:
    findings: list[IntegrityFinding] = []
    for row in connection.execute(
        """SELECT submitted_datum_id, submitted_lexeme, decimal_coefficient,
                  decimal_scale, governing_1e4, precision_status
           FROM submitted_datum WHERE decimal_coefficient IS NOT NULL"""
    ):
        datum_id, lexeme, coefficient, scale, governing, status = row
        try:
            reconstructed = ExactDecimal(str(lexeme or coefficient), str(coefficient), int(scale))
            int(str(coefficient))
            if status == "Eligible" and governing != reconstructed.governing_1e4():
                findings.append(_finding("GOVERNING_DECIMAL_MISMATCH", f"Stored governing_1e4 {governing} does not match coefficient/scale", "Submitted Datum", str(datum_id)))
        except (ValueError, OverflowError) as error:
            findings.append(_finding("INVALID_EXACT_DECIMAL", str(error), "Submitted Datum", str(datum_id)))
    return findings


def verify_import_reconciliation(connection: sqlite3.Connection) -> list[IntegrityFinding]:
    findings: list[IntegrityFinding] = []
    for row in connection.execute(
        """SELECT it.import_transaction_id, it.status, it.discovered_count,
                  it.committed_count, it.duplicate_count, it.blocked_count,
                  it.ignored_count, it.failed_count,
                  vr.occurrence_count, vr.committed_count, vr.duplicate_count,
                  vr.blocked_count, vr.ignored_count, vr.failed_count,
                  vr.pending_count
           FROM import_transaction it
           JOIN v_source_tab_reconciliation vr
             ON vr.import_transaction_id = it.import_transaction_id
           WHERE it.status = 'Committed'"""
    ):
        transaction_id = str(row[0])
        stored = tuple(int(value) for value in row[2:8])
        actual = tuple(int(value) for value in row[8:14])
        if stored != actual:
            findings.append(_finding("IMPORT_COUNT_MISMATCH", f"Stored counts {stored} do not match occurrence counts {actual}", "Import Transaction", transaction_id))
        if int(row[14]) != 0:
            findings.append(_finding("COMPLETED_IMPORT_HAS_PENDING_SOURCE", f"Committed transaction retains {row[14]} pending occurrences", "Import Transaction", transaction_id))
    return findings


def verify_import_discovery(connection: sqlite3.Connection) -> list[IntegrityFinding]:
    findings: list[IntegrityFinding] = []
    for row in connection.execute(
        """SELECT txn.import_transaction_id,
                  reconciliation.discovered_workbook_count,
                  reconciliation.registered_workbook_count,
                  reconciliation.ignored_workbook_count,
                  reconciliation.failed_workbook_count,
                  reconciliation.pending_workbook_count,
                  (SELECT COUNT(*) FROM source_workbook workbook
                   WHERE workbook.import_transaction_id = txn.import_transaction_id)
           FROM import_transaction txn
           LEFT JOIN v_import_discovery_reconciliation reconciliation
             ON reconciliation.import_transaction_id = txn.import_transaction_id
           WHERE txn.status = 'Committed'"""
    ):
        transaction_id = str(row[0])
        if row[1] is None:
            findings.append(_finding(
                "IMPORT_DISCOVERY_MANIFEST_MISSING",
                "Committed import has no declared workbook discovery inventory",
                "Import Transaction", transaction_id,
            ))
            continue
        discovered, registered, ignored, failed, pending, workbook_count = (
            int(value) for value in row[1:]
        )
        if pending:
            findings.append(_finding(
                "COMMITTED_IMPORT_HAS_PENDING_WORKBOOK",
                f"Committed import retains {pending} pending discovered workbooks",
                "Import Transaction", transaction_id,
            ))
        if discovered != registered + ignored + failed:
            findings.append(_finding(
                "IMPORT_DISCOVERY_COUNT_MISMATCH",
                "Discovered workbook dispositions do not reconcile",
                "Import Transaction", transaction_id,
            ))
        if registered != workbook_count:
            findings.append(_finding(
                "REGISTERED_WORKBOOK_COUNT_MISMATCH",
                f"Discovery manifest has {registered} registered workbooks but source evidence has {workbook_count}",
                "Import Transaction", transaction_id,
            ))
    return findings


def verify_source_fingerprints(connection: sqlite3.Connection) -> list[IntegrityFinding]:
    """Independently verify layered source hashes and duplicate lineage."""
    findings: list[IntegrityFinding] = []
    for workbook_id, file_hash, fingerprint_count, matching_count in connection.execute(
        """SELECT workbook.workbook_id, workbook.file_hash_sha256,
                  COUNT(fingerprint.fingerprint_id),
                  SUM(CASE WHEN fingerprint.digest = workbook.file_hash_sha256
                           THEN 1 ELSE 0 END)
           FROM source_workbook workbook
           LEFT JOIN fingerprint
             ON fingerprint.entity_type = 'Source Workbook'
            AND fingerprint.entity_id = workbook.workbook_id
            AND fingerprint.purpose = 'Exact File Duplicate'
            AND fingerprint.algorithm = 'SHA-256'
           GROUP BY workbook.workbook_id, workbook.file_hash_sha256"""
    ):
        digest = str(file_hash)
        if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
            findings.append(_finding(
                "SOURCE_WORKBOOK_HASH_INVALID",
                "Workbook SHA-256 must be 64 lowercase hexadecimal characters",
                "Source Workbook", str(workbook_id),
            ))
        if int(fingerprint_count) != 1:
            findings.append(_finding(
                "SOURCE_WORKBOOK_FINGERPRINT_CARDINALITY",
                f"Expected one exact-file SHA-256 fingerprint; found {fingerprint_count}",
                "Source Workbook", str(workbook_id),
            ))
        elif int(matching_count or 0) != 1:
            findings.append(_finding(
                "SOURCE_WORKBOOK_FINGERPRINT_MISMATCH",
                "Exact-file fingerprint does not match the workbook hash",
                "Source Workbook", str(workbook_id),
            ))

    for worksheet_id, sheet_fingerprint in connection.execute(
        "SELECT worksheet_id, sheet_fingerprint FROM source_worksheet"
    ):
        if not str(sheet_fingerprint).strip():
            findings.append(_finding(
                "SOURCE_WORKSHEET_FINGERPRINT_MISSING",
                "Worksheet evidence has no declared fingerprint",
                "Source Worksheet", str(worksheet_id),
            ))

    for occurrence_id, logical_fingerprint, prior_id, prior_status, prior_fingerprint in connection.execute(
        """SELECT duplicate.occurrence_id, duplicate.logical_fingerprint,
                  duplicate.duplicate_of_occurrence_id, prior.terminal_status,
                  prior.logical_fingerprint
           FROM source_occurrence duplicate
           LEFT JOIN source_occurrence prior
             ON prior.occurrence_id = duplicate.duplicate_of_occurrence_id
           WHERE duplicate.terminal_status = 'Duplicate'"""
    ):
        if occurrence_id == prior_id or prior_status != "Committed":
            findings.append(_finding(
                "SOURCE_DUPLICATE_TARGET_INVALID",
                "Duplicate occurrence must reference a different committed occurrence",
                "Source Occurrence", str(occurrence_id),
            ))
        if logical_fingerprint is None or logical_fingerprint != prior_fingerprint:
            findings.append(_finding(
                "SOURCE_DUPLICATE_FINGERPRINT_MISMATCH",
                "Duplicate and referenced occurrence must share a logical fingerprint",
                "Source Occurrence", str(occurrence_id),
            ))

    for logical_fingerprint, committed_count in connection.execute(
        """SELECT logical_fingerprint, COUNT(*)
           FROM source_occurrence
           WHERE terminal_status = 'Committed' AND logical_fingerprint IS NOT NULL
           GROUP BY logical_fingerprint HAVING COUNT(*) > 1"""
    ):
        findings.append(_finding(
            "SOURCE_LOGICAL_DUPLICATE_COMMITTED",
            f"Logical fingerprint has {committed_count} committed occurrences",
            "Logical Fingerprint", str(logical_fingerprint),
        ))

    for datum_id, worksheet_id, cell, lexeme, formula, context_hash in connection.execute(
        """SELECT source_datum_id, worksheet_id, cell_or_range,
                  submitted_lexeme, formula_text, context_hash
           FROM source_datum WHERE context_hash IS NOT NULL"""
    ):
        expected = hashlib.sha256(
            f"{worksheet_id}|{cell}|{lexeme}|{formula}".encode("utf-8")
        ).hexdigest()
        if context_hash != expected:
            findings.append(_finding(
                "SOURCE_DATUM_CONTEXT_HASH_MISMATCH",
                "Source-cell context hash does not match its immutable evidence fields",
                "Source Datum", str(datum_id),
            ))
    return findings


def verify_event_summary_generations(connection: sqlite3.Connection) -> list[IntegrityFinding]:
    findings: list[IntegrityFinding] = []
    manifested = {str(row[0]) for row in connection.execute(
        "SELECT projection_generation_id FROM event_summary_generation_manifest"
    )}
    for generation_id in sorted({str(row[0]) for row in connection.execute(
        "SELECT DISTINCT projection_generation_id FROM event_summary_projection"
    )} - manifested):
        findings.append(_finding(
            "EVENT_SUMMARY_MANIFEST_MISSING",
            "Event-summary rows have no atomic generation manifest",
            "Event Summary Generation", generation_id,
        ))
    for generation_id, cutoff, stored_hash, stored_count in connection.execute(
        """SELECT projection_generation_id, evidence_cutoff_utc,
                  build_manifest_hash, projected_row_count
           FROM event_summary_generation_manifest"""
    ):
        try:
            expected_rows = event_summary_source_rows(connection, str(cutoff))
        except ValueError as error:
            findings.append(_finding(
                "EVENT_SUMMARY_REBUILD_FAILED", str(error),
                "Event Summary Generation", str(generation_id),
            ))
            continue
        expected_hash = hashlib.sha256(
            canonical_json([tuple(row) for row in expected_rows]).encode("utf-8")
        ).hexdigest()
        actual_rows = connection.execute(
            """SELECT event_id, source_package_number, event_name, buyer_code_id,
                      event_status, supplier_count, open_action_count,
                      latest_activity_at_utc
               FROM event_summary_projection
               WHERE projection_generation_id = ? ORDER BY event_id""",
            (generation_id,),
        ).fetchall()
        if (stored_hash != expected_hash or int(stored_count) != len(expected_rows)
                or [tuple(row) for row in actual_rows] != [tuple(row) for row in expected_rows]):
            findings.append(_finding(
                "EVENT_SUMMARY_GENERATION_MISMATCH",
                "Stored event-summary generation differs from its cutoff-correct rebuild",
                "Event Summary Generation", str(generation_id),
            ))
    return findings


def verify_search_generations(connection: sqlite3.Connection) -> list[IntegrityFinding]:
    findings: list[IntegrityFinding] = []
    for generation_id, cutoff, stored_hash, stored_count in connection.execute(
        """SELECT search_generation_id, evidence_cutoff_utc,
                  build_manifest_hash, document_count
           FROM search_generation_manifest"""
    ):
        expected = search_source_rows(connection, str(cutoff))
        expected_hash = hashlib.sha256(canonical_json(expected).encode("utf-8")).hexdigest()
        actual = [tuple(row) for row in connection.execute(
            """SELECT search_document_id, entity_type, entity_id, title, subtitle,
                      searchable_text, commodity_id, event_id, supplier_id,
                      supplier_plant_id, part_id, status
               FROM search_document_projection WHERE search_generation_id = ?
               ORDER BY search_document_id""",
            (generation_id,),
        )]
        fts_rows = [tuple(row) for row in connection.execute(
            """SELECT search_document_id, searchable_text FROM search_document_fts
               WHERE search_generation_id = ? ORDER BY search_document_id""",
            (generation_id,),
        )]
        expected_fts = [(row[0], row[5]) for row in expected]
        if (stored_hash != expected_hash or int(stored_count) != len(expected)
                or actual != expected or fts_rows != expected_fts):
            findings.append(_finding(
                "SEARCH_GENERATION_MISMATCH",
                "Search projection or FTS cache differs from its authoritative cutoff rebuild",
                "Search Generation", str(generation_id),
            ))
    return findings


def verify_part_history_generations(connection: sqlite3.Connection) -> list[IntegrityFinding]:
    findings: list[IntegrityFinding] = []
    for generation_id, anchor_part_id, cutoff, stored_hash, stored_count in connection.execute(
        """SELECT part_history_generation_id, anchor_part_id, evidence_cutoff_utc,
                  build_manifest_hash, projected_row_count
           FROM part_history_generation_manifest"""
    ):
        try:
            expected = part_history_source_rows(connection, str(anchor_part_id), str(cutoff))
        except ValueError as error:
            findings.append(_finding("PART_HISTORY_REBUILD_FAILED", str(error),
                                     "Part History Generation", str(generation_id)))
            continue
        expected_hash = hashlib.sha256(canonical_json(expected).encode("utf-8")).hexdigest()
        actual = [tuple(row) for row in connection.execute(
            """SELECT part_history_row_id, anchor_part_id, evidence_part_id,
                      observation_id, submitted_datum_id, evidence_class,
                      comparison_field, comparison_eligibility, functional_family_id,
                      family_version_id, relationship_type, business_rationale,
                      supplier_id, supplier_plant_id, event_id, economic_date,
                      decimal_coefficient, decimal_scale, currency_id, normalized_unit_id
               FROM part_history_projection WHERE part_history_generation_id = ?
               ORDER BY part_history_row_id""", (generation_id,),
        )]
        if (stored_hash != expected_hash or int(stored_count) != len(expected)
                or actual != expected):
            findings.append(_finding(
                "PART_HISTORY_GENERATION_MISMATCH",
                "Part-history projection differs from its exact cutoff rebuild",
                "Part History Generation", str(generation_id),
            ))
    return findings


def verify_recovery_evidence(connection: sqlite3.Connection) -> list[IntegrityFinding]:
    findings: list[IntegrityFinding] = []
    for publication_id, checkpoint_id in connection.execute(
        """SELECT publication.publication_id, publication.local_checkpoint_id
           FROM publication WHERE NOT EXISTS (
               SELECT 1 FROM restore_event restore
               WHERE restore.local_checkpoint_id = publication.local_checkpoint_id
                 AND restore.restore_type = 'Automated Test'
                 AND restore.restore_status = 'Verified')"""
    ):
        findings.append(_finding(
            "PUBLICATION_WITHOUT_VERIFIED_RESTORE",
            "Publication checkpoint has no successful automated restore test",
            "Publication", str(publication_id),
        ))
    for restore_id in connection.execute(
        """SELECT restore_event_id FROM restore_event
           WHERE restore_status = 'Verified' AND (
               integrity_result <> 'ok' OR audit_chain_result <> 'ok'
               OR schema_result <> 'ok' OR reproduction_result <> 'ok')"""
    ):
        findings.append(_finding(
            "RESTORE_VERIFICATION_RESULT_MISMATCH",
            "Verified restore does not have successful component results",
            "Restore Event", str(restore_id[0]),
        ))
    return findings


def verify_sourcing_event_status_history(connection: sqlite3.Connection) -> list[IntegrityFinding]:
    findings: list[IntegrityFinding] = []
    allowed = {"Setup": "Active", "Active": "Finalized", "Finalized": "Closed"}
    for event_id, initial_status in connection.execute(
        "SELECT event_id, event_status FROM sourcing_event"
    ):
        rows = connection.execute(
            """SELECT sourcing_event_status_event_id, event_status,
                      supersedes_status_event_id, recorded_at_utc
               FROM sourcing_event_status_event WHERE event_id = ?
               ORDER BY recorded_at_utc, sourcing_event_status_event_id""",
            (event_id,),
        ).fetchall()
        if not rows:
            findings.append(_finding("EVENT_STATUS_HISTORY_MISSING",
                                     "Sourcing event has no append-only status history",
                                     "Sourcing Event", str(event_id)))
            continue
        if rows[0][1] != initial_status or rows[0][2] is not None:
            findings.append(_finding("EVENT_INITIAL_STATUS_MISMATCH",
                                     "Initial status event does not match immutable event setup",
                                     "Sourcing Event", str(event_id)))
        for prior, current in zip(rows, rows[1:]):
            if current[2] != prior[0] or allowed.get(str(prior[1])) != current[1]:
                findings.append(_finding("EVENT_STATUS_TRANSITION_INVALID",
                                         f"Invalid or disconnected transition {prior[1]} -> {current[1]}",
                                         "Sourcing Event", str(event_id)))
        if rows[-1][1] in {"Finalized", "Closed"} and connection.execute(
            """SELECT 1 FROM finalized_snapshot snapshot JOIN analysis analysis
                 ON analysis.analysis_id = snapshot.analysis_id
               WHERE analysis.event_id = ? LIMIT 1""", (event_id,),
        ).fetchone() is None:
            findings.append(_finding("FINALIZED_EVENT_SNAPSHOT_MISSING",
                                     "Finalized/closed event has no finalized analysis snapshot",
                                     "Sourcing Event", str(event_id)))
    return findings


def verify_analysis_run_status_history(connection: sqlite3.Connection) -> list[IntegrityFinding]:
    findings: list[IntegrityFinding] = []
    analysis_allowed = {
        "Working": {"Calculating"}, "Draft Revision Complete": {"Calculating", "Finalizing"},
        "Calculating": {"Draft Revision Complete", "Failed", "Cancelled"},
        "Finalizing": {"Finalized"}, "Finalized": set(), "Failed": set(), "Cancelled": set(),
    }
    for analysis_id in (str(row[0]) for row in connection.execute("SELECT analysis_id FROM analysis")):
        rows = connection.execute(
            """SELECT analysis_status_event_id, analysis_status,
                      supersedes_status_event_id, recorded_at_utc
               FROM analysis_status_event WHERE analysis_id = ?
               ORDER BY recorded_at_utc, analysis_status_event_id""", (analysis_id,),
        ).fetchall()
        if not rows:
            findings.append(_finding("ANALYSIS_STATUS_HISTORY_MISSING",
                                     "Analysis has no lifecycle history", "Analysis", analysis_id))
            continue
        by_id = {str(row[0]): row for row in rows}
        roots = [row for row in rows if row[2] is None]
        tips = [row for row in rows if not any(child[2] == row[0] for child in rows)]
        if len(roots) != 1 or len(tips) != 1:
            findings.append(_finding("ANALYSIS_STATUS_CHAIN_INVALID",
                                     "Analysis lifecycle must have exactly one root and tip",
                                     "Analysis", analysis_id))
        for row in rows:
            if row[2] is None:
                continue
            prior = by_id.get(str(row[2]))
            if prior is None or row[1] not in analysis_allowed.get(str(prior[1]), set()):
                findings.append(_finding("ANALYSIS_STATUS_TRANSITION_INVALID",
                                         "Analysis status transition is invalid or disconnected",
                                         "Analysis", analysis_id))
        if connection.execute(
            "SELECT 1 FROM finalized_snapshot WHERE analysis_id = ?", (analysis_id,),
        ).fetchone() and (len(tips) != 1 or tips[0][1] != "Finalized"):
            findings.append(_finding("FINALIZED_ANALYSIS_STATUS_MISMATCH",
                                     "Finalized snapshot requires Finalized analysis status",
                                     "Analysis", analysis_id))

    run_allowed = {"Started": {"Completed", "Failed", "Cancelled"},
                   "Completed": set(), "Failed": set(), "Cancelled": set()}
    for run_id, core_status, core_output, input_hash, scenario_id in connection.execute(
        """SELECT calculation_run_id, status, output_manifest_hash,
                  input_manifest_hash, scenario_revision_id FROM calculation_run"""
    ):
        rows = connection.execute(
            """SELECT calculation_run_status_event_id, run_status,
                      supersedes_status_event_id, output_manifest_hash
               FROM calculation_run_status_event WHERE calculation_run_id = ?""", (run_id,),
        ).fetchall()
        if not rows:
            findings.append(_finding("CALCULATION_RUN_STATUS_HISTORY_MISSING",
                                     "Calculation run has no lifecycle history",
                                     "Calculation Run", str(run_id)))
            continue
        by_id = {str(row[0]): row for row in rows}
        roots = [row for row in rows if row[2] is None]
        tips = [row for row in rows if not any(child[2] == row[0] for child in rows)]
        if len(roots) != 1 or len(tips) != 1 or roots[0][1] != core_status:
            findings.append(_finding("CALCULATION_RUN_STATUS_CHAIN_INVALID",
                                     "Run lifecycle must have one root/tip matching immutable initial status",
                                     "Calculation Run", str(run_id)))
        for row in rows:
            if row[2] is not None:
                prior = by_id.get(str(row[2]))
                if prior is None or row[1] not in run_allowed.get(str(prior[1]), set()):
                    findings.append(_finding("CALCULATION_RUN_STATUS_TRANSITION_INVALID",
                                             "Run status transition is invalid or disconnected",
                                             "Calculation Run", str(run_id)))
        if len(tips) == 1:
            result_count = int(connection.execute(
                "SELECT COUNT(*) FROM calculation_result WHERE calculation_run_id = ?", (run_id,),
            ).fetchone()[0])
            if tips[0][1] == "Completed" and (not tips[0][3] or not result_count):
                findings.append(_finding("COMPLETED_RUN_OUTPUT_MISSING",
                                         "Completed run requires output hash and results",
                                         "Calculation Run", str(run_id)))
            if tips[0][1] == "Completed":
                manifest_rows = connection.execute(
                    """SELECT evidence_entity_type, evidence_entity_id, included_flag,
                              analytical_role, exclusion_reason
                       FROM evidence_manifest_entry WHERE scenario_revision_id = ?
                       ORDER BY evidence_entity_type, evidence_entity_id, analytical_role""",
                    (scenario_id,),
                ).fetchall()
                expected_input_hash = hashlib.sha256(
                    canonical_json([tuple(row) for row in manifest_rows]).encode("utf-8")
                ).hexdigest()
                if expected_input_hash != input_hash:
                    findings.append(_finding(
                        "CALCULATION_INPUT_MANIFEST_MISMATCH",
                        "Completed run input hash does not reproduce from its scenario manifest",
                        "Calculation Run", str(run_id),
                    ))
                missing_lineage = int(connection.execute(
                    """SELECT COUNT(*) FROM calculation_result result
                       WHERE result.calculation_run_id = ? AND NOT EXISTS (
                           SELECT 1 FROM calculation_lineage lineage
                           WHERE lineage.calculation_result_id = result.calculation_result_id
                       )""", (run_id,),
                ).fetchone()[0])
                if missing_lineage:
                    findings.append(_finding(
                        "COMPLETED_RESULT_MISSING_LINEAGE",
                        f"{missing_lineage} completed calculation results have no lineage",
                        "Calculation Run", str(run_id),
                    ))
                outside_manifest = int(connection.execute(
                    """SELECT COUNT(*) FROM calculation_lineage lineage
                       JOIN calculation_result result
                         ON result.calculation_result_id = lineage.calculation_result_id
                       WHERE result.calculation_run_id = ? AND NOT EXISTS (
                           SELECT 1 FROM evidence_manifest_entry manifest
                           WHERE manifest.scenario_revision_id = ?
                             AND manifest.included_flag = 1
                             AND manifest.evidence_entity_type = lineage.source_entity_type
                             AND manifest.evidence_entity_id = lineage.source_entity_id
                       )""", (run_id, scenario_id),
                ).fetchone()[0])
                if outside_manifest:
                    findings.append(_finding(
                        "CALCULATION_LINEAGE_OUTSIDE_MANIFEST",
                        f"{outside_manifest} lineage rows use evidence not included by the scenario",
                        "Calculation Run", str(run_id),
                    ))
                ordered_results = connection.execute(
                    """SELECT result.result_code, result.decimal_coefficient,
                              result.decimal_scale, result.supplier_id,
                              result.part_id, result.program_year,
                              result.category_code, result.normalized_unit_id,
                              result.currency_id, ordering.output_ordinal
                       FROM calculation_result result
                       LEFT JOIN calculation_output_entry_order ordering
                         ON ordering.calculation_result_id = result.calculation_result_id
                        AND ordering.calculation_run_id = result.calculation_run_id
                       WHERE result.calculation_run_id = ?
                       ORDER BY ordering.output_ordinal""",
                    (run_id,),
                ).fetchall()
                ordinals = [row[9] for row in ordered_results]
                if ordinals != list(range(result_count)):
                    findings.append(_finding(
                        "CALCULATION_OUTPUT_ORDER_INVALID",
                        "Completed results require one contiguous immutable output ordinal",
                        "Calculation Run", str(run_id),
                    ))
                else:
                    output_payload = [
                        {
                            "code": row[0], "coefficient": row[1], "scale": row[2],
                            "supplier": row[3], "part": row[4], "year": row[5],
                            "category": row[6], "unit": row[7], "currency": row[8],
                        }
                        for row in ordered_results
                    ]
                    expected_output_hash = hashlib.sha256(
                        canonical_json(output_payload).encode("utf-8")
                    ).hexdigest()
                    if expected_output_hash != core_output or expected_output_hash != tips[0][3]:
                        findings.append(_finding(
                            "CALCULATION_OUTPUT_MANIFEST_MISMATCH",
                            "Completed run output hash does not reproduce from ordered results",
                            "Calculation Run", str(run_id),
                        ))
            if tips[0][1] in {"Failed", "Cancelled"} and result_count:
                findings.append(_finding("NON_GOVERNING_RUN_HAS_RESULTS",
                                         "Failed/cancelled run must not retain governing results",
                                         "Calculation Run", str(run_id)))
    return findings


def verify_import_state_history(connection: sqlite3.Connection) -> list[IntegrityFinding]:
    findings: list[IntegrityFinding] = []
    session_transitions = {
        "Created": {"Discovering", "Cancelling", "Failed"},
        "Discovering": {"Extracting", "Cancelling", "Failed"},
        "Extracting": {"Awaiting Confirmation", "Cancelling", "Failed"},
        "Awaiting Confirmation": {"Staging", "Cancelling", "Failed"},
        "Staging": {"Committing", "Cancelling", "Failed"},
        "Committing": {"Reconciling", "Cancelling", "Failed"},
        "Reconciling": {"Completed", "Cancelling", "Failed"},
        "Cancelling": {"Cancelled", "Failed"}, "Completed": set(),
        "Cancelled": set(), "Failed": set(),
    }
    transaction_transitions = {
        "Started": {"Committed", "Rolled Back", "Cancelled", "Failed"},
        "Committed": set(), "Rolled Back": set(), "Cancelled": set(), "Failed": set(),
    }
    checks = (
        ("import_session", "import_session_id", "status", "import_session_status_event",
         "import_session_id", "import_session_status_event_id", "session_status", "Import Session",
         session_transitions),
        ("import_transaction", "import_transaction_id", "status", "import_transaction_status_event",
         "import_transaction_id", "import_transaction_status_event_id", "transaction_status", "Import Transaction",
         transaction_transitions),
        ("source_occurrence", "occurrence_id", "terminal_status", "source_occurrence_status_event",
         "occurrence_id", "source_occurrence_status_event_id", "occurrence_status", "Source Occurrence",
         {"Pending": {"Committed", "Blocked", "Ignored", "Failed"}, "Committed": set(),
          "Duplicate": set(), "Blocked": {"Pending"}, "Ignored": set(), "Failed": set()}),
        ("staged_observation", "staged_observation_id", "status", "staged_observation_status_event",
         "staged_observation_id", "staged_observation_status_event_id", "staged_status", "Staged Observation",
         {"Extracted": {"Needs Review", "Ready to Commit", "Blocked", "Failed"},
          "Needs Review": {"Needs Review", "Ready to Commit", "Blocked", "Ignored", "Failed"},
          "Ready to Commit": {"Ready to Commit", "Committed", "Blocked", "Failed"},
          "Blocked": {"Needs Review", "Ready to Commit", "Ignored", "Failed"},
          "Committed": set(), "Duplicate": set(), "Ignored": set(), "Failed": set()}),
    )
    for core_table, core_id, core_status, event_table, event_entity, event_id, event_status, entity_type, allowed in checks:
        for entity_id, stored_status in connection.execute(
            f"SELECT {core_id}, {core_status} FROM {core_table}"
        ):
            rows = connection.execute(
                f"SELECT {event_id}, {event_status}, supersedes_status_event_id "
                f"FROM {event_table} WHERE {event_entity} = ?", (entity_id,),
            ).fetchall()
            if not rows:
                findings.append(_finding("IMPORT_STATE_HISTORY_MISSING",
                                         f"{entity_type} has no status history",
                                         entity_type, str(entity_id)))
                continue
            by_id = {str(row[0]): row for row in rows}
            roots = [row for row in rows if row[2] is None]
            tips = [row for row in rows if not any(child[2] == row[0] for child in rows)]
            if len(roots) != 1 or len(tips) != 1 or tips[0][1] != stored_status:
                findings.append(_finding("IMPORT_STATE_CHAIN_INVALID",
                                         f"{entity_type} status chain must have one root/tip matching current state",
                                         entity_type, str(entity_id)))
            for row in rows:
                if row[2] is not None:
                    prior = by_id.get(str(row[2]))
                    if prior is None or row[1] not in allowed.get(str(prior[1]), set()):
                        findings.append(_finding("IMPORT_STATE_TRANSITION_INVALID",
                                                 f"Invalid or disconnected {entity_type} transition",
                                                 entity_type, str(entity_id)))
    return findings


def verify_staging_resolutions(connection: sqlite3.Connection) -> list[IntegrityFinding]:
    findings: list[IntegrityFinding] = []
    for issue_id, in connection.execute("SELECT staging_issue_id FROM staging_issue"):
        rows = connection.execute(
            """SELECT staging_resolution_id, supersedes_staging_resolution_id
               FROM staging_resolution WHERE staging_issue_id = ?""",
            (issue_id,),
        ).fetchall()
        if not rows:
            continue
        identifiers = {str(row[0]) for row in rows}
        roots = [row for row in rows if row[1] is None]
        tips = [row for row in rows if not any(child[1] == row[0] for child in rows)]
        disconnected = any(
            row[1] is not None and str(row[1]) not in identifiers for row in rows
        )
        if len(roots) != 1 or len(tips) != 1 or disconnected:
            findings.append(_finding(
                "STAGING_RESOLUTION_CHAIN_INVALID",
                "Issue resolutions must form one connected append-only chain",
                "Staging Issue", str(issue_id),
            ))
    for staged_id, stored_count, status, unresolved_count in connection.execute(
        """SELECT staged.staged_observation_id, staged.blocking_issue_count,
                  staged.status,
                  (SELECT COUNT(*) FROM staging_issue issue
                   WHERE issue.staged_observation_id = staged.staged_observation_id
                     AND issue.blocking_flag = 1
                     AND NOT EXISTS (
                         SELECT 1 FROM v_current_staging_resolution resolution
                         WHERE resolution.staging_issue_id = issue.staging_issue_id
                     ))
           FROM staged_observation staged"""
    ):
        if stored_count != unresolved_count:
            findings.append(_finding(
                "STAGING_BLOCKER_COUNT_MISMATCH",
                "Stored blocking count does not match unresolved immutable issues",
                "Staged Observation", str(staged_id),
            ))
        if status in {"Ready to Commit", "Committed"} and unresolved_count != 0:
            findings.append(_finding(
                "STAGING_READY_WITH_BLOCKERS",
                "Ready or committed staging evidence has unresolved blockers",
                "Staged Observation", str(staged_id),
            ))
        if status == "Blocked" and unresolved_count == 0:
            findings.append(_finding(
                "STAGING_BLOCKED_WITHOUT_BLOCKERS",
                "Blocked staging evidence has no unresolved blocker",
                "Staged Observation", str(staged_id),
            ))
    return findings


def verify_pce_evidence_boundaries(connection: sqlite3.Connection) -> list[IntegrityFinding]:
    findings: list[IntegrityFinding] = []
    for membership_id, in connection.execute(
        """SELECT membership.round_observation_id
           FROM round_observation membership
           JOIN pbd_observation observation
             ON observation.observation_id = membership.observation_id
           JOIN supplier_quote_round round_row
             ON round_row.quote_round_id = membership.quote_round_id
           JOIN round_batch batch ON batch.round_batch_id = membership.round_batch_id
           JOIN event_part event_scope ON event_scope.event_part_id = membership.event_part_id
           JOIN scope_version scope ON scope.scope_version_id = event_scope.scope_version_id
           JOIN source_package package ON package.source_package_id = scope.source_package_id
           WHERE observation.observation_context <> 'Sourcing Event'
              OR observation.context_id <> round_row.event_id
              OR observation.supplier_id <> round_row.supplier_id
              OR observation.part_id <> event_scope.part_id
              OR package.event_id <> round_row.event_id
              OR batch.quote_round_id <> round_row.quote_round_id"""
    ):
        findings.append(_finding(
            "ROUND_OBSERVATION_CONTEXT_MISMATCH",
            "Round membership violates sourcing-event, supplier, part, or batch lineage",
            "Round Observation", str(membership_id),
        ))
    evidence_queries = {
        "PBD Observation": "SELECT observation_context FROM pbd_observation WHERE observation_id = ?",
        "Round Observation": """SELECT observation.observation_context
            FROM round_observation membership JOIN pbd_observation observation
              ON observation.observation_id = membership.observation_id
            WHERE membership.round_observation_id = ?""",
        "Submitted Datum": """SELECT observation.observation_context
            FROM submitted_datum datum JOIN pbd_observation observation
              ON observation.observation_id = datum.observation_id
            WHERE datum.submitted_datum_id = ?""",
    }
    for activity_id, before_type, before_id, after_type, after_id in connection.execute(
        """SELECT supplier_activity_id, before_entity_type, before_entity_id,
                  after_entity_type, after_entity_id FROM supplier_activity"""
    ):
        for entity_type, entity_id in ((before_type, before_id), (after_type, after_id)):
            query = evidence_queries.get(entity_type)
            row = None if query is None or entity_id is None else connection.execute(
                query, (entity_id,),
            ).fetchone()
            if row is not None and row[0] == "PCE Should Cost":
                findings.append(_finding(
                    "PCE_SUPPLIER_ACTIVITY_FORBIDDEN",
                    "PCE should-cost evidence cannot enter supplier behavior history",
                    "Supplier Activity", str(activity_id),
                ))
                break
    return findings


def verify_round_conflict_history(connection: sqlite3.Connection) -> list[IntegrityFinding]:
    findings: list[IntegrityFinding] = []
    for conflict_id, quote_round_id, event_part_id, prior_id, new_id in connection.execute(
        """SELECT round_conflict_id, quote_round_id, event_part_id,
                  prior_round_observation_id, new_round_observation_id
           FROM round_conflict"""
    ):
        memberships = connection.execute(
            """SELECT round_observation_id, quote_round_id, event_part_id
               FROM round_observation WHERE round_observation_id IN (?, ?)""",
            (prior_id, new_id),
        ).fetchall()
        if len(memberships) != 2 or any(
            row[1] != quote_round_id or row[2] != event_part_id for row in memberships
        ):
            findings.append(_finding(
                "ROUND_CONFLICT_LINEAGE_INVALID",
                "Both conflict records must belong to the declared round and event part",
                "Round Conflict", str(conflict_id),
            ))
        decisions = connection.execute(
            """SELECT round_conflict_decision_id,
                      supersedes_round_conflict_decision_id
               FROM round_conflict_decision WHERE round_conflict_id = ?""",
            (conflict_id,),
        ).fetchall()
        if decisions:
            identifiers = {str(row[0]) for row in decisions}
            roots = [row for row in decisions if row[1] is None]
            tips = [row for row in decisions
                    if not any(child[1] == row[0] for child in decisions)]
            disconnected = any(
                row[1] is not None and str(row[1]) not in identifiers for row in decisions
            )
            if len(roots) != 1 or len(tips) != 1 or disconnected:
                findings.append(_finding(
                    "ROUND_CONFLICT_DECISION_CHAIN_INVALID",
                    "Conflict decisions must form one connected append-only chain",
                    "Round Conflict", str(conflict_id),
                ))
    return findings


def verify_observation_commit_gate(connection: sqlite3.Connection) -> list[IntegrityFinding]:
    findings: list[IntegrityFinding] = []
    for observation_id, in connection.execute(
        """SELECT observation.observation_id
           FROM pbd_observation observation
           JOIN staged_observation staged
             ON staged.staged_observation_id = observation.staged_observation_id
           JOIN source_occurrence occurrence ON occurrence.occurrence_id = staged.occurrence_id
           JOIN source_worksheet worksheet ON worksheet.worksheet_id = occurrence.worksheet_id
           JOIN source_workbook workbook ON workbook.workbook_id = worksheet.workbook_id
           JOIN import_transaction transaction_row
             ON transaction_row.import_transaction_id = workbook.import_transaction_id
           JOIN import_session session ON session.import_session_id = transaction_row.import_session_id
           WHERE observation.part_id IS NULL
              OR trim(observation.submitted_supplier_name) = ''
              OR trim(observation.submitted_part_number) = ''
              OR observation.submitted_part_description IS NULL
              OR trim(observation.submitted_part_description) = ''
              OR staged.occurrence_id <> observation.occurrence_id
              OR staged.import_context_type <> observation.observation_context
              OR staged.import_context_id IS NOT observation.context_id
              OR session.import_context_type <> observation.observation_context
              OR session.context_id IS NOT observation.context_id
              OR (observation.observation_context = 'Historical Baseline' AND
                  (observation.economic_date IS NULL OR
                   observation.economic_date_precision = 'Unknown'))
              OR NOT EXISTS (
                  SELECT 1 FROM submitted_datum price
                  WHERE price.observation_id = observation.observation_id
                    AND price.field_code = 'PIECE_PRICE'
                    AND price.decimal_coefficient IS NOT NULL
                    AND price.precision_status = 'Eligible'
                    AND price.normalized_unit_id IS NOT NULL
                    AND price.currency_id IS NOT NULL
              )"""
    ):
        findings.append(_finding(
            "OBSERVATION_COMMIT_GATE_INVALID",
            "Committed observation fails identity, context, date, or piece-price requirements",
            "PBD Observation", str(observation_id),
        ))
    for datum_id, in connection.execute(
        """SELECT submitted_datum_id FROM submitted_datum
           WHERE ((decimal_coefficient IS NOT NULL) + (text_value IS NOT NULL) +
                  (date_value IS NOT NULL) + (boolean_value IS NOT NULL)) <> 1
              OR (precision_status = 'Eligible' AND decimal_coefficient IS NOT NULL
                  AND normalized_unit_id IS NULL)
              OR (field_code = 'PIECE_PRICE' AND
                  (decimal_coefficient IS NULL OR precision_status <> 'Eligible'
                   OR normalized_unit_id IS NULL OR currency_id IS NULL))"""
    ):
        findings.append(_finding(
            "SUBMITTED_DATUM_TYPE_INVALID",
            "Submitted datum fails typed-value or analytical eligibility requirements",
            "Submitted Datum", str(datum_id),
        ))
    return findings


def verify_source_availability_history(connection: sqlite3.Connection) -> list[IntegrityFinding]:
    findings: list[IntegrityFinding] = []
    for workbook_id, expected_hash in connection.execute(
        "SELECT workbook_id, file_hash_sha256 FROM source_workbook"
    ):
        rows = connection.execute(
            """SELECT source_workbook_availability_event_id, availability_status,
                      observed_file_hash_sha256, supersedes_availability_event_id
               FROM source_workbook_availability_event WHERE workbook_id = ?""",
            (workbook_id,),
        ).fetchall()
        identifiers = {str(row[0]) for row in rows}
        roots = [row for row in rows if row[3] is None]
        tips = [row for row in rows if not any(child[3] == row[0] for child in rows)]
        disconnected = any(row[3] is not None and str(row[3]) not in identifiers for row in rows)
        if len(roots) != 1 or len(tips) != 1 or disconnected:
            findings.append(_finding(
                "SOURCE_AVAILABILITY_CHAIN_INVALID",
                "Workbook availability must have one connected root and current tip",
                "Source Workbook", str(workbook_id),
            ))
        for row in rows:
            if row[1] == "Available Verified" and row[2] != expected_hash:
                findings.append(_finding(
                    "SOURCE_REVERIFICATION_HASH_MISMATCH",
                    "Verified-available source does not match its immutable import hash",
                    "Source Workbook", str(workbook_id),
                ))
            if row[1] == "Hash Mismatch" and row[2] == expected_hash:
                findings.append(_finding(
                    "SOURCE_MISMATCH_STATUS_INVALID",
                    "Hash-mismatch status records the original matching hash",
                    "Source Workbook", str(workbook_id),
                ))
    return findings


def verify_source_package_mismatches(connection: sqlite3.Connection) -> list[IntegrityFinding]:
    findings: list[IntegrityFinding] = []
    for mismatch in connection.execute(
        """SELECT mismatch.*, package.event_id, datum.submitted_lexeme
           FROM source_package_mismatch mismatch
           JOIN source_package package ON package.source_package_id = mismatch.source_package_id
           JOIN source_datum datum ON datum.source_datum_id = mismatch.source_datum_id"""
    ):
        mismatch_id = str(mismatch["source_package_mismatch_id"])
        normalized_lexeme = "".join(str(mismatch["submitted_lexeme"] or "").strip().upper().split())
        if (mismatch["submitted_normalized_package_number"] ==
                mismatch["event_normalized_package_number"] or
                normalized_lexeme != mismatch["submitted_normalized_package_number"]):
            findings.append(_finding(
                "SOURCE_PACKAGE_MISMATCH_EVIDENCE_INVALID",
                "Mismatch must preserve two different numbers and agree with its source cell",
                "Source Package Mismatch", mismatch_id,
            ))
        actions = connection.execute(
            """SELECT action.buyer_action_id, action.event_id, current.action_status
               FROM buyer_action action LEFT JOIN v_current_buyer_action current
                 ON current.buyer_action_id = action.buyer_action_id
               WHERE action.governing_entity_type = 'Source Package Mismatch'
                 AND action.governing_entity_id = ?""", (mismatch_id,),
        ).fetchall()
        if len(actions) != 1 or actions[0][1] != mismatch["event_id"] or actions[0][2] is None:
            findings.append(_finding(
                "SOURCE_PACKAGE_MISMATCH_ACTION_INVALID",
                "Each mismatch requires exactly one current buyer action in its event",
                "Source Package Mismatch", mismatch_id,
            ))
        resolutions = connection.execute(
            """SELECT source_package_mismatch_resolution_id, decision_code,
                      supersedes_resolution_id
               FROM source_package_mismatch_resolution
               WHERE source_package_mismatch_id = ?""", (mismatch_id,),
        ).fetchall()
        identifiers = {str(row[0]) for row in resolutions}
        roots = [row for row in resolutions if row[2] is None]
        tips = [row for row in resolutions
                if not any(child[2] == row[0] for child in resolutions)]
        disconnected = any(row[2] is not None and str(row[2]) not in identifiers
                           for row in resolutions)
        if resolutions and (len(roots) != 1 or len(tips) != 1 or disconnected):
            findings.append(_finding(
                "SOURCE_PACKAGE_MISMATCH_DECISION_CHAIN_INVALID",
                "Mismatch decisions must form one connected append-only chain",
                "Source Package Mismatch", mismatch_id,
            ))
        if len(tips) == 1 and len(actions) == 1:
            decision, action_status = str(tips[0][1]), str(actions[0][2])
            expected_status = ("Accepted Exception" if decision == "Supplier Correction Required"
                               else "Resolved")
            effective_number = connection.execute(
                """SELECT normalized_package_number FROM v_current_source_package_identity
                   WHERE source_package_id = ?""", (mismatch["source_package_id"],),
            ).fetchone()[0]
            if action_status != expected_status or (decision == "Event Number Corrected" and
                    effective_number != mismatch["submitted_normalized_package_number"]):
                findings.append(_finding(
                    "SOURCE_PACKAGE_MISMATCH_RESOLUTION_INVALID",
                    "Current mismatch decision disagrees with action state or effective package identity",
                    "Source Package Mismatch", mismatch_id,
                ))
    return findings


def verify_supplier_identity_confirmations(connection: sqlite3.Connection) -> list[IntegrityFinding]:
    findings: list[IntegrityFinding] = []
    for observation_id, original_supplier_id, submitted_name, provisional_code in connection.execute(
        """SELECT observation.observation_id, observation.supplier_id,
                  observation.submitted_supplier_name, staged.provisional_supplier_code
           FROM pbd_observation observation JOIN staged_observation staged
             ON staged.staged_observation_id = observation.staged_observation_id"""
    ):
        confirmations = connection.execute(
            """SELECT supplier_identity_confirmation_id, confirmed_supplier_id,
                      confirmed_supplier_code, prior_supplier_id,
                      provisional_supplier_code, submitted_supplier_name,
                      supersedes_confirmation_id
               FROM observation_supplier_identity_confirmation
               WHERE observation_id = ?""", (observation_id,),
        ).fetchall()
        identifiers = {str(row[0]) for row in confirmations}
        roots = [row for row in confirmations if row[6] is None]
        tips = [row for row in confirmations
                if not any(child[6] == row[0] for child in confirmations)]
        disconnected = any(row[6] is not None and str(row[6]) not in identifiers
                           for row in confirmations)
        if confirmations and (len(roots) != 1 or len(tips) != 1 or disconnected):
            findings.append(_finding(
                "SUPPLIER_IDENTITY_CHAIN_INVALID",
                "Supplier identity confirmations must form one connected append-only chain",
                "PBD Observation", str(observation_id),
            ))
        for row in confirmations:
            if (original_supplier_id is not None or row[4] != provisional_code or
                    row[5] != submitted_name or row[2] != str(row[2]).strip().upper()):
                findings.append(_finding(
                    "SUPPLIER_IDENTITY_EVIDENCE_INVALID",
                    "Confirmation must preserve unresolved original identity, provisional code, and submitted name",
                    "PBD Observation", str(observation_id),
                ))
        context = connection.execute(
            "SELECT observation_context, context_id FROM pbd_observation WHERE observation_id = ?",
            (observation_id,),
        ).fetchone()
        if context[0] == "Sourcing Event" and original_supplier_id is None:
            actions = connection.execute(
                """SELECT current.action_status FROM buyer_action action
                   LEFT JOIN v_current_buyer_action current
                     ON current.buyer_action_id = action.buyer_action_id
                   WHERE governing_entity_type = 'PBD Observation'
                     AND governing_entity_id = ?
                     AND issue_type = 'Supplier Code Confirmation Required'""",
                (observation_id,),
            ).fetchall()
            action_invalid = len(actions) != 1 or actions[0][0] is None
            if confirmations and not action_invalid:
                action_invalid = actions[0][0] != "Resolved"
            if action_invalid:
                findings.append(_finding(
                    "SUPPLIER_IDENTITY_ACTION_MISSING",
                    "Each sourcing-event identity requires one action that resolves with confirmation",
                    "PBD Observation", str(observation_id),
                ))
    return findings


def verify_generated_outputs(connection: sqlite3.Connection) -> list[IntegrityFinding]:
    findings: list[IntegrityFinding] = []
    for row in connection.execute(
        """SELECT artifact.generated_output_artifact_id,
                  artifact.finalized_snapshot_id, artifact.output_contract_hash,
                  artifact.evidence_manifest_hash,
                  artifact.calculation_output_manifest_hash,
                  artifact.relative_filename,
                  (SELECT validation.validation_status
                   FROM generated_output_validation_event validation
                   WHERE validation.generated_output_artifact_id = artifact.generated_output_artifact_id
                   ORDER BY validation.validated_at_utc DESC,
                            validation.generated_output_validation_event_id DESC LIMIT 1)
           FROM generated_output_artifact artifact"""
    ):
        artifact_id, snapshot_id, contract_hash, evidence_hash, calculation_hash, filename, status = row
        required_prefix = required_output_filename_prefix(
            connection, finalized_snapshot_id=str(snapshot_id)
        )
        if not str(filename).startswith(required_prefix):
            findings.append(_finding(
                "GENERATED_OUTPUT_NAME_INVALID",
                f"Generated output filename must begin with {required_prefix}",
                "Generated Output", str(artifact_id),
            ))
        if status is None:
            findings.append(_finding(
                "GENERATED_OUTPUT_NOT_VALIDATED",
                "Generated output has no validation event",
                "Generated Output", str(artifact_id),
            ))
        elif status != "Verified":
            findings.append(_finding(
                "GENERATED_OUTPUT_VALIDATION_FAILED",
                "The latest generated-output validation failed",
                "Generated Output", str(artifact_id),
            ))
        try:
            contract = build_output_contract(
                connection, finalized_snapshot_id=str(snapshot_id)
            )
        except ValueError as error:
            findings.append(_finding(
                "GENERATED_OUTPUT_CONTRACT_UNAVAILABLE", str(error),
                "Generated Output", str(artifact_id),
            ))
            continue
        if (
            contract.contract_hash != contract_hash
            or contract.evidence_manifest_hash != evidence_hash
            or contract.calculation_output_manifest_hash != calculation_hash
        ):
            findings.append(_finding(
                "GENERATED_OUTPUT_CONTRACT_MISMATCH",
                "Generated output is not pinned to the current immutable snapshot contract",
                "Generated Output", str(artifact_id),
            ))
    return findings


def verify_projection_generations(connection: sqlite3.Connection) -> list[IntegrityFinding]:
    findings: list[IntegrityFinding] = []
    columns = {row[1] for row in connection.execute("PRAGMA table_info(projection_generation_manifest)")}
    age_column = ("economic_age_population_version" if "economic_age_population_version" in columns else "'Legacy'")
    manifested = {
        str(row[0]) for row in connection.execute(
            "SELECT projection_generation_id FROM projection_generation_manifest"
        )
    }
    orphaned = {
        str(row[0]) for row in connection.execute(
            "SELECT DISTINCT projection_generation_id FROM active_round_part_projection"
        )
    } - manifested
    for generation_id in sorted(orphaned):
        findings.append(_finding(
            "PROJECTION_GENERATION_MANIFEST_MISSING",
            "Active-round projection rows have no atomic generation manifest",
            "Projection Generation", generation_id,
        ))
    for row in connection.execute(
        f"""SELECT projection_generation_id, projection_type, event_id,
                  evidence_cutoff_utc, build_manifest_hash, projected_row_count, {age_column}
           FROM projection_generation_manifest"""
    ):
        generation_id, projection_type, event_id, cutoff, stored_hash, stored_count, age_version = row
        if projection_type != "Active Round Part":
            continue
        expected_rows = projection_source_rows(connection, str(event_id), str(cutoff),
                                               economic_age_population_version=str(age_version))
        expected_tuples = [tuple(item) for item in expected_rows]
        expected_hash = hashlib.sha256(
            canonical_json(expected_tuples).encode("utf-8")
        ).hexdigest()
        actual_rows = [tuple(item) for item in connection.execute(
            """SELECT event_id, supplier_id, event_part_id, quote_round_id,
                      observation_id, coverage_status, piece_price_coefficient,
                      piece_price_scale, currency_id, normalized_unit_id
               FROM active_round_part_projection
               WHERE projection_generation_id = ?
               ORDER BY event_part_id, supplier_id""", (generation_id,)
        )]
        metadata_mismatch = int(connection.execute(
            """SELECT COUNT(*) FROM active_round_part_projection
               WHERE projection_generation_id = ?
                 AND (event_id <> ? OR evidence_cutoff_utc <> ?
                      OR build_manifest_hash <> ?)""",
            (generation_id, event_id, cutoff, stored_hash),
        ).fetchone()[0])
        if (
            int(stored_count) != len(actual_rows)
            or actual_rows != expected_tuples
            or stored_hash != expected_hash
            or metadata_mismatch
        ):
            findings.append(_finding(
                "PROJECTION_GENERATION_MISMATCH",
                "Active-round projection does not independently reproduce from its pinned evidence cutoff",
                "Projection Generation", str(generation_id),
            ))
    return findings


def verify_supplier_profile_generations(connection: sqlite3.Connection) -> list[IntegrityFinding]:
    findings: list[IntegrityFinding] = []
    # Safe migration preflight also verifies databases predating migration 0049.
    columns = {row[1] for row in connection.execute(
        "PRAGMA table_info(supplier_profile_reproduction_manifest)"
    )}
    population_column = ("reproduction.formula_population_version"
                         if "formula_population_version" in columns else "'Legacy'")
    scope_column = ("reproduction.scope_population_version"
                    if "scope_population_version" in columns else "'Legacy'")
    for row in connection.execute(
        f"""SELECT run.supplier_profile_run_id, run.supplier_id,
                  run.supplier_plant_id, run.commodity_id,
                  run.evidence_cutoff_utc, run.build_manifest_hash,
                  reproduction.active_window_start_utc,
                  reproduction.metric_manifest_hash,
                  reproduction.finding_manifest_hash, {population_column},
                  run.region_code, {scope_column}
           FROM supplier_profile_run run
           LEFT JOIN supplier_profile_reproduction_manifest reproduction
             ON reproduction.supplier_profile_run_id = run.supplier_profile_run_id
           WHERE run.run_status = 'Complete'"""
    ):
        run_id = str(row[0])
        if row[6] is None:
            findings.append(_finding(
                "SUPPLIER_PROFILE_REPRODUCTION_MANIFEST_MISSING",
                "Completed supplier profile has no reproduction manifest",
                "Supplier Profile Run", run_id,
            ))
            continue
        activities = profile_activity_rows(
            connection, supplier_id=str(row[1]), supplier_plant_id=row[2],
            commodity_id=str(row[3]), evidence_cutoff_utc=str(row[4]),
            active_window_start_utc=str(row[6]),
            region_code=row[10], scope_population_version=str(row[11]),
        )
        formula_exceptions = profile_formula_exception_rows(
            connection, supplier_id=str(row[1]), supplier_plant_id=row[2],
            commodity_id=str(row[3]), evidence_cutoff_utc=str(row[4]),
            active_window_start_utc=str(row[6]),
            population_version=str(row[9]),
            region_code=row[10], scope_population_version=str(row[11]),
        )
        _, _, evidence_hash, expected_metric_hash, expected_finding_hash = (
            derive_profile_outputs(activities, formula_exceptions)
        )
        expected_activity_ids = [str(activity["supplier_activity_id"]) for activity in activities]
        stored_activity_ids = [str(item[0]) for item in connection.execute(
            """SELECT evidence_entity_id FROM profile_evidence_entry
               WHERE supplier_profile_run_id = ?
                 AND evidence_entity_type = 'Supplier Activity'
                 AND evidence_role = 'Profile Activity' AND included_flag = 1
               ORDER BY rowid""", (run_id,)
        )]
        expected_formula_ids = [str(item["formula_integrity_event_id"])
                                for item in formula_exceptions]
        stored_formula_ids = [str(item[0]) for item in connection.execute(
            """SELECT evidence_entity_id FROM profile_evidence_entry
               WHERE supplier_profile_run_id = ?
                 AND evidence_entity_type = 'Formula Integrity Event'
                 AND evidence_role = 'Repeated Formula Exception'
                 AND included_flag = 1 ORDER BY rowid""", (run_id,)
        )]
        stored_metric_payload = [tuple(item) for item in connection.execute(
            """SELECT metric_code, decimal_coefficient, decimal_scale,
                      evidence_count, independent_event_count,
                      confidence_classification
               FROM supplier_profile_metric WHERE supplier_profile_run_id = ?
               ORDER BY rowid""", (run_id,)
        )]
        stored_finding_payload = [tuple(item) for item in connection.execute(
            """SELECT finding_type, finding_status, explanation_payload,
                      evidence_count, independent_event_count
               FROM supplier_profile_finding WHERE supplier_profile_run_id = ?
               ORDER BY rowid""", (run_id,)
        )]
        stored_metric_hash = hashlib.sha256(
            canonical_json(stored_metric_payload).encode("utf-8")
        ).hexdigest()
        stored_finding_hash = hashlib.sha256(
            canonical_json(stored_finding_payload).encode("utf-8")
        ).hexdigest()
        if (
            row[5] != evidence_hash
            or expected_activity_ids != stored_activity_ids
            or expected_formula_ids != stored_formula_ids
            or row[7] != expected_metric_hash
            or row[8] != expected_finding_hash
            or stored_metric_hash != expected_metric_hash
            or stored_finding_hash != expected_finding_hash
        ):
            findings.append(_finding(
                "SUPPLIER_PROFILE_REPRODUCTION_MISMATCH",
                "Completed supplier profile does not reproduce from its pinned evidence population and active window",
                "Supplier Profile Run", run_id,
            ))
    return findings


def verify_supplier_rate_distributions(connection: sqlite3.Connection) -> list[IntegrityFinding]:
    from .supplier_rates import derive_supplier_rate_distributions
    findings = []
    if not connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'supplier_rate_distribution_run'"
    ).fetchone():
        return findings
    for row in connection.execute("SELECT * FROM supplier_rate_distribution_run"):
        payload = derive_supplier_rate_distributions(
            connection, supplier_id=row["supplier_id"], commodity_id=row["commodity_id"],
            region_code=row["region_code"], supplier_plant_id=row["supplier_plant_id"],
            evidence_cutoff_utc=row["evidence_cutoff_utc"],
        )
        serialized = canonical_json(payload)
        digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        if serialized != row["result_payload"] or digest != row["manifest_hash"]:
            findings.append(_finding(
                "SUPPLIER_RATE_DISTRIBUTION_MISMATCH",
                "Supplier rate distribution does not reproduce from its cutoff evidence",
                "Supplier Rate Distribution", row["distribution_run_id"],
            ))
    return findings


def verify_supplier_family_profile_generations(connection: sqlite3.Connection) -> list[IntegrityFinding]:
    findings: list[IntegrityFinding] = []
    for run in connection.execute("SELECT * FROM supplier_family_profile_run"):
        run_id = str(run["supplier_family_profile_run_id"])
        entity = connection.execute(
            """SELECT entity_payload FROM registry_entity_cache
               WHERE cache_generation_id = ? AND entity_type = 'Supplier Family'
                 AND entity_id = ?""",
            (run["registry_cache_generation_id"], run["stable_family_id"]),
        ).fetchone()
        mismatch = entity is None
        members = [tuple(row) for row in connection.execute(
            """SELECT supplier_id, supplier_profile_run_id
               FROM supplier_family_profile_member
               WHERE supplier_family_profile_run_id = ? ORDER BY supplier_id""", (run_id,),
        )]
        expected_members: list[str] = []
        if entity is not None:
            payload = json.loads(str(entity[0]))
            expected_members = sorted(str(item) for item in payload.get("member_supplier_ids", ()))
            mismatch = mismatch or str(payload.get("family_name", "")) != run["family_name"]
        mismatch = mismatch or [str(row[0]) for row in members] != expected_members
        for supplier_id, profile_id in members:
            profile = connection.execute(
                """SELECT profile.*, reproduction.active_window_start_utc
                   FROM supplier_profile_run profile
                   JOIN supplier_profile_reproduction_manifest reproduction
                     ON reproduction.supplier_profile_run_id = profile.supplier_profile_run_id
                   WHERE profile.supplier_profile_run_id = ?""", (profile_id,),
            ).fetchone()
            if profile is None or (
                profile["supplier_id"] != supplier_id or profile["supplier_plant_id"] is not None
                or profile["commodity_id"] != run["commodity_id"]
                or profile["region_code"] is not None
                or profile["evidence_cutoff_utc"] != run["evidence_cutoff_utc"]
                or profile["active_window_start_utc"] != run["active_window_start_utc"]
                or profile["eligibility_rule_version_id"] != run["eligibility_rule_version_id"]
                or profile["calculation_rule_version_id"] != run["calculation_rule_version_id"]
                or profile["engine_version_id"] != run["engine_version_id"]
            ):
                mismatch = True
        profile_ids = tuple(str(row[1]) for row in members)
        activities: list[sqlite3.Row] = []
        formula_exceptions: list[sqlite3.Row] = []
        if profile_ids:
            placeholders = ",".join("?" for _ in profile_ids)
            activities = connection.execute(
                f"""SELECT DISTINCT activity.supplier_activity_id, activity.activity_type,
                           activity.event_id, activity.quote_round_id, activity.event_part_id,
                           activity.occurred_at_utc, activity.recorded_at_utc
                    FROM profile_evidence_entry evidence JOIN supplier_activity activity
                      ON evidence.evidence_entity_type = 'Supplier Activity'
                     AND evidence.evidence_entity_id = activity.supplier_activity_id
                    WHERE evidence.supplier_profile_run_id IN ({placeholders})
                      AND evidence.included_flag = 1
                    ORDER BY activity.recorded_at_utc, activity.supplier_activity_id""",
                profile_ids,
            ).fetchall()
            formula_exceptions = connection.execute(
                f"""SELECT DISTINCT integrity.formula_integrity_event_id,
                           integrity.observation_id, round.event_id,
                           integrity.recorded_at_utc
                    FROM profile_evidence_entry evidence
                    JOIN formula_integrity_event integrity
                      ON evidence.evidence_entity_type = 'Formula Integrity Event'
                     AND evidence.evidence_entity_id = integrity.formula_integrity_event_id
                    JOIN round_observation membership
                      ON membership.observation_id = integrity.observation_id
                    JOIN supplier_quote_round round
                      ON round.quote_round_id = membership.quote_round_id
                    WHERE evidence.supplier_profile_run_id IN ({placeholders})
                      AND evidence.included_flag = 1
                    ORDER BY integrity.recorded_at_utc,
                             integrity.formula_integrity_event_id""",
                profile_ids,
            ).fetchall()
        metrics, family_findings, evidence_hash, metric_hash, finding_hash = derive_profile_outputs(
            activities, formula_exceptions
        )
        manifest = hashlib.sha256(canonical_json({
            "stable_family_id": run["stable_family_id"],
            "registry_cache_generation_id": run["registry_cache_generation_id"],
            "contributing_profiles": members,
            "activity_ids": [row["supplier_activity_id"] for row in activities],
            "formula_integrity_event_ids": [row["formula_integrity_event_id"]
                                            for row in formula_exceptions],
            "evidence_manifest_hash": evidence_hash,
        }).encode("utf-8")).hexdigest()
        stored_metrics = [tuple(row) for row in connection.execute(
            """SELECT metric_code, decimal_coefficient, decimal_scale, evidence_count,
                      independent_event_count, confidence_classification
               FROM supplier_family_profile_metric
               WHERE supplier_family_profile_run_id = ? ORDER BY rowid""", (run_id,),
        )]
        stored_findings = [tuple(row) for row in connection.execute(
            """SELECT finding_type, finding_status, explanation_payload, evidence_count,
                      independent_event_count FROM supplier_family_profile_finding
               WHERE supplier_family_profile_run_id = ? ORDER BY rowid""", (run_id,),
        )]
        expected_metrics = [(code, value.coefficient, value.scale, evidence_count,
                             event_count, confidence)
                            for code, value, evidence_count, event_count, confidence in metrics]
        expected_findings = [(kind, status, canonical_json(payload), evidence_count, event_count)
                             for kind, status, payload, evidence_count, event_count in family_findings]
        if (mismatch or manifest != run["build_manifest_hash"]
                or metric_hash != run["metric_manifest_hash"]
                or finding_hash != run["finding_manifest_hash"]
                or stored_metrics != expected_metrics or stored_findings != expected_findings):
            findings.append(_finding(
                "SUPPLIER_FAMILY_PROFILE_REPRODUCTION_MISMATCH",
                "Supplier-family profile does not reproduce from its signed family and contributing scoped profiles",
                "Supplier Family Profile Run", run_id,
            ))
    return findings


def verify_historical_baseline_summaries(connection: sqlite3.Connection) -> list[IntegrityFinding]:
    findings: list[IntegrityFinding] = []
    for row in connection.execute("SELECT * FROM historical_baseline_completion_summary"):
        summary_id = str(row["historical_baseline_completion_summary_id"])
        try:
            payload = historical_baseline_summary_payload(
                connection, import_transaction_id=str(row["import_transaction_id"]),
                first_analyzed_date=str(row["first_analyzed_date"]),
            )
            expected_hash = hashlib.sha256(
                canonical_json(payload).encode("utf-8")
            ).hexdigest()
            mismatch = (
                int(row["imported_record_count"]) != payload["imported_record_count"]
                or int(row["duplicate_count"]) != payload["duplicate_count"]
                or int(row["incomplete_pbd_count"]) != payload["incomplete_pbd_count"]
                or int(row["formula_exception_count"]) != payload["formula_exception_count"]
                or int(row["staging_exception_count"]) != payload["staging_exception_count"]
                or json.loads(str(row["missing_field_payload"])) !=
                   json.loads(canonical_json(payload["missing_fields_by_supplier"]))
                or row["summary_hash"] != expected_hash
            )
        except (ValueError, json.JSONDecodeError):
            mismatch = True
        if mismatch:
            findings.append(_finding(
                "HISTORICAL_BASELINE_SUMMARY_MISMATCH",
                "Historical baseline completion summary does not reproduce from immutable import evidence",
                "Historical Baseline Summary", summary_id,
            ))
    return findings


def verify_historical_baseline_comparisons(connection: sqlite3.Connection) -> list[IntegrityFinding]:
    findings: list[IntegrityFinding] = []
    scores = {"Detailed and Reconciled": 2, "Valid Aggregate": 2,
              "Formula Reconciliation Exception": 1, "Incomplete": 0}
    for row in connection.execute("SELECT * FROM historical_baseline_observation_comparison"):
        comparison_id = str(row["historical_baseline_observation_comparison_id"])
        summary = connection.execute(
            """SELECT import_transaction_id FROM historical_baseline_completion_summary
               WHERE historical_baseline_completion_summary_id = ?""",
            (row["historical_baseline_completion_summary_id"],),
        ).fetchone()
        later = connection.execute(
            "SELECT * FROM v_effective_pbd_observation WHERE observation_id = ?",
            (row["later_observation_id"],),
        ).fetchone()
        mismatch = summary is None or later is None
        baseline = None
        if not mismatch:
            baseline = connection.execute(
                """SELECT observation.* FROM v_effective_pbd_observation observation
                   JOIN source_occurrence occurrence ON occurrence.occurrence_id = observation.occurrence_id
                   JOIN source_worksheet worksheet ON worksheet.worksheet_id = occurrence.worksheet_id
                   JOIN source_workbook workbook ON workbook.workbook_id = worksheet.workbook_id
                   WHERE workbook.import_transaction_id = ? AND observation.supplier_id = ?
                     AND observation.part_id = ?
                   ORDER BY observation.economic_date DESC, observation.recorded_at_utc DESC,
                            observation.observation_id DESC LIMIT 1""",
                (summary[0], later["supplier_id"], later["part_id"]),
            ).fetchone()
            mismatch = baseline is None
        if not mismatch and baseline is not None and later is not None:
            before_score = scores[str(baseline["structure_category"])]
            after_score = scores[str(later["structure_category"])]
            structure_trend = "Improved" if after_score > before_score else (
                "Deteriorated" if after_score < before_score else "Unchanged")
            def field_set(observation_id: str) -> set[str]:
                return {str(item[0]) for item in connection.execute(
                    "SELECT DISTINCT field_code FROM submitted_datum WHERE observation_id = ?",
                    (observation_id,),
                )}
            baseline_fields = field_set(str(baseline["observation_id"]))
            later_fields = field_set(str(later["observation_id"]))
            added = sorted(later_fields - baseline_fields)
            removed = sorted(baseline_fields - later_fields)
            field_trend = ("Unchanged" if not added and not removed else
                           "Improved" if added and not removed else
                           "Deteriorated" if removed and not added else "Mixed")
            def exception_count(observation_id: str) -> int:
                return int(connection.execute(
                    """SELECT COUNT(*) FROM formula_integrity_event
                       WHERE observation_id = ?
                         AND classification = 'Formula Reconciliation Exception'""",
                    (observation_id,),
                ).fetchone()[0])
            before_formula = exception_count(str(baseline["observation_id"]))
            after_formula = exception_count(str(later["observation_id"]))
            formula_trend = "Improved" if after_formula < before_formula else (
                "Deteriorated" if after_formula > before_formula else "Unchanged")
            payload = {
                "summary_id": row["historical_baseline_completion_summary_id"],
                "baseline_observation_id": baseline["observation_id"],
                "later_observation_id": later["observation_id"],
                "supplier_id": later["supplier_id"], "part_id": later["part_id"],
                "baseline_structure_category": baseline["structure_category"],
                "later_structure_category": later["structure_category"],
                "structure_trend": structure_trend, "added_fields": added,
                "removed_fields": removed, "field_coverage_trend": field_trend,
                "baseline_formula_exception_count": before_formula,
                "later_formula_exception_count": after_formula,
                "formula_integrity_trend": formula_trend,
                "comparison_rule_version_id": row["comparison_rule_version_id"],
            }
            expected_hash = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
            mismatch = (
                row["baseline_observation_id"] != baseline["observation_id"]
                or row["supplier_id"] != later["supplier_id"]
                or row["part_id"] != later["part_id"]
                or row["baseline_structure_category"] != baseline["structure_category"]
                or row["later_structure_category"] != later["structure_category"]
                or row["structure_trend"] != structure_trend
                or json.loads(str(row["added_field_payload"])) != added
                or json.loads(str(row["removed_field_payload"])) != removed
                or row["field_coverage_trend"] != field_trend
                or int(row["baseline_formula_exception_count"]) != before_formula
                or int(row["later_formula_exception_count"]) != after_formula
                or row["formula_integrity_trend"] != formula_trend
                or row["comparison_hash"] != expected_hash
            )
        if mismatch:
            findings.append(_finding(
                "HISTORICAL_BASELINE_COMPARISON_MISMATCH",
                "Historical comparison does not reproduce from its baseline and later immutable observations",
                "Historical Baseline Comparison", comparison_id,
            ))
    return findings


def verify_registry_cache(
    connection: sqlite3.Connection, *, as_of_utc: str
) -> list[IntegrityFinding]:
    findings: list[IntegrityFinding] = []
    active = connection.execute(
        """SELECT cache_generation_id, registry_publication_id,
                  registry_version, publication_hash, signature_status,
                  expires_at_utc
           FROM v_current_active_registry_cache"""
    ).fetchall()
    if len(active) != 1:
        findings.append(_finding(
            "ACTIVE_REGISTRY_CACHE_COUNT_INVALID",
            f"Expected exactly one active registry cache, found {len(active)}",
            "Registry Cache", None,
        ))
    for generation in active:
        generation_id = str(generation[0])
        if generation[4] != "Verified":
            findings.append(_finding(
                "REGISTRY_CACHE_SIGNATURE_INVALID",
                "Active registry cache does not have a verified signature",
                "Registry Cache", generation_id,
            ))
        if generation[5] is not None and str(generation[5]) <= as_of_utc:
            findings.append(_finding(
                "REGISTRY_CACHE_EXPIRED",
                f"Active registry cache expired at {generation[5]}",
                "Registry Cache", generation_id,
            ))
        entity_rows = [tuple(row) for row in connection.execute(
            """SELECT entity_type, entity_id, entity_payload, content_hash
               FROM registry_entity_cache WHERE cache_generation_id = ?
               ORDER BY entity_type, entity_id""", (generation_id,)
        )]
        invalid_entities = [row for row in entity_rows if hashlib.sha256(
            str(row[2]).encode("utf-8")
        ).hexdigest() != row[3]]
        _, manifest_hash = registry_cache_manifest(
            str(generation[1]), str(generation[2]), entity_rows
        )
        if invalid_entities or manifest_hash != generation[3]:
            findings.append(_finding(
                "REGISTRY_CACHE_CONTENT_MISMATCH",
                "Registry entity hashes or publication manifest hash do not reconcile",
                "Registry Cache", generation_id,
            ))
    for row in connection.execute(
        """SELECT scenario.scenario_revision_id, pin.cache_generation_id,
                  pin.registry_publication_id, pin.registry_version,
                  pin.publication_hash, generation.registry_publication_id,
                  generation.registry_version, generation.publication_hash
           FROM scenario_revision scenario
           LEFT JOIN scenario_registry_cache_pin pin
             ON pin.scenario_revision_id = scenario.scenario_revision_id
           LEFT JOIN registry_cache_generation generation
             ON generation.cache_generation_id = pin.cache_generation_id"""
    ):
        if row[1] is None:
            findings.append(_finding(
                "SCENARIO_REGISTRY_CACHE_PIN_MISSING",
                "Scenario revision does not pin a registry cache generation",
                "Scenario Revision", str(row[0]),
            ))
        elif tuple(row[2:5]) != tuple(row[5:8]):
            findings.append(_finding(
                "SCENARIO_REGISTRY_CACHE_PIN_MISMATCH",
                "Scenario registry pin does not match its immutable cache generation",
                "Scenario Revision", str(row[0]),
            ))
    return findings


def verify_finalized_analyses(connection: sqlite3.Connection) -> list[IntegrityFinding]:
    findings: list[IntegrityFinding] = []
    for row in connection.execute(
        """SELECT fs.finalized_snapshot_id, fs.analysis_id,
                  fs.scenario_revision_id, fs.calculation_run_id,
                  fs.open_action_count, fs.evidence_manifest_hash,
                  fs.audit_chain_anchor, status.run_status,
                  scenario.analysis_id, run.scenario_revision_id
           FROM finalized_snapshot fs
           JOIN scenario_revision scenario
             ON scenario.scenario_revision_id = fs.scenario_revision_id
           JOIN calculation_run run ON run.calculation_run_id = fs.calculation_run_id
           JOIN v_current_calculation_run_status status
             ON status.calculation_run_id = run.calculation_run_id"""
    ):
        (snapshot_id, analysis_id, scenario_id, run_id, open_action_count,
         manifest_hash, audit_anchor, run_status, scenario_analysis_id,
         run_scenario_id) = row
        if analysis_id != scenario_analysis_id or scenario_id != run_scenario_id:
            findings.append(_finding(
                "FINALIZED_SCOPE_RELATIONSHIP_MISMATCH",
                "Snapshot analysis, scenario, and calculation run do not form one scope",
                "Finalized Snapshot", str(snapshot_id),
            ))
        if run_status != "Completed":
            findings.append(_finding("FINALIZED_RUN_NOT_COMPLETE", f"Calculation run {run_id} has status {run_status}", "Finalized Snapshot", str(snapshot_id)))
        manifest_rows = connection.execute(
            """SELECT evidence_entity_type, evidence_entity_id, included_flag,
                      analytical_role, exclusion_reason
               FROM evidence_manifest_entry WHERE scenario_revision_id = ?
               ORDER BY evidence_entity_type, evidence_entity_id, analytical_role""",
            (scenario_id,),
        ).fetchall()
        expected_manifest_hash = hashlib.sha256(
            canonical_json([tuple(item) for item in manifest_rows]).encode("utf-8")
        ).hexdigest()
        if expected_manifest_hash != manifest_hash:
            findings.append(_finding(
                "FINALIZED_EVIDENCE_MANIFEST_MISMATCH",
                "Snapshot evidence hash does not reproduce from its scenario manifest",
                "Finalized Snapshot", str(snapshot_id),
            ))
        result_rows = connection.execute(
            """SELECT result_code, supplier_id, part_id, program_year,
                      category_code, decimal_coefficient, decimal_scale,
                      normalized_unit_id, currency_id
               FROM calculation_result WHERE calculation_run_id = ?""", (run_id,),
        ).fetchall()
        frozen_rows = connection.execute(
            """SELECT result_code, supplier_id, part_id, program_year,
                      category_code, decimal_coefficient, decimal_scale,
                      normalized_unit_id, currency_id
               FROM snapshot_result WHERE finalized_snapshot_id = ?""", (snapshot_id,),
        ).fetchall()
        source_results = sorted(canonical_json(tuple(item)) for item in result_rows)
        frozen_results = sorted(canonical_json(tuple(item)) for item in frozen_rows)
        if source_results != frozen_results:
            findings.append(_finding(
                "SNAPSHOT_RESULT_CONTENT_MISMATCH",
                "Frozen snapshot results do not exactly reproduce the completed run",
                "Finalized Snapshot", str(snapshot_id),
            ))
        missing_lineage = connection.execute(
            """SELECT COUNT(*) FROM calculation_result result
               WHERE result.calculation_run_id = ?
                 AND NOT EXISTS (
                     SELECT 1 FROM calculation_lineage lineage
                     WHERE lineage.calculation_result_id = result.calculation_result_id)""",
            (run_id,),
        ).fetchone()[0]
        if missing_lineage:
            findings.append(_finding("FINALIZED_RESULT_MISSING_LINEAGE", f"{missing_lineage} finalized results have no lineage", "Finalized Snapshot", str(snapshot_id)))
        action_rows = connection.execute(
            """SELECT snapshot_action_id, buyer_action_id,
                      buyer_action_version_id, frozen_status, frozen_payload
               FROM snapshot_action WHERE finalized_snapshot_id = ?""", (snapshot_id,),
        ).fetchall()
        frozen_open_count = sum(
            item[3] not in ("Resolved", "Accepted Exception") for item in action_rows
        )
        if frozen_open_count != open_action_count:
            findings.append(_finding(
                "SNAPSHOT_OPEN_ACTION_COUNT_MISMATCH",
                "Frozen open-action count does not match snapshot action statuses",
                "Finalized Snapshot", str(snapshot_id),
            ))
        for action in action_rows:
            version = connection.execute(
                """SELECT buyer_action_id, action_status, required_supplier_action,
                          owner_user_id, resolution_reason
                   FROM buyer_action_version WHERE buyer_action_version_id = ?""",
                (action[2],),
            ).fetchone()
            expected_payload = None if version is None else canonical_json({
                "required_supplier_action": version[2],
                "owner_user_id": version[3], "resolution_reason": version[4],
            })
            if version is None or version[0] != action[1] or version[1] != action[3] \
                    or expected_payload != action[4]:
                findings.append(_finding(
                    "SNAPSHOT_ACTION_CONTENT_MISMATCH",
                    "Frozen action does not reproduce from its referenced immutable version",
                    "Snapshot Action", str(action[0]),
                ))
        if connection.execute(
            "SELECT 1 FROM audit_event WHERE event_hash = ?", (audit_anchor,),
        ).fetchone() is None:
            findings.append(_finding(
                "SNAPSHOT_AUDIT_ANCHOR_MISSING",
                "Snapshot audit-chain anchor does not identify a retained audit event",
                "Finalized Snapshot", str(snapshot_id),
            ))
    return findings


def verify_formula_integrity(connection: sqlite3.Connection) -> list[IntegrityFinding]:
    """Validate formula records while treating supplier formula issues as advisory."""
    findings: list[IntegrityFinding] = []
    for row in connection.execute(
        """SELECT fe.formula_evidence_id, fe.observation_id, fe.parse_status,
                  fe.parsed_expression, fe.submitted_formula_text, sd.formula_text
           FROM formula_evidence fe
           JOIN source_datum sd ON sd.source_datum_id = fe.source_datum_id"""
    ):
        evidence_id, _, status, parsed, submitted, source_formula = row
        if source_formula is not None and str(source_formula).strip() != str(submitted).strip():
            findings.append(_finding("FORMULA_SOURCE_MISMATCH", "Stored formula evidence differs from immutable source evidence", "Formula Evidence", str(evidence_id)))
        if status == "Parsed" and not parsed:
            findings.append(_finding("PARSED_FORMULA_MISSING_EXPRESSION", "Parsed formula has no persisted expression", "Formula Evidence", str(evidence_id)))

    for formula_id, formula_text in connection.execute(
        "SELECT formula_evidence_id, submitted_formula_text FROM formula_evidence"
    ):
        rows = connection.execute(
            """SELECT formula_dependency_status_event_id, dependency_status,
                      dependency_manifest_hash, supersedes_dependency_status_event_id
               FROM formula_dependency_status_event WHERE formula_evidence_id = ?""",
            (formula_id,),
        ).fetchall()
        identifiers = {str(item[0]) for item in rows}
        roots = [item for item in rows if item[3] is None]
        tips = [item for item in rows if not any(child[3] == item[0] for child in rows)]
        disconnected = any(item[3] is not None and str(item[3]) not in identifiers for item in rows)
        if len(roots) != 1 or len(tips) != 1 or disconnected:
            findings.append(_finding(
                "FORMULA_DEPENDENCY_CHAIN_INVALID",
                "Formula dependency status must have one connected root and current tip",
                "Formula Evidence", str(formula_id),
            ))
            continue
        external = "[" in formula_text and "]" in formula_text
        if external and tips[0][1] == "Internally Reproducible":
            findings.append(_finding(
                "EXTERNAL_FORMULA_STATUS_INVALID",
                "External-link formula cannot be marked internally reproducible",
                "Formula Evidence", str(formula_id),
            ))
        if not external and tips[0][1].startswith("External Dependency"):
            findings.append(_finding(
                "INTERNAL_FORMULA_STATUS_INVALID",
                "Internal formula cannot carry external-dependency status",
                "Formula Evidence", str(formula_id),
            ))
        if tips[0][1] == "External Dependency Verified" and not tips[0][2]:
            findings.append(_finding(
                "FORMULA_DEPENDENCY_MANIFEST_MISSING",
                "Verified external dependency requires its immutable manifest hash",
                "Formula Evidence", str(formula_id),
            ))

    decimal_fields = ("expected", "recalculated", "variance")
    for row in connection.execute(
        """SELECT formula_integrity_event_id, observation_id, formula_evidence_id,
                  classification, expected_coefficient, expected_scale,
                  recalculated_coefficient, recalculated_scale,
                  variance_coefficient, variance_scale
           FROM formula_integrity_event"""
    ):
        event_id, observation_id, evidence_id, classification, *values = row
        if evidence_id is not None:
            owner = connection.execute(
                "SELECT observation_id FROM formula_evidence WHERE formula_evidence_id = ?",
                (evidence_id,),
            ).fetchone()
            if owner is None or owner[0] != observation_id:
                findings.append(_finding("FORMULA_EVENT_LINEAGE_MISMATCH", "Formula event and evidence do not belong to the same observation", "Formula Integrity Event", str(event_id)))
        for index, field in enumerate(decimal_fields):
            coefficient, scale = values[index * 2:index * 2 + 2]
            if (coefficient is None) != (scale is None):
                findings.append(_finding("FORMULA_DECIMAL_PAIR_INCOMPLETE", f"{field} coefficient and scale must both be present or absent", "Formula Integrity Event", str(event_id)))
            elif coefficient is not None:
                try:
                    int(str(coefficient))
                    ExactDecimal(str(coefficient), str(coefficient), int(scale))
                except (ValueError, OverflowError, TypeError) as error:
                    findings.append(_finding("INVALID_FORMULA_EXACT_DECIMAL", f"{field}: {error}", "Formula Integrity Event", str(event_id)))
        if classification == "Formula Reconciliation Exception":
            findings.append(_finding("FORMULA_RECONCILIATION_EXCEPTION", "Supplier formula does not reconcile to the expected piece-price value", "Formula Integrity Event", str(event_id), "Warning"))
        elif classification == "Insufficient Evidence":
            findings.append(_finding("FORMULA_EVIDENCE_INSUFFICIENT", "Formula could not be fully recalculated; analysis may continue with this limitation disclosed", "Formula Integrity Event", str(event_id), "Warning"))
    return findings


def verify_quote_version_history(connection: sqlite3.Connection) -> list[IntegrityFinding]:
    findings: list[IntegrityFinding] = []
    expected_sequences: dict[tuple[str, str, str, str], int] = {}
    for candidate in connection.execute(
        """SELECT * FROM quote_version_candidate
           ORDER BY event_id, supplier_id, part_id, region_code, version_number"""
    ):
        candidate_id = str(candidate["quote_version_candidate_id"])
        scope = (str(candidate["event_id"]), str(candidate["supplier_id"]),
                 str(candidate["part_id"]), str(candidate["region_code"]))
        expected = expected_sequences.get(scope, 0) + 1
        expected_sequences[scope] = expected
        lineage_count = int(connection.execute(
            """SELECT COUNT(DISTINCT round.event_id || '|' || observation.supplier_id ||
                                      '|' || observation.part_id)
               FROM pbd_observation observation
               JOIN round_observation membership
                 ON membership.observation_id = observation.observation_id
               JOIN supplier_quote_round round
                 ON round.quote_round_id = membership.quote_round_id
               JOIN event_part event_part
                 ON event_part.event_part_id = membership.event_part_id
               WHERE observation.observation_id = ?
                 AND observation.observation_context = 'Sourcing Event'
                 AND observation.supplier_id = ? AND observation.part_id = ?
                 AND round.event_id = ? AND event_part.part_id = ?""",
            (candidate["observation_id"], candidate["supplier_id"],
             candidate["part_id"], candidate["event_id"], candidate["part_id"]),
        ).fetchone()[0])
        review = connection.execute(
            """SELECT quote_version_review_event_id, review_decision
               FROM quote_version_review_event
               WHERE quote_version_candidate_id = ?""", (candidate_id,),
        ).fetchone()
        activations = connection.execute(
            """SELECT quote_version_review_event_id
               FROM quote_version_activation
               WHERE quote_version_candidate_id = ?""", (candidate_id,),
        ).fetchall()
        expected_activation_count = (
            1 if review is not None and review["review_decision"] == "Confirm Active" else 0
        )
        activation_matches_review = (
            not activations or review is not None
            and str(activations[0][0]) == str(review[0])
        )
        if (int(candidate["version_number"]) != expected or lineage_count != 1
                or len(activations) != expected_activation_count
                or not activation_matches_review):
            findings.append(_finding(
                "QUOTE_VERSION_HISTORY_MISMATCH",
                "Quote version sequence, lineage, review, or activation does not reproduce",
                "Quote Version Candidate", candidate_id,
            ))
    for scope in expected_sequences:
        activations = connection.execute(
            """SELECT activation.quote_version_activation_id,
                      activation.supersedes_activation_id, candidate.version_number
               FROM quote_version_activation activation
               JOIN quote_version_candidate candidate
                 ON candidate.quote_version_candidate_id = activation.quote_version_candidate_id
               WHERE candidate.event_id = ? AND candidate.supplier_id = ?
                 AND candidate.part_id = ? AND candidate.region_code = ?
               ORDER BY candidate.version_number""", scope,
        ).fetchall()
        prior_id: str | None = None
        for activation in activations:
            if activation["supersedes_activation_id"] != prior_id:
                findings.append(_finding(
                    "QUOTE_VERSION_ACTIVATION_CHAIN_MISMATCH",
                    "Active quote versions do not form a direct increasing chain",
                    "Quote Version Activation", str(activation[0]),
                ))
            prior_id = str(activation[0])
    return findings


def verify_quote_version_movements(connection: sqlite3.Connection) -> list[IntegrityFinding]:
    findings: list[IntegrityFinding] = []
    for generation in connection.execute(
        "SELECT * FROM quote_version_movement_generation"
    ):
        generation_id = str(generation["quote_version_movement_generation_id"])
        candidate, current_values = _candidate_values(
            connection, str(generation["quote_version_candidate_id"]),
            DEFAULT_MOVEMENT_FIELDS,
        )
        scope = (candidate["event_id"], candidate["supplier_id"],
                 candidate["part_id"], candidate["region_code"])
        prior = connection.execute(
            """SELECT * FROM quote_version_candidate
               WHERE event_id = ? AND supplier_id = ? AND part_id = ?
                 AND region_code = ? AND version_number < ?
               ORDER BY version_number DESC LIMIT 1""",
            (*scope, candidate["version_number"]),
        ).fetchone()
        initial = connection.execute(
            """SELECT * FROM quote_version_candidate
               WHERE event_id = ? AND supplier_id = ? AND part_id = ?
                 AND region_code = ? ORDER BY version_number LIMIT 1""", scope,
        ).fetchone()
        mismatch = prior is None or initial is None
        expected_rows: list[tuple[object, ...]] = []
        if prior is not None and initial is not None:
            _, prior_values = _candidate_values(
                connection, str(prior["quote_version_candidate_id"]),
                DEFAULT_MOVEMENT_FIELDS,
            )
            _, initial_values = _candidate_values(
                connection, str(initial["quote_version_candidate_id"]),
                DEFAULT_MOVEMENT_FIELDS,
            )
            expected_rows = [
                _movement_row(basis, code, current_values.get(code), baseline.get(code))
                for basis, baseline in (("Prior Version", prior_values),
                                        ("Initial Version", initial_values))
                for code in DEFAULT_MOVEMENT_FIELDS
            ]
            manifest = {
                "quote_version_candidate_id": candidate["quote_version_candidate_id"],
                "prior_candidate_id": prior["quote_version_candidate_id"],
                "initial_candidate_id": initial["quote_version_candidate_id"],
                "calculation_rule_version_id": generation["calculation_rule_version_id"],
                "components": expected_rows,
            }
            expected_hash = hashlib.sha256(
                canonical_json(manifest).encode("utf-8")
            ).hexdigest()
            mismatch = mismatch or (
                generation["prior_candidate_id"] != prior["quote_version_candidate_id"]
                or generation["initial_candidate_id"] != initial["quote_version_candidate_id"]
                or generation["component_manifest_hash"] != expected_hash
            )
        stored_rows = [tuple(row) for row in connection.execute(
            """SELECT comparison_basis, field_code,
                      current_submitted_datum_id, baseline_submitted_datum_id,
                      comparison_status, current_coefficient, current_scale,
                      baseline_coefficient, baseline_scale, delta_coefficient,
                      delta_scale, movement_percent_coefficient,
                      movement_percent_scale, normalized_unit_id, currency_id
               FROM quote_version_component_movement
               WHERE quote_version_movement_generation_id = ?
               ORDER BY CASE comparison_basis WHEN 'Prior Version' THEN 0 ELSE 1 END,
                        CASE field_code
                            WHEN 'PIECE_PRICE' THEN 0 WHEN 'LABOR_DOLLARS' THEN 1
                            WHEN 'BURDEN_DOLLARS' THEN 2
                            WHEN 'FACTORY_OVERHEAD_DOLLARS' THEN 3
                            WHEN 'HEAD_OFFICE_OVERHEAD_DOLLARS' THEN 4
                            WHEN 'R_AND_D_OVERHEAD_DOLLARS' THEN 5
                            WHEN 'OVERHEAD_DOLLARS' THEN 6 WHEN 'PROFIT_DOLLARS' THEN 7
                            WHEN 'PURCHASED_COMPONENTS' THEN 8 ELSE 9 END""",
            (generation_id,),
        )]
        if mismatch or stored_rows != expected_rows:
            findings.append(_finding(
                "QUOTE_VERSION_MOVEMENT_MISMATCH",
                "Quote component movement does not reproduce from prior and initial submitted evidence",
                "Quote Version Movement Generation", generation_id,
            ))
    return findings


def verify_supplier_competitiveness_history(
    connection: sqlite3.Connection,
) -> list[IntegrityFinding]:
    findings: list[IntegrityFinding] = []
    for observation in connection.execute(
        "SELECT * FROM supplier_competitiveness_observation"
    ):
        observation_id = str(observation["supplier_competitiveness_observation_id"])
        event = connection.execute(
            "SELECT commodity_id FROM sourcing_event WHERE event_id = ?",
            (observation["event_id"],),
        ).fetchone()
        if observation["evidence_entity_type"] == "Quote Round":
            evidence = connection.execute(
                """SELECT 1 FROM supplier_quote_round
                   WHERE quote_round_id = ? AND quote_round_id = ?
                     AND event_id = ? AND supplier_id = ?""",
                (observation["evidence_entity_id"], observation["quote_population_id"],
                 observation["event_id"], observation["supplier_id"]),
            ).fetchone()
        elif observation["evidence_entity_type"] == "Calculation Result":
            evidence = connection.execute(
                """SELECT 1 FROM calculation_result result
                   JOIN calculation_run run
                     ON run.calculation_run_id = result.calculation_run_id
                   JOIN scenario_revision scenario
                     ON scenario.scenario_revision_id = run.scenario_revision_id
                   JOIN analysis ON analysis.analysis_id = scenario.analysis_id
                   WHERE result.calculation_result_id = ? AND result.supplier_id = ?
                     AND analysis.event_id = ? AND run.status = 'Completed'""",
                (observation["evidence_entity_id"], observation["supplier_id"],
                 observation["event_id"]),
            ).fetchone()
        else:
            evidence = None
        supersedes = observation["supersedes_observation_id"]
        prior_valid = supersedes is None or connection.execute(
            """SELECT 1 FROM supplier_competitiveness_observation prior
               WHERE prior.supplier_competitiveness_observation_id = ?
                 AND prior.event_id = ? AND prior.supplier_id = ?
                 AND prior.commodity_id = ? AND prior.quote_population_id = ?""",
            (supersedes, observation["event_id"], observation["supplier_id"],
             observation["commodity_id"], observation["quote_population_id"]),
        ).fetchone() is not None
        if (event is None or event[0] != observation["commodity_id"]
                or evidence is None or not prior_valid):
            findings.append(_finding(
                "SUPPLIER_COMPETITIVENESS_LINEAGE_MISMATCH",
                "Competitiveness status does not reproduce from its event, supplier, population, and evidence",
                "Supplier Competitiveness Observation", observation_id,
            ))
    for assessment in connection.execute(
        "SELECT * FROM supplier_improvement_assessment"
    ):
        assessment_id = str(assessment["supplier_improvement_assessment_id"])
        rows = improvement_evidence_rows(
            connection, supplier_id=str(assessment["supplier_id"]),
            commodity_id=str(assessment["commodity_id"]),
            evidence_cutoff_utc=str(assessment["evidence_cutoff_utc"]),
        )
        status, trailing, manifest_hash = derive_improvement_assessment(rows)
        expected_ids = [str(row["supplier_competitiveness_observation_id"])
                        for row in rows]
        stored_ids = [str(row[0]) for row in connection.execute(
            """SELECT supplier_competitiveness_observation_id
               FROM supplier_improvement_evidence
               WHERE supplier_improvement_assessment_id = ?
               ORDER BY evidence_ordinal""", (assessment_id,),
        )]
        if (assessment["assessment_status"] != status
                or int(assessment["trailing_competitive_population_count"]) != trailing
                or assessment["evidence_manifest_hash"] != manifest_hash
                or stored_ids != expected_ids):
            findings.append(_finding(
                "SUPPLIER_IMPROVEMENT_ASSESSMENT_MISMATCH",
                "Supplier improvement label does not reproduce from retained competitiveness history",
                "Supplier Improvement Assessment", assessment_id,
            ))
    return findings


def verify_current_historical_contexts(
    connection: sqlite3.Connection,
) -> list[IntegrityFinding]:
    findings: list[IntegrityFinding] = []
    for context in connection.execute(
        "SELECT * FROM supplier_current_historical_context"
    ):
        context_id = str(context["supplier_current_historical_context_id"])
        try:
            current, _, historical_findings, historical_status, notice, manifest_hash = (
                current_historical_context_payload(
                    connection,
                    supplier_competitiveness_observation_id=str(
                        context["supplier_competitiveness_observation_id"]
                    ),
                    supplier_profile_run_id=str(context["supplier_profile_run_id"]),
                )
            )
        except ValueError:
            findings.append(_finding(
                "CURRENT_HISTORICAL_CONTEXT_MISMATCH",
                "Current-event and historical-profile scopes no longer reproduce",
                "Supplier Current Historical Context", context_id,
            ))
            continue
        expected_finding_ids = [str(row["supplier_profile_finding_id"])
                                for row in historical_findings]
        stored_finding_ids = [str(row[0]) for row in connection.execute(
            """SELECT supplier_profile_finding_id
               FROM supplier_current_historical_finding
               WHERE supplier_current_historical_context_id = ?
               ORDER BY evidence_ordinal""", (context_id,),
        )]
        expected_notice = None if notice is None else canonical_json(notice)
        if (context["current_event_status"] != current["competitiveness_status"]
                or context["historical_profile_status"] != historical_status
                or context["historical_risk_notice_payload"] != expected_notice
                or context["context_manifest_hash"] != manifest_hash
                or stored_finding_ids != expected_finding_ids):
            findings.append(_finding(
                "CURRENT_HISTORICAL_CONTEXT_MISMATCH",
                "Current-event status, historical risks, notice, or manifest does not reproduce",
                "Supplier Current Historical Context", context_id,
            ))
    return findings


def verify_supplier_traffic_lights(connection: sqlite3.Connection) -> list[IntegrityFinding]:
    findings: list[IntegrityFinding] = []
    for assessment in connection.execute(
        "SELECT * FROM supplier_traffic_light_assessment"
    ):
        assessment_id = str(assessment["supplier_traffic_light_assessment_id"])
        stored = connection.execute(
            """SELECT category_code, category_status, confidence_classification,
                      material_commercial_impact_flag, recurrence_count,
                      evidence_entity_type, evidence_entity_id, explanation
               FROM supplier_traffic_light_category
               WHERE supplier_traffic_light_assessment_id = ?
               ORDER BY evidence_ordinal""", (assessment_id,),
        ).fetchall()
        categories = tuple(TrafficLightCategoryInput(
            category_code=str(row[0]), category_status=str(row[1]),
            confidence_classification=str(row[2]),
            material_commercial_impact=bool(row[3]), recurrence_count=int(row[4]),
            evidence_entity_type=row[5], evidence_entity_id=row[6],
            explanation=str(row[7]),
        ) for row in stored)
        mismatch = False
        try:
            for item in categories:
                _validate_evidence(
                    connection, supplier_id=str(assessment["supplier_id"]),
                    commodity_id=str(assessment["commodity_id"]), item=item,
                )
            status, governing, explanation, manifest_hash = derive_traffic_light(categories)
            mismatch = (
                assessment["overall_status"] != status
                or assessment["governing_category_payload"] != canonical_json(governing)
                or assessment["professional_explanation"] != explanation
                or assessment["category_manifest_hash"] != manifest_hash
            )
        except ValueError:
            mismatch = True
        if mismatch:
            findings.append(_finding(
                "SUPPLIER_TRAFFIC_LIGHT_MISMATCH",
                "Overall supplier status does not reproduce from complete scoped category evidence",
                "Supplier Traffic Light Assessment", assessment_id,
            ))
    return findings


def verify_supplier_overall_traffic_lights(
    connection: sqlite3.Connection,
) -> list[IntegrityFinding]:
    findings: list[IntegrityFinding] = []
    for overall in connection.execute(
        "SELECT * FROM supplier_overall_traffic_light_assessment"
    ):
        overall_id = str(overall["supplier_overall_traffic_light_assessment_id"])
        rows = connection.execute(
            """SELECT assessment.*
               FROM supplier_overall_traffic_light_commodity member
               JOIN supplier_traffic_light_assessment assessment
                 ON assessment.supplier_traffic_light_assessment_id =
                    member.supplier_traffic_light_assessment_id
               WHERE member.supplier_overall_traffic_light_assessment_id = ?
               ORDER BY member.evidence_ordinal""", (overall_id,),
        ).fetchall()
        stored_members = [tuple(row) for row in connection.execute(
            """SELECT commodity_id, supplier_traffic_light_assessment_id,
                      commodity_status
               FROM supplier_overall_traffic_light_commodity
               WHERE supplier_overall_traffic_light_assessment_id = ?
               ORDER BY evidence_ordinal""", (overall_id,),
        )]
        expected_members = [
            (row["commodity_id"], row["supplier_traffic_light_assessment_id"],
             row["overall_status"]) for row in rows
        ]
        mismatch = (
            stored_members != expected_members
            or len({str(row["commodity_id"]) for row in rows}) != len(rows)
            or any(row["supplier_id"] != overall["supplier_id"] for row in rows)
            or any(row["evidence_cutoff_utc"] > overall["evidence_cutoff_utc"]
                   for row in rows)
        )
        try:
            status, governing, explanation, manifest_hash = (
                derive_supplier_overall_traffic_light(rows)
            )
            mismatch = mismatch or (
                overall["overall_status"] != status
                or overall["governing_commodity_payload"] != canonical_json(governing)
                or overall["professional_explanation"] != explanation
                or overall["commodity_manifest_hash"] != manifest_hash
            )
        except ValueError:
            mismatch = True
        if mismatch:
            findings.append(_finding(
                "SUPPLIER_OVERALL_TRAFFIC_LIGHT_MISMATCH",
                "Supplier-wide status does not reproduce from retained commodity assessments",
                "Supplier Overall Traffic Light Assessment", overall_id,
            ))
    return findings


def assess_analysis_readiness(
    connection: sqlite3.Connection,
    migrations_dir: Path = DEFAULT_MIGRATIONS,
    as_of_utc: str | None = None,
) -> AnalysisReadiness:
    findings = run_health_gate(connection, migrations_dir, as_of_utc)
    blocking = tuple(finding for finding in findings if finding.severity == "Error")
    advisory = tuple(finding for finding in findings if finding.severity != "Error")
    return AnalysisReadiness(not blocking, blocking, advisory)


def run_health_gate(
    connection: sqlite3.Connection,
    migrations_dir: Path = DEFAULT_MIGRATIONS,
    as_of_utc: str | None = None,
) -> list[IntegrityFinding]:
    effective_as_of = as_of_utc or datetime.now(timezone.utc).isoformat(
        timespec="seconds"
    ).replace("+00:00", "Z")
    findings: list[IntegrityFinding] = []
    for row in connection.execute("PRAGMA integrity_check"):
        if row[0] != "ok":
            findings.append(_finding("SQLITE_INTEGRITY_FAILURE", str(row[0])))
    for row in connection.execute("PRAGMA foreign_key_check"):
        findings.append(_finding("FOREIGN_KEY_FAILURE", repr(tuple(row))))
    findings.extend(verify_migration_checksums(connection, migrations_dir))
    findings.extend(
        _finding("AUDIT_CHAIN_FAILURE", detail, "Audit Event", None)
        for detail in verify_audit_chain(connection)
    )
    findings.extend(verify_polymorphic_references(connection))
    findings.extend(verify_exact_decimals(connection))
    findings.extend(verify_import_reconciliation(connection))
    findings.extend(verify_import_discovery(connection))
    findings.extend(verify_source_fingerprints(connection))
    from .extraction_staging import verify_extraction_receipts
    findings.extend(_finding("WORKBOOK_EXTRACTION_REPRODUCTION_MISMATCH", detail,
                             "Source Workbook", workbook_id)
                    for workbook_id, detail in verify_extraction_receipts(connection))
    findings.extend(verify_event_summary_generations(connection))
    findings.extend(verify_search_generations(connection))
    findings.extend(verify_part_history_generations(connection))
    findings.extend(verify_recovery_evidence(connection))
    findings.extend(verify_sourcing_event_status_history(connection))
    findings.extend(verify_analysis_run_status_history(connection))
    findings.extend(verify_import_state_history(connection))
    findings.extend(verify_staging_resolutions(connection))
    findings.extend(verify_pce_evidence_boundaries(connection))
    findings.extend(verify_round_conflict_history(connection))
    findings.extend(verify_observation_commit_gate(connection))
    findings.extend(verify_source_availability_history(connection))
    findings.extend(verify_source_package_mismatches(connection))
    findings.extend(verify_supplier_identity_confirmations(connection))
    findings.extend(verify_quote_version_history(connection))
    findings.extend(verify_quote_version_movements(connection))
    findings.extend(verify_supplier_competitiveness_history(connection))
    findings.extend(verify_current_historical_contexts(connection))
    findings.extend(verify_supplier_traffic_lights(connection))
    findings.extend(verify_supplier_overall_traffic_lights(connection))
    findings.extend(verify_generated_outputs(connection))
    findings.extend(verify_projection_generations(connection))
    findings.extend(verify_supplier_profile_generations(connection))
    findings.extend(verify_supplier_rate_distributions(connection))
    findings.extend(verify_supplier_family_profile_generations(connection))
    findings.extend(verify_historical_baseline_summaries(connection))
    findings.extend(verify_historical_baseline_comparisons(connection))
    findings.extend(verify_registry_cache(connection, as_of_utc=effective_as_of))
    findings.extend(verify_finalized_analyses(connection))
    findings.extend(verify_formula_integrity(connection))
    return findings
