from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass

from .audit import AuditContext, append_audit_event
from .audit import canonical_json
from .connection import immediate_transaction
from .decimals import ExactDecimal
from .ids import uuid7


@dataclass(frozen=True)
class WorksheetInput:
    ordinal: int
    name: str
    visibility: str
    used_range: str | None
    sheet_fingerprint: str
    region_locator: str | None
    logical_fingerprint: str | None
    detection_result: str


@dataclass(frozen=True)
class WorkbookInput:
    filename: str
    source_locator: str | None
    file_size_bytes: int
    file_hash_sha256: str
    modified_at_utc: str | None
    displayed_document_date: str | None
    worksheets: tuple[WorksheetInput, ...]


@dataclass(frozen=True)
class DiscoveredWorkbook:
    filename: str
    source_locator: str | None
    expected_file_hash_sha256: str | None = None


@dataclass(frozen=True)
class FieldEvidence:
    field_code: str
    cell_or_range: str
    submitted_lexeme: str | None
    formula_text: str | None = None
    cached_value_lexeme: str | None = None
    exact_decimal: ExactDecimal | None = None
    text_value: str | None = None
    date_value: str | None = None
    boolean_value: bool | None = None
    normalized_unit_id: str | None = None
    currency_id: str | None = None
    precision_status: str = "Not Applicable"


@dataclass(frozen=True)
class ObservationCommit:
    observation_context: str
    context_id: str | None
    supplier_id: str | None
    supplier_plant_id: str | None
    part_id: str | None
    submitted_supplier_name: str
    submitted_part_number: str
    submitted_part_description: str | None
    economic_date: str | None
    economic_date_precision: str
    structure_category: str
    fields: tuple[FieldEvidence, ...]


SESSION_PHASES = (
    "Created", "Discovering", "Extracting", "Awaiting Confirmation",
    "Staging", "Committing", "Reconciling", "Completed",
)


def _advance_import_session(
    connection: sqlite3.Connection, import_transaction_id: str,
    target_status: str, recorded_at_utc: str,
) -> None:
    current = connection.execute(
        """SELECT session.import_session_id, status.import_session_status_event_id,
                  status.session_status
           FROM import_transaction transaction_row
           JOIN import_session session ON session.import_session_id = transaction_row.import_session_id
           JOIN v_current_import_session_status status ON status.import_session_id = session.import_session_id
           WHERE transaction_row.import_transaction_id = ?""",
        (import_transaction_id,),
    ).fetchone()
    if current is None:
        raise ValueError("Import transaction has no session status history")
    start = SESSION_PHASES.index(str(current[2]))
    target = SESSION_PHASES.index(target_status)
    if target < start:
        # A retry starts a new transaction without rewinding the session's
        # append-only macro lifecycle.
        return
    prior_id = str(current[1])
    for phase in SESSION_PHASES[start + 1:target + 1]:
        status_id = uuid7()
        connection.execute(
            """INSERT INTO import_session_status_event VALUES (?, ?, ?, ?, ?, ?)""",
            (status_id, current[0], phase,
             f"Import advanced to {phase} at a governed safe boundary",
             prior_id, recorded_at_utc),
        )
        prior_id = status_id
    connection.execute(
        "UPDATE import_session SET status = ? WHERE import_session_id = ?",
        (target_status, current[0]),
    )


def _append_occurrence_status(
    connection: sqlite3.Connection, occurrence_id: str, status: str,
    detail: str, recorded_at_utc: str,
) -> None:
    prior = connection.execute(
        """SELECT source_occurrence_status_event_id
           FROM v_current_source_occurrence_status WHERE occurrence_id = ?""",
        (occurrence_id,),
    ).fetchone()
    connection.execute(
        "INSERT INTO source_occurrence_status_event VALUES (?, ?, ?, ?, ?, ?)",
        (uuid7(), occurrence_id, status, detail, None if prior is None else prior[0], recorded_at_utc),
    )
    connection.execute("UPDATE source_occurrence SET terminal_status = ? WHERE occurrence_id = ?",
                       (status, occurrence_id))


def begin_import(
    connection: sqlite3.Connection,
    *,
    context_type: str,
    context_id: str | None,
    initiated_by_user_id: str,
    engine_version_id: str,
    started_at_utc: str,
    audit: AuditContext,
) -> tuple[str, str]:
    session_id = uuid7()
    transaction_id = uuid7()
    with immediate_transaction(connection):
        connection.execute(
            """INSERT INTO import_session
               (import_session_id, import_context_type, context_id,
                initiated_by_user_id, engine_version_id, status, started_at_utc)
               VALUES (?, ?, ?, ?, ?, 'Discovering', ?)""",
            (session_id, context_type, context_id, initiated_by_user_id, engine_version_id, started_at_utc),
        )
        connection.execute(
            """INSERT INTO import_transaction
               (import_transaction_id, import_session_id, transaction_sequence,
                status, started_at_utc)
               VALUES (?, ?, 1, 'Started', ?)""",
            (transaction_id, session_id, started_at_utc),
        )
        connection.execute(
            """INSERT INTO import_session_status_event VALUES
               (?, ?, 'Discovering', 'Import session started', NULL, ?)""",
            (uuid7(), session_id, started_at_utc),
        )
        connection.execute(
            """INSERT INTO import_transaction_status_event VALUES
               (?, ?, 'Started', ?, 'Import transaction started', NULL, ?)""",
            (uuid7(), transaction_id, canonical_json({
                "discovered": 0, "committed": 0, "duplicate": 0,
                "blocked": 0, "ignored": 0, "failed": 0,
            }), started_at_utc),
        )
        append_audit_event(
            connection, audit,
            {"import_session_id": session_id, "import_transaction_id": transaction_id, "context_type": context_type},
        )
    return session_id, transaction_id


