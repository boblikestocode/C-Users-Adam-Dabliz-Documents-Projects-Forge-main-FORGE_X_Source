from __future__ import annotations

import tempfile
import unittest
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
)
from database.services.audit import AuditContext, verify_audit_chain
from database.services.connection import connect
from database.services.decimals import ExactDecimal
from database.services.outputs import (
    build_output_contract,
    register_generated_output,
    validate_generated_output,
)
from database.services.integrity import run_health_gate
from database.services.events import correct_source_package_number
from database.validation.generate_synthetic import PROFILES, populate


ROOT = Path(__file__).resolve().parents[2]


def audit(event_type: str, timestamp: str) -> AuditContext:
    return AuditContext(
        event_type=event_type, actor_user_id="buyer-1",
        effective_authority="Buyer", occurred_at_utc=timestamp,
        display_timezone="America/New_York", workstation_session="output-test",
        application_version="test", action_method="Automated Test",
        reason_code="TEST",
    )


class OutputServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / "database" / "tests")
        self.base = Path(self.temp.name)
        self.database = self.base / "output.db"
        populate(self.database, PROFILES["smoke"])
        self.connection = connect(self.database)
        event_id = self.connection.execute("SELECT event_id FROM sourcing_event LIMIT 1").fetchone()[0]
        self.event_id = event_id
        self.package_id = self.connection.execute(
            "SELECT source_package_id FROM source_package WHERE event_id = ?", (event_id,)
        ).fetchone()[0]
        scope_id = self.connection.execute("SELECT scope_version_id FROM scope_version LIMIT 1").fetchone()[0]
        observation = self.connection.execute(
            "SELECT observation_id, supplier_id, part_id FROM pbd_observation LIMIT 1"
        ).fetchone()
        engine_id = self.connection.execute("SELECT engine_version_id FROM engine_version LIMIT 1").fetchone()[0]
        rule_id = self.connection.execute(
            "SELECT rule_version_id FROM rule_version WHERE rule_domain = 'calculation'"
        ).fetchone()[0]
        analysis_id = create_analysis(
            self.connection, event_id=event_id, analysis_type="Piece Price",
            readable_name="Output Contract", created_by_user_id="buyer-1",
            created_at_utc="2026-09-04T18:00:00Z",
            audit=audit("Analysis Created", "2026-09-04T18:00:00Z"),
        )
        scenario_id = create_scenario_revision(
            self.connection, analysis_id=analysis_id, scope_version_id=scope_id,
            gst_baseline_id=None, scenario_name="Output", parent_revision_id=None,
            inputs=(ScenarioInput(input_code="DISPLAY_MODE", text_value="Piece Price"),),
            evidence=(EvidenceSelection("PBD Observation", observation[0], True, "Supplier Quote"),),
            created_by_user_id="buyer-1", created_at_utc="2026-09-04T18:00:01Z",
            audit=audit("Scenario Created", "2026-09-04T18:00:01Z"),
        )
        run_id = persist_calculation_run(
            self.connection, scenario_revision_id=scenario_id,
            engine_version_id=engine_id, calculation_rule_version_id=rule_id,
            results=(CalculationResultInput(
                "ACTIVE_PIECE_PRICE", ExactDecimal.parse("9.8750"),
                supplier_id=observation[1], part_id=observation[2],
                normalized_unit_id="USD/PART", currency_id="USD",
                confidence_classification="Confirmed",
                lineage=(ResultLineage("PBD Observation", observation[0], "Governing Supplier Quote"),),
            ),),
            started_at_utc="2026-09-04T18:00:02Z",
            completed_at_utc="2026-09-04T18:00:03Z",
            audit=audit("Calculation Completed", "2026-09-04T18:00:03Z"),
        )
        self.snapshot_id = finalize_analysis(
            self.connection, analysis_id=analysis_id,
            scenario_revision_id=scenario_id, calculation_run_id=run_id,
            finalized_by_user_id="buyer-1",
            finalized_at_utc="2026-09-04T18:00:04Z",
            presentation_rule_version_id=rule_id,
            audit=audit("Analysis Finalized", "2026-09-04T18:00:04Z"),
        )

    def tearDown(self) -> None:
        self.connection.close()
        self.temp.cleanup()

    def test_contract_pins_evidence_calculation_results_and_rules(self) -> None:
        contract = build_output_contract(
            self.connection, finalized_snapshot_id=self.snapshot_id
        )
        self.assertEqual(contract.results[0]["result_code"], "ACTIVE_PIECE_PRICE")
        self.assertEqual(contract.results[0]["decimal_coefficient"], "98750")
        self.assertEqual(len(contract.contract_hash), 64)
        self.assertEqual(len(contract.evidence_manifest_hash), 64)
        self.assertEqual(len(contract.calculation_output_manifest_hash), 64)

    def test_registration_rejects_output_from_a_different_contract(self) -> None:
        output = self.base / "SP-SYNTHETIC-001 - Synthetic Sourcing Event - buyer-analysis.xlsx"
        output.write_bytes(b"synthetic workbook bytes")
        with self.assertRaisesRegex(ValueError, "does not match"):
            register_generated_output(
                self.connection, finalized_snapshot_id=self.snapshot_id,
                artifact_type="Buyer Workbook", output_path=output,
                rendered_contract_hash="0" * 64, renderer_version="test-renderer",
                created_by_user_id="buyer-1", created_at_utc="2026-09-04T18:00:05Z",
                audit=audit("Output Registered", "2026-09-04T18:00:05Z"),
            )
        self.assertEqual(
            self.connection.execute("SELECT COUNT(*) FROM generated_output_artifact").fetchone()[0], 0
        )

    def test_registered_output_detects_later_file_tampering(self) -> None:
        output = self.base / "SP-SYNTHETIC-001 - Synthetic Sourcing Event - buyer-analysis.xlsx"
        output.write_bytes(b"synthetic workbook bytes")
        contract = build_output_contract(
            self.connection, finalized_snapshot_id=self.snapshot_id
        )
        artifact = register_generated_output(
            self.connection, finalized_snapshot_id=self.snapshot_id,
            artifact_type="Buyer Workbook", output_path=output,
            rendered_contract_hash=contract.contract_hash,
            renderer_version="test-renderer", created_by_user_id="buyer-1",
            created_at_utc="2026-09-04T18:00:05Z",
            audit=audit("Output Registered", "2026-09-04T18:00:05Z"),
        )
        output.write_bytes(b"tampered workbook bytes")
        self.assertFalse(validate_generated_output(
            self.connection,
            generated_output_artifact_id=artifact.generated_output_artifact_id,
            output_path=output, validated_at_utc="2026-09-04T18:00:06Z",
            audit=audit("Output Validated", "2026-09-04T18:00:06Z"),
        ))
        statuses = [row[0] for row in self.connection.execute(
            """SELECT validation_status FROM generated_output_validation_event
               WHERE generated_output_artifact_id = ? ORDER BY validated_at_utc""",
            (artifact.generated_output_artifact_id,),
        )]
        self.assertEqual(statuses, ["Verified", "Failed"])
        self.assertIn(
            "GENERATED_OUTPUT_VALIDATION_FAILED",
            {finding.code for finding in run_health_gate(self.connection)},
        )
        self.assertEqual(verify_audit_chain(self.connection), [])

    def test_finalized_output_name_uses_identity_at_finalization_cutoff(self) -> None:
        correct_source_package_number(
            self.connection, source_package_id=self.package_id,
            corrected_package_number="SP-NEW-002", correction_reason="Later correction",
            corrected_by_user_id="buyer-1", recorded_at_utc="2026-09-04T19:00:00Z",
            audit=audit("Source Package Corrected", "2026-09-04T19:00:00Z"),
        )
        contract = build_output_contract(
            self.connection, finalized_snapshot_id=self.snapshot_id
        )
        historical = self.base / "SP-SYNTHETIC-001 - Synthetic Sourcing Event - final.xlsx"
        historical.write_bytes(b"historical finalized output")
        artifact = register_generated_output(
            self.connection, finalized_snapshot_id=self.snapshot_id,
            artifact_type="Buyer Workbook", output_path=historical,
            rendered_contract_hash=contract.contract_hash,
            renderer_version="test-renderer", created_by_user_id="buyer-1",
            created_at_utc="2026-09-04T19:01:00Z",
            audit=audit("Output Registered", "2026-09-04T19:01:00Z"),
        )
        self.assertTrue(artifact.generated_output_artifact_id)
        current_name = self.base / "SP-NEW-002 - Synthetic Sourcing Event - final.xlsx"
        current_name.write_bytes(b"wrong cutoff name")
        with self.assertRaisesRegex(ValueError, "must begin with SP-SYNTHETIC-001"):
            register_generated_output(
                self.connection, finalized_snapshot_id=self.snapshot_id,
                artifact_type="Supporting Export", output_path=current_name,
                rendered_contract_hash=contract.contract_hash,
                renderer_version="test-renderer", created_by_user_id="buyer-1",
                created_at_utc="2026-09-04T19:02:00Z",
                audit=audit("Output Registered", "2026-09-04T19:02:00Z"),
            )


if __name__ == "__main__":
    unittest.main()
