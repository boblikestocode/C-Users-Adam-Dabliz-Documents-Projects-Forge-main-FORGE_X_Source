from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from database.migration_runner import apply_migrations
from database.services.audit import AuditContext
from database.services.connection import connect
from database.services.events import (
    correct_source_package_number,
    create_sourcing_event,
    standardized_event_name,
    transition_sourcing_event,
)
from database.services.integrity import run_health_gate
from database.services.search import search_source_rows
from database.services.summary_projections import event_summary_source_rows


ROOT = Path(__file__).resolve().parents[2]


def audit(kind: str, timestamp: str) -> AuditContext:
    return AuditContext(kind, "buyer", "Buyer", timestamp, "America/New_York",
                        "event-test", "test", "Automated Test", "EVENT_LIFECYCLE")


class EventServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / "database" / "tests")
        self.path = Path(self.temp.name) / "event.db"
        apply_migrations(self.path)
        self.connection = connect(self.path)

    def tearDown(self) -> None:
        self.connection.close()
        self.temp.cleanup()

    def test_event_identity_is_immutable_and_status_transitions_append(self) -> None:
        event_id = create_sourcing_event(
            self.connection, commodity_id="commodity", readable_name="New Event",
            buyer_code_id="BUYER-1", primary_buyer_user_id="buyer",
            created_at_utc="2026-09-04T10:00:00Z",
            audit=audit("Sourcing Event Created", "2026-09-04T10:00:00Z"),
        )
        transition_sourcing_event(
            self.connection, event_id=event_id, target_status="Active",
            transition_reason="Buyer started event", decided_by_user_id="buyer",
            recorded_at_utc="2026-09-04T10:01:00Z",
            audit=audit("Sourcing Event Activated", "2026-09-04T10:01:00Z"),
        )
        self.assertEqual(self.connection.execute(
            "SELECT event_status FROM v_current_sourcing_event_status WHERE event_id = ?",
            (event_id,),
        ).fetchone()[0], "Active")
        self.assertEqual(self.connection.execute(
            "SELECT COUNT(*) FROM sourcing_event_status_event WHERE event_id = ?",
            (event_id,),
        ).fetchone()[0], 2)
        with self.assertRaisesRegex(Exception, "sourcing_event is immutable"):
            self.connection.execute(
                "UPDATE sourcing_event SET readable_name = 'Changed' WHERE event_id = ?",
                (event_id,),
            )

    def test_invalid_skip_and_finalization_without_snapshot_are_rejected(self) -> None:
        event_id = create_sourcing_event(
            self.connection, commodity_id="commodity", readable_name="New Event",
            buyer_code_id="BUYER-1", primary_buyer_user_id="buyer",
            created_at_utc="2026-09-04T10:00:00Z",
            audit=audit("Sourcing Event Created", "2026-09-04T10:00:00Z"),
        )
        with self.assertRaisesRegex(ValueError, "Setup -> Closed"):
            transition_sourcing_event(
                self.connection, event_id=event_id, target_status="Closed",
                transition_reason="Skip", decided_by_user_id="buyer",
                recorded_at_utc="2026-09-04T10:01:00Z",
                audit=audit("Invalid", "2026-09-04T10:01:00Z"),
            )
        transition_sourcing_event(
            self.connection, event_id=event_id, target_status="Active",
            transition_reason="Start", decided_by_user_id="buyer",
            recorded_at_utc="2026-09-04T10:01:00Z",
            audit=audit("Sourcing Event Activated", "2026-09-04T10:01:00Z"),
        )
        with self.assertRaisesRegex(ValueError, "finalized analysis snapshot"):
            transition_sourcing_event(
                self.connection, event_id=event_id, target_status="Finalized",
                transition_reason="Premature", decided_by_user_id="buyer",
                recorded_at_utc="2026-09-04T10:02:00Z",
                audit=audit("Invalid", "2026-09-04T10:02:00Z"),
            )

    def test_health_detects_directly_inserted_invalid_transition(self) -> None:
        event_id = create_sourcing_event(
            self.connection, commodity_id="commodity", readable_name="New Event",
            buyer_code_id="BUYER-1", primary_buyer_user_id="buyer",
            created_at_utc="2026-09-04T10:00:00Z",
            audit=audit("Sourcing Event Created", "2026-09-04T10:00:00Z"),
        )
        initial_id = self.connection.execute(
            "SELECT sourcing_event_status_event_id FROM v_current_sourcing_event_status WHERE event_id = ?",
            (event_id,),
        ).fetchone()[0]
        self.connection.execute(
            """INSERT INTO sourcing_event_status_event VALUES
               ('bad-status', ?, 'Closed', 'invalid', 'buyer', ?,
                '2026-09-04T10:01:00Z')""", (event_id, initial_id),
        )
        self.assertIn("EVENT_STATUS_TRANSITION_INVALID",
                      {finding.code for finding in run_health_gate(self.connection)})

    def test_source_package_number_correction_is_append_only_and_audited(self) -> None:
        event_id = create_sourcing_event(
            self.connection, commodity_id="commodity", readable_name="New Event",
            buyer_code_id="BUYER-1", primary_buyer_user_id="buyer",
            created_at_utc="2026-09-04T10:00:00Z",
            audit=audit("Sourcing Event Created", "2026-09-04T10:00:00Z"),
        )
        self.connection.execute(
            "INSERT INTO source_package VALUES (?, ?, ?, ?, 'Primary', ?)",
            ("package-1", event_id, "SP100", "SP100", "2026-09-04T10:01:00Z"),
        )
        first = correct_source_package_number(
            self.connection, source_package_id="package-1",
            corrected_package_number="SP 200", correction_reason="Buyer entry correction",
            corrected_by_user_id="buyer", recorded_at_utc="2026-09-04T10:02:00Z",
            audit=audit("Source Package Corrected", "2026-09-04T10:02:00Z"),
        )
        second = correct_source_package_number(
            self.connection, source_package_id="package-1",
            corrected_package_number="SP300", correction_reason="Final verified number",
            corrected_by_user_id="buyer", recorded_at_utc="2026-09-04T10:03:00Z",
            audit=audit("Source Package Corrected", "2026-09-04T10:03:00Z"),
        )
        current = self.connection.execute(
            """SELECT normalized_package_number, displayed_package_number
               FROM v_current_source_package_identity WHERE source_package_id = 'package-1'"""
        ).fetchone()
        self.assertEqual(tuple(current), ("SP300", "SP300"))
        history = self.connection.execute(
            """SELECT source_package_number_correction_id, original_displayed_package_number,
                      corrected_displayed_package_number, supersedes_correction_id
               FROM source_package_number_correction ORDER BY recorded_at_utc"""
        ).fetchall()
        self.assertEqual([tuple(row) for row in history], [
            (first, "SP100", "SP 200", None),
            (second, "SP 200", "SP300", first),
        ])
        with self.assertRaisesRegex(Exception, "append-only"):
            self.connection.execute(
                "UPDATE source_package_number_correction SET correction_reason = 'changed'"
            )
        self.assertEqual(self.connection.execute(
            "SELECT COUNT(*) FROM audit_event WHERE event_type = 'Source Package Corrected'"
        ).fetchone()[0], 2)
        before = event_summary_source_rows(self.connection, "2026-09-04T10:01:30Z")
        after = event_summary_source_rows(self.connection, "2026-09-04T10:03:30Z")
        self.assertEqual(before[0][1], "SP100")
        self.assertEqual(after[0][1], "SP300")
        before_search = search_source_rows(self.connection, "2026-09-04T10:01:30Z")
        after_search = search_source_rows(self.connection, "2026-09-04T10:03:30Z")
        self.assertIn("SP100", {row[3] for row in before_search})
        self.assertIn("SP300", {row[3] for row in after_search})
        self.assertEqual(
            standardized_event_name(self.connection, event_id=event_id,
                                    evidence_cutoff_utc="2026-09-04T10:01:30Z"),
            "SP100 - New Event",
        )
        self.assertEqual(
            standardized_event_name(self.connection, event_id=event_id,
                                    evidence_cutoff_utc="2026-09-04T10:03:30Z"),
            "SP300 - New Event",
        )


if __name__ == "__main__":
    unittest.main()
