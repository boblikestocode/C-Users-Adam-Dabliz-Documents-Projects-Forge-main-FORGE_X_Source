from __future__ import annotations

import hashlib
import sqlite3

from .audit import canonical_json
from .connection import immediate_transaction
from .ids import uuid7


FIELD_CATEGORIES = {
    "PIECE_PRICE": "Piece Price", "MATERIAL": "Material",
    "PURCHASED_CONTENT": "Purchased Content", "LABOR": "Labor",
    "BURDEN": "Burden", "OVERHEAD": "Overhead", "PROFIT": "Profit",
    "TOOLING": "Tooling", "ED&D": "ED&D",
}


def part_history_source_rows(
    connection: sqlite3.Connection, anchor_part_id: str, evidence_cutoff_utc: str
) -> list[tuple[object, ...]]:
    exists = connection.execute(
        """SELECT 1 FROM v_effective_pbd_observation WHERE part_id = ?
           UNION SELECT 1 FROM event_part WHERE part_id = ? LIMIT 1""",
        (anchor_part_id, anchor_part_id),
    ).fetchone()
    if exists is None:
        raise ValueError("Anchor part does not exist")
    relationships: list[tuple[object, ...]] = [
        (anchor_part_id, "Exact Part", None, None, None, None, "Governing")
    ]
    for row in connection.execute(
        """SELECT candidate.part_id, version.functional_family_id,
                  version.family_version_id, version.relationship_type,
                  version.business_rationale, permission.measure_or_category,
                  permission.comparison_eligibility
           FROM family_version version
           JOIN family_member anchor ON anchor.family_version_id = version.family_version_id
                                    AND anchor.part_id = ?
           JOIN family_member candidate ON candidate.family_version_id = version.family_version_id
                                       AND candidate.part_id <> anchor.part_id
           JOIN family_comparison_permission permission
             ON permission.family_version_id = version.family_version_id
           WHERE version.recorded_at_utc <= ?
             AND permission.comparison_eligibility IN ('Governing', 'Directional')
             AND NOT EXISTS (SELECT 1 FROM family_version newer
                 WHERE newer.functional_family_id = version.functional_family_id
                   AND newer.recorded_at_utc <= ?
                   AND (newer.recorded_at_utc > version.recorded_at_utc OR
                        (newer.recorded_at_utc = version.recorded_at_utc AND
                         newer.family_version_id > version.family_version_id)))
           ORDER BY candidate.part_id, version.functional_family_id,
                    permission.measure_or_category""",
        (anchor_part_id, evidence_cutoff_utc, evidence_cutoff_utc),
    ):
        relationships.append((row[0], "Buyer-Confirmed Functional Family",
                              row[1], row[2], row[3], row[4], row[6], row[5]))

    output: list[tuple[object, ...]] = []
    for relationship in relationships:
        evidence_part, evidence_class, family_id, version_id, relationship_type, rationale, eligibility, *field_filter = relationship
        values = connection.execute(
            """SELECT datum.submitted_datum_id, observation.observation_id,
                      datum.field_code, COALESCE(
                        (SELECT identity.confirmed_supplier_id
                         FROM observation_supplier_identity_confirmation identity
                         WHERE identity.observation_id = observation.observation_id
                           AND identity.recorded_at_utc <= ?
                         ORDER BY identity.recorded_at_utc DESC,
                                  identity.supplier_identity_confirmation_id DESC LIMIT 1),
                        observation.supplier_id),
                      observation.supplier_plant_id, observation.context_id,
                      observation.economic_date, datum.decimal_coefficient,
                      datum.decimal_scale, datum.currency_id, datum.normalized_unit_id
               FROM pbd_observation observation JOIN submitted_datum datum
                 ON datum.observation_id = observation.observation_id
               WHERE observation.part_id = ? AND observation.recorded_at_utc <= ?
                 AND datum.decimal_coefficient IS NOT NULL AND datum.decimal_scale IS NOT NULL
               ORDER BY observation.economic_date, observation.observation_id,
                        datum.field_code, datum.submitted_datum_id""",
            (evidence_cutoff_utc, evidence_part, evidence_cutoff_utc),
        )
        for value in values:
            comparison_field = FIELD_CATEGORIES.get(str(value[2]))
            if comparison_field is None:
                continue
            if field_filter and comparison_field != field_filter[0]:
                continue
            row_id = hashlib.sha256(canonical_json((anchor_part_id, evidence_part,
                value[1], value[0], evidence_class, comparison_field, version_id)).encode("utf-8")).hexdigest()
            output.append((row_id, anchor_part_id, evidence_part, value[1], value[0],
                           evidence_class, comparison_field, eligibility, family_id,
                           version_id, relationship_type, rationale, value[3], value[4],
                           value[5], value[6], value[7], value[8], value[9], value[10]))
    return sorted(output, key=lambda row: str(row[0]))


def build_part_history_projection(
    connection: sqlite3.Connection, *, anchor_part_id: str, evidence_cutoff_utc: str
) -> str:
    generation_id = uuid7()
    rows = part_history_source_rows(connection, anchor_part_id, evidence_cutoff_utc)
    manifest_hash = hashlib.sha256(canonical_json(rows).encode("utf-8")).hexdigest()
    with immediate_transaction(connection):
        connection.execute(
            "INSERT INTO part_history_generation_manifest VALUES (?, ?, ?, ?, ?, 'Complete', ?)",
            (generation_id, anchor_part_id, evidence_cutoff_utc, manifest_hash,
             len(rows), evidence_cutoff_utc),
        )
        connection.executemany(
            """INSERT INTO part_history_projection VALUES
               (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [(generation_id, *row, evidence_cutoff_utc, manifest_hash) for row in rows],
        )
    return generation_id
