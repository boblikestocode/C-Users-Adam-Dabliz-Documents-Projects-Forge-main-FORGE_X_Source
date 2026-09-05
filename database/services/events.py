from __future__ import annotations

import sqlite3

from .audit import AuditContext, append_audit_event
from .connection import immediate_transaction
from .ids import uuid7


TRANSITIONS = {"Setup": "Active", "Active": "Finalized", "Finalized": "Closed"}


def standardized_event_name(
    connection: sqlite3.Connection, *, event_id: str, evidence_cutoff_utc: str,
) -> str:
    row = connection.execute(
        """SELECT event.readable_name,
                  COALESCE((SELECT correction.corrected_displayed_package_number
                            FROM source_package_number_correction correction
                            WHERE correction.source_package_id = package.source_package_id
                              AND correction.recorded_at_utc <= ?
                            ORDER BY correction.recorded_at_utc DESC,
                                     correction.source_package_number_correction_id DESC LIMIT 1),
                           package.displayed_package_number)
           FROM sourcing_event event JOIN source_package package
             ON package.event_id = event.event_id
           WHERE event.event_id = ? AND event.created_at_utc <= ?
             AND package.recorded_at_utc <= ?
           ORDER BY CASE package.package_role WHEN 'Primary' THEN 0
                         WHEN 'Replacement' THEN 1 WHEN 'Revised' THEN 2 ELSE 3 END,
                    package.recorded_at_utc DESC, package.source_package_id DESC LIMIT 1""",
        (evidence_cutoff_utc, event_id, evidence_cutoff_utc, evidence_cutoff_utc),
    ).fetchone()
    if row is None:
        raise ValueError("Sourcing event has no source package at cutoff")
    return f"{row[1]} - {row[0]}"


def create_sourcing_event(
    connection: sqlite3.Connection, *, commodity_id: str, readable_name: str,
    buyer_code_id: str, primary_buyer_user_id: str, created_at_utc: str,
    audit: AuditContext,
) -> str:
    if not all(value.strip() for value in
               (commodity_id, readable_name, buyer_code_id, primary_buyer_user_id)):
        raise ValueError("Sourcing event identity fields cannot be blank")
    event_id = uuid7()
    status_id = uuid7()
    with immediate_transaction(connection):
        connection.execute(
            "INSERT INTO sourcing_event VALUES (?, ?, ?, ?, ?, 'Setup', ?)",
            (event_id, commodity_id, readable_name.strip(), buyer_code_id,
             primary_buyer_user_id, created_at_utc),
        )
        connection.execute(
            """INSERT INTO sourcing_event_status_event VALUES
               (?, ?, 'Setup', 'Sourcing event created', ?, NULL, ?)""",
            (status_id, event_id, primary_buyer_user_id, created_at_utc),
        )
        append_audit_event(connection, audit, {
            "event_id": event_id, "event_status": "Setup", "commodity_id": commodity_id,
            "buyer_code_id": buyer_code_id,
        })
    return event_id


def transition_sourcing_event(
    connection: sqlite3.Connection, *, event_id: str, target_status: str,
    transition_reason: str, decided_by_user_id: str, recorded_at_utc: str,
    audit: AuditContext,
) -> str:
    if not transition_reason.strip() or not decided_by_user_id.strip():
        raise ValueError("Event transition requires decision authority and a reason")
    current = connection.execute(
        """SELECT sourcing_event_status_event_id, event_status, recorded_at_utc
           FROM v_current_sourcing_event_status WHERE event_id = ?""", (event_id,),
    ).fetchone()
    if current is None:
        raise ValueError("Sourcing event does not exist or has no status history")
    if TRANSITIONS.get(str(current[1])) != target_status:
        raise ValueError(f"Invalid sourcing-event transition: {current[1]} -> {target_status}")
    if recorded_at_utc < str(current[2]):
        raise ValueError("Event transition cannot predate current status")
    if target_status == "Finalized" and connection.execute(
        """SELECT 1 FROM finalized_snapshot snapshot JOIN analysis analysis
             ON analysis.analysis_id = snapshot.analysis_id
           WHERE analysis.event_id = ? LIMIT 1""", (event_id,),
    ).fetchone() is None:
        raise ValueError("Finalized event status requires a finalized analysis snapshot")
    status_id = uuid7()
    with immediate_transaction(connection):
        connection.execute(
            """INSERT INTO sourcing_event_status_event VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (status_id, event_id, target_status, transition_reason.strip(),
             decided_by_user_id, current[0], recorded_at_utc),
        )
        append_audit_event(connection, audit, {
            "event_id": event_id, "prior_status": str(current[1]),
            "event_status": target_status, "transition_reason": transition_reason.strip(),
            "sourcing_event_status_event_id": status_id,
        })
    return status_id


def correct_source_package_number(
    connection: sqlite3.Connection, *, source_package_id: str,
    corrected_package_number: str, correction_reason: str,
    corrected_by_user_id: str, recorded_at_utc: str, audit: AuditContext,
) -> str:
    displayed = corrected_package_number.strip()
    normalized = "".join(displayed.upper().split())
    if not normalized or not correction_reason.strip() or not corrected_by_user_id.strip():
        raise ValueError("Source package correction requires a number, reason, and buyer identity")
    correction_id = uuid7()
    with immediate_transaction(connection):
        current = connection.execute(
            """SELECT * FROM v_current_source_package_identity
               WHERE source_package_id = ?""", (source_package_id,),
        ).fetchone()
        if current is None:
            raise ValueError("Source package does not exist")
        if normalized == current["normalized_package_number"]:
            raise ValueError("Corrected source package number must change the current number")
        connection.execute(
            """INSERT INTO source_package_number_correction VALUES
               (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (correction_id, source_package_id,
             current["normalized_package_number"], current["displayed_package_number"],
             normalized, displayed, correction_reason.strip(), corrected_by_user_id,
             current["source_package_number_correction_id"], recorded_at_utc),
        )
        append_audit_event(connection, audit, {
            "source_package_id": source_package_id,
            "source_package_number_correction_id": correction_id,
            "original_package_number": current["displayed_package_number"],
            "corrected_package_number": displayed,
            "correction_reason": correction_reason.strip(),
            "corrected_by_user_id": corrected_by_user_id,
        })
    return correction_id