def terminate_import_transaction(
    connection: sqlite3.Connection,
    *,
    import_transaction_id: str,
    outcome: str,
    reason: str,
    completed_at_utc: str,
    audit: AuditContext,
) -> None:
    """Terminate one attempt without deleting its discovered or extracted evidence."""
    if outcome not in {"Failed", "Rolled Back"}:
        raise ValueError("Import transaction termination must be Failed or Rolled Back")
    if not reason.strip():
        raise ValueError("Import transaction termination requires a reason")
    with immediate_transaction(connection):
        transaction = connection.execute(
            """SELECT status, discovered_count, committed_count, duplicate_count,
                      blocked_count, ignored_count, failed_count
               FROM import_transaction WHERE import_transaction_id = ?""",
            (import_transaction_id,),
        ).fetchone()
        if transaction is None:
            raise ValueError("Import transaction does not exist")
        if transaction[0] != "Started":
            raise ValueError("Only a started import transaction may be terminated")
        counts = {
            "discovered": int(transaction[1]), "committed": int(transaction[2]),
            "duplicate": int(transaction[3]), "blocked": int(transaction[4]),
            "ignored": int(transaction[5]), "failed": int(transaction[6]),
        }
        prior = connection.execute(
            """SELECT import_transaction_status_event_id
               FROM v_current_import_transaction_status
               WHERE import_transaction_id = ?""",
            (import_transaction_id,),
        ).fetchone()
        if prior is None:
            raise ValueError("Import transaction has no status history")
        connection.execute(
            """UPDATE import_transaction SET status = ?, completed_at_utc = ?
               WHERE import_transaction_id = ?""",
            (outcome, completed_at_utc, import_transaction_id),
        )
        connection.execute(
            """INSERT INTO import_transaction_status_event VALUES
               (?, ?, ?, ?, ?, ?, ?)""",
            (uuid7(), import_transaction_id, outcome, canonical_json(counts),
             reason.strip(), prior[0], completed_at_utc),
        )
        append_audit_event(
            connection, audit,
            {"import_transaction_id": import_transaction_id,
             "outcome": outcome, "reason": reason.strip(), "counts": counts},
        )


def retry_import_transaction(
    connection: sqlite3.Connection,
    *,
    import_session_id: str,
    started_at_utc: str,
    audit: AuditContext,
) -> str:
    """Append a new attempt under a nonterminal import session."""
    transaction_id = uuid7()
    with immediate_transaction(connection):
        session = connection.execute(
            "SELECT status FROM import_session WHERE import_session_id = ?",
            (import_session_id,),
        ).fetchone()
        if session is None:
            raise ValueError("Import session does not exist")
        if session[0] in {"Completed", "Cancelling", "Cancelled", "Failed"}:
            raise ValueError("Terminal import sessions cannot be retried")
        latest = connection.execute(
            """SELECT transaction_sequence, status FROM import_transaction
               WHERE import_session_id = ? ORDER BY transaction_sequence DESC LIMIT 1""",
            (import_session_id,),
        ).fetchone()
        if latest is None or latest[1] not in {"Failed", "Rolled Back"}:
            raise ValueError("Retry requires a failed or rolled-back latest transaction")
        sequence = int(latest[0]) + 1
        connection.execute(
            """INSERT INTO import_transaction
               (import_transaction_id, import_session_id, transaction_sequence,
                status, started_at_utc) VALUES (?, ?, ?, 'Started', ?)""",
            (transaction_id, import_session_id, sequence, started_at_utc),
        )
        connection.execute(
            """INSERT INTO import_transaction_status_event VALUES
               (?, ?, 'Started', ?, 'Import retry transaction started', NULL, ?)""",
            (uuid7(), transaction_id, canonical_json({
                "discovered": 0, "committed": 0, "duplicate": 0,
                "blocked": 0, "ignored": 0, "failed": 0,
            }), started_at_utc),
        )
        append_audit_event(
            connection, audit,
            {"import_session_id": import_session_id,
             "import_transaction_id": transaction_id,
             "transaction_sequence": sequence},
        )
    return transaction_id


def cancel_import_session(
    connection: sqlite3.Connection,
    *,
    import_session_id: str,
    reason: str,
    completed_at_utc: str,
    audit: AuditContext,
) -> None:
    """Cancel a session and every still-started attempt at one safe boundary."""
    if not reason.strip():
        raise ValueError("Import cancellation requires a reason")
    with immediate_transaction(connection):
        session = connection.execute(
            "SELECT status FROM import_session WHERE import_session_id = ?",
            (import_session_id,),
        ).fetchone()
        if session is None:
            raise ValueError("Import session does not exist")
        if session[0] in {"Completed", "Cancelled", "Failed"}:
            raise ValueError("Terminal import sessions cannot be cancelled")
        prior_session = connection.execute(
            """SELECT import_session_status_event_id
               FROM v_current_import_session_status WHERE import_session_id = ?""",
            (import_session_id,),
        ).fetchone()
        cancelling_id = uuid7()
        connection.execute(
            "INSERT INTO import_session_status_event VALUES (?, ?, 'Cancelling', ?, ?, ?)",
            (cancelling_id, import_session_id, reason.strip(), prior_session[0], completed_at_utc),
        )
        for transaction_id, in connection.execute(
            """SELECT import_transaction_id FROM import_transaction
               WHERE import_session_id = ? AND status = 'Started'""",
            (import_session_id,),
        ).fetchall():
            prior = connection.execute(
                """SELECT import_transaction_status_event_id
                   FROM v_current_import_transaction_status
                   WHERE import_transaction_id = ?""", (transaction_id,),
            ).fetchone()[0]
            counts = connection.execute(
                """SELECT discovered_count, committed_count, duplicate_count,
                          blocked_count, ignored_count, failed_count
                   FROM import_transaction WHERE import_transaction_id = ?""",
                (transaction_id,),
            ).fetchone()
            payload = dict(zip(
                ("discovered", "committed", "duplicate", "blocked", "ignored", "failed"),
                map(int, counts),
            ))
            connection.execute(
                """UPDATE import_transaction SET status = 'Cancelled', completed_at_utc = ?
                   WHERE import_transaction_id = ?""",
                (completed_at_utc, transaction_id),
            )
            connection.execute(
                """INSERT INTO import_transaction_status_event VALUES
                   (?, ?, 'Cancelled', ?, ?, ?, ?)""",
                (uuid7(), transaction_id, canonical_json(payload), reason.strip(),
                 prior, completed_at_utc),
            )
        connection.execute(
            """UPDATE import_session SET status = 'Cancelled', completed_at_utc = ?
               WHERE import_session_id = ?""",
            (completed_at_utc, import_session_id),
        )
        connection.execute(
            "INSERT INTO import_session_status_event VALUES (?, ?, 'Cancelled', ?, ?, ?)",
            (uuid7(), import_session_id, reason.strip(), cancelling_id, completed_at_utc),
        )
        append_audit_event(
            connection, audit,
            {"import_session_id": import_session_id,
             "outcome": "Cancelled", "reason": reason.strip()},
        )


