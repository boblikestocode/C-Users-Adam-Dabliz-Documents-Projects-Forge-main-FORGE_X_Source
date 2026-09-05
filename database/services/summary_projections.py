from __future__ import annotations

import hashlib
import sqlite3

from .audit import canonical_json
from .connection import immediate_transaction
from .ids import uuid7


def event_summary_source_rows(
    connection: sqlite3.Connection, evidence_cutoff_utc: str
) -> list[tuple[object, ...]]:
    """Rebuild event summaries solely from authoritative rows visible at cutoff."""
    events = connection.execute(
        """SELECT event.event_id, event.readable_name, event.buyer_code_id,
                  (SELECT status.event_status FROM sourcing_event_status_event status
                   WHERE status.event_id = event.event_id AND status.recorded_at_utc <= ?
                   ORDER BY status.recorded_at_utc DESC,
                            status.sourcing_event_status_event_id DESC LIMIT 1),
                  event.created_at_utc
           FROM sourcing_event event
           WHERE event.created_at_utc <= ? ORDER BY event.event_id""",
        (evidence_cutoff_utc, evidence_cutoff_utc),
    ).fetchall()
    rows: list[tuple[object, ...]] = []
    for event in events:
        event_id = str(event[0])
        package = connection.execute(
            """SELECT COALESCE(
                       (SELECT correction.corrected_displayed_package_number
                        FROM source_package_number_correction correction
                        WHERE correction.source_package_id = package.source_package_id
                          AND correction.recorded_at_utc <= ?
                        ORDER BY correction.recorded_at_utc DESC,
                                 correction.source_package_number_correction_id DESC LIMIT 1),
                       package.displayed_package_number), package.recorded_at_utc
               FROM source_package package
               WHERE package.event_id = ? AND package.recorded_at_utc <= ?
               ORDER BY CASE package_role WHEN 'Primary' THEN 0 WHEN 'Replacement' THEN 1
                            WHEN 'Revised' THEN 2 ELSE 3 END,
                        recorded_at_utc DESC, source_package_id DESC LIMIT 1""",
            (evidence_cutoff_utc, event_id, evidence_cutoff_utc),
        ).fetchone()
        if package is None:
            raise ValueError(f"Sourcing event {event_id} has no source package at cutoff")
        supplier_count = int(connection.execute(
            """SELECT COUNT(DISTINCT supplier_id) FROM supplier_quote_round
               WHERE event_id = ? AND recorded_at_utc <= ?""",
            (event_id, evidence_cutoff_utc),
        ).fetchone()[0])
        open_action_count = int(connection.execute(
            """SELECT COUNT(*) FROM buyer_action action
               WHERE action.event_id = ? AND action.created_at_utc <= ?
                 AND (SELECT version.action_status FROM buyer_action_version version
                      WHERE version.buyer_action_id = action.buyer_action_id
                        AND version.recorded_at_utc <= ?
                      ORDER BY version.recorded_at_utc DESC,
                               version.buyer_action_version_id DESC LIMIT 1)
                     NOT IN ('Resolved', 'Accepted Exception')""",
            (event_id, evidence_cutoff_utc, evidence_cutoff_utc),
        ).fetchone()[0])
        latest_activity = connection.execute(
            """SELECT MAX(activity_at) FROM (
                   SELECT created_at_utc AS activity_at FROM sourcing_event WHERE event_id = ?
                   UNION ALL SELECT recorded_at_utc FROM source_package
                     WHERE event_id = ? AND recorded_at_utc <= ?
                   UNION ALL SELECT recorded_at_utc FROM supplier_quote_round
                     WHERE event_id = ? AND recorded_at_utc <= ?
                   UNION ALL SELECT created_at_utc FROM buyer_action
                     WHERE event_id = ? AND created_at_utc <= ?
                   UNION ALL SELECT version.recorded_at_utc
                     FROM buyer_action_version version JOIN buyer_action action
                       ON action.buyer_action_id = version.buyer_action_id
                     WHERE action.event_id = ? AND version.recorded_at_utc <= ?
               )""",
            (event_id, event_id, evidence_cutoff_utc, event_id, evidence_cutoff_utc,
             event_id, evidence_cutoff_utc, event_id, evidence_cutoff_utc),
        ).fetchone()[0]
        rows.append((event_id, str(package[0]), str(event[1]), str(event[2]),
                     str(event[3]), supplier_count, open_action_count, latest_activity))
    return rows


def build_event_summary_projection(
    connection: sqlite3.Connection, *, evidence_cutoff_utc: str
) -> str:
    generation_id = uuid7()
    source_rows = event_summary_source_rows(connection, evidence_cutoff_utc)
    manifest_hash = hashlib.sha256(
        canonical_json([tuple(row) for row in source_rows]).encode("utf-8")
    ).hexdigest()
    with immediate_transaction(connection):
        connection.execute(
            """INSERT INTO event_summary_generation_manifest VALUES
               (?, ?, ?, ?, 'Complete', ?)""",
            (generation_id, evidence_cutoff_utc, manifest_hash,
             len(source_rows), evidence_cutoff_utc),
        )
        connection.executemany(
            """INSERT INTO event_summary_projection VALUES
               (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [(generation_id, *tuple(row), evidence_cutoff_utc, manifest_hash)
             for row in source_rows],
        )
    return generation_id
