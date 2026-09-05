from __future__ import annotations

import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from database.services.audit import AuditContext, verify_audit_chain
from database.services.analysis import EvidenceSelection, create_analysis, create_scenario_revision
from database.services.commercial_results import cross_border_result_inputs
from database.services.connection import connect
from database.services.cross_border import (
    OperationEvidenceInput, compare_aggregate_conversion, create_cross_border_review_action,
    create_exact_part_pair, match_cross_border_operations,
    record_location_measure, record_operation_evidence, reconstruct_us_to_mexico,
)
from database.services.decimals import ExactDecimal
from database.validation.generate_synthetic import PROFILES, populate


ROOT = Path(__file__).resolve().parents[2]


class CrossBorderServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / "database" / "tests")
        path = Path(self.temp.name) / "cross-border.db"
        populate(path, PROFILES["smoke"])
        self.connection = connect(path)
        pair = self.connection.execute(
            """SELECT MIN(observation_id), MAX(observation_id)
               FROM pbd_observation WHERE part_id IS NOT NULL
               GROUP BY part_id HAVING COUNT(*) >= 2 LIMIT 1"""
        ).fetchone()
        self.us_observation, self.mx_observation = pair
        self.sources = {}
        for observation_id in pair:
            self.sources[observation_id] = self.connection.execute(
                """SELECT datum.source_datum_id FROM pbd_observation observation
                   JOIN source_occurrence occurrence ON occurrence.occurrence_id = observation.occurrence_id
                   JOIN source_datum datum ON datum.worksheet_id = occurrence.worksheet_id
                   WHERE observation.observation_id = ? LIMIT 1""", (observation_id,)
            ).fetchone()[0]
        self.rule_id = self.connection.execute("SELECT rule_version_id FROM rule_version LIMIT 1").fetchone()[0]
        self.engine_id = self.connection.execute("SELECT engine_version_id FROM engine_version LIMIT 1").fetchone()[0]

    def tearDown(self) -> None:
        self.connection.close()
        self.temp.cleanup()

    @staticmethod
    def _audit(time: str) -> AuditContext:
        return AuditContext(
            event_type="Cross Border Evidence", actor_user_id="buyer-1",
            effective_authority="Primary Buyer", occurred_at_utc=time,
            display_timezone="America/New_York", workstation_session="test",
            application_version="test", action_method="Unit Test",
            reason_code="EXACT_PART", reason_text="Exact part reconstruction",
        )

    def _measure(self, observation: str, region: str, code: str, value: str,
                 unit: str = "USD", currency: str | None = "USD", basis: str | None = None) -> str:
        return record_location_measure(
            self.connection, observation_id=observation,
            source_datum_id=self.sources[observation], region_code=region,
            measure_code=code, value=ExactDecimal.parse(value), normalized_unit_id=unit,
            currency_id=currency, calculation_basis_code=basis, evidence_status="Valid",
            recorded_at_utc="2026-09-04T14:00:00Z", audit=self._audit("2026-09-04T14:00:00Z"),
        )

    def _pair(self) -> str:
        return create_exact_part_pair(
            self.connection, us_observation_id=self.us_observation,
            mx_observation_id=self.mx_observation, pairing_rule_version_id=self.rule_id,
            recorded_at_utc="2026-09-04T14:01:00Z", audit=self._audit("2026-09-04T14:01:00Z"),
        )

    def test_exact_pair_reconstructs_with_lowest_supported_mexico_labor_rate(self) -> None:
        for code, value, unit, currency, basis in (
            ("PIECE_PRICE", "130", "USD/PART", "USD", None),
            ("RAW_MATERIAL", "50", "USD/PART", "USD", None),
            ("PURCHASED_COMPONENTS", "10", "USD/PART", "USD", None),
            ("LABOR_HOURS", "2", "HOUR", None, None),
            ("LABOR_RATE", "20", "USD/HOUR", "USD", None),
            ("LABOR_DOLLARS", "40", "USD/PART", "USD", None),
            ("BURDEN_DOLLARS", "20", "USD/PART", "USD", None),
            ("OTHER_PHYSICAL_COST", "5", "USD/PART", "USD", None),
            ("OVERHEAD_PERCENT", "10", "PERCENT", None, "ADJUSTED_CONVERSION"),
            ("PROFIT_PERCENT", "5", "PERCENT", None, "COST_PLUS_OVERHEAD"),
        ):
            self._measure(self.us_observation, "US", code, value, unit, currency, basis)
        self._measure(self.mx_observation, "MX", "PIECE_PRICE", "120", "USD/PART", "USD")
        benchmark_id = self._measure(self.mx_observation, "MX", "LABOR_RATE", "7", "USD/HOUR", "USD")
        result = reconstruct_us_to_mexico(
            self.connection, cross_border_pair_id=self._pair(),
            calculation_rule_version_id=self.rule_id, engine_version_id=self.engine_id,
            calculated_at_utc="2026-09-04T14:02:00Z", audit=self._audit("2026-09-04T14:02:00Z"),
        )
        self.assertEqual(result.confidence_classification, "Confirmed")
        self.assertEqual(result.benchmark_evidence_id, benchmark_id)
        self.assertEqual(result.values["adjusted_labor"], Decimal("14.0000"))
        self.assertEqual(result.values["reconstructed_model_cost"], Decimal("107.5200"))
        self.assertEqual(result.values["total_component_opportunity"], Decimal("12.4800"))
        persisted = cross_border_result_inputs(
            self.connection,
            event_id=self.connection.execute(
                "SELECT context_id FROM pbd_observation WHERE observation_id = ?",
                (self.us_observation,),
            ).fetchone()[0],
            reconstruction_ids=(result.reconstruction_id,),
        )
        confirmed = next(item for item in persisted if item.result_code == "CROSS_BORDER_CONFIRMED_SAVINGS")
        self.assertEqual(confirmed.exact_decimal.as_decimal(), Decimal("12.4800"))
        self.assertEqual(confirmed.confidence_classification, "Confirmed")
        self.assertEqual(verify_audit_chain(self.connection), [])

    def test_missing_support_produces_low_confidence_without_invented_savings(self) -> None:
        self._measure(self.us_observation, "US", "PIECE_PRICE", "130", "USD/PART", "USD")
        self._measure(self.mx_observation, "MX", "PIECE_PRICE", "120", "USD/PART", "USD")
        result = reconstruct_us_to_mexico(
            self.connection, cross_border_pair_id=self._pair(),
            calculation_rule_version_id=self.rule_id, engine_version_id=self.engine_id,
            calculated_at_utc="2026-09-04T14:02:00Z", audit=self._audit("2026-09-04T14:02:00Z"),
        )
        self.assertEqual(result.confidence_classification, "Directional Estimate - Low Confidence")
        self.assertIn("LABOR_HOURS", result.missing_evidence)
        self.assertEqual(result.values, {})
        action_id = create_cross_border_review_action(
            self.connection, cross_border_reconstruction_id=result.reconstruction_id,
            owner_user_id="buyer-1", created_by_user_id="buyer-1",
            created_at_utc="2026-09-04T14:03:00Z", audit=self._audit("2026-09-04T14:03:00Z"),
        )
        action = self.connection.execute(
            "SELECT action_status, required_supplier_action FROM v_current_buyer_action WHERE buyer_action_id = ?",
            (action_id,),
        ).fetchone()
        self.assertEqual(action[0], "Open")
        self.assertIn("LABOR_HOURS", action[1])

    def test_different_parts_cannot_be_paired(self) -> None:
        different = self.connection.execute(
            "SELECT observation_id FROM pbd_observation WHERE part_id <> (SELECT part_id FROM pbd_observation WHERE observation_id = ?) LIMIT 1",
            (self.us_observation,),
        ).fetchone()[0]
        self._measure(self.us_observation, "US", "PIECE_PRICE", "1", "USD/PART", "USD")
        self.sources[different] = self.connection.execute(
            """SELECT datum.source_datum_id FROM pbd_observation observation
               JOIN source_occurrence occurrence ON occurrence.occurrence_id = observation.occurrence_id
               JOIN source_datum datum ON datum.worksheet_id = occurrence.worksheet_id
               WHERE observation.observation_id = ? LIMIT 1""", (different,)
        ).fetchone()[0]
        self._measure(different, "MX", "PIECE_PRICE", "1", "USD/PART", "USD")
        with self.assertRaisesRegex(ValueError, "same exact canonical part"):
            create_exact_part_pair(
                self.connection, us_observation_id=self.us_observation,
                mx_observation_id=different, pairing_rule_version_id=self.rule_id,
                recorded_at_utc="2026-09-04T14:01:00Z", audit=self._audit("2026-09-04T14:01:00Z"),
            )

    def _seed_pair_regions(self) -> str:
        self._measure(self.us_observation, "US", "PIECE_PRICE", "130", "USD/PART", "USD")
        self._measure(self.mx_observation, "MX", "PIECE_PRICE", "120", "USD/PART", "USD")
        return self._pair()

    def _operation_line(self, observation_id: str) -> str:
        row = self.connection.execute(
            "SELECT operation_line_id FROM operation_line WHERE observation_id = ? LIMIT 1",
            (observation_id,),
        ).fetchone()
        self.assertIsNotNone(row)
        return row[0]

    def test_exact_operation_identity_calculates_burden_opportunity(self) -> None:
        pair_id = self._seed_pair_regions()
        common = dict(
            normalized_operation="INJECTION MOLDING", unit_basis="MACHINE HOUR",
            burden_hours=ExactDecimal.parse("2"), normalized_rate_unit_id="USD/HOUR",
            currency_id="USD", equipment_identifier="PRESS-1200",
        )
        us_id = record_operation_evidence(
            self.connection, observation_id=self.us_observation,
            operation_line_id=self._operation_line(self.us_observation), region_code="US",
            evidence=OperationEvidenceInput(burden_rate=ExactDecimal.parse("50"), **common),
            recorded_at_utc="2026-09-04T14:02:00Z", audit=self._audit("2026-09-04T14:02:00Z"),
        )
        mx_id = record_operation_evidence(
            self.connection, observation_id=self.mx_observation,
            operation_line_id=self._operation_line(self.mx_observation), region_code="MX",
            evidence=OperationEvidenceInput(burden_rate=ExactDecimal.parse("20"), **common),
            recorded_at_utc="2026-09-04T14:02:00Z", audit=self._audit("2026-09-04T14:02:00Z"),
        )
        result = match_cross_border_operations(
            self.connection, cross_border_pair_id=pair_id,
            us_operation_evidence_id=us_id, mx_operation_evidence_id=mx_id,
            matching_rule_version_id=self.rule_id, recorded_at_utc="2026-09-04T14:03:00Z",
            audit=self._audit("2026-09-04T14:03:00Z"),
        )
        self.assertEqual(result.match_classification, "Exact Match")
        self.assertEqual(result.burden_opportunity, Decimal("60.0000"))

    def test_incomplete_equipment_signature_is_reference_only(self) -> None:
        pair_id = self._seed_pair_regions()
        us_id = record_operation_evidence(
            self.connection, observation_id=self.us_observation,
            operation_line_id=self._operation_line(self.us_observation), region_code="US",
            evidence=OperationEvidenceInput(
                normalized_operation="MOLDING", unit_basis="HOUR",
                burden_rate=ExactDecimal.parse("50"), burden_hours=ExactDecimal.parse("2"),
                normalized_rate_unit_id="USD/HOUR", currency_id="USD",
                normalized_equipment_description="PRESS", process_type="INJECTION",
            ), recorded_at_utc="2026-09-04T14:02:00Z", audit=self._audit("2026-09-04T14:02:00Z"),
        )
        mx_id = record_operation_evidence(
            self.connection, observation_id=self.mx_observation,
            operation_line_id=self._operation_line(self.mx_observation), region_code="MX",
            evidence=OperationEvidenceInput(
                normalized_operation="MOLDING", unit_basis="HOUR",
                burden_rate=ExactDecimal.parse("20"), burden_hours=ExactDecimal.parse("2"),
                normalized_rate_unit_id="USD/HOUR", currency_id="USD",
                normalized_equipment_description="PRESS", process_type="INJECTION",
            ), recorded_at_utc="2026-09-04T14:02:00Z", audit=self._audit("2026-09-04T14:02:00Z"),
        )
        result = match_cross_border_operations(
            self.connection, cross_border_pair_id=pair_id,
            us_operation_evidence_id=us_id, mx_operation_evidence_id=mx_id,
            matching_rule_version_id=self.rule_id, recorded_at_utc="2026-09-04T14:03:00Z",
            audit=self._audit("2026-09-04T14:03:00Z"),
        )
        self.assertEqual(result.match_classification, "Reference Only")
        self.assertIsNone(result.burden_opportunity)

    def test_aggregate_conversion_compares_combined_totals_without_inventing_split(self) -> None:
        pair_id = self._seed_pair_regions()
        self._measure(self.us_observation, "US", "TOTAL_CONVERSION_COST", "75", "USD/PART", "USD")
        self._measure(self.mx_observation, "MX", "TOTAL_CONVERSION_COST", "45", "USD/PART", "USD")
        result = compare_aggregate_conversion(
            self.connection, cross_border_pair_id=pair_id,
            calculation_rule_version_id=self.rule_id, engine_version_id=self.engine_id,
            calculated_at_utc="2026-09-04T14:03:00Z", audit=self._audit("2026-09-04T14:03:00Z"),
        )
        self.assertEqual(result.confidence_classification, "Confirmed")
        self.assertEqual(result.values["aggregate_conversion_opportunity"], Decimal("30.0000"))
        payload = self.connection.execute(
            "SELECT input_manifest_payload FROM cross_border_reconstruction WHERE cross_border_reconstruction_id = ?",
            (result.reconstruction_id,),
        ).fetchone()[0]
        self.assertIn('"split_manufactured":false', payload)

    def test_unreconciled_aggregate_structure_never_generates_savings(self) -> None:
        pair_id = self._seed_pair_regions()
        for observation, region, total, labor, burden, hours, rate in (
            (self.us_observation, "US", "75", "40", "20", "2", "20"),
            (self.mx_observation, "MX", "45", "20", "25", "2", "10"),
        ):
            self._measure(observation, region, "TOTAL_CONVERSION_COST", total, "USD/PART", "USD")
            self._measure(observation, region, "LABOR_DOLLARS", labor, "USD/PART", "USD")
            self._measure(observation, region, "BURDEN_DOLLARS", burden, "USD/PART", "USD")
            self._measure(observation, region, "LABOR_HOURS", hours, "HOUR", None)
            self._measure(observation, region, "LABOR_RATE", rate, "USD/HOUR", "USD")
        result = compare_aggregate_conversion(
            self.connection, cross_border_pair_id=pair_id,
            calculation_rule_version_id=self.rule_id, engine_version_id=self.engine_id,
            calculated_at_utc="2026-09-04T14:03:00Z", audit=self._audit("2026-09-04T14:03:00Z"),
        )
        self.assertEqual(result.confidence_classification, "Directional Estimate - Low Confidence")
        self.assertIn("US:CONVERSION_RECONCILIATION_EXCEPTION", result.missing_evidence)
        self.assertEqual(result.values, {})

    def test_scenario_manifest_requires_cross_border_source_observations(self) -> None:
        self._measure(self.us_observation, "US", "PIECE_PRICE", "130", "USD/PART", "USD")
        self._measure(self.mx_observation, "MX", "PIECE_PRICE", "120", "USD/PART", "USD")
        result = reconstruct_us_to_mexico(
            self.connection, cross_border_pair_id=self._pair(),
            calculation_rule_version_id=self.rule_id, engine_version_id=self.engine_id,
            calculated_at_utc="2026-09-04T14:02:00Z", audit=self._audit("2026-09-04T14:02:00Z"),
        )
        event_id = self.connection.execute(
            "SELECT context_id FROM pbd_observation WHERE observation_id = ?", (self.us_observation,)
        ).fetchone()[0]
        scope_id = self.connection.execute(
            """SELECT scope_version_id FROM scope_version version
               JOIN source_package package ON package.source_package_id = version.source_package_id
               WHERE package.event_id = ? LIMIT 1""", (event_id,)
        ).fetchone()[0]
        analysis_id = create_analysis(
            self.connection, event_id=event_id, analysis_type="Cross Border",
            readable_name="Cross-Border Test", created_by_user_id="buyer-1",
            created_at_utc="2026-09-04T14:03:00Z", audit=self._audit("2026-09-04T14:03:00Z"),
        )
        with self.assertRaisesRegex(ValueError, "underlying observations"):
            create_scenario_revision(
                self.connection, analysis_id=analysis_id, scope_version_id=scope_id,
                gst_baseline_id=None, scenario_name=None, parent_revision_id=None, inputs=(),
                evidence=(EvidenceSelection(
                    "Cross Border Reconstruction", result.reconstruction_id, True, "Directional Review"
                ),), created_by_user_id="buyer-1", created_at_utc="2026-09-04T14:04:00Z",
                audit=self._audit("2026-09-04T14:04:00Z"),
            )
        scenario_id = create_scenario_revision(
            self.connection, analysis_id=analysis_id, scope_version_id=scope_id,
            gst_baseline_id=None, scenario_name=None, parent_revision_id=None, inputs=(),
            evidence=(
                EvidenceSelection("PBD Observation", self.us_observation, True, "US Exact-Part Evidence"),
                EvidenceSelection("PBD Observation", self.mx_observation, True, "Mexico Exact-Part Evidence"),
                EvidenceSelection("Cross Border Reconstruction", result.reconstruction_id, True, "Directional Review"),
            ), created_by_user_id="buyer-1", created_at_utc="2026-09-04T14:04:00Z",
            audit=self._audit("2026-09-04T14:04:00Z"),
        )
        self.assertIsNotNone(scenario_id)


if __name__ == "__main__":
    unittest.main()
