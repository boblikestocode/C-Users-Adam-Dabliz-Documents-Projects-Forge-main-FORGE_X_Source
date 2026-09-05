from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from decimal import Decimal
from datetime import datetime, timezone

from .profile_populations import economic_age_at_cutoff


@dataclass(frozen=True)
class QuoteRoundEvolutionPoint:
    quote_round_id: str
    round_number: int
    supplier_submission_date: str | None
    total_quoted_apv: Decimal
    package_status: str
    common_parts_to_prior: int
    prior_common_apv: Decimal | None
    current_common_apv: Decimal | None
    movement_from_prior_percent: Decimal | None
    common_parts_to_initial: int
    movement_from_initial_percent: Decimal | None
    submitted_count: int
    added_count: int
    unchanged_count: int
    carried_forward_count: int
    omitted_count: int
    removed_count: int
    not_quoted_count: int
    review_required_count: int


@dataclass(frozen=True)
class SupplierRoundEvolution:
    event_id: str
    supplier_id: str
    program_years: tuple[int, ...]
    rounds: tuple[QuoteRoundEvolutionPoint, ...]


def _decimal(coefficient: str, scale: int) -> Decimal:
    return Decimal(int(coefficient)).scaleb(-int(scale))


def _lifecycle_fpv(
    connection: sqlite3.Connection, event_id: str, years: tuple[int, ...]
) -> dict[str, Decimal]:
    if not years:
        raise ValueError("At least one program year is required")
    placeholders = ",".join("?" for _ in years)
    baseline = connection.execute(
        """SELECT baseline.gst_baseline_id
           FROM gst_baseline baseline
           JOIN source_package package ON package.source_package_id = baseline.source_package_id
           WHERE package.event_id = ? AND baseline.baseline_status = 'Confirmed'
           ORDER BY baseline.baseline_version DESC LIMIT 1""", (event_id,),
    ).fetchone()
    if baseline is None:
        raise ValueError("Round evolution requires a confirmed GST baseline")
    rows = connection.execute(
        f"""SELECT event_part_id, program_year, decimal_coefficient, decimal_scale
            FROM gst_part_value WHERE gst_baseline_id = ? AND measure_code = 'FPV'
              AND precision_status = 'Eligible' AND program_year IN ({placeholders})""",
        (baseline[0], *years),
    ).fetchall()
    values: dict[str, Decimal] = {}
    seen: set[tuple[str, int]] = set()
    for row in rows:
        key = (str(row[0]), int(row[1]))
        if key in seen:
            raise ValueError(f"Duplicate governing FPV for {key}")
        seen.add(key)
        values[key[0]] = values.get(key[0], Decimal(0)) + _decimal(row[2], row[3])
    return values


def _round_prices(connection: sqlite3.Connection, round_id: str, age_cutoff_utc: str) -> tuple[dict[str, Decimal], dict[str, str]]:
    rows = connection.execute(
        """SELECT ro.event_part_id, ro.membership_status,
                  price.decimal_coefficient, price.decimal_scale, ro.observation_id
           FROM round_observation ro
           LEFT JOIN submitted_datum price
             ON price.observation_id = ro.observation_id
            AND price.field_code = 'PIECE_PRICE' AND price.precision_status = 'Eligible'
           WHERE ro.quote_round_id = ?""", (round_id,),
    ).fetchall()
    prices: dict[str, Decimal] = {}
    states: dict[str, str] = {}
    for row in rows:
        part_id, state = str(row[0]), str(row[1])
        if part_id in states:
            raise ValueError(f"Round has multiple records for event part {part_id}")
        states[part_id] = state
        if (state in ("Submitted", "Added", "Unchanged") and row[2] is not None
                and economic_age_at_cutoff(connection, str(row[4]), age_cutoff_utc).eligibility_status == "Eligible"):
            prices[part_id] = _decimal(row[2], row[3])
    decisions = connection.execute(
        """SELECT decision.event_part_id, decision.decision_code,
                  price.decimal_coefficient, price.decimal_scale, decision.prior_observation_id
           FROM carry_forward_decision decision
           LEFT JOIN submitted_datum price
             ON price.observation_id = decision.prior_observation_id
            AND price.field_code = 'PIECE_PRICE' AND price.precision_status = 'Eligible'
           WHERE decision.target_quote_round_id = ?
             AND NOT EXISTS (
                 SELECT 1 FROM carry_forward_decision newer
                 WHERE newer.target_quote_round_id = decision.target_quote_round_id
                   AND newer.event_part_id = decision.event_part_id
                   AND (newer.recorded_at_utc > decision.recorded_at_utc OR
                        (newer.recorded_at_utc = decision.recorded_at_utc AND
                         newer.carry_forward_decision_id > decision.carry_forward_decision_id)))""",
        (round_id,),
    ).fetchall()
    for row in decisions:
        part_id, decision = str(row[0]), str(row[1])
        if part_id in prices:
            continue
        states[part_id] = "Carried Forward" if decision == "Carry Forward" else decision
        if (decision == "Carry Forward" and row[2] is not None
                and economic_age_at_cutoff(connection, str(row[4]), age_cutoff_utc).eligibility_status == "Eligible"):
            prices[part_id] = _decimal(row[2], row[3])
    return prices, states