def declare_import_inventory(
    connection: sqlite3.Connection,
    *,
    import_transaction_id: str,
    workbooks: tuple[DiscoveredWorkbook, ...],
    discovered_at_utc: str,
    audit: AuditContext,
) -> tuple[str, ...]:
    if not workbooks:
        raise ValueError("Import inventory must contain every discovered workbook")
    locators = [item.source_locator for item in workbooks if item.source_locator is not None]
    if len(locators) != len(set(locators)):
        raise ValueError("Discovered workbook locators must be unique")
    for item in workbooks:
        if item.expected_file_hash_sha256 is not None:
            if len(item.expected_file_hash_sha256) != 64:
                raise ValueError("Expected workbook SHA-256 must contain 64 hexadecimal characters")
            try:
                int(item.expected_file_hash_sha256, 16)
            except ValueError as error:
                raise ValueError("Expected workbook SHA-256 is not hexadecimal") from error
    item_ids: list[str] = []
    with immediate_transaction(connection):
        transaction = connection.execute(
            "SELECT status FROM import_transaction WHERE import_transaction_id = ?",
            (import_transaction_id,),
        ).fetchone()
        if transaction is None:
            raise ValueError("Import transaction does not exist")
        if transaction[0] != "Started":
            raise ValueError("Inventory can only be declared for a started import transaction")
        if connection.execute(
            "SELECT 1 FROM import_discovery_item WHERE import_transaction_id = ?",
            (import_transaction_id,),
        ).fetchone() is not None:
            raise ValueError("Import inventory has already been declared")
        _advance_import_session(connection, import_transaction_id, "Extracting", discovered_at_utc)
        for ordinal, item in enumerate(workbooks):
            item_id = uuid7()
            disposition_id = uuid7()
            connection.execute(
                """INSERT INTO import_discovery_item
                   (import_discovery_item_id, import_transaction_id, discovery_ordinal,
                    submitted_filename, source_locator, expected_file_hash_sha256,
                    discovered_at_utc)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    item_id, import_transaction_id, ordinal, item.filename,
                    item.source_locator,
                    None if item.expected_file_hash_sha256 is None
                    else item.expected_file_hash_sha256.lower(),
                    discovered_at_utc,
                ),
            )
            connection.execute(
                """INSERT INTO import_discovery_disposition
                   (import_discovery_disposition_id, import_discovery_item_id,
                    disposition_status, recorded_at_utc)
                   VALUES (?, ?, 'Pending', ?)""",
                (disposition_id, item_id, discovered_at_utc),
            )
            item_ids.append(item_id)
        append_audit_event(
            connection, audit,
            {"import_transaction_id": import_transaction_id,
             "discovered_workbook_count": len(item_ids),
             "import_discovery_item_ids": item_ids},
        )
    return tuple(item_ids)


def register_workbook(
    connection: sqlite3.Connection,
    *,
    import_transaction_id: str,
    import_discovery_item_id: str,
    workbook: WorkbookInput,
    detection_rule_version_id: str,
    recorded_at_utc: str,
    audit: AuditContext,
) -> tuple[str, list[str]]:
    if len(workbook.file_hash_sha256) != 64:
        raise ValueError("Workbook SHA-256 must contain 64 hexadecimal characters")
    try:
        int(workbook.file_hash_sha256, 16)
    except ValueError as error:
        raise ValueError("Workbook SHA-256 is not hexadecimal") from error
    if not workbook.worksheets:
        raise ValueError("Every workbook must inventory at least one worksheet")
    ordinals = [sheet.ordinal for sheet in workbook.worksheets]
    if len(ordinals) != len(set(ordinals)):
        raise ValueError("Worksheet ordinals must be unique within a workbook")

    workbook_id = uuid7()
    occurrence_ids: list[str] = []
    with immediate_transaction(connection):
        if connection.execute(
            "SELECT 1 FROM import_transaction WHERE import_transaction_id = ?",
            (import_transaction_id,),
        ).fetchone() is None:
            raise ValueError("Import transaction does not exist")
        discovery = connection.execute(
            """SELECT item.submitted_filename, item.source_locator,
                      item.expected_file_hash_sha256, current.disposition_status,
                      current.import_discovery_disposition_id
               FROM import_discovery_item item
               JOIN v_current_import_discovery_disposition current
                 ON current.import_discovery_item_id = item.import_discovery_item_id
               WHERE item.import_discovery_item_id = ?
                 AND item.import_transaction_id = ?""",
            (import_discovery_item_id, import_transaction_id),
        ).fetchone()
        if discovery is None:
            raise ValueError("Workbook is not present in this import's discovery inventory")
        if discovery[3] != "Pending":
            raise ValueError("Discovered workbook already has a terminal disposition")
        if discovery[0] != workbook.filename or discovery[1] != workbook.source_locator:
            raise ValueError("Workbook identity does not match its discovery inventory item")
        if discovery[2] is not None and discovery[2] != workbook.file_hash_sha256.lower():
            raise ValueError("Workbook hash does not match its discovery inventory item")
        connection.execute(
            """INSERT INTO source_workbook
               (workbook_id, import_transaction_id, submitted_filename,
                source_locator, file_size_bytes, file_hash_sha256,
                filesystem_modified_at_utc, displayed_document_date,
                recorded_at_utc)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                workbook_id, import_transaction_id, workbook.filename,
                workbook.source_locator, workbook.file_size_bytes,
                workbook.file_hash_sha256.lower(), workbook.modified_at_utc,
                workbook.displayed_document_date, recorded_at_utc,
            ),
        )
        connection.execute(
            """INSERT INTO fingerprint
               (fingerprint_id, entity_type, entity_id, purpose, algorithm,
                digest, recorded_at_utc)
               VALUES (?, 'Source Workbook', ?, 'Exact File Duplicate',
                       'SHA-256', ?, ?)""",
            (uuid7(), workbook_id, workbook.file_hash_sha256.lower(), recorded_at_utc),
        )
        connection.execute(
            """INSERT INTO source_workbook_availability_event VALUES
               (?, ?, 'Unknown', NULL, 'Initial Import', ?,
                'External source has not yet been re-verified', NULL, ?)""",
            (uuid7(), workbook_id, audit.actor_user_id, recorded_at_utc),
        )
        for sheet in workbook.worksheets:
            worksheet_id = uuid7()
            occurrence_id = uuid7()
            prior = None
            if sheet.logical_fingerprint:
                prior = connection.execute(
                    """SELECT occurrence_id FROM source_occurrence
                       WHERE logical_fingerprint = ? AND terminal_status = 'Committed'
                       ORDER BY recorded_at_utc, occurrence_id LIMIT 1""",
                    (sheet.logical_fingerprint,),
                ).fetchone()
            terminal_status = "Duplicate" if prior else "Pending"
            connection.execute(
                """INSERT INTO source_worksheet
                   (worksheet_id, workbook_id, worksheet_ordinal,
                    submitted_name, visibility, used_range,
                    sheet_fingerprint, recorded_at_utc)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    worksheet_id, workbook_id, sheet.ordinal, sheet.name,
                    sheet.visibility, sheet.used_range, sheet.sheet_fingerprint,
                    recorded_at_utc,
                ),
            )
            connection.execute(
                """INSERT INTO source_occurrence
                   (occurrence_id, worksheet_id, region_locator,
                    logical_fingerprint, detection_rule_version_id,
                    detection_result, terminal_status,
                    duplicate_of_occurrence_id, recorded_at_utc)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    occurrence_id, worksheet_id, sheet.region_locator,
                    sheet.logical_fingerprint, detection_rule_version_id,
                    sheet.detection_result, terminal_status,
                    prior[0] if prior else None, recorded_at_utc,
                ),
            )
            connection.execute(
                """INSERT INTO source_occurrence_status_event VALUES
                   (?, ?, ?, 'Occurrence discovered during workbook registration', NULL, ?)""",
                (uuid7(), occurrence_id, terminal_status, recorded_at_utc),
            )
            occurrence_ids.append(occurrence_id)
        connection.execute(
            """INSERT INTO import_discovery_disposition
               (import_discovery_disposition_id, import_discovery_item_id,
                disposition_status, workbook_id, supersedes_disposition_id,
                recorded_at_utc)
               VALUES (?, ?, 'Registered', ?, ?, ?)""",
            (uuid7(), import_discovery_item_id, workbook_id, discovery[4], recorded_at_utc),
        )
        connection.execute(
            """UPDATE import_transaction
               SET discovered_count = discovered_count + ?
               WHERE import_transaction_id = ?""",
            (len(workbook.worksheets), import_transaction_id),
        )
        append_audit_event(
            connection, audit,
            {"workbook_id": workbook_id, "filename": workbook.filename, "worksheet_count": len(workbook.worksheets)},
        )
    return workbook_id, occurrence_ids


