from __future__ import annotations

import sqlite3
import tempfile
import unittest
import uuid
from pathlib import Path

from database.services.audit import AuditContext, append_audit_event, verify_audit_chain
from database.services.commodity import (
    SupplierActivityChange,
    activate_supplier_round,
    append_supplier_activity,
)
from database.services.connection import connect, immediate_transaction
from database.services.decimals import ExactDecimal
from database.services.ids import uuid7
from database.validation.generate_synthetic import PROFILES, populate


ROOT = Path(__file__).resolve().parents[2]


def audit_context(event_type: str, when: str) -> AuditContext:
    return AuditContext(
        event_type=event_type,
        actor_user_id="test-user",
        effective_authority="Buyer",
        occurred_at_utc=when,
        display_timezone="America/New_York",
        workstation_session="test-session",
        application_version="test",
        action_method="Automated Test",
        reason_code="TEST",
        reason_text="Integrity verification",
    )


class ExactDecimalAndIdTests(unittest.TestCase):
    def test_exact_decimal_preserves_lexeme_and_excel_rounding(self) -> None:
        positive = ExactDecimal.parse("0012.34565")
        negative = ExactDecimal.parse("-12.34565")
        self.assertEqual(positive.submitted_lexeme, "0012.34565")
        self.assertEqual((positive.coefficient, positive.scale), ("1234565", 5))
        self.assertEqual(positive.governing_1e4(), 123457)
        self.assertEqual(negative.governing_1e4(), -123457)

    def test_exact_decimal_rejects_non_finite_and_uuid7_has_expected_layout(self) -> None:
        for invalid in ("", "NaN", "Infinity", "not-a-number"):
            with self.assertRaises(ValueError):
                ExactDecimal.parse(invalid)
        identifier = uuid.UUID(uuid7(timestamp_ms=1_800_000_000_000))
        self.assertEqual(identifier.version, 7)
        self.assertEqual(identifier.variant, uuid.RFC_4122)


class DatabaseServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / "database" / "tests")
        self.database_path = Path(self.temp.name) / "service.db"
        populate(self.database_path, PROFILES["smoke"])
        self.connection = connect(self.database_path)

    def tearDown(self) -> None:
        self.connection.close()
        self.temp.cleanup()

    def test_audit_events_are_chained_and_verified(self) -> None:
        with immediate_transaction(self.connection):
            append_audit_event(
                self.connection,
                audit_context("First", "2026-09-02T12:00:00.000001Z"),
                {"value": 1},
            )
        with immediate_transaction(self.connection):
            append_audit_event(
                self.connection,
                audit_context("Second", "2026-09-02T12:00:00.000002Z"),
                {"value": 2},
            )
        self.assertEqual(verify_audit_chain(self.connection), [])
        rows = self.connection.execute(
            "SELECT recorded_sequence, prior_event_hash, event_hash FROM audit_event ORDER BY recorded_sequence"
        ).fetchall()
        self.assertEqual([row[0] for row in rows], [1, 2])
        self.assertIsNone(rows[0][1])
        self.assertEqual(rows[1][1], rows[0][2])

    def test_round_activation_appends_decision_and_audit_atomically(self) -> None:
        event_id, supplier_id = self.connection.execute(
            "SELECT event_id, supplier_id FROM supplier_quote_round LIMIT 1"
        ).fetchone()
        round_one = self.connection.execute(
            """SELECT quote_round_id FROM supplier_quote_round
               WHERE event_id = ? AND supplier_id = ? AND round_number = 1""",
            (event_id, supplier_id),
        ).fetchone()[0]
        activation_id = activate_supplier_round(
            self.connection,
            event_id=event_id,
            supplier_id=supplier_id,
            quote_round_id=round_one,
            decided_by_user_id="test-user",
            decision_reason="Negotiation review selection",
            audit=audit_context("Supplier Round Activated", "2026-09-02T12:00:01Z"),
        )
        active = self.connection.execute(
            """SELECT round_activation_id, quote_round_id FROM v_active_supplier_round
               WHERE event_id = ? AND supplier_id = ?""",
            (event_id, supplier_id),
        ).fetchone()
        self.assertEqual(tuple(active), (activation_id, round_one))
        self.assertEqual(verify_audit_chain(self.connection), [])

    def test_invalid_round_activation_rolls_back_without_audit(self) -> None:
        with self.assertRaises(ValueError):
            activate_supplier_round(
                self.connection,
                event_id="wrong-event",
                supplier_id="wrong-supplier",
                quote_round_id="missing-round",
                decided_by_user_id="test-user",
                decision_reason="Test invalid input",
                audit=audit_context("Supplier Round Activated", "2026-09-02T12:00:01Z"),
            )
        self.assertEqual(self.connection.execute("SELECT COUNT(*) FROM audit_event").fetchone()[0], 0)

    def test_supplier_activity_requires_real_evidence_and_is_audited(self) -> None:
        observation = self.connection.execute(
            """SELECT observation_id, supplier_id, supplier_plant_id, context_id
               FROM pbd_observation LIMIT 1"""
        ).fetchone()
        activity_id = append_supplier_activity(
            self.connection,
            SupplierActivityChange(
                supplier_id=observation[1],
                supplier_plant_id=observation[2],
                event_id=observation[3],
                quote_round_id=None,
                event_part_id=None,
                activity_type="Correction",
                before_entity_type="PBD Observation",
                before_entity_id=observation[0],
                after_entity_type="PBD Observation",
                after_entity_id=observation[0],
                occurred_at_utc="2026-09-02T12:00:02Z",
            ),
            audit_context("Supplier Activity Appended", "2026-09-02T12:00:02Z"),
        )
        self.assertIsNotNone(
            self.connection.execute(
                "SELECT 1 FROM supplier_activity WHERE supplier_activity_id = ?", (activity_id,)
            ).fetchone()
        )
        self.assertEqual(verify_audit_chain(self.connection), [])


if __name__ == "__main__":
    unittest.main()
