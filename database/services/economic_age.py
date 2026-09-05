from __future__ import annotations

import calendar
import sqlite3
from dataclasses import dataclass
from datetime import date

from .audit import AuditContext, append_audit_event
from .connection import immediate_transaction
from .ids import uuid7


@dataclass(frozen=True)
class EconomicAgeResult:
    observation_id: str
    economic_date: str | None
    date_precision: str
    cutoff_date: str
    eligibility_status: str
    reason_code: str


def _parse_supported_date(value: str, precision: str) -> tuple[date, date]:
    try:
        if precision == "Day":
            parsed = date.fromisoformat(value)
            return parsed, parsed
        if precision == "Month":
            year, month = map(int, value[:7].split("-"))
            return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])
        if precision == "Year":
            year = int(value[:4])
            return date(year, 1, 1), date(year, 12, 31)
    except (TypeError, ValueError) as error:
        raise ValueError(f"Invalid {precision} economic date: {value!r}") from error
    raise ValueError("Economic date precision must be Day, Month, or Year")


def _three_year_cutoff(as_of: date) -> date:
    try:
        return as_of.replace(year=as_of.year - 3)
    except ValueError:
        return as_of.replace(year=as_of.year - 3, day=28)


def confirm_economic_dates(
    connection: sqlite3.Connection,
    *,
    observation_ids: tuple[str, ...],
    confirmed_economic_date: str,
    date_precision: str,
    confirmed_by_user_id: str,
    confirmation_reason: str,
    recorded_at_utc: str,
    audit: AuditContext,
) -> tuple[str, ...]:
    if not 1 <= len(observation_ids) <= 20 or len(set(observation_ids)) != len(observation_ids):
        raise ValueError("Date confirmation requires 1–20 distinct visible observations")
    if not confirmation_reason.strip():
        raise ValueError("Economic-date confirmation requires a reason")
    _parse_supported_date(confirmed_economic_date, date_precision)
    placeholders = ",".join("?" for _ in observation_ids)
    batch_id = uuid7() if len(observation_ids) > 1 else None
    confirmation_ids: list[str] = []
    with immediate_transaction(connection):
        rows = connection.execute(
            f"""SELECT observation.observation_id, observation.supplier_id,
                       import_tx.import_session_id
                FROM v_effective_pbd_observation observation
                JOIN source_occurrence occurrence ON occurrence.occurrence_id = observation.occurrence_id
                JOIN source_worksheet worksheet ON worksheet.worksheet_id = occurrence.worksheet_id
                JOIN source_workbook workbook ON workbook.workbook_id = worksheet.workbook_id
                JOIN import_transaction import_tx
                  ON import_tx.import_transaction_id = workbook.import_transaction_id
                WHERE observation.observation_id IN ({placeholders})""",
            observation_ids,
        ).fetchall()
        if len(rows) != len(observation_ids):
            raise ValueError("Every selected observation must exist")
        if len(rows) > 1:
            suppliers = {row[1] for row in rows}
            sessions = {row[2] for row in rows}
            if None in suppliers or len(suppliers) != 1 or len(sessions) != 1:
                raise ValueError("Batch date confirmation requires one confirmed supplier and one import batch")
        for row in rows:
            current = connection.execute(
                """SELECT economic_date_confirmation_id
                   FROM v_current_economic_date_confirmation WHERE observation_id = ?""",
                (row[0],),
            ).fetchone()
            confirmation_id = uuid7()
            connection.execute(
                """INSERT INTO economic_date_confirmation
                   (economic_date_confirmation_id, observation_id, confirmed_economic_date,
                    date_precision, assignment_method, assignment_batch_id,
                    confirmed_by_user_id, confirmation_reason, recorded_at_utc,
                    supersedes_confirmation_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (confirmation_id, row[0], confirmed_economic_date, date_precision,
                 "Batch" if batch_id else "Individual", batch_id, confirmed_by_user_id,
                 confirmation_reason, recorded_at_utc, None if current is None else current[0]),
            )
            confirmation_ids.append(confirmation_id)
        append_audit_event(connection, audit, {
            "observation_ids": sorted(observation_ids),
            "confirmed_economic_date": confirmed_economic_date,
            "date_precision": date_precision,
            "assignment_method": "Batch" if batch_id else "Individual",
            "assignment_batch_id": batch_id,
            "confirmation_reason": confirmation_reason,
        })
    return tuple(confirmation_ids)


def classify_economic_age(
    connection: sqlite3.Connection,
    *,
    observation_ids: tuple[str, ...],
    as_of_date: date,
    eligibility_rule_version_id: str,
    confirmed_by_user_id: str,
    recorded_at_utc: str,
    audit: AuditContext,
) -> tuple[EconomicAgeResult, ...]:
    if not observation_ids:
        return ()
    cutoff = _three_year_cutoff(as_of_date)
    placeholders = ",".join("?" for _ in observation_ids)
    results: list[EconomicAgeResult] = []
    with immediate_transaction(connection):
        if connection.execute(
            "SELECT 1 FROM rule_version WHERE rule_version_id = ?", (eligibility_rule_version_id,)
        ).fetchone() is None:
            raise ValueError("Eligibility rule version does not exist")
        rows = connection.execute(
            f"""SELECT observation.observation_id,
                       COALESCE(confirmation.confirmed_economic_date, observation.economic_date),
                       COALESCE(confirmation.date_precision, observation.economic_date_precision, 'Unknown')
                FROM v_effective_pbd_observation observation
                LEFT JOIN v_current_economic_date_confirmation confirmation
                  ON confirmation.observation_id = observation.observation_id
                WHERE observation.observation_id IN ({placeholders})""",
            observation_ids,
        ).fetchall()
        if len(rows) != len(set(observation_ids)):
            raise ValueError("Every observation selected for aging must exist")
        for observation_id, value, precision in rows:
            if value is None or precision == "Unknown":
                status, reason = "Historical Context Only", "MISSING_SUPPORTED_ECONOMIC_DATE"
            else:
                earliest, latest = _parse_supported_date(str(value), str(precision))
                if precision == "Year" and earliest < cutoff <= latest:
                    status, reason = "Historical Context Only", "YEAR_ONLY_OVERLAPS_CUTOFF"
                elif earliest >= cutoff:
                    status, reason = "Eligible", "WITHIN_ROLLING_THREE_YEAR_WINDOW"
                else:
                    status, reason = "Historical Context Only", "OLDER_THAN_THREE_YEARS"
            current = connection.execute(
                """SELECT eligibility_id FROM v_latest_observation_eligibility
                   WHERE observation_id = ? AND analytical_role = 'Economic Age'""",
                (observation_id,),
            ).fetchone()
            connection.execute(
                """INSERT INTO observation_eligibility
                   (eligibility_id, observation_id, analytical_role, eligibility_status,
                    reason_code, confirmed_by_user_id, supersedes_eligibility_id,
                    recorded_at_utc, eligibility_rule_version_id)
                   VALUES (?, ?, 'Economic Age', ?, ?, ?, ?, ?, ?)""",
                (uuid7(), observation_id, status, reason, confirmed_by_user_id,
                 None if current is None else current[0], recorded_at_utc,
                 eligibility_rule_version_id),
            )
            results.append(EconomicAgeResult(
                str(observation_id), None if value is None else str(value), str(precision),
                cutoff.isoformat(), status, reason,
            ))
        append_audit_event(connection, audit, {
            "as_of_date": as_of_date.isoformat(), "cutoff_date": cutoff.isoformat(),
            "eligibility_rule_version_id": eligibility_rule_version_id,
            "results": [result.__dict__ for result in results],
        })
    return tuple(sorted(results, key=lambda item: item.observation_id))
