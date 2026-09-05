from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass

from .audit import AuditContext, append_audit_event, canonical_json
from .connection import immediate_transaction
from .ids import uuid7


REQUIRED_CATEGORIES = (
    "PBD Quality", "Current Market Position", "Labor", "Burden", "Overhead",
    "Profit", "Purchased Components", "Below the Line",
)


@dataclass(frozen=True)
class TrafficLightCategoryInput:
    category_code: str
    category_status: str
    confidence_classification: str
    material_commercial_impact: bool
    recurrence_count: int
    evidence_entity_type: str | None
    evidence_entity_id: str | None
    explanation: str


@dataclass(frozen=True)
class TrafficLightAssessmentResult:
    assessment_id: str
    overall_status: str
    governing_categories: tuple[str, ...]


@dataclass(frozen=True)
class SupplierOverallTrafficLightResult:
    assessment_id: str
    overall_status: str
    governing_commodities: tuple[str, ...]


def derive_traffic_light(
    categories: tuple[TrafficLightCategoryInput, ...],
) -> tuple[str, tuple[str, ...], str, str]:
    if tuple(item.category_code for item in categories) != REQUIRED_CATEGORIES:
        raise ValueError("Traffic-light categories must be complete and in governing order")
    governing_red = tuple(
        item.category_code for item in categories
        if item.category_status == "Red"
        and item.confidence_classification == "High"
        and (item.material_commercial_impact or item.recurrence_count >= 2)
    )
    if governing_red:
        status = "Red"
        governing = governing_red
        explanation = (
            "High-confidence material or recurring Red evidence governs the commodity-level "
            "supplier status; competitive categories do not offset it."
        )
    elif any(item.category_status == "Gray" for item in categories):
        status = "Gray"
        governing = tuple(item.category_code for item in categories
                          if item.category_status == "Gray")
        explanation = "Unsupported or unavailable category evidence prevents a supported overall conclusion."
    elif all(item.category_status == "Green" for item in categories):
        status = "Green"
        governing = REQUIRED_CATEGORIES
        explanation = "Complete supported category evidence, including PBD quality and current market position, is Green."
    else:
        status = "Yellow"
        governing = tuple(item.category_code for item in categories
                          if item.category_status in {"Yellow", "Red"})
        explanation = "Usable evidence is generally aligned, but limited concerns prevent the intentionally difficult Green conclusion."
    payload = [
        (item.category_code, item.category_status, item.confidence_classification,
         int(item.material_commercial_impact), item.recurrence_count,
         item.evidence_entity_type, item.evidence_entity_id, item.explanation)
        for item in categories
    ]
    digest = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
    return status, governing, explanation, digest


def _validate_evidence(
    connection: sqlite3.Connection, *, supplier_id: str, commodity_id: str,
    item: TrafficLightCategoryInput,
) -> None:
    if item.evidence_entity_type is None and item.evidence_entity_id is None:
        if item.category_status != "Gray":
            raise ValueError("Only unsupported Gray categories may omit evidence")
        return
    if item.evidence_entity_type == "Supplier Profile Finding":
        valid = connection.execute(
            """SELECT 1 FROM supplier_profile_finding finding
               JOIN supplier_profile_run run
                 ON run.supplier_profile_run_id = finding.supplier_profile_run_id
               WHERE finding.supplier_profile_finding_id = ?
                 AND run.supplier_id = ? AND run.commodity_id = ?
                 AND run.run_status = 'Complete'""",
            (item.evidence_entity_id, supplier_id, commodity_id),
        ).fetchone()
    elif item.evidence_entity_type == "Supplier Profile Run":
        valid = connection.execute(
            """SELECT 1 FROM supplier_profile_run
               WHERE supplier_profile_run_id = ? AND supplier_id = ?
                 AND commodity_id = ? AND run_status = 'Complete'""",
            (item.evidence_entity_id, supplier_id, commodity_id),
        ).fetchone()
    elif item.evidence_entity_type == "Supplier Competitiveness Observation":
        valid = connection.execute(
            """SELECT 1 FROM supplier_competitiveness_observation
               WHERE supplier_competitiveness_observation_id = ?
                 AND supplier_id = ? AND commodity_id = ?""",
            (item.evidence_entity_id, supplier_id, commodity_id),
        ).fetchone()
    else:
        raise ValueError("Unsupported traffic-light evidence type")
    if valid is None:
        raise ValueError("Traffic-light evidence does not match supplier and commodity scope")


