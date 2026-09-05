from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from decimal import Decimal

from .audit import canonical_json
from .connection import immediate_transaction
from .ids import uuid7


@dataclass(frozen=True)
class PiecePriceQuote:
    supplier_id: str
    submitted_supplier_name: str | None
    quote_round_id: str | None
    round_number: int | None
    observation_id: str | None
    coverage_status: str
    piece_price: Decimal | None
    currency_id: str | None
    normalized_unit_id: str | None


@dataclass(frozen=True)
class PiecePriceComparisonRow:
    event_part_id: str
    part_id: str
    submitted_part_number: str
    submitted_description: str | None
    vehicle_position: str | None
    target_piece_price: Decimal | None
    target_currency_id: str | None
    target_normalized_unit_id: str | None
    quotes: tuple[PiecePriceQuote, ...]


def _decimal(coefficient: str | None, scale: int | None) -> Decimal | None:
    if coefficient is None or scale is None:
        return None
    return Decimal(int(coefficient)).scaleb(-int(scale))


def projection_source_rows(
    connection: sqlite3.Connection, event_id: str, evidence_cutoff_utc: str
) -> list[sqlite3.Row]:
    rows = connection.execute(
        """WITH current_scope AS (
               SELECT ep.event_part_id
               FROM source_package sp
               JOIN scope_activation current
                 ON current.source_package_id = sp.source_package_id
               JOIN event_part ep ON ep.scope_version_id = current.scope_version_id
               WHERE sp.event_id = ? AND ep.scope_action <> 'Removed'
                 AND current.recorded_at_utc <= ?
                 AND current.activation_decision = 'Activate'
                 AND NOT EXISTS (
                     SELECT 1 FROM scope_activation newer
                     WHERE newer.source_package_id = current.source_package_id
                       AND newer.recorded_at_utc <= ?
                       AND (newer.recorded_at_utc > current.recorded_at_utc OR
                            (newer.recorded_at_utc = current.recorded_at_utc AND
                             newer.scope_activation_id > current.scope_activation_id))
                 )
           ), active_suppliers AS (
               SELECT active.supplier_id, active.quote_round_id
               FROM round_activation active
               WHERE active.event_id = ? AND active.recorded_at_utc <= ?
                 AND active.activation_decision = 'Activate'
                 AND NOT EXISTS (
                     SELECT 1 FROM round_activation newer
                     WHERE newer.event_id = active.event_id
                       AND newer.supplier_id = active.supplier_id
                       AND newer.recorded_at_utc <= ?
                       AND (newer.recorded_at_utc > active.recorded_at_utc OR
                            (newer.recorded_at_utc = active.recorded_at_utc AND
                             newer.round_activation_id > active.round_activation_id))
                 )
           ), conflict_state AS (
               SELECT conflict.round_conflict_id, conflict.quote_round_id,
                      conflict.event_part_id,
                      conflict.prior_round_observation_id,
                      conflict.new_round_observation_id,
                      decision.decision_code, decision.target_quote_round_id
               FROM round_conflict conflict
               LEFT JOIN round_conflict_decision decision
                 ON decision.round_conflict_id = conflict.round_conflict_id
                AND decision.recorded_at_utc <= ?
                AND NOT EXISTS (
                    SELECT 1 FROM round_conflict_decision newer
                    WHERE newer.supersedes_round_conflict_decision_id =
                          decision.round_conflict_decision_id
                      AND newer.recorded_at_utc <= ?
                )
               WHERE conflict.detected_at_utc <= ?
           ), direct_quote AS (
               SELECT ro.quote_round_id, ro.event_part_id, ro.observation_id,
                      ro.membership_status
               FROM round_observation ro
               WHERE ro.membership_status IN ('Submitted', 'Unchanged', 'Added')
                 AND ro.recorded_at_utc <= ?
                 AND NOT EXISTS (
                     SELECT 1 FROM conflict_state conflict
                     WHERE conflict.quote_round_id = ro.quote_round_id
                       AND conflict.event_part_id = ro.event_part_id
                       AND ro.round_observation_id IN (
                           conflict.prior_round_observation_id,
                           conflict.new_round_observation_id)
                 )
               UNION ALL
               SELECT conflict.quote_round_id, conflict.event_part_id,
                      membership.observation_id, 'Submitted'
               FROM conflict_state conflict
               JOIN round_observation membership
                 ON membership.round_observation_id = CASE
                    WHEN conflict.decision_code = 'New Replaces Earlier Within Round'
                    THEN conflict.new_round_observation_id
                    ELSE conflict.prior_round_observation_id END
               WHERE conflict.decision_code IN
                   ('New Replaces Earlier Within Round', 'Keep Earlier Active',
                    'Move New Submission to New Round')
               UNION ALL
               SELECT conflict.target_quote_round_id, conflict.event_part_id,
                      membership.observation_id, 'Submitted'
               FROM conflict_state conflict
               JOIN round_observation membership
                 ON membership.round_observation_id = conflict.new_round_observation_id
               WHERE conflict.decision_code = 'Move New Submission to New Round'
           ), negative_membership AS (
               SELECT ro.quote_round_id, ro.event_part_id, ro.membership_status
               FROM round_observation ro
               WHERE ro.membership_status IN ('Removed', 'Omitted', 'Conflict')
                 AND ro.recorded_at_utc <= ?
           ), carry AS (
               SELECT cfd.target_quote_round_id, cfd.event_part_id,
                      cfd.prior_observation_id, cfd.decision_code
               FROM carry_forward_decision cfd
               WHERE cfd.recorded_at_utc <= ? AND NOT EXISTS (
                   SELECT 1 FROM carry_forward_decision newer
                   WHERE newer.target_quote_round_id = cfd.target_quote_round_id
                     AND newer.event_part_id = cfd.event_part_id
                     AND newer.recorded_at_utc <= ?
                     AND (newer.recorded_at_utc > cfd.recorded_at_utc OR
                          (newer.recorded_at_utc = cfd.recorded_at_utc AND
                           newer.carry_forward_decision_id > cfd.carry_forward_decision_id))
               )
           )
           SELECT ?, active.supplier_id, scope.event_part_id,
                  active.quote_round_id,
                  COALESCE(direct.observation_id, carry.prior_observation_id),
                  CASE
                    WHEN direct.observation_id IS NOT NULL THEN direct.membership_status
                    WHEN negative.membership_status IS NOT NULL THEN negative.membership_status
                    WHEN carry.decision_code = 'Carry Forward' THEN 'Carried Forward'
                    WHEN carry.decision_code IS NOT NULL THEN carry.decision_code
                    ELSE 'Not Quoted'
                  END,
                  price.decimal_coefficient, price.decimal_scale,
                  price.currency_id, price.normalized_unit_id
           FROM current_scope scope CROSS JOIN active_suppliers active
           LEFT JOIN direct_quote direct
             ON direct.quote_round_id = active.quote_round_id
            AND direct.event_part_id = scope.event_part_id
           LEFT JOIN carry
             ON carry.target_quote_round_id = active.quote_round_id
            AND carry.event_part_id = scope.event_part_id
            AND direct.observation_id IS NULL
           LEFT JOIN negative_membership negative
             ON negative.quote_round_id = active.quote_round_id
            AND negative.event_part_id = scope.event_part_id
            AND direct.observation_id IS NULL
           LEFT JOIN submitted_datum price
             ON price.observation_id = COALESCE(direct.observation_id, carry.prior_observation_id)
            AND price.field_code = 'PIECE_PRICE'
            AND price.precision_status = 'Eligible'
            AND price.recorded_at_utc <= ?
            AND EXISTS (
                SELECT 1 FROM pbd_observation observation
                WHERE observation.observation_id = price.observation_id
                  AND observation.recorded_at_utc <= ?
                  AND NOT EXISTS (
                      SELECT 1 FROM observation_eligibility eligibility
                      WHERE eligibility.observation_id = observation.observation_id
                        AND eligibility.analytical_role = 'Economic Age'
                        AND eligibility.recorded_at_utc <= ?
                        AND eligibility.eligibility_status = 'Historical Context Only'
                        AND NOT EXISTS (
                            SELECT 1 FROM observation_eligibility newer
                            WHERE newer.observation_id = eligibility.observation_id
                              AND newer.analytical_role = eligibility.analytical_role
                              AND newer.recorded_at_utc <= ?
                              AND (newer.recorded_at_utc > eligibility.recorded_at_utc OR
                                   (newer.recorded_at_utc = eligibility.recorded_at_utc AND
                                    newer.eligibility_id > eligibility.eligibility_id))
                        )
                  )
            )
           ORDER BY scope.event_part_id, active.supplier_id""",
        (
            event_id, evidence_cutoff_utc, evidence_cutoff_utc,
            event_id, evidence_cutoff_utc, evidence_cutoff_utc,
            evidence_cutoff_utc, evidence_cutoff_utc, evidence_cutoff_utc,
            evidence_cutoff_utc, evidence_cutoff_utc,
            evidence_cutoff_utc, evidence_cutoff_utc,
            event_id, evidence_cutoff_utc, evidence_cutoff_utc,
            evidence_cutoff_utc, evidence_cutoff_utc,
        ),
    ).fetchall()
    keys: set[tuple[str, str]] = set()
    for row in rows:
        key = (str(row[1]), str(row[2]))
        if key in keys:
            raise ValueError(f"Multiple eligible piece prices resolve for supplier/part {key}")
        keys.add(key)
    return rows


