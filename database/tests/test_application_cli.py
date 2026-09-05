from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from app.cli import event_status_payload, health_payload, main, operational_status_payload
from database.validation.generate_synthetic import PROFILES, populate


ROOT = Path(__file__).resolve().parents[2]


class ApplicationCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / "database" / "tests")
        self.database = Path(self.temp.name) / "application.db"
        populate(self.database, PROFILES["smoke"])

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_health_payload_reports_a_migrated_database_ready(self) -> None:
        payload = health_payload(self.database)
        self.assertTrue(payload["ready"])
        self.assertEqual(payload["blocking_findings"], [])

    def test_event_status_payload_exposes_governed_gate_results(self) -> None:
        connection = sqlite3.connect(self.database)
        try:
            event_id = connection.execute("SELECT event_id FROM sourcing_event LIMIT 1").fetchone()[0]
        finally:
            connection.close()
        payload = event_status_payload(self.database, str(event_id))
        self.assertFalse(payload["analysis_ready"])
        self.assertIn(
            "CONFIRMED_GST_BASELINE_MISSING",
            {finding["code"] for finding in payload["findings"]},
        )

    def test_migrate_command_emits_json_and_is_idempotent(self) -> None:
        database = Path(self.temp.name) / "fresh.db"
        output = StringIO()
        with redirect_stdout(output):
            exit_code = main(["migrate", str(database)])
        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(len(payload["migrations"]), 49)
        self.assertTrue(all(item["status"] == "Applied" for item in payload["migrations"]))
        self.assertEqual(len(payload["preflight_database_hash"]), 64)
        self.assertIsNotNone(payload["migration_execution_id"])

    def test_operational_status_exposes_required_continuity_fields(self) -> None:
        payload = operational_status_payload(self.database)
        self.assertTrue(payload["database_health"]["ready"])
        self.assertEqual(payload["pending_sync_count"], 0)
        self.assertEqual(payload["open_conflict_count"], 0)
        self.assertIsNone(payload["last_verified_checkpoint"])
        self.assertIsNone(payload["last_successful_publication"])
        self.assertTrue(
            payload["authority_state"]["publication_requires_fresh_online_verification"]
        )


if __name__ == "__main__":
    unittest.main()