def _weighted_apv(prices: dict[str, Decimal], fpv: dict[str, Decimal], parts: set[str]) -> Decimal:
    return sum((prices[part] * fpv[part] for part in parts), Decimal(0))


def calculate_round_evolution(
    connection: sqlite3.Connection,
    *,
    event_id: str,
    supplier_id: str,
    program_years: tuple[int, ...],
    selected_round_ids: tuple[str, ...] | None = None,
    economic_age_cutoff_utc: str | None = None,
) -> SupplierRoundEvolution:
    age_cutoff = economic_age_cutoff_utc or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    parameters: list[object] = [event_id, supplier_id]
    selection = ""
    if selected_round_ids is not None:
        if not selected_round_ids:
            raise ValueError("Selected round list cannot be empty")
        selection = f" AND quote_round_id IN ({','.join('?' for _ in selected_round_ids)})"
        parameters.extend(selected_round_ids)
    rounds = connection.execute(
        """SELECT quote_round_id, round_number, supplier_submission_date
           FROM supplier_quote_round WHERE event_id = ? AND supplier_id = ?"""
        + selection + " ORDER BY round_number", parameters,
    ).fetchall()
    if not rounds:
        raise ValueError("No supplier quote rounds were selected")
    fpv = _lifecycle_fpv(connection, event_id, program_years)
    required_parts = {
        str(row[0]) for row in connection.execute(
            """SELECT ep.event_part_id FROM source_package sp
               JOIN v_current_event_scope scope ON scope.source_package_id = sp.source_package_id
               JOIN event_part ep ON ep.scope_version_id = scope.scope_version_id
               WHERE sp.event_id = ? AND ep.scope_action <> 'Removed'""", (event_id,)
        )
    }
    missing_fpv = required_parts - fpv.keys()
    if missing_fpv:
        raise ValueError(f"Confirmed GST baseline is missing FPV for {len(missing_fpv)} scoped parts")
    resolved = [(row, *_round_prices(connection, str(row[0]), age_cutoff)) for row in rounds]
    initial_prices = resolved[0][1]
    points: list[QuoteRoundEvolutionPoint] = []
    for index, (round_row, prices, states) in enumerate(resolved):
        prior_prices = resolved[index - 1][1] if index else None
        prior_common = set(prices) & set(prior_prices) & set(fpv) if prior_prices is not None else set()
        initial_common = set(prices) & set(initial_prices) & set(fpv)
        prior_apv = _weighted_apv(prior_prices, fpv, prior_common) if prior_prices is not None else None
        current_common_apv = _weighted_apv(prices, fpv, prior_common) if prior_prices is not None else None
        initial_apv = _weighted_apv(initial_prices, fpv, initial_common)
        current_initial_apv = _weighted_apv(prices, fpv, initial_common)
        counts = {state: sum(value == state for value in states.values()) for state in set(states.values())}
        points.append(QuoteRoundEvolutionPoint(
            str(round_row[0]), int(round_row[1]), round_row[2],
            _weighted_apv(prices, fpv, set(prices) & set(fpv)),
            "Complete — Comparable for Award" if required_parts <= prices.keys() else "Partial Package — Not Comparable for Award",
            len(prior_common), prior_apv, current_common_apv,
            ((current_common_apv - prior_apv) / prior_apv * 100) if prior_apv else None,
            len(initial_common),
            ((current_initial_apv - initial_apv) / initial_apv * 100) if index and initial_apv else (Decimal(0) if not index else None),
            counts.get("Submitted", 0), counts.get("Added", 0), counts.get("Unchanged", 0),
            counts.get("Carried Forward", 0), counts.get("Omitted", 0), counts.get("Removed", 0),
            counts.get("Not Quoted", 0) + len(required_parts - states.keys()),
            counts.get("Buyer Review Required", 0),
        ))
    return SupplierRoundEvolution(event_id, supplier_id, program_years, tuple(points))
