from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path

from database.services.audit import AuditContext, verify_audit_chain
from database.services.connection import connect
from database.services.economic_age import classify_economic_age, confirm_economic_dates
from database.services.piece_price import build_active_round_part_projection, get_piece_price_comparison
from database.validation.generate_synthetic import PROFILES, populate


ROOT = Path(__file__).resolve().parents[2]


class EconomicAgeServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / "database" / "tests")
        path = Path(self.temp.name) / "economic-age.db"
        populate(path, PROFILES["smoke"])
        self.connection = connect(path)
        self.rule_id = self.connection.execute(
            "SELECT rule_version_id FROM rule_version LIMIT 1"
        ).fetchone()[0]

    def tearDown(self) -> None:
        self.connection.close()
        self.temp.cleanup()

    @staticmethod
    def _audit(time: str) -> AuditContext:
        return AuditContext(
            event_type="Economic Age", actor_user_id="buyer-1",
            effective_authority="Primary Buyer", occurred_at_utc=time,
            display_timezone="America/New_York", workstation_session="test",
            application_version="test", action_method="Unit Test",
            reason_code="DATE_CONFIRMED", reason_text="Supplier submission evidence",
        )

    def _observations_for_one_supplier(self, count: int = 2) -> tuple[str, ...]:
        supplier = self.connection.execute(
            "SELECT supplier_id FROM pbd_observation WHERE supplier_id IS NOT NULL LIMIT 1"
        ).fetchone()[0]
        return tuple(row[0] for row in self.connection.execute(
            "SELECT observation_id FROM pbd_observation WHERE supplier_id = ? LIMIT ?",
            (supplier, count),
        ))

    def test_year_only_cutoff_overlap_is_context_until_precise_date_confirmed(self) -> None:
        observation_id = self._observations_for_one_supplier(1)[0]
        confirm_economic_dates(
            self.connection, observation_ids=(observation_id,),
            confirmed_economic_date="2023", date_precision="Year",
            confirmed_by_user_id="buyer-1", confirmation_reason="Legacy quote year",
            recorded_at_utc="2026-09-04T13:00:00Z", audit=self._audit("2026-09-04T13:00:00Z"),
        )
        result = classify_economic_age(
            self.connection, observation_ids=(observation_id,), as_of_date=date(2026, 9, 4),
            eligibility_rule_version_id=self.rule_id, confirmed_by_user_id="buyer-1",
            recorded_at_utc="2026-09-04T13:01:00Z", audit=self._audit("2026-09-04T13:01:00Z"),
        )[0]
        self.assertEqual(result.eligibility_status, "Historical Context Only")
        self.assertEqual(result.reason_code, "YEAR_ONLY_OVERLAPS_CUTOFF")
        confirm_economic_dates(
            self.connection, observation_ids=(observation_id,),
            confirmed_economic_date="2023-09-05", date_precision="Day",
            confirmed_by_user_id="buyer-1", confirmation_reason="Supplier email confirms exact date",
            recorded_at_utc="2026-09-04T13:02:00Z", audit=self._audit("2026-09-04T13:02:00Z"),
        )
        updated = classify_economic_age(
            self.connection, observation_ids=(observation_id,), as_of_date=date(2026, 9, 4),
            eligibility_rule_version_id=self.rule_id, confirmed_by_user_id="buyer-1",
            recorded_at_utc="2026-09-04T13:03:00Z", audit=self._audit("2026-09-04T13:03:00Z"),
        )[0]
        self.assertEqual(updated.eligibility_status, "Eligible")
        self.assertEqual(verify_audit_chain(self.connection), [])

    def test_batch_is_limited_and_requires_same_supplier_and_import(self) -> None:
        observations = self._observations_for_one_supplier(2)
        ids = confirm_economic_dates(
            self.connection, observation_ids=observations,
            confirmed_economic_date="2026-08", date_precision="Month",
            confirmed_by_user_id="buyer-1", confirmation_reason="Same supplier submission batch",
            recorded_at_utc="2026-09-04T13:00:00Z", audit=self._audit("2026-09-04T13:00:00Z"),
        )
        self.assertEqual(len(ids), 2)
        batch_count = self.connection.execute(
            "SELECT COUNT(DISTINCT assignment_batch_id) FROM economic_date_confirmation"
        ).fetchone()[0]
        self.assertEqual(batch_count, 1)
        with self.assertRaisesRegex(ValueError, "1–20"):
            confirm_economic_dates(
                self.connection, observation_ids=tuple(f"missing-{i}" for i in range(21)),
                confirmed_economic_date="2026", date_precision="Year",
                confirmed_by_user_id="buyer-1", confirmation_reason="Too broad",
                recorded_at_utc="2026-09-04T13:01:00Z", audit=self._audit("2026-09-04T13:01:00Z"),
            )
        different = tuple(row[0] for row in self.connection.execute(
            """SELECT MIN(observation_id) FROM pbd_observation
               WHERE supplier_id IS NOT NULL GROUP BY supplier_id LIMIT 2"""
        ))
        with self.assertRaisesRegex(ValueError, "one confirmed supplier"):
            confirm_economic_dates(
                self.connection, observation_ids=different,
                confirmed_economic_date="2026", date_precision="Year",
                confirmed_by_user_id="buyer-1", confirmation_reason="Invalid mixed supplier batch",
                recorded_at_utc="2026-09-04T13:01:00Z", audit=self._audit("2026-09-04T13:01:00Z"),
            )

    def test_old_dates_remain_permanent_context(self) -> None:
        observation_id = self._observations_for_one_supplier(1)[0]
        confirm_economic_dates(
            self.connection, observation_ids=(observation_id,),
            confirmed_economic_date="2020-01-01", date_precision="Day",
            confirmed_by_user_id="buyer-1", confirmation_reason="Archived quote",
            recorded_at_utc="2026-09-04T13:00:00Z", audit=self._audit("2026-09-04T13:00:00Z"),
        )
        results = classify_economic_age(
            self.connection, observation_ids=(observation_id,), as_of_date=date(2026, 9, 4),
            eligibility_rule_version_id=self.rule_id, confirmed_by_user_id="buyer-1",
            recorded_at_utc="2026-09-04T13:01:00Z", audit=self._audit("2026-09-04T13:01:00Z"),
        )
        self.assertEqual(results[0].reason_code, "OLDER_THAN_THREE_YEARS")
        self.assertEqual(results[0].eligibility_status, "Historical Context Only")

    def test_context_only_price_remains_visible_but_is_analytically_ineligible(self) -> None:
        event_id = self.connection.execute("SELECT event_id FROM sourcing_event LIMIT 1").fetchone()[0]
        observation_id = self.connection.execute(
            """SELECT membership.observation_id
               FROM v_active_supplier_round active
               JOIN round_observation membership
                 ON membership.quote_round_id = active.quote_round_id
               WHERE membership.observation_id IS NOT NULL LIMIT 1"""
        ).fetchone()[0]
        confirm_economic_dates(
            self.connection, observation_ids=(observation_id,),
            confirmed_economic_date="2020-01-01", date_precision="Day",
            confirmed_by_user_id="buyer-1", confirmation_reason="Archived supplier quote",
            recorded_at_utc="2026-09-04T13:00:00Z", audit=self._audit("2026-09-04T13:00:00Z"),
        )
        classify_economic_age(
            self.connection, observation_ids=(observation_id,), as_of_date=date(2026, 9, 4),
            eligibility_rule_version_id=self.rule_id, confirmed_by_user_id="buyer-1",
            recorded_at_utc="2026-09-04T13:01:00Z", audit=self._audit("2026-09-04T13:01:00Z"),
        )
        generation = build_active_round_part_projection(
            self.connection, event_id=event_id, evidence_cutoff_utc="2026-09-04T13:02:00Z"
        )
        comparison = get_piece_price_comparison(
            self.connection, generation_id=generation, event_id=event_id
        )
        quote = next(
            quote for part in comparison for quote in part.quotes
            if quote.observation_id == observation_id
        )
        self.assertIsNone(quote.piece_price)
        self.assertIsNotNone(self.connection.execute(
            "SELECT 1 FROM pbd_observation WHERE observation_id = ?", (observation_id,)
        ).fetchone())


if __name__ == "__main__":
    unittest.main()
