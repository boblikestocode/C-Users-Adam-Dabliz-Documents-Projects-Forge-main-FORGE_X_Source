from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any

from .audit import AuditContext, append_audit_event, canonical_json
from .connection import immediate_transaction
from .ids import uuid7


REQUIRED_COMPARISON_FIELDS = frozenset({
    "Piece Price", "Material", "Purchased Content", "Labor", "Burden",
    "Overhead", "Profit", "Conversion Structure", "Tooling", "ED&D",
})
COMPARISON_ELIGIBILITY = {"Governing", "Directional", "Not Comparable"}


@dataclass(frozen=True)
class FamilyMemberInput:
    part_id: str
    member_role: str
    program_id: str | None = None
    vehicle_position: str | None = None


@dataclass(frozen=True)
class FamilyVersionInput:
    readable_name: str
    relationship_type: str
    business_rationale: str
    members: tuple[FamilyMemberInput, ...]
    comparison_permissions: dict[str, str]
    supporting_context: dict[str, Any]
    business_valid_from: str | None = None
    business_valid_to: str | None = None


@dataclass(frozen=True)
class ComparablePart:
    anchor_part_id: str
    candidate_part_id: str
    evidence_class: str
    comparison_eligibility: str
    functional_family_id: str | None = None
    family_version_id: str | None = None
    relationship_type: str | None = None
    business_rationale: str | None = None


def _validate(definition: FamilyVersionInput) -> None:
    if not definition.readable_name.strip() or not definition.relationship_type.strip():
        raise ValueError("Family name and controlled relationship type are required")
    if not definition.business_rationale.strip():
        raise ValueError("Functional-family confirmation requires a business rationale")
    if len({member.part_id for member in definition.members}) < 2:
        raise ValueError("A functional family requires at least two distinct parts")
    member_keys = {(m.part_id, m.program_id, m.vehicle_position) for m in definition.members}
    if len(member_keys) != len(definition.members):
        raise ValueError("Functional-family membership cannot contain duplicates")
    if any(not member.member_role.strip() for member in definition.members):
        raise ValueError("Every family member requires a role")
    missing = REQUIRED_COMPARISON_FIELDS - definition.comparison_permissions.keys()
    if missing:
        raise ValueError(f"Comparison eligibility is required for: {', '.join(sorted(missing))}")
    invalid = set(definition.comparison_permissions.values()) - COMPARISON_ELIGIBILITY
    if invalid:
        raise ValueError("Unsupported family comparison eligibility")


def _insert_version(
    connection: sqlite3.Connection,
    *,
    functional_family_id: str,
    version_number: int,
    definition: FamilyVersionInput,
    confirmed_by_user_id: str,
    recorded_at_utc: str,
    supersedes_family_version_id: str | None,
) -> str:
    version_id = uuid7()
    for member in definition.members:
        if connection.execute(
            """SELECT 1 FROM event_part WHERE part_id = ?
               UNION SELECT 1 FROM pbd_observation WHERE part_id = ? LIMIT 1""",
            (member.part_id, member.part_id),
        ).fetchone() is None:
            raise ValueError(f"Functional-family member part does not exist: {member.part_id}")
    connection.execute(
        """INSERT INTO family_version
           (family_version_id, functional_family_id, version_number, readable_name,
            relationship_type, business_rationale, confirmed_by_user_id,
            business_valid_from, business_valid_to, recorded_at_utc,
            supersedes_family_version_id, supporting_context_payload)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (version_id, functional_family_id, version_number, definition.readable_name.strip(),
         definition.relationship_type.strip(), definition.business_rationale.strip(),
         confirmed_by_user_id, definition.business_valid_from, definition.business_valid_to,
         recorded_at_utc, supersedes_family_version_id,
         canonical_json(definition.supporting_context)),
    )
    for member in definition.members:
        connection.execute(
            """INSERT INTO family_member
               (family_member_id, family_version_id, part_id, program_id,
                vehicle_position, member_role, recorded_at_utc)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (uuid7(), version_id, member.part_id, member.program_id,
             member.vehicle_position, member.member_role.strip(), recorded_at_utc),
        )
    for measure, eligibility in sorted(definition.comparison_permissions.items()):
        connection.execute(
            """INSERT INTO family_comparison_permission
               (comparison_permission_id, family_version_id, measure_or_category,
                comparison_eligibility, recorded_at_utc)
               VALUES (?, ?, ?, ?, ?)""",
            (uuid7(), version_id, measure, eligibility, recorded_at_utc),
        )
    return version_id


