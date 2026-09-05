from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path

from database.services.analysis import (
    CalculationResultInput,
    EvidenceSelection,
    ResultLineage,
    ScenarioInput,
    create_analysis,
    create_scenario_revision,
    finalize_analysis,
    persist_calculation_run,
    start_calculation_run,
    terminate_calculation_run,
)
from database.services.audit import AuditContext, verify_audit_chain
from database.services.connection import connect
from database.services.decimals import ExactDecimal
from database.services.economic_age import confirm_economic_dates, classify_economic_age
from database.services.integrity import verify_analysis_run_status_history, verify_finalized_analyses
from database.validation.generate_synthetic import PROFILES, populate


ROOT = Path(__file__).resolve().parents[2]


def audit(event_type: str, timestamp: str) -> AuditContext:
    return AuditContext(
        event_type=event_type,
        actor_user_id="buyer-1",
        effective_authority="Buyer",
        occurred_at_utc=timestamp,
        display_timezone="America/New_York",
        workstation_session="analysis-test",
        application_version="test",
        action_method="Automated Test",
        reason_code="TEST",
    )


class AnalysisServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / "database" / "tests")
        self.database_path = Path(self.temp.name) / "analysis.db"
        populate(self.database_path, PROFILES["smoke"])
        self.connection = connect(self.database_path)
        self.event_id = self.connection.execute("SELECT event_id FROM sourcing_event LIMIT 1").fetchone()[0]
        self.scope_id = self.connection.execute("SELECT scope_version_id FROM scope_version LIMIT 1").fetchone()[0]
        self.observation = self.connection.execute(
            "SELECT observation_id, supplier_id, part_id FROM pbd_observation LIMIT 1"
        ).fetchone()
        self.engine_id = self.connection.execute("SELECT engine_version_id FROM engine_version LIMIT 1").fetchone()[0]
        self.rule_id = self.connection.execute(
            "SELECT rule_version_id FROM rule_version WHERE rule_domain = 'calculation'"
        ).fetchone()[0]

    def tearDown(self) -> None:
        self.connection.close()
        self.temp.cleanup()

    def _completed_analysis(self) -> tuple[str, str, str]:
        analysis_id = create_analysis(
            self.connection,
            event_id=self.event_id,
            analysis_type="Piece Price",
            readable_name="Synthetic Piece Price Analysis",
            created_by_user_id="buyer-1",
            created_at_utc="2026-09-03T13:00:00Z",
            audit=audit("Analysis Created", "2026-09-03T13:00:00Z"),
        )
        scenario_id = create_scenario_revision(
            self.connection,
            analysis_id=analysis_id,
            scope_version_id=self.scope_id,
            gst_baseline_id=None,
            scenario_name="Initial",
            parent_revision_id=None,
            inputs=(ScenarioInput(input_code="DISPLAY_MODE", text_value="Piece Price"),),
            evidence=(EvidenceSelection("PBD Observation", self.observation[0], True, "Supplier Quote"),),
            created_by_user_id="buyer-1",
            created_at_utc="2026-09-03T13:00:01Z",
            audit=audit("Scenario Created", "2026-09-03T13:00:01Z"),
        )
        run_id = persist_calculation_run(
            self.connection,
            scenario_revision_id=scenario_id,
            engine_version_id=self.engine_id,
            calculation_rule_version_id=self.rule_id,
            results=(
                CalculationResultInput(
                    result_code="ACTIVE_PIECE_PRICE",
                    exact_decimal=ExactDecimal.parse("9.8750"),
                    supplier_id=self.observation[1],
                    part_id=self.observation[2],
                    normalized_unit_id="USD/PART",
                    currency_id="USD",
                    confidence_classification="Confirmed",
                    lineage=(ResultLineage("PBD Observation", self.observation[0], "Governing Supplier Quote"),),
                ),
            ),
            started_at_utc="2026-09-03T13:00:02Z",
            completed_at_utc="2026-09-03T13:00:03Z",
            audit=audit("Calculation Completed", "2026-09-03T13:00:03Z"),
        )
        return analysis_id, scenario_id, run_id

    def test_analysis_finalization_freezes_results_and_audit_anchor(self) -> None:
        analysis_id, scenario_id, run_id = self._completed_analysis()
        snapshot_id = finalize_analysis(
            self.connection,
            analysis_id=analysis_id,
            scenario_revision_id=scenario_id,
            calculation_run_id=run_id,
            finalized_by_user_id="buyer-1",
            finalized_at_utc="2026-09-03T13:00:04Z",
            presentation_rule_version_id=self.rule_id,
            audit=audit("Analysis Finalized", "2026-09-03T13:00:04Z"),
        )
        snapshot = self.connection.execute(
            "SELECT audit_chain_anchor, open_action_count FROM finalized_snapshot WHERE finalized_snapshot_id = ?",
            (snapshot_id,),
        ).fetchone()
        final_audit_hash = self.connection.execute(
            "SELECT event_hash FROM audit_event ORDER BY recorded_sequence DESC LIMIT 1"
        ).fetchone()[0]
        self.assertEqual(snapshot[0], final_audit_hash)
        self.assertEqual(snapshot[1], 0)
        result = self.connection.execute(
            "SELECT result_code, decimal_coefficient, decimal_scale FROM snapshot_result WHERE finalized_snapshot_id = ?",
            (snapshot_id,),
        ).fetchone()
        self.assertEqual(tuple(result), ("ACTIVE_PIECE_PRICE", "98750", 4))
        pin = self.connection.execute(
            """SELECT cache_generation_id, registry_version
               FROM scenario_registry_cache_pin WHERE scenario_revision_id = ?""",
            (scenario_id,),
        ).fetchone()
        self.assertIsNotNone(pin)
        self.assertEqual(pin[1], "synthetic-registry-v1")
        self.assertEqual(verify_audit_chain(self.connection), [])
        self.assertEqual(verify_analysis_run_status_history(self.connection), [])
        self.assertEqual(verify_finalized_analyses(self.connection), [])

    def test_new_scenario_rejects_old_evidence_without_a_classification_job(self):
        confirm_economic_dates(
            self.connection, observation_ids=(self.observation[0],),
            confirmed_economic_date="2020-01-01", date_precision="Day",
            confirmed_by_user_id="buyer-1", confirmation_reason="Confirmed source submission",
            recorded_at_utc="2026-09-03T12:00:00Z",
            audit=audit("Date Confirmed", "2026-09-03T12:00:00Z"),
        )
        count = self.connection.execute("SELECT COUNT(*) FROM scenario_revision").fetchone()[0]
        with self.assertRaisesRegex(ValueError, "economic-age exclusion"):
            self._completed_analysis()
        self.assertEqual(self.connection.execute("SELECT COUNT(*) FROM scenario_revision").fetchone()[0], count)

    def test_finalization_consumes_corrected_date_despite_stale_context_classification(self):
        for value, timestamp in (("2020-01-01", "2026-09-02T12:00:00Z"),
                                 ("2026-01-01", "2026-09-03T12:00:00Z")):
            confirm_economic_dates(
                self.connection, observation_ids=(self.observation[0],),
                confirmed_economic_date=value, date_precision="Day",
                confirmed_by_user_id="buyer-1", confirmation_reason="Verified supplier submission",
                recorded_at_utc=timestamp, audit=audit("Date Confirmed", timestamp),
            )
            if value == "2020-01-01":
                classify_economic_age(
                    self.connection, observation_ids=(self.observation[0],),
                    as_of_date=date(2026, 9, 2), eligibility_rule_version_id=self.rule_id,
                    confirmed_by_user_id="buyer-1", recorded_at_utc=timestamp,
                    audit=audit("Age Classified", timestamp),
                )
        analysis_id, scenario_id, run_id = self._completed_analysis()
        snapshot = finalize_analysis(
            self.connection, analysis_id=analysis_id, scenario_revision_id=scenario_id,
            calculation_run_id=run_id, finalized_by_user_id="buyer-1",
            finalized_at_utc="2026-09-03T13:00:04Z", presentation_rule_version_id=self.rule_id,
            audit=audit("Analysis Finalized", "2026-09-03T13:00:04Z"),
        )
        self.assertIsNotNone(snapshot)
        self.assertEqual(verify_finalized_analyses(self.connection), [])

    def test_result_without_lineage_rolls_back_complete_run(self) -> None:
        analysis_id = create_analysis(
            self.connection, event_id=self.event_id, analysis_type="Test",
            readable_name="Test", created_by_user_id="buyer-1",
            created_at_utc="2026-09-03T13:00:00Z",
            audit=audit("Analysis Created", "2026-09-03T13:00:00Z"),
        )
        scenario_id = create_scenario_revision(
            self.connection, analysis_id=analysis_id,
            scope_version_id=self.scope_id, gst_baseline_id=None,
            scenario_name=None, parent_revision_id=None, inputs=(),
            evidence=(EvidenceSelection("PBD Observation", self.observation[0], True, "Supplier Quote"),),
            created_by_user_id="buyer-1", created_at_utc="2026-09-03T13:00:01Z",
            audit=audit("Scenario Created", "2026-09-03T13:00:01Z"),
        )
        before = self.connection.execute("SELECT COUNT(*) FROM calculation_run").fetchone()[0]
        with self.assertRaisesRegex(ValueError, "requires lineage"):
            persist_calculation_run(
                self.connection, scenario_revision_id=scenario_id,
                engine_version_id=self.engine_id,
                calculation_rule_version_id=self.rule_id,
                results=(CalculationResultInput("UNSUPPORTED", ExactDecimal.parse("1.0")),),
                started_at_utc="2026-09-03T13:00:02Z",
                completed_at_utc="2026-09-03T13:00:03Z",
                audit=audit("Calculation Completed", "2026-09-03T13:00:03Z"),
            )
        after = self.connection.execute("SELECT COUNT(*) FROM calculation_run").fetchone()[0]
        self.assertEqual(before, after)

    def test_lineage_must_be_in_included_scenario_manifest(self) -> None:
        analysis_id = create_analysis(
            self.connection, event_id=self.event_id, analysis_type="Test",
            readable_name="Manifest enforcement", created_by_user_id="buyer-1",
            created_at_utc="2026-09-03T14:00:00Z",
            audit=audit("Analysis Created", "2026-09-03T14:00:00Z"),
        )
        scenario_id = create_scenario_revision(
            self.connection, analysis_id=analysis_id, scope_version_id=self.scope_id,
            gst_baseline_id=None, scenario_name=None, parent_revision_id=None, inputs=(),
            evidence=(EvidenceSelection("PBD Observation", self.observation[0], False,
                                        "Supplier Quote", "Not selected"),),
            created_by_user_id="buyer-1", created_at_utc="2026-09-03T14:00:01Z",
            audit=audit("Scenario Created", "2026-09-03T14:00:01Z"),
        )
        with self.assertRaisesRegex(ValueError, "not included"):
            persist_calculation_run(
                self.connection, scenario_revision_id=scenario_id,
                engine_version_id=self.engine_id, calculation_rule_version_id=self.rule_id,
                results=(CalculationResultInput(
                    "TEST", ExactDecimal.parse("1"),
                    lineage=(ResultLineage("PBD Observation", self.observation[0], "Input"),),
                ),), started_at_utc="2026-09-03T14:00:02Z",
                completed_at_utc="2026-09-03T14:00:03Z",
                audit=audit("Calculation Completed", "2026-09-03T14:00:03Z"),
            )
        self.assertEqual(self.connection.execute(
            "SELECT COUNT(*) FROM calculation_run WHERE scenario_revision_id = ?", (scenario_id,)
        ).fetchone()[0], 0)

    def test_cancelled_worker_run_is_durable_and_non_governing(self) -> None:
        analysis_id = create_analysis(
            self.connection, event_id=self.event_id, analysis_type="Test",
            readable_name="Cancellation", created_by_user_id="buyer-1",
            created_at_utc="2026-09-03T15:00:00Z",
            audit=audit("Analysis Created", "2026-09-03T15:00:00Z"),
        )
        scenario_id = create_scenario_revision(
            self.connection, analysis_id=analysis_id, scope_version_id=self.scope_id,
            gst_baseline_id=None, scenario_name=None, parent_revision_id=None, inputs=(),
            evidence=(EvidenceSelection("PBD Observation", self.observation[0], True,
                                        "Supplier Quote"),),
            created_by_user_id="buyer-1", created_at_utc="2026-09-03T15:00:01Z",
            audit=audit("Scenario Created", "2026-09-03T15:00:01Z"),
        )
        run_id = start_calculation_run(
            self.connection, scenario_revision_id=scenario_id,
            engine_version_id=self.engine_id,
            calculation_rule_version_id=self.rule_id,
            started_at_utc="2026-09-03T15:00:02Z",
            audit=audit("Calculation Started", "2026-09-03T15:00:02Z"),
        )
        self.assertEqual(terminate_calculation_run(
            self.connection, calculation_run_id=run_id, outcome="Cancelled",
            status_detail="Buyer cancelled at safe boundary",
            completed_at_utc="2026-09-03T15:00:03Z",
            audit=audit("Calculation Cancelled", "2026-09-03T15:00:03Z"),
        ), "Cancelled")
        self.assertEqual(self.connection.execute(
            """SELECT run_status FROM v_current_calculation_run_status
               WHERE calculation_run_id = ?""", (run_id,),
        ).fetchone()[0], "Cancelled")
        self.assertEqual(self.connection.execute(
            """SELECT analysis_status FROM v_current_analysis_status
               WHERE analysis_id = ?""", (analysis_id,),
        ).fetchone()[0], "Cancelled")
        self.assertEqual(self.connection.execute(
            "SELECT COUNT(*) FROM calculation_result WHERE calculation_run_id = ?",
            (run_id,),
        ).fetchone()[0], 0)
        with self.assertRaisesRegex(Exception, "calculation_run is immutable"):
            self.connection.execute(
                "UPDATE calculation_run SET status = 'Failed' WHERE calculation_run_id = ?",
                (run_id,),
            )


if __name__ == "__main__":
    unittest.main()
