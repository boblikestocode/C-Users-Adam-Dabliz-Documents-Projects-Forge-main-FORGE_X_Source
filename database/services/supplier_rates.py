"""Descriptive same-year supplier economics with explicit comparable bases."""
from __future__ import annotations

import hashlib
import sqlite3
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP, localcontext

from .audit import AuditContext, append_audit_event, canonical_json
from .connection import immediate_transaction
from .ids import uuid7
from .profile_populations import economic_age_at_cutoff, observation_region_at_cutoff


RATE_CATEGORIES = {
    "LABOR_RATE", "FACTORY_OVERHEAD_PERCENT", "HEAD_OFFICE_OVERHEAD_PERCENT",
    "R_AND_D_OVERHEAD_PERCENT", "OTHER_OVERHEAD_PERCENT", "PROFIT_PERCENT",
}
POPULATION_VERSION = "Same Year Rates v1"


def rate_statistics(by_event: dict[str, list[Decimal]]) -> dict:
    """Expose raw and equally weighted independent-event statistics separately."""
    if not by_event or any(not values for values in by_event.values()):
        raise ValueError("Each contributing event requires rate evidence")
    with localcontext() as context:
        context.prec = 80
        render = lambda value: format(value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP), "f")
        values = [value for event_values in by_event.values() for value in event_values]
        event_means = [sum(v) / len(v) for v in by_event.values()]
        return {
            "independent_event_count": len(by_event),
            "minimum": render(min(values)), "maximum": render(max(values)),
            "range": render(max(values) - min(values)),
            "raw_mean": render(sum(values) / len(values)),
            "equal_event_mean": render(sum(event_means) / len(event_means)),
            "event_distributions": [
                {"event_id": event, "measure_count": len(v),
                 "minimum": render(min(v)), "maximum": render(max(v)),
                 "mean": render(sum(v) / len(v))}
                for event, v in sorted(by_event.items())],
        }


def confirm_supplier_rate_interpretation(
    connection: sqlite3.Connection, *, submitted_datum_id: str,
    region_code: str, rate_category_code: str, comparison_basis_code: str,
    confirmed_by_user_id: str, confirmation_reason: str, recorded_at_utc: str,
    audit: AuditContext,
) -> str:
    region = region_code.strip().upper()
    basis = comparison_basis_code.strip()
    if not region or not basis or not confirmation_reason.strip() or not confirmed_by_user_id.strip():
        raise ValueError("Rate comparability requires region, basis, buyer, and reason")
    if rate_category_code not in RATE_CATEGORIES:
        raise ValueError("Unsupported supplier consistency rate category")
    identifier = uuid7()
    with immediate_transaction(connection):
        datum = connection.execute(
            """SELECT decimal_coefficient, decimal_scale, normalized_unit_id, currency_id,
                      precision_status, recorded_at_utc, field_code FROM submitted_datum
               WHERE submitted_datum_id = ?""", (submitted_datum_id,),
        ).fetchone()
        if datum is None or any(datum[i] is None for i in range(4)) or datum[4] != "Eligible":
            raise ValueError("Rate interpretation requires eligible source value, currency, and unit")
        if datum[5] > recorded_at_utc:
            raise ValueError("Rate interpretation cannot precede its source evidence")
        if datum[6] != rate_category_code and not (
            datum[6] == "OVERHEAD_PERCENT" and rate_category_code.endswith("OVERHEAD_PERCENT")
        ):
            raise ValueError("Rate category must match the submitted rate field")
        prior = connection.execute(
            """SELECT interpretation_id FROM supplier_rate_interpretation current
               WHERE submitted_datum_id = ? AND NOT EXISTS (
                   SELECT 1 FROM supplier_rate_interpretation newer
                   WHERE newer.supersedes_interpretation_id = current.interpretation_id)""",
            (submitted_datum_id,),
        ).fetchone()
        connection.execute(
            "INSERT INTO supplier_rate_interpretation VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (identifier, submitted_datum_id, region, rate_category_code, basis,
             confirmed_by_user_id, confirmation_reason, prior[0] if prior else None,
             recorded_at_utc),
        )
        append_audit_event(connection, audit, {
            "interpretation_id": identifier, "submitted_datum_id": submitted_datum_id,
            "region_code": region, "rate_category_code": rate_category_code,
            "comparison_basis_code": basis, "confirmation_reason": confirmation_reason,
        })
    return identifier


