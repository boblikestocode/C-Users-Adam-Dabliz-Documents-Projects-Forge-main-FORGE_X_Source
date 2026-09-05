from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass
from decimal import Decimal

from .audit import AuditContext, append_audit_event, canonical_json
from .connection import immediate_transaction
from .decimals import ExactDecimal
from .ids import uuid7


FORMULA_POPULATION_VERSION = "Economic Age and Cutoff v1"


@dataclass(frozen=True)
class ProfileBuildResult:
    profile_run_id: str
    activity_count: int
    independent_event_count: int
    metric_count: int
    finding_count: int


@dataclass(frozen=True)
class FamilyProfileBuildResult:
    family_profile_run_id: str
    contributing_profile_run_ids: tuple[str, ...]
    activity_count: int
    independent_event_count: int
    metric_count: int
    finding_count: int


def _metric_decimal(numerator: int, denominator: int) -> ExactDecimal:
    if denominator == 0:
        return ExactDecimal.parse("0")
    ratio = Decimal(numerator) / Decimal(denominator)
    return ExactDecimal.parse(format(ratio.normalize(), "f"))


def profile_activity_rows(
    connection: sqlite3.Connection,
    *,
    supplier_id: str,
    supplier_plant_id: str | None,
    commodity_id: str,
    evidence_cutoff_utc: str,
    active_window_start_utc: str,
) -> list[sqlite3.Row]:
    return connection.execute(
        """SELECT activity.supplier_activity_id, activity.activity_type,
                  activity.event_id, activity.quote_round_id,
                  activity.event_part_id, activity.occurred_at_utc,
                  activity.recorded_at_utc
           FROM supplier_activity activity
           LEFT JOIN sourcing_event event ON event.event_id = activity.event_id
           WHERE activity.supplier_id = ?
             AND (? IS NULL OR activity.supplier_plant_id = ?)
             AND event.commodity_id = ?
             AND COALESCE(activity.occurred_at_utc, activity.recorded_at_utc) >= ?
             AND activity.recorded_at_utc <= ?
             AND NOT EXISTS (
                 SELECT 1 FROM observation_eligibility eligibility
                 WHERE eligibility.analytical_role = 'Economic Age'
                   AND eligibility.eligibility_status = 'Historical Context Only'
                   AND eligibility.recorded_at_utc <= ?
                   AND eligibility.observation_id IN (
                       SELECT activity.before_entity_id
                       WHERE activity.before_entity_type = 'PBD Observation'
                       UNION ALL
                       SELECT activity.after_entity_id
                       WHERE activity.after_entity_type = 'PBD Observation'
                       UNION ALL
                       SELECT datum.observation_id FROM submitted_datum datum
                       WHERE (activity.before_entity_type = 'Submitted Datum'
                              AND datum.submitted_datum_id = activity.before_entity_id)
                          OR (activity.after_entity_type = 'Submitted Datum'
                              AND datum.submitted_datum_id = activity.after_entity_id)
                       UNION ALL
                       SELECT membership.observation_id FROM round_observation membership
                       WHERE (activity.before_entity_type = 'Round Observation'
                              AND membership.round_observation_id = activity.before_entity_id)
                          OR (activity.after_entity_type = 'Round Observation'
                              AND membership.round_observation_id = activity.after_entity_id)
                   )
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
           ORDER BY COALESCE(activity.occurred_at_utc, activity.recorded_at_utc),
                    activity.supplier_activity_id""",
        (
            supplier_id, supplier_plant_id, supplier_plant_id,
            commodity_id, active_window_start_utc, evidence_cutoff_utc,
            evidence_cutoff_utc, evidence_cutoff_utc,
        ),
    ).fetchall()


