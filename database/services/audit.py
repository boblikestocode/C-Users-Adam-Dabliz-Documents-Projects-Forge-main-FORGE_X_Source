from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import asdict, dataclass
from typing import Any

from .ids import uuid7


GENESIS_PRIOR_HASH = None


@dataclass(frozen=True)
class AuditContext:
    event_type: str
    actor_user_id: str | None
    effective_authority: str | None
    occurred_at_utc: str
    display_timezone: str
    workstation_session: str | None
    application_version: str
    action_method: str
    reason_code: str
    reason_text: str | None = None


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def event_hash(sequence: int, prior_hash: str | None, context: AuditContext, payload: Any) -> str:
    envelope = {
        "sequence": sequence,
        "prior_event_hash": prior_hash,
        "context": asdict(context),
        "payload": payload,
    }
    return hashlib.sha256(canonical_json(envelope).encode("utf-8")).hexdigest()


def append_audit_event(
    connection: sqlite3.Connection,
    context: AuditContext,
    payload: Any,
    details: list[dict[str, Any]] | None = None,
) -> tuple[str, int, str]:
    if not connection.in_transaction:
        raise RuntimeError("Audit event must be appended inside the governing transaction")
    prior = connection.execute(
        "SELECT recorded_sequence, event_hash FROM audit_event ORDER BY recorded_sequence DESC LIMIT 1"
    ).fetchone()
    sequence = 1 if prior is None else int(prior[0]) + 1
    prior_hash = GENESIS_PRIOR_HASH if prior is None else str(prior[1])
    payload_text = canonical_json(payload)
    digest = event_hash(sequence, prior_hash, context, payload)
    audit_id = uuid7()
    connection.execute(
        """INSERT INTO audit_event
           (audit_event_id, recorded_sequence, event_type, actor_user_id,
            effective_authority, occurred_at_utc, display_timezone,
            workstation_session, application_version, action_method,
            reason_code, reason_text, event_payload, prior_event_hash, event_hash)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            audit_id, sequence, context.event_type, context.actor_user_id,
            context.effective_authority, context.occurred_at_utc,
            context.display_timezone, context.workstation_session,
            context.application_version, context.action_method,
            context.reason_code, context.reason_text, payload_text,
            prior_hash, digest,
        ),
    )
    for detail in details or []:
        connection.execute(
            """INSERT INTO audit_event_detail
               (audit_event_detail_id, audit_event_id, entity_type, entity_id,
                field_path, before_payload, after_payload, recorded_at_utc)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                uuid7(), audit_id, detail["entity_type"], detail["entity_id"],
                detail.get("field_path"),
                canonical_json(detail["before"]) if "before" in detail else None,
                canonical_json(detail["after"]) if "after" in detail else None,
                context.occurred_at_utc,
            ),
        )
    return audit_id, sequence, digest


def verify_audit_chain(connection: sqlite3.Connection) -> list[str]:
    failures: list[str] = []
    prior_hash: str | None = GENESIS_PRIOR_HASH
    expected_sequence = 1
    for row in connection.execute("SELECT * FROM audit_event ORDER BY recorded_sequence"):
        sequence = int(row["recorded_sequence"])
        if sequence != expected_sequence:
            failures.append(f"Expected sequence {expected_sequence}, found {sequence}")
        if row["prior_event_hash"] != prior_hash:
            failures.append(f"Sequence {sequence} prior hash mismatch")
        context = AuditContext(
            event_type=row["event_type"],
            actor_user_id=row["actor_user_id"],
            effective_authority=row["effective_authority"],
            occurred_at_utc=row["occurred_at_utc"],
            display_timezone=row["display_timezone"],
            workstation_session=row["workstation_session"],
            application_version=row["application_version"],
            action_method=row["action_method"],
            reason_code=row["reason_code"],
            reason_text=row["reason_text"],
        )
        payload = json.loads(row["event_payload"])
        expected_hash = event_hash(sequence, prior_hash, context, payload)
        if row["event_hash"] != expected_hash:
            failures.append(f"Sequence {sequence} event hash mismatch")
        prior_hash = row["event_hash"]
        expected_sequence = sequence + 1
    return failures
