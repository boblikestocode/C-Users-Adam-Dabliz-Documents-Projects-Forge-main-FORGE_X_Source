from __future__ import annotations

import sqlite3
import hashlib
from dataclasses import dataclass
from decimal import Decimal

from .audit import AuditContext, append_audit_event, canonical_json
from .connection import immediate_transaction
from .decimals import ExactDecimal
from .ids import uuid7


CLASSIFICATIONS = {
    "Initial Quote", "Requote", "Correction", "Final Offer", "Unclassified",
}

DEFAULT_MOVEMENT_FIELDS = (
    "PIECE_PRICE", "LABOR_DOLLARS", "BURDEN_DOLLARS",
    "FACTORY_OVERHEAD_DOLLARS", "HEAD_OFFICE_OVERHEAD_DOLLARS",
    "R_AND_D_OVERHEAD_DOLLARS", "OVERHEAD_DOLLARS", "PROFIT_DOLLARS",
    "PURCHASED_COMPONENTS", "OTHER_COST",
)


@dataclass(frozen=True)
class QuoteVersionCandidate:
    candidate_id: str
    version_number: int
    review_status: str


@dataclass(frozen=True)
class QuoteVersionMovementResult:
    generation_id: str
    prior_candidate_id: str
    initial_candidate_id: str
    component_count: int


