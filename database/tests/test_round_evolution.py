from __future__ import annotations

import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from database.services.connection import connect
from database.services.round_evolution import calculate_round_evolution
from database.validation.generate_synthetic import PROFILES, populate


ROOT = Path(__file__).resolve().parents[2]


class RoundEvolutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / "database" / "tests")
        path = Path(self.temp.name) / "evolution.db"
        populate(path, PROFILES["smoke"])
        self.connection = connect(path)
        self.event_id, self.supplier_id = self.connection.execute(
            "SELECT event_id, supplier_id FROM supplier_quote_round ORDER BY supplier_id LIMIT 1"
        ).fetchone()
        package_id, scope_id = self.connection.execute(
            """SELECT sp.source_package_id, sv.scope_version_id FROM source_package sp
               JOIN scope_version sv ON sv.source_package_id = sp.source_package_id
               WHERE sp.event_id = ?""", (self.event_id,),
        ).fetchone()
        self.connection.execute(
            """INSERT INTO gst_baseline
               (gst_baseline_id, source_package_id, scope_version_id, baseline_version,
                baseline_status, confirmed_by_user_id, confirmed_at_utc, recorded_at_utc)
               VALUES ('evolution-gst', ?, ?, 1, 'Confirmed', 'buyer-1',
                       '2026-09-04T02:00:00Z', '2026-09-04T02:00:00Z')""",
            (package_id, scope_id),
        )
        for index, part in enumerate(self.connection.execute("SELECT event_part_id FROM event_part")):
            for year, volume in ((2027, 100 + index), (2028, 200 + index)):
                self.connection.execute(
                    """INSERT INTO gst_part_value
                       (gst_part_value_id, gst_baseline_id, event_part_id, program_year,
                        measure_code, submitted_lexeme, decimal_coefficient, decimal_scale,
                        normalized_unit_id, precision_status, recorded_at_utc)
                       VALUES (?, 'evolution-gst', ?, ?, 'FPV', ?, ?, 0,
                               'PART/YEAR', 'Eligible', '2026-09-04T02:00:00Z')""",
                    (f"evolution-fpv-{index}-{year}", part[0], year, str(volume), str(volume)),
                )

    def tearDown(self) -> None:
        self.connection.close()
        self.temp.cleanup()

    def test_common_scope_apv_drives_prior_and_cumulative_movement(self) -> None:
        result = calculate_round_evolution(
            self.connection, event_id=self.event_id, supplier_id=self.supplier_id,
            program_years=(2027, 2028),
            economic_age_cutoff_utc="2026-09-05T12:00:00Z",
        )
        self.assertEqual(len(result.rounds), 3)
        self.assertEqual(result.rounds[0].movement_from_initial_percent, Decimal(0))
        self.assertIsNone(result.rounds[0].movement_from_prior_percent)
        self.assertTrue(all(point.package_status == "Complete — Comparable for Award" for point in result.rounds))
        self.assertTrue(all(point.common_parts_to_initial == PROFILES["smoke"].parts for point in result.rounds))
        self.assertLess(result.rounds[1].movement_from_prior_percent, Decimal(0))
        self.assertLess(result.rounds[2].movement_from_initial_percent, result.rounds[1].movement_from_initial_percent)

    def test_buyer_selected_rounds_remain_explicit(self) -> None:
        rounds = self.connection.execute(
            """SELECT quote_round_id FROM supplier_quote_round
               WHERE event_id = ? AND supplier_id = ? ORDER BY round_number""",
            (self.event_id, self.supplier_id),
        ).fetchall()
        result = calculate_round_evolution(
            self.connection, event_id=self.event_id, supplier_id=self.supplier_id,
            program_years=(2028,), selected_round_ids=(rounds[0][0], rounds[2][0]),
            economic_age_cutoff_utc="2026-09-05T12:00:00Z",
        )
        self.assertEqual([point.round_number for point in result.rounds], [1, 3])
        self.assertEqual(result.rounds[1].common_parts_to_prior, PROFILES["smoke"].parts)

    def test_unassessed_quotes_age_out_of_new_round_evolution(self):
        result = calculate_round_evolution(
            self.connection, event_id=self.event_id, supplier_id=self.supplier_id,
            program_years=(2027, 2028), economic_age_cutoff_utc="2030-09-05T12:00:00Z",
        )
        self.assertTrue(all(point.total_quoted_apv == 0 for point in result.rounds))
        self.assertTrue(all(point.package_status != "Complete — Comparable for Award" for point in result.rounds))
        self.assertTrue(all(point.common_parts_to_initial == 0 for point in result.rounds))


if __name__ == "__main__":
    unittest.main()
