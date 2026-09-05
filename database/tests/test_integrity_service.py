from __future__ import annotations

import tempfile
import unittest
import sqlite3
from pathlib import Path

from database.services.connection import connect
from database.services.audit import AuditContext
from database.services.decimals import ExactDecimal
from database.services.formulas import persist_formula_integrity
from database.services.integrity import assess_analysis_readiness, run_health_gate
from database.validation.generate_synthetic import PROFILES, populate


ROOT = Path(__file__).resolve().parents[2]


class IntegrityServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / "database" / "tests")
        self.database_path = Path(self.temp.name) / "health.db"
        populate(self.database_path, PROFILES["smoke"])
        self.connection = connect(self.database_path)

    def tearDown(self) -> None:
        self.connection.close()
        self.temp.cleanup()

    def test_clean_synthetic_database_passes_health_gate(self) -> None:
        self.assertEqual(run_health_gate(self.connection), [])

    def test_health_gate_detects_completed_result_without_lineage(self) -> None:
        event_id = self.connection.execute("SELECT event_id FROM sourcing_event LIMIT 1").fetchone()[0]
        scope_id = self.connection.execute("SELECT scope_version_id FROM scope_version LIMIT 1").fetchone()[0]
        self.connection.execute(
            """INSERT INTO analysis VALUES
               ('corrupt-analysis', ?, 'Sourcing', 'Corruption Test', 'tester',
                '2026-09-03T18:58:00Z')""", (event_id,),
        )
        self.connection.execute(
            """INSERT INTO analysis_status_event VALUES
               ('corrupt-analysis-status', 'corrupt-analysis', 'Working',
                'Corruption test', 'tester', NULL, '2026-09-03T18:58:00Z')"""
        )
        scenario_id = "corrupt-scenario"
        self.connection.execute(
            """INSERT INTO scenario_revision VALUES
               (?, 'corrupt-analysis', 1, NULL, 'Corruption Test', ?, NULL,
                'assumptions-hash', 'tester', '2026-09-03T18:58:30Z')""",
            (scenario_id, scope_id),
        )
        engine_id = self.connection.execute(
            "SELECT engine_version_id FROM engine_version LIMIT 1"
        ).fetchone()[0]
        rule_id = self.connection.execute(
            "SELECT rule_version_id FROM rule_version WHERE rule_domain = 'calculation' LIMIT 1"
        ).fetchone()[0]
        run_id = "corrupt-completed-run"
        self.connection.execute(
            """INSERT INTO calculation_run VALUES
               (?, ?, ?, ?, 'Completed', 'incorrect-input-hash', 'output-hash',
                '2026-09-03T18:59:00Z', '2026-09-03T19:00:00Z')""",
            (run_id, scenario_id, engine_id, rule_id),
        )
        self.connection.execute(
            """INSERT INTO calculation_run_status_event VALUES
               ('corrupt-run-status', ?, 'Completed', 'output-hash',
                'Corruption test', NULL, '2026-09-03T19:00:00Z')""",
            (run_id,),
        )
        self.connection.execute(
            """INSERT INTO calculation_result
               (calculation_result_id, calculation_run_id, result_code,
                recorded_at_utc) VALUES
               ('orphan-result', ?, 'CORRUPTION_TEST', '2026-09-03T19:00:00Z')""",
            (run_id,),
        )
        codes = {finding.code for finding in run_health_gate(self.connection)}
        self.assertIn("COMPLETED_RESULT_MISSING_LINEAGE", codes)

    def test_health_gate_detects_migration_and_decimal_corruption(self) -> None:
        self.connection.execute(
            "UPDATE schema_migration SET migration_checksum = 'wrong' WHERE migration_version = '0005'"
        )
        observation_id, worksheet_id = self.connection.execute(
            """SELECT po.observation_id, sws.worksheet_id
               FROM pbd_observation po
               JOIN source_occurrence so ON so.occurrence_id = po.occurrence_id
               JOIN source_worksheet sws ON sws.worksheet_id = so.worksheet_id
               LIMIT 1"""
        ).fetchone()
        self.connection.execute(
            """INSERT INTO source_datum
               (source_datum_id, worksheet_id, cell_or_range,
                submitted_lexeme, recorded_at_utc)
               VALUES ('bad-source', ?, 'ZZ999', '1.2345', '2026-09-03T19:00:00Z')""",
            (worksheet_id,),
        )
        self.connection.execute(
            """INSERT INTO submitted_datum
               (submitted_datum_id, observation_id, field_code,
                source_datum_id, submitted_lexeme, decimal_coefficient,
                decimal_scale, governing_1e4, normalized_unit_id, currency_id, precision_status,
                recorded_at_utc)
               VALUES ('bad-datum', ?, 'BAD_TEST', 'bad-source', '1.2345',
                       '12345', 4, 999999, 'USD/PART', 'USD', 'Eligible',
                       '2026-09-03T19:00:00Z')""",
            (observation_id,),
        )
        codes = {finding.code for finding in run_health_gate(self.connection)}
        self.assertIn("MIGRATION_CHECKSUM_MISMATCH", codes)
        self.assertIn("GOVERNING_DECIMAL_MISMATCH", codes)

    def test_health_gate_detects_unsupported_polymorphic_reference(self) -> None:
        self.connection.execute(
            "INSERT INTO knowledge_item VALUES ('item', 'TEST', 'user', '2026-09-03T19:00:00Z')"
        )
        self.connection.execute(
            """INSERT INTO knowledge_version
               (knowledge_version_id, knowledge_item_id, version_number,
                knowledge_level, structured_payload, payload_hash, status,
                recorded_from_utc)
               VALUES ('version', 'item', 1, 'Observed Evidence', '{}',
                       'hash', 'Active', '2026-09-03T19:00:00Z')"""
        )
        self.connection.execute(
            """INSERT INTO knowledge_evidence
               (knowledge_evidence_id, knowledge_version_id,
                evidence_entity_type, evidence_entity_id, evidence_role,
                recorded_at_utc)
               VALUES ('evidence', 'version', 'Unknown Entity', 'missing',
                       'Supporting', '2026-09-03T19:00:00Z')"""
        )
        findings = run_health_gate(self.connection)
        self.assertIn("UNSUPPORTED_POLYMORPHIC_TYPE", {finding.code for finding in findings})

    def test_health_gate_detects_committed_import_without_discovery_manifest(self) -> None:
        session_id = self.connection.execute(
            "SELECT import_session_id FROM import_session LIMIT 1"
        ).fetchone()[0]
        self.connection.execute(
            """INSERT INTO import_transaction
               (import_transaction_id, import_session_id, transaction_sequence,
                status, started_at_utc, completed_at_utc)
               VALUES ('missing-discovery', ?, 2, 'Committed',
                       '2026-09-03T19:00:00Z', '2026-09-03T19:01:00Z')""",
            (session_id,),
        )
        codes = {finding.code for finding in run_health_gate(self.connection)}
        self.assertIn("IMPORT_DISCOVERY_MANIFEST_MISSING", codes)

    def test_health_gate_detects_source_context_and_duplicate_lineage_corruption(self) -> None:
        worksheet_id, committed_id = self.connection.execute(
            """SELECT worksheet_id, occurrence_id FROM source_occurrence
               WHERE terminal_status = 'Committed' LIMIT 1"""
        ).fetchone()
        rule_id = self.connection.execute(
            "SELECT detection_rule_version_id FROM source_occurrence WHERE occurrence_id = ?",
            (committed_id,),
        ).fetchone()[0]
        self.connection.execute(
            """INSERT INTO source_datum
               (source_datum_id, worksheet_id, cell_or_range,
                submitted_lexeme, context_hash, recorded_at_utc)
               VALUES ('bad-context', ?, 'ZZ998', '7.5', 'wrong',
                       '2026-09-03T19:00:00Z')""",
            (worksheet_id,),
        )
        self.connection.execute(
            """INSERT INTO source_occurrence
               (occurrence_id, worksheet_id, logical_fingerprint,
                detection_rule_version_id, detection_result, terminal_status,
                duplicate_of_occurrence_id, recorded_at_utc)
               VALUES ('bad-duplicate', ?, 'different-logical-hash', ?, 'PBD',
                       'Duplicate', ?, '2026-09-03T19:00:00Z')""",
            (worksheet_id, rule_id, committed_id),
        )
        codes = {finding.code for finding in run_health_gate(self.connection)}
        self.assertIn("SOURCE_DATUM_CONTEXT_HASH_MISMATCH", codes)
        self.assertIn("SOURCE_DUPLICATE_FINGERPRINT_MISMATCH", codes)

    def test_workbook_worksheet_and_fingerprint_evidence_are_immutable(self) -> None:
        workbook_id, worksheet_id = self.connection.execute(
            """SELECT workbook.workbook_id, worksheet.worksheet_id
               FROM source_workbook workbook
               JOIN source_worksheet worksheet
                 ON worksheet.workbook_id = workbook.workbook_id LIMIT 1"""
        ).fetchone()
        fingerprint_id = self.connection.execute(
            """SELECT fingerprint_id FROM fingerprint
               WHERE entity_type = 'Source Workbook' AND entity_id = ?""",
            (workbook_id,),
        ).fetchone()[0]
        mutations = (
            ("UPDATE source_workbook SET submitted_filename = 'changed.xlsx' WHERE workbook_id = ?", workbook_id, "source_workbook is immutable"),
            ("UPDATE source_worksheet SET submitted_name = 'Changed' WHERE worksheet_id = ?", worksheet_id, "source_worksheet is immutable"),
            ("UPDATE fingerprint SET digest = 'changed' WHERE fingerprint_id = ?", fingerprint_id, "fingerprint is immutable"),
        )
        for statement, entity_id, message in mutations:
            with self.subTest(message=message):
                with self.assertRaisesRegex(sqlite3.IntegrityError, message):
                    self.connection.execute(statement, (entity_id,))

    def test_formula_exception_is_disclosed_but_does_not_block_analysis(self) -> None:
        observation_id, worksheet_id = self.connection.execute(
            """SELECT po.observation_id, so.worksheet_id
               FROM pbd_observation po
               JOIN source_occurrence so ON so.occurrence_id = po.occurrence_id
               LIMIT 1"""
        ).fetchone()
        rule_id = self.connection.execute(
            "SELECT rule_version_id FROM rule_version WHERE rule_domain = 'calculation'"
        ).fetchone()[0]
        self.connection.execute(
            """INSERT INTO source_datum
               (source_datum_id, worksheet_id, cell_or_range, formula_text, recorded_at_utc)
               VALUES ('health-formula', ?, 'Z99', '=A1+B1', '2026-09-03T21:00:00Z')""",
            (worksheet_id,),
        )
        persist_formula_integrity(
            self.connection,
            observation_id=observation_id,
            source_datum_id="health-formula",
            submitted_formula="=A1+B1",
            cell_values={"A1": ExactDecimal.parse("5"), "B1": ExactDecimal.parse("4")},
            expected_value=ExactDecimal.parse("10"),
            parser_rule_version_id=rule_id,
            integrity_rule_version_id=rule_id,
            currency_id="USD",
            normalized_unit_id="USD/PART",
            recorded_at_utc="2026-09-03T21:00:00Z",
            audit=AuditContext(
                event_type="Formula Integrity Evaluated",
                actor_user_id=None,
                effective_authority="System",
                occurred_at_utc="2026-09-03T21:00:00Z",
                display_timezone="America/New_York",
                workstation_session="health-test",
                application_version="test",
                action_method="Automated Test",
                reason_code="FORMULA_VALIDATION",
            ),
        )
        readiness = assess_analysis_readiness(self.connection)
        self.assertTrue(readiness.can_proceed)
        self.assertEqual(readiness.blocking_findings, ())
        self.assertIn("FORMULA_RECONCILIATION_EXCEPTION", {finding.code for finding in readiness.advisory_findings})


if __name__ == "__main__":
    unittest.main()
