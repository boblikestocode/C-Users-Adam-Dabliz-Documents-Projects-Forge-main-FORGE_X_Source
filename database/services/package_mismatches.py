from __future__ import annotations

import sqlite3

from .audit import AuditContext, append_audit_event
from .connection import immediate_transaction
from .ids import uuid7


def _normalize(value: str) -> str:
    return "".join(value.strip().upper().split())


def record_source_package_mismatch(
    connection: sqlite3.Connection, *, source_package_id: str,
    source_datum_id: str, submitted_package_number: str,
    owner_user_id: str, detected_at_utc: str, audit: AuditContext,
) -> tuple[str, str]:
    submitted_displayed = submitted_package_number.strip()
    submitted_normalized = _normalize(submitted_displayed)
    if not submitted_normalized:
        raise ValueError("Readable submitted source package number is required")
    package = connection.execute(
        "SELECT * FROM v_current_source_package_identity WHERE source_package_id = ?",
        (source_package_id,),
    ).fetchone()
    if package is None:
        raise ValueError("Source package does not exist")
    datum = connection.execute(
        "SELECT submitted_lexeme FROM source_datum WHERE source_datum_id = ?",
        (source_datum_id,),
    ).fetchone()
    if datum is None or _normalize(str(datum[0] or "")) != submitted_normalized:
        raise ValueError("Submitted package number must match its immutable source datum")
    if submitted_normalized == package["normalized_package_number"]:
        raise ValueError("Matching source package numbers do not require review")
    mismatch_id = uuid7()
    action_id = uuid7()
    action_version_id = uuid7()
    with immediate_transaction(connection):
        connection.execute(
            """INSERT INTO source_package_mismatch VALUES
               (?, ?, ?, ?, ?, ?, ?, 'Review Required', ?)""",
            (mismatch_id, source_package_id, source_datum_id, submitted_normalized,
             submitted_displayed, package["normalized_package_number"],
             package["displayed_package_number"], detected_at_utc),
        )
        connection.execute(
            """INSERT INTO buyer_action
               (buyer_action_id, event_id, supplier_id, part_id,
                governing_entity_type, governing_entity_id, issue_type,
                created_by_user_id, created_at_utc)
               VALUES (?, ?, NULL, NULL, 'Source Package Mismatch', ?,
                       'Source Package Mismatch — Review Required', ?, ?)""",
            (action_id, package["event_id"], mismatch_id,
             audit.actor_user_id, detected_at_utc),
        )
        connection.execute(
            """INSERT INTO buyer_action_version
               (buyer_action_version_id, buyer_action_id, action_status,
                required_supplier_action, owner_user_id, recorded_by_user_id,
                recorded_at_utc)
               VALUES (?, ?, 'Open', ?, ?, ?, ?)""",
            (action_version_id, action_id,
             "Confirm event number or request supplier correction",
             owner_user_id, audit.actor_user_id, detected_at_utc),
        )
        append_audit_event(connection, audit, {
            "source_package_mismatch_id": mismatch_id,
            "source_package_id": source_package_id,
            "submitted_package_number": submitted_displayed,
            "event_package_number": package["displayed_package_number"],
            "status": "Source Package Mismatch — Review Required",
            "buyer_action_id": action_id,
            "buyer_action_version_id": action_version_id,
        })
    return mismatch_id, action_id


def resolve_source_package_mismatch(
    connection: sqlite3.Connection, *, source_package_mismatch_id: str,
    decision_code: str, decision_reason: str, decided_by_user_id: str,
    recorded_at_utc: str, audit: AuditContext,
) -> str:
    if decision_code not in {"Supplier Correction Required", "Event Number Corrected"}:
        raise ValueError("Unsupported source package mismatch decision")
    if not decision_reason.strip() or not decided_by_user_id.strip():
        raise ValueError("Mismatch resolution requires a reason and buyer identity")
    mismatch = connection.execute(
        """SELECT mismatch.*, package.event_id,
                  current.normalized_package_number AS current_event_number
           FROM source_package_mismatch mismatch
           JOIN source_package package ON package.source_package_id = mismatch.source_package_id
           JOIN v_current_source_package_identity current ON current.source_package_id = mismatch.source_package_id
           WHERE mismatch.source_package_mismatch_id = ?""",
        (source_package_mismatch_id,),
    ).fetchone()
    if mismatch is None:
        raise ValueError("Source package mismatch does not exist")
    if decision_code == "Event Number Corrected" and mismatch["current_event_number"] != mismatch["submitted_normalized_package_number"]:
        raise ValueError("Event Number Corrected requires the current event number to match submitted evidence")
    current = connection.execute(
        "SELECT * FROM v_current_source_package_mismatch_resolution WHERE source_package_mismatch_id = ?",
        (source_package_mismatch_id,),
    ).fetchone()
    action = connection.execute(
        """SELECT current.* FROM buyer_action action
           JOIN v_current_buyer_action current
             ON current.buyer_action_id = action.buyer_action_id
           WHERE action.governing_entity_type = 'Source Package Mismatch'
             AND action.governing_entity_id = ?""",
        (source_package_mismatch_id,),
    ).fetchone()
    if action is None:
        raise ValueError("Source package mismatch has no buyer action")
    resolution_id = uuid7()
    action_version_id = uuid7()
    action_status = "Accepted Exception" if decision_code == "Supplier Correction Required" else "Resolved"
    with immediate_transaction(connection):
        connection.execute(
            "INSERT INTO source_package_mismatch_resolution VALUES (?, ?, ?, ?, ?, ?, ?)",
            (resolution_id, source_package_mismatch_id, decision_code, decision_reason.strip(),
             decided_by_user_id, None if current is None else current["source_package_mismatch_resolution_id"],
             recorded_at_utc),
        )
        connection.execute(
            """INSERT INTO buyer_action_version
               (buyer_action_version_id, buyer_action_id, action_status,
                financial_impact_coefficient, financial_impact_scale,
                currency_id, required_supplier_action, owner_user_id,
                resolution_reason, supersedes_action_version_id,
                recorded_by_user_id, recorded_at_utc)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (action_version_id, action["buyer_action_id"], action_status,
             action["financial_impact_coefficient"], action["financial_impact_scale"],
             action["currency_id"], decision_code, decided_by_user_id,
             decision_reason.strip(), action["buyer_action_version_id"],
             decided_by_user_id, recorded_at_utc),
        )
        append_audit_event(connection, audit, {
            "source_package_mismatch_id": source_package_mismatch_id,
            "source_package_mismatch_resolution_id": resolution_id,
            "decision_code": decision_code, "decision_reason": decision_reason.strip(),
            "buyer_action_id": action["buyer_action_id"],
            "buyer_action_version_id": action_version_id,
            "action_status": action_status,
        })
    return resolution_id
