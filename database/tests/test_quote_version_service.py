from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from database.services.audit import AuditContext, verify_audit_chain
from database.services.connection import connect
from database.services.integrity import run_health_gate
from database.services.quote_versions import (
    bulk_confirm_requote_candidates,
    build_quote_version_movement,
    register_quote_version_candidate,
    review_quote_version_candidate,
)
from database.validation.generate_synthetic import PROFILES, populate


ROOT = Path(__file__).resolve().parents[2]


def audit(event_type: str, occurred_at_utc: str) -> AuditContext:
    return AuditContext(
        event_type=event_type, actor_user_id="buyer-version-review",
        effective_authority="Buyer", occurred_at_utc=occurred_at_utc,
        display_timezone="America/New_York", workstation_session="version-test",
        application_version="test", action_method="Automated Test",
        reason_code="QUOTE_VERSION_REVIEW",
    )


class QuoteVersionServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / "database" / "tests")
        self.database_path = Path(self.temp.name) / "quote-versions.db"
        populate(self.database_path, PROFILES["smoke"])
        self.connection = connect(self.database_path)

    def tearDown(self) -> None:
        self.connection.close()
        self.temp.cleanup()

    def test_candidate_stays_pending_until_explicit_activation(self) -> None:
        rows = self.connection.execute(
            """SELECT observation.observation_id
               FROM pbd_observation observation
               JOIN round_observation membership
                 ON membership.observation_id = observation.observation_id
               JOIN supplier_quote_round round
                 ON round.quote_round_id = membership.quote_round_id
               WHERE (round.event_id, observation.supplier_id, observation.part_id) = (
                   SELECT round2.event_id, observation2.supplier_id, observation2.part_id
                   FROM pbd_observation observation2
                   JOIN round_observation membership2
                     ON membership2.observation_id = observation2.observation_id
                   JOIN supplier_quote_round round2
                     ON round2.quote_round_id = membership2.quote_round_id
                   GROUP BY round2.event_id, observation2.supplier_id, observation2.part_id
                   HAVING COUNT(*) >= 2 LIMIT 1)
               ORDER BY round.round_number LIMIT 2"""
        ).fetchall()
        self.assertEqual(len(rows), 2)
        first = register_quote_version_candidate(
            self.connection, observation_id=rows[0][0], region_code="na",
            source_identity="workbook-hash-v1", detected_at_utc="2026-09-03T10:00:00Z",
            audit=audit("Quote Version Candidate Detected", "2026-09-03T10:00:00Z"),
        )
        self.assertEqual((first.version_number, first.review_status),
                         (1, "Pending Version Review"))
        duplicate = register_quote_version_candidate(
            self.connection, observation_id=rows[1][0], region_code="NA",
            source_identity="workbook-hash-v1", detected_at_utc="2026-09-03T10:01:00Z",
            audit=audit("Quote Version Candidate Detected", "2026-09-03T10:01:00Z"),
        )
        self.assertEqual(duplicate.candidate_id, first.candidate_id)
        review_quote_version_candidate(
            self.connection, candidate_id=first.candidate_id,
            review_decision="Confirm Active", buyer_classification="Initial Quote",
            buyer_note="Initial governing quote", reviewed_by_user_id="buyer-version-review",
            reviewed_at_utc="2026-09-03T10:02:00Z",
            audit=audit("Quote Version Reviewed", "2026-09-03T10:02:00Z"),
        )
        second = register_quote_version_candidate(
            self.connection, observation_id=rows[1][0], region_code="NA",
            source_identity="workbook-hash-v2", detected_at_utc="2026-09-03T10:03:00Z",
            audit=audit("Quote Version Candidate Detected", "2026-09-03T10:03:00Z"),
        )
        self.assertEqual(second.version_number, 2)
        active = self.connection.execute(
            "SELECT quote_version_candidate_id FROM v_active_quote_version"
        ).fetchone()[0]
        self.assertEqual(active, first.candidate_id)
        self.assertEqual(self.connection.execute(
            """SELECT review_status FROM v_quote_version_review
               WHERE quote_version_candidate_id = ?""", (second.candidate_id,),
        ).fetchone()[0], "Pending Version Review")
        review_quote_version_candidate(
            self.connection, candidate_id=second.candidate_id,
            review_decision="Confirm Active", buyer_classification="Final Offer",
            buyer_note="Confirmed latest submission",
            reviewed_by_user_id="buyer-version-review",
            reviewed_at_utc="2026-09-03T10:04:00Z",
            audit=audit("Quote Version Reviewed", "2026-09-03T10:04:00Z"),
        )
        active = self.connection.execute(
            """SELECT quote_version_candidate_id, version_number,
                      buyer_classification FROM v_active_quote_version"""
        ).fetchone()
        self.assertEqual(tuple(active), (second.candidate_id, 2, "Final Offer"))
        rule_id = self.connection.execute(
            "SELECT rule_version_id FROM rule_version ORDER BY rule_version_id LIMIT 1"
        ).fetchone()[0]
        movement = build_quote_version_movement(
            self.connection, candidate_id=second.candidate_id,
            calculation_rule_version_id=rule_id,
            generated_at_utc="2026-09-03T10:05:00Z",
            audit=audit("Quote Version Movement Built", "2026-09-03T10:05:00Z"),
        )
        self.assertEqual(movement.prior_candidate_id, first.candidate_id)
        self.assertEqual(movement.initial_candidate_id, first.candidate_id)
        self.assertEqual(movement.component_count, 20)
        piece_price = self.connection.execute(
            """SELECT comparison_status, delta_coefficient, delta_scale,
                      current_submitted_datum_id, baseline_submitted_datum_id
               FROM quote_version_component_movement
               WHERE quote_version_movement_generation_id = ?
                 AND comparison_basis = 'Prior Version'
                 AND field_code = 'PIECE_PRICE'""",
            (movement.generation_id,),
        ).fetchone()
        self.assertEqual(piece_price[0], "Comparable")
        self.assertIsNotNone(piece_price[1])
        self.assertIsNotNone(piece_price[3])
        self.assertIsNotNone(piece_price[4])
        missing = self.connection.execute(
            """SELECT comparison_status
               FROM quote_version_component_movement
               WHERE quote_version_movement_generation_id = ?
                 AND comparison_basis = 'Initial Version'
                 AND field_code = 'LABOR_DOLLARS'""",
            (movement.generation_id,),
        ).fetchone()[0]
        self.assertEqual(missing, "Missing Both")
        self.assertEqual(verify_audit_chain(self.connection), [])
        self.assertNotIn(
            "QUOTE_VERSION_HISTORY_MISMATCH",
            {finding.code for finding in run_health_gate(self.connection)},
        )
        self.assertNotIn(
            "QUOTE_VERSION_MOVEMENT_MISMATCH",
            {finding.code for finding in run_health_gate(self.connection)},
        )
        with self.assertRaisesRegex(Exception, "immutable"):
            self.connection.execute(
                "UPDATE quote_version_candidate SET version_number = 99"
            )
        with self.assertRaisesRegex(Exception, "immutable"):
            self.connection.execute(
                "UPDATE quote_version_component_movement SET field_code = 'CHANGED'"
            )
        self.connection.execute("DROP TRIGGER no_update_quote_version_component_movement")
        self.connection.execute(
            """UPDATE quote_version_component_movement SET delta_coefficient = '999'
               WHERE quote_version_movement_generation_id = ?
                 AND comparison_status = 'Comparable'""",
            (movement.generation_id,),
        )
        self.assertIn(
            "QUOTE_VERSION_MOVEMENT_MISMATCH",
            {finding.code for finding in run_health_gate(self.connection)},
        )

    def test_bulk_confirmation_is_atomic_for_one_supplier_event(self) -> None:
        rows = self.connection.execute(
            """SELECT observation.observation_id
               FROM pbd_observation observation
               JOIN round_observation membership
                 ON membership.observation_id = observation.observation_id
               JOIN supplier_quote_round round
                 ON round.quote_round_id = membership.quote_round_id
               WHERE (round.event_id, observation.supplier_id, round.round_number) = (
                   SELECT round2.event_id, observation2.supplier_id, round2.round_number
                   FROM pbd_observation observation2
                   JOIN round_observation membership2
                     ON membership2.observation_id = observation2.observation_id
                   JOIN supplier_quote_round round2
                     ON round2.quote_round_id = membership2.quote_round_id
                   GROUP BY round2.event_id, observation2.supplier_id, round2.round_number
                   HAVING COUNT(DISTINCT observation2.part_id) >= 2 LIMIT 1)
               ORDER BY observation.part_id LIMIT 2"""
        ).fetchall()
        candidates = tuple(register_quote_version_candidate(
            self.connection, observation_id=row[0], region_code="NA",
            source_identity=f"requote-package-{index}",
            detected_at_utc="2026-09-03T11:00:00Z",
            audit=audit("Quote Version Candidate Detected", "2026-09-03T11:00:00Z"),
        ).candidate_id for index, row in enumerate(rows))
        reviews = bulk_confirm_requote_candidates(
            self.connection, candidate_ids=candidates,
            buyer_classification="Requote", buyer_note="Complete supplier requote",
            reviewed_by_user_id="buyer-version-review",
            reviewed_at_utc="2026-09-03T11:01:00Z",
            audit=audit("Quote Version Bulk Confirmed", "2026-09-03T11:01:00Z"),
        )
        self.assertEqual(len(reviews), 2)
        self.assertEqual(self.connection.execute(
            "SELECT COUNT(*) FROM v_active_quote_version"
        ).fetchone()[0], 2)
        self.assertNotIn(
            "QUOTE_VERSION_HISTORY_MISMATCH",
            {finding.code for finding in run_health_gate(self.connection)},
        )


if __name__ == "__main__":
    unittest.main()
