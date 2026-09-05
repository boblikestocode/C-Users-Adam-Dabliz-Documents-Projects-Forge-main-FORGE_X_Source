from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from database.services.connection import connect
from database.services.workflow import assess_event_workflow
from database.validation.generate_synthetic import PROFILES, populate


ROOT = Path(__file__).resolve().parents[2]


class WorkflowServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / "database" / "tests")
        path = Path(self.temp.name) / "workflow.db"
        populate(path, PROFILES["smoke"])
        self.connection = connect(path)
        self.event_id = self.connection.execute("SELECT event_id FROM sourcing_event LIMIT 1").fetchone()[0]
        self.package_id, self.scope_id = self.connection.execute(
            """SELECT package.source_package_id, version.scope_version_id
               FROM source_package package
               JOIN scope_version version ON version.source_package_id = package.source_package_id
               WHERE package.event_id = ? LIMIT 1""", (self.event_id,)
        ).fetchone()

    def tearDown(self) -> None:
        self.connection.close()
        self.temp.cleanup()

    def _confirm_baseline(self) -> None:
        self.connection.execute(
            """INSERT INTO gst_baseline
               (gst_baseline_id, source_package_id, scope_version_id, baseline_version,
                baseline_status, confirmed_by_user_id, confirmed_at_utc, recorded_at_utc)
               VALUES ('workflow-gst', ?, ?, 1, 'Confirmed', 'buyer-1',
                       '2026-09-04T15:00:00Z', '2026-09-04T15:00:00Z')""",
            (self.package_id, self.scope_id),
        )

    def test_missing_baseline_blocks_analysis_with_specific_finding(self) -> None:
        status = assess_event_workflow(self.connection, event_id=self.event_id)
        self.assertTrue(status.scope_ready)
        self.assertFalse(status.analysis_ready)
        self.assertIn("CONFIRMED_GST_BASELINE_MISSING", {item.code for item in status.findings})

    def test_confirmed_baseline_and_active_rounds_enable_workflow(self) -> None:
        self._confirm_baseline()
        status = assess_event_workflow(self.connection, event_id=self.event_id)
        self.assertTrue(status.analysis_ready)
        self.assertTrue(status.finalization_ready)
        self.assertTrue(status.publication_ready)
        self.assertGreater(status.active_supplier_count, 0)

    def test_unresolved_take_rate_blocks_finalization_but_not_analysis(self) -> None:
        self._confirm_baseline()
        rule_id = self.connection.execute("SELECT rule_version_id FROM rule_version LIMIT 1").fetchone()[0]
        self.connection.execute(
            """INSERT INTO take_rate_validation VALUES
               ('workflow-take-rate', 'workflow-gst', ?, 'PAIR_MISMATCH',
                'Blocking Finalization', '{}', '{}', '2026-09-04T15:01:00Z')""",
            (rule_id,),
        )
        status = assess_event_workflow(self.connection, event_id=self.event_id)
        self.assertTrue(status.analysis_ready)
        self.assertFalse(status.finalization_ready)
        finding = next(item for item in status.findings if item.code == "TAKE_RATE_REVIEW_UNRESOLVED")
        self.assertEqual(finding.count, 1)


if __name__ == "__main__":
    unittest.main()
