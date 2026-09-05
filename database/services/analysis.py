from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from typing import Any

from .audit import AuditContext, append_audit_event, canonical_json
from .connection import immediate_transaction
from .decimals import ExactDecimal
from .ids import uuid7


EVIDENCE_TABLES = {
    "PBD Observation": ("pbd_observation", "observation_id"),
    "Supplier Activity": ("supplier_activity", "supplier_activity_id"),
    "GST Baseline": ("gst_baseline", "gst_baseline_id"),
    "PCE Model": ("pce_model_membership", "pce_model_membership_id"),
    "Knowledge Version": ("knowledge_version", "knowledge_version_id"),
    "Quote Round": ("supplier_quote_round", "quote_round_id"),
    "Cross Border Reconstruction": ("cross_border_reconstruction", "cross_border_reconstruction_id"),
    "Cross Border Operation Match": ("cross_border_operation_match", "cross_border_operation_match_id"),
}


@dataclass(frozen=True)
class EvidenceSelection:
    entity_type: str
    entity_id: str
    included: bool
    analytical_role: str
    exclusion_reason: str | None = None


@dataclass(frozen=True)
class ScenarioInput:
    input_code: str
    submitted_lexeme: str | None = None
    exact_decimal: ExactDecimal | None = None
    text_value: str | None = None
    normalized_unit_id: str | None = None
    currency_id: str | None = None
    source_entity_type: str | None = None
    source_entity_id: str | None = None
    confirmed_by_user_id: str | None = None


@dataclass(frozen=True)
class ResultLineage:
    source_entity_type: str
    source_entity_id: str
    dependency_role: str


@dataclass(frozen=True)
class CalculationResultInput:
    result_code: str
    exact_decimal: ExactDecimal | None
    supplier_id: str | None = None
    part_id: str | None = None
    program_year: int | None = None
    category_code: str | None = None
    normalized_unit_id: str | None = None
    currency_id: str | None = None
    confidence_classification: str | None = None
    lineage: tuple[ResultLineage, ...] = ()


def _require_evidence(connection: sqlite3.Connection, entity_type: str, entity_id: str) -> None:
    location = EVIDENCE_TABLES.get(entity_type)
    if location is None:
        raise ValueError(f"Unsupported analysis evidence type: {entity_type}")
    table, key = location
    if connection.execute(f"SELECT 1 FROM {table} WHERE {key} = ?", (entity_id,)).fetchone() is None:
        raise ValueError(f"Analysis evidence does not exist: {entity_type} {entity_id}")


def create_analysis(
    connection: sqlite3.Connection,
    *,
    event_id: str,
    analysis_type: str,
    readable_name: str,
    created_by_user_id: str,
    created_at_utc: str,
    audit: AuditContext,
) -> str:
    analysis_id = uuid7()
    with immediate_transaction(connection):
        if connection.execute("SELECT 1 FROM sourcing_event WHERE event_id = ?", (event_id,)).fetchone() is None:
            raise ValueError("Sourcing event does not exist")
        connection.execute(
            "INSERT INTO analysis VALUES (?, ?, ?, ?, ?, ?)",
            (analysis_id, event_id, analysis_type, readable_name, created_by_user_id, created_at_utc),
        )
        connection.execute(
            """INSERT INTO analysis_status_event VALUES
               (?, ?, 'Working', 'Analysis created', ?, NULL, ?)""",
            (uuid7(), analysis_id, created_by_user_id, created_at_utc),
        )
        append_audit_event(connection, audit, {"analysis_id": analysis_id, "event_id": event_id, "analysis_type": analysis_type})
    return analysis_id


