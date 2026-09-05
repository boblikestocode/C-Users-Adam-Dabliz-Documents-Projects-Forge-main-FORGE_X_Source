from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from database.services.audit import AuditContext
from database.services.connection import connect
from database.services.operations import configure_recovery_test_policy, recovery_schedule_status
from database.services.publication import create_verified_checkpoint, run_automated_restore_test
from database.validation.generate_synthetic import PROFILES, populate


ROOT = Path(__file__).resolve().parents[2]


def audit(kind: str, timestamp: str) -> AuditContext:
    return AuditContext(kind, "admin", "Administrator", timestamp,
                        "America/New_York", "recovery-test", "test",
                        "Automated Test", "RECOVERY")


class RecoveryPolicyServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / "database" / "tests")
        self.base = Path(self.temp.name)
        self.path = self.base / "commodity.db"
        populate(self.path, PROFILES["smoke"])
        self.connection = connect(self.path)

    def tearDown(self) -> None:
        self.connection.close()
        self.temp.cleanup()

    def test_policy_reports_due_then_current_after_real_restore(self) -> None:
        self.assertEqual(recovery_schedule_status(
            self.connection, as_of_utc="2026-09-03T00:00:00Z")["status"],
            "Policy Required",
        )
        configure_recovery_test_policy(
            self.connection, interval_hours=168, policy_reason="Weekly recovery proof",
            approved_by_user_id="admin", recorded_at_utc="2026-09-03T00:00:00Z",
            audit=audit("Recovery Policy Configured", "2026-09-03T00:00:00Z"),
        )
        self.assertEqual(recovery_schedule_status(
            self.connection, as_of_utc="2026-09-03T00:01:00Z")["status"], "Due")
        checkpoint = create_verified_checkpoint(
            self.connection, checkpoint_path=self.base / "checkpoint.db",
            created_at_utc="2026-09-03T00:02:00Z",
            audit=audit("Checkpoint Verified", "2026-09-03T00:02:00Z"),
        )
        result = run_automated_restore_test(
            self.connection, checkpoint=checkpoint,
            restored_path=self.base / "restored.db",
            started_at_utc="2026-09-03T00:03:00Z",
            completed_at_utc="2026-09-03T00:04:00Z",
            audit=audit("Automated Restore Verified", "2026-09-03T00:04:00Z"),
        )
        self.assertEqual(result.restore_status, "Verified")
        status = recovery_schedule_status(
            self.connection, as_of_utc="2026-09-04T00:00:00Z")
        self.assertEqual(status["status"], "Current")
        self.assertEqual(status["next_due_at_utc"], "2026-09-10T00:04:00Z")

    def test_tampered_checkpoint_records_failed_restore(self) -> None:
        checkpoint = create_verified_checkpoint(
            self.connection, checkpoint_path=self.base / "checkpoint.db",
            created_at_utc="2026-09-03T00:02:00Z",
            audit=audit("Checkpoint Verified", "2026-09-03T00:02:00Z"),
        )
        with checkpoint.checkpoint_path.open("ab") as stream:
            stream.write(b"tampered")
        result = run_automated_restore_test(
            self.connection, checkpoint=checkpoint,
            restored_path=self.base / "failed-restored.db",
            started_at_utc="2026-09-03T00:03:00Z",
            completed_at_utc="2026-09-03T00:04:00Z",
            audit=audit("Automated Restore Failed", "2026-09-03T00:04:00Z"),
        )
        self.assertEqual(result.restore_status, "Failed")
        self.assertIn("source_checkpoint_hash_mismatch", result.failures)
        self.assertFalse((self.base / "failed-restored.db").exists())


if __name__ == "__main__":
    unittest.main()
