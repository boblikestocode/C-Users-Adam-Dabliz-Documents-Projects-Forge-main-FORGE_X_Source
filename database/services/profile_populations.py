"""Cutoff-specific source facts for regional and economic-age populations."""
from __future__ import annotations

import sqlite3
from datetime import date

from .economic_age import EconomicAgeResult, _parse_supported_date, _three_year_cutoff


SCOPE_POPULATION_VERSION = "Regional Age v1"


def economic_age_at_cutoff(
    connection: sqlite3.Connection, observation_id: str, cutoff_utc: str,
) -> EconomicAgeResult:
    """Evaluate the date known at the cutoff without changing any prior ledger."""
    as_of = date.fromisoformat(cutoff_utc[:10])
    cutoff = _three_year_cutoff(as_of)
    observation = connection.execute(
        """SELECT economic_date, economic_date_precision FROM pbd_observation
           WHERE observation_id = ? AND recorded_at_utc <= ?""",
        (observation_id, cutoff_utc),
    ).fetchone()
    confirmation = connection.execute(
        """SELECT confirmed_economic_date, date_precision
           FROM economic_date_confirmation
           WHERE observation_id = ? AND recorded_at_utc <= ?
           ORDER BY recorded_at_utc DESC, economic_date_confirmation_id DESC LIMIT 1""",
        (observation_id, cutoff_utc),
    ).fetchone()
    value, precision = (confirmation or observation or (None, "Unknown"))
    status = "Historical Context Only"
    reason = "MISSING_SUPPORTED_ECONOMIC_DATE"
    if observation is None:
        reason = "SOURCE_NOT_AVAILABLE_AT_CUTOFF"
    elif value is not None and precision not in (None, "Unknown"):
        earliest, latest = _parse_supported_date(str(value), str(precision))
        if earliest > as_of:
            reason = "FUTURE_ECONOMIC_DATE"
        elif precision == "Year" and earliest < cutoff <= latest:
            reason = "YEAR_ONLY_OVERLAPS_CUTOFF"
        elif earliest >= cutoff:
            status, reason = "Eligible", "WITHIN_ROLLING_THREE_YEAR_WINDOW"
        else:
            reason = "OLDER_THAN_THREE_YEARS"
    return EconomicAgeResult(observation_id, value, precision or "Unknown",
                             cutoff.isoformat(), status, reason)


def observation_region_at_cutoff(
    connection: sqlite3.Connection, observation_id: str, cutoff_utc: str,
) -> str | None:
    """A region requires one unambiguous retained assignment; never infer a plant."""
    regions = {str(row[0]).strip().upper() for row in connection.execute(
        """SELECT region_code FROM quote_version_candidate
           WHERE observation_id = ? AND detected_at_utc <= ?
           UNION
           SELECT region_code FROM location_measure_evidence
           WHERE observation_id = ? AND recorded_at_utc <= ?
             AND evidence_status = 'Valid'""",
        (observation_id, cutoff_utc, observation_id, cutoff_utc),
    )}
    if connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'supplier_rate_interpretation'"
    ).fetchone():
        regions.update(str(row[0]) for row in connection.execute(
            """SELECT interpretation.region_code FROM supplier_rate_interpretation interpretation
               JOIN submitted_datum datum USING (submitted_datum_id)
               WHERE datum.observation_id = ? AND datum.recorded_at_utc <= ?
                 AND interpretation.recorded_at_utc <= ? AND NOT EXISTS (
                     SELECT 1 FROM supplier_rate_interpretation newer
                     WHERE newer.supersedes_interpretation_id = interpretation.interpretation_id
                       AND newer.recorded_at_utc <= ?)""",
            (observation_id, cutoff_utc, cutoff_utc, cutoff_utc),
        ))
    return next(iter(regions)) if len(regions) == 1 else None


def activity_observations(
    connection: sqlite3.Connection, activity_id: str, cutoff_utc: str,
) -> tuple[str, ...]:
    rows = connection.execute(
        """WITH refs(kind, id) AS (
               SELECT before_entity_type, before_entity_id FROM supplier_activity
               WHERE supplier_activity_id = ?
               UNION SELECT after_entity_type, after_entity_id FROM supplier_activity
               WHERE supplier_activity_id = ?
           ), observations(id) AS (
               SELECT id FROM refs WHERE kind = 'PBD Observation'
               UNION SELECT datum.observation_id FROM refs JOIN submitted_datum datum
                 ON refs.kind = 'Submitted Datum' AND refs.id = datum.submitted_datum_id
                 AND datum.recorded_at_utc <= ?
               UNION SELECT member.observation_id FROM refs JOIN round_observation member
                 ON refs.kind = 'Round Observation' AND refs.id = member.round_observation_id
                 AND member.recorded_at_utc <= ?
           ) SELECT id FROM observations ORDER BY id""",
        (activity_id, activity_id, cutoff_utc, cutoff_utc),
    ).fetchall()
    return tuple(str(row[0]) for row in rows)


def scoped_activity_rows(connection, rows, region_code, cutoff_utc):
    result = []
    for row in rows:
        if (row["occurred_at_utc"] or row["recorded_at_utc"]) > cutoff_utc:
            continue
        event = connection.execute(
            "SELECT created_at_utc FROM sourcing_event WHERE event_id = ?",
            (row["event_id"],),
        ).fetchone()
        if event is None or event[0] > cutoff_utc:
            continue
        observations = activity_observations(connection, row["supplier_activity_id"], cutoff_utc)
        if any(economic_age_at_cutoff(connection, item, cutoff_utc).eligibility_status != "Eligible"
               for item in observations):
            continue
        if region_code is not None and (not observations or any(
            observation_region_at_cutoff(connection, item, cutoff_utc) != region_code
            for item in observations
        )):
            continue
        result.append(row)
    return result


def scoped_formula_rows(connection, rows, region_code, cutoff_utc):
    return [row for row in rows
            if economic_age_at_cutoff(connection, row["observation_id"], cutoff_utc).eligibility_status == "Eligible"
            and (region_code is None or observation_region_at_cutoff(
                connection, row["observation_id"], cutoff_utc) == region_code)]