def create_scenario_revision(
    connection: sqlite3.Connection,
    *,
    analysis_id: str,
    scope_version_id: str,
    gst_baseline_id: str | None,
    scenario_name: str | None,
    parent_revision_id: str | None,
    inputs: tuple[ScenarioInput, ...],
    evidence: tuple[EvidenceSelection, ...],
    created_by_user_id: str,
    created_at_utc: str,
    audit: AuditContext,
) -> str:
    if not evidence:
        raise ValueError("Scenario revision requires an explicit evidence manifest")
    scenario_id = uuid7()
    with immediate_transaction(connection):
        analysis = connection.execute(
            "SELECT event_id FROM analysis WHERE analysis_id = ?", (analysis_id,)
        ).fetchone()
        if analysis is None:
            raise ValueError("Analysis does not exist")
        registry_caches = connection.execute(
            """SELECT cache_generation_id, registry_publication_id,
                      registry_version, publication_hash
               FROM v_current_active_registry_cache
               WHERE signature_status = 'Verified'
                 AND (expires_at_utc IS NULL OR expires_at_utc > ?)
               ORDER BY imported_at_utc DESC, cache_generation_id DESC""",
            (created_at_utc,),
        ).fetchall()
        if len(registry_caches) != 1:
            raise ValueError(
                "Scenario revision requires exactly one active, verified, unexpired registry cache"
            )
        registry_cache = registry_caches[0]
        scope = connection.execute(
            """SELECT sp.event_id FROM scope_version sv
               JOIN source_package sp ON sp.source_package_id = sv.source_package_id
               WHERE sv.scope_version_id = ?""",
            (scope_version_id,),
        ).fetchone()
        if scope is None or scope[0] != analysis[0]:
            raise ValueError("Scope version does not belong to the analysis event")
        if gst_baseline_id is not None:
            baseline = connection.execute(
                "SELECT scope_version_id FROM gst_baseline WHERE gst_baseline_id = ?",
                (gst_baseline_id,),
            ).fetchone()
            if baseline is None or baseline[0] != scope_version_id:
                raise ValueError("GST baseline does not belong to the selected scope")
        for selection in evidence:
            _require_evidence(connection, selection.entity_type, selection.entity_id)
            if not selection.included and not selection.exclusion_reason:
                raise ValueError("Excluded evidence requires an exclusion reason")
        included = {(item.entity_type, item.entity_id) for item in evidence if item.included}
        for entity_type, entity_id in included:
            if entity_type == "Cross Border Reconstruction":
                dependencies = connection.execute(
                    """SELECT pair.us_observation_id, pair.mx_observation_id,
                              benchmark.observation_id
                       FROM cross_border_reconstruction reconstruction
                       JOIN cross_border_exact_part_pair pair
                         ON pair.cross_border_pair_id = reconstruction.cross_border_pair_id
                       LEFT JOIN location_measure_evidence benchmark
                         ON benchmark.location_measure_evidence_id = reconstruction.benchmark_evidence_id
                       WHERE reconstruction.cross_border_reconstruction_id = ?""",
                    (entity_id,),
                ).fetchone()
                required = {str(dependencies[0]), str(dependencies[1])}
                if dependencies[2] is not None:
                    required.add(str(dependencies[2]))
                missing = required - {item_id for item_type, item_id in included if item_type == "PBD Observation"}
                if missing:
                    raise ValueError("Cross-border reconstruction requires all underlying observations in the scenario manifest")
        revision_number = connection.execute(
            "SELECT COALESCE(MAX(revision_number), 0) + 1 FROM scenario_revision WHERE analysis_id = ?",
            (analysis_id,),
        ).fetchone()[0]
        assumptions_payload = [
            {
                "input_code": item.input_code,
                "submitted_lexeme": item.submitted_lexeme,
                "coefficient": item.exact_decimal.coefficient if item.exact_decimal else None,
                "scale": item.exact_decimal.scale if item.exact_decimal else None,
                "text_value": item.text_value,
                "unit": item.normalized_unit_id,
                "currency": item.currency_id,
                "source_type": item.source_entity_type,
                "source_id": item.source_entity_id,
            }
            for item in inputs
        ]
        assumptions_hash = hashlib.sha256(canonical_json(assumptions_payload).encode("utf-8")).hexdigest()
        connection.execute(
            """INSERT INTO scenario_revision
               (scenario_revision_id, analysis_id, revision_number,
                parent_revision_id, scenario_name, scope_version_id,
                gst_baseline_id, assumptions_hash, created_by_user_id,
                created_at_utc)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                scenario_id, analysis_id, revision_number, parent_revision_id,
                scenario_name, scope_version_id, gst_baseline_id,
                assumptions_hash, created_by_user_id, created_at_utc,
            ),
        )
        connection.execute(
            """INSERT INTO scenario_registry_cache_pin
               (scenario_registry_cache_pin_id, scenario_revision_id,
                cache_generation_id, registry_publication_id,
                registry_version, publication_hash, pinned_at_utc)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (uuid7(), scenario_id, registry_cache[0], registry_cache[1],
             registry_cache[2], registry_cache[3], created_at_utc),
        )
        for item in inputs:
            connection.execute(
                """INSERT INTO scenario_input
                   (scenario_input_id, scenario_revision_id, input_code,
                    submitted_lexeme, decimal_coefficient, decimal_scale,
                    text_value, normalized_unit_id, currency_id,
                    source_entity_type, source_entity_id,
                    confirmed_by_user_id, recorded_at_utc)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    uuid7(), scenario_id, item.input_code,
                    item.submitted_lexeme,
                    item.exact_decimal.coefficient if item.exact_decimal else None,
                    item.exact_decimal.scale if item.exact_decimal else None,
                    item.text_value, item.normalized_unit_id, item.currency_id,
                    item.source_entity_type, item.source_entity_id,
                    item.confirmed_by_user_id, created_at_utc,
                ),
            )
        for selection in evidence:
            connection.execute(
                """INSERT INTO evidence_manifest_entry
                   (evidence_manifest_entry_id, scenario_revision_id,
                    evidence_entity_type, evidence_entity_id, included_flag,
                    analytical_role, exclusion_reason, recorded_at_utc)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    uuid7(), scenario_id, selection.entity_type,
                    selection.entity_id, int(selection.included),
                    selection.analytical_role, selection.exclusion_reason,
                    created_at_utc,
                ),
            )
        append_audit_event(
            connection, audit,
            {"scenario_revision_id": scenario_id, "analysis_id": analysis_id,
             "revision_number": revision_number,
             "assumptions_hash": assumptions_hash, "evidence_count": len(evidence),
             "registry_cache_generation_id": registry_cache[0],
             "registry_publication_id": registry_cache[1],
             "registry_version": registry_cache[2]},
        )
    return scenario_id


