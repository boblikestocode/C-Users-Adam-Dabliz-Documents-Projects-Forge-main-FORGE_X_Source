from __future__ import annotations

import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from database.services.connection import connect
from database.services.audit import AuditContext
from database.services.decimals import ExactDecimal
from database.services.package_economics import calculate_package_economics
from database.services.payment_treatments import (
    PaymentTreatment, confirm_payment_treatment, record_payment_cost_evidence,
)
from database.services.piece_price import build_active_round_part_projection, get_piece_price_comparison
from database.validation.generate_synthetic import PROFILES, populate


ROOT = Path(__file__).resolve().parents[2]


class PackageEconomicsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / "database" / "tests")
        path = Path(self.temp.name) / "package-economics.db"
        populate(path, PROFILES["smoke"])
        self.connection = connect(path)
        self.event_id = self.connection.execute("SELECT event_id FROM sourcing_event").fetchone()[0]
        package_id, scope_id = self.connection.execute(
            """SELECT sp.source_package_id, sv.scope_version_id
               FROM source_package sp JOIN scope_version sv ON sv.source_package_id = sp.source_package_id
               WHERE sp.event_id = ?""", (self.event_id,)
        ).fetchone()
        self.connection.execute(
            """INSERT INTO gst_baseline
               (gst_baseline_id, source_package_id, scope_version_id, baseline_version,
                baseline_status, confirmed_by_user_id, confirmed_at_utc, recorded_at_utc)
               VALUES ('economics-gst', ?, ?, 1, 'Confirmed', 'buyer-1',
                       '2026-09-04T01:00:00Z', '2026-09-04T01:00:00Z')""",
            (package_id, scope_id),
        )
        parts = self.connection.execute("SELECT event_part_id FROM event_part").fetchall()
        for index, row in enumerate(parts):
            for year, volume in ((2027, 100 + index), (2028, 200 + index), (2029, 150 + index)):
                self.connection.execute(
                    """INSERT INTO gst_part_value
                       (gst_part_value_id, gst_baseline_id, event_part_id, program_year,
                        measure_code, submitted_lexeme, decimal_coefficient, decimal_scale,
                        normalized_unit_id, precision_status, recorded_at_utc)
                       VALUES (?, 'economics-gst', ?, ?, 'FPV', ?, ?, 0,
                               'PART/YEAR', 'Eligible', '2026-09-04T01:00:00Z')""",
                    (f"fpv-{index}-{year}", row[0], year, str(volume), str(volume)),
                )
                self.connection.execute(
                    """INSERT INTO gst_part_value
                       (gst_part_value_id, gst_baseline_id, event_part_id, program_year,
                        measure_code, submitted_lexeme, decimal_coefficient, decimal_scale,
                        governing_1e4, normalized_unit_id, currency_id,
                        precision_status, recorded_at_utc)
                       VALUES (?, 'economics-gst', ?, ?, 'Piece Price Target',
                               '9.0000', '90000', 4, 90000, 'USD/PART', 'USD',
                               'Eligible', '2026-09-04T01:00:00Z')""",
                    (f"target-{index}-{year}", row[0], year),
                )
        generation = build_active_round_part_projection(
            self.connection, event_id=self.event_id, evidence_cutoff_utc="2026-09-04T01:00:00Z"
        )
        self.comparison = get_piece_price_comparison(
            self.connection, generation_id=generation, event_id=self.event_id
        )

    def tearDown(self) -> None:
        self.connection.close()
        self.temp.cleanup()

    @staticmethod
    def _audit(time: str) -> AuditContext:
        return AuditContext(
            event_type="Payment Treatment", actor_user_id="buyer-1",
            effective_authority="Primary Buyer", occurred_at_utc=time,
            display_timezone="America/New_York", workstation_session="test",
            application_version="test", action_method="Unit Test",
            reason_code="TEST", reason_text="Test evidence",
        )

    def _add_payment_cost(self, *, confirm: bool) -> str:
        quote = self.comparison[0].quotes[0]
        source_datum_id = self.connection.execute(
            """SELECT datum.source_datum_id FROM pbd_observation observation
               JOIN source_occurrence occurrence ON occurrence.occurrence_id = observation.occurrence_id
               JOIN source_datum datum ON datum.worksheet_id = occurrence.worksheet_id
               WHERE observation.observation_id = ? LIMIT 1""",
            (quote.observation_id,),
        ).fetchone()[0]
        rule_id = self.connection.execute("SELECT rule_version_id FROM rule_version LIMIT 1").fetchone()[0]
        evidence_id = record_payment_cost_evidence(
            self.connection, observation_id=quote.observation_id,
            source_datum_id=source_datum_id, cost_type="SFT", program_year=2028,
            submitted_wording="Supplier tooling", submitted_amount=ExactDecimal.parse("1000.00"),
            normalized_unit_id="USD", currency_id="USD", treatment_rule_version_id=rule_id,
            recorded_at_utc="2026-09-04T02:00:00Z", audit=self._audit("2026-09-04T02:00:00Z"),
        )
        if confirm:
            confirm_payment_treatment(
                self.connection, payment_cost_evidence_id=evidence_id,
                treatment=PaymentTreatment("Separate Lump Sum"),
                treatment_rule_version_id=rule_id, confirmed_by_user_id="buyer-1",
                confirmation_reason="Paid once outside piece price",
                recorded_at_utc="2026-09-04T02:01:00Z", audit=self._audit("2026-09-04T02:01:00Z"),
            )
        return quote.supplier_id

    def test_complete_packages_apply_lta_and_npv_at_correct_time(self) -> None:
        result = calculate_package_economics(
            self.connection, event_id=self.event_id, comparison=self.comparison,
            program_years=(2027, 2028, 2029), lta_rate=Decimal("0.02"),
            discount_rate=Decimal("0.07"),
        )
        self.assertEqual(result.peak_volume_year, 2028)
        self.assertEqual(len(result.suppliers), PROFILES["smoke"].suppliers)
        supplier = result.suppliers[0]
        self.assertEqual(supplier.package_status, "Complete — Comparable for Award")
        self.assertEqual(supplier.part_coverage_percent, Decimal("100"))
        self.assertEqual(supplier.fpv_weighted_coverage_percent, Decimal("100"))
        self.assertEqual(supplier.annual_values[0].base_apv, supplier.annual_values[0].lta_adjusted_apv)
        self.assertEqual(supplier.annual_values[0].discounted_value, supplier.annual_values[0].base_apv)
        self.assertEqual(supplier.annual_values[1].lta_adjusted_apv, supplier.annual_values[1].base_apv * Decimal("0.98"))
        self.assertEqual(supplier.npv, sum((year.discounted_value for year in supplier.annual_values), Decimal(0)))
        self.assertEqual(result.target_status, "Complete")
        self.assertEqual(supplier.annual_values[0].gap_to_target,
                         supplier.annual_values[0].base_apv - supplier.annual_values[0].target_apv)
        self.assertEqual(supplier.lifecycle_gap_to_target,
                         sum((year.gap_to_target for year in supplier.annual_values), Decimal(0)))
        self.assertIn(supplier.package_rank, (1, 2, 3))
        self.assertEqual(sorted(item.package_rank for item in result.suppliers), [1, 2, 3])

    def test_incomplete_package_keeps_covered_spend_but_withholds_comparable_npv(self) -> None:
        first = self.comparison[0]
        quote = first.quotes[0]
        modified_quote = quote.__class__(
            quote.supplier_id, quote.submitted_supplier_name, quote.quote_round_id,
            quote.round_number, quote.observation_id, "Not Quoted", None,
            quote.currency_id, quote.normalized_unit_id,
        )
        modified_part = first.__class__(
            first.event_part_id, first.part_id, first.submitted_part_number,
            first.submitted_description, first.vehicle_position,
            first.target_piece_price, first.target_currency_id,
            first.target_normalized_unit_id, (modified_quote, *first.quotes[1:]),
        )
        comparison = (modified_part, *self.comparison[1:])
        result = calculate_package_economics(
            self.connection, event_id=self.event_id, comparison=comparison,
            program_years=(2027, 2028),
        )
        supplier = next(item for item in result.suppliers if item.supplier_id == quote.supplier_id)
        self.assertEqual(supplier.package_status, "Partial Package — Not Comparable for Award")
        self.assertLess(supplier.part_coverage_percent, Decimal("100"))
        self.assertGreater(supplier.lifecycle_spend, Decimal("0"))
        self.assertIsNone(supplier.npv)
        self.assertIsNone(supplier.package_rank)

    def test_period_and_missing_fpv_validation_are_strict(self) -> None:
        with self.assertRaisesRegex(ValueError, "one to five"):
            calculate_package_economics(
                self.connection, event_id=self.event_id, comparison=self.comparison,
                program_years=(2024, 2025, 2026, 2027, 2028, 2029),
            )
        with self.assertRaisesRegex(ValueError, "missing"):
            calculate_package_economics(
                self.connection, event_id=self.event_id, comparison=self.comparison,
                program_years=(2026,),
            )

    def test_separate_payment_cost_enters_cash_flow_once(self) -> None:
        before = calculate_package_economics(
            self.connection, event_id=self.event_id, comparison=self.comparison,
            program_years=(2027, 2028, 2029), discount_rate=Decimal("0.10"),
        )
        supplier_id = self._add_payment_cost(confirm=True)
        after = calculate_package_economics(
            self.connection, event_id=self.event_id, comparison=self.comparison,
            program_years=(2027, 2028, 2029), discount_rate=Decimal("0.10"),
        )
        old = next(item for item in before.suppliers if item.supplier_id == supplier_id)
        new = next(item for item in after.suppliers if item.supplier_id == supplier_id)
        self.assertEqual(new.payment_treatment_status, "Confirmed")
        self.assertEqual(new.annual_values[1].one_time_cost, Decimal("1000.00"))
        self.assertEqual(new.lifecycle_spend - old.lifecycle_spend, Decimal("1000.00"))
        self.assertAlmostEqual(new.npv - old.npv, Decimal("1000.00") / Decimal("1.10"), places=20)

    def test_unconfirmed_payment_cost_withholds_only_total_program_comparison(self) -> None:
        supplier_id = self._add_payment_cost(confirm=False)
        result = calculate_package_economics(
            self.connection, event_id=self.event_id, comparison=self.comparison,
            program_years=(2027, 2028),
        )
        supplier = next(item for item in result.suppliers if item.supplier_id == supplier_id)
        self.assertEqual(supplier.payment_treatment_status, "Treatment Unconfirmed — Total Program Cost Incomplete")
        self.assertGreater(supplier.lifecycle_spend, Decimal(0))
        self.assertIsNone(supplier.npv)
        self.assertIsNone(supplier.package_rank)


if __name__ == "__main__":
    unittest.main()
