from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass

from .audit import AuditContext, append_audit_event, canonical_json
from .connection import immediate_transaction
from .ids import uuid7


STATUSES = {"Green", "Yellow", "Red", "Gray"}


@dataclass(frozen=True)
class ImprovementAssessmentResult:
    assessment_id: str
    assessment_status: str
    trailing_competitive_population_count: int
    evidence_count: int


@dataclass(frozen=True)
class CurrentHistoricalContextResult:
    context_id: str
    current_event_status: str
    historical_profile_status: str
    historical_risk_notice: dict | None
    historical_finding_count: int


def _validate_evidence(
    connection: sqlite3.Connection, *, event_id: str, supplier_id: str,
    quote_population_id: str, evidence_entity_type: str,
    evidence_entity_id: str,
) -> None:
    if evidence_entity_type == "Quote Round":
        valid = connection.execute(
            """SELECT 1 FROM supplier_quote_round
               WHERE quote_round_id = ? AND quote_round_id = ?
                 AND event_id = ? AND supplier_id = ?""",
            (evidence_entity_id, quote_population_id, event_id, supplier_id),
        ).fetchone()
    elif evidence_entity_type == "Calculation Result":
        valid = connection.execute(
            """SELECT 1 FROM calculation_result result
               JOIN calculation_run run
                 ON run.calculation_run_id = result.calculation_run_id
               JOIN scenario_revision scenario
                 ON scenario.scenario_revision_id = run.scenario_revision_id
               JOIN analysis ON analysis.analysis_id = scenario.analysis_id
               WHERE result.calculation_result_id = ? AND result.supplier_id = ?
                 AND analysis.event_id = ? AND run.status = 'Completed'""",
            (evidence_entity_id, supplier_id, event_id),
        ).fetchone()
    else:
        raise ValueError("Competitiveness evidence must be a Quote Round or Calculation Result")
    if valid is None:
        raise ValueError("Competitiveness evidence does not match the supplier and event")