def persist_calculation_run(
    connection: sqlite3.Connection,
    *,
    scenario_revision_id: str,
    engine_version_id: str,
    calculation_rule_version_id: str,
    results: tuple[CalculationResultInput, ...],
    started_at_utc: str,
    completed_at_utc: str,
    audit: AuditContext,
) -> str:
    if not results:
        raise ValueError("A completed calculation run requires results")
    run_id = uuid7()
    with immediate_transaction(connection):
        manifest_rows = connection.execute(
            """SELECT evidence_entity_type, evidence_entity_id, included_flag,
                      analytical_role, exclusion_reason
               FROM evidence_manifest_entry WHERE scenario_revision_id = ?
               ORDER BY evidence_entity_type, evidence_entity_id, analytical_role""",
            (scenario_revision_id,),
        ).fetchall()
        if not manifest_rows:
            raise ValueError("Scenario revision has no evidence manifest")
        included_evidence = {
            (str(row[0]), str(row[1])) for row in manifest_rows if int(row[2]) == 1
        }
        input_hash = hashlib.sha256(
            canonical_json([tuple(row) for row in manifest_rows]).encode("utf-8")
        ).hexdigest()
        result_payload = [
            {
                "code": result.result_code,
                "coefficient": result.exact_decimal.coefficient if result.exact_decimal else None,
                "scale": result.exact_decimal.scale if result.exact_decimal else None,
                "supplier": result.supplier_id,
                "part": result.part_id,
                "year": result.program_year,
                "category": result.category_code,
                "unit": result.normalized_unit_id,
                "currency": result.currency_id,
            }
            for result in results
        ]
        output_hash = hashlib.sha256(canonical_json(result_payload).encode("utf-8")).hexdigest()
        connection.execute(
            """INSERT INTO calculation_run
               (calculation_run_id, scenario_revision_id, engine_version_id,
                calculation_rule_version_id, status, input_manifest_hash,
                output_manifest_hash, started_at_utc, completed_at_utc)
               VALUES (?, ?, ?, ?, 'Completed', ?, ?, ?, ?)""",
            (
                run_id, scenario_revision_id, engine_version_id,
                calculation_rule_version_id, input_hash, output_hash,
                started_at_utc, completed_at_utc,
            ),
        )
        connection.execute(
            """INSERT INTO calculation_run_status_event VALUES
               (?, ?, 'Completed', ?, 'Calculation and lineage committed atomically', NULL, ?)""",
            (uuid7(), run_id, output_hash, completed_at_utc),
        )
        analysis_status = connection.execute(
            """SELECT current.analysis_status_event_id, current.analysis_status,
                      scenario.analysis_id
               FROM scenario_revision scenario JOIN v_current_analysis_status current
                 ON current.analysis_id = scenario.analysis_id
               WHERE scenario.scenario_revision_id = ?""", (scenario_revision_id,),
        ).fetchone()
        if analysis_status is None or analysis_status[1] not in {"Working", "Draft Revision Complete"}:
            raise ValueError("Analysis is not available for calculation")
        calculating_id = uuid7()
        connection.execute(
            """INSERT INTO analysis_status_event VALUES
               (?, ?, 'Calculating', 'Calculation run executed', NULL, ?, ?)""",
            (calculating_id, analysis_status[2], analysis_status[0], started_at_utc),
        )
        connection.execute(
            """INSERT INTO analysis_status_event VALUES
               (?, ?, 'Draft Revision Complete', 'Calculation results committed', NULL, ?, ?)""",
            (uuid7(), analysis_status[2], calculating_id, completed_at_utc),
        )
        for output_ordinal, result in enumerate(results):
            if not result.lineage:
                raise ValueError(f"Calculation result {result.result_code} requires lineage")
            result_id = uuid7()
            connection.execute(
                """INSERT INTO calculation_result
                   (calculation_result_id, calculation_run_id, result_code,
                    supplier_id, part_id, program_year, category_code,
                    decimal_coefficient, decimal_scale, governing_1e4,
                    normalized_unit_id, currency_id,
                    confidence_classification, recorded_at_utc)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    result_id, run_id, result.result_code, result.supplier_id,
                    result.part_id, result.program_year, result.category_code,
                    result.exact_decimal.coefficient if result.exact_decimal else None,
                    result.exact_decimal.scale if result.exact_decimal else None,
                    result.exact_decimal.governing_1e4() if result.exact_decimal else None,
                    result.normalized_unit_id, result.currency_id,
                    result.confidence_classification, completed_at_utc,
                ),
            )
            connection.execute(
                """INSERT INTO calculation_output_entry_order
                   (calculation_result_id, calculation_run_id,
                    output_ordinal, recorded_at_utc)
                   VALUES (?, ?, ?, ?)""",
                (result_id, run_id, output_ordinal, completed_at_utc),
            )
            for ordinal, lineage in enumerate(result.lineage):
                if (lineage.source_entity_type, lineage.source_entity_id) not in included_evidence:
                    raise ValueError(
                        f"Calculation lineage is not included in the scenario evidence manifest: "
                        f"{lineage.source_entity_type} {lineage.source_entity_id}"
                    )
                _require_evidence(connection, lineage.source_entity_type, lineage.source_entity_id)
                connection.execute(
                    """INSERT INTO calculation_lineage
                       (calculation_lineage_id, calculation_result_id,
                        source_entity_type, source_entity_id, dependency_role,
                        dependency_ordinal, recorded_at_utc)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        uuid7(), result_id, lineage.source_entity_type,
                        lineage.source_entity_id, lineage.dependency_role,
                        ordinal, completed_at_utc,
                    ),
                )
        append_audit_event(
            connection, audit,
            {"calculation_run_id": run_id, "scenario_revision_id": scenario_revision_id, "input_manifest_hash": input_hash, "output_manifest_hash": output_hash, "result_count": len(results)},
        )
    return run_id