def profile_formula_exception_rows(
    connection: sqlite3.Connection, *, supplier_id: str,
    supplier_plant_id: str | None, commodity_id: str,
    evidence_cutoff_utc: str, active_window_start_utc: str,
    population_version: str = FORMULA_POPULATION_VERSION,
) -> list[sqlite3.Row]:
    if population_version not in ("Legacy", FORMULA_POPULATION_VERSION):
        raise ValueError("Unsupported profile formula population version")
    eligibility_filter = ""
    parameters = [supplier_id, supplier_plant_id, supplier_plant_id, commodity_id,
                  active_window_start_utc, evidence_cutoff_utc]
    if population_version == FORMULA_POPULATION_VERSION:
        eligibility_filter = """
             AND observation.recorded_at_utc <= ?
             AND membership.recorded_at_utc <= ?
             AND round.recorded_at_utc <= ?
             AND event.created_at_utc <= ?
             AND NOT EXISTS (
                 SELECT 1 FROM observation_eligibility eligibility
                 WHERE eligibility.observation_id = observation.observation_id
                   AND eligibility.analytical_role = 'Economic Age'
                   AND eligibility.eligibility_status = 'Historical Context Only'
                   AND eligibility.recorded_at_utc <= ?
                   AND NOT EXISTS (
                       SELECT 1 FROM observation_eligibility newer
                       WHERE newer.observation_id = eligibility.observation_id
                         AND newer.analytical_role = eligibility.analytical_role
                         AND newer.recorded_at_utc <= ?
                         AND (newer.recorded_at_utc > eligibility.recorded_at_utc OR
                              (newer.recorded_at_utc = eligibility.recorded_at_utc AND
                               newer.eligibility_id > eligibility.eligibility_id))
                   )
             )"""
        parameters.extend([evidence_cutoff_utc] * 6)
    return connection.execute(
        f"""SELECT DISTINCT integrity.formula_integrity_event_id,
                  integrity.observation_id, round.event_id,
                  integrity.recorded_at_utc
           FROM formula_integrity_event integrity
           JOIN pbd_observation observation
             ON observation.observation_id = integrity.observation_id
           JOIN round_observation membership
             ON membership.observation_id = observation.observation_id
           JOIN supplier_quote_round round
             ON round.quote_round_id = membership.quote_round_id
           JOIN sourcing_event event ON event.event_id = round.event_id
           WHERE integrity.classification = 'Formula Reconciliation Exception'
             AND observation.supplier_id = ?
             AND (? IS NULL OR observation.supplier_plant_id = ?)
             AND event.commodity_id = ?
             AND integrity.recorded_at_utc >= ?
             AND integrity.recorded_at_utc <= ?
             {eligibility_filter}
           ORDER BY integrity.recorded_at_utc,
                    integrity.formula_integrity_event_id""",
        parameters,
    ).fetchall()


def derive_profile_outputs(
    activities: list[sqlite3.Row],
    formula_exceptions: list[sqlite3.Row] | None = None,
) -> tuple[
    list[tuple[str, ExactDecimal, int, int, str]],
    list[tuple[str, str, dict, int, int]],
    str,
    str,
    str,
]:
    formula_exceptions = formula_exceptions or []
    counts = Counter(str(row["activity_type"]) for row in activities)
    event_types: dict[str, set[str]] = defaultdict(set)
    for row in activities:
        if row["event_id"] is not None:
            event_types[str(row["event_id"])].add(str(row["activity_type"]))
    independent_events = len(event_types)
    metrics: list[tuple[str, ExactDecimal, int, int, str]] = [
        ("ACTIVITY_COUNT", ExactDecimal.parse(str(len(activities))), len(activities), independent_events, "Observed"),
        ("INDEPENDENT_EVENT_COUNT", ExactDecimal.parse(str(independent_events)), len(activities), independent_events, "Observed"),
    ]
    for activity_type in sorted(counts):
        code = activity_type.upper().replace(" ", "_")
        events_with_type = sum(activity_type in types for types in event_types.values())
        metrics.append((f"{code}_COUNT", ExactDecimal.parse(str(counts[activity_type])), counts[activity_type], events_with_type, "Observed"))
        metrics.append((f"{code}_EVENT_RATE", _metric_decimal(events_with_type, independent_events), counts[activity_type], events_with_type, "Descriptive"))
    findings: list[tuple[str, str, dict, int, int]] = []
    for activity_type in sorted(counts):
        events_with_type = sum(activity_type in types for types in event_types.values())
        findings.append((
            "Observed Supplier Activity", "Descriptive Only",
            {"activity_type": activity_type, "activity_count": counts[activity_type],
             "independent_event_count": events_with_type,
             "statement": f"Observed {activity_type.lower()} activity; no traffic-light conclusion assigned."},
            counts[activity_type], events_with_type,
        ))
    exception_observations = {
        str(row["observation_id"]) for row in formula_exceptions
    }
    exception_events = {
        str(row["event_id"]) for row in formula_exceptions
        if row["event_id"] is not None
    }
    if len(exception_observations) >= 2:
        findings.append((
            "Repeated Formula Reconciliation Exceptions", "Red",
            {"formula_exception_count": len(formula_exceptions),
             "affected_pbd_count": len(exception_observations),
             "independent_event_count": len(exception_events),
             "statement": "Confirmed formula reconciliation exceptions recur across multiple PBD observations for this supplier and commodity."},
            len(formula_exceptions), len(exception_events),
        ))
    evidence_payload = [
        {"supplier_activity_id": row["supplier_activity_id"],
         "activity_type": row["activity_type"], "event_id": row["event_id"],
         "recorded_at_utc": row["recorded_at_utc"]}
        for row in activities
    ] + [
        {"formula_integrity_event_id": row["formula_integrity_event_id"],
         "observation_id": row["observation_id"], "event_id": row["event_id"],
         "recorded_at_utc": row["recorded_at_utc"]}
        for row in formula_exceptions
    ]
    metric_payload = [
        (code, value.coefficient, value.scale, evidence_count, event_count, confidence)
        for code, value, evidence_count, event_count, confidence in metrics
    ]
    finding_payload = [
        (kind, status, canonical_json(payload), evidence_count, event_count)
        for kind, status, payload, evidence_count, event_count in findings
    ]
    digest = lambda payload: hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
    return metrics, findings, digest(evidence_payload), digest(metric_payload), digest(finding_payload)


