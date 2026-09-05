from __future__ import annotations

import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from database.services.commercial_metrics import calculate_piece_price_metrics
from database.services.connection import connect
from database.services.piece_price import build_active_round_part_projection, get_piece_price_comparison
from database.validation.generate_synthetic import PROFILES, populate


ROOT = Path(__file__).resolve().parents[2]


class CommercialMetricTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / "database" / "tests")
        path = Path(self.temp.name) / "metrics.db"
        populate(path, PROFILES["smoke"])
        self.connection = connect(path)
        self.event_id = self.connection.execute("SELECT event_id FROM sourcing_event").fetchone()[0]

    def tearDown(self) -> None:
        self.connection.close()
        self.temp.cleanup()

    def _comparison(self, year: int | None = None):
        generation = build_active_round_part_projection(
            self.connection, event_id=self.event_id, evidence_cutoff_utc="2026-09-04T00:00:00Z"
        )
        return get_piece_price_comparison(
            self.connection, generation_id=generation, event_id=self.event_id, program_year=year
        )

    def test_rank_and_peer_gap_need_no_assumed_baseline(self) -> None:
        metrics = calculate_piece_price_metrics(
            self.connection, event_id=self.event_id, comparison=self._comparison()
        )[0]
        self.assertEqual(metrics.incumbency_status, "Not Defined")
        self.assertIsNone(metrics.incumbent_supplier_id)
        ranked = sorted(metrics.suppliers, key=lambda item: item.competitive_rank)
        self.assertEqual([item.competitive_rank for item in ranked], [1, 2, 3])
        self.assertEqual(ranked[0].peer_low_gap, Decimal("0.0000"))
        self.assertTrue(all(item.target_gap is None for item in ranked))
        self.assertTrue(all(item.incumbent_savings_per_piece is None for item in ranked))

    def test_explicit_incumbent_target_and_fpv_enable_supported_savings(self) -> None:
        comparison = self._comparison()
        part = comparison[0]
        incumbent_quote = max(part.quotes, key=lambda quote: quote.piece_price)
        package_id, scope_id = self.connection.execute(
            """SELECT sp.source_package_id, sv.scope_version_id
               FROM source_package sp JOIN scope_version sv ON sv.source_package_id = sp.source_package_id
               WHERE sp.event_id = ?""", (self.event_id,)
        ).fetchone()
        self.connection.execute(
            """INSERT INTO incumbency_assignment VALUES
               ('metric-incumbent', ?, ?, ?, 'Incumbent', NULL, NULL,
                'buyer-1', '2026-09-04T00:00:00Z', NULL)""",
            (self.event_id, part.event_part_id, incumbent_quote.supplier_id),
        )
        self.connection.execute(
            """INSERT INTO gst_baseline
               (gst_baseline_id, source_package_id, scope_version_id, baseline_version,
                baseline_status, confirmed_by_user_id, confirmed_at_utc, recorded_at_utc)
               VALUES ('metric-gst', ?, ?, 1, 'Confirmed', 'buyer-1',
                       '2026-09-04T00:00:00Z', '2026-09-04T00:00:00Z')""",
            (package_id, scope_id),
        )
        values = (
            ('metric-target', 'Piece Price Target', '90000', 4, 'USD/PART', 'USD'),
            ('metric-fpv', 'FPV', '1000', 0, 'PART/YEAR', None),
        )
        for value_id, code, coefficient, scale, unit, currency in values:
            self.connection.execute(
                """INSERT INTO gst_part_value
                   (gst_part_value_id, gst_baseline_id, event_part_id, program_year,
                    measure_code, submitted_lexeme, decimal_coefficient, decimal_scale,
                    normalized_unit_id, currency_id, precision_status, recorded_at_utc)
                   VALUES (?, 'metric-gst', ?, 2028, ?, ?, ?, ?, ?, ?, 'Eligible',
                           '2026-09-04T00:00:00Z')""",
                (value_id, part.event_part_id, code, coefficient, coefficient, scale, unit, currency),
            )
        comparison = self._comparison(2028)
        metrics = calculate_piece_price_metrics(
            self.connection, event_id=self.event_id, comparison=comparison, program_year=2028
        )[0]
        self.assertEqual(metrics.incumbency_status, "Confirmed")
        self.assertEqual(metrics.annual_fpv, Decimal("1000"))
        lowest = min(metrics.suppliers, key=lambda item: item.piece_price)
        self.assertEqual(lowest.target_gap, lowest.piece_price - Decimal("9.0000"))
        self.assertEqual(lowest.incumbent_savings_per_piece, incumbent_quote.piece_price - lowest.piece_price)
        self.assertEqual(lowest.annual_incumbent_savings, lowest.incumbent_savings_per_piece * Decimal("1000"))


if __name__ == "__main__":
    unittest.main()
