from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from database.services.connection import connect
from database.services.integrity import run_health_gate
from database.services.summary_projections import build_event_summary_projection
from database.validation.generate_synthetic import PROFILES, populate


ROOT = Path(__file__).resolve().parents[2]


class SummaryProjectionServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / "database" / "tests")
        self.database_path = Path(self.temp.name) / "summary.db"
        populate(self.database_path, PROFILES["smoke"])
        self.connection = connect(self.database_path)
        self.event_id = self.connection.execute(
            "SELECT event_id FROM sourcing_event LIMIT 1"
        ).fetchone()[0]

    def tearDown(self) -> None:
        self.connection.close()
        self.temp.cleanup()

    def test_builds_atomic_event_summary_from_authoritative_sources(self) -> None:
        generation = build_event_summary_projection(
            self.connection, evidence_cutoff_utc="2026-12-31T23:59:59Z"
        )
        row = self.connection.execute(
            """SELECT source_package_number, event_name, buyer_code_id,
                      event_status, supplier_count, open_action_count
               FROM event_summary_projection
               WHERE projection_generation_id = ?""",
            (generation,),
        ).fetchone()
        self.assertEqual(
            tuple(row),
            ("SP-SYNTHETIC-001", "Synthetic Sourcing Event", "BUYER-001",
             "Active", 3, 0),
        )
        manifest = self.connection.execute(
            """SELECT projected_row_count, generation_status
               FROM event_summary_generation_manifest
               WHERE projection_generation_id = ?""",
            (generation,),
        ).fetchone()
        self.assertEqual(tuple(manifest), (1, "Complete"))
        self.assertNotIn(
            "EVENT_SUMMARY_GENERATION_MISMATCH",
            {finding.code for finding in run_health_gate(
                self.connection, as_of_utc="2026-12-31T23:59:59Z")},
        )

    def test_historical_generation_survives_later_action_resolution(self) -> None:
        self.connection.execute(
            """INSERT INTO buyer_action VALUES
               ('summary-action', ?, NULL, NULL, 'Sourcing Event', ?, 'Review',
                'buyer', '2026-02-01T00:00:00Z')""",
            (self.event_id, self.event_id),
        )
        self.connection.execute(
            """INSERT INTO buyer_action_version
               (buyer_action_version_id, buyer_action_id, action_status,
                required_supplier_action, owner_user_id, recorded_by_user_id,
                recorded_at_utc)
               VALUES ('summary-open', 'summary-action', 'Open', 'Review',
                       'buyer', 'buyer', '2026-02-01T00:00:00Z')"""
        )
        generation = build_event_summary_projection(
            self.connection, evidence_cutoff_utc="2026-02-02T00:00:00Z"
        )
        self.connection.execute(
            """INSERT INTO buyer_action_version
               (buyer_action_version_id, buyer_action_id, action_status,
                required_supplier_action, owner_user_id, resolution_reason,
                supersedes_action_version_id, recorded_by_user_id, recorded_at_utc)
               VALUES ('summary-resolved', 'summary-action', 'Resolved', 'Review',
                       'buyer', 'Reviewed', 'summary-open', 'buyer',
                       '2026-02-03T00:00:00Z')"""
        )
        self.assertEqual(self.connection.execute(
            """SELECT open_action_count FROM event_summary_projection
               WHERE projection_generation_id = ?""", (generation,),
        ).fetchone()[0], 1)
        self.assertNotIn(
            "EVENT_SUMMARY_GENERATION_MISMATCH",
            {finding.code for finding in run_health_gate(
                self.connection, as_of_utc="2026-02-04T00:00:00Z")},
        )

    def test_health_detects_incomplete_declared_generation(self) -> None:
        self.connection.execute(
            """INSERT INTO event_summary_generation_manifest VALUES
               ('forged-generation', '2026-12-31T23:59:59Z', ?, 0,
                'Complete', '2026-12-31T23:59:59Z')""",
            ("0" * 64,),
        )
        self.assertIn(
            "EVENT_SUMMARY_GENERATION_MISMATCH",
            {finding.code for finding in run_health_gate(
                self.connection, as_of_utc="2026-12-31T23:59:59Z")},
        )


if __name__ == "__main__":
    unittest.main()
