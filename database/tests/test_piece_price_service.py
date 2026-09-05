from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from database.services.connection import connect
from database.services.audit import AuditContext
from database.services.commodity import activate_supplier_round
from database.services.piece_price import build_active_round_part_projection, get_piece_price_comparison
from database.services.integrity import run_health_gate
from database.services.rounds import create_quote_round, record_carry_forward, record_coverage_decision
from database.validation.generate_synthetic import PROFILES, populate


ROOT = Path(__file__).resolve().parents[2]


class PiecePriceProjectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / "database" / "tests")
        self.database_path = Path(self.temp.name) / "piece-price.db"
        populate(self.database_path, PROFILES["smoke"])
        self.connection = connect(self.database_path)
        self.event_id = self.connection.execute("SELECT event_id FROM sourcing_event").fetchone()[0]

    def tearDown(self) -> None:
        self.connection.close()
        self.temp.cleanup()

    def _audit(self, event_type: str, timestamp: str) -> AuditContext:
        return AuditContext(event_type, "buyer-1", "Buyer", timestamp,
                            "America/New_York", "coverage-test", "test",
                            "Automated Test", "TEST")

    def test_build_and_query_exact_piece_price_matrix(self) -> None:
        generation = build_active_round_part_projection(
            self.connection, event_id=self.event_id,
            evidence_cutoff_utc="2026-09-03T22:00:00Z",
        )
        comparison = get_piece_price_comparison(
            self.connection, generation_id=generation, event_id=self.event_id,
        )
        self.assertEqual(len(comparison), PROFILES["smoke"].parts)
        self.assertTrue(all(len(part.quotes) == PROFILES["smoke"].suppliers for part in comparison))
        self.assertTrue(all(quote.piece_price is not None for part in comparison for quote in part.quotes))
        self.assertTrue(all(quote.coverage_status == "Submitted" for part in comparison for quote in part.quotes))
        stored = self.connection.execute(
            "SELECT COUNT(DISTINCT build_manifest_hash), COUNT(*) FROM active_round_part_projection WHERE projection_generation_id = ?",
            (generation,),
        ).fetchone()
        self.assertEqual(tuple(stored), (1, PROFILES["smoke"].parts * PROFILES["smoke"].suppliers))

    def test_generations_are_append_only_and_explicitly_selected(self) -> None:
        first = build_active_round_part_projection(
            self.connection, event_id=self.event_id, evidence_cutoff_utc="2026-09-03T22:00:00Z",
        )
        second = build_active_round_part_projection(
            self.connection, event_id=self.event_id, evidence_cutoff_utc="2026-09-03T22:01:00Z",
        )
        self.assertNotEqual(first, second)
        total = self.connection.execute("SELECT COUNT(*) FROM active_round_part_projection").fetchone()[0]
        self.assertEqual(total, 2 * PROFILES["smoke"].parts * PROFILES["smoke"].suppliers)
        self.assertEqual(len(get_piece_price_comparison(self.connection, generation_id=first, event_id=self.event_id)), PROFILES["smoke"].parts)

    def test_confirmed_gst_target_is_optional_and_separate_from_quotes(self) -> None:
        package_id = self.connection.execute(
            "SELECT source_package_id FROM source_package WHERE event_id = ?", (self.event_id,)
        ).fetchone()[0]
        scope_id = self.connection.execute(
            "SELECT scope_version_id FROM scope_version WHERE source_package_id = ?", (package_id,)
        ).fetchone()[0]
        event_part_id = self.connection.execute(
            "SELECT event_part_id FROM event_part WHERE scope_version_id = ? ORDER BY submitted_part_number LIMIT 1",
            (scope_id,),
        ).fetchone()[0]
        self.connection.execute(
            """INSERT INTO gst_baseline
               (gst_baseline_id, source_package_id, scope_version_id,
                baseline_version, baseline_status, confirmed_by_user_id,
                confirmed_at_utc, recorded_at_utc)
               VALUES ('gst-price-target', ?, ?, 1, 'Confirmed', 'buyer-1',
                       '2026-09-03T22:00:00Z', '2026-09-03T22:00:00Z')""",
            (package_id, scope_id),
        )
        self.connection.execute(
            """INSERT INTO gst_part_value
               (gst_part_value_id, gst_baseline_id, event_part_id, program_year,
                measure_code, submitted_lexeme, decimal_coefficient,
                decimal_scale, governing_1e4, normalized_unit_id, currency_id,
                precision_status, recorded_at_utc)
               VALUES ('gst-target-value', 'gst-price-target', ?, 2028,
                       'Piece Price Target', '9.5000', '95000', 4, 95000,
                       'USD/PART', 'USD', 'Eligible', '2026-09-03T22:00:00Z')""",
            (event_part_id,),
        )
        generation = build_active_round_part_projection(
            self.connection, event_id=self.event_id, evidence_cutoff_utc="2026-09-03T22:00:00Z",
        )
        without_target = get_piece_price_comparison(
            self.connection, generation_id=generation, event_id=self.event_id,
        )[0]
        with_target = get_piece_price_comparison(
            self.connection, generation_id=generation, event_id=self.event_id, program_year=2028,
        )[0]
        self.assertIsNone(without_target.target_piece_price)
        self.assertEqual(str(with_target.target_piece_price), "9.5000")
        self.assertEqual(with_target.target_currency_id, "USD")
        self.assertNotEqual(with_target.target_piece_price, with_target.quotes[0].piece_price)

    def test_partial_round_preserves_explicit_coverage_states(self) -> None:
        supplier_id = self.connection.execute(
            "SELECT supplier_id FROM supplier_quote_round ORDER BY supplier_id LIMIT 1"
        ).fetchone()[0]
        prior_round = self.connection.execute(
            """SELECT quote_round_id FROM supplier_quote_round
               WHERE event_id = ? AND supplier_id = ? ORDER BY round_number DESC LIMIT 1""",
            (self.event_id, supplier_id),
        ).fetchone()[0]
        parts = self.connection.execute(
            "SELECT event_part_id FROM event_part ORDER BY submitted_part_number LIMIT 4"
        ).fetchall()
        partial_round = create_quote_round(
            self.connection, event_id=self.event_id, supplier_id=supplier_id,
            round_number=4, round_description="Partial supplier response",
            supplier_submission_date="2026-09-03", recorded_at_utc="2026-09-03T23:00:00Z",
            audit=self._audit("Quote Round Created", "2026-09-03T23:00:00Z"),
        )
        prior_observation = self.connection.execute(
            "SELECT observation_id FROM round_observation WHERE quote_round_id = ? AND event_part_id = ?",
            (prior_round, parts[0][0]),
        ).fetchone()[0]
        record_carry_forward(
            self.connection, target_quote_round_id=partial_round,
            event_part_id=parts[0][0], prior_observation_id=prior_observation,
            decided_by_user_id="buyer-1", decision_reason="Supplier reaffirmed prior price",
            recorded_at_utc="2026-09-03T23:00:01Z",
            audit=self._audit("Part Carried Forward", "2026-09-03T23:00:01Z"),
        )
        record_coverage_decision(
            self.connection, target_quote_round_id=partial_round,
            event_part_id=parts[1][0], decision_code="Not Quoted",
            decided_by_user_id="buyer-1", decision_reason="Supplier explicitly declined",
            recorded_at_utc="2026-09-03T23:00:02Z",
            audit=self._audit("Coverage Decision Recorded", "2026-09-03T23:00:02Z"),
        )
        record_coverage_decision(
            self.connection, target_quote_round_id=partial_round,
            event_part_id=parts[2][0], decision_code="Buyer Review Required",
            decided_by_user_id="buyer-1", decision_reason="Supplier response is ambiguous",
            recorded_at_utc="2026-09-03T23:00:03Z",
            audit=self._audit("Coverage Decision Recorded", "2026-09-03T23:00:03Z"),
        )
        transaction_id = self.connection.execute(
            "SELECT import_transaction_id FROM import_transaction LIMIT 1"
        ).fetchone()[0]
        self.connection.execute(
            """INSERT INTO round_batch VALUES
               ('partial-round-batch', ?, ?, 'buyer-1', '2026-09-03T23:00:03Z')""",
            (partial_round, transaction_id),
        )
        omitted_observation = self.connection.execute(
            "SELECT observation_id FROM round_observation WHERE quote_round_id = ? AND event_part_id = ?",
            (prior_round, parts[3][0]),
        ).fetchone()[0]
        self.connection.execute(
            """INSERT INTO round_observation VALUES
               ('explicit-omission', ?, 'partial-round-batch', ?, ?, 'Omitted',
                '2026-09-03T23:00:03Z')""",
            (partial_round, omitted_observation, parts[3][0]),
        )
        activate_supplier_round(
            self.connection, event_id=self.event_id, supplier_id=supplier_id,
            quote_round_id=partial_round, decided_by_user_id="buyer-1",
            decision_reason="Use partial response for analysis",
            audit=self._audit("Supplier Round Activated", "2026-09-03T23:00:04Z"),
        )
        generation = build_active_round_part_projection(
            self.connection, event_id=self.event_id, evidence_cutoff_utc="2026-09-03T23:00:05Z",
        )
        matrix = get_piece_price_comparison(
            self.connection, generation_id=generation, event_id=self.event_id,
        )
        coverage = {
            row.event_part_id: next(q for q in row.quotes if q.supplier_id == supplier_id)
            for row in matrix
        }
        self.assertEqual(coverage[parts[0][0]].coverage_status, "Carried Forward")
        self.assertIsNotNone(coverage[parts[0][0]].piece_price)
        self.assertEqual(coverage[parts[1][0]].coverage_status, "Not Quoted")
        self.assertIsNone(coverage[parts[1][0]].piece_price)
        self.assertEqual(coverage[parts[2][0]].coverage_status, "Buyer Review Required")
        self.assertIsNone(coverage[parts[2][0]].piece_price)
        self.assertEqual(coverage[parts[3][0]].coverage_status, "Omitted")
        self.assertIsNone(coverage[parts[3][0]].piece_price)

    def test_health_gate_independently_rebuilds_and_detects_projection_tampering(self) -> None:
        generation = build_active_round_part_projection(
            self.connection, event_id=self.event_id,
            evidence_cutoff_utc="2026-09-03T22:00:00Z",
        )
        self.assertNotIn(
            "PROJECTION_GENERATION_MISMATCH",
            {finding.code for finding in run_health_gate(self.connection)},
        )
        row = self.connection.execute(
            """SELECT supplier_id, event_part_id FROM active_round_part_projection
               WHERE projection_generation_id = ? LIMIT 1""", (generation,),
        ).fetchone()
        self.connection.execute(
            """UPDATE active_round_part_projection SET piece_price_coefficient = '999999'
               WHERE projection_generation_id = ? AND supplier_id = ?
                 AND event_part_id = ?""", (generation, row[0], row[1]),
        )
        self.assertIn(
            "PROJECTION_GENERATION_MISMATCH",
            {finding.code for finding in run_health_gate(self.connection)},
        )

    def test_historical_generation_reproduces_after_later_round_activation(self) -> None:
        generation = build_active_round_part_projection(
            self.connection, event_id=self.event_id,
            evidence_cutoff_utc="2026-09-03T22:00:00Z",
        )
        supplier_id = self.connection.execute(
            "SELECT supplier_id FROM v_active_supplier_round WHERE event_id = ? LIMIT 1",
            (self.event_id,),
        ).fetchone()[0]
        later_round = create_quote_round(
            self.connection, event_id=self.event_id, supplier_id=supplier_id,
            round_number=4, round_description="Later empty response",
            supplier_submission_date="2026-09-03",
            recorded_at_utc="2026-09-03T23:00:00Z",
            audit=self._audit("Quote Round Created", "2026-09-03T23:00:00Z"),
        )
        activate_supplier_round(
            self.connection, event_id=self.event_id, supplier_id=supplier_id,
            quote_round_id=later_round, decided_by_user_id="buyer-1",
            decision_reason="Later buyer selection",
            audit=self._audit("Supplier Round Activated", "2026-09-03T23:00:01Z"),
        )
        self.assertGreater(self.connection.execute(
            """SELECT COUNT(*) FROM active_round_part_projection
               WHERE projection_generation_id = ? AND observation_id IS NOT NULL""",
            (generation,),
        ).fetchone()[0], 0)
        self.assertNotIn(
            "PROJECTION_GENERATION_MISMATCH",
            {finding.code for finding in run_health_gate(self.connection)},
        )


if __name__ == "__main__":
    unittest.main()