def finalize_analysis(
    connection: sqlite3.Connection,
    *,
    analysis_id: str,
    scenario_revision_id: str,
    calculation_run_id: str,
    finalized_by_user_id: str,
    finalized_at_utc: str,
    presentation_rule_version_id: str,
    audit: AuditContext,
) -> str:
    snapshot_id = uuid7()
    with immediate_transaction(connection):
        run = connection.execute(
            """SELECT status.run_status, cr.scenario_revision_id, sr.analysis_id
               FROM calculation_run cr
               JOIN v_current_calculation_run_status status
                 ON status.calculation_run_id = cr.calculation_run_id
               JOIN scenario_revision sr ON sr.scenario_revision_id = cr.scenario_revision_id
               WHERE cr.calculation_run_id = ?""",
            (calculation_run_id,),
        ).fetchone()
        if run is None or run[0] != "Completed" or run[1] != scenario_revision_id or run[2] != analysis_id:
            raise ValueError("Finalization requires the selected completed run, scenario, and analysis")
        analysis_status = connection.execute(
            """SELECT analysis_status_event_id, analysis_status
               FROM v_current_analysis_status WHERE analysis_id = ?""",
            (analysis_id,),
        ).fetchone()
        if analysis_status is None or analysis_status[1] != "Draft Revision Complete":
            raise ValueError("Analysis must have a completed draft revision before finalization")
        unresolved_identity = connection.execute(
            """SELECT COUNT(*) FROM evidence_manifest_entry em
               JOIN v_effective_pbd_observation po
                 ON em.evidence_entity_type = 'PBD Observation'
                AND em.evidence_entity_id = po.observation_id
               WHERE em.scenario_revision_id = ? AND em.included_flag = 1
                 AND po.supplier_id IS NULL""",
            (scenario_revision_id,),
        ).fetchone()[0]
        if unresolved_identity:
            raise ValueError("Supplier identity confirmation is required before finalization")
        context_only_included = connection.execute(
            """SELECT COUNT(*) FROM evidence_manifest_entry manifest
               JOIN v_latest_observation_eligibility eligibility
                 ON manifest.evidence_entity_type = 'PBD Observation'
                AND manifest.evidence_entity_id = eligibility.observation_id
               WHERE manifest.scenario_revision_id = ? AND manifest.included_flag = 1
                 AND eligibility.analytical_role = 'Economic Age'
                 AND eligibility.eligibility_status = 'Historical Context Only'""",
            (scenario_revision_id,),
        ).fetchone()[0]
        if context_only_included:
            raise ValueError("Historical-context evidence cannot be included in a finalized analysis")
        baseline_id = connection.execute(
            "SELECT gst_baseline_id FROM scenario_revision WHERE scenario_revision_id = ?",
            (scenario_revision_id,),
        ).fetchone()[0]
        if baseline_id is not None:
            blocking_take_rates = connection.execute(
                """SELECT COUNT(*) FROM take_rate_validation tv
                   WHERE tv.gst_baseline_id = ? AND tv.severity = 'Blocking Finalization'
                     AND NOT EXISTS (
                         SELECT 1 FROM v_current_take_rate_decision td
                         WHERE td.take_rate_validation_id = tv.take_rate_validation_id
                           AND td.decision_code IN ('Accepted Exception', 'Corrected Baseline'))""",
                (baseline_id,),
            ).fetchone()[0]
            if blocking_take_rates:
                raise ValueError("Take-rate review must be resolved or accepted before finalization")
        action_rows = connection.execute(
            """SELECT ba.buyer_action_id, av.buyer_action_version_id,
                      av.action_status, av.required_supplier_action,
                      av.owner_user_id, av.resolution_reason
               FROM buyer_action ba
               JOIN v_current_buyer_action av ON av.buyer_action_id = ba.buyer_action_id
               JOIN analysis a ON a.event_id = ba.event_id
               WHERE a.analysis_id = ?""",
            (analysis_id,),
        ).fetchall()
        open_action_count = sum(row[2] not in ("Resolved", "Accepted Exception") for row in action_rows)
        manifest_rows = connection.execute(
            """SELECT evidence_entity_type, evidence_entity_id, included_flag,
                      analytical_role, exclusion_reason
               FROM evidence_manifest_entry WHERE scenario_revision_id = ?
               ORDER BY evidence_entity_type, evidence_entity_id, analytical_role""",
            (scenario_revision_id,),
        ).fetchall()
        manifest_hash = hashlib.sha256(
            canonical_json([tuple(row) for row in manifest_rows]).encode("utf-8")
        ).hexdigest()
        finalizing_id = uuid7()
        connection.execute(
            """INSERT INTO analysis_status_event VALUES
               (?, ?, 'Finalizing', 'Finalization checks passed', ?, ?, ?)""",
            (finalizing_id, analysis_id, finalized_by_user_id,
             analysis_status[0], finalized_at_utc),
        )
        _, _, audit_anchor = append_audit_event(
            connection, audit,
            {"finalized_snapshot_id": snapshot_id, "analysis_id": analysis_id, "scenario_revision_id": scenario_revision_id, "calculation_run_id": calculation_run_id, "open_action_count": open_action_count, "manifest_hash": manifest_hash},
        )
        connection.execute(
            """INSERT INTO finalized_snapshot VALUES
               (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                snapshot_id, analysis_id, scenario_revision_id,
                calculation_run_id, open_action_count, manifest_hash,
                audit_anchor, finalized_by_user_id, finalized_at_utc,
            ),
        )
        results = connection.execute(
            "SELECT * FROM calculation_result WHERE calculation_run_id = ?",
            (calculation_run_id,),
        ).fetchall()
        for result in results:
            connection.execute(
                """INSERT INTO snapshot_result
                   (snapshot_result_id, finalized_snapshot_id, result_code,
                    supplier_id, part_id, program_year, category_code,
                    decimal_coefficient, decimal_scale, normalized_unit_id,
                    currency_id, presentation_rule_version_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    uuid7(), snapshot_id, result["result_code"],
                    result["supplier_id"], result["part_id"],
                    result["program_year"], result["category_code"],
                    result["decimal_coefficient"], result["decimal_scale"],
                    result["normalized_unit_id"], result["currency_id"],
                    presentation_rule_version_id,
                ),
            )
        for action in action_rows:
            connection.execute(
                "INSERT INTO snapshot_action VALUES (?, ?, ?, ?, ?, ?)",
                (
                    uuid7(), snapshot_id, action[0], action[1], action[2],
                    canonical_json({"required_supplier_action": action[3], "owner_user_id": action[4], "resolution_reason": action[5]}),
                ),
            )
        connection.execute(
            """INSERT INTO analysis_status_event VALUES
               (?, ?, 'Finalized', 'Immutable snapshot created', ?, ?, ?)""",
            (uuid7(), analysis_id, finalized_by_user_id,
             finalizing_id, finalized_at_utc),
        )
    return snapshot_id


