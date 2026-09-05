from __future__ import annotations

import sqlite3
from dataclasses import dataclass, replace
from decimal import Decimal

from .piece_price import PiecePriceComparisonRow


COVERED_STATES = {"Submitted", "Unchanged", "Added", "Carried Forward"}


@dataclass(frozen=True)
class AnnualPackageValue:
    program_year: int
    total_fpv: Decimal
    covered_fpv: Decimal
    base_apv: Decimal
    lta_adjusted_apv: Decimal
    one_time_cost: Decimal
    total_cash_flow: Decimal
    discounted_value: Decimal
    target_apv: Decimal | None
    gap_to_target: Decimal | None


@dataclass(frozen=True)
class SupplierPackageEconomics:
    supplier_id: str
    package_status: str
    part_coverage_percent: Decimal
    fpv_weighted_coverage_percent: Decimal
    currency_id: str | None
    normalized_unit_id: str | None
    payment_treatment_status: str
    annual_values: tuple[AnnualPackageValue, ...]
    lifecycle_spend: Decimal
    npv: Decimal | None
    lifecycle_gap_to_target: Decimal | None
    package_rank: int | None


@dataclass(frozen=True)
class PackageEconomics:
    event_id: str
    program_years: tuple[int, ...]
    peak_volume_year: int
    lta_rate: Decimal
    discount_rate: Decimal
    target_status: str
    target_currency_id: str | None
    target_normalized_unit_id: str | None
    suppliers: tuple[SupplierPackageEconomics, ...]


def _decimal(coefficient: str, scale: int) -> Decimal:
    return Decimal(int(coefficient)).scaleb(-int(scale))


def _governing_values(
    connection: sqlite3.Connection, event_id: str, years: tuple[int, ...]
) -> tuple[dict[tuple[str, int], Decimal], dict[tuple[str, int], tuple[Decimal, str | None, str]]]:
    placeholders = ",".join("?" for _ in years)
    baseline = connection.execute(
        """SELECT baseline.gst_baseline_id
           FROM gst_baseline baseline
           JOIN source_package package ON package.source_package_id = baseline.source_package_id
           WHERE package.event_id = ? AND baseline.baseline_status = 'Confirmed'
           ORDER BY baseline.baseline_version DESC LIMIT 1""",
        (event_id,),
    ).fetchone()
    if baseline is None:
        raise ValueError("Package economics requires a confirmed GST baseline")
    rows = connection.execute(
        f"""SELECT event_part_id, program_year, measure_code,
                   decimal_coefficient, decimal_scale, currency_id, normalized_unit_id
            FROM gst_part_value
            WHERE gst_baseline_id = ? AND measure_code IN ('FPV', 'Piece Price Target')
              AND precision_status = 'Eligible' AND program_year IN ({placeholders})""",
        (baseline[0], *years),
    ).fetchall()
    fpv = {(str(row[0]), int(row[1])): _decimal(row[3], row[4]) for row in rows if row[2] == 'FPV'}
    targets = {
        (str(row[0]), int(row[1])): (_decimal(row[3], row[4]), row[5], str(row[6]))
        for row in rows if row[2] == 'Piece Price Target'
    }
    return fpv, targets


def _payment_costs(
    connection: sqlite3.Connection,
    observation_ids: tuple[str, ...],
    years: tuple[int, ...],
    currency_id: str | None,
) -> tuple[dict[int, Decimal], str]:
    if not observation_ids:
        return {}, "No One-Time Costs Disclosed"
    placeholders = ",".join("?" for _ in observation_ids)
    rows = connection.execute(
        f"""SELECT evidence.program_year, evidence.decimal_coefficient,
                   evidence.decimal_scale, evidence.currency_id,
                   treatment.treatment_classification,
                   treatment.upfront_coefficient, treatment.upfront_scale
            FROM payment_cost_evidence evidence
            JOIN v_current_payment_treatment treatment
              ON treatment.payment_cost_evidence_id = evidence.payment_cost_evidence_id
            WHERE evidence.observation_id IN ({placeholders})""",
        observation_ids,
    ).fetchall()
    if not rows:
        return {}, "No One-Time Costs Disclosed"
    if any(row[4] == "Treatment Unconfirmed" for row in rows):
        return {}, "Treatment Unconfirmed — Total Program Cost Incomplete"
    if any(row[3] != currency_id for row in rows if row[4] in ("Separate Lump Sum", "Partially Amortized")):
        return {}, "Currency Alignment Required — Total Program Cost Incomplete"
    costs = {year: Decimal(0) for year in years}
    for row in rows:
        year = int(row[0])
        if year not in costs:
            continue
        if row[4] == "Separate Lump Sum":
            costs[year] += _decimal(row[1], row[2])
        elif row[4] == "Partially Amortized":
            costs[year] += _decimal(row[5], row[6])
    return costs, "Confirmed"


