from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from io import StringIO
from pathlib import Path
from unittest.mock import patch
from xml.sax.saxutils import escape
from zipfile import ZipFile, ZIP_DEFLATED

from app.cli import main
from database.tests import test_ingestion_service as fixtures
from database.services.audit import verify_audit_chain
from database.services.extraction_staging import (
    load_extracted_worksheet, register_extracted_workbook, stage_workbook_receipts,
    verify_extraction_receipts,
)
from database.services.ingestion import (DiscoveredWorkbook, ObservationCommit, begin_import,
                                        declare_import_inventory, finalize_import_transaction,
                                        record_staging_resolution, commit_observation)
from database.services.integrity import run_health_gate
from database.services.workbook_extraction import (
    ExtractionCancelled, extract_workbook, mapped_field, default_detection_profile,
)

NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG = "http://schemas.openxmlformats.org/package/2006/relationships"
ROOT = Path(__file__).resolve().parents[2]
WHEN = fixtures.WHEN


def detailed_sheet(part="PART-1", extra="", style="1"):
    markers = ["Part Number", "Supplier", "Plant", "Operations", "Materials", "Total Selling Price"]
    rows = []
    for index, label in enumerate(markers, 1):
        additional = f'<c r="B1" t="inlineStr"><is><t>{escape(part)}</t></is></c>' if index == 1 else ""
        if index == 2:
            additional = '<c r="B2" t="s"><v>0</v></c>'
        if index == 6:
            additional = f'<c r="B6" s="{style}"><v>12.345600</v></c><c r="C6"><f>B6*2</f><v>24.691200</v></c>' + extra
        rows.append(f'<row r="{index}"><c r="A{index}" t="inlineStr"><is><t>{label}</t></is></c>{additional}</row>')
    return (f'<worksheet xmlns="{NS}"><dimension ref="A1:F10"/><sheetData>{"".join(rows)}</sheetData>'
            '<mergeCells count="1"><mergeCell ref="A10:C10"/></mergeCells></worksheet>').encode()


