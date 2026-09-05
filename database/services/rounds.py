from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from decimal import Decimal

from .audit import AuditContext, append_audit_event
from .connection import immediate_transaction
from .ids import uuid7


@dataclass(frozen=True)
class PartRoundChange:
    event_part_id: str
    prior_observation_id: str | None
    current_observation_id: str | None
    prior_piece_price: Decimal | None
    current_piece_price: Decimal | None
    classification: str


@dataclass(frozen=True)
class RoundComparison:
    prior_round_id: str
    current_round_id: str
    changed: int
    unchanged: int
    added: int
    omitted: int
    parts: tuple[PartRoundChange, ...]


@dataclass(frozen=True)
class RoundObservationInput:
    observation_id: str
    event_part_id: str
    membership_status: str = "Submitted"


def register_round_batch(
    connection: sqlite3.Connection,
    *, quote_round_id: str, import_transaction_id: str,
    observations: tuple[RoundObservationInput, ...],
    confirmed_by_user_id: str, confirmed_at_utc: str,
    audit: AuditContext,
) -> tuple[str, tuple[str, ...]]:
    if not observations:
        raise ValueError("Round batch requires at least one observation")
    if not confirmed_by_user_id.strip():
        raise ValueError("Round batch requires a confirming user")
    if any(item.membership_status not in {"Submitted", "Unchanged", "Added"}
           for item in observations):
        raise ValueError("Imported round membership must be Submitted, Unchanged, or Added")
    if len({item.event_part_id for item in observations}) != len(observations):
        raise ValueError("One import batch cannot silently contain duplicate event parts")
    batch_id = uuid7()
    conflict_ids: list[str] = []
    with immediate_transaction(connection):
        context = connection.execute(
            """SELECT round_row.event_id, round_row.supplier_id,
                      session.import_context_type, session.context_id
               FROM supplier_quote_round round_row
               JOIN import_transaction transaction_row
                 ON transaction_row.import_transaction_id = ?
               JOIN import_session session
                 ON session.import_session_id = transaction_row.import_session_id
               WHERE round_row.quote_round_id = ?""",
            (import_transaction_id, quote_round_id),
        ).fetchone()
        if context is None:
            raise ValueError("Quote round or import transaction does not exist")
        if context[2] != "Sourcing Event" or context[3] != context[0]:
            raise ValueError("Round batch import must match the sourcing event")
        connection.execute(
            """INSERT INTO round_batch VALUES (?, ?, ?, ?, ?)""",
            (batch_id, quote_round_id, import_transaction_id,
             confirmed_by_user_id.strip(), confirmed_at_utc),
        )
        for item in observations:
            prior = connection.execute(
                """SELECT round_observation_id FROM round_observation
                   WHERE quote_round_id = ? AND event_part_id = ?
                     AND membership_status IN ('Submitted', 'Unchanged', 'Added')""",
                (quote_round_id, item.event_part_id),
            ).fetchall()
            if len(prior) > 1:
                raise ValueError("Round already contains unresolved multiple governing records")
            membership_id = uuid7()
            status = "Conflict" if prior else item.membership_status
            connection.execute(
                """INSERT INTO round_observation VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (membership_id, quote_round_id, batch_id, item.observation_id,
                 item.event_part_id, status, confirmed_at_utc),
            )
            if prior:
                conflict_id = uuid7()
                connection.execute(
                    """INSERT INTO round_conflict VALUES
                       (?, ?, ?, ?, ?, 'Same Part Within Supplier Round', ?)""",
                    (conflict_id, quote_round_id, item.event_part_id,
                     prior[0][0], membership_id, confirmed_at_utc),
                )
                conflict_ids.append(conflict_id)
        append_audit_event(
            connection, audit,
            {"round_batch_id": batch_id, "quote_round_id": quote_round_id,
             "import_transaction_id": import_transaction_id,
             "observation_count": len(observations),
             "round_conflict_ids": conflict_ids},
        )
    return batch_id, tuple(conflict_ids)


def resolve_round_conflict(
    connection: sqlite3.Connection,
    *, round_conflict_id: str, decision_code: str,
    decided_by_user_id: str, decision_reason: str,
    recorded_at_utc: str, audit: AuditContext,
    target_quote_round_id: str | None = None,
    supersedes_round_conflict_decision_id: str | None = None,
) -> str:
    allowed = {"New Replaces Earlier Within Round", "Move New Submission to New Round",
               "Keep Earlier Active", "Exclude Both Pending Review"}
    if decision_code not in allowed or not decision_reason.strip() or not decided_by_user_id.strip():
        raise ValueError("Round conflict resolution requires a valid decision, user, and reason")
    if (decision_code == "Move New Submission to New Round") != (target_quote_round_id is not None):
        raise ValueError("Only move decisions require a target quote round")
    decision_id = uuid7()
    with immediate_transaction(connection):
        conflict = connection.execute(
            """SELECT conflict.quote_round_id, round_row.event_id,
                      round_row.supplier_id, round_row.round_number
               FROM round_conflict conflict
               JOIN supplier_quote_round round_row
                 ON round_row.quote_round_id = conflict.quote_round_id
               WHERE conflict.round_conflict_id = ?""", (round_conflict_id,),
        ).fetchone()
        if conflict is None:
            raise ValueError("Round conflict does not exist")
        current = connection.execute(
            """SELECT round_conflict_decision_id FROM v_current_round_conflict_decision
               WHERE round_conflict_id = ?""", (round_conflict_id,),
        ).fetchone()
        current_id = None if current is None else str(current[0])
        if current_id is None and supersedes_round_conflict_decision_id is not None:
            raise ValueError("Initial conflict decision cannot supersede another decision")
        if current_id is not None and supersedes_round_conflict_decision_id != current_id:
            raise ValueError("A revised conflict decision must supersede the current decision")
        if target_quote_round_id is not None:
            target = connection.execute(
                """SELECT event_id, supplier_id, round_number FROM supplier_quote_round
                   WHERE quote_round_id = ?""", (target_quote_round_id,),
            ).fetchone()
            if target is None or target[0] != conflict[1] or target[1] != conflict[2] or target[2] <= conflict[3]:
                raise ValueError("Conflict move target must be a later round for the same event and supplier")
        connection.execute(
            """INSERT INTO round_conflict_decision
               (round_conflict_decision_id, round_conflict_id, decision_code,
                target_quote_round_id, decided_by_user_id, decision_reason,
                recorded_at_utc, supersedes_round_conflict_decision_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (decision_id, round_conflict_id, decision_code, target_quote_round_id,
             decided_by_user_id.strip(), decision_reason.strip(), recorded_at_utc,
             supersedes_round_conflict_decision_id),
        )
        append_audit_event(connection, audit, {
            "round_conflict_decision_id": decision_id,
            "round_conflict_id": round_conflict_id, "decision_code": decision_code,
            "target_quote_round_id": target_quote_round_id,
        })
    return decision_id


def _exact_decimal(coefficient: str | None, scale: int | None) -> Decimal | None:
    if coefficient is None or scale is None:
        return None
    return Decimal(int(coefficient)).scaleb(-scale)


def create_quote_round(
    connection: sqlite3.Connection,
    *,
    event_id: str,
    supplier_id: str,
    round_number: int,
    round_description: str | None,
    supplier_submission_date: str | None,
    recorded_at_utc: str,
    audit: AuditContext,
) -> str:
    if round_number <= 0:
        raise ValueError("Round number must be a positive whole number")
    quote_round_id = uuid7()
    with immediate_transaction(connection):
        if connection.execute(
            "SELECT 1 FROM sourcing_event WHERE event_id = ?", (event_id,)
        ).fetchone() is None:
            raise ValueError("Sourcing event does not exist")
        connection.execute(
            """INSERT INTO supplier_quote_round
               (quote_round_id, event_id, supplier_id, round_number,
                round_description, supplier_submission_date, recorded_at_utc)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                quote_round_id, event_id, supplier_id, round_number,
                round_description, supplier_submission_date, recorded_at_utc,
            ),
        )
        append_audit_event(
            connection, audit,
            {
                "quote_round_id": quote_round_id,
                "event_id": event_id,
                "supplier_id": supplier_id,
                "round_number": round_number,
            },
        )
    return quote_round_id


def _round_parts(connection: sqlite3.Connection, quote_round_id: str) -> dict[str, tuple[str, Decimal | None]]:
    rows = connection.execute(
        """SELECT ro.event_part_id, ro.observation_id,
                  sd.decimal_coefficient, sd.decimal_scale
           FROM round_observation ro
           LEFT JOIN submitted_datum sd
             ON sd.observation_id = ro.observation_id
            AND sd.field_code = 'PIECE_PRICE'
           WHERE ro.quote_round_id = ?
             AND ro.membership_status IN ('Submitted', 'Unchanged', 'Added')""",
        (quote_round_id,),
    ).fetchall()
    result: dict[str, tuple[str, Decimal | None]] = {}
    for row in rows:
        event_part_id = str(row[0])
        if event_part_id in result:
            raise ValueError(f"Round contains unresolved multiple active records for event part {event_part_id}")
        result[event_part_id] = (str(row[1]), _exact_decimal(row[2], row[3]))
    return result


def compare_rounds(
    connection: sqlite3.Connection,
    *,
    prior_round_id: str,
    current_round_id: str,
) -> RoundComparison:
    round_context = connection.execute(
        """SELECT quote_round_id, event_id, supplier_id, round_number
           FROM supplier_quote_round WHERE quote_round_id IN (?, ?)""",
        (prior_round_id, current_round_id),
    ).fetchall()
    if len(round_context) != 2:
        raise ValueError("Both supplier rounds must exist")
    by_id = {row[0]: row for row in round_context}
    prior = by_id[prior_round_id]
    current = by_id[current_round_id]
    if prior[1] != current[1] or prior[2] != current[2]:
        raise ValueError("Round comparison requires the same event and supplier")
    if prior[3] >= current[3]:
        raise ValueError("Prior round number must be lower than current round number")

    prior_parts = _round_parts(connection, prior_round_id)
    current_parts = _round_parts(connection, current_round_id)
    changes: list[PartRoundChange] = []
    for event_part_id in sorted(prior_parts.keys() | current_parts.keys()):
        before = prior_parts.get(event_part_id)
        after = current_parts.get(event_part_id)
        if before is None:
            classification = "Added"
        elif after is None:
            classification = "Omitted"
        elif before[1] == after[1]:
            classification = "Unchanged"
        else:
            classification = "Changed"
        changes.append(
            PartRoundChange(
                event_part_id=event_part_id,
                prior_observation_id=before[0] if before else None,
                current_observation_id=after[0] if after else None,
                prior_piece_price=before[1] if before else None,
                current_piece_price=after[1] if after else None,
                classification=classification,
            )
        )
    return RoundComparison(
        prior_round_id=prior_round_id,
        current_round_id=current_round_id,
        changed=sum(change.classification == "Changed" for change in changes),
        unchanged=sum(change.classification == "Unchanged" for change in changes),
        added=sum(change.classification == "Added" for change in changes),
        omitted=sum(change.classification == "Omitted" for change in changes),
        parts=tuple(changes),
    )


def record_carry_forward(
    connection: sqlite3.Connection,
    *,
    target_quote_round_id: str,
    event_part_id: str,
    prior_observation_id: str,
    decided_by_user_id: str,
    decision_reason: str,
    recorded_at_utc: str,
    audit: AuditContext,
) -> str:
    if not decision_reason.strip():
        raise ValueError("Carry-forward requires a business reason")
    decision_id = uuid7()
    with immediate_transaction(connection):
        target = connection.execute(
            """SELECT event_id, supplier_id, round_number
               FROM supplier_quote_round WHERE quote_round_id = ?""",
            (target_quote_round_id,),
        ).fetchone()
        if target is None:
            raise ValueError("Target quote round does not exist")
        if connection.execute(
            """SELECT 1 FROM round_observation
               WHERE quote_round_id = ? AND event_part_id = ?
                 AND membership_status IN ('Submitted', 'Unchanged', 'Added')""",
            (target_quote_round_id, event_part_id),
        ).fetchone():
            raise ValueError("Cannot carry forward a part already submitted in the target round")
        prior = connection.execute(
            """SELECT qr.event_id, qr.supplier_id, qr.round_number
               FROM round_observation ro
               JOIN supplier_quote_round qr ON qr.quote_round_id = ro.quote_round_id
               WHERE ro.observation_id = ? AND ro.event_part_id = ?
               ORDER BY qr.round_number DESC LIMIT 1""",
            (prior_observation_id, event_part_id),
        ).fetchone()
        if prior is None:
            raise ValueError("Prior observation is not a submitted record for this event part")
        if prior[0] != target[0] or prior[1] != target[1] or prior[2] >= target[2]:
            raise ValueError("Carry-forward evidence must come from an earlier round for the same event and supplier")
        connection.execute(
            """INSERT INTO carry_forward_decision
               (carry_forward_decision_id, target_quote_round_id,
                event_part_id, prior_observation_id, decision_code,
                decided_by_user_id, decision_reason, recorded_at_utc)
               VALUES (?, ?, ?, ?, 'Carry Forward', ?, ?, ?)""",
            (
                decision_id, target_quote_round_id, event_part_id,
                prior_observation_id, decided_by_user_id,
                decision_reason, recorded_at_utc,
            ),
        )
        append_audit_event(
            connection, audit,
            {
                "carry_forward_decision_id": decision_id,
                "target_quote_round_id": target_quote_round_id,
                "event_part_id": event_part_id,
                "prior_observation_id": prior_observation_id,
            },
        )
    return decision_id


def record_coverage_decision(
    connection: sqlite3.Connection,
    *,
    target_quote_round_id: str,
    event_part_id: str,
    decision_code: str,
    decided_by_user_id: str,
    decision_reason: str,
    recorded_at_utc: str,
    audit: AuditContext,
) -> str:
    if decision_code not in ("Not Quoted", "Buyer Review Required"):
        raise ValueError("Coverage decision must be Not Quoted or Buyer Review Required")
    if not decision_reason.strip():
        raise ValueError("Coverage decision requires a business reason")
    decision_id = uuid7()
    with immediate_transaction(connection):
        target = connection.execute(
            """SELECT qr.event_id, ep.event_part_id
               FROM supplier_quote_round qr
               JOIN source_package sp ON sp.event_id = qr.event_id
               JOIN v_current_event_scope scope
                 ON scope.source_package_id = sp.source_package_id
               JOIN event_part ep ON ep.scope_version_id = scope.scope_version_id
               WHERE qr.quote_round_id = ? AND ep.event_part_id = ?""",
            (target_quote_round_id, event_part_id),
        ).fetchone()
        if target is None:
            raise ValueError("Coverage decision requires a current-scope part in the target round")
        if connection.execute(
            """SELECT 1 FROM round_observation
               WHERE quote_round_id = ? AND event_part_id = ?
                 AND membership_status IN ('Submitted', 'Unchanged', 'Added')""",
            (target_quote_round_id, event_part_id),
        ).fetchone():
            raise ValueError("Cannot mark a part not quoted when it was submitted in the target round")
        connection.execute(
            """INSERT INTO carry_forward_decision
               (carry_forward_decision_id, target_quote_round_id,
                event_part_id, prior_observation_id, decision_code,
                decided_by_user_id, decision_reason, recorded_at_utc)
               VALUES (?, ?, ?, NULL, ?, ?, ?, ?)""",
            (decision_id, target_quote_round_id, event_part_id, decision_code,
             decided_by_user_id, decision_reason, recorded_at_utc),
        )
        append_audit_event(
            connection, audit,
            {"carry_forward_decision_id": decision_id,
             "target_quote_round_id": target_quote_round_id,
             "event_part_id": event_part_id, "decision_code": decision_code},
        )
    return decision_id
