"""Recoverable workbook extraction receipts and explicit staging boundaries."""
from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
import xml.etree.ElementTree as ET
from dataclasses import asdict

from .audit import AuditContext, append_audit_event, canonical_json
from .connection import immediate_transaction
from .decimals import ExactDecimal
from .ingestion import (ObservationCommit, record_occurrence_disposition,
                        register_workbook, stage_observation)
from .workbook_extraction import (ADAPTER_VERSION, ExtractedWorkbook, ExtractedWorksheet,
                                 extract_worksheet, shared_strings, validate_detection_profile)


def _hash(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _receipt_exists(connection: sqlite3.Connection) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'workbook_extraction_receipt'"
    ).fetchone() is not None


def _registered_sheets(connection: sqlite3.Connection, workbook_id: str):
    return connection.execute(
        """SELECT occurrence.occurrence_id, sheet.worksheet_ordinal AS ordinal,
                  sheet.submitted_name AS name, sheet.visibility, sheet.used_range,
                  sheet.sheet_fingerprint, occurrence.region_locator,
                  occurrence.logical_fingerprint, occurrence.detection_result
           FROM source_worksheet sheet JOIN source_occurrence occurrence USING (worksheet_id)
           WHERE sheet.workbook_id = ? ORDER BY sheet.worksheet_ordinal""", (workbook_id,),
    ).fetchall()


def persist_extraction_receipts(
    connection: sqlite3.Connection, *, workbook_id: str, extracted: ExtractedWorkbook,
    recorded_at_utc: str, audit: AuditContext,
) -> None:
    manifest = canonical_json({"workbook": asdict(extracted.workbook), "profile": extracted.profile,
                               "context": extracted.context, "adapter_version": ADAPTER_VERSION})
    with immediate_transaction(connection):
        workbook = connection.execute(
            """SELECT submitted_filename, source_locator, file_size_bytes, file_hash_sha256
               FROM source_workbook WHERE workbook_id = ?""", (workbook_id,),
        ).fetchone()
        if workbook is None or tuple(workbook) != (extracted.workbook.filename, extracted.workbook.source_locator,
                                                   extracted.workbook.file_size_bytes, extracted.workbook.file_hash_sha256):
            raise ValueError("Extraction receipt must match the registered workbook identity and fingerprint")
        registered = _registered_sheets(connection, workbook_id)
        if [dict(row) | {"occurrence_id": None} for row in registered] != [
            asdict(sheet.metadata) | {"occurrence_id": None} for sheet in extracted.worksheets
        ]:
            raise ValueError("Extraction inventory does not match every registered worksheet")
        prior = connection.execute("SELECT manifest_payload FROM workbook_extraction_receipt WHERE workbook_id = ?",
                                   (workbook_id,)).fetchone()
        if prior is not None:
            if prior[0] != manifest:
                raise ValueError("Existing workbook extraction receipt differs from this attempt")
            return
        connection.execute("INSERT INTO workbook_extraction_receipt VALUES (?, ?, ?, ?, ?)",
                           (workbook_id, ADAPTER_VERSION, manifest, _hash(manifest), recorded_at_utc))
        for registered_sheet, sheet in zip(registered, extracted.worksheets):
            payload = canonical_json(sheet.payload)
            connection.execute("INSERT INTO worksheet_extraction_receipt VALUES (?, ?, ?, ?, ?)",
                               (registered_sheet["occurrence_id"], workbook_id, payload, _hash(payload), recorded_at_utc))
        append_audit_event(connection, audit, {"workbook_id": workbook_id, "adapter_version": ADAPTER_VERSION,
                                               "extraction_manifest_hash": _hash(manifest),
                                               "worksheet_count": len(registered)})


def register_extracted_workbook(
    connection: sqlite3.Connection, *, extracted: ExtractedWorkbook,
    import_transaction_id: str, import_discovery_item_id: str,
    detection_rule_version_id: str, recorded_at_utc: str, audit: AuditContext,
) -> str:
    # Registration and receipt persistence are recoverable, separately audited boundaries.
    prior = connection.execute(
        """SELECT current.workbook_id FROM v_current_import_discovery_disposition current
           JOIN import_discovery_item item USING (import_discovery_item_id)
           WHERE current.import_discovery_item_id = ? AND item.import_transaction_id = ?
             AND current.disposition_status = 'Registered'""",
        (import_discovery_item_id, import_transaction_id),
    ).fetchone()
    if prior is None:
        workbook_id, _ = register_workbook(
            connection, import_transaction_id=import_transaction_id,
            import_discovery_item_id=import_discovery_item_id, workbook=extracted.workbook,
            detection_rule_version_id=detection_rule_version_id, recorded_at_utc=recorded_at_utc, audit=audit,
        )
    else:
        workbook_id = str(prior[0])
    persist_extraction_receipts(connection, workbook_id=workbook_id, extracted=extracted,
                                recorded_at_utc=recorded_at_utc, audit=audit)
    return workbook_id


