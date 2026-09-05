from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from .audit import AuditContext, append_audit_event
from .connection import immediate_transaction
from .decimals import ExactDecimal
from .ids import uuid7


ACTION_STATUSES = {
    "Open", "Sent to Supplier", "Supplier Response Received",
    "Resolved", "Accepted Exception",
}

ACTION_ENTITIES = {
    "PBD Observation": ("pbd_observation", "observation_id"),
    "Formula Integrity Event": ("formula_integrity_event", "formula_integrity_event_id"),
    "Take Rate Validation": ("take_rate_validation", "take_rate_validation_id"),
    "Round Conflict": ("round_conflict", "round_conflict_id"),
    "Knowledge Conflict": ("knowledge_conflict", "knowledge_conflict_id"),
    "Sourcing Event": ("sourcing_event", "event_id"),
    "Source Package Mismatch": ("source_package_mismatch", "source_package_mismatch_id"),
    "Cross Border Reconstruction": ("cross_border_reconstruction", "cross_border_reconstruction_id"),
}

SUGGESTION_ENTITIES = {
    "PBD Observation": ("pbd_observation", "observation_id"),
    "Supplier Activity": ("supplier_activity", "supplier_activity_id"),
    "Quote Round": ("supplier_quote_round", "quote_round_id"),
    "Formula Integrity Event": ("formula_integrity_event", "formula_integrity_event_id"),
}


def _require(connection: sqlite3.Connection, entity_type: str, entity_id: str, catalog: dict[str, tuple[str, str]]) -> None:
    location = catalog.get(entity_type)
    if location is None:
        raise ValueError(f"Unsupported entity type: {entity_type}")
    table, key = location
    if connection.execute(f"SELECT 1 FROM {table} WHERE {key} = ?", (entity_id,)).fetchone() is None:
        raise ValueError(f"Entity does not exist: {entity_type} {entity_id}")


def create_buyer_action(
    connection: sqlite3.Connection,
    *,
    event_id: str,
    supplier_id: str | None,
    part_id: str | None,
    governing_entity_type: str,
    governing_entity_id: str,
    issue_type: str,
    financial_impact: ExactDecimal | None,
    currency_id: str | None,
    required_supplier_action: str,
    owner_user_id: str,
    created_by_user_id: str,
    created_at_utc: str,
    audit: AuditContext,
) -> str:
    if not required_supplier_action.strip():
        raise ValueError("Required supplier action cannot be blank")
    action_id = uuid7()
    version_id = uuid7()
    with immediate_transaction(connection):
        _require(connection, "Sourcing Event", event_id, ACTION_ENTITIES)
        _require(connection, governing_entity_type, governing_entity_id, ACTION_ENTITIES)
        connection.execute(
            """INSERT INTO buyer_action
               (buyer_action_id, event_id, supplier_id, part_id,
                governing_entity_type, governing_entity_id, issue_type,
                created_by_user_id, created_at_utc)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                action_id, event_id, supplier_id, part_id,
                governing_entity_type, governing_entity_id, issue_type,
                created_by_user_id, created_at_utc,
            ),
        )
        connection.execute(
            """INSERT INTO buyer_action_version
               (buyer_action_version_id, buyer_action_id, action_status,
                financial_impact_coefficient, financial_impact_scale,
                currency_id, required_supplier_action, owner_user_id,
                recorded_by_user_id, recorded_at_utc)
               VALUES (?, ?, 'Open', ?, ?, ?, ?, ?, ?, ?)""",
            (
                version_id, action_id,
                financial_impact.coefficient if financial_impact else None,
                financial_impact.scale if financial_impact else None,
                currency_id, required_supplier_action, owner_user_id,
                created_by_user_id, created_at_utc,
            ),
        )
        append_audit_event(
            connection, audit,
            {"buyer_action_id": action_id, "buyer_action_version_id": version_id, "status": "Open", "issue_type": issue_type},
        )
    return action_id


def transition_buyer_action(
    connection: sqlite3.Connection,
    *,
    buyer_action_id: str,
    new_status: str,
    required_supplier_action: str,
    owner_user_id: str,
    recorded_by_user_id: str,
    recorded_at_utc: str,
    transition_reason: str,
    working_note: str | None,
    audit: AuditContext,
) -> str:
    if new_status not in ACTION_STATUSES:
        raise ValueError("Unsupported buyer action status")
    if not transition_reason.strip():
        raise ValueError("Every buyer action transition requires a reason")
    version_id = uuid7()
    with immediate_transaction(connection):
        current = connection.execute(
            """SELECT * FROM v_current_buyer_action
               WHERE buyer_action_id = ?""",
            (buyer_action_id,),
        ).fetchone()
        if current is None:
            raise ValueError("Buyer action does not exist")
        resolution_reason = transition_reason if new_status in ("Resolved", "Accepted Exception") else None
        connection.execute(
            """INSERT INTO buyer_action_version
               (buyer_action_version_id, buyer_action_id, action_status,
                financial_impact_coefficient, financial_impact_scale,
                currency_id, required_supplier_action, owner_user_id,
                working_note, resolution_reason,
                supersedes_action_version_id, recorded_by_user_id,
                recorded_at_utc)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                version_id, buyer_action_id, new_status,
                current["financial_impact_coefficient"],
                current["financial_impact_scale"], current["currency_id"],
                required_supplier_action, owner_user_id, working_note,
                resolution_reason, current["buyer_action_version_id"],
                recorded_by_user_id, recorded_at_utc,
            ),
        )
        append_audit_event(
            connection, audit,
            {"buyer_action_id": buyer_action_id, "buyer_action_version_id": version_id, "before_status": current["action_status"], "after_status": new_status, "transition_reason": transition_reason},
        )
    return version_id


