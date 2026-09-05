from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from database.services.audit import AuditContext, verify_audit_chain
from database.services.authority import (
    lock_expired_sessions,
    open_database_session,
    record_online_authority_verification,
    record_session_activity,
    require_active_write_session,
    revoke_authority_verification,
)
from database.services.connection import connect
from database.validation.generate_synthetic import PROFILES, populate


ROOT = Path(__file__).resolve().parents[2]


def audit(event_type: str, timestamp: str) -> AuditContext:
    return AuditContext(
        event_type=event_type, actor_user_id="buyer-1",
        effective_authority="Buyer", occurred_at_utc=timestamp,
        display_timezone="America/New_York", workstation_session="authority-test",
        application_version="test", action_method="Automated Test",
        reason_code="TEST",
    )


class AuthorityServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / "database" / "tests")
        path = Path(self.temp.name) / "authority.db"
        populate(path, PROFILES["smoke"])
        self.connection = connect(path)
        self.database_id = self.connection.execute(
            "SELECT database_id FROM commodity_database"
        ).fetchone()[0]

    def tearDown(self) -> None:
        self.connection.close()
        self.temp.cleanup()

    def _verify(self) -> str:
        return record_online_authority_verification(
            self.connection, stable_user_id="buyer-1",
            registered_device_id="device-1", authority_scope="Commodity Write",
            authority_reference="authority-v7",
            registry_publication_id="registry-publication-4",
            write_authority=True,
            verified_online_at_utc="2026-09-01T12:00:00Z",
            audit=audit("Authority Verified", "2026-09-01T12:00:00Z"),
        )

    def test_same_user_and_device_can_write_offline_within_fourteen_days(self) -> None:
        verification_id = self._verify()
        decision = open_database_session(
            self.connection, database_id=self.database_id,
            stable_user_id="buyer-1", registered_device_id="device-1",
            opened_at_utc="2026-09-14T12:00:00Z",
            audit=audit("Database Opened", "2026-09-14T12:00:00Z"),
        )
        self.assertEqual(decision.access_mode, "Read Write")
        self.assertEqual(decision.authority_verification_id, verification_id)
        self.assertEqual(decision.inactivity_expires_at_utc, "2026-09-14T12:15:00Z")
        self.assertEqual(require_active_write_session(
            self.connection,
            database_access_session_id=decision.database_access_session_id,
            stable_user_id="buyer-1", registered_device_id="device-1",
            operation_at_utc="2026-09-14T12:14:59Z",
        ), verification_id)

    def test_expired_or_different_device_authority_is_read_only(self) -> None:
        self._verify()
        expired = open_database_session(
            self.connection, database_id=self.database_id,
            stable_user_id="buyer-1", registered_device_id="device-1",
            opened_at_utc="2026-09-15T12:00:01Z",
            audit=audit("Database Opened", "2026-09-15T12:00:01Z"),
        )
        other_device = open_database_session(
            self.connection, database_id=self.database_id,
            stable_user_id="buyer-1", registered_device_id="device-2",
            opened_at_utc="2026-09-02T12:00:00Z",
            audit=audit("Database Opened", "2026-09-02T12:00:00Z"),
        )
        self.assertEqual(expired.access_mode, "Read Only")
        self.assertIn("14-day", expired.reason)
        self.assertEqual(other_device.access_mode, "Read Only")
        self.assertIn("user and device", other_device.reason)

    def test_revocation_immediately_locks_active_session(self) -> None:
        verification_id = self._verify()
        decision = open_database_session(
            self.connection, database_id=self.database_id,
            stable_user_id="buyer-1", registered_device_id="device-1",
            opened_at_utc="2026-09-02T12:00:00Z",
            audit=audit("Database Opened", "2026-09-02T12:00:00Z"),
        )
        revoke_authority_verification(
            self.connection, authority_verification_id=verification_id,
            reason="Authority removed by current registry",
            revoked_at_utc="2026-09-02T12:01:00Z",
            audit=audit("Authority Revoked", "2026-09-02T12:01:00Z"),
        )
        session = self.connection.execute(
            """SELECT session_status, terminal_reason FROM database_access_session
               WHERE database_access_session_id = ?""",
            (decision.database_access_session_id,),
        ).fetchone()
        self.assertEqual(session[0], "Locked")
        self.assertIn("Authority revoked", session[1])
        with self.assertRaisesRegex(PermissionError, "active read-write"):
            require_active_write_session(
                self.connection,
                database_access_session_id=decision.database_access_session_id,
                stable_user_id="buyer-1", registered_device_id="device-1",
                operation_at_utc="2026-09-02T12:02:00Z",
            )

    def test_activity_renews_fifteen_minute_window_and_expiry_locks(self) -> None:
        self._verify()
        decision = open_database_session(
            self.connection, database_id=self.database_id,
            stable_user_id="buyer-1", registered_device_id="device-1",
            opened_at_utc="2026-09-02T12:00:00Z",
            audit=audit("Database Opened", "2026-09-02T12:00:00Z"),
        )
        renewed = record_session_activity(
            self.connection,
            database_access_session_id=decision.database_access_session_id,
            activity_at_utc="2026-09-02T12:10:00Z",
        )
        self.assertEqual(renewed, "2026-09-02T12:25:00Z")
        self.assertEqual(lock_expired_sessions(
            self.connection, observed_at_utc="2026-09-02T12:24:59Z",
            audit=audit("Inactive Sessions Locked", "2026-09-02T12:24:59Z"),
        ), ())
        self.assertEqual(lock_expired_sessions(
            self.connection, observed_at_utc="2026-09-02T12:25:00Z",
            audit=audit("Inactive Sessions Locked", "2026-09-02T12:25:00Z"),
        ), (decision.database_access_session_id,))
        self.assertEqual(verify_audit_chain(self.connection), [])


if __name__ == "__main__":
    unittest.main()
