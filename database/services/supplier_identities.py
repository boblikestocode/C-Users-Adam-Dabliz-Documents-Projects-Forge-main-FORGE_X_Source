from __future__ import annotations

import sqlite3

from .audit import AuditContext, append_audit_event
from .connection import immediate_transaction
from .ids import uuid7


def confirm_supplier_identity_batch(
    connection: sqlite3.Connection, *, observation_ids: tuple[str, ...],
    confirmed_supplier_id: str, confirmed_supplier_code: str,
    confirmation_reason: str, confirmed_by_user_id: str,
    recorded_at_utc: str, audit: AuditContext,
) -> tuple[str, ...]:
    if not observation_ids or len(set(observation_ids)) != len(observation_ids):
        raise ValueError("Supplier identity confirmation requires distinct selected observations")
    supplier_id = confirmed_supplier_id.strip()
    supplier_code = confirmed_supplier_code.strip().upper()
    if not supplier_id or not supplier_code or not confirmation_reason.strip() or not confirmed_by_user_id.strip():
        raise ValueError("Supplier identity confirmation requires identity, code, reason, and buyer")
    placeholders = ",".join("?" for _ in observation_ids)
    rows = connection.execute(
        f"""SELECT observation.observation_id, observation.supplier_id,
                   observation.submitted_supplier_name, staged.provisional_supplier_code
            FROM pbd_observation observation JOIN staged_observation staged
              ON staged.staged_observation_id = observation.staged_observation_id
            WHERE observation.observation_id IN ({placeholders})""", observation_ids,
    ).fetchall()
    if len(rows) != len(observation_ids):
        raise ValueError("Every selected supplier identity record must exist")
    if any(row[1] is not None for row in rows):
        raise ValueError("Buyer confirmation only applies to unresolved supplier identities")
    if connection.execute(
        f"""SELECT 1 FROM v_current_observation_supplier_identity
            WHERE observation_id IN ({placeholders}) LIMIT 1""", observation_ids,
    ).fetchone() is not None:
        raise ValueError("Selected observation already has a supplier identity confirmation")
    ids = tuple(uuid7() for _ in rows)
    with immediate_transaction(connection):
        for confirmation_id, row in zip(ids, rows):
            connection.execute(
                """INSERT INTO observation_supplier_identity_confirmation VALUES
                   (?, ?, ?, ?, NULL, ?, ?, ?, ?, NULL, ?)""",
                (confirmation_id, row[0], supplier_id, supplier_code, row[3], row[2],
                 confirmation_reason.strip(), confirmed_by_user_id, recorded_at_utc),
            )
            action = connection.execute(
                """SELECT current.* FROM buyer_action action
                   JOIN v_current_buyer_action current
                     ON current.buyer_action_id = action.buyer_action_id
                   WHERE action.governing_entity_type = 'PBD Observation'
                     AND action.governing_entity_id = ?
                     AND action.issue_type = 'Supplier Code Confirmation Required'""",
                (row[0],),
            ).fetchone()
            if action is not None:
                connection.execute(
                    """INSERT INTO buyer_action_version
                       (buyer_action_version_id, buyer_action_id, action_status,
                        financial_impact_coefficient, financial_impact_scale,
                        currency_id, required_supplier_action, owner_user_id,
                        resolution_reason, supersedes_action_version_id,
                        recorded_by_user_id, recorded_at_utc)
                       VALUES (?, ?, 'Resolved', ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (uuid7(), action["buyer_action_id"],
                     action["financial_impact_coefficient"], action["financial_impact_scale"],
                     action["currency_id"], f"Confirmed as {supplier_code}",
                     confirmed_by_user_id, confirmation_reason.strip(),
                     action["buyer_action_version_id"], confirmed_by_user_id,
                     recorded_at_utc),
                )
        append_audit_event(connection, audit, {
            "supplier_identity_confirmation_ids": ids,
            "selected_observation_ids": observation_ids,
            "confirmed_supplier_id": supplier_id,
            "confirmed_supplier_code": supplier_code,
            "confirmation_reason": confirmation_reason.strip(),
        })
    return ids