def record_source_workbook_availability(
    connection: sqlite3.Connection, *, workbook_id: str,
    availability_status: str, verification_method: str,
    checked_by_user_id: str | None, status_detail: str,
    checked_at_utc: str, audit: AuditContext,
    observed_file_hash_sha256: str | None = None,
) -> str:
    if availability_status not in {"Available Verified", "Unavailable", "Hash Mismatch"}:
        raise ValueError("Invalid source availability status")
    if not verification_method.strip() or not status_detail.strip():
        raise ValueError("Source availability requires method and detail")
    observed = None if observed_file_hash_sha256 is None else observed_file_hash_sha256.lower()
    if availability_status in {"Available Verified", "Hash Mismatch"}:
        if observed is None or len(observed) != 64:
            raise ValueError("Available or mismatched source requires an observed SHA-256")
        try:
            int(observed, 16)
        except ValueError as error:
            raise ValueError("Observed source SHA-256 is not hexadecimal") from error
    elif observed is not None:
        raise ValueError("Unavailable source cannot have an observed file hash")
    availability_id = uuid7()
    with immediate_transaction(connection):
        workbook = connection.execute("SELECT file_hash_sha256 FROM source_workbook WHERE workbook_id = ?", (workbook_id,)).fetchone()
        if workbook is None:
            raise ValueError("Source workbook does not exist")
        if availability_status == "Available Verified" and observed != workbook[0]:
            raise ValueError("Available Verified requires the original workbook hash")
        if availability_status == "Hash Mismatch" and observed == workbook[0]:
            raise ValueError("Hash Mismatch requires a different observed workbook hash")
        current = connection.execute("SELECT source_workbook_availability_event_id FROM v_current_source_workbook_availability WHERE workbook_id = ?", (workbook_id,)).fetchone()
        if current is None:
            raise ValueError("Source workbook has no availability history")
        connection.execute(
            "INSERT INTO source_workbook_availability_event VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (availability_id, workbook_id, availability_status, observed,
             verification_method.strip(), checked_by_user_id, status_detail.strip(), current[0], checked_at_utc),
        )
        append_audit_event(connection, audit, {"source_workbook_availability_event_id": availability_id,
            "workbook_id": workbook_id, "availability_status": availability_status,
            "observed_file_hash_sha256": observed})
    return availability_id


