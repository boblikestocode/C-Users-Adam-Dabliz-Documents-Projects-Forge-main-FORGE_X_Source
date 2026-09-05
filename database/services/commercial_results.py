from __future__ import annotations

import sqlite3
import json
from decimal import Decimal

from .analysis import CalculationResultInput, ResultLineage, persist_calculation_run
from .audit import AuditContext
from .decimals import ExactDecimal
from .package_economics import PackageEconomics
from .piece_price import PiecePriceComparisonRow
from .round_evolution import SupplierRoundEvolution


def _exact(value: Decimal | int | None) -> ExactDecimal | None:
    return None if value is None else ExactDecimal.parse(format(Decimal(value), "f"))


def _supplier_lineage(
    comparison: tuple[PiecePriceComparisonRow, ...], supplier_id: str, baseline_id: str
) -> tuple[ResultLineage, ...]:
    observation_ids = sorted({
        quote.observation_id for part in comparison for quote in part.quotes
        if quote.supplier_id == supplier_id and quote.observation_id is not None
    })
    return (
        ResultLineage("GST Baseline", baseline_id, "Governing FPV and Target"),
        *(ResultLineage("PBD Observation", observation_id, "Selected Active Piece Price")
          for observation_id in observation_ids),
    )


def cross_border_result_inputs(
    connection: sqlite3.Connection, *, event_id: str,
    reconstruction_ids: tuple[str, ...],
) -> tuple[CalculationResultInput, ...]:
    results: list[CalculationResultInput] = []
    for reconstruction_id in reconstruction_ids:
        row = connection.execute(
            """SELECT reconstruction.confidence_classification,
                      reconstruction.result_payload, pair.part_id,
                      mx.supplier_id, price.normalized_unit_id, price.currency_id,
                      us.context_id, mx.context_id
               FROM cross_border_reconstruction reconstruction
               JOIN cross_border_exact_part_pair pair
                 ON pair.cross_border_pair_id = reconstruction.cross_border_pair_id
               JOIN pbd_observation us ON us.observation_id = pair.us_observation_id
               JOIN pbd_observation mx ON mx.observation_id = pair.mx_observation_id
               LEFT JOIN location_measure_evidence price
                 ON price.observation_id = pair.mx_observation_id
                AND price.measure_code = 'PIECE_PRICE'
               WHERE reconstruction.cross_border_reconstruction_id = ?""",
            (reconstruction_id,),
        ).fetchone()
        if row is None:
            raise ValueError("Cross-border reconstruction does not exist")
        if row[6] != event_id or row[7] != event_id:
            raise ValueError("Cross-border reconstruction must belong to the scenario sourcing event")
        payload = json.loads(row[1])
        confidence = str(row[0])
        for key, value in sorted(payload.items()):
            code = f"CROSS_BORDER_{key.upper()}"
            if key == "total_component_opportunity" and confidence == "Confirmed":
                code = "CROSS_BORDER_CONFIRMED_SAVINGS"
            results.append(CalculationResultInput(
                code, ExactDecimal.parse(str(value)), supplier_id=row[3], part_id=row[2],
                normalized_unit_id=row[4], currency_id=row[5],
                confidence_classification=confidence,
                lineage=(ResultLineage(
                    "Cross Border Reconstruction", reconstruction_id,
                    "Exact-Part Cross-Border Reconstruction",
                ),),
            ))
    return tuple(results)


