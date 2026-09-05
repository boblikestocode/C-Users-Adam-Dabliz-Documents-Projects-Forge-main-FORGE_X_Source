from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from typing import Any

from .audit import AuditContext, append_audit_event, canonical_json
from .connection import immediate_transaction
from .ids import uuid7


EVIDENCE_TABLES = {
    "PBD Observation": ("pbd_observation", "observation_id"),
    "Supplier Activity": ("supplier_activity", "supplier_activity_id"),
    "Formula Integrity Event": ("formula_integrity_event", "formula_integrity_event_id"),
    "Finalized Snapshot": ("finalized_snapshot", "finalized_snapshot_id"),
}


@dataclass(frozen=True)
class KnowledgeScope:
    supplier_id: str | None = None
    supplier_plant_id: str | None = None
    supplier_family_id: str | None = None
    commodity_id: str | None = None
    part_id: str | None = None
    functional_family_id: str | None = None
    process_code: str | None = None
    region_code: str | None = None
    program_id: str | None = None
    company_wide: bool = False

    def specificity(self) -> int:
        if self.company_wide:
            return 6
        if self.supplier_plant_id and self.part_id:
            return 1
        if self.supplier_plant_id and (self.functional_family_id or self.process_code):
            return 2
        if self.supplier_plant_id and self.commodity_id:
            return 3
        if self.supplier_family_id and self.region_code and self.commodity_id:
            return 4
        if self.region_code and self.commodity_id:
            return 5
        raise ValueError("Knowledge scope does not match an approved precedence level")

    def signature(self) -> str:
        payload = {
            "supplier_id": self.supplier_id,
            "supplier_plant_id": self.supplier_plant_id,
            "supplier_family_id": self.supplier_family_id,
            "commodity_id": self.commodity_id,
            "part_id": self.part_id,
            "functional_family_id": self.functional_family_id,
            "process_code": self.process_code,
            "region_code": self.region_code,
            "program_id": self.program_id,
            "company_wide": self.company_wide,
        }
        return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class KnowledgeContext:
    supplier_id: str | None = None
    supplier_plant_id: str | None = None
    supplier_family_id: str | None = None
    commodity_id: str | None = None
    part_id: str | None = None
    functional_family_id: str | None = None
    process_code: str | None = None
    region_code: str | None = None
    program_id: str | None = None


@dataclass(frozen=True)
class KnowledgeResolution:
    status: str
    selected_knowledge_version_id: str | None
    equivalent_version_ids: tuple[str, ...]
    conflicting_version_ids: tuple[str, ...]
    specificity: int | None


def create_knowledge_version(
    connection: sqlite3.Connection,
    *,
    knowledge_type: str,
    knowledge_level: str,
    payload: dict[str, Any],
    scope: KnowledgeScope,
    business_valid_from: str | None,
    business_valid_to: str | None,
    recorded_at_utc: str,
    created_by_user_id: str,
    confirmed_by_user_id: str | None,
    approval_authority_type: str | None,
    business_rationale: str | None,
    evidence: tuple[tuple[str, str, str], ...],
    audit: AuditContext,
    knowledge_item_id: str | None = None,
    supersedes_knowledge_version_id: str | None = None,
) -> tuple[str, str]:
    if knowledge_level not in {"Observed Evidence", "Confirmed Knowledge", "Corporate Standard"}:
        raise ValueError("Unsupported knowledge level")
    if knowledge_level != "Observed Evidence" and (not confirmed_by_user_id or not business_rationale):
        raise ValueError("Confirmed knowledge requires a confirmer and rationale")
    if knowledge_level == "Corporate Standard" and approval_authority_type != "Master":
        raise ValueError("Corporate standards require master authority")
    if not evidence:
        raise ValueError("Every knowledge version requires supporting or contextual evidence")
    specificity = scope.specificity()
    item_id = knowledge_item_id or uuid7()
    version_id = uuid7()
    payload_text = canonical_json(payload)
    payload_hash = hashlib.sha256(payload_text.encode("utf-8")).hexdigest()

    with immediate_transaction(connection):
        for entity_type, entity_id, _ in evidence:
            location = EVIDENCE_TABLES.get(entity_type)
            if location is None:
                raise ValueError(f"Unsupported knowledge evidence type: {entity_type}")
            table, key = location
            if connection.execute(
                f"SELECT 1 FROM {table} WHERE {key} = ?", (entity_id,)
            ).fetchone() is None:
                raise ValueError(f"Knowledge evidence does not exist: {entity_type} {entity_id}")
        if knowledge_item_id is None:
            connection.execute(
                "INSERT INTO knowledge_item VALUES (?, ?, ?, ?)",
                (item_id, knowledge_type, created_by_user_id, recorded_at_utc),
            )
            version_number = 1
        else:
            item = connection.execute(
                "SELECT knowledge_type FROM knowledge_item WHERE knowledge_item_id = ?",
                (item_id,),
            ).fetchone()
            if item is None or item[0] != knowledge_type:
                raise ValueError("Knowledge item does not exist or has a different type")
            latest = connection.execute(
                "SELECT MAX(version_number) FROM knowledge_version WHERE knowledge_item_id = ?",
                (item_id,),
            ).fetchone()[0]
            version_number = int(latest or 0) + 1
            if supersedes_knowledge_version_id is not None:
                prior = connection.execute(
                    """SELECT knowledge_item_id FROM knowledge_version
                       WHERE knowledge_version_id = ?""",
                    (supersedes_knowledge_version_id,),
                ).fetchone()
                if prior is None or prior[0] != item_id:
                    raise ValueError("Superseded knowledge version must belong to the same item")
        connection.execute(
            """INSERT INTO knowledge_version
               (knowledge_version_id, knowledge_item_id, version_number,
                knowledge_level, structured_payload, payload_hash, status,
                business_valid_from, business_valid_to, recorded_from_utc,
                confirmed_by_user_id, approval_authority_type,
                business_rationale, supersedes_knowledge_version_id)
               VALUES (?, ?, ?, ?, ?, ?, 'Active', ?, ?, ?, ?, ?, ?, ?)""",
            (
                version_id, item_id, version_number, knowledge_level,
                payload_text, payload_hash, business_valid_from,
                business_valid_to, recorded_at_utc, confirmed_by_user_id,
                approval_authority_type, business_rationale,
                supersedes_knowledge_version_id,
            ),
        )
        connection.execute(
            """INSERT INTO knowledge_scope
               (knowledge_scope_id, knowledge_version_id, supplier_id,
                supplier_plant_id, supplier_family_id, commodity_id, part_id,
                functional_family_id, process_code, region_code, program_id,
                company_wide_flag, scope_specificity, scope_signature,
                recorded_at_utc)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                uuid7(), version_id, scope.supplier_id,
                scope.supplier_plant_id, scope.supplier_family_id,
                scope.commodity_id, scope.part_id, scope.functional_family_id,
                scope.process_code, scope.region_code, scope.program_id,
                int(scope.company_wide), specificity, scope.signature(),
                recorded_at_utc,
            ),
        )
        for entity_type, entity_id, evidence_role in evidence:
            connection.execute(
                """INSERT INTO knowledge_evidence
                   (knowledge_evidence_id, knowledge_version_id,
                    evidence_entity_type, evidence_entity_id, evidence_role,
                    recorded_at_utc)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (uuid7(), version_id, entity_type, entity_id, evidence_role, recorded_at_utc),
            )
        append_audit_event(
            connection, audit,
            {
                "knowledge_item_id": item_id,
                "knowledge_version_id": version_id,
                "knowledge_type": knowledge_type,
                "knowledge_level": knowledge_level,
                "scope_specificity": specificity,
                "payload_hash": payload_hash,
            },
        )
    return item_id, version_id