def start_calculation_run(
    connection: sqlite3.Connection, *, scenario_revision_id: str,
    engine_version_id: str, calculation_rule_version_id: str,
    started_at_utc: str, audit: AuditContext,
) -> str:
    run_id = uuid7()
    with immediate_transaction(connection):
        manifest = connection.execute(
            """SELECT evidence_entity_type, evidence_entity_id, included_flag,
                      analytical_role, exclusion_reason
               FROM evidence_manifest_entry WHERE scenario_revision_id = ?
               ORDER BY evidence_entity_type, evidence_entity_id, analytical_role""",
            (scenario_revision_id,),
        ).fetchall()
        if not manifest:
            raise ValueError("Scenario revision has no evidence manifest")
        state = connection.execute(
            """SELECT status.analysis_status_event_id, status.analysis_status,
                      scenario.analysis_id
               FROM scenario_revision scenario JOIN v_current_analysis_status status
                 ON status.analysis_id = scenario.analysis_id
               WHERE scenario.scenario_revision_id = ?""", (scenario_revision_id,),
        ).fetchone()
        if state is None or state[1] not in {"Working", "Draft Revision Complete"}:
            raise ValueError("Analysis is not available for calculation")
        input_hash = hashlib.sha256(canonical_json([tuple(row) for row in manifest]).encode("utf-8")).hexdigest()
        connection.execute(
            """INSERT INTO calculation_run VALUES
               (?, ?, ?, ?, 'Started', ?, NULL, ?, NULL)""",
            (run_id, scenario_revision_id, engine_version_id,
             calculation_rule_version_id, input_hash, started_at_utc),
        )
        connection.execute(
            """INSERT INTO calculation_run_status_event VALUES
               (?, ?, 'Started', NULL, 'Calculation worker started', NULL, ?)""",
            (uuid7(), run_id, started_at_utc),
        )
        connection.execute(
            """INSERT INTO analysis_status_event VALUES
               (?, ?, 'Calculating', 'Calculation worker started', NULL, ?, ?)""",
            (uuid7(), state[2], state[0], started_at_utc),
        )
        append_audit_event(connection, audit, {"calculation_run_id": run_id,
            "scenario_revision_id": scenario_revision_id, "run_status": "Started"})
    return run_id


