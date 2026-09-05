from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from typing import Any

from .audit import canonical_json
from .connection import immediate_transaction
from .ids import uuid7


@dataclass(frozen=True)
class RegistryAuditContext:
    event_type: str
    actor_user_id: str
    occurred_at_utc: str
    application_version: str
    reason_code: str
    reason_text: str | None = None


def _append_registry_audit(
    connection: sqlite3.Connection,
    context: RegistryAuditContext,
    payload: Any,
) -> tuple[str, int, str]:
    if not connection.in_transaction:
        raise RuntimeError("Registry audit must be appended inside the governing transaction")
    prior = connection.execute(
        "SELECT recorded_sequence, event_hash FROM registry_audit_event ORDER BY recorded_sequence DESC LIMIT 1"
    ).fetchone()
    sequence = 1 if prior is None else int(prior[0]) + 1
    prior_hash = None if prior is None else str(prior[1])
    envelope = {
        "sequence": sequence,
        "prior_event_hash": prior_hash,
        "context": {
            "event_type": context.event_type,
            "actor_user_id": context.actor_user_id,
            "occurred_at_utc": context.occurred_at_utc,
            "application_version": context.application_version,
            "reason_code": context.reason_code,
            "reason_text": context.reason_text,
        },
        "payload": payload,
    }
    digest = hashlib.sha256(canonical_json(envelope).encode("utf-8")).hexdigest()
    audit_id = uuid7()
    connection.execute(
        """INSERT INTO registry_audit_event
           (audit_event_id, recorded_sequence, event_type, actor_user_id,
            occurred_at_utc, application_version, reason_code, reason_text,
            event_payload, prior_event_hash, event_hash)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            audit_id, sequence, context.event_type, context.actor_user_id,
            context.occurred_at_utc, context.application_version,
            context.reason_code, context.reason_text,
            canonical_json(payload), prior_hash, digest,
        ),
    )
    return audit_id, sequence, digest


def verify_registry_audit_chain(connection: sqlite3.Connection) -> list[str]:
    failures: list[str] = []
    prior_hash: str | None = None
    expected_sequence = 1
    for row in connection.execute("SELECT * FROM registry_audit_event ORDER BY recorded_sequence"):
        sequence = int(row["recorded_sequence"])
        if sequence != expected_sequence:
            failures.append(f"Expected sequence {expected_sequence}, found {sequence}")
        if row["prior_event_hash"] != prior_hash:
            failures.append(f"Sequence {sequence} prior hash mismatch")
        context = {
            "event_type": row["event_type"],
            "actor_user_id": row["actor_user_id"],
            "occurred_at_utc": row["occurred_at_utc"],
            "application_version": row["application_version"],
            "reason_code": row["reason_code"],
            "reason_text": row["reason_text"],
        }
        envelope = {
            "sequence": sequence,
            "prior_event_hash": prior_hash,
            "context": context,
            "payload": json.loads(row["event_payload"]),
        }
        expected_hash = hashlib.sha256(canonical_json(envelope).encode("utf-8")).hexdigest()
        if row["event_hash"] != expected_hash:
            failures.append(f"Sequence {sequence} event hash mismatch")
        prior_hash = row["event_hash"]
        expected_sequence = sequence + 1
    return failures


def registry_publication_entities(connection: sqlite3.Connection) -> tuple[dict[str, object], ...]:
    """Return deterministic current registry entities for signing and cache publication."""
    entities: list[dict[str, object]] = []
    for row in connection.execute(
        """SELECT stable_commodity_id, commodity_code, locked_name, status
           FROM v_current_commodity ORDER BY stable_commodity_id"""
    ):
        entities.append({"entity_type": "Commodity", "entity_id": row[0],
                         "payload": {"commodity_code": row[1], "locked_name": row[2],
                                     "status": row[3]}})
    for row in connection.execute(
        """SELECT stable_supplier_id, supplier_code, legal_name, display_name, status
           FROM v_current_supplier ORDER BY stable_supplier_id"""
    ):
        entities.append({"entity_type": "Supplier", "entity_id": row[0],
                         "payload": {"supplier_code": row[1], "legal_name": row[2],
                                     "display_name": row[3], "status": row[4]}})
    for row in connection.execute(
        """SELECT stable_part_id, normalized_part_number, displayed_part_number,
                  canonical_description, status
           FROM v_current_part ORDER BY stable_part_id"""
    ):
        ownership = connection.execute(
            "SELECT stable_commodity_id FROM part_commodity_assignment WHERE stable_part_id = ?",
            (row[0],),
        ).fetchone()
        entities.append({"entity_type": "Part", "entity_id": row[0],
                         "payload": {"normalized_part_number": row[1],
                                     "displayed_part_number": row[2],
                                     "canonical_description": row[3], "status": row[4],
                                     "stable_commodity_id": ownership[0] if ownership else None}})
    for row in connection.execute(
        """SELECT stable_family_id, family_name FROM v_current_supplier_family
           ORDER BY stable_family_id"""
    ):
        members = tuple(str(member[0]) for member in connection.execute(
            """SELECT stable_supplier_id FROM v_current_supplier_family_member
               WHERE stable_family_id = ? AND business_valid_to IS NULL
               ORDER BY stable_supplier_id""", (row[0],),
        ))
        entities.append({"entity_type": "Supplier Family", "entity_id": row[0],
                         "payload": {"family_name": row[1],
                                     "member_supplier_ids": members}})
    return tuple(sorted(entities, key=lambda item: (str(item["entity_type"]),
                                                     str(item["entity_id"]))))


def _require_master(connection: sqlite3.Connection, user_id: str, as_of: str) -> None:
    authorized = connection.execute(
        """SELECT 1
           FROM authority_assignment aa
           JOIN role_definition rd ON rd.stable_role_id = aa.stable_role_id
           WHERE aa.stable_user_id = ?
             AND aa.scope_type = 'Company'
             AND rd.role_code = 'MASTER'
             AND aa.business_valid_from <= ?
             AND (aa.business_valid_to IS NULL OR aa.business_valid_to > ?)
             AND rd.business_valid_from <= ?
             AND (rd.business_valid_to IS NULL OR rd.business_valid_to > ?)
             AND NOT EXISTS (
                 SELECT 1 FROM authority_assignment newer
                 WHERE newer.supersedes_authority_version_id = aa.authority_version_id)
             AND NOT EXISTS (
                 SELECT 1 FROM role_definition newer_role
                 WHERE newer_role.supersedes_role_version_id = rd.role_version_id)
           LIMIT 1""",
        (user_id, as_of, as_of, as_of, as_of),
    ).fetchone()
    if authorized is None:
        raise PermissionError("Forge X master authority is required")


def create_commodity(
    connection: sqlite3.Connection,
    *,
    commodity_code: str,
    locked_name: str,
    confirmed_by_user_id: str,
    business_valid_from: str,
    recorded_at_utc: str,
    audit: RegistryAuditContext,
) -> tuple[str, str]:
    normalized_code = commodity_code.strip().upper()
    normalized_name = " ".join(locked_name.split()).upper()
    if not normalized_code or not normalized_name:
        raise ValueError("Commodity code and name are required")
    stable_id = uuid7()
    version_id = uuid7()
    with immediate_transaction(connection):
        connection.execute(
            """INSERT INTO commodity
               (commodity_version_id, stable_commodity_id, commodity_code,
                locked_name, normalized_name, status, business_valid_from,
                approved_by_user_id, recorded_at_utc)
               VALUES (?, ?, ?, ?, ?, 'Active', ?, ?, ?)""",
            (
                version_id, stable_id, normalized_code, locked_name.strip(),
                normalized_name, business_valid_from, confirmed_by_user_id,
                recorded_at_utc,
            ),
        )
        _append_registry_audit(
            connection, audit,
            {"stable_commodity_id": stable_id, "commodity_version_id": version_id, "commodity_code": normalized_code, "locked_name": locked_name.strip()},
        )
    return stable_id, version_id


def correct_commodity(
    connection: sqlite3.Connection,
    *,
    stable_commodity_id: str,
    corrected_code: str,
    corrected_name: str,
    master_user_id: str,
    correction_reason: str,
    business_valid_from: str,
    recorded_at_utc: str,
    audit: RegistryAuditContext,
) -> str:
    if not correction_reason.strip():
        raise ValueError("Commodity correction requires a reason")
    version_id = uuid7()
    with immediate_transaction(connection):
        _require_master(connection, master_user_id, recorded_at_utc)
        current = connection.execute(
            "SELECT * FROM v_current_commodity WHERE stable_commodity_id = ?",
            (stable_commodity_id,),
        ).fetchone()
        if current is None:
            raise ValueError("Current commodity identity does not exist")
        normalized_code = corrected_code.strip().upper()
        normalized_name = " ".join(corrected_name.split()).upper()
        connection.execute(
            """INSERT INTO commodity
               (commodity_version_id, stable_commodity_id, commodity_code,
                locked_name, normalized_name, status, business_valid_from,
                approved_by_user_id, recorded_at_utc,
                supersedes_commodity_version_id)
               VALUES (?, ?, ?, ?, ?, 'Active', ?, ?, ?, ?)""",
            (
                version_id, stable_commodity_id, normalized_code,
                corrected_name.strip(), normalized_name,
                business_valid_from, master_user_id, recorded_at_utc,
                current["commodity_version_id"],
            ),
        )
        _append_registry_audit(
            connection, audit,
            {
                "stable_commodity_id": stable_commodity_id,
                "new_commodity_version_id": version_id,
                "supersedes_commodity_version_id": current["commodity_version_id"],
                "before": {"commodity_code": current["commodity_code"], "locked_name": current["locked_name"]},
                "after": {"commodity_code": normalized_code, "locked_name": corrected_name.strip()},
                "correction_reason": correction_reason,
            },
        )
    return version_id


def create_supplier(
    connection: sqlite3.Connection,
    *,
    supplier_code: str,
    display_name: str,
    legal_name: str | None,
    confirmed_by_user_id: str,
    business_valid_from: str,
    recorded_at_utc: str,
    audit: RegistryAuditContext,
) -> tuple[str, str]:
    stable_id, version_id = uuid7(), uuid7()
    with immediate_transaction(connection):
        connection.execute(
            """INSERT INTO supplier
               (supplier_version_id, stable_supplier_id, supplier_code,
                legal_name, display_name, status, business_valid_from,
                confirmed_by_user_id, recorded_at_utc)
               VALUES (?, ?, ?, ?, ?, 'Active', ?, ?, ?)""",
            (
                version_id, stable_id, supplier_code.strip().upper(),
                legal_name, display_name.strip(), business_valid_from,
                confirmed_by_user_id, recorded_at_utc,
            ),
        )
        _append_registry_audit(connection, audit, {"stable_supplier_id": stable_id, "supplier_version_id": version_id, "supplier_code": supplier_code.strip().upper()})
    return stable_id, version_id


def create_part(
    connection: sqlite3.Connection,
    *,
    part_number: str,
    canonical_description: str,
    stable_commodity_id: str,
    commodity_assignment_reason: str,
    confirmed_by_user_id: str,
    business_valid_from: str,
    recorded_at_utc: str,
    audit: RegistryAuditContext,
) -> tuple[str, str]:
    stable_id, version_id = uuid7(), uuid7()
    normalized = part_number.strip().upper()
    if len(normalized) != 10 or not normalized.isalnum() or not normalized.isascii():
        raise ValueError("Canonical part number must contain exactly 10 ASCII alphanumeric characters")
    if not canonical_description.strip():
        raise ValueError("Canonical part description is required")
    if not commodity_assignment_reason.strip():
        raise ValueError("Part commodity assignment requires a reason")
    with immediate_transaction(connection):
        if connection.execute(
            """SELECT 1 FROM v_current_commodity
               WHERE stable_commodity_id = ? AND status = 'Active'""",
            (stable_commodity_id,),
        ).fetchone() is None:
            raise ValueError("Part requires an active governing commodity")
        connection.execute(
            """INSERT INTO part
               (part_version_id, stable_part_id, normalized_part_number,
                displayed_part_number, canonical_description, status,
                business_valid_from, confirmed_by_user_id, recorded_at_utc)
               VALUES (?, ?, ?, ?, ?, 'Active', ?, ?, ?)""",
            (
                version_id, stable_id, normalized, part_number.strip(),
                canonical_description.strip(), business_valid_from,
                confirmed_by_user_id, recorded_at_utc,
            ),
        )
        assignment_id = uuid7()
        connection.execute(
            """INSERT INTO part_commodity_assignment VALUES (?, ?, ?, ?, ?, ?)""",
            (assignment_id, stable_id, stable_commodity_id, confirmed_by_user_id,
             commodity_assignment_reason.strip(), recorded_at_utc),
        )
        _append_registry_audit(connection, audit, {
            "stable_part_id": stable_id, "part_version_id": version_id,
            "normalized_part_number": normalized,
            "part_commodity_assignment_id": assignment_id,
            "stable_commodity_id": stable_commodity_id,
        })
    return stable_id, version_id


def verify_registry_integrity(connection: sqlite3.Connection) -> list[str]:
    failures = verify_registry_audit_chain(connection)
    for version_id, number, description in connection.execute(
        "SELECT part_version_id, normalized_part_number, canonical_description FROM part"
    ):
        if len(number) != 10 or number != number.upper() or not number.isascii() or not number.isalnum():
            failures.append(f"Part version {version_id} has invalid canonical part number")
        if description is None or not description.strip():
            failures.append(f"Part version {version_id} has no canonical description")
    for stable_part_id, in connection.execute(
        """SELECT DISTINCT stable_part_id FROM part
           WHERE NOT EXISTS (SELECT 1 FROM part_commodity_assignment assignment
                             WHERE assignment.stable_part_id = part.stable_part_id)"""
    ):
        failures.append(f"Part {stable_part_id} has no governing commodity assignment")
    for stable_family_id, family_name in connection.execute(
        "SELECT stable_family_id, family_name FROM v_current_supplier_family"
    ):
        if not str(family_name).strip():
            failures.append(f"Supplier family {stable_family_id} has no name")
    for stable_id, table, version_key, predecessor_key in (
        (row[0], "supplier_family", "family_version_id", "supersedes_family_version_id")
        for row in connection.execute("SELECT DISTINCT stable_family_id FROM supplier_family")
    ):
        rows = connection.execute(
            f"SELECT {version_key}, {predecessor_key} FROM {table} WHERE stable_family_id = ?",
            (stable_id,),
        ).fetchall()
        identifiers = {str(row[0]) for row in rows}
        roots = [row for row in rows if row[1] is None]
        tips = [row for row in rows if not any(child[1] == row[0] for child in rows)]
        if len(roots) != 1 or len(tips) != 1 or any(
            row[1] is not None and str(row[1]) not in identifiers for row in rows
        ):
            failures.append(f"Supplier family {stable_id} has an invalid version chain")
    for stable_membership_id, in connection.execute(
        "SELECT DISTINCT stable_membership_id FROM supplier_family_member"
    ):
        rows = connection.execute(
            """SELECT membership_version_id, supersedes_membership_version_id,
                      stable_family_id, stable_supplier_id
               FROM supplier_family_member WHERE stable_membership_id = ?""",
            (stable_membership_id,),
        ).fetchall()
        identifiers = {str(row[0]) for row in rows}
        roots = [row for row in rows if row[1] is None]
        tips = [row for row in rows if not any(child[1] == row[0] for child in rows)]
        if len(roots) != 1 or len(tips) != 1 or any(
            row[1] is not None and str(row[1]) not in identifiers for row in rows
        ):
            failures.append(f"Supplier family membership {stable_membership_id} has an invalid version chain")
        if any(connection.execute(
            "SELECT 1 FROM supplier_family WHERE stable_family_id = ?", (row[2],)
        ).fetchone() is None or connection.execute(
            "SELECT 1 FROM supplier WHERE stable_supplier_id = ?", (row[3],)
        ).fetchone() is None for row in rows):
            failures.append(f"Supplier family membership {stable_membership_id} has invalid entity lineage")
    for stable_supplier_id, count in connection.execute(
        """SELECT stable_supplier_id, COUNT(*) FROM v_current_supplier_family_member
           WHERE business_valid_to IS NULL GROUP BY stable_supplier_id HAVING COUNT(*) > 1"""
    ):
        failures.append(f"Supplier {stable_supplier_id} has {count} active family memberships")
    for proposal_id, status, family_id, supplier_id in connection.execute(
        """SELECT proposal.supplier_family_proposal_id, decision.decision_status,
                  decision.stable_family_id, proposal.stable_supplier_id
           FROM supplier_family_proposal proposal
           JOIN supplier_family_proposal_decision decision
             ON decision.supplier_family_proposal_id = proposal.supplier_family_proposal_id"""
    ):
        if status == "Approved" and connection.execute(
            """SELECT 1 FROM supplier_family_member
               WHERE stable_family_id = ? AND stable_supplier_id = ?""",
            (family_id, supplier_id),
        ).fetchone() is None:
            failures.append(f"Approved supplier family proposal {proposal_id} has no membership evidence")
    return failures


def correct_part_description(
    connection: sqlite3.Connection, *, stable_part_id: str,
    corrected_description: str, master_user_id: str,
    correction_reason: str, business_valid_from: str,
    recorded_at_utc: str, audit: RegistryAuditContext,
) -> str:
    if not corrected_description.strip() or not correction_reason.strip():
        raise ValueError("Part description correction requires description and reason")
    version_id = uuid7()
    with immediate_transaction(connection):
        _require_master(connection, master_user_id, recorded_at_utc)
        current = connection.execute(
            "SELECT * FROM v_current_part WHERE stable_part_id = ?",
            (stable_part_id,),
        ).fetchone()
        if current is None:
            raise ValueError("Current part identity does not exist")
        assignment = connection.execute(
            """SELECT stable_commodity_id FROM part_commodity_assignment
               WHERE stable_part_id = ?""", (stable_part_id,),
        ).fetchone()
        if assignment is None:
            raise ValueError("Part has no permanent commodity ownership")
        connection.execute(
            """INSERT INTO part
               (part_version_id, stable_part_id, normalized_part_number,
                displayed_part_number, canonical_description, status,
                business_valid_from, confirmed_by_user_id, recorded_at_utc,
                supersedes_part_version_id)
               VALUES (?, ?, ?, ?, ?, 'Active', ?, ?, ?, ?)""",
            (version_id, stable_part_id, current["normalized_part_number"],
             current["displayed_part_number"], corrected_description.strip(),
             business_valid_from, master_user_id, recorded_at_utc,
             current["part_version_id"]),
        )
        _append_registry_audit(connection, audit, {
            "stable_part_id": stable_part_id,
            "new_part_version_id": version_id,
            "supersedes_part_version_id": current["part_version_id"],
            "normalized_part_number": current["normalized_part_number"],
            "stable_commodity_id": assignment[0],
            "before_description": current["canonical_description"],
            "after_description": corrected_description.strip(),
            "correction_reason": correction_reason.strip(),
        })
    return version_id


def propose_supplier_family(
    connection: sqlite3.Connection, *, stable_supplier_id: str,
    proposal_reason: str, proposed_by_user_id: str, proposed_at_utc: str,
    audit: RegistryAuditContext, proposed_family_name: str | None = None,
    proposed_stable_family_id: str | None = None,
) -> str:
    if (proposed_family_name is None) == (proposed_stable_family_id is None):
        raise ValueError("Propose either a new supplier family or an existing family")
    if not proposal_reason.strip() or not proposed_by_user_id.strip():
        raise ValueError("Supplier family proposal requires buyer and rationale")
    proposal_id = uuid7()
    with immediate_transaction(connection):
        if connection.execute(
            "SELECT 1 FROM v_current_supplier WHERE stable_supplier_id = ?",
            (stable_supplier_id,),
        ).fetchone() is None:
            raise ValueError("Supplier family proposal requires a current supplier")
        if proposed_stable_family_id is not None and connection.execute(
            "SELECT 1 FROM v_current_supplier_family WHERE stable_family_id = ?",
            (proposed_stable_family_id,),
        ).fetchone() is None:
            raise ValueError("Proposed supplier family does not exist")
        connection.execute(
            "INSERT INTO supplier_family_proposal VALUES (?, ?, ?, ?, ?, ?, ?)",
            (proposal_id, stable_supplier_id,
             None if proposed_family_name is None else proposed_family_name.strip(),
             proposed_stable_family_id, proposal_reason.strip(),
             proposed_by_user_id, proposed_at_utc),
        )
        _append_registry_audit(connection, audit, {
            "supplier_family_proposal_id": proposal_id,
            "stable_supplier_id": stable_supplier_id,
            "proposed_family_name": proposed_family_name,
            "proposed_stable_family_id": proposed_stable_family_id,
        })
    return proposal_id


def decide_supplier_family_proposal(
    connection: sqlite3.Connection, *, supplier_family_proposal_id: str,
    decision_status: str, decision_reason: str, master_user_id: str,
    decided_at_utc: str, business_valid_from: str,
    audit: RegistryAuditContext,
) -> tuple[str, str | None]:
    if decision_status not in {"Approved", "Rejected"} or not decision_reason.strip():
        raise ValueError("Supplier family decision requires Approved/Rejected and rationale")
    decision_id = uuid7()
    with immediate_transaction(connection):
        _require_master(connection, master_user_id, decided_at_utc)
        proposal = connection.execute(
            """SELECT proposal.* FROM supplier_family_proposal proposal
               LEFT JOIN supplier_family_proposal_decision decision
                 ON decision.supplier_family_proposal_id = proposal.supplier_family_proposal_id
               WHERE proposal.supplier_family_proposal_id = ?
                 AND decision.supplier_family_proposal_id IS NULL""",
            (supplier_family_proposal_id,),
        ).fetchone()
        if proposal is None:
            raise ValueError("Supplier family proposal does not exist or is already decided")
        family_id: str | None = None
        if decision_status == "Approved":
            family_id = proposal["proposed_stable_family_id"]
            if family_id is None:
                family_id, family_version_id = uuid7(), uuid7()
                connection.execute(
                    """INSERT INTO supplier_family VALUES
                       (?, ?, ?, ?, NULL, ?, ?, ?, NULL)""",
                    (family_version_id, family_id, proposal["proposed_family_name"],
                     business_valid_from, master_user_id, decision_reason.strip(), decided_at_utc),
                )
            connection.execute(
                """INSERT INTO supplier_family_member VALUES
                   (?, ?, ?, ?, ?, NULL, ?, ?, ?, NULL)""",
                (uuid7(), uuid7(), family_id, proposal["stable_supplier_id"],
                 business_valid_from, master_user_id, decision_reason.strip(), decided_at_utc),
            )
        connection.execute(
            "INSERT INTO supplier_family_proposal_decision VALUES (?, ?, ?, ?, ?, ?, ?)",
            (decision_id, supplier_family_proposal_id, decision_status, family_id,
             decision_reason.strip(), master_user_id, decided_at_utc),
        )
        _append_registry_audit(connection, audit, {
            "supplier_family_proposal_id": supplier_family_proposal_id,
            "supplier_family_proposal_decision_id": decision_id,
            "decision_status": decision_status, "stable_family_id": family_id,
        })
    return decision_id, family_id


def remove_supplier_from_family(
    connection: sqlite3.Connection, *, stable_supplier_id: str,
    removal_reason: str, master_user_id: str, removed_at_utc: str,
    audit: RegistryAuditContext,
) -> str:
    if not removal_reason.strip():
        raise ValueError("Supplier family removal requires a reason")
    version_id = uuid7()
    with immediate_transaction(connection):
        _require_master(connection, master_user_id, removed_at_utc)
        current = connection.execute(
            """SELECT * FROM v_current_supplier_family_member
               WHERE stable_supplier_id = ? AND business_valid_to IS NULL""",
            (stable_supplier_id,),
        ).fetchone()
        if current is None:
            raise ValueError("Supplier has no active family membership")
        connection.execute(
            """INSERT INTO supplier_family_member VALUES
               (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (version_id, current["stable_membership_id"], current["stable_family_id"],
             stable_supplier_id, current["business_valid_from"], removed_at_utc,
             master_user_id, removal_reason.strip(), removed_at_utc,
             current["membership_version_id"]),
        )
        _append_registry_audit(connection, audit, {
            "stable_supplier_id": stable_supplier_id,
            "stable_family_id": current["stable_family_id"],
            "removed_membership_version_id": version_id,
            "removal_reason": removal_reason.strip(),
        })
    return version_id
