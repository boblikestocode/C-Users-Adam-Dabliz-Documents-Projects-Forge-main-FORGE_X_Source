from __future__ import annotations

import sqlite3
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

from .audit import AuditContext, append_audit_event
from .connection import immediate_transaction
from .ids import uuid7

from .integrity import assess_analysis_readiness


def configure_recovery_test_policy(
    connection: sqlite3.Connection, *, interval_hours: int, policy_reason: str,
    approved_by_user_id: str, recorded_at_utc: str, audit: AuditContext,
) -> str:
    if not 1 <= interval_hours <= 8760:
        raise ValueError("Recovery-test interval must be between 1 and 8760 hours")
    if not policy_reason.strip():
        raise ValueError("Recovery-test policy requires a reason")
    current = connection.execute(
        "SELECT recovery_test_policy_version_id FROM v_current_recovery_test_policy"
    ).fetchone()
    policy_id = uuid7()
    with immediate_transaction(connection):
        connection.execute(
            """INSERT INTO recovery_test_policy_version VALUES (?, ?, ?, ?, ?, ?)""",
            (policy_id, interval_hours, policy_reason.strip(), approved_by_user_id,
             None if current is None else current[0], recorded_at_utc),
        )
        append_audit_event(connection, audit, {
            "recovery_test_policy_version_id": policy_id,
            "interval_hours": interval_hours,
            "supersedes_policy_version_id": None if current is None else current[0],
        })
    return policy_id


def recovery_schedule_status(connection: sqlite3.Connection, *, as_of_utc: str) -> dict[str, object]:
    policy = connection.execute(
        """SELECT recovery_test_policy_version_id, interval_hours
           FROM v_current_recovery_test_policy"""
    ).fetchone()
    latest = connection.execute(
        """SELECT restore_event_id, local_checkpoint_id, completed_at_utc
           FROM restore_event WHERE restore_type = 'Automated Test'
             AND restore_status = 'Verified'
           ORDER BY completed_at_utc DESC, restore_event_id DESC LIMIT 1"""
    ).fetchone()
    if policy is None:
        return {"status": "Policy Required", "policy_version_id": None,
                "interval_hours": None, "last_verified_at_utc": None,
                "next_due_at_utc": None}
    last = None if latest is None else datetime.fromisoformat(str(latest[2]).replace("Z", "+00:00"))
    due = None if last is None else last + timedelta(hours=int(policy[1]))
    now = datetime.fromisoformat(as_of_utc.replace("Z", "+00:00"))
    return {"status": "Due" if due is None or now >= due else "Current",
            "policy_version_id": str(policy[0]), "interval_hours": int(policy[1]),
            "last_restore_event_id": None if latest is None else str(latest[0]),
            "last_checkpoint_id": None if latest is None else str(latest[1]),
            "last_verified_at_utc": None if latest is None else str(latest[2]),
            "next_due_at_utc": None if due is None else due.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")}


def operational_status(connection: sqlite3.Connection) -> dict[str, object]:
    readiness = assess_analysis_readiness(connection)
    checkpoint = connection.execute(
        """SELECT local_checkpoint_id, schema_version, database_hash,
                  created_at_utc, verified_at_utc
           FROM local_checkpoint WHERE verification_status = 'Verified'
             AND EXISTS (SELECT 1 FROM restore_event restore
                 WHERE restore.local_checkpoint_id = local_checkpoint.local_checkpoint_id
                   AND restore.restore_type = 'Automated Test'
                   AND restore.restore_status = 'Verified')
           ORDER BY verified_at_utc DESC, local_checkpoint_id DESC LIMIT 1"""
    ).fetchone()
    published = connection.execute(
        """SELECT publication.publication_id, status.recorded_at_utc,
                  publication.package_hash, publication.destination_locator
           FROM publication
           JOIN v_current_publication_status status
             ON status.publication_id = publication.publication_id
           WHERE status.publication_status = 'Published'
           ORDER BY status.recorded_at_utc DESC, publication.publication_id DESC LIMIT 1"""
    ).fetchone()
    snapshot = connection.execute(
        """SELECT finalized_snapshot_id, analysis_id, scenario_revision_id,
                  calculation_run_id, finalized_at_utc
           FROM finalized_snapshot
           ORDER BY finalized_at_utc DESC, finalized_snapshot_id DESC LIMIT 1"""
    ).fetchone()
    authority = connection.execute(
        """SELECT authority_verification_reference,
                  authority_verified_online_at_utc
           FROM publication_attempt
           ORDER BY authority_verified_online_at_utc DESC,
                    publication_attempt_id DESC LIMIT 1"""
    ).fetchone()
    queue_counts = {
        str(row[0]): int(row[1])
        for row in connection.execute(
            "SELECT queue_status, COUNT(*) FROM sync_queue_item GROUP BY queue_status"
        )
    }
    open_conflict_count = int(connection.execute(
        "SELECT COUNT(*) FROM sync_conflict WHERE conflict_status = 'Open'"
    ).fetchone()[0])

    def record(row: sqlite3.Row | None, names: tuple[str, ...]) -> dict[str, object] | None:
        return None if row is None else dict(zip(names, tuple(row)))

    return {
        "database_health": {
            "ready": readiness.can_proceed,
            "blocking_findings": [asdict(item) for item in readiness.blocking_findings],
            "advisory_findings": [asdict(item) for item in readiness.advisory_findings],
        },
        "last_verified_checkpoint": record(
            checkpoint,
            ("checkpoint_id", "schema_version", "database_hash", "created_at_utc", "verified_at_utc"),
        ),
        "last_successful_publication": record(
            published,
            ("publication_id", "published_at_utc", "package_hash", "destination_locator"),
        ),
        "pending_sync_count": sum(
            count for status, count in queue_counts.items()
            if status in {"Queued", "Leased", "Retry Pending"}
        ),
        "sync_queue_counts": queue_counts,
        "latest_finalized_snapshot": record(
            snapshot,
            ("snapshot_id", "analysis_id", "scenario_revision_id", "calculation_run_id", "finalized_at_utc"),
        ),
        "authority_state": {
            "publication_requires_fresh_online_verification": True,
            "last_verification_reference": None if authority is None else authority[0],
            "last_verified_online_at_utc": None if authority is None else authority[1],
        },
        "open_conflict_count": open_conflict_count,
        "recovery_test_schedule": recovery_schedule_status(
            connection,
            as_of_utc=datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        ),
    }
