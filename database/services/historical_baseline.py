from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass, replace
from datetime import date

from .audit import AuditContext, append_audit_event, canonical_json
from .connection import immediate_transaction
from .ids import uuid7
from .ingestion import ObservationCommit, commit_observation


@dataclass(frozen=True)
class HistoricalBaselineSummary:
    summary_id: str
    imported_record_count: int
    duplicate_count: int
    incomplete_pbd_count: int
    formula_exception_count: int
    staging_exception_count: int
    missing_fields_by_supplier: dict[str, tuple[str, ...]]
    summary_hash: str


@dataclass(frozen=True)
class HistoricalBaselineComparison:
    comparison_id: str
    baseline_observation_id: str
    later_observation_id: str
    structure_trend: str
    field_coverage_trend: str
    formula_integrity_trend: str
    comparison_hash: str


def commit_historical_baseline_observation(
    connection: sqlite3.Connection, *, staged_observation_id: str,
    observation: ObservationCommit, first_analyzed_date: str,
    recorded_at_utc: str, audit: AuditContext,
) -> str:
    if observation.observation_context != "Historical Baseline":
        raise ValueError("Historical baseline commit requires Historical Baseline context")
    date.fromisoformat(first_analyzed_date)
    effective = observation
    if observation.economic_date is None or observation.economic_date_precision == "Unknown":
        effective = replace(observation, economic_date=first_analyzed_date,
                            economic_date_precision="Day")
    return commit_observation(
        connection, staged_observation_id=staged_observation_id,
        observation=effective, recorded_at_utc=recorded_at_utc, audit=audit,
    )


def historical_baseline_summary_payload(
    connection: sqlite3.Connection, *, import_transaction_id: str,
    first_analyzed_date: str,
) -> dict[str, object]:
    date.fromisoformat(first_analyzed_date)
    transaction = connection.execute(
        """SELECT session.import_context_type, status.transaction_status,
                  transaction_row.committed_count, transaction_row.duplicate_count
           FROM import_transaction transaction_row
           JOIN import_session session ON session.import_session_id = transaction_row.import_session_id
           JOIN v_current_import_transaction_status status
             ON status.import_transaction_id = transaction_row.import_transaction_id
           WHERE transaction_row.import_transaction_id = ?""", (import_transaction_id,),
    ).fetchone()
    if transaction is None or transaction[0] != "Historical Baseline" or transaction[1] != "Committed":
        raise ValueError("Completion summary requires a committed Historical Baseline transaction")
    observations = connection.execute(
        """SELECT observation.observation_id, observation.submitted_supplier_name,
                  observation.structure_category
           FROM pbd_observation observation
           JOIN source_occurrence occurrence ON occurrence.occurrence_id = observation.occurrence_id
           JOIN source_worksheet worksheet ON worksheet.worksheet_id = occurrence.worksheet_id
           JOIN source_workbook workbook ON workbook.workbook_id = worksheet.workbook_id
           WHERE workbook.import_transaction_id = ? ORDER BY observation.observation_id""",
        (import_transaction_id,),
    ).fetchall()
    global_fields = {str(row[0]) for row in connection.execute(
        """SELECT DISTINCT datum.field_code FROM submitted_datum datum
           JOIN pbd_observation observation ON observation.observation_id = datum.observation_id
           JOIN source_occurrence occurrence ON occurrence.occurrence_id = observation.occurrence_id
           JOIN source_worksheet worksheet ON worksheet.worksheet_id = occurrence.worksheet_id
           JOIN source_workbook workbook ON workbook.workbook_id = worksheet.workbook_id
           WHERE workbook.import_transaction_id = ?""", (import_transaction_id,),
    )}
    supplier_fields: dict[str, set[str]] = {}
    for observation_id, supplier_name, _ in observations:
        supplier_fields.setdefault(str(supplier_name), set()).update(
            str(row[0]) for row in connection.execute(
                "SELECT field_code FROM submitted_datum WHERE observation_id = ?",
                (observation_id,),
            )
        )
    missing = {supplier: tuple(sorted(global_fields - fields))
               for supplier, fields in sorted(supplier_fields.items())}
    incomplete = sum(row[2] == "Incomplete" for row in observations)
    formula_exceptions = sum(row[2] == "Formula Reconciliation Exception" for row in observations)
    staging_exceptions = int(connection.execute(
        """SELECT COUNT(*) FROM staging_issue issue
           JOIN staged_observation staged ON staged.staged_observation_id = issue.staged_observation_id
           JOIN source_occurrence occurrence ON occurrence.occurrence_id = staged.occurrence_id
           JOIN source_worksheet worksheet ON worksheet.worksheet_id = occurrence.worksheet_id
           JOIN source_workbook workbook ON workbook.workbook_id = worksheet.workbook_id
           WHERE workbook.import_transaction_id = ?""", (import_transaction_id,),
    ).fetchone()[0])
    payload = {
        "import_transaction_id": import_transaction_id,
        "first_analyzed_date": first_analyzed_date,
        "imported_record_count": len(observations),
        "duplicate_count": int(transaction[3]),
        "incomplete_pbd_count": incomplete,
        "formula_exception_count": formula_exceptions,
        "staging_exception_count": staging_exceptions,
        "comparison_field_population": tuple(sorted(global_fields)),
        "missing_fields_by_supplier": missing,
    }
    return payload


