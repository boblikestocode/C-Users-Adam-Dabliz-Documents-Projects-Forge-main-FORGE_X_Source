from __future__ import annotations

import sqlite3
import unittest
import uuid
from pathlib import Path

from database.migration_runner import apply_migrations, file_checksum, validate_database


ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "database" / "migrations" / "0001_commodity_core.sql"


def new_id() -> str:
    return str(uuid.uuid4())


class CommodityCoreSchemaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db = sqlite3.connect(":memory:")
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.executescript(MIGRATION.read_text(encoding="utf-8"))

        self.engine_id = new_id()
        self.rule_id = new_id()
        self.db.execute(
            """INSERT INTO engine_version
               (engine_version_id, build_id, package_hash, recorded_at_utc)
               VALUES (?, 'test-build', 'hash', '2026-09-02T00:00:00Z')""",
            (self.engine_id,),
        )
        self.db.execute(
            """INSERT INTO rule_version
               (rule_version_id, rule_domain, semantic_version, rule_checksum,
                effective_from_utc, implementation_version, recorded_at_utc)
               VALUES (?, 'extraction', '1.0.0', 'hash',
                       '2026-09-02T00:00:00Z', 'test', '2026-09-02T00:00:00Z')""",
            (self.rule_id,),
        )

    def tearDown(self) -> None:
        self.db.close()

    def _insert_source_chain(self) -> tuple[str, str, str, str]:
        session_id = new_id()
        transaction_id = new_id()
        workbook_id = new_id()
        worksheet_id = new_id()
        occurrence_id = new_id()
        staged_id = new_id()

        self.db.execute(
            """INSERT INTO import_session
               (import_session_id, import_context_type, initiated_by_user_id,
                engine_version_id, status, started_at_utc)
               VALUES (?, 'Historical Baseline', 'user-1', ?, 'Staging', '2026-09-02T00:00:00Z')""",
            (session_id, self.engine_id),
        )
        self.db.execute(
            """INSERT INTO import_transaction
               (import_transaction_id, import_session_id, transaction_sequence,
                status, started_at_utc)
               VALUES (?, ?, 1, 'Started', '2026-09-02T00:00:00Z')""",
            (transaction_id, session_id),
        )
        self.db.execute(
            """INSERT INTO source_workbook
               (workbook_id, import_transaction_id, submitted_filename,
                file_size_bytes, file_hash_sha256, recorded_at_utc)
               VALUES (?, ?, 'supplier.xlsx', 100, ?, '2026-09-02T00:00:00Z')""",
            (workbook_id, transaction_id, "a" * 64),
        )
        self.db.execute(
            """INSERT INTO source_worksheet
               (worksheet_id, workbook_id, worksheet_ordinal, submitted_name,
                visibility, sheet_fingerprint, recorded_at_utc)
               VALUES (?, ?, 0, 'PBD', 'Visible', 'sheet-hash', '2026-09-02T00:00:00Z')""",
            (worksheet_id, workbook_id),
        )
        self.db.execute(
            """INSERT INTO source_occurrence
               (occurrence_id, worksheet_id, logical_fingerprint,
                detection_rule_version_id, detection_result, terminal_status,
                recorded_at_utc)
               VALUES (?, ?, 'logical-hash', ?, 'PBD', 'Committed', '2026-09-02T00:00:00Z')""",
            (occurrence_id, worksheet_id, self.rule_id),
        )
        self.db.execute(
            """INSERT INTO staged_observation
               (staged_observation_id, occurrence_id, provisional_supplier_name,
                provisional_part_number, import_context_type, status,
                recorded_at_utc)
               VALUES (?, ?, 'Supplier A', 'PART-1', 'Historical Baseline',
                       'Committed', '2026-09-02T00:00:00Z')""",
            (staged_id, occurrence_id),
        )
        return transaction_id, worksheet_id, occurrence_id, staged_id

    def _insert_observation(self) -> tuple[str, str]:
        _, worksheet_id, occurrence_id, staged_id = self._insert_source_chain()
        observation_id = new_id()
        source_datum_id = new_id()
        self.db.execute(
            """INSERT INTO source_datum
               (source_datum_id, worksheet_id, cell_or_range, submitted_lexeme,
                recorded_at_utc)
               VALUES (?, ?, 'B12', '12.34567', '2026-09-02T00:00:00Z')""",
            (source_datum_id, worksheet_id),
        )
        self.db.execute(
            """INSERT INTO pbd_observation
               (observation_id, staged_observation_id, occurrence_id,
                observation_context, submitted_supplier_name,
                submitted_part_number, structure_category, recorded_at_utc)
               VALUES (?, ?, ?, 'Historical Baseline', 'Supplier A', 'PART-1',
                       'Valid Aggregate', '2026-09-02T00:00:00Z')""",
            (observation_id, staged_id, occurrence_id),
        )
        self.db.execute(
            """INSERT INTO submitted_datum
               (submitted_datum_id, observation_id, field_code, source_datum_id,
                submitted_lexeme, decimal_coefficient, decimal_scale,
                governing_1e4, normalized_unit_id, currency_id,
                precision_status, recorded_at_utc)
               VALUES (?, ?, 'PIECE_PRICE', ?, '12.34567', '1234567', 5,
                       123457, 'USD/PART', 'USD', 'Eligible',
                       '2026-09-02T00:00:00Z')""",
            (new_id(), observation_id, source_datum_id),
        )
        return observation_id, source_datum_id

    def test_schema_has_expected_foundation(self) -> None:
        table_count = self.db.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        ).fetchone()[0]
        self.assertGreaterEqual(table_count, 45)
        self.assertEqual(
            self.db.execute("SELECT migration_version FROM schema_migration").fetchone()[0],
            "0001",
        )

    def test_migration_runner_is_repeatable_and_checksum_verified(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory(dir=ROOT / "database" / "tests") as temp_dir:
            database_path = Path(temp_dir) / "forge-x.db"
            first = apply_migrations(database_path)
            second = apply_migrations(database_path)
            self.assertEqual(
                [result.version for result in first],
            ["0001", "0002", "0003", "0004", "0005", "0006", "0007", "0008", "0009", "0010", "0011", "0012", "0013", "0014", "0015", "0016", "0017", "0018", "0019", "0020", "0021", "0022", "0023", "0024", "0025", "0026", "0027", "0028", "0029", "0030", "0031", "0032", "0033", "0034", "0035", "0036", "0037", "0038", "0039", "0040", "0041", "0042", "0043", "0044", "0045", "0046", "0047", "0048", "0049"],
            )
            self.assertEqual(first[0].status, "Applied")
            self.assertTrue(all(result.status == "Applied" for result in first))
            self.assertTrue(all(result.status == "Already Applied" for result in second))
            self.assertEqual(first[0].checksum, file_checksum(MIGRATION))
            self.assertEqual(validate_database(database_path), [])

            connection = sqlite3.connect(database_path)
            try:
                required = {
                    "formula_integrity_event",
                    "activity_measure",
                    "knowledge_conflict",
                    "supplier_profile_run",
                    "snapshot_result",
                    "publication_manifest_entry",
                    "restore_event",
                    "event_summary_projection",
                    "active_round_part_projection",
                }
                actual = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                }
                self.assertTrue(required <= actual)

                real_columns = []
                for table in actual:
                    for column in connection.execute(f'PRAGMA table_xinfo("{table}")'):
                        if column[2].upper() == "REAL":
                            real_columns.append(f"{table}.{column[1]}")
                self.assertEqual(real_columns, [], "Governing schema must not use REAL")
            finally:
                connection.close()

    def test_foreign_keys_are_enforced(self) -> None:
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute(
                """INSERT INTO source_worksheet
                   (worksheet_id, workbook_id, worksheet_ordinal, submitted_name,
                    visibility, sheet_fingerprint, recorded_at_utc)
                   VALUES (?, 'missing', 0, 'PBD', 'Visible', 'hash',
                           '2026-09-02T00:00:00Z')""",
                (new_id(),),
            )

    def test_submitted_evidence_preserves_exact_decimal(self) -> None:
        observation_id, _ = self._insert_observation()
        row = self.db.execute(
            """SELECT submitted_lexeme, decimal_coefficient, decimal_scale,
                      governing_1e4
               FROM submitted_datum WHERE observation_id = ?""",
            (observation_id,),
        ).fetchone()
        self.assertEqual(row, ("12.34567", "1234567", 5, 123457))

    def test_source_and_observation_are_immutable(self) -> None:
        observation_id, source_datum_id = self._insert_observation()
        with self.assertRaisesRegex(sqlite3.IntegrityError, "source_datum is immutable"):
            self.db.execute(
                "UPDATE source_datum SET submitted_lexeme = '99' WHERE source_datum_id = ?",
                (source_datum_id,),
            )
        with self.assertRaisesRegex(sqlite3.IntegrityError, "pbd_observation is immutable"):
            self.db.execute(
                "UPDATE pbd_observation SET submitted_part_number = 'CHANGED' WHERE observation_id = ?",
                (observation_id,),
            )
        with self.assertRaisesRegex(sqlite3.IntegrityError, "pbd_observation cannot be deleted"):
            self.db.execute("DELETE FROM pbd_observation WHERE observation_id = ?", (observation_id,))

    def test_import_reconciliation_view_counts_occurrence(self) -> None:
        transaction_id, _, _, _ = self._insert_source_chain()
        row = self.db.execute(
            """SELECT occurrence_count, committed_count, pending_count
               FROM v_source_tab_reconciliation
               WHERE import_transaction_id = ?""",
            (transaction_id,),
        ).fetchone()
        self.assertEqual(row, (1, 1, 0))

    def test_latest_action_version_is_resolved_by_supersession(self) -> None:
        event_id = new_id()
        action_id = new_id()
        first_id = new_id()
        second_id = new_id()
        self.db.execute(
            """INSERT INTO sourcing_event
               (event_id, commodity_id, readable_name, buyer_code_id,
                primary_buyer_user_id, event_status, created_at_utc)
               VALUES (?, 'commodity', 'Event', 'buyer-code', 'user-1',
                       'Active', '2026-09-02T00:00:00Z')""",
            (event_id,),
        )
        self.db.execute(
            """INSERT INTO buyer_action
               (buyer_action_id, event_id, governing_entity_type,
                governing_entity_id, issue_type, created_by_user_id,
                created_at_utc)
               VALUES (?, ?, 'Event', ?, 'Missing Support', 'user-1',
                       '2026-09-02T00:00:00Z')""",
            (action_id, event_id, event_id),
        )
        self.db.execute(
            """INSERT INTO buyer_action_version
               (buyer_action_version_id, buyer_action_id, action_status,
                required_supplier_action, owner_user_id, recorded_by_user_id,
                recorded_at_utc)
               VALUES (?, ?, 'Open', 'Provide support', 'user-1', 'user-1',
                       '2026-09-02T00:00:00Z')""",
            (first_id, action_id),
        )
        self.db.execute(
            """INSERT INTO buyer_action_version
               (buyer_action_version_id, buyer_action_id, action_status,
                required_supplier_action, owner_user_id, resolution_reason,
                supersedes_action_version_id, recorded_by_user_id, recorded_at_utc)
               VALUES (?, ?, 'Resolved', 'Provide support', 'user-1',
                       'Received in later round', ?, 'user-1',
                       '2026-09-03T00:00:00Z')""",
            (second_id, action_id, first_id),
        )
        current = self.db.execute(
            "SELECT buyer_action_version_id, action_status FROM v_current_buyer_action"
        ).fetchall()
        self.assertEqual(current, [(second_id, "Resolved")])

    def test_finalized_snapshot_is_immutable(self) -> None:
        event_id = new_id()
        package_id = new_id()
        scope_id = new_id()
        analysis_id = new_id()
        scenario_id = new_id()
        run_id = new_id()
        snapshot_id = new_id()
        self.db.execute(
            """INSERT INTO sourcing_event
               (event_id, commodity_id, readable_name, buyer_code_id,
                primary_buyer_user_id, event_status, created_at_utc)
               VALUES (?, 'commodity', 'Event', 'buyer-code', 'user-1',
                       'Active', '2026-09-02T00:00:00Z')""",
            (event_id,),
        )
        self.db.execute(
            """INSERT INTO source_package
               (source_package_id, event_id, normalized_package_number,
                displayed_package_number, package_role, recorded_at_utc)
               VALUES (?, ?, ?, ?, 'Primary', '2026-09-02T00:00:00Z')""",
            (package_id, event_id, f"SP-{package_id}", f"SP-{package_id}"),
        )
        self.db.execute(
            """INSERT INTO scope_version
               (scope_version_id, source_package_id, version_number,
                change_reason, confirmed_by_user_id, confirmed_at_utc)
               VALUES (?, ?, 1, 'Initial', 'user-1', '2026-09-02T00:00:00Z')""",
            (scope_id, package_id),
        )
        self.db.execute(
            """INSERT INTO analysis
               (analysis_id, event_id, analysis_type, readable_name,
                created_by_user_id, created_at_utc)
               VALUES (?, ?, 'Sourcing', 'Analysis', 'user-1', '2026-09-02T00:00:00Z')""",
            (analysis_id, event_id),
        )
        self.db.execute(
            """INSERT INTO scenario_revision
               (scenario_revision_id, analysis_id, revision_number,
                scope_version_id, assumptions_hash, created_by_user_id,
                created_at_utc)
               VALUES (?, ?, 1, ?, 'assumptions', 'user-1', '2026-09-02T00:00:00Z')""",
            (scenario_id, analysis_id, scope_id),
        )
        self.db.execute(
            """INSERT INTO calculation_run
               (calculation_run_id, scenario_revision_id, engine_version_id,
                calculation_rule_version_id, status, input_manifest_hash,
                output_manifest_hash, started_at_utc, completed_at_utc)
               VALUES (?, ?, ?, ?, 'Completed', 'input', 'output',
                       '2026-09-02T00:00:00Z', '2026-09-02T00:01:00Z')""",
            (run_id, scenario_id, self.engine_id, self.rule_id),
        )
        self.db.execute(
            """INSERT INTO finalized_snapshot
               (finalized_snapshot_id, analysis_id, scenario_revision_id,
                calculation_run_id, open_action_count, evidence_manifest_hash,
                audit_chain_anchor, finalized_by_user_id, finalized_at_utc)
               VALUES (?, ?, ?, ?, 0, 'manifest', 'anchor', 'user-1',
                       '2026-09-02T00:02:00Z')""",
            (snapshot_id, analysis_id, scenario_id, run_id),
        )
        with self.assertRaisesRegex(sqlite3.IntegrityError, "finalized_snapshot is immutable"):
            self.db.execute(
                "UPDATE finalized_snapshot SET open_action_count = 1 WHERE finalized_snapshot_id = ?",
                (snapshot_id,),
            )


if __name__ == "__main__":
    unittest.main()