def build_supplier_profile(
    connection: sqlite3.Connection,
    *,
    supplier_id: str,
    supplier_plant_id: str | None,
    commodity_id: str,
    region_code: str | None,
    evidence_cutoff_utc: str,
    active_window_start_utc: str,
    eligibility_rule_version_id: str,
    calculation_rule_version_id: str,
    engine_version_id: str,
    started_at_utc: str,
    completed_at_utc: str,
    audit: AuditContext,
) -> ProfileBuildResult:
    activities = profile_activity_rows(
        connection, supplier_id=supplier_id,
        supplier_plant_id=supplier_plant_id, commodity_id=commodity_id,
        evidence_cutoff_utc=evidence_cutoff_utc,
        active_window_start_utc=active_window_start_utc,
    )
    formula_exceptions = profile_formula_exception_rows(
        connection, supplier_id=supplier_id, supplier_plant_id=supplier_plant_id,
        commodity_id=commodity_id, evidence_cutoff_utc=evidence_cutoff_utc,
        active_window_start_utc=active_window_start_utc,
    )
    metrics, findings, manifest_hash, metric_hash, finding_hash = derive_profile_outputs(
        activities, formula_exceptions
    )
    independent_events = len({str(row["event_id"]) for row in activities if row["event_id"] is not None})
    profile_run_id = uuid7()
    with immediate_transaction(connection):
        connection.execute(
            """INSERT INTO supplier_profile_run
               (supplier_profile_run_id, supplier_id, supplier_plant_id,
                commodity_id, region_code, evidence_cutoff_utc,
                eligibility_rule_version_id, calculation_rule_version_id,
                engine_version_id, build_manifest_hash, run_status,
                started_at_utc, completed_at_utc)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'Complete', ?, ?)""",
            (
                profile_run_id, supplier_id, supplier_plant_id,
                commodity_id, region_code, evidence_cutoff_utc,
                eligibility_rule_version_id, calculation_rule_version_id,
                engine_version_id, manifest_hash, started_at_utc,
                completed_at_utc,
            ),
        )
        connection.execute(
            """INSERT INTO supplier_profile_reproduction_manifest
               (supplier_profile_reproduction_manifest_id,
                supplier_profile_run_id, active_window_start_utc,
                metric_manifest_hash, finding_manifest_hash, recorded_at_utc,
                formula_population_version)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (uuid7(), profile_run_id, active_window_start_utc,
             metric_hash, finding_hash, completed_at_utc, FORMULA_POPULATION_VERSION),
        )
        for code, value, evidence_count, event_count, confidence in metrics:
            connection.execute(
                """INSERT INTO supplier_profile_metric
                   (supplier_profile_metric_id, supplier_profile_run_id,
                    metric_code, decimal_coefficient, decimal_scale,
                    evidence_count, independent_event_count,
                    confidence_classification)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    uuid7(), profile_run_id, code, value.coefficient,
                    value.scale, evidence_count, event_count, confidence,
                ),
            )
        for finding_type, status, payload, evidence_count, event_count in findings:
            connection.execute(
                """INSERT INTO supplier_profile_finding
                   (supplier_profile_finding_id, supplier_profile_run_id,
                    finding_type, finding_status, explanation_payload,
                    evidence_count, independent_event_count)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    uuid7(), profile_run_id, finding_type, status,
                    canonical_json(payload), evidence_count, event_count,
                ),
            )
        for row in activities:
            connection.execute(
                """INSERT INTO profile_evidence_entry
                   (profile_evidence_entry_id, supplier_profile_run_id,
                    evidence_entity_type, evidence_entity_id, evidence_role,
                    included_flag)
                   VALUES (?, ?, 'Supplier Activity', ?, 'Profile Activity', 1)""",
                (uuid7(), profile_run_id, row["supplier_activity_id"]),
            )
        for row in formula_exceptions:
            connection.execute(
                """INSERT INTO profile_evidence_entry
                   (profile_evidence_entry_id, supplier_profile_run_id,
                    evidence_entity_type, evidence_entity_id, evidence_role,
                    included_flag)
                   VALUES (?, ?, 'Formula Integrity Event', ?,
                           'Repeated Formula Exception', 1)""",
                (uuid7(), profile_run_id, row["formula_integrity_event_id"]),
            )
        append_audit_event(
            connection, audit,
            {
                "supplier_profile_run_id": profile_run_id,
                "supplier_id": supplier_id,
                "supplier_plant_id": supplier_plant_id,
                "commodity_id": commodity_id,
                "region_code": region_code,
                "evidence_cutoff_utc": evidence_cutoff_utc,
                "active_window_start_utc": active_window_start_utc,
                "activity_count": len(activities),
                "independent_event_count": independent_events,
                "build_manifest_hash": manifest_hash,
                "formula_population_version": FORMULA_POPULATION_VERSION,
            },
        )
    return ProfileBuildResult(
        profile_run_id=profile_run_id,
        activity_count=len(activities),
        independent_event_count=independent_events,
        metric_count=len(metrics),
        finding_count=len(findings),
    )


def build_supplier_family_profile(
    connection: sqlite3.Connection, *, stable_family_id: str,
    registry_cache_generation_id: str, commodity_id: str,
    evidence_cutoff_utc: str, active_window_start_utc: str,
    eligibility_rule_version_id: str, calculation_rule_version_id: str,
    engine_version_id: str, started_at_utc: str, completed_at_utc: str,
    audit: AuditContext,
) -> FamilyProfileBuildResult:
    cache = connection.execute(
        """SELECT entity.entity_payload, generation.signature_status
           FROM registry_entity_cache entity JOIN registry_cache_generation generation
             ON generation.cache_generation_id = entity.cache_generation_id
           WHERE entity.cache_generation_id = ? AND entity.entity_type = 'Supplier Family'
             AND entity.entity_id = ?""",
        (registry_cache_generation_id, stable_family_id),
    ).fetchone()
    if cache is None or cache[1] != "Verified":
        raise ValueError("Supplier family roll-up requires a signature-verified registry entity")
    payload = json.loads(str(cache[0]))
    family_name = str(payload.get("family_name", "")).strip()
    members = tuple(str(item) for item in payload.get("member_supplier_ids", ()))
    if not family_name or not members or len(members) != len(set(members)):
        raise ValueError("Supplier family registry entity requires distinct active members")
    contributing: list[tuple[str, str]] = []
    for supplier_id in sorted(members):
        profile = connection.execute(
            """SELECT run.supplier_profile_run_id
               FROM supplier_profile_run run
               JOIN supplier_profile_reproduction_manifest reproduction
                 ON reproduction.supplier_profile_run_id = run.supplier_profile_run_id
               WHERE run.supplier_id = ? AND run.supplier_plant_id IS NULL
                 AND run.commodity_id = ? AND run.region_code IS NULL
                 AND run.evidence_cutoff_utc = ?
                 AND reproduction.active_window_start_utc = ?
                 AND run.eligibility_rule_version_id = ?
                 AND run.calculation_rule_version_id = ?
                 AND run.engine_version_id = ? AND run.run_status = 'Complete'
               ORDER BY run.completed_at_utc DESC, run.supplier_profile_run_id DESC LIMIT 1""",
            (supplier_id, commodity_id, evidence_cutoff_utc, active_window_start_utc,
             eligibility_rule_version_id, calculation_rule_version_id, engine_version_id),
        ).fetchone()
        if profile is None:
            raise ValueError(f"Supplier family member {supplier_id} lacks a matching scoped profile")
        contributing.append((supplier_id, str(profile[0])))
    placeholders = ",".join("?" for _ in contributing)
    profile_ids = tuple(item[1] for item in contributing)
    activities = connection.execute(
        f"""SELECT DISTINCT activity.supplier_activity_id, activity.activity_type,
                   activity.event_id, activity.quote_round_id, activity.event_part_id,
                   activity.occurred_at_utc, activity.recorded_at_utc
            FROM profile_evidence_entry evidence JOIN supplier_activity activity
              ON evidence.evidence_entity_type = 'Supplier Activity'
             AND evidence.evidence_entity_id = activity.supplier_activity_id
            WHERE evidence.supplier_profile_run_id IN ({placeholders})
              AND evidence.included_flag = 1
            ORDER BY activity.recorded_at_utc, activity.supplier_activity_id""",
        profile_ids,
    ).fetchall()
    formula_exceptions = connection.execute(
        f"""SELECT DISTINCT integrity.formula_integrity_event_id,
                   integrity.observation_id, round.event_id,
                   integrity.recorded_at_utc
            FROM profile_evidence_entry evidence
            JOIN formula_integrity_event integrity
              ON evidence.evidence_entity_type = 'Formula Integrity Event'
             AND evidence.evidence_entity_id = integrity.formula_integrity_event_id
            JOIN round_observation membership
              ON membership.observation_id = integrity.observation_id
            JOIN supplier_quote_round round
              ON round.quote_round_id = membership.quote_round_id
            WHERE evidence.supplier_profile_run_id IN ({placeholders})
              AND evidence.included_flag = 1
            ORDER BY integrity.recorded_at_utc,
                     integrity.formula_integrity_event_id""",
        profile_ids,
    ).fetchall()
    metrics, findings, evidence_hash, metric_hash, finding_hash = derive_profile_outputs(
        activities, formula_exceptions
    )
    manifest_payload = {
        "stable_family_id": stable_family_id,
        "registry_cache_generation_id": registry_cache_generation_id,
        "contributing_profiles": contributing,
        "activity_ids": [row["supplier_activity_id"] for row in activities],
        "formula_integrity_event_ids": [row["formula_integrity_event_id"]
                                        for row in formula_exceptions],
        "evidence_manifest_hash": evidence_hash,
    }
    manifest_hash = hashlib.sha256(canonical_json(manifest_payload).encode("utf-8")).hexdigest()
    run_id = uuid7()
    with immediate_transaction(connection):
        connection.execute(
            """INSERT INTO supplier_family_profile_run VALUES
               (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'Complete', ?, ?)""",
            (run_id, stable_family_id, family_name, registry_cache_generation_id,
             commodity_id, evidence_cutoff_utc, active_window_start_utc,
             eligibility_rule_version_id, calculation_rule_version_id,
             engine_version_id, manifest_hash, metric_hash, finding_hash,
             started_at_utc, completed_at_utc),
        )
        connection.executemany(
            "INSERT INTO supplier_family_profile_member VALUES (?, ?, ?, ?)",
            [(uuid7(), run_id, supplier_id, profile_id)
             for supplier_id, profile_id in contributing],
        )
        connection.executemany(
            "INSERT INTO supplier_family_profile_metric VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [(uuid7(), run_id, code, value.coefficient, value.scale,
              evidence_count, event_count, confidence)
             for code, value, evidence_count, event_count, confidence in metrics],
        )
        connection.executemany(
            "INSERT INTO supplier_family_profile_finding VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(uuid7(), run_id, kind, status, canonical_json(payload_row),
              evidence_count, event_count)
             for kind, status, payload_row, evidence_count, event_count in findings],
        )
        append_audit_event(connection, audit, {
            "supplier_family_profile_run_id": run_id,
            "stable_family_id": stable_family_id,
            "registry_cache_generation_id": registry_cache_generation_id,
            "contributing_profile_run_ids": profile_ids,
            "build_manifest_hash": manifest_hash,
        })
    return FamilyProfileBuildResult(run_id, profile_ids, len(activities),
                                    len({str(row["event_id"]) for row in activities
                                         if row["event_id"] is not None}),
                                    len(metrics), len(findings))
