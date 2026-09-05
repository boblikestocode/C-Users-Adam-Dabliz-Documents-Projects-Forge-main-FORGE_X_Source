from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from database.migration_runner import validate_database
from database.validation.benchmark_queries import benchmark
from database.validation.generate_synthetic import PROFILES, populate


ROOT = Path(__file__).resolve().parents[2]


class SyntheticValidationTests(unittest.TestCase):
    def test_smoke_population_reconciles_and_uses_active_latest_round(self) -> None:
        profile = PROFILES["smoke"]
        with tempfile.TemporaryDirectory(dir=ROOT / "database" / "tests") as temp_dir:
            database_path = Path(temp_dir) / "synthetic.db"
            counts = populate(database_path, profile)
            self.assertEqual(counts["observations"], profile.observations)
            self.assertEqual(counts["detail_rows"], profile.detail_rows)
            self.assertEqual(validate_database(database_path), [])

            connection = sqlite3.connect(database_path)
            try:
                observation_count = connection.execute(
                    "SELECT COUNT(*) FROM pbd_observation"
                ).fetchone()[0]
                activity_count = connection.execute(
                    "SELECT COUNT(*) FROM supplier_activity"
                ).fetchone()[0]
                active_round_count = connection.execute(
                    "SELECT COUNT(*) FROM v_active_supplier_round"
                ).fetchone()[0]
                reconciliation = connection.execute(
                    """SELECT occurrence_count, committed_count, pending_count
                       FROM v_source_tab_reconciliation"""
                ).fetchone()
                self.assertEqual(observation_count, profile.observations)
                self.assertEqual(activity_count, profile.observations)
                self.assertEqual(active_round_count, profile.suppliers)
                self.assertEqual(reconciliation, (profile.observations, profile.observations, 0))
            finally:
                connection.close()

            results = benchmark(database_path, iterations=3)
            self.assertEqual(len(results), 15)
            self.assertTrue(all(result.rows >= 0 for result in results))
            self.assertTrue(all(result.p95_ms >= 0 for result in results))
            self.assertTrue(all(len(result.plan_fingerprint) == 64 for result in results))
            self.assertTrue(all(result.plan_steps for result in results))
            self.assertTrue(all(result.within_target for result in results))


if __name__ == "__main__":
    unittest.main()
