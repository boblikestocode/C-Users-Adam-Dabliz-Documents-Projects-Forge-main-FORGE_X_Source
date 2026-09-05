from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from database.services.audit import AuditContext
from database.services.connection import connect
from database.services.events import correct_source_package_number
from database.services.integrity import run_health_gate
from database.services.package_mismatches import (
    record_source_package_mismatch,
    resolve_source_package_mismatch,
)
from database.validation.generate_synthetic import PROFILES, populate


ROOT = Path(__file__).resolve().parents[2]


def audit(kind: str, timestamp: str) -> AuditContext:
    return AuditContext(kind, "buyer-1", "Buyer", timestamp, "America/New_York",
                        "mismatch-test", "test", "Automated Test", "PACKAGE_MISMATCH")


class SourcePackageMismatchServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / "database" / "tests")
        self.path = Path(self.temp.name) / "mismatch.db"
        populate(self.path, PROFILES["smoke"])
        self.connection = connect(self.path)
        self.package_id = self.connection.execute(
            "SELECT source_package_id FROM source_package LIMIT 1"
        ).fetchone()[0]
        worksheet_id = self.connection.execute(
            "SELECT worksheet_id FROM source_worksheet LIMIT 1"
        ).fetchone()[0]
        self.datum_id = "package-number-datum"
        self.connection.execute(
            """INSERT INTO source_datum
               (source_datum_id, worksheet_id, cell_or_range, submitted_lexeme,
                recorded_at_utc) VALUES (?, ?, 'ZZ999', 'SP-WRONG-9', ?)""",
            (self.datum_id, worksheet_id, "2026-09-04T14:59:00Z"),
        )

    def tearDown(self) -> None:
        self.connection.close()
        self.temp.cleanup()

    def test_supplier_error_preserves_mismatch_and_closes_action_as_exception(self) -> None:
        mismatch_id, action_id = record_source_package_mismatch(
            self.connection, source_package_id=self.package_id,
            source_datum_id=self.datum_id, submitted_package_number="SP-WRONG-9",
            owner_user_id="buyer-1", detected_at_utc="2026-09-04T15:00:00Z",
            audit=audit("Package Mismatch Detected", "2026-09-04T15:00:00Z"),
        )
        row = self.connection.execute(
            """SELECT submitted_displayed_package_number, event_displayed_package_number,
                      mismatch_status FROM source_package_mismatch
               WHERE source_package_mismatch_id = ?""", (mismatch_id,),
        ).fetchone()
        self.assertEqual(tuple(row), ("SP-WRONG-9", "SP-SYNTHETIC-001", "Review Required"))
        resolve_source_package_mismatch(
            self.connection, source_package_mismatch_id=mismatch_id,
            decision_code="Supplier Correction Required",
            decision_reason="Buyer confirmed supplier administrative error",
            decided_by_user_id="buyer-1", recorded_at_utc="2026-09-04T15:01:00Z",
            audit=audit("Package Mismatch Resolved", "2026-09-04T15:01:00Z"),
        )
        self.assertEqual(self.connection.execute(
            "SELECT action_status FROM v_current_buyer_action WHERE buyer_action_id = ?",
            (action_id,),
        ).fetchone()[0], "Accepted Exception")
        self.assertNotIn("SOURCE_PACKAGE_MISMATCH_RESOLUTION_INVALID",
                         {finding.code for finding in run_health_gate(self.connection)})
        with self.assertRaisesRegex(Exception, "immutable"):
            self.connection.execute(
                "UPDATE source_package_mismatch SET mismatch_status = 'Review Required' WHERE source_package_mismatch_id = ?",
                (mismatch_id,),
            )

    def test_event_correction_must_match_submitted_evidence(self) -> None:
        self.connection.execute(
            """INSERT INTO source_datum
               (source_datum_id, worksheet_id, cell_or_range, submitted_lexeme,
                recorded_at_utc)
               SELECT 'correct-package-datum', worksheet_id, 'ZZ998',
                      'SP-CORRECT-2', '2026-09-04T14:59:00Z'
               FROM source_worksheet LIMIT 1"""
        )
        mismatch_id, action_id = record_source_package_mismatch(
            self.connection, source_package_id=self.package_id,
            source_datum_id="correct-package-datum", submitted_package_number="SP-CORRECT-2",
            owner_user_id="buyer-1", detected_at_utc="2026-09-04T15:00:00Z",
            audit=audit("Package Mismatch Detected", "2026-09-04T15:00:00Z"),
        )
        with self.assertRaisesRegex(ValueError, "current event number"):
            resolve_source_package_mismatch(
                self.connection, source_package_mismatch_id=mismatch_id,
                decision_code="Event Number Corrected", decision_reason="Too early",
                decided_by_user_id="buyer-1", recorded_at_utc="2026-09-04T15:01:00Z",
                audit=audit("Invalid Resolution", "2026-09-04T15:01:00Z"),
            )
        correct_source_package_number(
            self.connection, source_package_id=self.package_id,
            corrected_package_number="SP-CORRECT-2", correction_reason="Correct buyer entry",
            corrected_by_user_id="buyer-1", recorded_at_utc="2026-09-04T15:02:00Z",
            audit=audit("Source Package Corrected", "2026-09-04T15:02:00Z"),
        )
        resolve_source_package_mismatch(
            self.connection, source_package_mismatch_id=mismatch_id,
            decision_code="Event Number Corrected", decision_reason="Event identity corrected",
            decided_by_user_id="buyer-1", recorded_at_utc="2026-09-04T15:03:00Z",
            audit=audit("Package Mismatch Resolved", "2026-09-04T15:03:00Z"),
        )
        self.assertEqual(self.connection.execute(
            "SELECT action_status FROM v_current_buyer_action WHERE buyer_action_id = ?",
            (action_id,),
        ).fetchone()[0], "Resolved")
        self.assertNotIn("SOURCE_PACKAGE_MISMATCH_RESOLUTION_INVALID",
                         {finding.code for finding in run_health_gate(self.connection)})

    def test_health_detects_missing_current_action_version(self) -> None:
        mismatch_id, action_id = record_source_package_mismatch(
            self.connection, source_package_id=self.package_id,
            source_datum_id=self.datum_id, submitted_package_number="SP-WRONG-9",
            owner_user_id="buyer-1", detected_at_utc="2026-09-04T15:00:00Z",
            audit=audit("Package Mismatch Detected", "2026-09-04T15:00:00Z"),
        )
        self.connection.execute(
            """INSERT INTO buyer_action
               (buyer_action_id, event_id, governing_entity_type, governing_entity_id,
                issue_type, created_by_user_id, created_at_utc)
               SELECT 'duplicate-action', event_id, governing_entity_type,
                      governing_entity_id, issue_type, created_by_user_id, created_at_utc
               FROM buyer_action WHERE buyer_action_id = ?""", (action_id,),
        )
        codes = {finding.code for finding in run_health_gate(self.connection)}
        self.assertIn("SOURCE_PACKAGE_MISMATCH_ACTION_INVALID", codes)
        self.assertEqual(self.connection.execute(
            "SELECT mismatch_status FROM source_package_mismatch WHERE source_package_mismatch_id = ?",
            (mismatch_id,),
        ).fetchone()[0], "Review Required")


if __name__ == "__main__":
    unittest.main()