def persist_commercial_results(
    connection: sqlite3.Connection,
    *,
    scenario_revision_id: str,
    engine_version_id: str,
    calculation_rule_version_id: str,
    package: PackageEconomics,
    comparison: tuple[PiecePriceComparisonRow, ...],
    evolutions: tuple[SupplierRoundEvolution, ...],
    started_at_utc: str,
    completed_at_utc: str,
    audit: AuditContext,
    cross_border_reconstruction_ids: tuple[str, ...] = (),
) -> str:
    scenario = connection.execute(
        """SELECT revision.gst_baseline_id, analysis.event_id
           FROM scenario_revision revision
           JOIN analysis ON analysis.analysis_id = revision.analysis_id
           WHERE revision.scenario_revision_id = ?""", (scenario_revision_id,),
    ).fetchone()
    if scenario is None or scenario[0] is None:
        raise ValueError("Commercial result persistence requires a scenario with a GST baseline")
    baseline_id, event_id = str(scenario[0]), str(scenario[1])
    if event_id != package.event_id or any(item.event_id != event_id for item in evolutions):
        raise ValueError("Commercial results must belong to the scenario sourcing event")
    results: list[CalculationResultInput] = []
    target_written: set[int] = set()
    for supplier in package.suppliers:
        lineage = _supplier_lineage(comparison, supplier.supplier_id, baseline_id)
        for annual in supplier.annual_values:
            common = dict(supplier_id=supplier.supplier_id, program_year=annual.program_year,
                          normalized_unit_id=supplier.normalized_unit_id,
                          currency_id=supplier.currency_id, lineage=lineage)
            results.extend((
                CalculationResultInput("SUPPLIER_BASE_APV", _exact(annual.base_apv), **common),
                CalculationResultInput("SUPPLIER_LTA_APV", _exact(annual.lta_adjusted_apv), **common),
                CalculationResultInput("SUPPLIER_ONE_TIME_COST", _exact(annual.one_time_cost), **common),
                CalculationResultInput("SUPPLIER_TOTAL_CASH_FLOW", _exact(annual.total_cash_flow),
                                       confidence_classification=supplier.payment_treatment_status, **common),
                CalculationResultInput("SUPPLIER_DISCOUNTED_APV", _exact(annual.discounted_value), **common),
            ))
            if annual.gap_to_target is not None:
                results.append(CalculationResultInput("SUPPLIER_TARGET_GAP", _exact(annual.gap_to_target), **common))
            if annual.target_apv is not None and annual.program_year not in target_written:
                results.append(CalculationResultInput(
                    "OFFICIAL_TARGET_APV", _exact(annual.target_apv), program_year=annual.program_year,
                    normalized_unit_id=package.target_normalized_unit_id,
                    currency_id=package.target_currency_id,
                    lineage=(ResultLineage("GST Baseline", baseline_id, "Official Company Target and FPV"),),
                ))
                target_written.add(annual.program_year)
        results.extend((
            CalculationResultInput("SUPPLIER_LIFECYCLE_SPEND", _exact(supplier.lifecycle_spend),
                                   supplier_id=supplier.supplier_id, normalized_unit_id=supplier.normalized_unit_id,
                                   currency_id=supplier.currency_id, lineage=lineage),
            CalculationResultInput("SUPPLIER_NPV", _exact(supplier.npv), supplier_id=supplier.supplier_id,
                                   normalized_unit_id=supplier.normalized_unit_id, currency_id=supplier.currency_id,
                                   confidence_classification=supplier.package_status, lineage=lineage),
            CalculationResultInput("SUPPLIER_PACKAGE_RANK", _exact(supplier.package_rank),
                                   supplier_id=supplier.supplier_id, normalized_unit_id="RANK",
                                   confidence_classification=supplier.package_status, lineage=lineage),
        ))
        if supplier.lifecycle_gap_to_target is not None:
            results.append(CalculationResultInput(
                "SUPPLIER_LIFECYCLE_TARGET_GAP", _exact(supplier.lifecycle_gap_to_target),
                supplier_id=supplier.supplier_id, normalized_unit_id=supplier.normalized_unit_id,
                currency_id=supplier.currency_id, lineage=lineage,
            ))
    for evolution in evolutions:
        for point in evolution.rounds:
            lineage = (
                ResultLineage("GST Baseline", baseline_id, "Governing FPV"),
                ResultLineage("Quote Round", point.quote_round_id, "Selected Supplier Round"),
            )
            category = f"ROUND_{point.round_number}"
            results.append(CalculationResultInput(
                "ROUND_TOTAL_QUOTED_APV", _exact(point.total_quoted_apv),
                supplier_id=evolution.supplier_id, category_code=category,
                normalized_unit_id="CURRENCY", confidence_classification=point.package_status,
                lineage=lineage,
            ))
            if point.movement_from_prior_percent is not None:
                results.append(CalculationResultInput(
                    "ROUND_PRIOR_MOVEMENT_PERCENT", _exact(point.movement_from_prior_percent),
                    supplier_id=evolution.supplier_id, category_code=category,
                    normalized_unit_id="PERCENT", lineage=lineage,
                ))
            if point.movement_from_initial_percent is not None:
                results.append(CalculationResultInput(
                    "ROUND_CUMULATIVE_MOVEMENT_PERCENT", _exact(point.movement_from_initial_percent),
                    supplier_id=evolution.supplier_id, category_code=category,
                    normalized_unit_id="PERCENT", lineage=lineage,
                ))
    results.extend(cross_border_result_inputs(
        connection, event_id=event_id,
        reconstruction_ids=cross_border_reconstruction_ids,
    ))
    return persist_calculation_run(
        connection, scenario_revision_id=scenario_revision_id,
        engine_version_id=engine_version_id,
        calculation_rule_version_id=calculation_rule_version_id,
        results=tuple(results), started_at_utc=started_at_utc,
        completed_at_utc=completed_at_utc, audit=audit,
    )
