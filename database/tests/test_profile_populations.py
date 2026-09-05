from __future__ import annotations

import unittest

from database.tests import test_profile_service as fixtures
from database.services.profiles import profile_activity_rows, profile_formula_exception_rows
from database.services.profile_populations import economic_age_at_cutoff, observation_region_at_cutoff
from database.services.quote_versions import register_quote_version_candidate
from database.services.economic_age import confirm_economic_dates
from database.services.integrity import verify_supplier_profile_generations, verify_projection_generations
from database.services.piece_price import build_active_round_part_projection, projection_source_rows

audit = fixtures.audit


class ProfilePopulationTests(unittest.TestCase):
    setUp = fixtures.SupplierProfileServiceTests.setUp
    tearDown = fixtures.SupplierProfileServiceTests.tearDown
    _seed_formula_exceptions = fixtures.SupplierProfileServiceTests._seed_formula_exceptions
    _formula_profile = fixtures.SupplierProfileServiceTests._formula_profile
    _date_decision = fixtures.SupplierProfileServiceTests._date_decision

    def _region(self, observation, region, time="2026-09-03T14:00:00Z"):
        register_quote_version_candidate(
            self.connection, observation_id=observation, region_code=region,
            source_identity=observation, detected_at_utc=time, audit=audit(),
        )

    def _formulas(self, region, cutoff="2026-09-03T16:00:00Z"):
        return profile_formula_exception_rows(
            self.connection, supplier_id=self.supplier_id, supplier_plant_id=self.plant_id,
            commodity_id=self.commodity_id, evidence_cutoff_utc=cutoff,
            active_window_start_utc="2023-01-01T00:00:00Z", region_code=region,
            scope_population_version="Regional Age v1",
        )

    def test_regional_populations_separate_us_mx_and_unknown(self):
        us, mx = self._seed_formula_exceptions()
        self.assertEqual(self._formulas("US"), [])
        self._region(us, "US")
        self._region(mx, "MX")
        self.assertEqual({row["observation_id"] for row in self._formulas("US")}, {us})
        self.assertEqual({row["observation_id"] for row in self._formulas("MX")}, {mx})
        self.assertEqual(len(self._formulas(None)), 2)
        self.assertEqual(self._formulas("US", "2026-09-03T13:00:00Z"), [])

    def test_new_profiles_recheck_stale_age_without_rewriting_old_runs(self):
        observations = self._seed_formula_exceptions()
        self._date_decision(observations[0], "2023-09-04", "2026-09-03T15:30:00Z")
        prior = self._formula_profile("2026-09-04T16:00:00Z")
        current = self._formula_profile("2026-09-05T16:00:00Z")
        count = lambda run: self.connection.execute(
            """SELECT COUNT(*) FROM profile_evidence_entry WHERE supplier_profile_run_id = ?
               AND evidence_entity_type = 'Formula Integrity Event'""", (run.profile_run_id,),
        ).fetchone()[0]
        self.assertEqual(count(prior), 2)
        self.assertEqual(count(current), 1)
        self.assertEqual(verify_supplier_profile_generations(self.connection), [])

    def test_date_confirmation_is_consumed_without_separate_classification(self):
        observation = self._seed_formula_exceptions()[0]
        self._date_decision(observation, "2020-01-01", "2026-09-03T15:30:00Z")
        self.assertEqual(len(self._formulas(None)), 1)
        confirm_economic_dates(
            self.connection, observation_ids=(observation,), confirmed_economic_date="2026-01-01",
            date_precision="Day", confirmed_by_user_id="buyer-1", confirmation_reason="Supplier confirms date",
            recorded_at_utc="2026-09-04T12:00:00Z", audit=audit(),
        )
        self.assertEqual(len(self._formulas(None, "2026-09-04T16:00:00Z")), 2)
        self.assertEqual(len(self._formulas(None)), 1)

    def test_unassessed_old_and_future_dates_are_not_governing(self):
        observation = self._seed_formula_exceptions()[0]
        for value in ("2020-01-01", "2027-01-01"):
            confirm_economic_dates(
                self.connection, observation_ids=(observation,), confirmed_economic_date=value,
                date_precision="Day", confirmed_by_user_id="buyer-1", confirmation_reason="Test source date",
                recorded_at_utc="2026-09-04T12:00:00Z", audit=audit(),
            )
            self.assertEqual(economic_age_at_cutoff(
                self.connection, observation, "2026-09-04T16:00:00Z").eligibility_status,
                "Historical Context Only")

    def test_regional_activity_requires_all_linked_observations_in_region(self):
        observation = self._seed_formula_exceptions()[0]
        self._region(observation, "US")
        rows = profile_activity_rows(
            self.connection, supplier_id=self.supplier_id, supplier_plant_id=self.plant_id,
            commodity_id=self.commodity_id, evidence_cutoff_utc="2026-09-04T16:00:00Z",
            active_window_start_utc="2023-01-01T00:00:00Z", region_code="US",
            scope_population_version="Regional Age v1",
        )
        self.assertLessEqual(len(rows), 1)
        self.assertEqual(observation_region_at_cutoff(
            self.connection, observation, "2026-09-03T13:00:00Z"), None)

    def test_piece_price_refresh_preserves_prior_projection(self):
        event = self.connection.execute("SELECT event_id FROM sourcing_event LIMIT 1").fetchone()[0]
        observation = next(row[4] for row in projection_source_rows(
            self.connection, event, "2026-09-04T16:00:00Z") if row[4] is not None)
        self._date_decision(observation, "2023-09-04", "2026-09-03T15:30:00Z")
        prior = build_active_round_part_projection(
            self.connection, event_id=event, evidence_cutoff_utc="2026-09-04T16:00:00Z")
        current = build_active_round_part_projection(
            self.connection, event_id=event, evidence_cutoff_utc="2026-09-05T16:00:00Z")
        value = lambda generation: self.connection.execute(
            """SELECT piece_price_coefficient FROM active_round_part_projection
               WHERE projection_generation_id = ? AND observation_id = ?""", (generation, observation),
        ).fetchone()[0]
        self.assertIsNotNone(value(prior))
        self.assertIsNone(value(current))
        self.assertEqual(verify_projection_generations(self.connection), [])


if __name__ == "__main__":
    unittest.main()
