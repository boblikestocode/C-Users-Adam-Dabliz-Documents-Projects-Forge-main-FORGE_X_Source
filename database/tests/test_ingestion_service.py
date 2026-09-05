from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from database.migration_runner import apply_migrations
from database.services.audit import AuditContext, verify_audit_chain
from database.services.connection import connect
from database.services.decimals import ExactDecimal
from database.services.ids import uuid7
from database.services.historical_baseline import (
    compare_to_historical_baseline,
    commit_historical_baseline_observation,
    record_historical_baseline_completion,
)
from database.services.integrity import run_health_gate, verify_import_state_history
from database.services.supplier_identities import confirm_supplier_identity_batch
from database.services.ingestion import (
    DiscoveredWorkbook,
    FieldEvidence,
    ObservationCommit,
    WorkbookInput,
    WorksheetInput,
    begin_import,
    cancel_import_session,
    commit_observation,
    declare_import_inventory,
    finalize_import_transaction,
    register_workbook,
    record_discovery_disposition,
    record_occurrence_disposition,
    record_staging_resolution,
    record_source_workbook_availability,
    retry_import_transaction,
    stage_observation,
    terminate_import_transaction,
)


ROOT = Path(__file__).resolve().parents[2]
WHEN = "2026-09-02T15:00:00Z"


def audit(event_type: str) -> AuditContext:
    return AuditContext(
        event_type=event_type,
        actor_user_id="buyer-1",
        effective_authority="Buyer",
        occurred_at_utc=WHEN,
        display_timezone="America/New_York",
        workstation_session="ingestion-test",
        application_version="test",
        action_method="Automated Test",
        reason_code="TEST",
    )


class IngestionServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / "database" / "tests")
        self.database_path = Path(self.temp.name) / "ingestion.db"
        apply_migrations(self.database_path)
        self.connection = connect(self.database_path)
        self.engine_id = uuid7()
        self.rule_id = uuid7()
        self.connection.execute(
            """INSERT INTO engine_version
               (engine_version_id, build_id, package_hash, recorded_at_utc)
               VALUES (?, 'ingestion-test', 'engine-hash', ?)""",
            (self.engine_id, WHEN),
        )
        self.connection.execute(
            """INSERT INTO rule_version
               (rule_version_id, rule_domain, semantic_version, rule_checksum,
                effective_from_utc, implementation_version, recorded_at_utc)
               VALUES (?, 'extraction', '1.0.0', 'rule-hash', ?, 'test', ?)""",
            (self.rule_id, WHEN, WHEN),
        )

    def tearDown(self) -> None:
        self.connection.close()
        self.temp.cleanup()

    def _begin_and_register(self, logical_fingerprint: str = "logical-1",
                            context_type: str = "Historical Baseline",
                            context_id: str | None = None) -> tuple[str, str]:
        _, transaction_id = begin_import(
            self.connection,
            context_type=context_type,
            context_id=context_id,
            initiated_by_user_id="buyer-1",
            engine_version_id=self.engine_id,
            started_at_utc=WHEN,
            audit=audit("Import Started"),
        )
        discovery_id = declare_import_inventory(
            self.connection,
            import_transaction_id=transaction_id,
            workbooks=(DiscoveredWorkbook("supplier.xlsx", "test://supplier.xlsx", "a" * 64),),
            discovered_at_utc=WHEN,
            audit=audit("Import Inventory Declared"),
        )[0]
        _, occurrences = register_workbook(
            self.connection,
            import_transaction_id=transaction_id,
            import_discovery_item_id=discovery_id,
            workbook=WorkbookInput(
                filename="supplier.xlsx",
                source_locator="test://supplier.xlsx",
                file_size_bytes=1024,
                file_hash_sha256="a" * 64,
                modified_at_utc=None,
                displayed_document_date=None,
                worksheets=(
                    WorksheetInput(
                        ordinal=0,
                        name="PBD",
                        visibility="Visible",
                        used_range="A1:Z100",
                        sheet_fingerprint="sheet-1",
                        region_locator="A1:Z100",
                        logical_fingerprint=logical_fingerprint,
                        detection_result="PBD",
                    ),
                ),
            ),
            detection_rule_version_id=self.rule_id,
            recorded_at_utc=WHEN,
            audit=audit("Workbook Registered"),
        )
        return transaction_id, occurrences[0]

    def test_pending_discovered_workbook_blocks_finalization(self) -> None:
        _, transaction_id = begin_import(
            self.connection, context_type="Historical Baseline", context_id=None,
            initiated_by_user_id="buyer-1", engine_version_id=self.engine_id,
            started_at_utc=WHEN, audit=audit("Import Started"),
        )
        items = declare_import_inventory(
            self.connection, import_transaction_id=transaction_id,
            workbooks=(
                DiscoveredWorkbook("first.xlsx", "test://first.xlsx"),
                DiscoveredWorkbook("second.xlsx", "test://second.xlsx"),
            ),
            discovered_at_utc=WHEN, audit=audit("Import Inventory Declared"),
        )
        record_discovery_disposition(
            self.connection, import_discovery_item_id=items[0],
            disposition_status="Ignored", reason="Buyer confirmed non-PBD workbook",
            recorded_at_utc=WHEN, audit=audit("Workbook Ignored"),
        )
        with self.assertRaisesRegex(ValueError, "discovered workbooks are pending"):
            finalize_import_transaction(
                self.connection, import_transaction_id=transaction_id,
                completed_at_utc=WHEN, audit=audit("Import Completed"),
            )

    def test_unavailable_external_workbook_retains_ingested_evidence_state(self) -> None:
        _, occurrence_id = self._begin_and_register("unavailable-source")
        workbook_id = self.connection.execute(
            """SELECT workbook.workbook_id FROM source_occurrence occurrence
               JOIN source_worksheet worksheet ON worksheet.worksheet_id = occurrence.worksheet_id
               JOIN source_workbook workbook ON workbook.workbook_id = worksheet.workbook_id
               WHERE occurrence.occurrence_id = ?""", (occurrence_id,),
        ).fetchone()[0]
        initial = self.connection.execute(
            """SELECT availability_status FROM v_current_source_workbook_availability
               WHERE workbook_id = ?""", (workbook_id,),
        ).fetchone()[0]
        self.assertEqual(initial, "Unknown")
        record_source_workbook_availability(
            self.connection, workbook_id=workbook_id,
            availability_status="Unavailable", verification_method="Filesystem Check",
            checked_by_user_id="buyer-1",
            status_detail="Original supplier path is no longer accessible",
            checked_at_utc=WHEN, audit=audit("Source Availability Checked"),
        )
        current = self.connection.execute(
            """SELECT availability_status, source_locator
               FROM v_source_workbook_reverification WHERE workbook_id = ?""",
            (workbook_id,),
        ).fetchone()
        self.assertEqual(tuple(current), ("Unavailable", "test://supplier.xlsx"))
        self.assertEqual(self.connection.execute(
            "SELECT COUNT(*) FROM source_occurrence WHERE occurrence_id = ?",
            (occurrence_id,),
        ).fetchone()[0], 1)
        self.assertEqual(verify_audit_chain(self.connection), [])

    def test_failed_attempt_can_retry_without_erasing_prior_evidence(self) -> None:
        session_id, first_id = begin_import(
            self.connection, context_type="Historical Baseline", context_id=None,
            initiated_by_user_id="buyer-1", engine_version_id=self.engine_id,
            started_at_utc=WHEN, audit=audit("Import Started"),
        )
        first_item = declare_import_inventory(
            self.connection, import_transaction_id=first_id,
            workbooks=(DiscoveredWorkbook("broken.xlsx", "test://broken.xlsx"),),
            discovered_at_utc=WHEN, audit=audit("Inventory Declared"),
        )[0]
        terminate_import_transaction(
            self.connection, import_transaction_id=first_id, outcome="Failed",
            reason="Workbook parser terminated unexpectedly",
            completed_at_utc=WHEN, audit=audit("Import Attempt Failed"),
        )
        retry_id = retry_import_transaction(
            self.connection, import_session_id=session_id, started_at_utc=WHEN,
            audit=audit("Import Retried"),
        )
        retry_items = declare_import_inventory(
            self.connection, import_transaction_id=retry_id,
            workbooks=(DiscoveredWorkbook("replacement.xlsx", "test://replacement.xlsx"),),
            discovered_at_utc=WHEN, audit=audit("Retry Inventory Declared"),
        )
        self.assertEqual(len(retry_items), 1)
        self.assertEqual(self.connection.execute(
            "SELECT COUNT(*) FROM import_discovery_item WHERE import_discovery_item_id = ?",
            (first_item,),
        ).fetchone()[0], 1)
        self.assertEqual(self.connection.execute(
            "SELECT transaction_sequence FROM import_transaction WHERE import_transaction_id = ?",
            (retry_id,),
        ).fetchone()[0], 2)
        self.assertEqual(verify_audit_chain(self.connection), [])
        self.assertEqual(verify_import_state_history(self.connection), [])

    def test_session_cancellation_is_atomic_and_terminal(self) -> None:
        session_id, transaction_id = begin_import(
            self.connection, context_type="Historical Baseline", context_id=None,
            initiated_by_user_id="buyer-1", engine_version_id=self.engine_id,
            started_at_utc=WHEN, audit=audit("Import Started"),
        )
        cancel_import_session(
            self.connection, import_session_id=session_id,
            reason="Buyer cancelled the selected import batch",
            completed_at_utc=WHEN, audit=audit("Import Cancelled"),
        )
        self.assertEqual(self.connection.execute(
            "SELECT status FROM import_session WHERE import_session_id = ?",
            (session_id,),
        ).fetchone()[0], "Cancelled")
        self.assertEqual(self.connection.execute(
            "SELECT status FROM import_transaction WHERE import_transaction_id = ?",
            (transaction_id,),
        ).fetchone()[0], "Cancelled")
        self.assertEqual([row[0] for row in self.connection.execute(
            """SELECT session_status FROM import_session_status_event
               WHERE import_session_id = ? ORDER BY rowid""", (session_id,),
        )][-2:], ["Cancelling", "Cancelled"])
        with self.assertRaisesRegex(ValueError, "Terminal import sessions"):
            retry_import_transaction(
                self.connection, import_session_id=session_id, started_at_utc=WHEN,
                audit=audit("Invalid Retry"),
            )
        self.assertEqual(verify_audit_chain(self.connection), [])
        self.assertEqual(verify_import_state_history(self.connection), [])

    def test_explicitly_disposed_inventory_completes_without_silent_omission(self) -> None:
        _, transaction_id = begin_import(
            self.connection, context_type="Historical Baseline", context_id=None,
            initiated_by_user_id="buyer-1", engine_version_id=self.engine_id,
            started_at_utc=WHEN, audit=audit("Import Started"),
        )
        item_id = declare_import_inventory(
            self.connection, import_transaction_id=transaction_id,
            workbooks=(DiscoveredWorkbook("notes.xlsx", "test://notes.xlsx"),),
            discovered_at_utc=WHEN, audit=audit("Import Inventory Declared"),
        )[0]
        record_discovery_disposition(
            self.connection, import_discovery_item_id=item_id,
            disposition_status="Ignored", reason="Buyer confirmed no PBD tabs",
            recorded_at_utc=WHEN, audit=audit("Workbook Ignored"),
        )
        counts = finalize_import_transaction(
            self.connection, import_transaction_id=transaction_id,
            completed_at_utc=WHEN, audit=audit("Import Completed"),
        )
        self.assertEqual(counts["occurrence_count"], 0)
        state = self.connection.execute(
            "SELECT ignored_workbook_count, pending_workbook_count FROM v_import_discovery_reconciliation"
        ).fetchone()
        self.assertEqual(tuple(state), (1, 0))

    def test_multi_tab_workbook_requires_every_tab_to_reconcile(self) -> None:
        _, transaction_id = begin_import(
            self.connection, context_type="Historical Baseline", context_id=None,
            initiated_by_user_id="buyer-1", engine_version_id=self.engine_id,
            started_at_utc=WHEN, audit=audit("Import Started"),
        )
        discovery_id = declare_import_inventory(
            self.connection, import_transaction_id=transaction_id,
            workbooks=(DiscoveredWorkbook("multi.xlsx", "test://multi.xlsx"),),
            discovered_at_utc=WHEN, audit=audit("Import Inventory Declared"),
        )[0]
        _, occurrences = register_workbook(
            self.connection, import_transaction_id=transaction_id,
            import_discovery_item_id=discovery_id,
            workbook=WorkbookInput(
                "multi.xlsx", "test://multi.xlsx", 2048, "b" * 64, None, None,
                tuple(
                    WorksheetInput(index, f"Tab {index + 1}", "Visible", "A1:Z100",
                                   f"sheet-{index}", "A1:Z100", f"multi-{index}", "PBD")
                    for index in range(3)
                ),
            ),
            detection_rule_version_id=self.rule_id, recorded_at_utc=WHEN,
            audit=audit("Workbook Registered"),
        )
        for occurrence_id in occurrences:
            record_occurrence_disposition(
                self.connection, occurrence_id=occurrence_id,
                terminal_status="Ignored", reason="Regression inventory reconciliation",
                recorded_at_utc=WHEN, audit=audit("Worksheet Dispositioned"),
            )
        counts = finalize_import_transaction(
            self.connection, import_transaction_id=transaction_id,
            completed_at_utc=WHEN, audit=audit("Import Completed"),
        )
        self.assertEqual(counts["occurrence_count"], 3)
        self.assertEqual(counts["ignored_count"], 3)

    def test_complete_ingestion_preserves_lineage_and_reconciles(self) -> None:
        transaction_id, occurrence_id = self._begin_and_register()
        staged_id = stage_observation(
            self.connection,
            occurrence_id=occurrence_id,
            provisional_supplier_code="SUP-001",
            provisional_supplier_name="Supplier One",
            provisional_part_number="PART-001",
            import_context_type="Historical Baseline",
            import_context_id=None,
            blocking_issues=(),
            recorded_at_utc=WHEN,
            audit=audit("Observation Staged"),
        )
        observation_id = commit_observation(
            self.connection,
            staged_observation_id=staged_id,
            observation=ObservationCommit(
                observation_context="Historical Baseline",
                context_id=None,
                supplier_id="supplier-1",
                supplier_plant_id="plant-1",
                part_id="part-1",
                submitted_supplier_name="Supplier One",
                submitted_part_number="PART-001",
                submitted_part_description="Test Part",
                economic_date="2026-09-01",
                economic_date_precision="Day",
                structure_category="Valid Aggregate",
                fields=(
                    FieldEvidence(
                        field_code="PIECE_PRICE",
                        cell_or_range="B12",
                        submitted_lexeme="12.34565",
                        exact_decimal=ExactDecimal.parse("12.34565"),
                        normalized_unit_id="USD/PART",
                        currency_id="USD",
                        precision_status="Eligible",
                    ),
                ),
            ),
            recorded_at_utc=WHEN,
            audit=audit("Observation Committed"),
        )
        counts = finalize_import_transaction(
            self.connection,
            import_transaction_id=transaction_id,
            completed_at_utc=WHEN,
            audit=audit("Import Completed"),
        )
        self.assertEqual(counts["committed_count"], 1)
        session_id = self.connection.execute(
            "SELECT import_session_id FROM import_transaction WHERE import_transaction_id = ?",
            (transaction_id,),
        ).fetchone()[0]
        phases = {row[0] for row in self.connection.execute(
            """SELECT session_status FROM import_session_status_event
               WHERE import_session_id = ?""", (session_id,),
        )}
        self.assertTrue({"Discovering", "Extracting", "Awaiting Confirmation",
                         "Staging", "Committing", "Reconciling", "Completed"} <= phases)
        self.assertEqual(self.connection.execute(
            """SELECT transaction_status FROM v_current_import_transaction_status
               WHERE import_transaction_id = ?""", (transaction_id,),
        ).fetchone()[0], "Committed")
        self.assertEqual(self.connection.execute(
            """SELECT occurrence_status FROM v_current_source_occurrence_status
               WHERE occurrence_id = ?""", (occurrence_id,),
        ).fetchone()[0], "Committed")
        self.assertEqual(self.connection.execute(
            """SELECT staged_status FROM v_current_staged_observation_status
               WHERE staged_observation_id = ?""", (staged_id,),
        ).fetchone()[0], "Committed")
        row = self.connection.execute(
            """SELECT sd.submitted_lexeme, sd.decimal_coefficient,
                      sd.decimal_scale, sd.governing_1e4,
                      src.cell_or_range, ws.submitted_name, wb.submitted_filename
               FROM submitted_datum sd
               JOIN source_datum src ON src.source_datum_id = sd.source_datum_id
               JOIN source_worksheet ws ON ws.worksheet_id = src.worksheet_id
               JOIN source_workbook wb ON wb.workbook_id = ws.workbook_id
               WHERE sd.observation_id = ?""",
            (observation_id,),
        ).fetchone()
        self.assertEqual(tuple(row), ("12.34565", "1234565", 5, 123457, "B12", "PBD", "supplier.xlsx"))
        self.assertEqual(verify_audit_chain(self.connection), [])

    def test_missing_supplier_code_creates_action_then_batch_confirmation_resolves_it(self) -> None:
        event_id = "supplier-confirmation-event"
        self.connection.execute(
            """INSERT INTO sourcing_event VALUES
               (?, 'commodity', 'Supplier Confirmation', 'BUYER-1', 'buyer-1', 'Setup', ?)""",
            (event_id, WHEN),
        )
        _, occurrence_id = self._begin_and_register(
            "missing-supplier-code", "Sourcing Event", event_id
        )
        staged_id = stage_observation(
            self.connection, occurrence_id=occurrence_id,
            provisional_supplier_code=None, provisional_supplier_name="Supplier Unknown",
            provisional_part_number="PART-001", import_context_type="Sourcing Event",
            import_context_id=event_id, blocking_issues=(), recorded_at_utc=WHEN,
            audit=audit("Observation Staged"),
        )
        observation_id = commit_observation(
            self.connection, staged_observation_id=staged_id,
            observation=ObservationCommit(
                "Sourcing Event", event_id, None, None, "part-1",
                "Supplier Unknown", "PART-001", "Test Part", None, "Unknown",
                "Valid Aggregate",
                (FieldEvidence("PIECE_PRICE", "B12", "12.00",
                               exact_decimal=ExactDecimal.parse("12.00"),
                               normalized_unit_id="USD/PART", currency_id="USD",
                               precision_status="Eligible"),),
            ), recorded_at_utc=WHEN, audit=audit("Observation Committed"),
        )
        action = self.connection.execute(
            """SELECT action.buyer_action_id, current.action_status
               FROM buyer_action action JOIN v_current_buyer_action current
                 ON current.buyer_action_id = action.buyer_action_id
               WHERE action.governing_entity_id = ?
                 AND action.issue_type = 'Supplier Code Confirmation Required'""",
            (observation_id,),
        ).fetchone()
        self.assertEqual(action[1], "Open")
        confirm_supplier_identity_batch(
            self.connection, observation_ids=(observation_id,),
            confirmed_supplier_id="supplier-900", confirmed_supplier_code="SUP-900",
            confirmation_reason="Buyer confirmed supplier code",
            confirmed_by_user_id="buyer-1", recorded_at_utc=WHEN,
            audit=audit("Supplier Identity Confirmed"),
        )
        self.assertEqual(self.connection.execute(
            "SELECT action_status FROM v_current_buyer_action WHERE buyer_action_id = ?",
            (action[0],),
        ).fetchone()[0], "Resolved")
        self.assertEqual(self.connection.execute(
            "SELECT supplier_id FROM v_effective_pbd_observation WHERE observation_id = ?",
            (observation_id,),
        ).fetchone()[0], "supplier-900")

    def test_historical_baseline_uses_analysis_date_and_records_completion_summary(self) -> None:
        transaction_id, occurrence_id = self._begin_and_register("baseline-summary")
        staged_id = stage_observation(
            self.connection, occurrence_id=occurrence_id,
            provisional_supplier_code="SUP-001", provisional_supplier_name="Supplier One",
            provisional_part_number="PART-001", import_context_type="Historical Baseline",
            import_context_id=None, blocking_issues=(), recorded_at_utc=WHEN,
            audit=audit("Observation Staged"),
        )
        observation_id = commit_historical_baseline_observation(
            self.connection, staged_observation_id=staged_id,
            observation=ObservationCommit(
                "Historical Baseline", None, "supplier-1", None, "part-1",
                "Supplier One", "PART-001", "Test Part", None, "Unknown",
                "Incomplete",
                (FieldEvidence("PIECE_PRICE", "B12", "12.00",
                               exact_decimal=ExactDecimal.parse("12.00"),
                               normalized_unit_id="USD/PART", currency_id="USD",
                               precision_status="Eligible"),),
            ), first_analyzed_date="2026-09-02", recorded_at_utc=WHEN,
            audit=audit("Baseline Observation Committed"),
        )
        finalize_import_transaction(
            self.connection, import_transaction_id=transaction_id,
            completed_at_utc=WHEN, audit=audit("Import Completed"),
        )
        summary = record_historical_baseline_completion(
            self.connection, import_transaction_id=transaction_id,
            first_analyzed_date="2026-09-02", generated_at_utc=WHEN,
            audit=audit("Historical Baseline Summarized"),
        )
        self.assertEqual(summary.imported_record_count, 1)
        self.assertEqual(summary.incomplete_pbd_count, 1)
        self.assertEqual(summary.duplicate_count, 0)
        self.assertEqual(self.connection.execute(
            "SELECT economic_date FROM pbd_observation WHERE observation_id = ?",
            (observation_id,),
        ).fetchone()[0], "2026-09-02")
        with self.assertRaisesRegex(Exception, "immutable"):
            self.connection.execute(
                "UPDATE historical_baseline_completion_summary SET incomplete_pbd_count = 0"
            )
        self.assertNotIn(
            "HISTORICAL_BASELINE_SUMMARY_MISMATCH",
            {finding.code for finding in run_health_gate(self.connection)},
        )
        _, later_occurrence_id = self._begin_and_register("later-baseline-submission")
        later_staged_id = stage_observation(
            self.connection, occurrence_id=later_occurrence_id,
            provisional_supplier_code="SUP-001", provisional_supplier_name="Supplier One",
            provisional_part_number="PART-001", import_context_type="Historical Baseline",
            import_context_id=None, blocking_issues=(), recorded_at_utc=WHEN,
            audit=audit("Later Observation Staged"),
        )
        later_id = commit_historical_baseline_observation(
            self.connection, staged_observation_id=later_staged_id,
            observation=ObservationCommit(
                "Historical Baseline", None, "supplier-1", None, "part-1",
                "Supplier One", "PART-001", "Test Part", "2026-09-03", "Day",
                "Valid Aggregate", (
                    FieldEvidence("PIECE_PRICE", "B12", "11.50",
                                  exact_decimal=ExactDecimal.parse("11.50"),
                                  normalized_unit_id="USD/PART", currency_id="USD",
                                  precision_status="Eligible"),
                    FieldEvidence("MATERIAL_TOTAL", "B13", "7.00",
                                  exact_decimal=ExactDecimal.parse("7.00"),
                                  normalized_unit_id="USD/PART", currency_id="USD",
                                  precision_status="Eligible"),
                ),
            ), first_analyzed_date="2026-09-03", recorded_at_utc=WHEN,
            audit=audit("Later Baseline Observation Committed"),
        )
        comparison = compare_to_historical_baseline(
            self.connection, summary_id=summary.summary_id,
            later_observation_id=later_id, comparison_rule_version_id=self.rule_id,
            generated_at_utc=WHEN, audit=audit("Baseline Comparison Generated"),
        )
        self.assertEqual(comparison.structure_trend, "Improved")
        self.assertEqual(comparison.field_coverage_trend, "Improved")
        self.assertEqual(comparison.formula_integrity_trend, "Unchanged")
        with self.assertRaisesRegex(Exception, "immutable"):
            self.connection.execute(
                "UPDATE historical_baseline_observation_comparison SET structure_trend = 'Unchanged'"
            )
        self.assertNotIn(
            "HISTORICAL_BASELINE_COMPARISON_MISMATCH",
            {finding.code for finding in run_health_gate(self.connection)},
        )
        self.connection.execute("DROP TRIGGER historical_baseline_comparison_no_update")
        self.connection.execute(
            "UPDATE historical_baseline_observation_comparison SET structure_trend = 'Unchanged'"
        )
        self.assertIn(
            "HISTORICAL_BASELINE_COMPARISON_MISMATCH",
            {finding.code for finding in run_health_gate(self.connection)},
        )
        self.connection.execute("DROP TRIGGER historical_baseline_summary_no_update")
        self.connection.execute(
            "UPDATE historical_baseline_completion_summary SET incomplete_pbd_count = 0"
        )
        self.assertIn(
            "HISTORICAL_BASELINE_SUMMARY_MISMATCH",
            {finding.code for finding in run_health_gate(self.connection)},
        )

    def test_blocked_observation_cannot_commit(self) -> None:
        _, occurrence_id = self._begin_and_register("blocked-logical")
        staged_id = stage_observation(
            self.connection,
            occurrence_id=occurrence_id,
            provisional_supplier_code=None,
            provisional_supplier_name="Supplier One",
            provisional_part_number="PART-001",
            import_context_type="Historical Baseline",
            import_context_id=None,
            blocking_issues=(("Currency Missing", "Buyer confirmation required"),),
            recorded_at_utc=WHEN,
            audit=audit("Observation Blocked"),
        )
        with self.assertRaisesRegex(ValueError, "Ready to Commit"):
            commit_observation(
                self.connection,
                staged_observation_id=staged_id,
                observation=ObservationCommit(
                    observation_context="Historical Baseline",
                    context_id=None,
                    supplier_id=None,
                    supplier_plant_id=None,
                    part_id=None,
                    submitted_supplier_name="Supplier One",
                    submitted_part_number="PART-001",
                    submitted_part_description=None,
                    economic_date=None,
                    economic_date_precision="Unknown",
                    structure_category="Incomplete",
                    fields=(FieldEvidence("PIECE_PRICE", "B12", "12.00"),),
                ),
                recorded_at_utc=WHEN,
                audit=audit("Observation Committed"),
            )
        self.assertEqual(self.connection.execute("SELECT COUNT(*) FROM pbd_observation").fetchone()[0], 0)

    def test_blocked_observation_recovers_after_governed_resolution(self) -> None:
        _, occurrence_id = self._begin_and_register("resolved-logical")
        staged_id = stage_observation(
            self.connection, occurrence_id=occurrence_id,
            provisional_supplier_code=None,
            provisional_supplier_name="Supplier One",
            provisional_part_number="PART-001",
            import_context_type="Historical Baseline", import_context_id=None,
            blocking_issues=(("Currency Missing", "Buyer confirmation required"),),
            recorded_at_utc=WHEN, audit=audit("Observation Blocked"),
        )
        issue_id = self.connection.execute(
            "SELECT staging_issue_id FROM staging_issue WHERE staged_observation_id = ?",
            (staged_id,),
        ).fetchone()[0]
        resolution_id = record_staging_resolution(
            self.connection, staging_issue_id=issue_id,
            decision_code="CONFIRMED_USD", decided_by_user_id="buyer-1",
            decision_reason="Buyer confirmed the submitted quote currency",
            selected_value="USD", recorded_at_utc=WHEN,
            audit=audit("Staging Issue Resolved"),
        )
        self.assertEqual(tuple(self.connection.execute(
            "SELECT status, blocking_issue_count FROM staged_observation WHERE staged_observation_id = ?",
            (staged_id,),
        ).fetchone()), ("Ready to Commit", 0))
        self.assertEqual(self.connection.execute(
            "SELECT terminal_status FROM source_occurrence WHERE occurrence_id = ?",
            (occurrence_id,),
        ).fetchone()[0], "Pending")
        with self.assertRaisesRegex(Exception, "immutable"):
            self.connection.execute(
                "UPDATE staging_resolution SET decision_code = 'CHANGED' WHERE staging_resolution_id = ?",
                (resolution_id,),
            )
        observation_id = commit_observation(
            self.connection, staged_observation_id=staged_id,
            observation=ObservationCommit(
                "Historical Baseline", None, "supplier-1", None, "part-1",
                "Supplier One", "PART-001", "Test Part", "2026", "Year",
                "Valid Aggregate",
                (FieldEvidence("PIECE_PRICE", "B12", "12.00",
                               exact_decimal=ExactDecimal.parse("12.00"),
                               normalized_unit_id="USD/PART", currency_id="USD",
                               precision_status="Eligible"),),
            ),
            recorded_at_utc=WHEN, audit=audit("Observation Committed"),
        )
        self.assertIsNotNone(observation_id)
        self.assertEqual(verify_audit_chain(self.connection), [])

    def test_ready_observation_failing_commit_gate_remains_recoverable(self) -> None:
        _, occurrence_id = self._begin_and_register("invalid-ready-logical")
        staged_id = stage_observation(
            self.connection, occurrence_id=occurrence_id,
            provisional_supplier_code="SUP-001",
            provisional_supplier_name="Supplier One",
            provisional_part_number="PART-001",
            import_context_type="Historical Baseline", import_context_id=None,
            blocking_issues=(), recorded_at_utc=WHEN,
            audit=audit("Observation Staged"),
        )
        with self.assertRaisesRegex(ValueError, "non-empty part description"):
            commit_observation(
                self.connection, staged_observation_id=staged_id,
                observation=ObservationCommit(
                    "Historical Baseline", None, "supplier-1", None, "part-1",
                    "Supplier One", "PART-001", "", "2026", "Year",
                    "Valid Aggregate",
                    (FieldEvidence(
                        "PIECE_PRICE", "B12", "12.00",
                        exact_decimal=ExactDecimal.parse("12.00"),
                        normalized_unit_id="USD/PART", currency_id="USD",
                        precision_status="Eligible",
                    ),),
                ),
                recorded_at_utc=WHEN, audit=audit("Invalid Commit"),
            )
        self.assertEqual(tuple(self.connection.execute(
            "SELECT status, blocking_issue_count FROM staged_observation WHERE staged_observation_id = ?",
            (staged_id,),
        ).fetchone()), ("Ready to Commit", 0))
        self.assertEqual(self.connection.execute(
            "SELECT terminal_status FROM source_occurrence WHERE occurrence_id = ?",
            (occurrence_id,),
        ).fetchone()[0], "Pending")
        self.assertEqual(self.connection.execute(
            "SELECT COUNT(*) FROM pbd_observation WHERE staged_observation_id = ?",
            (staged_id,),
        ).fetchone()[0], 0)

    def test_duplicate_occurrence_is_retained_and_reconciled(self) -> None:
        first_transaction, first_occurrence = self._begin_and_register("duplicate-logical")
        staged = stage_observation(
            self.connection,
            occurrence_id=first_occurrence,
            provisional_supplier_code="SUP-001",
            provisional_supplier_name="Supplier One",
            provisional_part_number="PART-001",
            import_context_type="Historical Baseline",
            import_context_id=None,
            blocking_issues=(),
            recorded_at_utc=WHEN,
            audit=audit("Observation Staged"),
        )
        commit_observation(
            self.connection,
            staged_observation_id=staged,
            observation=ObservationCommit(
                "Historical Baseline", None, "supplier-1", None, "part-1",
                "Supplier One", "PART-001", "Test Part", "2026", "Year",
                "Valid Aggregate",
                (FieldEvidence("PIECE_PRICE", "B12", "1.00", exact_decimal=ExactDecimal.parse("1.00"), normalized_unit_id="USD/PART", currency_id="USD", precision_status="Eligible"),),
            ),
            recorded_at_utc=WHEN,
            audit=audit("Observation Committed"),
        )
        finalize_import_transaction(
            self.connection,
            import_transaction_id=first_transaction,
            completed_at_utc=WHEN,
            audit=audit("Import Completed"),
        )

        second_transaction, duplicate_occurrence = self._begin_and_register("duplicate-logical")
        status = self.connection.execute(
            "SELECT terminal_status, duplicate_of_occurrence_id FROM source_occurrence WHERE occurrence_id = ?",
            (duplicate_occurrence,),
        ).fetchone()
        self.assertEqual(tuple(status), ("Duplicate", first_occurrence))
        counts = finalize_import_transaction(
            self.connection,
            import_transaction_id=second_transaction,
            completed_at_utc=WHEN,
            audit=audit("Duplicate Import Completed"),
        )
        self.assertEqual(counts["duplicate_count"], 1)
        self.assertEqual(verify_audit_chain(self.connection), [])


if __name__ == "__main__":
    unittest.main()