def record_historical_baseline_completion(
    connection: sqlite3.Connection, *, import_transaction_id: str,
    first_analyzed_date: str, generated_at_utc: str, audit: AuditContext,
) -> HistoricalBaselineSummary:
    payload = historical_baseline_summary_payload(
        connection, import_transaction_id=import_transaction_id,
        first_analyzed_date=first_analyzed_date,
    )
    digest = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
    summary_id = uuid7()
    with immediate_transaction(connection):
        connection.execute(
            """INSERT INTO historical_baseline_completion_summary VALUES
               (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (summary_id, import_transaction_id, first_analyzed_date,
             payload["imported_record_count"], payload["duplicate_count"],
             payload["incomplete_pbd_count"], payload["formula_exception_count"],
             payload["staging_exception_count"],
             canonical_json(payload["missing_fields_by_supplier"]), digest, generated_at_utc),
        )
        append_audit_event(connection, audit, {
            "historical_baseline_completion_summary_id": summary_id,
            "summary_hash": digest, **payload,
        })
    return HistoricalBaselineSummary(
        summary_id, int(payload["imported_record_count"]),
        int(payload["duplicate_count"]), int(payload["incomplete_pbd_count"]),
        int(payload["formula_exception_count"]), int(payload["staging_exception_count"]),
        dict(payload["missing_fields_by_supplier"]), digest,
    )


def compare_to_historical_baseline(
    connection: sqlite3.Connection, *, summary_id: str,
    later_observation_id: str, comparison_rule_version_id: str,
    generated_at_utc: str, audit: AuditContext,
) -> HistoricalBaselineComparison:
    summary = connection.execute(
        "SELECT import_transaction_id FROM historical_baseline_completion_summary WHERE historical_baseline_completion_summary_id = ?",
        (summary_id,),
    ).fetchone()
    later = connection.execute(
        "SELECT * FROM v_effective_pbd_observation WHERE observation_id = ?",
        (later_observation_id,),
    ).fetchone()
    if summary is None or later is None or later["supplier_id"] is None or later["part_id"] is None:
        raise ValueError("Baseline comparison requires summary and confirmed later supplier/part identity")
    if connection.execute(
        "SELECT 1 FROM rule_version WHERE rule_version_id = ?", (comparison_rule_version_id,),
    ).fetchone() is None:
        raise ValueError("Baseline comparison rule version does not exist")
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
    if baseline is None or baseline["observation_id"] == later_observation_id:
        raise ValueError("No distinct matching baseline supplier/part observation exists")
    structure_scores = {"Detailed and Reconciled": 2, "Valid Aggregate": 2,
                        "Formula Reconciliation Exception": 1, "Incomplete": 0}
    before_score = structure_scores[str(baseline["structure_category"])]
    after_score = structure_scores[str(later["structure_category"])]
    structure_trend = "Improved" if after_score > before_score else (
        "Deteriorated" if after_score < before_score else "Unchanged")
    def fields(observation_id: str) -> set[str]:
        return {str(row[0]) for row in connection.execute(
            "SELECT DISTINCT field_code FROM submitted_datum WHERE observation_id = ?",
            (observation_id,),
        )}
    baseline_fields = fields(str(baseline["observation_id"]))
    later_fields = fields(later_observation_id)
    added, removed = sorted(later_fields - baseline_fields), sorted(baseline_fields - later_fields)
    field_trend = ("Unchanged" if not added and not removed else
                   "Improved" if added and not removed else
                   "Deteriorated" if removed and not added else "Mixed")
    def formula_exceptions(observation_id: str) -> int:
        return int(connection.execute(
            """SELECT COUNT(*) FROM formula_integrity_event
               WHERE observation_id = ? AND classification = 'Formula Reconciliation Exception'""",
            (observation_id,),
        ).fetchone()[0])
    before_formula = formula_exceptions(str(baseline["observation_id"]))
    after_formula = formula_exceptions(later_observation_id)
    formula_trend = "Improved" if after_formula < before_formula else (
        "Deteriorated" if after_formula > before_formula else "Unchanged")
    payload = {
        "summary_id": summary_id, "baseline_observation_id": baseline["observation_id"],
        "later_observation_id": later_observation_id, "supplier_id": later["supplier_id"],
        "part_id": later["part_id"], "baseline_structure_category": baseline["structure_category"],
        "later_structure_category": later["structure_category"], "structure_trend": structure_trend,
        "added_fields": added, "removed_fields": removed, "field_coverage_trend": field_trend,
        "baseline_formula_exception_count": before_formula,
        "later_formula_exception_count": after_formula, "formula_integrity_trend": formula_trend,
        "comparison_rule_version_id": comparison_rule_version_id,
    }
    digest = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
    comparison_id = uuid7()
    with immediate_transaction(connection):
        connection.execute(
            """INSERT INTO historical_baseline_observation_comparison VALUES
               (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (comparison_id, summary_id, baseline["observation_id"], later_observation_id,
             later["supplier_id"], later["part_id"], baseline["structure_category"],
             later["structure_category"], structure_trend, canonical_json(added),
             canonical_json(removed), field_trend, before_formula, after_formula,
             formula_trend, comparison_rule_version_id, digest, generated_at_utc),
        )
        append_audit_event(connection, audit, {
            "historical_baseline_observation_comparison_id": comparison_id,
            "comparison_hash": digest, **payload,
        })
    return HistoricalBaselineComparison(comparison_id, str(baseline["observation_id"]),
                                        later_observation_id, structure_trend,
                                        field_trend, formula_trend, digest)
