from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any

from .audit import AuditContext, append_audit_event, canonical_json
from .connection import immediate_transaction
from .ids import uuid7


SEVERITIES = {"Information", "Review Required", "Blocking Finalization"}
DECISIONS = {"Accepted Exception", "Corrected Baseline", "Still Under Review"}


@dataclass(frozen=True)
class TakeRateIssue:
    validation_type: str
    severity: str
    affected_population: dict[str, Any]
    validation_result: dict[str, Any]


def record_take_rate_validation(
    connection: sqlite3.Connection,
    *,
    gst_baseline_id: str,
    validation_rule_version_id: str,
    issue: TakeRateIssue,
    recorded_at_utc: str,
    audit: AuditContext,
) -> str:
    if issue.severity not in SEVERITIES:
        raise ValueError("Unsupported take-rate validation severity")
    if not issue.validation_type.strip():
        raise ValueError("Take-rate validation type cannot be blank")
    validation_id = uuid7()
    with immediate_transaction(connection):
        if connection.execute(
            "SELECT 1 FROM gst_baseline WHERE gst_baseline_id = ?", (gst_baseline_id,)
        ).fetchone() is None:
            raise ValueError("GST baseline does not exist")
        if connection.execute(
            "SELECT 1 FROM rule_version WHERE rule_version_id = ?", (validation_rule_version_id,)
        ).fetchone() is None:
            raise ValueError("Validation rule version does not exist")
        connection.execute(
            """INSERT INTO take_rate_validation
               (take_rate_validation_id, gst_baseline_id, validation_rule_version_id,
                validation_type, severity, affected_population, validation_result,
                recorded_at_utc)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                validation_id, gst_baseline_id, validation_rule_version_id,
                issue.validation_type, issue.severity,
                canonical_json(issue.affected_population),
                canonical_json(issue.validation_result), recorded_at_utc,
            ),
        )
        append_audit_event(connection, audit, {
            "take_rate_validation_id": validation_id,
            "gst_baseline_id": gst_baseline_id,
            "validation_type": issue.validation_type,
            "severity": issue.severity,
            "affected_population": issue.affected_population,
            "validation_result": issue.validation_result,
        })
    return validation_id


def decide_take_rate_validation(
    connection: sqlite3.Connection,
    *,
    take_rate_validation_id: str,
    decision_code: str,
    decided_by_user_id: str,
    decision_reason: str,
    recorded_at_utc: str,
    audit: AuditContext,
    replacement_gst_baseline_id: str | None = None,
) -> str:
    if decision_code not in DECISIONS:
        raise ValueError("Unsupported take-rate decision")
    if not decision_reason.strip():
        raise ValueError("Take-rate decision requires a business rationale")
    if (decision_code == "Corrected Baseline") != (replacement_gst_baseline_id is not None):
        raise ValueError("Corrected Baseline requires exactly one replacement baseline")
    decision_id = uuid7()
    with immediate_transaction(connection):
        validation = connection.execute(
            "SELECT gst_baseline_id FROM take_rate_validation WHERE take_rate_validation_id = ?",
            (take_rate_validation_id,),
        ).fetchone()
        if validation is None:
            raise ValueError("Take-rate validation does not exist")
        prior = connection.execute(
            "SELECT decision_code FROM v_current_take_rate_decision WHERE take_rate_validation_id = ?",
            (take_rate_validation_id,),
        ).fetchone()
        if replacement_gst_baseline_id is not None:
            replacement = connection.execute(
                """SELECT predecessor_baseline_id, source_package_id
                   FROM gst_baseline WHERE gst_baseline_id = ?""",
                (replacement_gst_baseline_id,),
            ).fetchone()
            original_package = connection.execute(
                "SELECT source_package_id FROM gst_baseline WHERE gst_baseline_id = ?",
                (validation[0],),
            ).fetchone()[0]
            if replacement is None or replacement[0] != validation[0] or replacement[1] != original_package:
                raise ValueError("Replacement baseline must directly supersede the validated baseline in the same source package")
        connection.execute(
            """INSERT INTO take_rate_decision
               (take_rate_decision_id, take_rate_validation_id, decision_code,
                replacement_gst_baseline_id, decided_by_user_id, decision_reason,
                recorded_at_utc)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                decision_id, take_rate_validation_id, decision_code,
                replacement_gst_baseline_id, decided_by_user_id,
                decision_reason, recorded_at_utc,
            ),
        )
        append_audit_event(connection, audit, {
            "take_rate_validation_id": take_rate_validation_id,
            "take_rate_decision_id": decision_id,
            "before_decision": None if prior is None else prior[0],
            "after_decision": decision_code,
            "replacement_gst_baseline_id": replacement_gst_baseline_id,
            "decision_reason": decision_reason,
        })
    return decision_id


def unresolved_blocking_take_rates(
    connection: sqlite3.Connection, gst_baseline_id: str
) -> tuple[sqlite3.Row, ...]:
    return tuple(connection.execute(
        """SELECT validation.*
           FROM take_rate_validation validation
           LEFT JOIN v_current_take_rate_decision decision
             ON decision.take_rate_validation_id = validation.take_rate_validation_id
           WHERE validation.gst_baseline_id = ?
             AND validation.severity = 'Blocking Finalization'
             AND COALESCE(decision.decision_code, 'Still Under Review')
                 NOT IN ('Accepted Exception', 'Corrected Baseline')
           ORDER BY validation.recorded_at_utc, validation.take_rate_validation_id""",
        (gst_baseline_id,),
    ).fetchall())