def build_supplier_traffic_light(
    connection: sqlite3.Connection, *, supplier_id: str, commodity_id: str,
    evidence_cutoff_utc: str, categories: tuple[TrafficLightCategoryInput, ...],
    generated_at_utc: str, audit: AuditContext,
) -> TrafficLightAssessmentResult:
    for item in categories:
        _validate_evidence(
            connection, supplier_id=supplier_id, commodity_id=commodity_id, item=item
        )
    status, governing, explanation, manifest_hash = derive_traffic_light(categories)
    assessment_id = uuid7()
    with immediate_transaction(connection):
        connection.execute(
            """INSERT INTO supplier_traffic_light_assessment VALUES
               (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (assessment_id, supplier_id, commodity_id, evidence_cutoff_utc, status,
             canonical_json(governing), explanation, manifest_hash, generated_at_utc),
        )
        connection.executemany(
            """INSERT INTO supplier_traffic_light_category VALUES
               (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [(uuid7(), assessment_id, item.category_code, item.category_status,
              item.confidence_classification, int(item.material_commercial_impact),
              item.recurrence_count, item.evidence_entity_type, item.evidence_entity_id,
              item.explanation, ordinal)
             for ordinal, item in enumerate(categories)],
        )
        append_audit_event(connection, audit, {
            "supplier_traffic_light_assessment_id": assessment_id,
            "supplier_id": supplier_id, "commodity_id": commodity_id,
            "evidence_cutoff_utc": evidence_cutoff_utc,
            "overall_status": status, "governing_categories": governing,
            "category_manifest_hash": manifest_hash,
        })
    return TrafficLightAssessmentResult(assessment_id, status, governing)


def derive_supplier_overall_traffic_light(
    commodity_rows: list[sqlite3.Row],
) -> tuple[str, tuple[str, ...], str, str]:
    if not commodity_rows:
        raise ValueError("Supplier overall traffic light requires commodity assessments")
    red = tuple(str(row["commodity_id"]) for row in commodity_rows
                if row["overall_status"] == "Red")
    gray = tuple(str(row["commodity_id"]) for row in commodity_rows
                 if row["overall_status"] == "Gray")
    if red:
        status, governing = "Red", red
        explanation = (
            "One or more governing Red commodity assessments control the supplier-wide "
            "status; stronger performance in other commodities does not conceal them."
        )
    elif gray:
        status, governing = "Gray", gray
        explanation = "Unsupported commodity evidence prevents a supported supplier-wide conclusion."
    elif all(row["overall_status"] == "Green" for row in commodity_rows):
        status = "Green"
        governing = tuple(str(row["commodity_id"]) for row in commodity_rows)
        explanation = "Every included commodity assessment is supported and Green."
    else:
        status = "Yellow"
        governing = tuple(str(row["commodity_id"]) for row in commodity_rows
                          if row["overall_status"] == "Yellow")
        explanation = "No commodity is governing Red, but one or more supported commodity concerns prevent Green."
    payload = [
        (row["commodity_id"], row["supplier_traffic_light_assessment_id"],
         row["overall_status"], row["evidence_cutoff_utc"],
         row["category_manifest_hash"])
        for row in commodity_rows
    ]
    digest = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
    return status, governing, explanation, digest


def build_supplier_overall_traffic_light(
    connection: sqlite3.Connection, *, supplier_id: str,
    evidence_cutoff_utc: str, commodity_assessment_ids: tuple[str, ...],
    generated_at_utc: str, audit: AuditContext,
) -> SupplierOverallTrafficLightResult:
    if (not commodity_assessment_ids
            or len(commodity_assessment_ids) != len(set(commodity_assessment_ids))):
        raise ValueError("Distinct commodity traffic-light assessments are required")
    placeholders = ",".join("?" for _ in commodity_assessment_ids)
    rows = connection.execute(
        f"""SELECT * FROM supplier_traffic_light_assessment
            WHERE supplier_traffic_light_assessment_id IN ({placeholders})
            ORDER BY commodity_id""", commodity_assessment_ids,
    ).fetchall()
    if len(rows) != len(commodity_assessment_ids):
        raise ValueError("A selected commodity traffic-light assessment does not exist")
    if any(row["supplier_id"] != supplier_id for row in rows):
        raise ValueError("All commodity assessments must belong to the supplier")
    if len({str(row["commodity_id"]) for row in rows}) != len(rows):
        raise ValueError("Select exactly one traffic-light assessment per commodity")
    if any(str(row["evidence_cutoff_utc"]) > evidence_cutoff_utc for row in rows):
        raise ValueError("Commodity assessment cannot be newer than overall evidence cutoff")
    status, governing, explanation, manifest_hash = (
        derive_supplier_overall_traffic_light(rows)
    )
    assessment_id = uuid7()
    with immediate_transaction(connection):
        connection.execute(
            """INSERT INTO supplier_overall_traffic_light_assessment VALUES
               (?, ?, ?, ?, ?, ?, ?, ?)""",
            (assessment_id, supplier_id, evidence_cutoff_utc, status,
             canonical_json(governing), explanation, manifest_hash, generated_at_utc),
        )
        connection.executemany(
            "INSERT INTO supplier_overall_traffic_light_commodity VALUES (?, ?, ?, ?, ?, ?)",
            [(uuid7(), assessment_id, row["commodity_id"],
              row["supplier_traffic_light_assessment_id"], row["overall_status"], ordinal)
             for ordinal, row in enumerate(rows)],
        )
        append_audit_event(connection, audit, {
            "supplier_overall_traffic_light_assessment_id": assessment_id,
            "supplier_id": supplier_id, "evidence_cutoff_utc": evidence_cutoff_utc,
            "overall_status": status, "governing_commodities": governing,
            "commodity_assessment_ids": commodity_assessment_ids,
            "commodity_manifest_hash": manifest_hash,
        })
    return SupplierOverallTrafficLightResult(assessment_id, status, governing)
