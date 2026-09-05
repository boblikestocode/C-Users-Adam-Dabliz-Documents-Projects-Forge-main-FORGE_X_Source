from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any

from .audit import AuditContext, append_audit_event, canonical_json
from .connection import immediate_transaction
from .decimals import ExactDecimal
from .ids import uuid7


COST_TYPES = {"SFT", "PST", "ED&D"}
TREATMENTS = {
    "Separate Lump Sum", "Amortized in Piece Price",
    "Partially Amortized", "Treatment Unconfirmed",
}


@dataclass(frozen=True)
class PaymentTreatment:
    classification: str
    upfront_amount: ExactDecimal | None = None
    embedded_per_part: ExactDecimal | None = None
    allocation_basis: dict[str, Any] | None = None


def record_payment_cost_evidence(
    connection: sqlite3.Connection,
    *,
    observation_id: str,
    source_datum_id: str,
    cost_type: str,
    program_year: int,
    submitted_wording: str,
    submitted_amount: ExactDecimal,
    normalized_unit_id: str,
    currency_id: str | None,
    treatment_rule_version_id: str,
    recorded_at_utc: str,
    audit: AuditContext,
) -> str:
    if cost_type not in COST_TYPES:
        raise ValueError("Unsupported payment cost type")
    if not submitted_wording.strip():
        raise ValueError("Supplier-submitted wording cannot be blank")
    if not 1900 <= program_year <= 2200:
        raise ValueError("Payment-cost program year is outside the supported range")
    evidence_id, treatment_id = uuid7(), uuid7()
    with immediate_transaction(connection):
        source = connection.execute(
            """SELECT 1 FROM source_datum datum
               JOIN source_occurrence occurrence ON occurrence.worksheet_id = datum.worksheet_id
               JOIN pbd_observation observation ON observation.occurrence_id = occurrence.occurrence_id
               WHERE datum.source_datum_id = ? AND observation.observation_id = ?""",
            (source_datum_id, observation_id),
        ).fetchone()
        if source is None:
            raise ValueError("Source datum must belong to the payment-cost observation")
        if connection.execute(
            "SELECT 1 FROM rule_version WHERE rule_version_id = ?", (treatment_rule_version_id,)
        ).fetchone() is None:
            raise ValueError("Treatment rule version does not exist")
        connection.execute(
            """INSERT INTO payment_cost_evidence VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (evidence_id, observation_id, source_datum_id, cost_type, program_year, submitted_wording,
             submitted_amount.submitted_lexeme, submitted_amount.coefficient,
             submitted_amount.scale, normalized_unit_id, currency_id, recorded_at_utc),
        )
        connection.execute(
            """INSERT INTO payment_treatment_version
               (payment_treatment_version_id, payment_cost_evidence_id,
                treatment_classification, treatment_rule_version_id, recorded_at_utc)
               VALUES (?, ?, 'Treatment Unconfirmed', ?, ?)""",
            (treatment_id, evidence_id, treatment_rule_version_id, recorded_at_utc),
        )
        append_audit_event(connection, audit, {
            "payment_cost_evidence_id": evidence_id, "observation_id": observation_id,
            "cost_type": cost_type, "program_year": program_year,
            "treatment": "Treatment Unconfirmed",
        })
    return evidence_id


def confirm_payment_treatment(
    connection: sqlite3.Connection,
    *,
    payment_cost_evidence_id: str,
    treatment: PaymentTreatment,
    treatment_rule_version_id: str,
    confirmed_by_user_id: str,
    confirmation_reason: str,
    recorded_at_utc: str,
    audit: AuditContext,
) -> str:
    if treatment.classification not in TREATMENTS:
        raise ValueError("Unsupported payment treatment")
    if not confirmation_reason.strip():
        raise ValueError("Payment treatment confirmation requires a reason")
    if treatment.classification == "Partially Amortized" and (
        treatment.upfront_amount is None or treatment.embedded_per_part is None
        or treatment.allocation_basis is None
    ):
        raise ValueError("Partially Amortized treatment requires upfront, per-part, and allocation-basis evidence")
    version_id = uuid7()
    with immediate_transaction(connection):
        current = connection.execute(
            "SELECT * FROM v_current_payment_treatment WHERE payment_cost_evidence_id = ?",
            (payment_cost_evidence_id,),
        ).fetchone()
        if current is None:
            raise ValueError("Payment-cost evidence does not exist")
        connection.execute(
            """INSERT INTO payment_treatment_version
               (payment_treatment_version_id, payment_cost_evidence_id,
                treatment_classification, upfront_coefficient, upfront_scale,
                embedded_per_part_coefficient, embedded_per_part_scale,
                allocation_basis_payload, treatment_rule_version_id,
                confirmed_by_user_id, confirmation_reason, recorded_at_utc,
                supersedes_treatment_version_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                version_id, payment_cost_evidence_id, treatment.classification,
                treatment.upfront_amount.coefficient if treatment.upfront_amount else None,
                treatment.upfront_amount.scale if treatment.upfront_amount else None,
                treatment.embedded_per_part.coefficient if treatment.embedded_per_part else None,
                treatment.embedded_per_part.scale if treatment.embedded_per_part else None,
                canonical_json(treatment.allocation_basis) if treatment.allocation_basis is not None else None,
                treatment_rule_version_id, confirmed_by_user_id, confirmation_reason,
                recorded_at_utc, current["payment_treatment_version_id"],
            ),
        )
        append_audit_event(connection, audit, {
            "payment_cost_evidence_id": payment_cost_evidence_id,
            "payment_treatment_version_id": version_id,
            "before_treatment": current["treatment_classification"],
            "after_treatment": treatment.classification,
            "confirmation_reason": confirmation_reason,
        })
    return version_id


def observations_with_unconfirmed_payment_treatment(
    connection: sqlite3.Connection,
) -> tuple[str, ...]:
    return tuple(row[0] for row in connection.execute(
        """SELECT DISTINCT evidence.observation_id
           FROM payment_cost_evidence evidence
           JOIN v_current_payment_treatment treatment
             ON treatment.payment_cost_evidence_id = evidence.payment_cost_evidence_id
           WHERE treatment.treatment_classification = 'Treatment Unconfirmed'
           ORDER BY evidence.observation_id"""
    ).fetchall())