def record_discovery_disposition(
    connection: sqlite3.Connection,
    *,
    import_discovery_item_id: str,
    disposition_status: str,
    reason: str,
    recorded_at_utc: str,
    audit: AuditContext,
) -> str:
    if disposition_status not in {"Ignored", "Failed"}:
        raise ValueError("Explicit discovery disposition must be Ignored or Failed")
    if not reason.strip():
        raise ValueError("Ignored or failed workbooks require an explicit reason")
    disposition_id = uuid7()
    with immediate_transaction(connection):
        current = connection.execute(
            """SELECT import_discovery_disposition_id, disposition_status
               FROM v_current_import_discovery_disposition
               WHERE import_discovery_item_id = ?""",
            (import_discovery_item_id,),
        ).fetchone()
        if current is None:
            raise ValueError("Import discovery item does not exist")
        if current[1] != "Pending":
            raise ValueError("Discovered workbook already has a terminal disposition")
        connection.execute(
            """INSERT INTO import_discovery_disposition
               (import_discovery_disposition_id, import_discovery_item_id,
                disposition_status, disposition_reason,
                supersedes_disposition_id, recorded_at_utc)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (disposition_id, import_discovery_item_id, disposition_status,
             reason.strip(), current[0], recorded_at_utc),
        )
        append_audit_event(
            connection, audit,
            {"import_discovery_item_id": import_discovery_item_id,
             "disposition_status": disposition_status, "reason": reason.strip()},
        )
    return disposition_id


def stage_observation(
    connection: sqlite3.Connection,
    *,
    occurrence_id: str,
    provisional_supplier_code: str | None,
    provisional_supplier_name: str | None,
    provisional_part_number: str | None,
    import_context_type: str,
    import_context_id: str | None,
    blocking_issues: tuple[tuple[str, str], ...],
    recorded_at_utc: str,
    audit: AuditContext,
) -> str:
    staged_id = uuid7()
    status = "Blocked" if blocking_issues else "Ready to Commit"
    with immediate_transaction(connection):
        occurrence = connection.execute(
            "SELECT terminal_status FROM source_occurrence WHERE occurrence_id = ?",
            (occurrence_id,),
        ).fetchone()
        if occurrence is None:
            raise ValueError("Source occurrence does not exist")
        if occurrence[0] == "Duplicate":
            raise ValueError("Duplicate occurrences cannot be staged as new evidence")
        transaction_id = connection.execute(
            """SELECT workbook.import_transaction_id FROM source_occurrence occurrence
               JOIN source_worksheet worksheet ON worksheet.worksheet_id = occurrence.worksheet_id
               JOIN source_workbook workbook ON workbook.workbook_id = worksheet.workbook_id
               WHERE occurrence.occurrence_id = ?""", (occurrence_id,),
        ).fetchone()[0]
        _advance_import_session(connection, transaction_id, "Staging", recorded_at_utc)
        connection.execute(
            """INSERT INTO staged_observation
               (staged_observation_id, occurrence_id,
                provisional_supplier_code, provisional_supplier_name,
                provisional_part_number, import_context_type,
                import_context_id, status, blocking_issue_count, recorded_at_utc)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                staged_id, occurrence_id, provisional_supplier_code,
                provisional_supplier_name, provisional_part_number,
                import_context_type, import_context_id, status,
                len(blocking_issues), recorded_at_utc,
            ),
        )
        connection.execute(
            """INSERT INTO staged_observation_status_event VALUES
               (?, ?, ?, ?, 'Observation staged', NULL, ?)""",
            (uuid7(), staged_id, status, len(blocking_issues), recorded_at_utc),
        )
        for issue_type, detail in blocking_issues:
            connection.execute(
                """INSERT INTO staging_issue
                   (staging_issue_id, staged_observation_id, issue_type,
                    issue_detail, blocking_flag, recorded_at_utc)
                   VALUES (?, ?, ?, ?, 1, ?)""",
                (uuid7(), staged_id, issue_type, detail, recorded_at_utc),
            )
        if blocking_issues:
            _append_occurrence_status(connection, occurrence_id, "Blocked",
                                      "Staging contains blocking issues", recorded_at_utc)
        append_audit_event(
            connection, audit,
            {"staged_observation_id": staged_id, "occurrence_id": occurrence_id, "status": status, "blocking_issue_count": len(blocking_issues)},
        )
    return staged_id