def stage_workbook_receipts(
    connection: sqlite3.Connection, *, workbook_id: str, recorded_at_utc: str,
    audit: AuditContext,
) -> dict[str, int]:
    if connection.execute("SELECT 1 FROM workbook_extraction_receipt WHERE workbook_id = ?", (workbook_id,)).fetchone() is None:
        raise ValueError("Staging requires a complete retained workbook extraction")
    context = connection.execute(
        """SELECT session.import_context_type, session.context_id
           FROM source_workbook workbook JOIN import_transaction tx USING (import_transaction_id)
           JOIN import_session session USING (import_session_id) WHERE workbook.workbook_id = ?""",
        (workbook_id,),
    ).fetchone()
    counts = {"staged": 0, "ignored": 0, "failed": 0, "duplicate": 0, "already_processed": 0}
    for row in _registered_sheets(connection, workbook_id):
        state = connection.execute("SELECT terminal_status FROM source_occurrence WHERE occurrence_id = ?",
                                   (row["occurrence_id"],)).fetchone()[0]
        if state == "Duplicate":
            counts["duplicate"] += 1
            continue
        if state != "Pending" or connection.execute(
            "SELECT 1 FROM staged_observation WHERE occurrence_id = ?", (row["occurrence_id"],)
        ).fetchone():
            counts["already_processed"] += 1
            continue
        if row["detection_result"] in {"Non-PBD", "Failed"}:
            terminal = "Ignored" if row["detection_result"] == "Non-PBD" else "Failed"
            record_occurrence_disposition(connection, occurrence_id=row["occurrence_id"],
                                          terminal_status=terminal, reason="Retained deterministic workbook extraction disposition",
                                          recorded_at_utc=recorded_at_utc, audit=audit)
            counts[terminal.lower()] += 1
        else:
            stage_observation(
                connection, occurrence_id=row["occurrence_id"], provisional_supplier_code=None,
                provisional_supplier_name=None, provisional_part_number=None,
                import_context_type=context[0], import_context_id=context[1],
                blocking_issues=(("Extraction Mapping Review",
                                  "Confirm detailed PBD structure, identity, source field mappings, economic date, currency, and units using retained cell evidence."),),
                recorded_at_utc=recorded_at_utc, audit=audit,
            )
            counts["staged"] += 1
    return counts


def load_extracted_worksheet(connection: sqlite3.Connection, occurrence_id: str) -> ExtractedWorksheet:
    row = connection.execute(
        """SELECT receipt.workbook_id, receipt.extraction_payload, receipt.extraction_hash,
                  workbook.manifest_payload, workbook.manifest_hash, workbook.adapter_version
           FROM worksheet_extraction_receipt receipt JOIN workbook_extraction_receipt workbook USING (workbook_id)
           WHERE occurrence_id = ?""", (occurrence_id,),
    ).fetchone()
    if row is None or row[5] != ADAPTER_VERSION or _hash(row[1]) != row[2] or _hash(row[3]) != row[4]:
        raise ValueError("Extraction receipt is missing, unsupported, or fails its manifest hash")
    manifest, payload = json.loads(row[3]), json.loads(row[1])
    registered = next(item for item in _registered_sheets(connection, row[0]) if item["occurrence_id"] == occurrence_id)
    strings_raw = manifest["context"]["shared_strings_xml_base64"]
    rebuilt = extract_worksheet(
        base64.b64decode(payload["raw_xml_base64"], validate=True), ordinal=registered["ordinal"],
        name=registered["name"], visibility=registered["visibility"],
        strings=shared_strings(None if strings_raw is None else base64.b64decode(strings_raw, validate=True)),
        profile=validate_detection_profile(manifest["profile"]), kind=payload["kind"],
        comments_xml=None if payload["comments_xml_base64"] is None else base64.b64decode(payload["comments_xml_base64"], validate=True),
    )
    if canonical_json(rebuilt.payload) != row[1] or asdict(rebuilt.metadata) != {k: v for k, v in dict(registered).items() if k != "occurrence_id"}:
        raise ValueError("Extraction receipt does not reproduce from retained source XML")
    return rebuilt


