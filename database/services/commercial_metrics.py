from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from decimal import Decimal

from .piece_price import PiecePriceComparisonRow


@dataclass(frozen=True)
class SupplierPriceMetric:
    supplier_id: str
    piece_price: Decimal | None
    currency_id: str | None
    normalized_unit_id: str | None
    competitive_rank: int | None
    peer_low_gap: Decimal | None
    target_gap: Decimal | None
    incumbent_savings_per_piece: Decimal | None
    annual_incumbent_savings: Decimal | None
    comparison_status: str


@dataclass(frozen=True)
class PartCommercialMetrics:
    event_part_id: str
    incumbent_supplier_id: str | None
    incumbency_status: str
    annual_fpv: Decimal | None
    suppliers: tuple[SupplierPriceMetric, ...]


def _decimal(coefficient: str | None, scale: int | None) -> Decimal | None:
    if coefficient is None or scale is None:
        return None
    return Decimal(int(coefficient)).scaleb(-int(scale))


def _incumbent(connection: sqlite3.Connection, event_id: str, event_part_id: str) -> tuple[str | None, str]:
    rows = connection.execute(
        """SELECT supplier_id, event_part_id
           FROM incumbency_assignment assignment
           WHERE assignment.event_id = ?
             AND assignment.assignment_status = 'Incumbent'
             AND (assignment.event_part_id = ? OR assignment.event_part_id IS NULL)
             AND NOT EXISTS (
                 SELECT 1 FROM incumbency_assignment newer
                 WHERE newer.supersedes_assignment_id = assignment.incumbency_assignment_id)
           ORDER BY CASE WHEN assignment.event_part_id IS NOT NULL THEN 0 ELSE 1 END""",
        (event_id, event_part_id),
    ).fetchall()
    if not rows:
        return None, "Not Defined"
    most_specific = [row for row in rows if row[1] is not None] or rows
    suppliers = {str(row[0]) for row in most_specific}
    if len(suppliers) != 1:
        return None, "Ambiguous"
    return next(iter(suppliers)), "Confirmed"


def _fpv(connection: sqlite3.Connection, event_id: str, event_part_id: str, program_year: int | None) -> Decimal | None:
    if program_year is None:
        return None
    rows = connection.execute(
        """SELECT value.decimal_coefficient, value.decimal_scale
           FROM gst_part_value value
           JOIN gst_baseline baseline ON baseline.gst_baseline_id = value.gst_baseline_id
           JOIN source_package package ON package.source_package_id = baseline.source_package_id
           WHERE package.event_id = ? AND value.event_part_id = ?
             AND value.program_year = ? AND value.measure_code = 'FPV'
             AND value.precision_status = 'Eligible'
             AND baseline.baseline_status = 'Confirmed'
           ORDER BY baseline.baseline_version DESC""",
        (event_id, event_part_id, program_year),
    ).fetchall()
    return _decimal(rows[0][0], rows[0][1]) if rows else None


def calculate_piece_price_metrics(
    connection: sqlite3.Connection,
    *,
    event_id: str,
    comparison: tuple[PiecePriceComparisonRow, ...],
    program_year: int | None = None,
) -> tuple[PartCommercialMetrics, ...]:
    results: list[PartCommercialMetrics] = []
    for part in comparison:
        incumbent_id, incumbency_status = _incumbent(connection, event_id, part.event_part_id)
        annual_fpv = _fpv(connection, event_id, part.event_part_id, program_year)
        incumbent_quote = next((quote for quote in part.quotes if quote.supplier_id == incumbent_id), None)
        incumbent_price = incumbent_quote.piece_price if incumbent_quote else None

        comparable_groups: dict[tuple[str, str], list[Decimal]] = {}
        for quote in part.quotes:
            if quote.piece_price is not None and quote.currency_id and quote.normalized_unit_id:
                comparable_groups.setdefault((quote.currency_id, quote.normalized_unit_id), []).append(quote.piece_price)

        metrics: list[SupplierPriceMetric] = []
        for quote in part.quotes:
            key = (quote.currency_id, quote.normalized_unit_id)
            peers = sorted(set(comparable_groups.get(key, [])))
            comparable = quote.piece_price is not None and len(peers) > 0
            rank = peers.index(quote.piece_price) + 1 if comparable else None
            peer_gap = quote.piece_price - peers[0] if comparable else None
            target_comparable = (
                quote.piece_price is not None and part.target_piece_price is not None
                and quote.currency_id == part.target_currency_id
                and quote.normalized_unit_id == part.target_normalized_unit_id
            )
            target_gap = quote.piece_price - part.target_piece_price if target_comparable else None
            incumbent_comparable = (
                quote.piece_price is not None and incumbent_price is not None
                and incumbent_quote.currency_id == quote.currency_id
                and incumbent_quote.normalized_unit_id == quote.normalized_unit_id
            )
            savings = incumbent_price - quote.piece_price if incumbent_comparable else None
            annual_savings = savings * annual_fpv if savings is not None and annual_fpv is not None else None
            if quote.piece_price is None:
                status = quote.coverage_status
            elif not quote.currency_id or not quote.normalized_unit_id:
                status = "Unit or Currency Missing"
            else:
                status = "Comparable"
            metrics.append(SupplierPriceMetric(
                quote.supplier_id, quote.piece_price, quote.currency_id,
                quote.normalized_unit_id, rank, peer_gap, target_gap,
                savings, annual_savings, status,
            ))
        results.append(PartCommercialMetrics(
            part.event_part_id, incumbent_id, incumbency_status, annual_fpv, tuple(metrics)
        ))
    return tuple(results)