def make_workbook(path, sheets=None, *, external=False):
    sheets = sheets or [("Detailed Quote", "visible", detailed_sheet())]
    with ZipFile(path, "w", ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="xml" ContentType="application/xml"/><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/></Types>')
        archive.writestr("_rels/.rels", f'<Relationships xmlns="{PKG}"><Relationship Id="office" Type="{REL}/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        tabs, relationships = [], []
        for index, (name, state, raw) in enumerate(sheets, 1):
            tabs.append(f'<sheet name="{escape(name)}" sheetId="{index}" state="{state}" r:id="sheet{index}"/>')
            mode = ' TargetMode="External"' if external else ""
            relationships.append(f'<Relationship Id="sheet{index}" Type="{REL}/worksheet" Target="worksheets/sheet{index}.xml"{mode}/>')
            archive.writestr(f"xl/worksheets/sheet{index}.xml", raw)
        relationships += [f'<Relationship Id="strings" Type="{REL}/sharedStrings" Target="sharedStrings.xml"/>',
                          f'<Relationship Id="styles" Type="{REL}/styles" Target="styles.xml"/>']
        archive.writestr("xl/workbook.xml", f'<workbook xmlns="{NS}" xmlns:r="{REL}"><sheets>{"".join(tabs)}</sheets></workbook>')
        archive.writestr("xl/_rels/workbook.xml.rels", f'<Relationships xmlns="{PKG}">{"".join(relationships)}</Relationships>')
        archive.writestr("xl/sharedStrings.xml", f'<sst xmlns="{NS}"><si><r><t>Synthetic </t></r><r><t>Supplier</t></r></si></sst>')
        archive.writestr("xl/styles.xml", f'<styleSheet xmlns="{NS}"><numFmts count="1"><numFmt numFmtId="164" formatCode="0.000000"/></numFmts></styleSheet>')
        archive.writestr("xl/worksheets/_rels/sheet1.xml.rels", f'<Relationships xmlns="{PKG}"><Relationship Id="note" Type="{REL}/comments" Target="../comments1.xml"/></Relationships>')
        archive.writestr("xl/comments1.xml", f'<comments xmlns="{NS}"><authors><author>Test</author></authors><commentList><comment ref="B1" authorId="0"><text><t>Retained supplier note</t></text></comment></commentList></comments>')


class WorkbookExtractionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / "database" / "tests")
        self.path = Path(self.temp.name) / "synthetic.xlsx"

    def tearDown(self):
        self.temp.cleanup()

    def test_one_open_scans_53_tabs_and_retains_hidden_quotes(self):
        sheets = [(f"Detail {i}", "veryHidden" if i == 1 else "hidden" if i == 2 else "visible", detailed_sheet(f"PART-{i}"))
                  for i in range(1, 53)]
        sheets.append(("PBD Summary", "visible", detailed_sheet("SUMMARY")))
        make_workbook(self.path, sheets)
        before = self.path.read_bytes()
        original_open = Path.open
        opens = []
        def counted(path, *args, **kwargs):
            if path == self.path:
                opens.append(args)
            return original_open(path, *args, **kwargs)
        progress = []
        with patch.object(Path, "open", counted):
            extracted = extract_workbook(self.path, progress=lambda *event: progress.append(event))
        self.assertEqual(len(opens), 1)
        self.assertEqual(extracted.summary()["scanned_tabs"], 53)
        self.assertEqual(extracted.summary()["pbd_tabs"], 52)
        self.assertEqual(extracted.summary()["ignored_tabs"], 1)
        self.assertEqual(extracted.worksheets[0].metadata.visibility, "Very Hidden")
        self.assertEqual(extracted.worksheets[1].metadata.visibility, "Hidden")
        self.assertEqual(progress[-1][:2], (53, 53))
        self.assertEqual(extracted.workbook.file_hash_sha256, hashlib.sha256(before).hexdigest())
        self.assertEqual(self.path.read_bytes(), before)

    def test_exact_values_formulas_shared_strings_notes_and_styles_are_retained(self):
        extra = '<c r="D6"><f t="shared" si="0"/><v>1.2500</v></c><c r="E6"><f>B6*3</f></c><c r="F6" t="e"><v>#REF!</v></c>'
        make_workbook(self.path, [("Quote", "visible", detailed_sheet(extra=extra))])
        sheet = extract_workbook(self.path).worksheets[0]
        cells = {c["cell"]: c for c in sheet.payload["cells"]}
        self.assertEqual(cells["B2"]["submitted_lexeme"], "Synthetic Supplier")
        self.assertEqual(cells["B6"]["submitted_lexeme"], "12.345600")
        self.assertEqual(cells["B6"]["style_index"], "1")
        self.assertEqual(cells["C6"]["formula_text"], "=B6*2")
        self.assertEqual(cells["C6"]["cached_value_lexeme"], "24.691200")
        self.assertEqual(cells["D6"]["formula_attributes"], {"t": "shared", "si": "0"})
        self.assertEqual(sheet.payload["notes"][0]["text"], "Retained supplier note")
        self.assertEqual(sheet.payload["merged_ranges"], ["A10:C10"])
        for cell in ("D6", "E6", "F6"):
            with self.assertRaises(ValueError):
                mapped_field(sheet, cell_reference=cell, field_code="PIECE_PRICE", value_kind="decimal")
        field = mapped_field(sheet, cell_reference="B6", field_code="PIECE_PRICE", value_kind="decimal", normalized_unit_id="USD/PART", currency_id="USD")
        self.assertEqual((field.exact_decimal.coefficient, field.exact_decimal.scale), ("12345600", 6))

    def test_partial_and_malformed_tabs_do_not_discard_valid_tabs(self):
        partial = f'<worksheet xmlns="{NS}"><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>Part Number</t></is></c></row></sheetData></worksheet>'.encode()
        make_workbook(self.path, [("Good", "visible", detailed_sheet()), ("Partial", "hidden", partial),
                                  ("Broken", "visible", b"<invalid")])
        summary = extract_workbook(self.path).summary()
        self.assertEqual((summary["pbd_tabs"], summary["review_tabs"], summary["failed_tabs"]), (1, 1, 1))

    def test_style_changes_do_not_create_false_logical_versions(self):
        make_workbook(self.path)
        first = extract_workbook(self.path)
        make_workbook(self.path, [("Renamed Quote", "hidden", detailed_sheet(style="2"))])
        second = extract_workbook(self.path)
        self.assertNotEqual(first.workbook.file_hash_sha256, second.workbook.file_hash_sha256)
        self.assertEqual(first.worksheets[0].metadata.logical_fingerprint, second.worksheets[0].metadata.logical_fingerprint)

    def test_external_sheet_relationships_and_cancellation_do_not_execute_content(self):
        make_workbook(self.path, external=True)
        with self.assertRaisesRegex(ValueError, "external relationship"):
            extract_workbook(self.path)
        make_workbook(self.path)
        with self.assertRaises(ExtractionCancelled):
            extract_workbook(self.path, cancelled=lambda: True)

    def test_cli_discovers_without_a_database(self):
        make_workbook(self.path)
        output = StringIO()
        with redirect_stdout(output):
            code = main(["discover-workbook", str(self.path)])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())["pbd_tabs"], 1)
        self.assertEqual(list(self.path.parent.glob("*.db")), [])

    def test_summary_exclusion_cannot_be_removed_by_profile_and_invalid_profile_fails(self):
        make_workbook(self.path, [("PBD Summary", "visible", detailed_sheet())])
        profile = default_detection_profile()
        profile["ignored_sheet_names"] = []
        sheet = extract_workbook(self.path, profile=profile).worksheets[0]
        self.assertEqual(sheet.metadata.detection_result, "Non-PBD")
        with self.assertRaises(ValueError):
            mapped_field(sheet, cell_reference="B6", field_code="PIECE_PRICE", value_kind="decimal")
        with self.assertRaises(ValueError):
            extract_workbook(self.path, profile={})

    def test_missing_workbook_cli_reports_failure_as_json(self):
        output = StringIO()
        with redirect_stdout(output):
            code = main(["discover-workbook", str(self.path)])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(output.getvalue())["extraction_status"], "Failed")