def record_staging_resolution(
    connection: sqlite3.Connection,
    *,
    staging_issue_id: str,
    decision_code: str,
    decided_by_user_id: str,
    decision_reason: str,
    recorded_at_utc: str,
    audit: AuditContext,
    selected_entity_type: str | None = None,
    selected_entity_id: str | None = None,
    selected_value: str | None = None,
    supersedes_staging_resolution_id: str | None = None,
) -> str:
    """Append a governed issue decision and recover a fully resolved observation."""
    if not decision_code.strip():
        raise ValueError("Staging resolution requires a decision code")
    if not decided_by_user_id.strip():
        raise ValueError("Staging resolution requires the deciding user")
    if not decision_reason.strip():
        raise ValueError("Staging resolution requires a reason")
    if (selected_entity_type is None) != (selected_entity_id is None):
        raise ValueError("Selected entity type and identifier must be supplied together")

    resolution_id = uuid7()
    with immediate_transaction(connection):
        issue = connection.execute(
            """SELECT issue.staged_observation_id, staged.occurrence_id,
                      staged.status
               FROM staging_issue issue
               JOIN staged_observation staged
                 ON staged.staged_observation_id = issue.staged_observation_id
               WHERE issue.staging_issue_id = ?""",
            (staging_issue_id,),
        ).fetchone()
        if issue is None:
            raise ValueError("Staging issue does not exist")
        if issue[2] in {"Committed", "Duplicate", "Ignored", "Failed"}:
            raise ValueError("Terminal staged observations cannot be revised")

        current = connection.execute(
            """SELECT staging_resolution_id FROM v_current_staging_resolution
               WHERE staging_issue_id = ?""",
            (staging_issue_id,),
        ).fetchone()
        current_id = None if current is None else str(current[0])
        if current_id is None and supersedes_staging_resolution_id is not None:
            raise ValueError("Initial staging resolution cannot supersede another resolution")
        if current_id is not None and supersedes_staging_resolution_id != current_id:
            raise ValueError("A revised decision must supersede the current staging resolution")

        connection.execute(
            """INSERT INTO staging_resolution
               (staging_resolution_id, staging_issue_id, decision_code,
                selected_entity_type, selected_entity_id, selected_value,
                decided_by_user_id, decision_reason, recorded_at_utc,
                supersedes_staging_resolution_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (resolution_id, staging_issue_id, decision_code.strip(),
             selected_entity_type, selected_entity_id, selected_value,
             decided_by_user_id.strip(), decision_reason.strip(), recorded_at_utc,
             supersedes_staging_resolution_id),
        )

        staged_id, occurrence_id = str(issue[0]), str(issue[1])
        unresolved_count = connection.execute(
            """SELECT COUNT(*) FROM staging_issue issue
               WHERE issue.staged_observation_id = ? AND issue.blocking_flag = 1
                 AND NOT EXISTS (
                     SELECT 1 FROM v_current_staging_resolution resolution
                     WHERE resolution.staging_issue_id = issue.staging_issue_id
                 )""",
            (staged_id,),
        ).fetchone()[0]
        staged_status = "Ready to Commit" if unresolved_count == 0 else "Needs Review"
        prior_status = connection.execute(
            """SELECT staged_observation_status_event_id
               FROM v_current_staged_observation_status
               WHERE staged_observation_id = ?""",
            (staged_id,),
        ).fetchone()
        if prior_status is None:
            raise ValueError("Staged observation has no status history")
        connection.execute(
            """UPDATE staged_observation SET status = ?, blocking_issue_count = ?
               WHERE staged_observation_id = ?""",
            (staged_status, unresolved_count, staged_id),
        )
        connection.execute(
            """INSERT INTO staged_observation_status_event VALUES
               (?, ?, ?, ?, 'Blocking issue resolution recorded', ?, ?)""",
            (uuid7(), staged_id, staged_status, unresolved_count,
             prior_status[0], recorded_at_utc),
        )
        occurrence_status = connection.execute(
            "SELECT terminal_status FROM source_occurrence WHERE occurrence_id = ?",
            (occurrence_id,),
        ).fetchone()[0]
        if unresolved_count == 0 and occurrence_status == "Blocked":
            _append_occurrence_status(
                connection, occurrence_id, "Pending",
                "All blocking staging issues have current resolutions",
                recorded_at_utc,
            )
        append_audit_event(
            connection, audit,
            {"staging_resolution_id": resolution_id,
             "staging_issue_id": staging_issue_id,
             "staged_observation_id": staged_id,
             "decision_code": decision_code.strip(),
             "remaining_blocking_issue_count": unresolved_count},
        )
    return resolution_id


def record_occurrence_disposition(
    connection: sqlite3.Connection,
    *,
    occurrence_id: str,
    terminal_status: str,
    reason: str,
    recorded_at_utc: str,
    audit: AuditContext,
) -> None:
    if terminal_status not in {"Ignored", "Failed"}:
        raise ValueError("Explicit occurrence disposition must be Ignored or Failed")
    if not reason.strip():
        raise ValueError("Ignored or failed worksheet occurrences require an explicit reason")
    with immediate_transaction(connection):
        occurrence = connection.execute(
            "SELECT terminal_status FROM source_occurrence WHERE occurrence_id = ?",
            (occurrence_id,),
        ).fetchone()
        if occurrence is None:
            raise ValueError("Source occurrence does not exist")
        if occurrence[0] != "Pending":
            raise ValueError("Only pending worksheet occurrences may be dispositioned")
        _append_occurrence_status(connection, occurrence_id, terminal_status,
                                  reason.strip(), recorded_at_utc)
        append_audit_event(
            connection, audit,
            {"occurrence_id": occurrence_id, "terminal_status": terminal_status,
             "reason": reason.strip(), "recorded_at_utc": recorded_at_utc},
            [{"entity_type": "Source Occurrence", "entity_id": occurrence_id,
              "field_path": "terminal_status", "before": "Pending",
              "after": terminal_status}],
        )


def commit_observation(
    connection: sqlite3.Connection,
    *,
    staged_observation_id: str,
    observation: ObservationCommit,
    recorded_at_utc: str,
    audit: AuditContext,
) -> str:
    staged_gate = connection.execute(
        """SELECT status, blocking_issue_count FROM staged_observation
           WHERE staged_observation_id = ?""", (staged_observation_id,),
    ).fetchone()
    if staged_gate is None:
        raise ValueError("Staged observation does not exist")
    if staged_gate[0] != "Ready to Commit" or staged_gate[1] != 0:
        raise ValueError("Only an unblocked Ready to Commit observation may be committed")
    if not observation.fields:
        raise ValueError("Committed observation requires field-level source evidence")
    if observation.part_id is None or not observation.submitted_part_number.strip():
        raise ValueError("Committed observation requires a valid canonical and submitted part identity")
    if not observation.submitted_supplier_name.strip():
        raise ValueError("Committed observation requires the submitted supplier identity")
    if observation.submitted_part_description is None or not observation.submitted_part_description.strip():
        raise ValueError("Committed observation requires a non-empty part description")
    if observation.observation_context == "Historical Baseline" and (
        observation.economic_date is None or observation.economic_date_precision == "Unknown"
    ):
        raise ValueError("Historical observations require a buyer-confirmed economic date")
    field_codes = [field.field_code for field in observation.fields]
    if len(field_codes) != len(set(field_codes)):
        raise ValueError("Committed observation cannot contain duplicate semantic field codes")
    for field in observation.fields:
        typed_count = sum(value is not None for value in (
            field.exact_decimal, field.text_value, field.date_value, field.boolean_value,
        ))
        if typed_count != 1:
            raise ValueError(f"Field {field.field_code} requires exactly one typed value")
        if field.exact_decimal is not None and field.precision_status == "Eligible" \
                and field.normalized_unit_id is None:
            raise ValueError(f"Eligible decimal field {field.field_code} requires a normalized unit")
    piece_prices = [field for field in observation.fields if field.field_code == "PIECE_PRICE"]
    if len(piece_prices) != 1 or piece_prices[0].exact_decimal is None \
            or piece_prices[0].precision_status != "Eligible" \
            or piece_prices[0].currency_id is None \
            or piece_prices[0].normalized_unit_id is None:
        raise ValueError("Committed observation requires one eligible piece price with currency and unit")
    observation_id = uuid7()
    with immediate_transaction(connection):
        staged = connection.execute(
            """SELECT occurrence_id, status, blocking_issue_count,
                      import_context_type, import_context_id
               FROM staged_observation WHERE staged_observation_id = ?""",
            (staged_observation_id,),
        ).fetchone()
        if staged is None:
            raise ValueError("Staged observation does not exist")
        if staged["status"] != "Ready to Commit" or staged["blocking_issue_count"] != 0:
            raise ValueError("Only an unblocked Ready to Commit observation may be committed")
        if staged["import_context_type"] != observation.observation_context or \
                staged["import_context_id"] != observation.context_id:
            raise ValueError("Committed observation context must match its staged import context")
        transaction_id = connection.execute(
            """SELECT workbook.import_transaction_id FROM source_occurrence occurrence
               JOIN source_worksheet worksheet ON worksheet.worksheet_id = occurrence.worksheet_id
               JOIN source_workbook workbook ON workbook.workbook_id = worksheet.workbook_id
               WHERE occurrence.occurrence_id = ?""", (staged["occurrence_id"],),
        ).fetchone()[0]
        _advance_import_session(connection, transaction_id, "Committing", recorded_at_utc)
        worksheet_id = connection.execute(
            "SELECT worksheet_id FROM source_occurrence WHERE occurrence_id = ?",
            (staged["occurrence_id"],),
        ).fetchone()[0]
        connection.execute(
            """INSERT INTO pbd_observation
               (observation_id, staged_observation_id, occurrence_id,
                observation_context, context_id, supplier_id,
                supplier_plant_id, part_id, submitted_supplier_name,
                submitted_part_number, submitted_part_description,
                economic_date, economic_date_precision, structure_category,
                recorded_at_utc)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                observation_id, staged_observation_id, staged["occurrence_id"],
                observation.observation_context, observation.context_id,
                observation.supplier_id, observation.supplier_plant_id,
                observation.part_id, observation.submitted_supplier_name,
                observation.submitted_part_number,
                observation.submitted_part_description,
                observation.economic_date, observation.economic_date_precision,
                observation.structure_category, recorded_at_utc,
            ),
        )
        supplier_identity_action_id = None
        if observation.supplier_id is None and observation.observation_context == "Sourcing Event":
            supplier_identity_action_id = uuid7()
            supplier_identity_action_version_id = uuid7()
            connection.execute(
                """INSERT INTO buyer_action
                   (buyer_action_id, event_id, supplier_id, part_id,
                    governing_entity_type, governing_entity_id, issue_type,
                    created_by_user_id, created_at_utc)
                   VALUES (?, ?, NULL, ?, 'PBD Observation', ?,
                           'Supplier Code Confirmation Required', ?, ?)""",
                (supplier_identity_action_id, observation.context_id, observation.part_id,
                 observation_id, audit.actor_user_id, recorded_at_utc),
            )
            connection.execute(
                """INSERT INTO buyer_action_version
                   (buyer_action_version_id, buyer_action_id, action_status,
                    required_supplier_action, owner_user_id, recorded_by_user_id,
                    recorded_at_utc)
                   VALUES (?, ?, 'Open', 'Confirm supplier code and identity', ?, ?, ?)""",
                (supplier_identity_action_version_id, supplier_identity_action_id,
                 audit.actor_user_id, audit.actor_user_id, recorded_at_utc),
            )
        for field in observation.fields:
            source_datum_id = uuid7()
            context_hash = hashlib.sha256(
                f"{worksheet_id}|{field.cell_or_range}|{field.submitted_lexeme}|{field.formula_text}".encode("utf-8")
            ).hexdigest()
            connection.execute(
                """INSERT INTO source_datum
                   (source_datum_id, worksheet_id, cell_or_range,
                    submitted_lexeme, formula_text, cached_value_lexeme,
                    context_hash, recorded_at_utc)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    source_datum_id, worksheet_id, field.cell_or_range,
                    field.submitted_lexeme, field.formula_text,
                    field.cached_value_lexeme, context_hash, recorded_at_utc,
                ),
            )
            exact = field.exact_decimal
            governing = exact.governing_1e4() if exact and field.precision_status == "Eligible" else None
            connection.execute(
                """INSERT INTO submitted_datum
                   (submitted_datum_id, observation_id, field_code,
                    source_datum_id, submitted_lexeme, decimal_coefficient,
                    decimal_scale, governing_1e4, text_value, date_value,
                    boolean_value, normalized_unit_id, currency_id,
                    precision_status, recorded_at_utc)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    uuid7(), observation_id, field.field_code, source_datum_id,
                    field.submitted_lexeme,
                    exact.coefficient if exact else None,
                    exact.scale if exact else None,
                    governing, field.text_value, field.date_value,
                    None if field.boolean_value is None else int(field.boolean_value),
                    field.normalized_unit_id, field.currency_id,
                    field.precision_status, recorded_at_utc,
                ),
            )
        connection.execute(
            """UPDATE staged_observation SET status = 'Committed'
               WHERE staged_observation_id = ?""",
            (staged_observation_id,),
        )
        staged_prior = connection.execute(
            """SELECT staged_observation_status_event_id
               FROM v_current_staged_observation_status WHERE staged_observation_id = ?""",
            (staged_observation_id,),
        ).fetchone()[0]
        connection.execute(
            """INSERT INTO staged_observation_status_event VALUES
               (?, ?, 'Committed', 0, 'Immutable observation committed', ?, ?)""",
            (uuid7(), staged_observation_id, staged_prior, recorded_at_utc),
        )
        _append_occurrence_status(connection, staged["occurrence_id"], "Committed",
                                  "Immutable observation committed", recorded_at_utc)
        append_audit_event(
            connection, audit,
            {"observation_id": observation_id, "staged_observation_id": staged_observation_id,
             "field_count": len(observation.fields),
             "supplier_identity_action_id": supplier_identity_action_id},
            [{"entity_type": "PBD Observation", "entity_id": observation_id, "after": {"submitted_part_number": observation.submitted_part_number}}],
        )
    return observation_id