def derive_supplier_rate_distributions(
    connection: sqlite3.Connection, *, supplier_id: str, commodity_id: str,
    region_code: str, evidence_cutoff_utc: str, supplier_plant_id: str | None = None,
) -> dict:
    region = region_code.strip().upper()
    if not region:
        raise ValueError("Supplier economic comparisons require an explicit region")
    candidates = connection.execute(
        """SELECT interpretation.*, datum.observation_id, datum.source_datum_id,
                  datum.decimal_coefficient, datum.decimal_scale,
                  datum.normalized_unit_id, datum.currency_id, datum.precision_status,
                  datum.recorded_at_utc AS datum_recorded_at,
                  observation.supplier_plant_id
           FROM supplier_rate_interpretation interpretation
           JOIN submitted_datum datum USING (submitted_datum_id)
           JOIN pbd_observation observation USING (observation_id)
           WHERE observation.supplier_id = ? AND interpretation.recorded_at_utc <= ?
             AND (? IS NULL OR observation.supplier_plant_id = ?)
             AND NOT EXISTS (
                 SELECT 1 FROM supplier_rate_interpretation newer
                 WHERE newer.supersedes_interpretation_id = interpretation.interpretation_id
                   AND newer.recorded_at_utc <= ?)
           ORDER BY interpretation.interpretation_id""",
        (supplier_id, evidence_cutoff_utc, supplier_plant_id, supplier_plant_id, evidence_cutoff_utc),
    ).fetchall()
    groups = defaultdict(list)
    exclusions = []
    for row in candidates:
        events = connection.execute(
            """SELECT DISTINCT event.event_id FROM round_observation member
               JOIN supplier_quote_round round USING (quote_round_id)
               JOIN sourcing_event event USING (event_id)
               WHERE member.observation_id = ? AND member.recorded_at_utc <= ?
                 AND round.recorded_at_utc <= ? AND event.created_at_utc <= ?
                 AND event.commodity_id = ? ORDER BY event.event_id""",
            (row["observation_id"], evidence_cutoff_utc, evidence_cutoff_utc,
             evidence_cutoff_utc, commodity_id),
        ).fetchall()
        age = economic_age_at_cutoff(connection, row["observation_id"], evidence_cutoff_utc)
        reason = None
        if len(events) != 1:
            reason = "UNRESOLVED_SINGLE_EVENT_LINEAGE"
        elif row["datum_recorded_at"] > evidence_cutoff_utc:
            reason = "SOURCE_NOT_AVAILABLE_AT_CUTOFF"
        elif row["precision_status"] != "Eligible":
            reason = "PRECISION_BLOCKED"
        elif age.eligibility_status != "Eligible":
            reason = age.reason_code
        elif (row["region_code"] != region or
              observation_region_at_cutoff(connection, row["observation_id"], evidence_cutoff_utc) != region):
            reason = "REGION_NOT_ALIGNED"
        elif not row["normalized_unit_id"] or not row["currency_id"]:
            reason = "UNIT_OR_CURRENCY_MISSING"
        if reason:
            exclusions.append({"interpretation_id": row["interpretation_id"],
                               "observation_id": row["observation_id"], "reason": reason})
            continue
        key = (int(age.economic_date[:4]), row["rate_category_code"],
               row["normalized_unit_id"], row["currency_id"],
               row["comparison_basis_code"], row["supplier_plant_id"])
        groups[key].append({
            "interpretation_id": row["interpretation_id"],
            "submitted_datum_id": row["submitted_datum_id"],
            "source_datum_id": row["source_datum_id"],
            "observation_id": row["observation_id"], "event_id": events[0][0],
            "economic_date": age.economic_date, "date_precision": age.date_precision,
            "coefficient": row["decimal_coefficient"], "scale": row["decimal_scale"],
        })
    distributions = []
    with localcontext() as context:
        context.prec = 80
        for key, evidence in sorted(groups.items(), key=lambda pair: canonical_json(pair[0])):
            by_event = defaultdict(list)
            for item in evidence:
                value = Decimal(item["coefficient"]).scaleb(-item["scale"])
                by_event[item["event_id"]].append(value)
            distributions.append({
                "economic_year": key[0], "rate_category_code": key[1],
                "normalized_unit_id": key[2], "currency_id": key[3],
                "comparison_basis_code": key[4], "supplier_plant_id": key[5],
                "measure_count": len(evidence),
                "quote_count": len({item["observation_id"] for item in evidence}),
                **rate_statistics(by_event),
                "status": "Descriptive Only" if len(by_event) >= 2 else "Insufficient Independent Events",
                "evidence": evidence,
            })
    return {"population_version": POPULATION_VERSION, "supplier_id": supplier_id,
            "commodity_id": commodity_id, "region_code": region,
            "supplier_plant_id": supplier_plant_id, "evidence_cutoff_utc": evidence_cutoff_utc,
            "distributions": distributions, "excluded_evidence": exclusions,
            "limitations": ["No numerical traffic-light thresholds have been approved.",
                            "Missing plant evidence is reported separately from known plants.",
                            "Each distinct event has equal weight in the equal-event mean."]}


def build_supplier_rate_distributions(
    connection: sqlite3.Connection, *, supplier_id: str, commodity_id: str,
    region_code: str, evidence_cutoff_utc: str, calculation_rule_version_id: str,
    engine_version_id: str, recorded_at_utc: str, audit: AuditContext,
    supplier_plant_id: str | None = None,
) -> str:
    identifier = uuid7()
    with immediate_transaction(connection):
        payload = derive_supplier_rate_distributions(
            connection, supplier_id=supplier_id, commodity_id=commodity_id,
            region_code=region_code, evidence_cutoff_utc=evidence_cutoff_utc,
            supplier_plant_id=supplier_plant_id,
        )
        serialized = canonical_json(payload)
        digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        connection.execute(
            "INSERT INTO supplier_rate_distribution_run VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (identifier, supplier_id, commodity_id, payload["region_code"], supplier_plant_id,
             evidence_cutoff_utc, calculation_rule_version_id, engine_version_id,
             POPULATION_VERSION, serialized, digest, recorded_at_utc),
        )
        append_audit_event(connection, audit, {"distribution_run_id": identifier, "manifest_hash": digest})
    return identifier
