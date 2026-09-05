from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

from .audit import AuditContext, append_audit_event, canonical_json
from .actions import create_buyer_action
from .connection import immediate_transaction
from .decimals import ExactDecimal
from .ids import uuid7


MEASURE_CODES = {
    "PIECE_PRICE", "RAW_MATERIAL", "PURCHASED_COMPONENTS", "LABOR_HOURS",
    "LABOR_RATE", "LABOR_DOLLARS", "BURDEN_DOLLARS", "OTHER_PHYSICAL_COST",
    "OVERHEAD_PERCENT", "PROFIT_PERCENT", "TOTAL_CONVERSION_COST",
}
REQUIRED_US = {
    "PIECE_PRICE", "RAW_MATERIAL", "PURCHASED_COMPONENTS", "LABOR_HOURS",
    "LABOR_RATE", "LABOR_DOLLARS", "BURDEN_DOLLARS", "OTHER_PHYSICAL_COST",
    "OVERHEAD_PERCENT", "PROFIT_PERCENT",
}


@dataclass(frozen=True)
class CrossBorderResult:
    reconstruction_id: str
    confidence_classification: str
    missing_evidence: tuple[str, ...]
    benchmark_evidence_id: str | None
    values: dict[str, Decimal]


@dataclass(frozen=True)
class OperationEvidenceInput:
    normalized_operation: str
    unit_basis: str
    burden_rate: ExactDecimal
    burden_hours: ExactDecimal
    normalized_rate_unit_id: str
    currency_id: str
    equipment_identifier: str | None = None
    normalized_equipment_description: str | None = None
    equipment_size_or_capacity: str | None = None
    process_type: str | None = None


@dataclass(frozen=True)
class OperationMatchResult:
    operation_match_id: str
    match_classification: str
    match_reason: str
    burden_opportunity: Decimal | None


def _decimal(row: sqlite3.Row) -> Decimal:
    return Decimal(int(row["decimal_coefficient"])).scaleb(-int(row["decimal_scale"]))