def terminate_calculation_run(
    connection: sqlite3.Connection, *, calculation_run_id: str,
    outcome: str, status_detail: str, completed_at_utc: str, audit: AuditContext,
) -> str:
    if outcome not in {"Failed", "Cancelled"} or not status_detail.strip():
        raise ValueError("Run termination requires Failed/Cancelled and detail")
    with immediate_transaction(connection):
        state = connection.execute(
            """SELECT run_status_event.calculation_run_status_event_id,
                      run_status_event.run_status, scenario.analysis_id,
                      analysis_status.analysis_status_event_id
               FROM calculation_run run
               JOIN v_current_calculation_run_status run_status_event
                 ON run_status_event.calculation_run_id = run.calculation_run_id
               JOIN scenario_revision scenario ON scenario.scenario_revision_id = run.scenario_revision_id
               JOIN v_current_analysis_status analysis_status ON analysis_status.analysis_id = scenario.analysis_id
               WHERE run.calculation_run_id = ?""", (calculation_run_id,),
        ).fetchone()
        if state is None or state[1] != "Started":
            raise ValueError("Only a started calculation run may be terminated")
        connection.execute(
            """INSERT INTO calculation_run_status_event VALUES
               (?, ?, ?, NULL, ?, ?, ?)""",
            (uuid7(), calculation_run_id, outcome, status_detail.strip(), state[0], completed_at_utc),
        )
        connection.execute(
            """INSERT INTO analysis_status_event VALUES (?, ?, ?, ?, NULL, ?, ?)""",
            (uuid7(), state[2], outcome, status_detail.strip(), state[3], completed_at_utc),
        )
        append_audit_event(connection, audit, {"calculation_run_id": calculation_run_id,
            "run_status": outcome, "status_detail": status_detail.strip()})
    return outcome
