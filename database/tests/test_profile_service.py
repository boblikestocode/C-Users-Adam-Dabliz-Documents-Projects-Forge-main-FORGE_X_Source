from __future__ import annotations

import tempfile
import unittest
from unittest.mock import patch
from datetime import date
from pathlib import Path

from database.services.audit import AuditContext, verify_audit_chain
from database.services.connection import connect
from database.services.competitiveness import (
    build_current_historical_context,
    confirm_supplier_competitiveness,
)
from database.services.profiles import (
    build_supplier_family_profile, build_supplier_profile,
    profile_formula_exception_rows,
)
from database.services.economic_age import confirm_economic_dates, classify_economic_age
from database.services.registry_cache import RegistryCacheEntity, install_registry_cache
from database.services.integrity import run_health_gate
from database.services.traffic_lights import (
    REQUIRED_CATEGORIES,
    TrafficLightCategoryInput,
    build_supplier_overall_traffic_light,
    build_supplier_traffic_light,
    derive_supplier_overall_traffic_light,
)
from database.validation.generate_synthetic import PROFILES, populate


ROOT = Path(__file__).resolve().parents[2]


def audit() -> AuditContext:
    return AuditContext(
        event_type="Supplier Profile Built",
        actor_user_id=None,
        effective_authority="System",
        occurred_at_utc="2026-09-03T16:00:00Z",
        display_timezone="America/New_York",
        workstation_session="profile-test",
        application_version="test",
        action_method="Automated Test",
        reason_code="PROFILE_REBUILD",
    )


class SupplierProfileServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / "database" / "tests")
        self.database_path = Path(self.temp.name) / "profile.db"
        populate(self.database_path, PROFILES["smoke"])
        self.connection = connect(self.database_path)
        self.supplier_id, self.plant_id = self.connection.execute(
            "SELECT supplier_id, supplier_plant_id FROM supplier_activity LIMIT 1"
        ).fetchone()
        self.commodity_id = self.connection.execute(
            "SELECT commodity_id FROM commodity_database"
        ).fetchone()[0]
        self.engine_id = self.connection.execute(
            "SELECT engine_version_id FROM engine_version LIMIT 1"
        ).fetchone()[0]
        rules = self.connection.execute(
            "SELECT rule_version_id FROM rule_version ORDER BY rule_domain"
        ).fetchall()
        self.rule_id = rules[0][0]

    def tearDown(self) -> None:
        self.connection.close()
        self.temp.cleanup()

    def _formula_population(self, cutoff: str, **kwargs):
        return profile_formula_exception_rows(
            self.connection, supplier_id=self.supplier_id,
            supplier_plant_id=self.plant_id, commodity_id=self.commodity_id,
            evidence_cutoff_utc=cutoff,
            active_window_start_utc="2024-01-01T00:00:00Z", **kwargs,
        )

    def _formula_profile(self, cutoff: str, **kwargs):
        return build_supplier_profile(
            self.connection, supplier_id=self.supplier_id,
            supplier_plant_id=self.plant_id, commodity_id=self.commodity_id,
            region_code=None, evidence_cutoff_utc=cutoff,
            active_window_start_utc="2024-01-01T00:00:00Z",
            eligibility_rule_version_id=self.rule_id,
            calculation_rule_version_id=self.rule_id, engine_version_id=self.engine_id,
            started_at_utc=cutoff, completed_at_utc=cutoff, audit=audit(), **kwargs,
        )

    def _seed_formula_exceptions(self, recorded_at="2026-09-03T15:00:00Z"):
        observations = tuple(row[0] for row in self.connection.execute(
            """SELECT observation_id FROM pbd_observation
               WHERE supplier_id = ? AND supplier_plant_id = ?
               ORDER BY observation_id LIMIT 2""", (self.supplier_id, self.plant_id),
        ))
        self.assertEqual(len(observations), 2)
        for index, observation_id in enumerate(observations):
            self.connection.execute(
                """INSERT INTO formula_integrity_event
                   (formula_integrity_event_id, observation_id,
                    integrity_rule_version_id, classification,
                    evidence_payload, recorded_at_utc)
                   VALUES (?, ?, ?, 'Formula Reconciliation Exception', '{}', ?)""",
                (f"population-exception-{index}", observation_id, self.rule_id, recorded_at),
            )
        return observations

    def _date_decision(self, observation_id, economic_date, recorded_at):
        confirm_economic_dates(
            self.connection, observation_ids=(observation_id,),
            confirmed_economic_date=economic_date, date_precision="Day",
            confirmed_by_user_id="buyer-1", confirmation_reason="Verified supplier date",
            recorded_at_utc=recorded_at, audit=audit(),
        )
        classify_economic_age(
            self.connection, observation_ids=(observation_id,),
            as_of_date=date(2026, 9, 4), eligibility_rule_version_id=self.rule_id,
            confirmed_by_user_id="buyer-1", recorded_at_utc=recorded_at, audit=audit(),
        )

    def test_context_only_formula_evidence_cannot_drive_current_red(self):
        observations = self._seed_formula_exceptions()
        prior = self._formula_profile("2026-09-03T16:00:00Z")
        self._date_decision(observations[0], "2020-01-01", "2026-09-04T12:00:00Z")
        current = self._formula_profile("2026-09-04T13:00:00Z")
        statuses = lambda run: [row[0] for row in self.connection.execute(
            "SELECT finding_status FROM supplier_profile_finding WHERE supplier_profile_run_id = ?",
            (run.profile_run_id,),
        )]
        self.assertIn("Red", statuses(prior))
        self.assertNotIn("Red", statuses(current))
        self.assertEqual(len(self._formula_population("2026-09-03T16:00:00Z")), 2)
        self.assertEqual(len(self._formula_population("2026-09-04T13:00:00Z")), 1)
        self.assertEqual(self.connection.execute(
            "SELECT COUNT(*) FROM formula_integrity_event WHERE formula_integrity_event_id LIKE 'population-%'"
        ).fetchone()[0], 2)
        self.assertNotIn("SUPPLIER_PROFILE_REPRODUCTION_MISMATCH",
                         {item.code for item in run_health_gate(self.connection)})

    def test_later_eligible_decision_restores_only_later_profile_population(self):
        observations = self._seed_formula_exceptions()
        self._date_decision(observations[0], "2020-01-01", "2026-09-04T12:00:00Z")
        self._date_decision(observations[0], "2026-01-01", "2026-09-05T12:00:00Z")
        self.assertEqual(len(self._formula_population("2026-09-04T13:00:00Z")), 1)
        self.assertEqual(len(self._formula_population("2026-09-05T13:00:00Z")), 2)

    def test_formula_population_requires_observation_and_round_known_at_cutoff(self):
        self._seed_formula_exceptions(recorded_at="2025-12-01T00:00:00Z")
        # Synthetic observations and round memberships are recorded January 15.
        self.assertEqual(self._formula_population("2026-01-01T00:00:00Z"), [])
        self.assertEqual(len(self._formula_population("2026-01-16T00:00:00Z")), 2)
        self.assertEqual(len(self._formula_population(
            "2026-01-01T00:00:00Z", population_version="Legacy")), 2)

    def test_legacy_formula_profile_remains_reproducible_after_eligibility_fix(self):
        observations = self._seed_formula_exceptions()
        self._date_decision(observations[0], "2020-01-01", "2026-09-04T12:00:00Z")
        cutoff = "2026-09-04T13:00:00Z"
        legacy_rows = self._formula_population(cutoff, population_version="Legacy")
        with patch("database.services.profiles.profile_formula_exception_rows", return_value=legacy_rows), patch(
            "database.services.profiles.FORMULA_POPULATION_VERSION", "Legacy"
        ):
            legacy = self._formula_profile(cutoff, scope_population_version="Legacy")
        current = self._formula_profile(cutoff)
        for result, expected_count in ((legacy, 2), (current, 1)):
            self.assertEqual(self.connection.execute(
                """SELECT COUNT(*) FROM profile_evidence_entry
                   WHERE supplier_profile_run_id = ?
                     AND evidence_entity_type = 'Formula Integrity Event'""",
                (result.profile_run_id,),
            ).fetchone()[0], expected_count)
        self.assertNotIn("SUPPLIER_PROFILE_REPRODUCTION_MISMATCH",
                         {item.code for item in run_health_gate(self.connection)})
        with self.assertRaisesRegex(Exception, "immutable"):
            self.connection.execute(
                "UPDATE supplier_profile_reproduction_manifest SET formula_population_version = 'Legacy'"
            )

    def test_profile_counts_records_and_independent_events_separately(self) -> None:
        result = build_supplier_profile(
            self.connection,
            supplier_id=self.supplier_id,
            supplier_plant_id=self.plant_id,
            commodity_id=self.commodity_id,
            region_code=None,
            evidence_cutoff_utc="2026-12-31T23:59:59Z",
            active_window_start_utc="2024-01-01T00:00:00Z",
            eligibility_rule_version_id=self.rule_id,
            calculation_rule_version_id=self.rule_id,
            engine_version_id=self.engine_id,
            started_at_utc="2026-09-03T15:59:59Z",
            completed_at_utc="2026-09-03T16:00:00Z",
            audit=audit(),
        )
        self.assertEqual(result.activity_count, PROFILES["smoke"].parts * PROFILES["smoke"].rounds)
        self.assertEqual(result.independent_event_count, 1)
        metric = self.connection.execute(
            """SELECT decimal_coefficient, decimal_scale,
                      evidence_count, independent_event_count
               FROM supplier_profile_metric
               WHERE supplier_profile_run_id = ? AND metric_code = 'PRICE_CHANGE_EVENT_RATE'""",
            (result.profile_run_id,),
        ).fetchone()
        self.assertEqual(tuple(metric), ("1", 0, PROFILES["smoke"].parts * 2, 1))
        findings = self.connection.execute(
            """SELECT DISTINCT finding_status FROM supplier_profile_finding
               WHERE supplier_profile_run_id = ?""",
            (result.profile_run_id,),
        ).fetchall()
        self.assertEqual([row[0] for row in findings], ["Descriptive Only"])
        self.assertEqual(verify_audit_chain(self.connection), [])

    def test_latest_profile_resolves_by_evidence_cutoff(self) -> None:
        common = dict(
            connection=self.connection,
            supplier_id=self.supplier_id,
            supplier_plant_id=self.plant_id,
            commodity_id=self.commodity_id,
            region_code=None,
            active_window_start_utc="2024-01-01T00:00:00Z",
            eligibility_rule_version_id=self.rule_id,
            calculation_rule_version_id=self.rule_id,
            engine_version_id=self.engine_id,
            started_at_utc="2026-09-03T15:59:59Z",
            completed_at_utc="2026-09-03T16:00:00Z",
            audit=audit(),
        )
        first = build_supplier_profile(evidence_cutoff_utc="2026-06-01T00:00:00Z", **common)
        second = build_supplier_profile(evidence_cutoff_utc="2026-12-31T00:00:00Z", **common)
        latest = self.connection.execute(
            "SELECT supplier_profile_run_id FROM v_latest_supplier_profile"
        ).fetchall()
        self.assertIn((second.profile_run_id,), [tuple(row) for row in latest])
        self.assertNotIn((first.profile_run_id,), [tuple(row) for row in latest])

    def test_health_gate_detects_profile_metric_tampering(self) -> None:
        result = build_supplier_profile(
            self.connection, supplier_id=self.supplier_id,
            supplier_plant_id=self.plant_id, commodity_id=self.commodity_id,
            region_code=None, evidence_cutoff_utc="2026-12-31T23:59:59Z",
            active_window_start_utc="2024-01-01T00:00:00Z",
            eligibility_rule_version_id=self.rule_id,
            calculation_rule_version_id=self.rule_id,
            engine_version_id=self.engine_id,
            started_at_utc="2026-09-03T15:59:59Z",
            completed_at_utc="2026-09-03T16:00:00Z", audit=audit(),
        )
        self.assertNotIn(
            "SUPPLIER_PROFILE_REPRODUCTION_MISMATCH",
            {finding.code for finding in run_health_gate(self.connection)},
        )
        self.connection.execute(
            """UPDATE supplier_profile_metric SET decimal_coefficient = '999'
               WHERE supplier_profile_run_id = ? AND metric_code = 'ACTIVITY_COUNT'""",
            (result.profile_run_id,),
        )
        self.assertIn(
            "SUPPLIER_PROFILE_REPRODUCTION_MISMATCH",
            {finding.code for finding in run_health_gate(self.connection)},
        )

    def test_repeated_formula_exceptions_create_red_commodity_finding(self) -> None:
        observations = self.connection.execute(
            """SELECT DISTINCT observation.observation_id
               FROM pbd_observation observation
               JOIN round_observation membership
                 ON membership.observation_id = observation.observation_id
               JOIN supplier_quote_round round
                 ON round.quote_round_id = membership.quote_round_id
               JOIN sourcing_event event ON event.event_id = round.event_id
               WHERE observation.supplier_id = ?
                 AND observation.supplier_plant_id = ?
                 AND event.commodity_id = ?
               ORDER BY observation.observation_id LIMIT 2""",
            (self.supplier_id, self.plant_id, self.commodity_id),
        ).fetchall()
        self.assertEqual(len(observations), 2)
        for index, observation in enumerate(observations):
            self.connection.execute(
                """INSERT INTO formula_integrity_event
                   (formula_integrity_event_id, observation_id,
                    integrity_rule_version_id, classification,
                    evidence_payload, recorded_at_utc)
                   VALUES (?, ?, ?, 'Formula Reconciliation Exception', '{}', ?)""",
                (f"profile-formula-exception-{index}", observation[0], self.rule_id,
                 f"2026-09-03T15:5{index}:00Z"),
            )
        result = build_supplier_profile(
            self.connection, supplier_id=self.supplier_id,
            supplier_plant_id=self.plant_id, commodity_id=self.commodity_id,
            region_code=None, evidence_cutoff_utc="2026-12-31T23:59:59Z",
            active_window_start_utc="2024-01-01T00:00:00Z",
            eligibility_rule_version_id=self.rule_id,
            calculation_rule_version_id=self.rule_id,
            engine_version_id=self.engine_id,
            started_at_utc="2026-09-03T15:59:59Z",
            completed_at_utc="2026-09-03T16:00:00Z", audit=audit(),
        )
        finding = self.connection.execute(
            """SELECT supplier_profile_finding_id, finding_status, evidence_count
               FROM supplier_profile_finding
               WHERE supplier_profile_run_id = ?
                 AND finding_type = 'Repeated Formula Reconciliation Exceptions'""",
            (result.profile_run_id,),
        ).fetchone()
        self.assertEqual(tuple(finding[1:]), ("Red", 2))
        evidence = self.connection.execute(
            """SELECT COUNT(*) FROM profile_evidence_entry
               WHERE supplier_profile_run_id = ?
                 AND evidence_entity_type = 'Formula Integrity Event'""",
            (result.profile_run_id,),
        ).fetchone()[0]
        self.assertEqual(evidence, 2)
        quote_round = self.connection.execute(
            """SELECT round.quote_round_id, round.event_id
               FROM supplier_quote_round round
               JOIN sourcing_event event ON event.event_id = round.event_id
               WHERE round.supplier_id = ? AND event.commodity_id = ?
               ORDER BY round.round_number LIMIT 1""",
            (self.supplier_id, self.commodity_id),
        ).fetchone()
        current_id = confirm_supplier_competitiveness(
            self.connection, event_id=quote_round[1], supplier_id=self.supplier_id,
            quote_population_id=quote_round[0], competitiveness_status="Green",
            evidence_entity_type="Quote Round", evidence_entity_id=quote_round[0],
            explanation_payload={"basis": "supported current package result"},
            confirmed_by_user_id="buyer-current", confirmed_at_utc="2026-09-03T16:01:00Z",
            audit=audit(),
        )
        context = build_current_historical_context(
            self.connection,
            supplier_competitiveness_observation_id=current_id,
            supplier_profile_run_id=result.profile_run_id,
            generated_at_utc="2026-09-03T16:02:00Z", audit=audit(),
        )
        self.assertEqual(context.current_event_status, "Green")
        self.assertEqual(context.historical_profile_status, "Red")
        self.assertEqual(context.historical_risk_notice["notice_type"],
                         "Historical Risk Notice")
        retained_current = self.connection.execute(
            """SELECT current_event_status FROM supplier_current_historical_context
               WHERE supplier_current_historical_context_id = ?""", (context.context_id,),
        ).fetchone()[0]
        self.assertEqual(retained_current, "Green")
        categories = tuple(
            TrafficLightCategoryInput(
                category_code=category,
                category_status=("Red" if category == "Overhead" else
                                 "Yellow" if category == "PBD Quality" else "Green"),
                confidence_classification="High",
                material_commercial_impact=category == "Overhead",
                recurrence_count=2 if category == "Overhead" else 1,
                evidence_entity_type=("Supplier Competitiveness Observation"
                                      if category == "Current Market Position" else
                                      "Supplier Profile Finding"
                                      if category == "Overhead" else "Supplier Profile Run"),
                evidence_entity_id=(current_id
                                    if category == "Current Market Position" else
                                    finding[0] if category == "Overhead" else
                                    result.profile_run_id),
                explanation=f"Supported {category.lower()} conclusion.",
            ) for category in REQUIRED_CATEGORIES
        )
        traffic_light = build_supplier_traffic_light(
            self.connection, supplier_id=self.supplier_id,
            commodity_id=self.commodity_id,
            evidence_cutoff_utc="2026-12-31T23:59:59Z",
            categories=categories, generated_at_utc="2026-09-03T16:03:00Z",
            audit=audit(),
        )
        self.assertEqual(traffic_light.overall_status, "Red")
        self.assertEqual(traffic_light.governing_categories, ("Overhead",))
        overall = build_supplier_overall_traffic_light(
            self.connection, supplier_id=self.supplier_id,
            evidence_cutoff_utc="2026-12-31T23:59:59Z",
            commodity_assessment_ids=(traffic_light.assessment_id,),
            generated_at_utc="2026-09-03T16:04:00Z", audit=audit(),
        )
        self.assertEqual(overall.overall_status, "Red")
        self.assertEqual(overall.governing_commodities, (self.commodity_id,))
        self.assertEqual(self.connection.execute(
            """SELECT competitiveness_status
               FROM supplier_competitiveness_observation
               WHERE supplier_competitiveness_observation_id = ?""", (current_id,),
        ).fetchone()[0], "Green")
        synthetic_status, synthetic_governing, _, _ = (
            derive_supplier_overall_traffic_light([
                {"commodity_id": "COM-GREEN",
                 "supplier_traffic_light_assessment_id": "assessment-green",
                 "overall_status": "Green", "evidence_cutoff_utc": "2026-09-01Z",
                 "category_manifest_hash": "green-hash"},
                {"commodity_id": "COM-RED",
                 "supplier_traffic_light_assessment_id": "assessment-red",
                 "overall_status": "Red", "evidence_cutoff_utc": "2026-09-01Z",
                 "category_manifest_hash": "red-hash"},
            ])
        )
        self.assertEqual((synthetic_status, synthetic_governing),
                         ("Red", ("COM-RED",)))
        self.assertNotIn(
            "SUPPLIER_OVERALL_TRAFFIC_LIGHT_MISMATCH",
            {item.code for item in run_health_gate(self.connection)},
        )
        with self.assertRaisesRegex(Exception, "immutable"):
            self.connection.execute(
                """UPDATE supplier_overall_traffic_light_assessment
                   SET commodity_manifest_hash = ?""", ("0" * 64,),
            )
        self.connection.execute("DROP TRIGGER no_update_supplier_overall_traffic_light")
        self.connection.execute(
            """UPDATE supplier_overall_traffic_light_assessment
               SET commodity_manifest_hash = ?
               WHERE supplier_overall_traffic_light_assessment_id = ?""",
            ("0" * 64, overall.assessment_id),
        )
        self.assertIn(
            "SUPPLIER_OVERALL_TRAFFIC_LIGHT_MISMATCH",
            {item.code for item in run_health_gate(self.connection)},
        )
        self.assertNotIn(
            "SUPPLIER_TRAFFIC_LIGHT_MISMATCH",
            {item.code for item in run_health_gate(self.connection)},
        )
        with self.assertRaisesRegex(Exception, "immutable"):
            self.connection.execute(
                "UPDATE supplier_traffic_light_category SET category_status = 'Yellow'"
            )
        self.connection.execute("DROP TRIGGER no_update_supplier_traffic_light_category")
        self.connection.execute(
            """UPDATE supplier_traffic_light_category SET category_status = 'Yellow'
               WHERE supplier_traffic_light_assessment_id = ?
                 AND category_code = 'Overhead'""", (traffic_light.assessment_id,),
        )
        self.assertIn(
            "SUPPLIER_TRAFFIC_LIGHT_MISMATCH",
            {item.code for item in run_health_gate(self.connection)},
        )
        self.assertNotIn(
            "SUPPLIER_PROFILE_REPRODUCTION_MISMATCH",
            {item.code for item in run_health_gate(self.connection)},
        )
        self.assertNotIn(
            "CURRENT_HISTORICAL_CONTEXT_MISMATCH",
            {item.code for item in run_health_gate(self.connection)},
        )
        with self.assertRaisesRegex(Exception, "immutable"):
            self.connection.execute(
                "UPDATE supplier_current_historical_context SET context_manifest_hash = ?",
                ("0" * 64,),
            )
        self.connection.execute("DROP TRIGGER no_update_supplier_current_historical_context")
        self.connection.execute(
            """UPDATE supplier_current_historical_context
               SET context_manifest_hash = ?
               WHERE supplier_current_historical_context_id = ?""",
            ("0" * 64, context.context_id),
        )
        self.assertIn(
            "CURRENT_HISTORICAL_CONTEXT_MISMATCH",
            {item.code for item in run_health_gate(self.connection)},
        )

    def test_family_rollup_retains_each_scoped_supplier_profile(self) -> None:
        suppliers = tuple(row[0] for row in self.connection.execute(
            "SELECT DISTINCT supplier_id FROM supplier_activity ORDER BY supplier_id LIMIT 2"
        ))
        self.assertEqual(len(suppliers), 2)
        cutoff = "2026-12-31T23:59:59Z"
        window = "2024-01-01T00:00:00Z"
        profile_ids = []
        for index, supplier_id in enumerate(suppliers):
            result = build_supplier_profile(
                self.connection, supplier_id=supplier_id, supplier_plant_id=None,
                commodity_id=self.commodity_id, region_code=None,
                evidence_cutoff_utc=cutoff, active_window_start_utc=window,
                eligibility_rule_version_id=self.rule_id,
                calculation_rule_version_id=self.rule_id,
                engine_version_id=self.engine_id,
                started_at_utc=f"2026-09-03T16:0{index}:00Z",
                completed_at_utc=f"2026-09-03T16:0{index}:01Z", audit=audit(),
            )
            profile_ids.append(result.profile_run_id)
        cache_id = install_registry_cache(
            self.connection, registry_publication_id="family-publication",
            registry_version="family-v1",
            entities=(RegistryCacheEntity(
                "Supplier Family", "family-1",
                {"family_name": "Example Holdings",
                 "member_supplier_ids": list(suppliers)},
            ),), digital_signature="valid",
            signature_verifier=lambda payload, signature: signature == "valid",
            imported_at_utc="2026-09-03T16:10:00Z", expires_at_utc=None,
            audit=audit(),
        )
        family = build_supplier_family_profile(
            self.connection, stable_family_id="family-1",
            registry_cache_generation_id=cache_id, commodity_id=self.commodity_id,
            evidence_cutoff_utc=cutoff, active_window_start_utc=window,
            eligibility_rule_version_id=self.rule_id,
            calculation_rule_version_id=self.rule_id, engine_version_id=self.engine_id,
            started_at_utc="2026-09-03T16:11:00Z",
            completed_at_utc="2026-09-03T16:11:01Z", audit=audit(),
        )
        self.assertEqual(set(family.contributing_profile_run_ids), set(profile_ids))
        members = self.connection.execute(
            """SELECT supplier_id, supplier_profile_run_id
               FROM supplier_family_profile_member
               WHERE supplier_family_profile_run_id = ? ORDER BY supplier_id""",
            (family.family_profile_run_id,),
        ).fetchall()
        self.assertEqual([tuple(row) for row in members],
                         list(zip(suppliers, profile_ids)))
        metric = self.connection.execute(
            """SELECT decimal_coefficient FROM supplier_family_profile_metric
               WHERE supplier_family_profile_run_id = ? AND metric_code = 'ACTIVITY_COUNT'""",
            (family.family_profile_run_id,),
        ).fetchone()
        self.assertEqual(int(metric[0]), family.activity_count)
        self.assertNotIn(
            "SUPPLIER_FAMILY_PROFILE_REPRODUCTION_MISMATCH",
            {finding.code for finding in run_health_gate(self.connection)},
        )
        with self.assertRaisesRegex(Exception, "immutable"):
            self.connection.execute(
                "UPDATE supplier_family_profile_member SET supplier_id = 'changed'"
            )


if __name__ == "__main__":
    unittest.main()