def create_functional_family(
    connection: sqlite3.Connection,
    *,
    definition: FamilyVersionInput,
    created_by_user_id: str,
    confirmed_by_user_id: str,
    recorded_at_utc: str,
    audit: AuditContext,
) -> str:
    _validate(definition)
    family_id = uuid7()
    with immediate_transaction(connection):
        connection.execute(
            "INSERT INTO functional_part_family VALUES (?, ?, ?)",
            (family_id, created_by_user_id, recorded_at_utc),
        )
        version_id = _insert_version(
            connection, functional_family_id=family_id, version_number=1,
            definition=definition, confirmed_by_user_id=confirmed_by_user_id,
            recorded_at_utc=recorded_at_utc, supersedes_family_version_id=None,
        )
        append_audit_event(connection, audit, {
            "functional_family_id": family_id, "family_version_id": version_id,
            "version_number": 1, "member_part_ids": sorted({m.part_id for m in definition.members}),
            "comparison_permissions": definition.comparison_permissions,
        })
    return family_id


def revise_functional_family(
    connection: sqlite3.Connection,
    *,
    functional_family_id: str,
    definition: FamilyVersionInput,
    confirmed_by_user_id: str,
    recorded_at_utc: str,
    audit: AuditContext,
) -> str:
    _validate(definition)
    with immediate_transaction(connection):
        current = connection.execute(
            "SELECT * FROM v_current_family_version WHERE functional_family_id = ?",
            (functional_family_id,),
        ).fetchone()
        if current is None:
            raise ValueError("Functional family does not exist")
        version_id = _insert_version(
            connection, functional_family_id=functional_family_id,
            version_number=int(current["version_number"]) + 1, definition=definition,
            confirmed_by_user_id=confirmed_by_user_id, recorded_at_utc=recorded_at_utc,
            supersedes_family_version_id=current["family_version_id"],
        )
        append_audit_event(connection, audit, {
            "functional_family_id": functional_family_id,
            "family_version_id": version_id,
            "supersedes_family_version_id": current["family_version_id"],
            "version_number": int(current["version_number"]) + 1,
            "business_rationale": definition.business_rationale,
        })
    return version_id


def comparison_permission(
    connection: sqlite3.Connection, *, functional_family_id: str, measure_or_category: str
) -> str | None:
    row = connection.execute(
        """SELECT permission.comparison_eligibility
           FROM v_current_family_version version
           JOIN family_comparison_permission permission
             ON permission.family_version_id = version.family_version_id
           WHERE version.functional_family_id = ? AND permission.measure_or_category = ?""",
        (functional_family_id, measure_or_category),
    ).fetchone()
    return None if row is None else str(row[0])


def resolve_comparable_parts(
    connection: sqlite3.Connection,
    *,
    anchor_part_id: str,
    measure_or_category: str,
    include_directional: bool = True,
) -> tuple[ComparablePart, ...]:
    """Resolve exact and confirmed-family evidence without using descriptions.

    Exact identity is always returned first. Family relationships remain explicitly
    labelled and never collapse distinct part identifiers into an exact match.
    """
    exists = connection.execute(
        """SELECT 1 FROM event_part WHERE part_id = ?
           UNION SELECT 1 FROM pbd_observation WHERE part_id = ? LIMIT 1""",
        (anchor_part_id, anchor_part_id),
    ).fetchone()
    if exists is None:
        raise ValueError("Anchor part does not exist")
    results = [ComparablePart(
        anchor_part_id, anchor_part_id, "Exact Part", "Governing",
    )]
    allowed = ("Governing", "Directional") if include_directional else ("Governing",)
    placeholders = ",".join("?" for _ in allowed)
    rows = connection.execute(
        f"""SELECT DISTINCT candidate.part_id, version.functional_family_id,
                   version.family_version_id, version.relationship_type,
                   version.business_rationale, permission.comparison_eligibility
            FROM v_current_family_version version
            JOIN family_member anchor
              ON anchor.family_version_id = version.family_version_id
             AND anchor.part_id = ?
            JOIN family_member candidate
              ON candidate.family_version_id = version.family_version_id
             AND candidate.part_id <> anchor.part_id
            JOIN family_comparison_permission permission
              ON permission.family_version_id = version.family_version_id
             AND permission.measure_or_category = ?
            WHERE permission.comparison_eligibility IN ({placeholders})
            ORDER BY CASE permission.comparison_eligibility
                         WHEN 'Governing' THEN 0 ELSE 1 END,
                     candidate.part_id, version.functional_family_id""",
        (anchor_part_id, measure_or_category, *allowed),
    ).fetchall()
    results.extend(ComparablePart(
        anchor_part_id=anchor_part_id, candidate_part_id=str(row[0]),
        evidence_class="Buyer-Confirmed Functional Family",
        functional_family_id=str(row[1]), family_version_id=str(row[2]),
        relationship_type=str(row[3]), business_rationale=str(row[4]),
        comparison_eligibility=str(row[5]),
    ) for row in rows)
    return tuple(results)