class ExtractionStagingTests(unittest.TestCase):
    setUp = fixtures.IngestionServiceTests.setUp
    tearDown = fixtures.IngestionServiceTests.tearDown

    def _register(self, extracted):
        _, tx = begin_import(self.connection, context_type="Historical Baseline", context_id=None,
                             initiated_by_user_id="buyer-1", engine_version_id=self.engine_id,
                             started_at_utc=WHEN, audit=fixtures.audit("Import Started"))
        item = declare_import_inventory(
            self.connection, import_transaction_id=tx,
            workbooks=(DiscoveredWorkbook(extracted.workbook.filename, extracted.workbook.source_locator,
                                          extracted.workbook.file_hash_sha256),),
            discovered_at_utc=WHEN, audit=fixtures.audit("Discovered"),
        )[0]
        workbook = register_extracted_workbook(
            self.connection, extracted=extracted, import_transaction_id=tx,
            import_discovery_item_id=item, detection_rule_version_id=self.rule_id,
            recorded_at_utc=WHEN, audit=fixtures.audit("Extracted"),
        )
        return tx, item, workbook

    def test_all_tabs_reconcile_and_staging_retries_do_not_duplicate_evidence(self):
        path = Path(self.temp.name) / "staging.xlsx"
        make_workbook(path, [("Quote", "visible", detailed_sheet()), ("PBD Summary", "visible", detailed_sheet()),
                             ("Broken", "hidden", b"<invalid")])
        extracted = extract_workbook(path)
        tx, item, workbook = self._register(extracted)
        repeated = register_extracted_workbook(
            self.connection, extracted=extracted, import_transaction_id=tx,
            import_discovery_item_id=item, detection_rule_version_id=self.rule_id,
            recorded_at_utc=WHEN, audit=fixtures.audit("Retry"),
        )
        self.assertEqual(repeated, workbook)
        counts = stage_workbook_receipts(self.connection, workbook_id=workbook, recorded_at_utc=WHEN, audit=fixtures.audit("Staged"))
        self.assertEqual((counts["staged"], counts["ignored"], counts["failed"]), (1, 1, 1))
        self.assertEqual(stage_workbook_receipts(self.connection, workbook_id=workbook, recorded_at_utc=WHEN,
                                               audit=fixtures.audit("Retry"))["already_processed"], 3)
        summary = finalize_import_transaction(self.connection, import_transaction_id=tx,
                                              completed_at_utc=WHEN, audit=fixtures.audit("Reconciled"))
        self.assertEqual((summary["blocked_count"], summary["ignored_count"], summary["failed_count"]), (1, 1, 1))
        self.assertEqual(verify_extraction_receipts(self.connection), [])
        self.assertEqual(verify_audit_chain(self.connection), [])
        self.assertNotIn("WORKBOOK_EXTRACTION_REPRODUCTION_MISMATCH", {f.code for f in run_health_gate(self.connection)})

    def test_commit_uses_retained_cells_after_source_unavailable_and_identical_reimport_is_duplicate(self):
        path = Path(self.temp.name) / "retained.xlsx"
        make_workbook(path)
        extracted = extract_workbook(path)
        tx, _, workbook = self._register(extracted)
        stage_workbook_receipts(self.connection, workbook_id=workbook, recorded_at_utc=WHEN, audit=fixtures.audit("Staged"))
        staged, occurrence = self.connection.execute("SELECT staged_observation_id, occurrence_id FROM staged_observation").fetchone()
        issue = self.connection.execute("SELECT staging_issue_id FROM staging_issue").fetchone()[0]
        record_staging_resolution(self.connection, staging_issue_id=issue, decision_code="Confirmed Mapping",
                                  decided_by_user_id="buyer-1", decision_reason="Reviewed retained detailed PBD fields and commercial identity",
                                  recorded_at_utc=WHEN, audit=fixtures.audit("Confirmed"))
        self.assertEqual(stage_workbook_receipts(self.connection, workbook_id=workbook, recorded_at_utc=WHEN,
                                               audit=fixtures.audit("Retry"))["already_processed"], 1)
        path.unlink()
        sheet = load_extracted_worksheet(self.connection, occurrence)
        field = mapped_field(sheet, cell_reference="B6", field_code="PIECE_PRICE", value_kind="decimal",
                             normalized_unit_id="USD/PART", currency_id="USD")
        observation = ObservationCommit("Historical Baseline", None, "supplier-1", "plant-1", "part-1",
                                        "Synthetic Supplier", "PART-1", "Synthetic part", "2026-09-01", "Day",
                                        "Incomplete", (field,))
        with self.assertRaisesRegex(ValueError, "differs from retained"):
            commit_observation(self.connection, staged_observation_id=staged,
                               observation=replace(observation, fields=(replace(field, submitted_lexeme="999"),)),
                               recorded_at_utc=WHEN, audit=fixtures.audit("Rejected"))
        commit_observation(self.connection, staged_observation_id=staged, observation=observation,
                           recorded_at_utc=WHEN, audit=fixtures.audit("Committed"))
        finalize_import_transaction(self.connection, import_transaction_id=tx, completed_at_utc=WHEN,
                                    audit=fixtures.audit("Reconciled"))
        _, _, duplicate_workbook = self._register(extracted)
        self.assertEqual(stage_workbook_receipts(self.connection, workbook_id=duplicate_workbook, recorded_at_utc=WHEN,
                                               audit=fixtures.audit("Duplicate"))["duplicate"], 1)
        self.assertEqual(self.connection.execute("SELECT COUNT(*) FROM pbd_observation").fetchone()[0], 1)
        self.assertEqual(verify_extraction_receipts(self.connection), [])

    def test_receipts_are_immutable_and_rebuild_detects_forged_parsed_cells(self):
        path = Path(self.temp.name) / "integrity.xlsx"
        make_workbook(path)
        _, _, workbook = self._register(extract_workbook(path))
        with self.assertRaisesRegex(Exception, "immutable"):
            self.connection.execute("UPDATE worksheet_extraction_receipt SET extraction_payload = '{}' WHERE workbook_id = ?", (workbook,))
        self.connection.execute("DROP TRIGGER no_update_worksheet_extraction")
        raw = self.connection.execute("SELECT extraction_payload FROM worksheet_extraction_receipt").fetchone()[0]
        payload = json.loads(raw)
        payload["cells"][0]["submitted_lexeme"] = "Forged"
        from database.services.audit import canonical_json
        changed = canonical_json(payload)
        self.connection.execute("UPDATE worksheet_extraction_receipt SET extraction_payload = ?, extraction_hash = ?",
                                (changed, hashlib.sha256(changed.encode()).hexdigest()))
        self.assertIn("does not reproduce", verify_extraction_receipts(self.connection)[0][1])

    def test_incomplete_candidate_requires_explicit_pbd_structure_confirmation(self):
        path = Path(self.temp.name) / "aggregate.xlsx"
        make_workbook(path, [("Aggregate Quote", "visible", detailed_sheet().replace(b"Operations", b"Conversion Costs"))])
        extracted = extract_workbook(path)
        self.assertEqual(extracted.summary()["review_tabs"], 1)
        _, _, workbook = self._register(extracted)
        stage_workbook_receipts(self.connection, workbook_id=workbook, recorded_at_utc=WHEN, audit=fixtures.audit("Staged"))
        staged, occurrence = self.connection.execute("SELECT staged_observation_id, occurrence_id FROM staged_observation").fetchone()
        issue = self.connection.execute("SELECT staging_issue_id FROM staging_issue").fetchone()[0]
        initial = record_staging_resolution(
            self.connection, staging_issue_id=issue, decision_code="Confirmed Mapping", decided_by_user_id="buyer-1",
            decision_reason="Field mapping reviewed", recorded_at_utc=WHEN, audit=fixtures.audit("Reviewed"))
        field = mapped_field(load_extracted_worksheet(self.connection, occurrence), cell_reference="B6",
                             field_code="PIECE_PRICE", value_kind="decimal", normalized_unit_id="USD/PART", currency_id="USD")
        observation = ObservationCommit("Historical Baseline", None, "supplier-1", "plant-1", "part-1",
                                        "Synthetic Supplier", "PART-1", "Synthetic part", "2026-09-01", "Day", "Valid Aggregate", (field,))
        with self.assertRaisesRegex(ValueError, "qualifying detailed PBD"):
            commit_observation(self.connection, staged_observation_id=staged, observation=observation,
                               recorded_at_utc=WHEN, audit=fixtures.audit("Rejected"))
        record_staging_resolution(
            self.connection, staging_issue_id=issue, decision_code="Confirmed Detailed PBD", decided_by_user_id="buyer-1",
            decision_reason="Buyer confirms this is the detailed aggregate PBD, not a summary",
            supersedes_staging_resolution_id=initial, recorded_at_utc=WHEN, audit=fixtures.audit("Confirmed"))
        commit_observation(self.connection, staged_observation_id=staged, observation=observation,
                           recorded_at_utc=WHEN, audit=fixtures.audit("Committed"))
        self.assertEqual(self.connection.execute("SELECT COUNT(*) FROM pbd_observation").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