def _rounded(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def record_location_measure(
    connection: sqlite3.Connection,
    *, observation_id: str, source_datum_id: str, region_code: str,
    measure_code: str, value: ExactDecimal, normalized_unit_id: str,
    currency_id: str | None, calculation_basis_code: str | None,
    evidence_status: str, recorded_at_utc: str, audit: AuditContext,
) -> str:
    if region_code not in ("US", "MX") or measure_code not in MEASURE_CODES:
        raise ValueError("Unsupported cross-border region or measure")
    if evidence_status not in ("Valid", "Reference Only", "Blocked"):
        raise ValueError("Unsupported location-measure evidence status")
    evidence_id = uuid7()
    with immediate_transaction(connection):
        row = connection.execute(
            """SELECT 1 FROM pbd_observation observation
               JOIN source_occurrence occurrence ON occurrence.occurrence_id = observation.occurrence_id
               JOIN source_datum datum ON datum.worksheet_id = occurrence.worksheet_id
               WHERE observation.observation_id = ? AND datum.source_datum_id = ?""",
            (observation_id, source_datum_id),
        ).fetchone()
        if row is None:
            raise ValueError("Location measure source datum must belong to its observation")
        connection.execute(
            """INSERT INTO location_measure_evidence VALUES
               (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (evidence_id, observation_id, source_datum_id, region_code, measure_code,
             value.submitted_lexeme, value.coefficient, value.scale,
             normalized_unit_id, currency_id, calculation_basis_code,
             evidence_status, recorded_at_utc),
        )
        append_audit_event(connection, audit, {
            "location_measure_evidence_id": evidence_id, "observation_id": observation_id,
            "region_code": region_code, "measure_code": measure_code,
            "evidence_status": evidence_status,
        })
    return evidence_id


def create_exact_part_pair(
    connection: sqlite3.Connection, *, us_observation_id: str,
    mx_observation_id: str, pairing_rule_version_id: str,
    recorded_at_utc: str, audit: AuditContext,
) -> str:
    pair_id = uuid7()
    with immediate_transaction(connection):
        observations = connection.execute(
            """SELECT observation_id, part_id FROM pbd_observation
               WHERE observation_id IN (?, ?)""",
            (us_observation_id, mx_observation_id),
        ).fetchall()
        if len(observations) != 2 or observations[0][1] is None or observations[0][1] != observations[1][1]:
            raise ValueError("Cross-border pairing requires the same exact canonical part identity")
        regions = dict(connection.execute(
            """SELECT observation_id, region_code FROM location_measure_evidence
               WHERE observation_id IN (?, ?) GROUP BY observation_id, region_code""",
            (us_observation_id, mx_observation_id),
        ).fetchall())
        if regions.get(us_observation_id) != "US" or regions.get(mx_observation_id) != "MX":
            raise ValueError("Cross-border pair requires supported US and Mexico region evidence")
        connection.execute(
            "INSERT INTO cross_border_exact_part_pair VALUES (?, ?, ?, ?, ?, ?)",
            (pair_id, observations[0][1], us_observation_id, mx_observation_id,
             pairing_rule_version_id, recorded_at_utc),
        )
        append_audit_event(connection, audit, {
            "cross_border_pair_id": pair_id, "part_id": observations[0][1],
            "us_observation_id": us_observation_id, "mx_observation_id": mx_observation_id,
            "relationship": "Exact Part",
        })
    return pair_id


def reconstruct_us_to_mexico(
    connection: sqlite3.Connection, *, cross_border_pair_id: str,
    calculation_rule_version_id: str, engine_version_id: str,
    calculated_at_utc: str, audit: AuditContext,
) -> CrossBorderResult:
    with immediate_transaction(connection):
        pair = connection.execute(
            "SELECT * FROM cross_border_exact_part_pair WHERE cross_border_pair_id = ?",
            (cross_border_pair_id,),
        ).fetchone()
        if pair is None:
            raise ValueError("Cross-border exact-part pair does not exist")
        us_rows = connection.execute(
            """SELECT * FROM location_measure_evidence
               WHERE observation_id = ? AND region_code = 'US' AND evidence_status = 'Valid'""",
            (pair["us_observation_id"],),
        ).fetchall()
        mx_rows = connection.execute(
            """SELECT * FROM location_measure_evidence
               WHERE observation_id = ? AND region_code = 'MX' AND evidence_status = 'Valid'""",
            (pair["mx_observation_id"],),
        ).fetchall()
        us = {str(row["measure_code"]): row for row in us_rows}
        mx = {str(row["measure_code"]): row for row in mx_rows}
        missing = sorted(REQUIRED_US - us.keys())
        if "PIECE_PRICE" not in mx:
            missing.append("MX:PIECE_PRICE")
        labor_hours = us.get("LABOR_HOURS")
        benchmark = None
        if labor_hours is not None:
            benchmark = connection.execute(
                """SELECT evidence.*, observation.submitted_supplier_name,
                          observation.supplier_plant_id, observation.part_id,
                          worksheet.submitted_name AS worksheet_name
                   FROM location_measure_evidence evidence
                   JOIN v_economically_eligible_observation observation
                     ON observation.observation_id = evidence.observation_id
                   JOIN source_datum datum ON datum.source_datum_id = evidence.source_datum_id
                   JOIN source_worksheet worksheet ON worksheet.worksheet_id = datum.worksheet_id
                   WHERE evidence.region_code = 'MX' AND evidence.measure_code = 'LABOR_RATE'
                     AND evidence.evidence_status = 'Valid'
                     AND evidence.normalized_unit_id = ?
                     AND COALESCE(evidence.currency_id, '') = COALESCE(?, '')
                   ORDER BY (CAST(evidence.decimal_coefficient AS REAL) /
                             pow(10, evidence.decimal_scale)), evidence.location_measure_evidence_id
                   LIMIT 1""",
                (us["LABOR_RATE"]["normalized_unit_id"] if "LABOR_RATE" in us else "",
                 us["LABOR_RATE"]["currency_id"] if "LABOR_RATE" in us else None),
            ).fetchone()
        if benchmark is None:
            missing.append("MX:VALID_LABOR_RATE_BENCHMARK")
        basis_errors = []
        if us.get("OVERHEAD_PERCENT") is not None and us["OVERHEAD_PERCENT"]["calculation_basis_code"] != "ADJUSTED_CONVERSION":
            basis_errors.append("US:OVERHEAD_PERCENT_BASIS")
        if us.get("PROFIT_PERCENT") is not None and us["PROFIT_PERCENT"]["calculation_basis_code"] != "COST_PLUS_OVERHEAD":
            basis_errors.append("US:PROFIT_PERCENT_BASIS")
        missing.extend(basis_errors)
        values: dict[str, Decimal] = {}
        confidence = "Directional Estimate - Low Confidence" if missing else "Confirmed"
        if not missing:
            raw = _decimal(us["RAW_MATERIAL"])
            purchased = _decimal(us["PURCHASED_COMPONENTS"])
            other = _decimal(us["OTHER_PHYSICAL_COST"])
            hours = _decimal(us["LABOR_HOURS"])
            old_labor = _decimal(us["LABOR_DOLLARS"])
            burden = _decimal(us["BURDEN_DOLLARS"])
            new_labor = hours * _decimal(benchmark)
            conversion = new_labor + burden
            overhead = conversion * _decimal(us["OVERHEAD_PERCENT"]) / Decimal(100)
            pre_profit = raw + purchased + other + conversion + overhead
            profit = pre_profit * _decimal(us["PROFIT_PERCENT"]) / Decimal(100)
            model = pre_profit + profit
            mx_price = _decimal(mx["PIECE_PRICE"])
            old_conversion = old_labor + burden
            old_overhead = old_conversion * _decimal(us["OVERHEAD_PERCENT"]) / Decimal(100)
            old_pre_profit = raw + purchased + other + old_conversion + old_overhead
            old_profit = old_pre_profit * _decimal(us["PROFIT_PERCENT"]) / Decimal(100)
            values = {key: _rounded(value) for key, value in {
                "us_piece_price": _decimal(us["PIECE_PRICE"]), "mx_piece_price": mx_price,
                "us_labor_rate": _decimal(us["LABOR_RATE"]),
                "benchmark_labor_rate": _decimal(benchmark), "adjusted_labor": new_labor,
                "preserved_burden": burden, "adjusted_overhead": overhead,
                "adjusted_profit": profit, "labor_opportunity": old_labor - new_labor,
                "overhead_opportunity": old_overhead - overhead,
                "profit_opportunity": old_profit - profit,
                "reconstructed_model_cost": model, "remaining_price_bridge": mx_price - model,
                "total_component_opportunity": mx_price - model,
            }.items()}
        reconstruction_id = uuid7()
        manifest = {
            "us_observation_id": pair["us_observation_id"],
            "mx_observation_id": pair["mx_observation_id"],
            "us_evidence_ids": sorted(row["location_measure_evidence_id"] for row in us_rows),
            "mx_evidence_ids": sorted(row["location_measure_evidence_id"] for row in mx_rows),
            "benchmark": None if benchmark is None else {
                "evidence_id": benchmark["location_measure_evidence_id"],
                "supplier": benchmark["submitted_supplier_name"],
                "plant": benchmark["supplier_plant_id"], "part_id": benchmark["part_id"],
                "worksheet": benchmark["worksheet_name"],
            },
        }
        connection.execute(
            """INSERT INTO cross_border_reconstruction VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (reconstruction_id, cross_border_pair_id,
             None if benchmark is None else benchmark["location_measure_evidence_id"],
             calculation_rule_version_id, engine_version_id, confidence,
             canonical_json(manifest), canonical_json(sorted(set(missing))),
             canonical_json({key: format(value, 'f') for key, value in values.items()}),
             calculated_at_utc),
        )
        append_audit_event(connection, audit, {
            "cross_border_reconstruction_id": reconstruction_id,
            "cross_border_pair_id": cross_border_pair_id,
            "confidence_classification": confidence,
            "missing_evidence": sorted(set(missing)),
            "result": {key: format(value, 'f') for key, value in values.items()},
        })
    return CrossBorderResult(
        reconstruction_id, confidence, tuple(sorted(set(missing))),
        None if benchmark is None else str(benchmark["location_measure_evidence_id"]), values,
    )


def record_operation_evidence(
    connection: sqlite3.Connection, *, observation_id: str, operation_line_id: str,
    region_code: str, evidence: OperationEvidenceInput, recorded_at_utc: str,
    audit: AuditContext,
) -> str:
    if region_code not in ("US", "MX"):
        raise ValueError("Operation evidence region must be US or MX")
    if not evidence.normalized_operation.strip() or not evidence.unit_basis.strip():
        raise ValueError("Normalized operation and unit basis are required")
    evidence_id = uuid7()
    with immediate_transaction(connection):
        if connection.execute(
            """SELECT 1 FROM operation_line
               WHERE operation_line_id = ? AND observation_id = ?""",
            (operation_line_id, observation_id),
        ).fetchone() is None:
            raise ValueError("Operation line must belong to its observation")
        connection.execute(
            """INSERT INTO cross_border_operation_evidence VALUES
               (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (evidence_id, observation_id, operation_line_id, region_code,
             evidence.normalized_operation.strip(), evidence.equipment_identifier,
             evidence.normalized_equipment_description, evidence.equipment_size_or_capacity,
             evidence.process_type, evidence.unit_basis.strip(),
             evidence.burden_rate.coefficient, evidence.burden_rate.scale,
             evidence.burden_hours.coefficient, evidence.burden_hours.scale,
             evidence.normalized_rate_unit_id, evidence.currency_id, recorded_at_utc),
        )
        append_audit_event(connection, audit, {
            "cross_border_operation_evidence_id": evidence_id,
            "observation_id": observation_id, "operation_line_id": operation_line_id,
            "region_code": region_code, "normalized_operation": evidence.normalized_operation,
        })
    return evidence_id


def match_cross_border_operations(
    connection: sqlite3.Connection, *, cross_border_pair_id: str,
    us_operation_evidence_id: str, mx_operation_evidence_id: str,
    matching_rule_version_id: str, recorded_at_utc: str,
    audit: AuditContext,
) -> OperationMatchResult:
    with immediate_transaction(connection):
        pair = connection.execute(
            "SELECT us_observation_id, mx_observation_id FROM cross_border_exact_part_pair WHERE cross_border_pair_id = ?",
            (cross_border_pair_id,),
        ).fetchone()
        us = connection.execute(
            "SELECT * FROM cross_border_operation_evidence WHERE cross_border_operation_evidence_id = ?",
            (us_operation_evidence_id,),
        ).fetchone()
        mx = connection.execute(
            "SELECT * FROM cross_border_operation_evidence WHERE cross_border_operation_evidence_id = ?",
            (mx_operation_evidence_id,),
        ).fetchone()
        if pair is None or us is None or mx is None:
            raise ValueError("Pair and operation evidence must exist")
        if us["observation_id"] != pair[0] or mx["observation_id"] != pair[1] or us["region_code"] != "US" or mx["region_code"] != "MX":
            raise ValueError("Operation evidence must belong to the corresponding exact-part pair sides")
        same_operation = us["normalized_operation"] == mx["normalized_operation"]
        exact_equipment_id = (
            us["equipment_identifier"] is not None
            and us["equipment_identifier"] == mx["equipment_identifier"]
        )
        complete_signature = all(
            us[field] is not None and us[field] == mx[field]
            for field in ("normalized_equipment_description", "equipment_size_or_capacity", "process_type")
        ) and us["unit_basis"] == mx["unit_basis"]
        aligned_math = (
            us["burden_hours_coefficient"] == mx["burden_hours_coefficient"]
            and us["burden_hours_scale"] == mx["burden_hours_scale"]
            and us["normalized_rate_unit_id"] == mx["normalized_rate_unit_id"]
            and us["currency_id"] == mx["currency_id"]
        )
        exact = same_operation and (exact_equipment_id or complete_signature) and aligned_math
        if exact:
            us_rate = Decimal(int(us["burden_rate_coefficient"])).scaleb(-int(us["burden_rate_scale"]))
            mx_rate = Decimal(int(mx["burden_rate_coefficient"])).scaleb(-int(mx["burden_rate_scale"]))
            hours = Decimal(int(us["burden_hours_coefficient"])).scaleb(-int(us["burden_hours_scale"]))
            opportunity = _rounded(
                (us_rate - mx_rate) * hours
            )
            reason = "Exact normalized operation and equipment identity with aligned hours, rate unit, and currency"
            classification = "Exact Match"
        else:
            opportunity = None
            classification = "Reference Only"
            reason = "Operation/equipment identity or mathematical basis is not an exact supported match"
        match_id = uuid7()
        exact_value = None if opportunity is None else ExactDecimal.parse(format(opportunity, "f"))
        connection.execute(
            """INSERT INTO cross_border_operation_match VALUES
               (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (match_id, cross_border_pair_id, us_operation_evidence_id,
             mx_operation_evidence_id, classification, reason,
             None if exact_value is None else exact_value.coefficient,
             None if exact_value is None else exact_value.scale,
             matching_rule_version_id, recorded_at_utc),
        )
        append_audit_event(connection, audit, {
            "cross_border_operation_match_id": match_id,
            "cross_border_pair_id": cross_border_pair_id,
            "match_classification": classification, "match_reason": reason,
            "burden_opportunity": None if opportunity is None else format(opportunity, "f"),
        })
    return OperationMatchResult(match_id, classification, reason, opportunity)


def _aggregate_conversion(
    evidence: dict[str, sqlite3.Row], region: str
) -> tuple[Decimal | None, list[str]]:
    issues: list[str] = []
    total_row = evidence.get("TOTAL_CONVERSION_COST")
    labor_row = evidence.get("LABOR_DOLLARS")
    burden_row = evidence.get("BURDEN_DOLLARS")
    hours_row = evidence.get("LABOR_HOURS")
    rate_row = evidence.get("LABOR_RATE")
    if (hours_row is None) != (rate_row is None):
        issues.append(f"{region}:INCOMPLETE_LABOR_HOURS_RATE")
    if hours_row is not None and rate_row is not None:
        if labor_row is None:
            issues.append(f"{region}:MISSING_LABOR_DOLLARS")
        elif _rounded(_decimal(hours_row) * _decimal(rate_row)) != _rounded(_decimal(labor_row)):
            issues.append(f"{region}:LABOR_RECONCILIATION_EXCEPTION")
    split_total = None
    if labor_row is not None and burden_row is not None:
        split_total = _decimal(labor_row) + _decimal(burden_row)
    if total_row is not None and split_total is not None:
        if _rounded(_decimal(total_row)) != _rounded(split_total):
            issues.append(f"{region}:CONVERSION_RECONCILIATION_EXCEPTION")
    if total_row is not None:
        return _decimal(total_row), issues
    if split_total is not None:
        return split_total, issues
    issues.append(f"{region}:MISSING_SUPPORTED_CONVERSION_TOTAL")
    return None, issues


def compare_aggregate_conversion(
    connection: sqlite3.Connection, *, cross_border_pair_id: str,
    calculation_rule_version_id: str, engine_version_id: str,
    calculated_at_utc: str, audit: AuditContext,
) -> CrossBorderResult:
    """Compare exact-part aggregate conversion without manufacturing a split."""
    with immediate_transaction(connection):
        pair = connection.execute(
            "SELECT * FROM cross_border_exact_part_pair WHERE cross_border_pair_id = ?",
            (cross_border_pair_id,),
        ).fetchone()
        if pair is None:
            raise ValueError("Cross-border exact-part pair does not exist")
        rows = connection.execute(
            """SELECT * FROM location_measure_evidence
               WHERE observation_id IN (?, ?) AND evidence_status = 'Valid'""",
            (pair["us_observation_id"], pair["mx_observation_id"]),
        ).fetchall()
        by_observation: dict[str, dict[str, sqlite3.Row]] = {}
        for row in rows:
            by_observation.setdefault(str(row["observation_id"]), {})[str(row["measure_code"])] = row
        us = by_observation.get(str(pair["us_observation_id"]), {})
        mx = by_observation.get(str(pair["mx_observation_id"]), {})
        us_total, us_issues = _aggregate_conversion(us, "US")
        mx_total, mx_issues = _aggregate_conversion(mx, "MX")
        issues = sorted(set(us_issues + mx_issues))
        bases = {
            (row["currency_id"], row["normalized_unit_id"])
            for evidence in (us, mx)
            for code, row in evidence.items()
            if code in ("TOTAL_CONVERSION_COST", "LABOR_DOLLARS", "BURDEN_DOLLARS")
        }
        if len(bases) > 1:
            issues.append("CURRENCY_OR_UNIT_ALIGNMENT_REQUIRED")
        values: dict[str, Decimal] = {}
        if us_total is not None and mx_total is not None and not issues:
            values = {
                "us_conversion_cost": _rounded(us_total),
                "mx_conversion_cost": _rounded(mx_total),
                "aggregate_conversion_opportunity": _rounded(us_total - mx_total),
            }
        confidence = "Confirmed" if values else "Directional Estimate - Low Confidence"
        reconstruction_id = uuid7()
        manifest = {
            "method": "Aggregate Conversion Comparison",
            "us_observation_id": pair["us_observation_id"],
            "mx_observation_id": pair["mx_observation_id"],
            "evidence_ids": sorted(row["location_measure_evidence_id"] for row in rows),
            "split_manufactured": False,
        }
        connection.execute(
            """INSERT INTO cross_border_reconstruction VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?, ?)""",
            (reconstruction_id, cross_border_pair_id, calculation_rule_version_id,
             engine_version_id, confidence, canonical_json(manifest),
             canonical_json(issues),
             canonical_json({key: format(value, 'f') for key, value in values.items()}),
             calculated_at_utc),
        )
        append_audit_event(connection, audit, {
            "cross_border_reconstruction_id": reconstruction_id,
            "cross_border_pair_id": cross_border_pair_id,
            "method": "Aggregate Conversion Comparison",
            "confidence_classification": confidence, "issues": issues,
            "result": {key: format(value, 'f') for key, value in values.items()},
        })
    return CrossBorderResult(reconstruction_id, confidence, tuple(issues), None, values)


def create_cross_border_review_action(
    connection: sqlite3.Connection, *, cross_border_reconstruction_id: str,
    owner_user_id: str, created_by_user_id: str, created_at_utc: str,
    audit: AuditContext,
) -> str:
    row = connection.execute(
        """SELECT reconstruction.confidence_classification,
                  reconstruction.missing_evidence_payload,
                  us.observation_context, us.context_id,
                  mx.observation_context, mx.context_id
           FROM cross_border_reconstruction reconstruction
           JOIN cross_border_exact_part_pair pair
             ON pair.cross_border_pair_id = reconstruction.cross_border_pair_id
           JOIN pbd_observation us ON us.observation_id = pair.us_observation_id
           JOIN pbd_observation mx ON mx.observation_id = pair.mx_observation_id
           WHERE reconstruction.cross_border_reconstruction_id = ?""",
        (cross_border_reconstruction_id,),
    ).fetchone()
    if row is None:
        raise ValueError("Cross-border reconstruction does not exist")
    if row[0] != "Directional Estimate - Low Confidence":
        raise ValueError("Buyer review actions are only required for incomplete cross-border evidence")
    if row[2] != "Sourcing Event" or row[4] != "Sourcing Event" or row[3] is None or row[3] != row[5]:
        raise ValueError("Cross-border review action requires both observations in the same sourcing event")
    return create_buyer_action(
        connection, event_id=str(row[3]), supplier_id=None, part_id=None,
        governing_entity_type="Cross Border Reconstruction",
        governing_entity_id=cross_border_reconstruction_id,
        issue_type="Incomplete Cross-Border Evidence", financial_impact=None,
        currency_id=None,
        required_supplier_action=f"Complete or substantiate cross-border evidence: {row[1]}",
        owner_user_id=owner_user_id, created_by_user_id=created_by_user_id,
        created_at_utc=created_at_utc, audit=audit,
    )
