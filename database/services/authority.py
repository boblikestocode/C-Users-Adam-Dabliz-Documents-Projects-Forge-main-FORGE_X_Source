from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .audit import AuditContext, append_audit_event
from .connection import immediate_transaction
from .ids import uuid7


OFFLINE_AUTHORITY_LIMIT = timedelta(days=14)
INACTIVITY_LIMIT = timedelta(minutes=15)


@dataclass(frozen=True)
class AccessDecision:
    database_access_session_id: str
    access_mode: str
    authority_verification_id: str | None
    reason: str
    inactivity_expires_at_utc: str


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValueError("Authority timestamps must be UTC ISO-8601 values")
    return parsed.astimezone(timezone.utc)


def _format_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def record_online_authority_verification(
    connection: sqlite3.Connection,
    *,
    stable_user_id: str,
    registered_device_id: str,
    authority_scope: str,
    authority_reference: str,
    registry_publication_id: str,
    write_authority: bool,
    verified_online_at_utc: str,
    audit: AuditContext,
) -> str:
    if not all(value.strip() for value in (
        stable_user_id, registered_device_id, authority_scope,
        authority_reference, registry_publication_id,
    )):
        raise ValueError("Online authority evidence requires complete identity and registry references")
    verified = _parse_utc(verified_online_at_utc)
    expires = _format_utc(verified + OFFLINE_AUTHORITY_LIMIT)
    verification_id = uuid7()
    with immediate_transaction(connection):
        connection.execute(
            """INSERT INTO authority_verification
               (authority_verification_id, stable_user_id, registered_device_id,
                authority_scope, authority_reference, registry_publication_id,
                write_authority_flag, verified_online_at_utc,
                offline_expires_at_utc, recorded_at_utc)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (verification_id, stable_user_id, registered_device_id,
             authority_scope, authority_reference, registry_publication_id,
             int(write_authority), verified_online_at_utc, expires,
             verified_online_at_utc),
        )
        connection.execute(
            """INSERT INTO authority_verification_status_event VALUES
               (?, ?, 'Verified', 'Current online authority registry verified', ?)""",
            (uuid7(), verification_id, verified_online_at_utc),
        )
        append_audit_event(
            connection, audit,
            {"authority_verification_id": verification_id,
             "stable_user_id": stable_user_id,
             "registered_device_id": registered_device_id,
             "authority_scope": authority_scope,
             "registry_publication_id": registry_publication_id,
             "write_authority": write_authority,
             "offline_expires_at_utc": expires},
        )
    return verification_id


def revoke_authority_verification(
    connection: sqlite3.Connection,
    *,
    authority_verification_id: str,
    reason: str,
    revoked_at_utc: str,
    audit: AuditContext,
) -> None:
    _parse_utc(revoked_at_utc)
    if not reason.strip():
        raise ValueError("Authority revocation requires a reason")
    with immediate_transaction(connection):
        current = connection.execute(
            """SELECT verification_status FROM v_current_authority_verification_status
               WHERE authority_verification_id = ?""",
            (authority_verification_id,),
        ).fetchone()
        if current is None:
            raise ValueError("Authority verification does not exist")
        if current[0] == "Revoked":
            raise ValueError("Authority verification is already revoked")
        connection.execute(
            """INSERT INTO authority_verification_status_event VALUES
               (?, ?, 'Revoked', ?, ?)""",
            (uuid7(), authority_verification_id, reason.strip(), revoked_at_utc),
        )
        connection.execute(
            """UPDATE database_access_session
               SET session_status = 'Locked', terminal_reason = ?,
                   last_activity_at_utc = ?, inactivity_expires_at_utc = ?
               WHERE authority_verification_id = ? AND session_status = 'Active'""",
            ("Authority revoked: " + reason.strip(), revoked_at_utc,
             _format_utc(_parse_utc(revoked_at_utc) + INACTIVITY_LIMIT),
             authority_verification_id),
        )
        append_audit_event(
            connection, audit,
            {"authority_verification_id": authority_verification_id,
             "verification_status": "Revoked", "reason": reason.strip()},
        )


def open_database_session(
    connection: sqlite3.Connection,
    *,
    database_id: str,
    stable_user_id: str,
    registered_device_id: str,
    opened_at_utc: str,
    audit: AuditContext,
) -> AccessDecision:
    opened = _parse_utc(opened_at_utc)
    authority = connection.execute(
        """SELECT verification.authority_verification_id,
                  verification.offline_expires_at_utc,
                  verification.write_authority_flag
           FROM authority_verification verification
           JOIN v_current_authority_verification_status status
             ON status.authority_verification_id = verification.authority_verification_id
           WHERE verification.stable_user_id = ?
             AND verification.registered_device_id = ?
             AND status.verification_status = 'Verified'
           ORDER BY verification.verified_online_at_utc DESC,
                    verification.authority_verification_id DESC LIMIT 1""",
        (stable_user_id, registered_device_id),
    ).fetchone()
    if authority is None:
        mode, verification_id = "Read Only", None
        reason = "No current authority verification for this user and device"
    elif opened > _parse_utc(str(authority[1])):
        mode, verification_id = "Read Only", str(authority[0])
        reason = "Offline write authority expired after the absolute 14-day limit"
    elif not bool(authority[2]):
        mode, verification_id = "Read Only", str(authority[0])
        reason = "Current authority grants read-only access"
    else:
        mode, verification_id = "Read Write", str(authority[0])
        reason = "Current same-user, same-device authority is valid"
    expires = _format_utc(opened + INACTIVITY_LIMIT)
    session_id = uuid7()
    with immediate_transaction(connection):
        connection.execute(
            """INSERT INTO database_access_session
               (database_access_session_id, database_id, stable_user_id,
                registered_device_id, authority_verification_id, access_mode,
                session_status, opened_at_utc, last_activity_at_utc,
                inactivity_expires_at_utc)
               VALUES (?, ?, ?, ?, ?, ?, 'Active', ?, ?, ?)""",
            (session_id, database_id, stable_user_id, registered_device_id,
             verification_id, mode, opened_at_utc, opened_at_utc, expires),
        )
        append_audit_event(
            connection, audit,
            {"database_access_session_id": session_id, "database_id": database_id,
             "stable_user_id": stable_user_id,
             "registered_device_id": registered_device_id, "access_mode": mode,
             "authority_verification_id": verification_id, "reason": reason,
             "inactivity_expires_at_utc": expires},
        )
    return AccessDecision(session_id, mode, verification_id, reason, expires)


def record_session_activity(
    connection: sqlite3.Connection,
    *,
    database_access_session_id: str,
    activity_at_utc: str,
) -> str:
    activity = _parse_utc(activity_at_utc)
    expires = _format_utc(activity + INACTIVITY_LIMIT)
    with immediate_transaction(connection):
        session = connection.execute(
            """SELECT session_status, inactivity_expires_at_utc
               FROM database_access_session WHERE database_access_session_id = ?""",
            (database_access_session_id,),
        ).fetchone()
        if session is None or session[0] != "Active":
            raise ValueError("Database access session is not active")
        if activity >= _parse_utc(str(session[1])):
            raise PermissionError("Database access session has exceeded the inactivity limit")
        connection.execute(
            """UPDATE database_access_session SET last_activity_at_utc = ?,
               inactivity_expires_at_utc = ? WHERE database_access_session_id = ?""",
            (activity_at_utc, expires, database_access_session_id),
        )
    return expires


def require_active_write_session(
    connection: sqlite3.Connection,
    *,
    database_access_session_id: str,
    stable_user_id: str,
    registered_device_id: str,
    operation_at_utc: str,
) -> str:
    operation_at = _parse_utc(operation_at_utc)
    session = connection.execute(
        """SELECT session.access_mode, session.session_status,
                  session.inactivity_expires_at_utc,
                  session.authority_verification_id,
                  verification.offline_expires_at_utc,
                  status.verification_status
           FROM database_access_session session
           LEFT JOIN authority_verification verification
             ON verification.authority_verification_id = session.authority_verification_id
           LEFT JOIN v_current_authority_verification_status status
             ON status.authority_verification_id = verification.authority_verification_id
           WHERE session.database_access_session_id = ?
             AND session.stable_user_id = ?
             AND session.registered_device_id = ?""",
        (database_access_session_id, stable_user_id, registered_device_id),
    ).fetchone()
    if session is None:
        raise PermissionError("Database session does not match the authenticated user and device")
    if session[0] != "Read Write" or session[1] != "Active":
        raise PermissionError("An active read-write database session is required")
    if operation_at >= _parse_utc(str(session[2])):
        raise PermissionError("Database session has reached the inactivity limit")
    if session[3] is None or session[5] != "Verified":
        raise PermissionError("Current write authority is unavailable or revoked")
    if operation_at > _parse_utc(str(session[4])):
        raise PermissionError("Offline write authority has exceeded the absolute 14-day limit")
    return str(session[3])


def lock_expired_sessions(
    connection: sqlite3.Connection,
    *,
    observed_at_utc: str,
    audit: AuditContext,
) -> tuple[str, ...]:
    _parse_utc(observed_at_utc)
    expired = [str(row[0]) for row in connection.execute(
        """SELECT database_access_session_id FROM database_access_session
           WHERE session_status = 'Active' AND inactivity_expires_at_utc <= ?
           ORDER BY database_access_session_id""", (observed_at_utc,)
    )]
    if not expired:
        return ()
    with immediate_transaction(connection):
        for session_id in expired:
            connection.execute(
                """UPDATE database_access_session SET session_status = 'Locked',
                   terminal_reason = '15-minute inactivity limit reached'
                   WHERE database_access_session_id = ?""", (session_id,),
            )
        append_audit_event(
            connection, audit,
            {"locked_database_access_session_ids": expired,
             "reason": "15-minute inactivity limit reached",
             "observed_at_utc": observed_at_utc},
        )
    return tuple(expired)