def validate_commit_extraction(connection: sqlite3.Connection, occurrence_id: str, observation: ObservationCommit) -> None:
    if not _receipt_exists(connection) or connection.execute(
        """SELECT 1 FROM source_occurrence occurrence JOIN source_worksheet sheet USING (worksheet_id)
           JOIN workbook_extraction_receipt receipt USING (workbook_id)
           WHERE occurrence.occurrence_id = ?""", (occurrence_id,),
    ).fetchone() is None:
        return
    sheet = load_extracted_worksheet(connection, occurrence_id)
    structure_confirmed = connection.execute(
        """SELECT 1 FROM staged_observation staged JOIN staging_issue issue USING (staged_observation_id)
           JOIN v_current_staging_resolution resolution USING (staging_issue_id)
           WHERE staged.occurrence_id = ? AND issue.issue_type = 'Extraction Mapping Review'
             AND resolution.decision_code = 'Confirmed Detailed PBD'""", (occurrence_id,),
    ).fetchone() is not None
    if sheet.metadata.detection_result != "PBD" and not (
        sheet.metadata.detection_result == "Review Required" and structure_confirmed
    ):
        raise ValueError("Governing commits require a qualifying detailed PBD extraction")
    cells = {cell["cell"]: cell for cell in sheet.payload["cells"]}
    for field in observation.fields:
        cell = cells.get(field.cell_or_range)
        if cell is None or (field.submitted_lexeme, field.formula_text, field.cached_value_lexeme) != (
            cell["submitted_lexeme"], cell["formula_text"], cell["cached_value_lexeme"]
        ):
            raise ValueError("Committed field evidence differs from retained workbook extraction")
        if cell["cell_type"] == "e" or cell["formula_attributes"].get("t") in {"shared", "array", "dataTable"}:
            raise ValueError("Unsupported source-cell errors or formula expansion cannot govern a committed field")
        if cell["formula_text"] is not None and cell["cached_value_lexeme"] is None:
            raise ValueError("A source formula without a cached result requires recalculation review")
        if field.exact_decimal is not None and (
            cell["submitted_lexeme"] is None or
            field.exact_decimal.as_decimal() != ExactDecimal.parse(cell["submitted_lexeme"]).as_decimal()
        ):
            raise ValueError("Committed numeric value differs from the extracted source value")


def verify_extraction_receipts(connection: sqlite3.Connection) -> list[tuple[str, str]]:
    if not _receipt_exists(connection):
        return []
    findings = []
    for receipt in connection.execute("SELECT * FROM workbook_extraction_receipt"):
        workbook_id = receipt["workbook_id"]
        try:
            payload = receipt["manifest_payload"]
            if _hash(payload) != receipt["manifest_hash"] or receipt["adapter_version"] != ADAPTER_VERSION:
                raise ValueError("Workbook extraction manifest or adapter version is invalid")
            manifest = json.loads(payload)
            workbook = connection.execute("SELECT * FROM source_workbook WHERE workbook_id = ?", (workbook_id,)).fetchone()
            expected = manifest["workbook"]
            for name, column in (("filename", "submitted_filename"), ("source_locator", "source_locator"),
                                 ("file_size_bytes", "file_size_bytes"), ("file_hash_sha256", "file_hash_sha256"),
                                 ("modified_at_utc", "filesystem_modified_at_utc")):
                if expected[name] != workbook[column]:
                    raise ValueError("Workbook extraction identity does not match source registration")
            registered = _registered_sheets(connection, workbook_id)
            if expected["worksheets"] != [{k: v for k, v in dict(row).items() if k != "occurrence_id"} for row in registered]:
                raise ValueError("Workbook extraction inventory does not match registered worksheets")
            if connection.execute("SELECT COUNT(*) FROM worksheet_extraction_receipt WHERE workbook_id = ?",
                                  (workbook_id,)).fetchone()[0] != len(registered):
                raise ValueError("Workbook extraction receipts do not reconcile to its source tabs")
            for sheet in registered:
                load_extracted_worksheet(connection, sheet["occurrence_id"])
        except (ValueError, KeyError, TypeError, StopIteration, ET.ParseError) as error:
            findings.append((workbook_id, str(error)))
    return findings
