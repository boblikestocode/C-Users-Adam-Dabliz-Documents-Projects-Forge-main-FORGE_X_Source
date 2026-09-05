from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from .integrity import assess_analysis_readiness


@dataclass(frozen=True)
class WorkflowFinding:
    code: str
    severity: str
    message: str
    count: int = 1


@dataclass(frozen=True)
class EventWorkflowStatus:
    event_id: str
    event_status: str
    scope_ready: bool
    analysis_ready: bool
    finalization_ready: bool
    publication_ready: bool
    active_supplier_count: int
    confirmed_baseline_id: str | None
    open_action_count: int
    findings: tuple[WorkflowFinding, ...]


def assess_event_workflow(
    connection: sqlite3.Connection, *, event_id: str
) -> EventWorkflowStatus:
    event = connection.execute(
        "SELECT event_status FROM v_current_sourcing_event_status WHERE event_id = ?", (event_id,)
    ).fetchone()
    if event is None:
        raise ValueError("Sourcing event does not exist")
    findings: list[WorkflowFinding] = []
    scope_count = int(connection.execute(
        """SELECT COUNT(*) FROM source_package package
           JOIN v_current_event_scope scope ON scope.source_package_id = package.source_package_id
           WHERE package.event_id = ?""", (event_id,)
    ).fetchone()[0])
    if scope_count == 0:
        findings.append(WorkflowFinding("ACTIVE_SCOPE_MISSING", "Blocking", "No active source-package scope is available."))
    elif scope_count > 1:
        findings.append(WorkflowFinding("MULTIPLE_ACTIVE_SCOPES", "Blocking", "Multiple source packages have active scopes.", scope_count))
    baseline = connection.execute(
        """SELECT baseline.gst_baseline_id
           FROM gst_baseline baseline
           JOIN source_package package ON package.source_package_id = baseline.source_package_id
           WHERE package.event_id = ? AND baseline.baseline_status = 'Confirmed'
           ORDER BY baseline.baseline_version DESC, baseline.gst_baseline_id DESC LIMIT 1""",
        (event_id,),
    ).fetchone()
    baseline_id = None if baseline is None else str(baseline[0])
    if baseline_id is None:
        findings.append(WorkflowFinding("CONFIRMED_GST_BASELINE_MISSING", "Blocking", "A confirmed GST baseline is required for governing analysis."))
    active_supplier_count = int(connection.execute(
        "SELECT COUNT(*) FROM v_active_supplier_round WHERE event_id = ?", (event_id,)
    ).fetchone()[0])
    if active_supplier_count == 0:
        findings.append(WorkflowFinding("ACTIVE_SUPPLIER_ROUND_MISSING", "Blocking", "At least one supplier must have an active quote round."))
    unresolved_identity = int(connection.execute(
            """SELECT COUNT(*) FROM v_effective_pbd_observation
           WHERE observation_context = 'Sourcing Event' AND context_id = ?
             AND supplier_id IS NULL""", (event_id,)
    ).fetchone()[0])
    if unresolved_identity:
        findings.append(WorkflowFinding(
            "SUPPLIER_IDENTITY_UNRESOLVED", "Finalization Blocking",
            "Supplier identity must be confirmed before finalization.", unresolved_identity,
        ))
    unresolved_take_rates = 0
    if baseline_id is not None:
        unresolved_take_rates = int(connection.execute(
            """SELECT COUNT(*) FROM take_rate_validation validation
               LEFT JOIN v_current_take_rate_decision decision
                 ON decision.take_rate_validation_id = validation.take_rate_validation_id
               WHERE validation.gst_baseline_id = ?
                 AND validation.severity = 'Blocking Finalization'
                 AND COALESCE(decision.decision_code, 'Still Under Review')
                     NOT IN ('Accepted Exception', 'Corrected Baseline')""",
            (baseline_id,),
        ).fetchone()[0])
    if unresolved_take_rates:
        findings.append(WorkflowFinding(
            "TAKE_RATE_REVIEW_UNRESOLVED", "Finalization Blocking",
            "Blocking take-rate anomalies require correction or an accepted exception.",
            unresolved_take_rates,
        ))
    unconfirmed_payment = int(connection.execute(
        """SELECT COUNT(*) FROM payment_cost_evidence evidence
           JOIN v_current_payment_treatment treatment
             ON treatment.payment_cost_evidence_id = evidence.payment_cost_evidence_id
           JOIN pbd_observation observation ON observation.observation_id = evidence.observation_id
           WHERE observation.observation_context = 'Sourcing Event'
             AND observation.context_id = ?
             AND treatment.treatment_classification = 'Treatment Unconfirmed'""",
        (event_id,),
    ).fetchone()[0])
    if unconfirmed_payment:
        findings.append(WorkflowFinding(
            "PAYMENT_TREATMENT_UNCONFIRMED", "Advisory",
            "Unaffected measures may proceed; total-program cost and NPV remain incomplete.",
            unconfirmed_payment,
        ))
    open_action_count = int(connection.execute(
        """SELECT COUNT(*) FROM buyer_action action
           JOIN v_current_buyer_action version ON version.buyer_action_id = action.buyer_action_id
           WHERE action.event_id = ? AND version.action_status NOT IN ('Resolved', 'Accepted Exception')""",
        (event_id,),
    ).fetchone()[0])
    if open_action_count:
        findings.append(WorkflowFinding(
            "BUYER_ACTIONS_OPEN", "Advisory",
            "Open buyer actions are disclosed but do not by themselves block finalization.",
            open_action_count,
        ))
    database_readiness = assess_analysis_readiness(connection)
    if not database_readiness.can_proceed:
        findings.append(WorkflowFinding(
            "DATABASE_INTEGRITY_NOT_READY", "Blocking",
            "Database health checks must pass before analysis or publication.",
            len(database_readiness.blocking_findings),
        ))
    scope_ready = scope_count == 1
    analysis_ready = (
        scope_ready and baseline_id is not None and active_supplier_count > 0
        and database_readiness.can_proceed
    )
    finalization_ready = analysis_ready and unresolved_identity == 0 and unresolved_take_rates == 0
    publication_conflicts = int(connection.execute(
        "SELECT COUNT(*) FROM sync_conflict WHERE conflict_status = 'Open'"
    ).fetchone()[0])
    if publication_conflicts:
        findings.append(WorkflowFinding(
            "PUBLICATION_CONFLICT_OPEN", "Publication Blocking",
            "SharePoint publication conflicts require governed reconciliation.",
            publication_conflicts,
        ))
    return EventWorkflowStatus(
        event_id=event_id, event_status=str(event[0]), scope_ready=scope_ready,
        analysis_ready=analysis_ready, finalization_ready=finalization_ready,
        publication_ready=finalization_ready and publication_conflicts == 0,
        active_supplier_count=active_supplier_count,
        confirmed_baseline_id=baseline_id, open_action_count=open_action_count,
        findings=tuple(findings),
    )
