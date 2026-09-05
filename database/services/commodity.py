from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from .audit import AuditContext, append_audit_event
from .connection import immediate_transaction
from .ids import uuid7


ACTIVITY_TYPES = {
    "Price Change", "Formula Change", "Scope Change", "Omission",
    "Correction", "Addition", "Removal", "Reaffirmation",
}

EVIDENCE_TABLES = {
    "PBD Observation": ("pbd_observation", "observation_id"),
    "Round Observation": ("round_observation", "round_observation_id"),
    "Submitted Datum": ("submitted_datum", "submitted_datum_id"),
}


@dataclass(frozen=True)
class SupplierActivityChange:
    supplier_id: str
    supplier_plant_id: str | None
    event_id: str | None
    quote_round_id: str | None
    event_part_id: str | None
    activity_type: str
    before_entity_type: str | None
    before_entity_id: str | None
    after_entity_type: str | None
    after_entity_id: str | None
    occurred_at_utc: str | None


def _require_entity(
    connection: sqlite3.Connection, entity_type: str | None, entity_id: str | None
) -> None:
    if entity_type is None and entity_id is None:
        return
    if entity_type is None or entity_id is None:
        raise ValueError("Evidence type and ID must be provided together")
    if entity_type not in EVIDENCE_TABLES:
        raise ValueError(f"Unsupported evidence entity type: {entity_type}")
    table, key = EVIDENCE_TABLES[entity_type]
    if connection.execute(f"SELECT 1 FROM {table} WHERE {key} = ?", (entity_id,)).fetchone() is None:
        raise ValueError(f"{entity_type} does not exist: {entity_id}")


def _is_pce_evidence(
    connection: sqlite3.Connection, entity_type: str | None, entity_id: str | None
) -> bool:
    if entity_type is None or entity_id is None:
        return False
    joins = {
        "PBD Observation": (
            "SELECT observation_context FROM pbd_observation WHERE observation_id = ?"
        ),
        "Round Observation": (
            """SELECT observation.observation_context FROM round_observation membership
               JOIN pbd_observation observation
                 ON observation.observation_id = membership.observation_id
               WHERE membership.round_observation_id = ?"""
        ),
        "Submitted Datum": (
            """SELECT observation.observation_context FROM submitted_datum datum
               JOIN pbd_observation observation
                 ON observation.observation_id = datum.observation_id
               WHERE datum.submitted_datum_id = ?"""
        ),
    }
    row = connection.execute(joins[entity_type], (entity_id,)).fetchone()
    return row is not None and row[0] == "PCE Should Cost"


def append_supplier_activity(
    connection: sqlite3.Connection,
    change: SupplierActivityChange,
    audit: AuditContext,
) -> str:
    if change.activity_type not in ACTIVITY_TYPES:
        raise ValueError(f"Unsupported supplier activity type: {change.activity_type}")
    if change.before_entity_id is None and change.after_entity_id is None:
        raise ValueError("Supplier activity requires before or after evidence")
    activity_id = uuid7()
    with immediate_transaction(connection):
        _require_entity(connection, change.before_entity_type, change.before_entity_id)
        _require_entity(connection, change.after_entity_type, change.after_entity_id)
        if _is_pce_evidence(connection, change.before_entity_type, change.before_entity_id) or \
                _is_pce_evidence(connection, change.after_entity_type, change.after_entity_id):
            raise ValueError("PCE should-cost evidence cannot enter supplier behavior history")
        connection.execute(
            """INSERT INTO supplier_activity
               (supplier_activity_id, supplier_id, supplier_plant_id, event_id,
                quote_round_id, event_part_id, activity_type,
                before_entity_type, before_entity_id, after_entity_type,
                after_entity_id, occurred_at_utc, recorded_at_utc)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                activity_id, change.supplier_id, change.supplier_plant_id,
                change.event_id, change.quote_round_id, change.event_part_id,
                change.activity_type, change.before_entity_type,
                change.before_entity_id, change.after_entity_type,
                change.after_entity_id, change.occurred_at_utc,
                audit.occurred_at_utc,
            ),
        )
        append_audit_event(
            connection,
            audit,
            {"supplier_activity_id": activity_id, "activity_type": change.activity_type},
            [{"entity_type": "Supplier Activity", "entity_id": activity_id, "after": {"activity_type": change.activity_type}}],
        )
    return activity_id


def activate_supplier_round(
    connection: sqlite3.Connection,
    *,
    event_id: str,
    supplier_id: str,
    quote_round_id: str,
    decided_by_user_id: str,
    decision_reason: str,
    audit: AuditContext,
) -> str:
    if not decision_reason.strip():
        raise ValueError("Round activation requires a decision reason")
    activation_id = uuid7()
    with immediate_transaction(connection):
        round_row = connection.execute(
            """SELECT event_id, supplier_id, round_number
               FROM supplier_quote_round WHERE quote_round_id = ?""",
            (quote_round_id,),
        ).fetchone()
        if round_row is None:
            raise ValueError(f"Quote round does not exist: {quote_round_id}")
        if round_row["event_id"] != event_id or round_row["supplier_id"] != supplier_id:
            raise ValueError("Quote round does not belong to the supplied event and supplier")
        prior = connection.execute(
            """SELECT quote_round_id FROM v_active_supplier_round
               WHERE event_id = ? AND supplier_id = ?""",
            (event_id, supplier_id),
        ).fetchone()
        connection.execute(
            """INSERT INTO round_activation
               (round_activation_id, event_id, supplier_id, quote_round_id,
                activation_decision, decided_by_user_id, decision_reason,
                recorded_at_utc)
               VALUES (?, ?, ?, ?, 'Activate', ?, ?, ?)""",
            (
                activation_id, event_id, supplier_id, quote_round_id,
                decided_by_user_id, decision_reason, audit.occurred_at_utc,
            ),
        )
        append_audit_event(
            connection,
            audit,
            {
                "round_activation_id": activation_id,
                "event_id": event_id,
                "supplier_id": supplier_id,
                "quote_round_id": quote_round_id,
                "round_number": round_row["round_number"],
            },
            [{
                "entity_type": "Supplier Active Round",
                "entity_id": supplier_id,
                "before": {"quote_round_id": prior[0] if prior else None},
                "after": {"quote_round_id": quote_round_id},
            }],
        )
    return activation_id