def register_quote_version_candidate(
    connection: sqlite3.Connection, *, observation_id: str,
    region_code: str, source_identity: str, detected_at_utc: str,
    audit: AuditContext,
) -> QuoteVersionCandidate:
    region = region_code.strip().upper()
    source = source_identity.strip()
    if not region or not source:
        raise ValueError("Region and source identity are required for quote version detection")
    lineage = connection.execute(
        """SELECT DISTINCT round.event_id, observation.supplier_id,
                  observation.part_id
           FROM pbd_observation observation
           JOIN round_observation membership
             ON membership.observation_id = observation.observation_id
           JOIN supplier_quote_round round
             ON round.quote_round_id = membership.quote_round_id
           WHERE observation.observation_id = ?
             AND observation.observation_context = 'Sourcing Event'""",
        (observation_id,),
    ).fetchall()
    if len(lineage) != 1 or lineage[0][1] is None or lineage[0][2] is None:
        raise ValueError("Quote candidate requires one exact sourcing-event/supplier/part lineage")
    event_id, supplier_id, part_id = map(str, lineage[0])
    existing = connection.execute(
        """SELECT candidate.quote_version_candidate_id,
                  candidate.version_number, review.review_status
           FROM v_quote_version_review review
           JOIN quote_version_candidate candidate
             ON candidate.quote_version_candidate_id = review.quote_version_candidate_id
           WHERE candidate.observation_id = ? OR
                 (candidate.event_id = ? AND candidate.supplier_id = ?
                  AND candidate.part_id = ? AND candidate.region_code = ?
                  AND candidate.source_identity = ?)
           ORDER BY candidate.version_number LIMIT 1""",
        (observation_id, event_id, supplier_id, part_id, region, source),
    ).fetchone()
    if existing is not None:
        return QuoteVersionCandidate(str(existing[0]), int(existing[1]),
                                     str(existing[2]))
    version_number = int(connection.execute(
        """SELECT COALESCE(MAX(version_number), 0) + 1
           FROM quote_version_candidate
           WHERE event_id = ? AND supplier_id = ? AND part_id = ?
             AND region_code = ?""",
        (event_id, supplier_id, part_id, region),
    ).fetchone()[0])
    candidate_id = uuid7()
    with immediate_transaction(connection):
        connection.execute(
            """INSERT INTO quote_version_candidate VALUES
               (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (candidate_id, observation_id, event_id, supplier_id, part_id,
             region, source, version_number, detected_at_utc),
        )
        append_audit_event(connection, audit, {
            "quote_version_candidate_id": candidate_id,
            "observation_id": observation_id, "event_id": event_id,
            "supplier_id": supplier_id, "part_id": part_id,
            "region_code": region, "source_identity": source,
            "version_number": version_number,
            "review_status": "Pending Version Review",
        })
    return QuoteVersionCandidate(candidate_id, version_number,
                                 "Pending Version Review")


def _review_quote_version_candidate(
    connection: sqlite3.Connection, *, candidate_id: str,
    review_decision: str, buyer_classification: str,
    buyer_note: str | None, reviewed_by_user_id: str,
    reviewed_at_utc: str, audit: AuditContext,
) -> str:
    if review_decision not in {"Confirm Active", "Reject Candidate"}:
        raise ValueError("Unsupported quote version review decision")
    if buyer_classification not in CLASSIFICATIONS:
        raise ValueError("Unsupported buyer quote classification")
    candidate = connection.execute(
        "SELECT * FROM quote_version_candidate WHERE quote_version_candidate_id = ?",
        (candidate_id,),
    ).fetchone()
    if candidate is None:
        raise ValueError("Quote version candidate does not exist")
    if connection.execute(
        "SELECT 1 FROM quote_version_review_event WHERE quote_version_candidate_id = ?",
        (candidate_id,),
    ).fetchone() is not None:
        raise ValueError("Quote version candidate has already been reviewed")
    review_id = uuid7()
    connection.execute(
        """INSERT INTO quote_version_review_event VALUES
           (?, ?, ?, ?, ?, ?, ?)""",
        (review_id, candidate_id, review_decision, buyer_classification,
         buyer_note, reviewed_by_user_id, reviewed_at_utc),
    )
    if review_decision == "Confirm Active":
        prior = connection.execute(
            """SELECT active.quote_version_activation_id,
                      active.version_number
               FROM v_active_quote_version active
               WHERE active.event_id = ? AND active.supplier_id = ?
                 AND active.part_id = ? AND active.region_code = ?""",
            (candidate["event_id"], candidate["supplier_id"],
             candidate["part_id"], candidate["region_code"]),
        ).fetchone()
        if prior is not None and int(candidate["version_number"]) <= int(prior[1]):
            raise ValueError("An older quote candidate cannot replace the active version")
        connection.execute(
            "INSERT INTO quote_version_activation VALUES (?, ?, ?, ?, ?)",
            (uuid7(), review_id, candidate_id,
             None if prior is None else prior[0], reviewed_at_utc),
        )
    append_audit_event(connection, audit, {
        "quote_version_review_event_id": review_id,
        "quote_version_candidate_id": candidate_id,
        "review_decision": review_decision,
        "buyer_classification": buyer_classification,
        "buyer_note": buyer_note,
        "reviewed_by_user_id": reviewed_by_user_id,
    })
    return review_id


def review_quote_version_candidate(
    connection: sqlite3.Connection, *, candidate_id: str,
    review_decision: str, buyer_classification: str,
    buyer_note: str | None, reviewed_by_user_id: str,
    reviewed_at_utc: str, audit: AuditContext,
) -> str:
    with immediate_transaction(connection):
        return _review_quote_version_candidate(
            connection, candidate_id=candidate_id,
            review_decision=review_decision,
            buyer_classification=buyer_classification, buyer_note=buyer_note,
            reviewed_by_user_id=reviewed_by_user_id,
            reviewed_at_utc=reviewed_at_utc, audit=audit,
        )


def bulk_confirm_requote_candidates(
    connection: sqlite3.Connection, *, candidate_ids: tuple[str, ...],
    buyer_classification: str, buyer_note: str | None,
    reviewed_by_user_id: str, reviewed_at_utc: str,
    audit: AuditContext,
) -> tuple[str, ...]:
    if not candidate_ids or len(candidate_ids) != len(set(candidate_ids)):
        raise ValueError("Bulk confirmation requires distinct quote candidates")
    rows = connection.execute(
        f"""SELECT quote_version_candidate_id, event_id, supplier_id
            FROM quote_version_candidate
            WHERE quote_version_candidate_id IN ({','.join('?' for _ in candidate_ids)})""",
        candidate_ids,
    ).fetchall()
    if len(rows) != len(candidate_ids) or len({(row[1], row[2]) for row in rows}) != 1:
        raise ValueError("Bulk requote confirmation requires one supplier and sourcing event")
    review_ids: list[str] = []
    with immediate_transaction(connection):
        for candidate_id in candidate_ids:
            review_ids.append(_review_quote_version_candidate(
                connection, candidate_id=candidate_id,
                review_decision="Confirm Active",
                buyer_classification=buyer_classification,
                buyer_note=buyer_note, reviewed_by_user_id=reviewed_by_user_id,
                reviewed_at_utc=reviewed_at_utc, audit=audit,
            ))
    return tuple(review_ids)


def _candidate_values(
    connection: sqlite3.Connection, candidate_id: str,
    field_codes: tuple[str, ...],
) -> tuple[sqlite3.Row, dict[str, sqlite3.Row]]:
    candidate = connection.execute(
        "SELECT * FROM quote_version_candidate WHERE quote_version_candidate_id = ?",
        (candidate_id,),
    ).fetchone()
    if candidate is None:
        raise ValueError("Quote version candidate does not exist")
    placeholders = ",".join("?" for _ in field_codes)
    rows = connection.execute(
        f"""SELECT submitted_datum_id, field_code, decimal_coefficient,
                   decimal_scale, normalized_unit_id, currency_id
            FROM submitted_datum
            WHERE observation_id = ? AND field_code IN ({placeholders})
              AND precision_status = 'Eligible' AND decimal_coefficient IS NOT NULL
            ORDER BY field_code, submitted_datum_id""",
        (candidate["observation_id"], *field_codes),
    ).fetchall()
    values: dict[str, sqlite3.Row] = {}
    for row in rows:
        code = str(row["field_code"])
        if code in values:
            raise ValueError(f"Quote candidate has multiple eligible values for {code}")
        values[code] = row
    return candidate, values


def _movement_row(
    basis: str, field_code: str, current: sqlite3.Row | None,
    baseline: sqlite3.Row | None,
) -> tuple[object, ...]:
    if current is None and baseline is None:
        status = "Missing Both"
    elif current is None:
        status = "Missing Current Evidence"
    elif baseline is None:
        status = "Missing Baseline Evidence"
    elif current["normalized_unit_id"] != baseline["normalized_unit_id"]:
        status = "Unit Alignment Required"
    elif current["currency_id"] != baseline["currency_id"]:
        status = "Currency Alignment Required"
    else:
        status = "Comparable"
    delta: ExactDecimal | None = None
    percent: ExactDecimal | None = None
    if status == "Comparable" and current is not None and baseline is not None:
        current_value = Decimal(int(current["decimal_coefficient"])).scaleb(
            -int(current["decimal_scale"])
        )
        baseline_value = Decimal(int(baseline["decimal_coefficient"])).scaleb(
            -int(baseline["decimal_scale"])
        )
        delta = ExactDecimal.parse(format(current_value - baseline_value, "f"))
        if baseline_value != 0:
            percent = ExactDecimal.parse(
                format((current_value - baseline_value) / baseline_value * 100, "f")
            )
    return (
        basis, field_code,
        None if current is None else current["submitted_datum_id"],
        None if baseline is None else baseline["submitted_datum_id"], status,
        None if current is None else current["decimal_coefficient"],
        None if current is None else current["decimal_scale"],
        None if baseline is None else baseline["decimal_coefficient"],
        None if baseline is None else baseline["decimal_scale"],
        None if delta is None else delta.coefficient,
        None if delta is None else delta.scale,
        None if percent is None else percent.coefficient,
        None if percent is None else percent.scale,
        None if current is None else current["normalized_unit_id"],
        None if current is None else current["currency_id"],
    )


def build_quote_version_movement(
    connection: sqlite3.Connection, *, candidate_id: str,
    calculation_rule_version_id: str, generated_at_utc: str,
    audit: AuditContext,
) -> QuoteVersionMovementResult:
    field_codes = DEFAULT_MOVEMENT_FIELDS
    current_candidate, current_values = _candidate_values(
        connection, candidate_id, field_codes
    )
    if int(current_candidate["version_number"]) <= 1:
        raise ValueError("Movement requires a quote version after V1")
    scope = (current_candidate["event_id"], current_candidate["supplier_id"],
             current_candidate["part_id"], current_candidate["region_code"])
    prior_candidate = connection.execute(
        """SELECT * FROM quote_version_candidate
           WHERE event_id = ? AND supplier_id = ? AND part_id = ? AND region_code = ?
             AND version_number < ? ORDER BY version_number DESC LIMIT 1""",
        (*scope, current_candidate["version_number"]),
    ).fetchone()
    initial_candidate = connection.execute(
        """SELECT * FROM quote_version_candidate
           WHERE event_id = ? AND supplier_id = ? AND part_id = ? AND region_code = ?
           ORDER BY version_number LIMIT 1""", scope,
    ).fetchone()
    if prior_candidate is None or initial_candidate is None:
        raise ValueError("Prior and initial quote candidates are required")
    _, prior_values = _candidate_values(connection, str(prior_candidate[0]), field_codes)
    _, initial_values = _candidate_values(connection, str(initial_candidate[0]), field_codes)
    rows = [
        _movement_row(basis, code, current_values.get(code), baseline.get(code))
        for basis, baseline in (("Prior Version", prior_values),
                                ("Initial Version", initial_values))
        for code in field_codes
    ]
    manifest = {
        "quote_version_candidate_id": candidate_id,
        "prior_candidate_id": prior_candidate[0],
        "initial_candidate_id": initial_candidate[0],
        "calculation_rule_version_id": calculation_rule_version_id,
        "components": rows,
    }
    manifest_hash = hashlib.sha256(
        canonical_json(manifest).encode("utf-8")
    ).hexdigest()
    generation_id = uuid7()
    with immediate_transaction(connection):
        connection.execute(
            """INSERT INTO quote_version_movement_generation VALUES
               (?, ?, ?, ?, ?, ?, ?)""",
            (generation_id, candidate_id, prior_candidate[0], initial_candidate[0],
             calculation_rule_version_id, manifest_hash, generated_at_utc),
        )
        connection.executemany(
            """INSERT INTO quote_version_component_movement VALUES
               (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [(uuid7(), generation_id, *row) for row in rows],
        )
        append_audit_event(connection, audit, {
            "quote_version_movement_generation_id": generation_id,
            "quote_version_candidate_id": candidate_id,
            "prior_candidate_id": prior_candidate[0],
            "initial_candidate_id": initial_candidate[0],
            "component_manifest_hash": manifest_hash,
        })
    return QuoteVersionMovementResult(
        generation_id, str(prior_candidate[0]), str(initial_candidate[0]), len(rows)
    )
