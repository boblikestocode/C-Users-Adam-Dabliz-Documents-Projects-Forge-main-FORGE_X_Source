from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from database.services.audit import AuditContext, verify_audit_chain
from database.services.connection import connect
from database.services.take_rates import (
    TakeRateIssue,
    decide_take_rate_validation,
    record_take_rate_validation,
    unresolved_blocking_take_rates,
)
from database.validation.generate_synthetic import PROFILES, populate


ROOT = Path(__file__).resolve().parents[2]


class TakeRateServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / "database" / "tests")
        path = Path(self.temp.name) / "take-rates.db"
        populate(path, PROFILES["smoke"])
        self.connection = connect(path)
        package_id, scope_id = self.connection.execute(
            "SELECT source_package_id, scope_version_id FROM scope_version LIMIT 1"
        ).fetchone()
        self.package_id = package_id
        self.scope_id = scope_id
        self.rule_id = self.connection.execute(
            "SELECT rule_version_id FROM rule_version LIMIT 1"
        ).fetchone()[0]
        self.connection.execute(
            """INSERT INTO gst_baseline
               (gst_baseline_id, source_package_id, scope_version_id, baseline_version,
                baseline_status, confirmed_by_user_id, confirmed_at_utc, recorded_at_utc)
               VALUES ('gst-original', ?, ?, 1, 'Confirmed', 'buyer-1',
                       '2026-09-04T10:00:00Z', '2026-09-04T10:00:00Z')""",
            (package_id, scope_id),
        )

    def tearDown(self) -> None:
        self.connection.close()
        self.temp.cleanup()

    @staticmethod
    def _audit(event_type: str, occurred_at: str) -> AuditContext:
        return AuditContext(
            event_type=event_type,
            actor_user_id="buyer-1",
            effective_authority="Primary Buyer",
            occurred_at_utc=occurred_at,
            display_timezone="America/New_York",
            workstation_session="test-session",
            application_version="test",
            action_method="Unit Test",
            reason_code="TEST",
            reason_text="Test evidence",
        )

    def _record_blocking(self) -> str:
        return record_take_rate_validation(
            self.connection,
            gst_baseline_id="gst-original",
            validation_rule_version_id=self.rule_id,
            issue=TakeRateIssue(
                validation_type="LEFT_RIGHT_QUANTITY_MISMATCH",
                severity="Blocking Finalization",
                affected_population={"event_part_ids": ["part-left", "part-right"], "program_year": 2028},
                validation_result={"left_take_rate": "1.0", "right_take_rate": "0.8"},
            ),
            recorded_at_utc="2026-09-04T10:01:00Z",
            audit=self._audit("Take Rate Validated", "2026-09-04T10:01:00Z"),
        )

    def test_validation_and_decisions_are_audited_and_append_only(self) -> None:
        validation_id = self._record_blocking()
        self.assertEqual(len(unresolved_blocking_take_rates(self.connection, "gst-original")), 1)
        decide_take_rate_validation(
            self.connection,
            take_rate_validation_id=validation_id,
            decision_code="Accepted Exception",
            decided_by_user_id="buyer-1",
            decision_reason="Confirmed staggered option content",
            recorded_at_utc="2026-09-04T10:02:00Z",
            audit=self._audit("Take Rate Decision", "2026-09-04T10:02:00Z"),
        )
        self.assertEqual(unresolved_blocking_take_rates(self.connection, "gst-original"), ())
        decide_take_rate_validation(
            self.connection,
            take_rate_validation_id=validation_id,
            decision_code="Still Under Review",
            decided_by_user_id="buyer-1",
            decision_reason="New program information requires another review",
            recorded_at_utc="2026-09-04T10:03:00Z",
            audit=self._audit("Take Rate Decision", "2026-09-04T10:03:00Z"),
        )
        self.assertEqual(len(unresolved_blocking_take_rates(self.connection, "gst-original")), 1)
        self.assertEqual(verify_audit_chain(self.connection), [])
        with self.assertRaisesRegex(Exception, "append-only"):
            self.connection.execute(
                "UPDATE take_rate_decision SET decision_reason = 'changed' WHERE take_rate_validation_id = ?",
                (validation_id,),
            )

    def test_corrected_baseline_must_directly_supersede_original(self) -> None:
        validation_id = self._record_blocking()
        self.connection.execute(
            """INSERT INTO gst_baseline
               (gst_baseline_id, source_package_id, scope_version_id, baseline_version,
                predecessor_baseline_id, baseline_status, recorded_at_utc)
               VALUES ('gst-corrected', ?, ?, 2, 'gst-original', 'Pending',
                       '2026-09-04T10:02:00Z')""",
            (self.package_id, self.scope_id),
        )
        decide_take_rate_validation(
            self.connection,
            take_rate_validation_id=validation_id,
            decision_code="Corrected Baseline",
            replacement_gst_baseline_id="gst-corrected",
            decided_by_user_id="buyer-1",
            decision_reason="Corrected the official left-hand volume",
            recorded_at_utc="2026-09-04T10:03:00Z",
            audit=self._audit("Take Rate Decision", "2026-09-04T10:03:00Z"),
        )
        self.assertEqual(unresolved_blocking_take_rates(self.connection, "gst-original"), ())

    def test_invalid_decision_rolls_back_without_audit(self) -> None:
        validation_id = self._record_blocking()
        audit_count = self.connection.execute("SELECT COUNT(*) FROM audit_event").fetchone()[0]
        with self.assertRaisesRegex(ValueError, "Replacement baseline"):
            decide_take_rate_validation(
                self.connection,
                take_rate_validation_id=validation_id,
                decision_code="Corrected Baseline",
                replacement_gst_baseline_id="missing",
                decided_by_user_id="buyer-1",
                decision_reason="Correction",
                recorded_at_utc="2026-09-04T10:02:00Z",
                audit=self._audit("Take Rate Decision", "2026-09-04T10:02:00Z"),
            )
        self.assertEqual(self.connection.execute("SELECT COUNT(*) FROM audit_event").fetchone()[0], audit_count)
        self.assertIsNone(self.connection.execute(
            "SELECT 1 FROM take_rate_decision WHERE take_rate_validation_id = ?", (validation_id,)
        ).fetchone())


if __name__ == "__main__":
    unittest.main()