def build_active_round_part_projection(
    connection: sqlite3.Connection,
    *,
    event_id: str,
    evidence_cutoff_utc: str,
) -> str:
    if connection.execute("SELECT 1 FROM sourcing_event WHERE event_id = ?", (event_id,)).fetchone() is None:
        raise ValueError("Sourcing event does not exist")
    generation_id = uuid7()
    source_rows = projection_source_rows(connection, event_id, evidence_cutoff_utc)
    manifest = [tuple(row) for row in source_rows]
    manifest_hash = hashlib.sha256(canonical_json(manifest).encode("utf-8")).hexdigest()
    with immediate_transaction(connection):
        connection.execute(
            """INSERT INTO projection_generation_manifest
               (projection_generation_id, projection_type, event_id,
                evidence_cutoff_utc, build_manifest_hash, projected_row_count,
                generation_status, created_at_utc)
               VALUES (?, 'Active Round Part', ?, ?, ?, ?, 'Complete', ?)""",
            (generation_id, event_id, evidence_cutoff_utc, manifest_hash,
             len(source_rows), evidence_cutoff_utc),
        )
        connection.executemany(
            """INSERT INTO active_round_part_projection
               (projection_generation_id, event_id, supplier_id, event_part_id,
                quote_round_id, observation_id, coverage_status,
                piece_price_coefficient, piece_price_scale, currency_id,
                normalized_unit_id, evidence_cutoff_utc, build_manifest_hash)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (generation_id, *tuple(row), evidence_cutoff_utc, manifest_hash)
                for row in source_rows
            ],
        )
    return generation_id


def get_piece_price_comparison(
    connection: sqlite3.Connection,
    *,
    generation_id: str,
    event_id: str,
    program_year: int | None = None,
) -> tuple[PiecePriceComparisonRow, ...]:
    rows = connection.execute(
        """SELECT projection.event_part_id, ep.part_id,
                  ep.submitted_part_number, ep.submitted_description,
                  ep.vehicle_position, projection.supplier_id,
                  observation.submitted_supplier_name,
                  projection.quote_round_id, round.round_number,
                  projection.observation_id, projection.coverage_status,
                  projection.piece_price_coefficient,
                  projection.piece_price_scale, projection.currency_id,
                  projection.normalized_unit_id
           FROM active_round_part_projection projection
           JOIN event_part ep ON ep.event_part_id = projection.event_part_id
           LEFT JOIN pbd_observation observation
             ON observation.observation_id = projection.observation_id
           LEFT JOIN supplier_quote_round round
             ON round.quote_round_id = projection.quote_round_id
           WHERE projection.projection_generation_id = ?
             AND projection.event_id = ?
           ORDER BY ep.submitted_part_number, ep.event_part_id,
                    projection.supplier_id""",
        (generation_id, event_id),
    ).fetchall()
    targets: dict[str, sqlite3.Row] = {}
    if program_year is not None:
        target_rows = connection.execute(
            """SELECT value.event_part_id, value.decimal_coefficient,
                      value.decimal_scale, value.currency_id,
                      value.normalized_unit_id
               FROM gst_part_value value
               JOIN gst_baseline baseline
                 ON baseline.gst_baseline_id = value.gst_baseline_id
               JOIN source_package package
                 ON package.source_package_id = baseline.source_package_id
               WHERE package.event_id = ? AND baseline.baseline_status = 'Confirmed'
                 AND value.program_year = ?
                 AND value.measure_code = 'Piece Price Target'
                 AND value.precision_status = 'Eligible'
               ORDER BY baseline.baseline_version DESC""",
            (event_id, program_year),
        ).fetchall()
        for target in target_rows:
            targets.setdefault(str(target[0]), target)

    grouped: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        grouped.setdefault(str(row[0]), []).append(row)
    result: list[PiecePriceComparisonRow] = []
    for event_part_id, part_rows in grouped.items():
        first, target = part_rows[0], targets.get(event_part_id)
        result.append(PiecePriceComparisonRow(
            event_part_id, str(first[1]), str(first[2]), first[3], first[4],
            _decimal(target[1], target[2]) if target else None,
            target[3] if target else None, target[4] if target else None,
            tuple(PiecePriceQuote(
                str(row[5]), row[6], row[7], row[8], row[9], str(row[10]),
                _decimal(row[11], row[12]), row[13], row[14],
            ) for row in part_rows),
        ))
    return tuple(result)
