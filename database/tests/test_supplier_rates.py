from __future__ import annotations

import hashlib
import unittest
from decimal import Decimal

from database.tests import test_profile_service as fixtures
from database.services.decimals import ExactDecimal
from database.services.ids import uuid7
from database.services.supplier_rates import (
    confirm_supplier_rate_interpretation, derive_supplier_rate_distributions,
    build_supplier_rate_distributions,
    rate_statistics,
)
from database.services.integrity import verify_supplier_rate_distributions
from database.services.quote_versions import register_quote_version_candidate
from database.services.economic_age import confirm_economic_dates


class SupplierRateTests(unittest.TestCase):
    setUp = fixtures.SupplierProfileServiceTests.setUp
    tearDown = fixtures.SupplierProfileServiceTests.tearDown

    def _observations(self):
        return [row[0] for row in self.connection.execute(
            "SELECT observation_id FROM pbd_observation WHERE supplier_id = ? ORDER BY observation_id",
            (self.supplier_id,),
        )]

    def _rate(self, observation, value, category="LABOR_RATE", region="US", unit="USD/HOUR", basis="DIRECT_LABOR"):
        worksheet = self.connection.execute(
            """SELECT occurrence.worksheet_id FROM pbd_observation observation
               JOIN source_occurrence occurrence USING (occurrence_id)
               WHERE observation.observation_id = ?""", (observation,),
        ).fetchone()[0]
        source, datum = uuid7(), uuid7()
        number = ExactDecimal.parse(value)
        self.connection.execute(
            "INSERT INTO source_datum VALUES (?, ?, 'Z100', ?, NULL, ?, ?, ?)",
            (source, worksheet, value, value,
             hashlib.sha256(f"{worksheet}|Z100|{value}|None".encode()).hexdigest(), "2026-09-03T12:00:00Z"),
        )
        self.connection.execute(
            """INSERT INTO submitted_datum VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, ?, 'USD', 'Eligible', ?)""",
            (datum, observation, category, source, value, number.coefficient, number.scale,
             number.governing_1e4(), unit, "2026-09-03T12:00:00Z"),
        )
        self._confirm(datum, region, category, basis)
        return datum

    def _confirm(self, datum, region="US", category="LABOR_RATE", basis="DIRECT_LABOR", time="2026-09-03T13:00:00Z"):
        return confirm_supplier_rate_interpretation(
            self.connection, submitted_datum_id=datum, region_code=region,
            rate_category_code=category, comparison_basis_code=basis,
            confirmed_by_user_id="buyer-1", confirmation_reason="Confirmed source rate basis",
            recorded_at_utc=time, audit=fixtures.audit(),
        )

    def _derive(self, region="US", cutoff="2026-09-04T16:00:00Z"):
        return derive_supplier_rate_distributions(
            self.connection, supplier_id=self.supplier_id, commodity_id=self.commodity_id,
            region_code=region, evidence_cutoff_utc=cutoff,
        )

    def test_exact_rates_keep_units_and_categories_separate(self):
        observations = self._observations()
        self._rate(observations[0], "10.0001")
        self._rate(observations[1], "20.0002")
        self._rate(observations[2], "90", unit="USD/DAY")
        self._rate(observations[3], "5", category="PROFIT_PERCENT", unit="PERCENT", basis="VALUE_ADDED")
        groups = self._derive()["distributions"]
        self.assertEqual(len(groups), 3)
        hourly = next(item for item in groups if item["normalized_unit_id"] == "USD/HOUR")
        self.assertEqual(hourly["range"], "10.0001")
        self.assertEqual(hourly["raw_mean"], "15.0002")
        self.assertEqual(hourly["quote_count"], 2)
        self.assertEqual(hourly["independent_event_count"], 1)
        self.assertEqual(hourly["status"], "Insufficient Independent Events")
        self.assertTrue(all(item["source_datum_id"] for item in hourly["evidence"]))

    def test_regional_conflicts_and_stale_dates_are_excluded(self):
        observations = self._observations()
        self._rate(observations[0], "10", region="US")
        self._rate(observations[1], "20", region="MX")
        self._rate(observations[2], "30", region="US")
        confirm_economic_dates(
            self.connection, observation_ids=(observations[2],), confirmed_economic_date="2020-01-01",
            date_precision="Day", confirmed_by_user_id="buyer-1", confirmation_reason="Source date",
            recorded_at_utc="2026-09-03T14:00:00Z", audit=fixtures.audit(),
        )
        self.assertEqual(self._derive()["distributions"][0]["quote_count"], 1)
        register_quote_version_candidate(
            self.connection, observation_id=observations[0], region_code="MX",
            source_identity=observations[0], detected_at_utc="2026-09-04T12:00:00Z", audit=fixtures.audit(),
        )
        self.assertEqual(self._derive()["distributions"], [])
        self.assertEqual({item["reason"] for item in self._derive()["excluded_evidence"]},
                         {"REGION_NOT_ALIGNED", "OLDER_THAN_THREE_YEARS"})

    def test_versioned_interpretations_leave_prior_distribution_reproducible(self):
        datum = self._rate(self._observations()[0], "10")
        run = build_supplier_rate_distributions(
            self.connection, supplier_id=self.supplier_id, commodity_id=self.commodity_id,
            region_code="US", evidence_cutoff_utc="2026-09-04T16:00:00Z",
            calculation_rule_version_id=self.rule_id, engine_version_id=self.engine_id,
            recorded_at_utc="2026-09-04T16:00:00Z", audit=fixtures.audit(),
        )
        self._confirm(datum, region="MX", time="2026-09-05T12:00:00Z")
        self.assertEqual(verify_supplier_rate_distributions(self.connection), [])
        self.assertEqual(self._derive(cutoff="2026-09-05T16:00:00Z")["distributions"], [])
        self.assertEqual(len(self._derive()["distributions"]), 1)
        with self.assertRaisesRegex(Exception, "immutable"):
            self.connection.execute("UPDATE supplier_rate_distribution_run SET result_payload = '{}' WHERE distribution_run_id = ?", (run,))
        self.connection.execute("DROP TRIGGER no_update_supplier_rate_distribution")
        self.connection.execute("UPDATE supplier_rate_distribution_run SET result_payload = '{}' WHERE distribution_run_id = ?", (run,))
        self.assertEqual(verify_supplier_rate_distributions(self.connection)[0].code,
                         "SUPPLIER_RATE_DISTRIBUTION_MISMATCH")

    def test_piece_price_cannot_be_reclassified_as_a_labor_rate(self):
        datum = self.connection.execute("SELECT submitted_datum_id FROM submitted_datum WHERE field_code = 'PIECE_PRICE' LIMIT 1").fetchone()[0]
        with self.assertRaisesRegex(ValueError, "submitted rate field"):
            self._confirm(datum)

    def test_large_package_cannot_dominate_independent_event_mean(self):
        statistics = rate_statistics({"large-package": [Decimal("10")] * 100,
                                      "small-package": [Decimal("40")]})
        self.assertEqual(statistics["equal_event_mean"], "25.0000")
        self.assertEqual(statistics["raw_mean"], "10.2970")
        self.assertEqual(statistics["independent_event_count"], 2)
        self.assertEqual(statistics["range"], "30.0000")


if __name__ == "__main__":
    unittest.main()