def finalize_import_transaction(
    connection: sqlite3.Connection,
    *,
    import_transaction_id: str,
    completed_at_utc: str,
    audit: AuditContext,
) -> dict[str, int]:
    with immediate_transaction(connection):
        discovery = connection.execute(
            """SELECT discovered_workbook_count, registered_workbook_count,
                      ignored_workbook_count, failed_workbook_count,
                      pending_workbook_count
               FROM v_import_discovery_reconciliation
               WHERE import_transaction_id = ?""",
            (import_transaction_id,),
        ).fetchone()
        if discovery is None:
            raise ValueError("Import transaction cannot complete without a discovery inventory")
        if discovery[4] != 0:
            raise ValueError("Import transaction cannot complete while discovered workbooks are pending")
        registered = connection.execute(
            "SELECT COUNT(*) FROM source_workbook WHERE import_transaction_id = ?",
            (import_transaction_id,),
        ).fetchone()[0]
        if registered != discovery[1]:
            raise ValueError("Registered workbook count does not reconcile to discovery inventory")
        row = connection.execute(
            "SELECT * FROM v_source_tab_reconciliation WHERE import_transaction_id = ?",
            (import_transaction_id,),
        ).fetchone()
        if row is None:
            raise ValueError("Import transaction does not exist")
        if row["pending_count"] != 0:
            raise ValueError("Import transaction cannot complete while source occurrences are pending")
        counts = {name: int(row[name]) for name in (
            "occurrence_count", "committed_count", "duplicate_count",
            "blocked_count", "ignored_count", "failed_count", "pending_count",
        )}
        connection.execute(
            """UPDATE import_transaction SET status = 'Committed',
               discovered_count = ?, committed_count = ?, duplicate_count = ?,
               blocked_count = ?, ignored_count = ?, failed_count = ?,
               completed_at_utc = ? WHERE import_transaction_id = ?""",
            (
                counts["occurrence_count"], counts["committed_count"],
                counts["duplicate_count"], counts["blocked_count"],
                counts["ignored_count"], counts["failed_count"],
                completed_at_utc, import_transaction_id,
            ),
        )
        prior_transaction_status = connection.execute(
            """SELECT import_transaction_status_event_id
               FROM v_current_import_transaction_status WHERE import_transaction_id = ?""",
            (import_transaction_id,),
        ).fetchone()[0]
        connection.execute(
            """INSERT INTO import_transaction_status_event VALUES
               (?, ?, 'Committed', ?, 'All discovered evidence reconciled', ?, ?)""",
            (uuid7(), import_transaction_id, canonical_json(counts),
             prior_transaction_status, completed_at_utc),
        )
        session_id = connection.execute(
            "SELECT import_session_id FROM import_transaction WHERE import_transaction_id = ?",
            (import_transaction_id,),
        ).fetchone()[0]
        _advance_import_session(connection, import_transaction_id, "Completed", completed_at_utc)
        connection.execute("UPDATE import_session SET completed_at_utc = ? WHERE import_session_id = ?",
                           (completed_at_utc, session_id))
        append_audit_event(connection, audit, {"import_transaction_id": import_transaction_id, "counts": counts})
    return counts