def suggest_resolution(
    connection: sqlite3.Connection,
    *,
    buyer_action_id: str,
    source_entity_type: str,
    source_entity_id: str,
    suggestion_rule_version_id: str,
    suggestion_reason: str,
    generated_at_utc: str,
    audit: AuditContext,
) -> str:
    stable_id = uuid7()
    version_id = uuid7()
    with immediate_transaction(connection):
        if connection.execute("SELECT 1 FROM buyer_action WHERE buyer_action_id = ?", (buyer_action_id,)).fetchone() is None:
            raise ValueError("Buyer action does not exist")
        _require(connection, source_entity_type, source_entity_id, SUGGESTION_ENTITIES)
        connection.execute(
            """INSERT INTO buyer_action_resolution_suggestion
               (suggestion_id, stable_suggestion_id, buyer_action_id,
                suggested_source_entity_type, suggested_source_entity_id,
                suggestion_rule_version_id, suggestion_reason,
                suggestion_status, generated_at_utc)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'Pending Buyer Review', ?)""",
            (
                version_id, stable_id, buyer_action_id, source_entity_type,
                source_entity_id, suggestion_rule_version_id,
                suggestion_reason, generated_at_utc,
            ),
        )
        append_audit_event(
            connection, audit,
            {"suggestion_id": stable_id, "suggestion_version_id": version_id, "buyer_action_id": buyer_action_id, "status": "Pending Buyer Review", "source_entity_type": source_entity_type, "source_entity_id": source_entity_id},
        )
    return stable_id


def review_resolution_suggestion(
    connection: sqlite3.Connection,
    *,
    stable_suggestion_id: str,
    decision: str,
    reviewed_by_user_id: str,
    reviewed_at_utc: str,
    review_reason: str,
    audit: AuditContext,
) -> str:
    if decision not in ("Accepted by Buyer", "Dismissed by Buyer"):
        raise ValueError("Suggestion review must accept or dismiss")
    if not review_reason.strip():
        raise ValueError("Suggestion review requires a reason")
    version_id = uuid7()
    with immediate_transaction(connection):
        current = connection.execute(
            """SELECT * FROM buyer_action_resolution_suggestion s
               WHERE s.stable_suggestion_id = ?
                 AND NOT EXISTS (
                     SELECT 1 FROM buyer_action_resolution_suggestion newer
                     WHERE newer.supersedes_suggestion_id = s.suggestion_id)
               ORDER BY s.generated_at_utc DESC LIMIT 1""",
            (stable_suggestion_id,),
        ).fetchone()
        if current is None or current["suggestion_status"] != "Pending Buyer Review":
            raise ValueError("Pending suggestion does not exist")
        connection.execute(
            """INSERT INTO buyer_action_resolution_suggestion
               (suggestion_id, stable_suggestion_id, buyer_action_id,
                suggested_source_entity_type, suggested_source_entity_id,
                suggestion_rule_version_id, suggestion_reason,
                suggestion_status, generated_at_utc, reviewed_by_user_id,
                reviewed_at_utc, review_reason, supersedes_suggestion_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                version_id, stable_suggestion_id, current["buyer_action_id"],
                current["suggested_source_entity_type"],
                current["suggested_source_entity_id"],
                current["suggestion_rule_version_id"],
                current["suggestion_reason"], decision,
                current["generated_at_utc"], reviewed_by_user_id,
                reviewed_at_utc, review_reason, current["suggestion_id"],
            ),
        )
        append_audit_event(
            connection, audit,
            {"suggestion_id": stable_suggestion_id, "suggestion_version_id": version_id, "buyer_action_id": current["buyer_action_id"], "status": decision, "review_reason": review_reason},
        )
    return version_id