def calculate_package_economics(
    connection: sqlite3.Connection,
    *,
    event_id: str,
    comparison: tuple[PiecePriceComparisonRow, ...],
    program_years: tuple[int, ...],
    lta_rate: Decimal = Decimal("0"),
    discount_rate: Decimal = Decimal("0.07"),
) -> PackageEconomics:
    if not 1 <= len(program_years) <= 5 or len(set(program_years)) != len(program_years):
        raise ValueError("Analysis period must contain one to five unique program years")
    if tuple(sorted(program_years)) != program_years:
        raise ValueError("Program years must be in ascending order")
    if not Decimal("0") <= lta_rate < Decimal("1"):
        raise ValueError("LTA rate must be at least 0% and less than 100%")
    if discount_rate <= Decimal("-1"):
        raise ValueError("Discount rate must be greater than -100%")
    if not comparison:
        raise ValueError("Package economics requires scoped parts")

    fpv, targets = _governing_values(connection, event_id, program_years)
    missing = [(part.event_part_id, year) for part in comparison for year in program_years
               if (part.event_part_id, year) not in fpv]
    if missing:
        raise ValueError(f"Confirmed GST baseline is missing {len(missing)} required part/year FPV values")
    totals_by_year = {
        year: sum((fpv[(part.event_part_id, year)] for part in comparison), Decimal(0))
        for year in program_years
    }
    peak_year = max(program_years, key=lambda year: (totals_by_year[year], -year))
    lifecycle_fpv = sum(totals_by_year.values(), Decimal(0))
    target_complete = all(
        (part.event_part_id, year) in targets for part in comparison for year in program_years
    )
    target_bases = {(value[1], value[2]) for value in targets.values()} if target_complete else set()
    target_comparable = target_complete and len(target_bases) == 1 and next(iter(target_bases))[0] is not None
    target_basis = next(iter(target_bases)) if target_comparable else (None, None)
    target_apv_by_year = {
        year: sum((targets[(part.event_part_id, year)][0] * fpv[(part.event_part_id, year)]
                   for part in comparison), Decimal(0))
        for year in program_years
    } if target_comparable else {}
    supplier_ids = sorted({quote.supplier_id for part in comparison for quote in part.quotes})
    suppliers: list[SupplierPackageEconomics] = []
    for supplier_id in supplier_ids:
        quotes = {
            part.event_part_id: next((q for q in part.quotes if q.supplier_id == supplier_id), None)
            for part in comparison
        }
        covered = {
            part_id: quote for part_id, quote in quotes.items()
            if quote is not None and quote.coverage_status in COVERED_STATES and quote.piece_price is not None
        }
        units = {(q.currency_id, q.normalized_unit_id) for q in covered.values()}
        common_basis = len(units) == 1 and next(iter(units))[0] is not None and next(iter(units))[1] is not None
        unit = next(iter(units)) if common_basis else (None, None)
        complete = len(covered) == len(comparison)
        observation_ids = tuple(sorted({
            str(quote.observation_id) for quote in covered.values() if quote.observation_id is not None
        }))
        payment_costs, payment_status = _payment_costs(
            connection, observation_ids, program_years, unit[0]
        )
        payment_complete = payment_status in ("Confirmed", "No One-Time Costs Disclosed")
        status = (
            "Complete — Comparable for Award"
            if complete and common_basis and payment_complete
            else "Partial Package — Not Comparable for Award"
        )
        covered_lifecycle_fpv = sum(
            (fpv[(part_id, year)] for part_id in covered for year in program_years), Decimal(0)
        )
        annual: list[AnnualPackageValue] = []
        for period, year in enumerate(program_years):
            base = sum((quote.piece_price * fpv[(part_id, year)] for part_id, quote in covered.items()), Decimal(0))
            adjusted = base * ((Decimal(1) - lta_rate) ** period)
            one_time_cost = payment_costs.get(year, Decimal(0))
            total_cash_flow = adjusted + one_time_cost
            discounted = total_cash_flow / ((Decimal(1) + discount_rate) ** period)
            target_apv = target_apv_by_year.get(year) if common_basis and unit_matches(units, target_basis) else None
            annual.append(AnnualPackageValue(
                year, totals_by_year[year],
                sum((fpv[(part_id, year)] for part_id in covered), Decimal(0)),
                base, adjusted, one_time_cost, total_cash_flow, discounted, target_apv,
                base - target_apv if target_apv is not None else None,
            ))
        lifecycle_spend = sum((item.total_cash_flow for item in annual), Decimal(0))
        npv = sum((item.discounted_value for item in annual), Decimal(0)) if complete and common_basis and payment_complete else None
        suppliers.append(SupplierPackageEconomics(
            supplier_id, status,
            Decimal(len(covered)) * Decimal(100) / Decimal(len(comparison)),
            covered_lifecycle_fpv * Decimal(100) / lifecycle_fpv if lifecycle_fpv else Decimal(0),
            unit[0], unit[1], payment_status, tuple(annual), lifecycle_spend, npv,
            sum((item.gap_to_target for item in annual), Decimal(0))
            if all(item.gap_to_target is not None for item in annual) else None,
            None,
        ))
    ranked = list(suppliers)
    for basis in {(item.currency_id, item.normalized_unit_id) for item in suppliers if item.npv is not None}:
        values = sorted({item.npv for item in suppliers if (item.currency_id, item.normalized_unit_id) == basis and item.npv is not None})
        ranked = [
            replace(item, package_rank=values.index(item.npv) + 1)
            if (item.currency_id, item.normalized_unit_id) == basis and item.npv is not None else item
            for item in ranked
        ]
    target_status = "Complete" if target_comparable else "Unavailable or Incomplete"
    return PackageEconomics(event_id, program_years, peak_year, lta_rate, discount_rate,
                            target_status, target_basis[0], target_basis[1], tuple(ranked))


def unit_matches(units: set[tuple[str | None, str | None]], target_basis: tuple[str | None, str | None]) -> bool:
    return len(units) == 1 and next(iter(units)) == target_basis