def confirm_supplier_competitiveness(
    connection: sqlite3.Connection, *, event_id: str, supplier_id: str,
    quote_population_id: str, competitiveness_status: str,
    evidence_entity_type: str, evidence_entity_id: str,
    explanation_payload: dict, confirmed_by_user_id: str,
    confirmed_at_utc: str, audit: AuditContext,
    supersedes_observation_id: str | None = None,
) -> str:
    if competitiveness_status not in STATUSES:
        raise ValueError("Unsupported competitiveness status")
    _validate_evidence(
        connection, event_id=event_id, supplier_id=supplier_id,
        quote_population_id=quote_population_id,
        evidence_entity_type=evidence_entity_type,
        evidence_entity_id=evidence_entity_id,
    )
    event = connection.execute(
        "SELECT commodity_id FROM sourcing_event WHERE event_id = ?", (event_id,),
    ).fetchone()
    if event is None:
        raise ValueError("Sourcing event does not exist")
    current = connection.execute(
        """SELECT observation.supplier_competitiveness_observation_id
           FROM supplier_competitiveness_observation observation
           WHERE observation.event_id = ? AND observation.supplier_id = ?
             AND observation.quote_population_id = ?
             AND NOT EXISTS (
                 SELECT 1 FROM supplier_competitiveness_observation newer
                 WHERE newer.supersedes_observation_id =
                       observation.supplier_competitiveness_observation_id)""",
        (event_id, supplier_id, quote_population_id),
    ).fetchone()
    current_id = None if current is None else str(current[0])
    if current_id != supersedes_observation_id:
        raise ValueError("Competitiveness correction must directly supersede current state")
    observation_id = uuid7()
    payload = canonical_json(explanation_payload)
    with immediate_transaction(connection):
        connection.execute(
            """INSERT INTO supplier_competitiveness_observation VALUES
               (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (observation_id, event_id, supplier_id, event[0], quote_population_id,
             competitiveness_status, evidence_entity_type, evidence_entity_id,
             payload, confirmed_by_user_id, confirmed_at_utc,
             supersedes_observation_id),
        )
        append_audit_event(connection, audit, {
            "supplier_competitiveness_observation_id": observation_id,
            "event_id": event_id, "supplier_id": supplier_id,
            "commodity_id": event[0], "quote_population_id": quote_population_id,
            "competitiveness_status": competitiveness_status,
            "evidence_entity_type": evidence_entity_type,
            "evidence_entity_id": evidence_entity_id,
            "supersedes_observation_id": supersedes_observation_id,
        })
    return observation_id


def improvement_evidence_rows(
    connection: sqlite3.Connection, *, supplier_id: str, commodity_id: str,
    evidence_cutoff_utc: str,
) -> list[sqlite3.Row]:
    return connection.execute(
        """SELECT observation.supplier_competitiveness_observation_id,
                  observation.event_id, observation.quote_population_id,
                  observation.competitiveness_status,
                  observation.evidence_entity_type, observation.evidence_entity_id,
                  observation.confirmed_at_utc
           FROM supplier_competitiveness_observation observation
           JOIN sourcing_event event ON event.event_id = observation.event_id
           WHERE observation.supplier_id = ? AND observation.commodity_id = ?
             AND observation.confirmed_at_utc <= ?
             AND NOT EXISTS (
                 SELECT 1 FROM supplier_competitiveness_observation newer
                 WHERE newer.supersedes_observation_id =
                       observation.supplier_competitiveness_observation_id
                   AND newer.confirmed_at_utc <= ?)
           ORDER BY event.created_at_utc, observation.confirmed_at_utc,
                    observation.supplier_competitiveness_observation_id""",
        (supplier_id, commodity_id, evidence_cutoff_utc, evidence_cutoff_utc),
    ).fetchall()


def derive_improvement_assessment(
    rows: list[sqlite3.Row],
) -> tuple[str, int, str]:
    statuses = [str(row["competitiveness_status"]) for row in rows]
    trailing = 0
    for status in reversed(statuses):
        if status != "Green":
            break
        trailing += 1
    if not statuses or (len(statuses) == 1 and statuses[0] == "Green"):
        assessment = "Insufficient Evidence"
    elif statuses[-1] != "Green":
        assessment = "No Current Improvement"
    elif trailing == len(statuses):
        assessment = "Consistently Competitive"
    elif trailing == 1:
        assessment = "Recent Improvement"
    else:
        assessment = "Sustained Improvement"
    evidence_payload = [
        {key: row[key] for key in (
            "supplier_competitiveness_observation_id", "event_id",
            "quote_population_id", "competitiveness_status",
            "evidence_entity_type", "evidence_entity_id", "confirmed_at_utc",
        )} for row in rows
    ]
    digest = hashlib.sha256(canonical_json(evidence_payload).encode("utf-8")).hexdigest()
    return assessment, trailing, digest


def build_supplier_improvement_assessment(
    connection: sqlite3.Connection, *, supplier_id: str, commodity_id: str,
    evidence_cutoff_utc: str, generated_at_utc: str, audit: AuditContext,
) -> ImprovementAssessmentResult:
    rows = improvement_evidence_rows(
        connection, supplier_id=supplier_id, commodity_id=commodity_id,
        evidence_cutoff_utc=evidence_cutoff_utc,
    )
    assessment, trailing, manifest_hash = derive_improvement_assessment(rows)
    assessment_id = uuid7()
    with immediate_transaction(connection):
        connection.execute(
            """INSERT INTO supplier_improvement_assessment VALUES
               (?, ?, ?, ?, ?, ?, ?, ?)""",
            (assessment_id, supplier_id, commodity_id, evidence_cutoff_utc,
             assessment, trailing, manifest_hash, generated_at_utc),
        )
        connection.executemany(
            "INSERT INTO supplier_improvement_evidence VALUES (?, ?, ?, ?)",
            [(uuid7(), assessment_id,
              row["supplier_competitiveness_observation_id"], ordinal)
             for ordinal, row in enumerate(rows)],
        )
        append_audit_event(connection, audit, {
            "supplier_improvement_assessment_id": assessment_id,
            "supplier_id": supplier_id, "commodity_id": commodity_id,
            "evidence_cutoff_utc": evidence_cutoff_utc,
            "assessment_status": assessment,
            "trailing_competitive_population_count": trailing,
            "evidence_manifest_hash": manifest_hash,
        })
    return ImprovementAssessmentResult(
        assessment_id, assessment, trailing, len(rows)
    )


def current_historical_context_payload(
    connection: sqlite3.Connection, *,
    supplier_competitiveness_observation_id: str,
    supplier_profile_run_id: str,
) -> tuple[sqlite3.Row, sqlite3.Row, list[sqlite3.Row], str, dict | None, str]:
    current = connection.execute(
        """SELECT observation.*, event.commodity_id AS event_commodity_id
           FROM supplier_competitiveness_observation observation
           JOIN sourcing_event event ON event.event_id = observation.event_id
           WHERE observation.supplier_competitiveness_observation_id = ?""",
        (supplier_competitiveness_observation_id,),
    ).fetchone()
    profile = connection.execute(
        "SELECT * FROM supplier_profile_run WHERE supplier_profile_run_id = ?",
        (supplier_profile_run_id,),
    ).fetchone()
    if current is None or profile is None:
        raise ValueError("Current competitiveness observation and supplier profile are required")
    if (current["supplier_id"] != profile["supplier_id"]
            or current["commodity_id"] != profile["commodity_id"]
            or profile["run_status"] != "Complete"):
        raise ValueError("Current competitiveness and historical profile scopes must match")
    findings = connection.execute(
        """SELECT supplier_profile_finding_id, finding_type, finding_status,
                  explanation_payload, evidence_count, independent_event_count
           FROM supplier_profile_finding
           WHERE supplier_profile_run_id = ? AND finding_status = 'Red'
           ORDER BY finding_type, supplier_profile_finding_id""",
        (supplier_profile_run_id,),
    ).fetchall()
    historical_status = "Red" if findings else "No Governing Red"
    notice: dict | None = None
    if current["competitiveness_status"] == "Green" and findings:
        notice = {
            "notice_type": "Historical Risk Notice",
            "statement": "Current Event Competitiveness remains Green; retained historical Red findings require continued monitoring.",
            "historical_finding_count": len(findings),
            "governing_finding_types": sorted({str(row["finding_type"]) for row in findings}),
            "supplier_profile_run_id": supplier_profile_run_id,
            "profile_evidence_cutoff_utc": profile["evidence_cutoff_utc"],
        }
    manifest = {
        "supplier_competitiveness_observation_id": supplier_competitiveness_observation_id,
        "supplier_profile_run_id": supplier_profile_run_id,
        "current_event_status": current["competitiveness_status"],
        "historical_profile_status": historical_status,
        "historical_risk_notice": notice,
        "historical_findings": [tuple(row) for row in findings],
    }
    manifest_hash = hashlib.sha256(
        canonical_json(manifest).encode("utf-8")
    ).hexdigest()
    return current, profile, findings, historical_status, notice, manifest_hash


def build_current_historical_context(
    connection: sqlite3.Connection, *,
    supplier_competitiveness_observation_id: str,
    supplier_profile_run_id: str, generated_at_utc: str,
    audit: AuditContext,
) -> CurrentHistoricalContextResult:
    current, _, findings, historical_status, notice, manifest_hash = (
        current_historical_context_payload(
            connection,
            supplier_competitiveness_observation_id=
                supplier_competitiveness_observation_id,
            supplier_profile_run_id=supplier_profile_run_id,
        )
    )
    context_id = uuid7()
    with immediate_transaction(connection):
        connection.execute(
            """INSERT INTO supplier_current_historical_context VALUES
               (?, ?, ?, ?, ?, ?, ?, ?)""",
            (context_id, supplier_competitiveness_observation_id,
             supplier_profile_run_id, current["competitiveness_status"],
             historical_status,
             None if notice is None else canonical_json(notice),
             manifest_hash, generated_at_utc),
        )
        connection.executemany(
            "INSERT INTO supplier_current_historical_finding VALUES (?, ?, ?, ?)",
            [(uuid7(), context_id, row["supplier_profile_finding_id"], ordinal)
             for ordinal, row in enumerate(findings)],
        )
        append_audit_event(connection, audit, {
            "supplier_current_historical_context_id": context_id,
            "supplier_competitiveness_observation_id":
                supplier_competitiveness_observation_id,
            "supplier_profile_run_id": supplier_profile_run_id,
            "current_event_status": current["competitiveness_status"],
            "historical_profile_status": historical_status,
            "historical_risk_notice": notice,
            "context_manifest_hash": manifest_hash,
        })
    return CurrentHistoricalContextResult(
        context_id, str(current["competitiveness_status"]), historical_status,
        notice, len(findings),
    )