def resolve_knowledge(
    connection: sqlite3.Connection,
    *,
    knowledge_type: str,
    context: KnowledgeContext,
    business_date: str,
    recorded_cutoff_utc: str,
) -> KnowledgeResolution:
    rows = connection.execute(
        """SELECT kv.knowledge_version_id, kv.payload_hash,
                  ks.scope_specificity, ks.supplier_id,
                  ks.supplier_plant_id, ks.supplier_family_id,
                  ks.commodity_id, ks.part_id, ks.functional_family_id,
                  ks.process_code, ks.region_code, ks.program_id,
                  ks.company_wide_flag
           FROM knowledge_version kv
           JOIN knowledge_item ki ON ki.knowledge_item_id = kv.knowledge_item_id
           JOIN knowledge_scope ks ON ks.knowledge_version_id = kv.knowledge_version_id
           WHERE ki.knowledge_type = ?
             AND kv.knowledge_level IN ('Confirmed Knowledge', 'Corporate Standard')
             AND kv.status = 'Active'
             AND kv.recorded_from_utc <= ?
             AND (kv.recorded_to_utc IS NULL OR kv.recorded_to_utc > ?)
             AND (kv.business_valid_from IS NULL OR kv.business_valid_from <= ?)
             AND (kv.business_valid_to IS NULL OR kv.business_valid_to > ?)
             AND NOT EXISTS (
                 SELECT 1 FROM knowledge_version newer
                 WHERE newer.supersedes_knowledge_version_id = kv.knowledge_version_id
                   AND newer.recorded_from_utc <= ?
             )""",
        (
            knowledge_type, recorded_cutoff_utc, recorded_cutoff_utc,
            business_date, business_date, recorded_cutoff_utc,
        ),
    ).fetchall()

    def matches(row: sqlite3.Row) -> bool:
        if row["company_wide_flag"]:
            return True
        fields = (
            "supplier_id", "supplier_plant_id", "supplier_family_id",
            "commodity_id", "part_id", "functional_family_id",
            "process_code", "region_code", "program_id",
        )
        return all(row[field] is None or row[field] == getattr(context, field) for field in fields)

    applicable = [row for row in rows if matches(row)]
    if not applicable:
        return KnowledgeResolution("Not Found", None, (), (), None)
    specificity = min(int(row["scope_specificity"]) for row in applicable)
    highest = [row for row in applicable if int(row["scope_specificity"]) == specificity]
    hashes = {row["payload_hash"] for row in highest}
    version_ids = tuple(sorted(str(row["knowledge_version_id"]) for row in highest))
    if len(hashes) > 1:
        return KnowledgeResolution("Conflict", None, (), version_ids, specificity)
    return KnowledgeResolution("Resolved", version_ids[0], version_ids, (), specificity)
