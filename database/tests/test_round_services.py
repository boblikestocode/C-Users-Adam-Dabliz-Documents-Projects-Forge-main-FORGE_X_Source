from __future__ import annotations

import tempfile
import unittest
import sqlite3
from decimal import Decimal
from pathlib import Path

from database.services.audit import AuditContext, verify_audit_chain
from database.services.connection import connect
from database.services.commodity import SupplierActivityChange, append_supplier_activity
from database.services.commodity import activate_supplier_round
from database.services.piece_price import projection_source_rows
from database.services.rounds import (
    RoundObservationInput, compare_rounds, create_quote_round,
    record_carry_forward, register_round_batch, resolve_round_conflict,
)
from database.validation.generate_synthetic import PROFILES, populate


ROOT = Path(__file__).resolve().parents[2]


def audit(event_type: str, timestamp: str) -> AuditContext:
    return AuditContext(
        event_type=event_type,
        actor_user_id="buyer-1",
        effective_authority="Buyer",
        occurred_at_utc=timestamp,
        display_timezone="America/New_York",
        workstation_session="round-test",
        application_version="test",
        action_method="Automated Test",
        reason_code="TEST",
    )


class RoundServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / "database" / "tests")
        self.database_path = Path(self.temp.name) / "rounds.db"
        populate(self.database_path, PROFILES["smoke"])
        self.connection = connect(self.database_path)
        self.event_id, self.supplier_id = self.connection.execute(
            "SELECT event_id, supplier_id FROM supplier_quote_round ORDER BY supplier_id, round_number LIMIT 1"
        ).fetchone()

    def tearDown(self) -> None:
        self.connection.close()
        self.temp.cleanup()

    def _round(self, number: int) -> str:
        return self.connection.execute(
            """SELECT quote_round_id FROM supplier_quote_round
               WHERE event_id = ? AND supplier_id = ? AND round_number = ?""",
            (self.event_id, self.supplier_id, number),
        ).fetchone()[0]

    def _insert_pce_observation(self) -> str:
        engine_id = self.connection.execute(
            "SELECT engine_version_id FROM engine_version LIMIT 1"
        ).fetchone()[0]
        rule_id = self.connection.execute(
            "SELECT rule_version_id FROM rule_version WHERE rule_domain = 'extraction' LIMIT 1"
        ).fetchone()[0]
        self.connection.execute(
            """INSERT INTO import_session
               (import_session_id, import_context_type, initiated_by_user_id,
                engine_version_id, status, started_at_utc)
               VALUES ('pce-session', 'PCE Should Cost', 'buyer-1', ?,
                       'Staging', '2026-09-03T11:58:00Z')""", (engine_id,),
        )
        self.connection.execute(
            """INSERT INTO import_transaction
               (import_transaction_id, import_session_id, transaction_sequence,
                status, started_at_utc)
               VALUES ('pce-transaction', 'pce-session', 1, 'Started',
                       '2026-09-03T11:58:00Z')"""
        )
        self.connection.execute(
            """INSERT INTO source_workbook
               (workbook_id, import_transaction_id, submitted_filename,
                file_size_bytes, file_hash_sha256, recorded_at_utc)
               VALUES ('pce-workbook', 'pce-transaction', 'pce.xlsx', 1, ?,
                       '2026-09-03T11:58:00Z')""", ("c" * 64,),
        )
        self.connection.execute(
            """INSERT INTO source_worksheet
               (worksheet_id, workbook_id, worksheet_ordinal, submitted_name,
                visibility, sheet_fingerprint, recorded_at_utc)
               VALUES ('pce-worksheet', 'pce-workbook', 0, 'PCE', 'Visible',
                       'pce-sheet', '2026-09-03T11:58:00Z')"""
        )
        self.connection.execute(
            """INSERT INTO source_occurrence
               (occurrence_id, worksheet_id, detection_rule_version_id,
                detection_result, terminal_status, recorded_at_utc)
               VALUES ('pce-occurrence', 'pce-worksheet', ?, 'PCE', 'Committed',
                       '2026-09-03T11:59:00Z')""", (rule_id,),
        )
        self.connection.execute(
            """INSERT INTO staged_observation
               (staged_observation_id, occurrence_id, import_context_type,
                status, blocking_issue_count, recorded_at_utc)
               VALUES ('pce-staged', 'pce-occurrence', 'PCE Should Cost',
                       'Committed', 0, '2026-09-03T11:59:00Z')"""
        )
        part_id = self.connection.execute(
            """SELECT event_scope.part_id FROM round_observation membership
               JOIN event_part event_scope
                 ON event_scope.event_part_id = membership.event_part_id
               WHERE membership.quote_round_id = ? LIMIT 1""", (self._round(1),),
        ).fetchone()[0]
        self.connection.execute(
            """INSERT INTO pbd_observation
               (observation_id, staged_observation_id, occurrence_id,
                observation_context, supplier_id, part_id,
                submitted_supplier_name, submitted_part_number,
                submitted_part_description,
                economic_date_precision, structure_category, recorded_at_utc)
               VALUES ('pce-observation', 'pce-staged', 'pce-occurrence',
                       'PCE Should Cost', ?, ?, 'Should Cost', 'PCE-PART',
                       'PCE Test Part',
                       'Unknown', 'Valid Aggregate', '2026-09-03T11:59:00Z')""",
            (self.supplier_id, part_id),
        )
        return "pce-observation"

    def test_round_comparison_classifies_all_price_changes(self) -> None:
        comparison = compare_rounds(
            self.connection,
            prior_round_id=self._round(1),
            current_round_id=self._round(2),
        )
        self.assertEqual(comparison.changed, PROFILES["smoke"].parts)
        self.assertEqual((comparison.unchanged, comparison.added, comparison.omitted), (0, 0, 0))
        first = comparison.parts[0]
        self.assertEqual(first.prior_piece_price - first.current_piece_price, Decimal("0.0125"))

    def test_create_round_and_carry_forward_preserve_prior_observation(self) -> None:
        round_four = create_quote_round(
            self.connection,
            event_id=self.event_id,
            supplier_id=self.supplier_id,
            round_number=4,
            round_description="Partial Final",
            supplier_submission_date="2026-09-03",
            recorded_at_utc="2026-09-03T12:00:00Z",
            audit=audit("Quote Round Created", "2026-09-03T12:00:00Z"),
        )
        event_part_id, prior_observation_id = self.connection.execute(
            """SELECT ro.event_part_id, ro.observation_id
               FROM round_observation ro WHERE ro.quote_round_id = ? LIMIT 1""",
            (self._round(3),),
        ).fetchone()
        decision_id = record_carry_forward(
            self.connection,
            target_quote_round_id=round_four,
            event_part_id=event_part_id,
            prior_observation_id=prior_observation_id,
            decided_by_user_id="buyer-1",
            decision_reason="Supplier reaffirmed prior commercial price",
            recorded_at_utc="2026-09-03T12:00:01Z",
            audit=audit("Part Carried Forward", "2026-09-03T12:00:01Z"),
        )
        stored = self.connection.execute(
            """SELECT prior_observation_id, decision_code FROM carry_forward_decision
               WHERE carry_forward_decision_id = ?""",
            (decision_id,),
        ).fetchone()
        self.assertEqual(tuple(stored), (prior_observation_id, "Carry Forward"))
        self.assertEqual(verify_audit_chain(self.connection), [])

    def test_carry_forward_rejects_part_already_submitted(self) -> None:
        target_round = self._round(3)
        event_part_id, prior_observation_id = self.connection.execute(
            """SELECT ro.event_part_id, ro.observation_id
               FROM round_observation ro WHERE ro.quote_round_id = ? LIMIT 1""",
            (self._round(2),),
        ).fetchone()
        with self.assertRaisesRegex(ValueError, "already submitted"):
            record_carry_forward(
                self.connection,
                target_quote_round_id=target_round,
                event_part_id=event_part_id,
                prior_observation_id=prior_observation_id,
                decided_by_user_id="buyer-1",
                decision_reason="Invalid duplicate carry-forward",
                recorded_at_utc="2026-09-03T12:00:00Z",
                audit=audit("Part Carried Forward", "2026-09-03T12:00:00Z"),
            )
        self.assertEqual(self.connection.execute("SELECT COUNT(*) FROM audit_event").fetchone()[0], 0)

    def test_pce_evidence_cannot_enter_rounds_or_supplier_behavior(self) -> None:
        observation_id = self._insert_pce_observation()
        round_id = self._round(1)
        batch_id = self.connection.execute(
            "SELECT round_batch_id FROM round_batch WHERE quote_round_id = ? LIMIT 1",
            (round_id,),
        ).fetchone()[0]
        event_part_id = self.connection.execute(
            "SELECT event_part_id FROM round_observation WHERE quote_round_id = ? LIMIT 1",
            (round_id,),
        ).fetchone()[0]
        with self.assertRaisesRegex(sqlite3.IntegrityError, "matching sourcing-event"):
            self.connection.execute(
                """INSERT INTO round_observation VALUES
                   ('pce-membership', ?, ?, ?, ?, 'Submitted',
                    '2026-09-03T12:00:00Z')""",
                (round_id, batch_id, observation_id, event_part_id),
            )
        with self.assertRaisesRegex(ValueError, "cannot enter supplier behavior"):
            append_supplier_activity(
                self.connection,
                SupplierActivityChange(
                    self.supplier_id, None, None, None, None, "Addition",
                    None, None, "PBD Observation", observation_id,
                    "2026-09-03T12:00:00Z",
                ),
                audit("Supplier Activity Added", "2026-09-03T12:00:00Z"),
            )

    def test_same_part_conflict_is_non_governing_until_buyer_resolution(self) -> None:
        target_round = self._round(1)
        event_part_id, replacement_observation = self.connection.execute(
            """SELECT later.event_part_id, later.observation_id
               FROM round_observation later
               WHERE later.quote_round_id = ? ORDER BY later.event_part_id LIMIT 1""",
            (self._round(2),),
        ).fetchone()
        prior_observation = self.connection.execute(
            """SELECT observation_id FROM round_observation
               WHERE quote_round_id = ? AND event_part_id = ?""",
            (target_round, event_part_id),
        ).fetchone()[0]
        session_id = self.connection.execute(
            "SELECT import_session_id FROM import_session LIMIT 1"
        ).fetchone()[0]
        transaction_id = "conflict-import-transaction"
        self.connection.execute(
            """INSERT INTO import_transaction
               (import_transaction_id, import_session_id, transaction_sequence,
                status, started_at_utc) VALUES (?, ?, 2, 'Started', ?)""",
            (transaction_id, session_id, "2026-09-03T12:00:00Z"),
        )
        _, conflicts = register_round_batch(
            self.connection, quote_round_id=target_round,
            import_transaction_id=transaction_id,
            observations=(RoundObservationInput(replacement_observation, event_part_id),),
            confirmed_by_user_id="buyer-1",
            confirmed_at_utc="2026-09-03T12:00:01Z",
            audit=audit("Round Batch Registered", "2026-09-03T12:00:01Z"),
        )
        self.assertEqual(len(conflicts), 1)
        activate_supplier_round(
            self.connection, event_id=self.event_id, supplier_id=self.supplier_id,
            quote_round_id=target_round, decided_by_user_id="buyer-1",
            decision_reason="Test unresolved conflict behavior",
            audit=audit("Round Activated", "2026-09-03T12:00:02Z"),
        )
        unresolved = [row for row in projection_source_rows(
            self.connection, self.event_id, "2026-09-03T12:00:02Z"
        ) if row[1] == self.supplier_id and row[2] == event_part_id][0]
        self.assertEqual((unresolved[4], unresolved[5]), (None, "Conflict"))
        decision_id = resolve_round_conflict(
            self.connection, round_conflict_id=conflicts[0],
            decision_code="New Replaces Earlier Within Round",
            decided_by_user_id="buyer-1",
            decision_reason="Buyer confirmed the later same-round submission",
            recorded_at_utc="2026-09-03T12:00:03Z",
            audit=audit("Round Conflict Resolved", "2026-09-03T12:00:03Z"),
        )
        resolved = [row for row in projection_source_rows(
            self.connection, self.event_id, "2026-09-03T12:00:03Z"
        ) if row[1] == self.supplier_id and row[2] == event_part_id][0]
        self.assertEqual((resolved[4], resolved[5]), (replacement_observation, "Submitted"))
        self.assertNotEqual(resolved[4], prior_observation)
        self.assertEqual(self.connection.execute(
            """SELECT round_conflict_decision_id
               FROM v_current_round_conflict_decision WHERE round_conflict_id = ?""",
            (conflicts[0],),
        ).fetchone()[0], decision_id)
        self.assertEqual(verify_audit_chain(self.connection), [])


if __name__ == "__main__":
    unittest.main()
