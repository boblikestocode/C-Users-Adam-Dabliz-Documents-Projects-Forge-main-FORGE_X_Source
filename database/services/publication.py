from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from database.migration_runner import validate_database

from .audit import AuditContext, append_audit_event, canonical_json, verify_audit_chain
from .connection import connect, immediate_transaction
from .ids import uuid7


Signer = Callable[[bytes], str]


@dataclass(frozen=True)
class CheckpointResult:
    checkpoint_id: str
    checkpoint_path: Path
    database_hash: str
    audit_chain_anchor: str
    schema_version: str
    verification_manifest_hash: str
    representative_reproduction_hash: str
    verification_manifest: dict[str, object]


@dataclass(frozen=True)
class PublicationResult:
    publication_id: str
    manifest_hash: str
    digital_signature: str


@dataclass(frozen=True)
class PublicationAttemptResult:
    publication_attempt_id: str
    attempt_status: str
    may_upload: bool


@dataclass(frozen=True)
class RestoreTestResult:
    restore_event_id: str
    restore_status: str
    restored_path: Path
    failures: tuple[str, ...]


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


REPRODUCTION_COUNT_TABLES = (
    "source_workbook", "source_worksheet", "source_occurrence",
    "pbd_observation", "submitted_datum", "operation_line", "material_line",
    "supplier_quote_round", "round_observation", "calculation_result",
    "finalized_snapshot", "snapshot_result", "buyer_action",
)


def representative_reproduction(connection: sqlite3.Connection) -> tuple[str, dict[str, int]]:
    counts = {
        table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        for table in REPRODUCTION_COUNT_TABLES
    }
    active_rounds = [tuple(row) for row in connection.execute(
        """SELECT active.event_id, active.supplier_id, active.quote_round_id,
                  round.round_number
           FROM v_active_supplier_round active
           JOIN supplier_quote_round round
             ON round.quote_round_id = active.quote_round_id
           ORDER BY active.event_id, active.supplier_id"""
    )]
    snapshot_results = [tuple(row) for row in connection.execute(
        """SELECT finalized_snapshot_id, result_code, supplier_id, part_id,
                  program_year, category_code, decimal_coefficient, decimal_scale,
                  text_value, normalized_unit_id, currency_id,
                  presentation_rule_version_id
           FROM snapshot_result
           ORDER BY finalized_snapshot_id, result_code, supplier_id, part_id,
                    program_year, category_code, snapshot_result_id"""
    )]
    payload = {
        "record_counts": counts,
        "active_supplier_rounds": active_rounds,
        "snapshot_results": snapshot_results,
    }
    digest = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
    return digest, counts


def create_verified_checkpoint(
    connection: sqlite3.Connection,
    *,
    checkpoint_path: Path,
    created_at_utc: str,
    audit: AuditContext,
) -> CheckpointResult:
    if checkpoint_path.exists():
        raise FileExistsError(f"Refusing to overwrite checkpoint: {checkpoint_path}")
    if connection.in_transaction:
        raise RuntimeError("Checkpoint cannot begin inside an active transaction")
    audit_failures = verify_audit_chain(connection)
    if audit_failures:
        raise RuntimeError(f"Audit chain verification failed: {audit_failures}")
    identity = connection.execute(
        "SELECT database_id, commodity_id, schema_version FROM commodity_database"
    ).fetchone()
    if identity is None:
        raise RuntimeError("Commodity database identity is missing")
    prior = connection.execute(
        "SELECT event_hash FROM audit_event ORDER BY recorded_sequence DESC LIMIT 1"
    ).fetchone()
    audit_anchor = prior[0] if prior else hashlib.sha256(b"FORGE-X-GENESIS").hexdigest()
    audit_sequence = int(connection.execute(
        "SELECT COALESCE(MAX(recorded_sequence), 0) FROM audit_event"
    ).fetchone()[0])

    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    destination = sqlite3.connect(checkpoint_path)
    try:
        connection.backup(destination)
    finally:
        destination.close()
    failures = validate_database(checkpoint_path)
    checkpoint_connection = connect(checkpoint_path, read_only=True)
    try:
        failures.extend(verify_audit_chain(checkpoint_connection))
        reproduction_hash, record_counts = representative_reproduction(checkpoint_connection)
        registry_versions = [tuple(row) for row in checkpoint_connection.execute(
            """SELECT registry_publication_id, registry_version, publication_hash
               FROM registry_cache_generation WHERE activation_status = 'Active'
               ORDER BY registry_publication_id"""
        )]
    finally:
        checkpoint_connection.close()
    if failures:
        checkpoint_path.unlink(missing_ok=True)
        raise RuntimeError(f"Checkpoint verification failed: {failures}")

    database_hash = hash_file(checkpoint_path)
    verification_manifest: dict[str, object] = {
        "database_id": str(identity[0]),
        "commodity_id": str(identity[1]),
        "schema_version": str(identity[2]),
        "database_hash": database_hash,
        "audit_chain_anchor": str(audit_anchor),
        "audit_event_sequence": audit_sequence,
        "record_counts": record_counts,
        "registry_versions": registry_versions,
        "representative_reproduction_hash": reproduction_hash,
        "created_at_utc": created_at_utc,
    }
    verification_manifest_hash = hashlib.sha256(
        canonical_json(verification_manifest).encode("utf-8")
    ).hexdigest()
    checkpoint_id = uuid7()
    with immediate_transaction(connection):
        connection.execute(
            """INSERT INTO local_checkpoint
               (local_checkpoint_id, database_id, schema_version,
                database_hash, audit_chain_anchor, verification_status,
                created_at_utc, verified_at_utc)
               VALUES (?, ?, ?, ?, ?, 'Verified', ?, ?)""",
            (
                checkpoint_id, identity[0], identity[2], database_hash,
                audit_anchor, created_at_utc, created_at_utc,
            ),
        )
        connection.execute(
            """INSERT INTO local_checkpoint_verification_manifest
               (local_checkpoint_verification_manifest_id, local_checkpoint_id,
                manifest_payload, manifest_hash,
                representative_reproduction_hash, audit_event_sequence,
                record_count_payload, registry_version_payload, recorded_at_utc)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                uuid7(), checkpoint_id, canonical_json(verification_manifest),
                verification_manifest_hash, reproduction_hash, audit_sequence,
                canonical_json(record_counts), canonical_json(registry_versions),
                created_at_utc,
            ),
        )
        append_audit_event(
            connection, audit,
            {"local_checkpoint_id": checkpoint_id, "database_hash": database_hash,
             "audit_chain_anchor": audit_anchor, "schema_version": identity[2],
             "verification_manifest_hash": verification_manifest_hash,
             "representative_reproduction_hash": reproduction_hash},
        )
    return CheckpointResult(
        checkpoint_id, checkpoint_path, database_hash, audit_anchor,
        str(identity[2]), verification_manifest_hash, reproduction_hash,
        verification_manifest,
    )


def prepare_publication(
    connection: sqlite3.Connection,
    *,
    checkpoint: CheckpointResult,
    destination_locator: str,
    signer: Signer,
    initiated_at_utc: str,
    audit: AuditContext,
) -> PublicationResult:
    stored = connection.execute(
        """SELECT verification_status, database_hash, audit_chain_anchor,
                  schema_version, manifest.manifest_hash,
                  manifest.representative_reproduction_hash,
                  manifest.manifest_payload
           FROM local_checkpoint
           JOIN local_checkpoint_verification_manifest manifest
             ON manifest.local_checkpoint_id = local_checkpoint.local_checkpoint_id
           WHERE local_checkpoint.local_checkpoint_id = ?""",
        (checkpoint.checkpoint_id,),
    ).fetchone()
    if stored is None or stored[0] != "Verified":
        raise ValueError("Publication requires a verified checkpoint")
    restored = connection.execute(
        """SELECT 1 FROM restore_event
           WHERE local_checkpoint_id = ? AND restore_type = 'Automated Test'
             AND restore_status = 'Verified' LIMIT 1""",
        (checkpoint.checkpoint_id,),
    ).fetchone()
    if restored is None:
        raise ValueError("Publication requires a successful automated restore test")
    current_hash = hash_file(checkpoint.checkpoint_path)
    if current_hash != stored[1] or current_hash != checkpoint.database_hash:
        raise RuntimeError("Checkpoint file hash no longer matches its verified record")
    stored_manifest_hash = hashlib.sha256(str(stored[6]).encode("utf-8")).hexdigest()
    if (
        stored_manifest_hash != stored[4]
        or checkpoint.verification_manifest_hash != stored[4]
        or checkpoint.representative_reproduction_hash != stored[5]
    ):
        raise RuntimeError("Checkpoint verification manifest no longer matches its stored record")
    stored_manifest = json.loads(str(stored[6]))
    publication_id = uuid7()
    manifest = {
        "publication_id": publication_id,
        "checkpoint_id": checkpoint.checkpoint_id,
        "database_hash": current_hash,
        "audit_chain_anchor": stored[2],
        "schema_version": stored[3],
        "filename": checkpoint.checkpoint_path.name,
        "size_bytes": checkpoint.checkpoint_path.stat().st_size,
        "destination_locator": destination_locator,
        "verification_manifest_hash": checkpoint.verification_manifest_hash,
        "representative_reproduction_hash": checkpoint.representative_reproduction_hash,
    }
    manifest_bytes = canonical_json(manifest).encode("utf-8")
    manifest_hash = hashlib.sha256(manifest_bytes).hexdigest()
    signature = signer(manifest_bytes)
    if not signature:
        raise ValueError("Publication signer returned an empty signature")
    with immediate_transaction(connection):
        connection.execute(
            """INSERT INTO publication
               (publication_id, local_checkpoint_id, destination_locator,
                publication_status, package_hash, audit_chain_anchor,
                digital_signature, initiated_at_utc)
               VALUES (?, ?, ?, 'Prepared', ?, ?, ?, ?)""",
            (
                publication_id, checkpoint.checkpoint_id,
                destination_locator, manifest_hash, stored[2], signature,
                initiated_at_utc,
            ),
        )
        connection.execute(
            """INSERT INTO publication_manifest_entry
               (publication_manifest_entry_id, publication_id, entry_type,
                relative_path, size_bytes, content_hash, version_payload)
               VALUES (?, ?, 'Commodity Database', ?, ?, ?, ?)""",
            (
                uuid7(), publication_id, checkpoint.checkpoint_path.name,
                checkpoint.checkpoint_path.stat().st_size, current_hash,
                canonical_json({
                    "schema_version": stored[3], "audit_chain_anchor": stored[2],
                    "checkpoint_verification_manifest": stored_manifest,
                    "checkpoint_verification_manifest_hash": stored[4],
                    "publishing_identity": audit.actor_user_id,
                }),
            ),
        )
        connection.execute(
            """INSERT INTO publication_status_event
               (publication_status_event_id, publication_id,
                publication_status, status_reason, recorded_at_utc)
               VALUES (?, ?, 'Prepared', 'Verified checkpoint manifest prepared', ?)""",
            (uuid7(), publication_id, initiated_at_utc),
        )
        append_audit_event(
            connection, audit,
            {"publication_id": publication_id, "local_checkpoint_id": checkpoint.checkpoint_id, "manifest_hash": manifest_hash, "destination_locator": destination_locator},
        )
    return PublicationResult(publication_id, manifest_hash, signature)


def queue_publication(
    connection: sqlite3.Connection,
    *,
    publication_id: str,
    recorded_at_utc: str,
    audit: AuditContext,
) -> str:
    publication = connection.execute(
        """SELECT p.package_hash, cps.publication_status
           FROM publication p
           JOIN v_current_publication_status cps ON cps.publication_id = p.publication_id
           WHERE p.publication_id = ?""",
        (publication_id,),
    ).fetchone()
    if publication is None:
        raise ValueError("Publication does not exist")
    idempotency_key = hashlib.sha256(f"{publication_id}|{publication[0]}".encode("utf-8")).hexdigest()
    existing = connection.execute(
        "SELECT sync_queue_item_id FROM sync_queue_item WHERE idempotency_key = ?",
        (idempotency_key,),
    ).fetchone()
    if existing:
        return str(existing[0])
    if publication[1] not in ("Prepared", "Locally Verified", "Retry Pending"):
        raise ValueError("Only a prepared or retryable publication may be queued")
    queue_id = uuid7()
    with immediate_transaction(connection):
        connection.execute(
            """INSERT INTO sync_queue_item
               (sync_queue_item_id, publication_id, idempotency_key,
                queue_status, recorded_at_utc)
               VALUES (?, ?, ?, 'Queued', ?)""",
            (queue_id, publication_id, idempotency_key, recorded_at_utc),
        )
        connection.execute(
            """INSERT INTO publication_status_event
               (publication_status_event_id, publication_id,
                publication_status, status_reason, recorded_at_utc)
               VALUES (?, ?, 'Queued', 'Queued for idempotent publication', ?)""",
            (uuid7(), publication_id, recorded_at_utc),
        )
        append_audit_event(
            connection, audit,
            {"publication_id": publication_id, "sync_queue_item_id": queue_id, "idempotency_key": idempotency_key},
        )
    return queue_id


def begin_publication_attempt(
    connection: sqlite3.Connection,
    *,
    sync_queue_item_id: str,
    lease_owner: str,
    lease_expires_at_utc: str,
    authority_verification_reference: str,
    authority_verified_online_at_utc: str,
    expected_predecessor_publication_id: str | None,
    observed_remote_publication_id: str | None,
    started_at_utc: str,
    audit: AuditContext,
) -> PublicationAttemptResult:
    if not lease_owner.strip() or not authority_verification_reference.strip():
        raise ValueError("Publication requires worker identity and fresh authority evidence")
    if authority_verified_online_at_utc != started_at_utc:
        raise ValueError("SharePoint publication requires fresh online authority verification")
    if lease_expires_at_utc <= started_at_utc:
        raise ValueError("Publication lease must expire after the attempt starts")
    attempt_id = uuid7()
    with immediate_transaction(connection):
        queue = connection.execute(
            """SELECT queue.publication_id, queue.queue_status,
                      queue.lease_expires_at_utc, publication.package_hash
               FROM sync_queue_item queue
               JOIN publication ON publication.publication_id = queue.publication_id
               WHERE queue.sync_queue_item_id = ?""",
            (sync_queue_item_id,),
        ).fetchone()
        if queue is None:
            raise ValueError("Publication queue item does not exist")
        if queue[1] not in ("Queued", "Retry Pending"):
            raise ValueError("Only queued or retry-pending publications may be leased")
        attempt_number = int(connection.execute(
            """SELECT COALESCE(MAX(attempt_number), 0) + 1 FROM publication_attempt
               WHERE sync_queue_item_id = ?""", (sync_queue_item_id,)
        ).fetchone()[0])
        connection.execute(
            """INSERT INTO publication_attempt
               (publication_attempt_id, publication_id, sync_queue_item_id,
                attempt_number, lease_owner, authority_verification_reference,
                authority_verified_online_at_utc,
                expected_predecessor_publication_id,
                observed_remote_publication_id, started_at_utc)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                attempt_id, queue[0], sync_queue_item_id, attempt_number,
                lease_owner.strip(), authority_verification_reference.strip(),
                authority_verified_online_at_utc,
                expected_predecessor_publication_id,
                observed_remote_publication_id, started_at_utc,
            ),
        )
        if expected_predecessor_publication_id != observed_remote_publication_id:
            connection.execute(
                """INSERT INTO sync_conflict
                   (sync_conflict_id, local_publication_id, remote_publication_id,
                    conflict_type, conflict_status, detected_at_utc)
                   VALUES (?, ?, ?, 'Unexpected Remote Predecessor', 'Open', ?)""",
                (uuid7(), queue[0], observed_remote_publication_id, started_at_utc),
            )
            connection.execute(
                """UPDATE sync_queue_item SET queue_status = 'Conflict',
                   lease_owner = NULL, lease_expires_at_utc = NULL,
                   last_error = 'Unexpected remote predecessor'
                   WHERE sync_queue_item_id = ?""", (sync_queue_item_id,),
            )
            status = "Conflict"
            detail = "Remote latest publication differs from the expected predecessor"
        else:
            connection.execute(
                """UPDATE sync_queue_item SET queue_status = 'Leased',
                   lease_owner = ?, lease_expires_at_utc = ?, last_error = NULL
                   WHERE sync_queue_item_id = ?""",
                (lease_owner.strip(), lease_expires_at_utc, sync_queue_item_id),
            )
            status = "Uploading"
            detail = "Expected predecessor and fresh authority evidence verified"
        connection.execute(
            """INSERT INTO publication_attempt_event VALUES (?, ?, ?, NULL, ?, ?)""",
            (uuid7(), attempt_id, status, detail, started_at_utc),
        )
        connection.execute(
            """INSERT INTO publication_status_event
               (publication_status_event_id, publication_id,
                publication_status, status_reason, recorded_at_utc)
               VALUES (?, ?, ?, ?, ?)""",
            (uuid7(), queue[0], status, detail, started_at_utc),
        )
        append_audit_event(
            connection, audit,
            {"publication_attempt_id": attempt_id, "publication_id": queue[0],
             "attempt_number": attempt_number, "status": status,
             "expected_predecessor_publication_id": expected_predecessor_publication_id,
             "observed_remote_publication_id": observed_remote_publication_id,
             "authority_verification_reference": authority_verification_reference.strip()},
        )
    return PublicationAttemptResult(attempt_id, status, status == "Uploading")


def complete_publication_attempt(
    connection: sqlite3.Connection,
    *,
    publication_attempt_id: str,
    lease_owner: str,
    outcome: str,
    observed_remote_hash: str | None,
    detail: str,
    completed_at_utc: str,
    audit: AuditContext,
) -> str:
    if outcome not in {"Published", "Retry Pending", "Failed"}:
        raise ValueError("Publication outcome must be Published, Retry Pending, or Failed")
    if not detail.strip():
        raise ValueError("Publication attempt completion requires detail")
    with immediate_transaction(connection):
        attempt = connection.execute(
            """SELECT attempt.publication_id, attempt.sync_queue_item_id,
                      attempt.lease_owner, publication.package_hash,
                      queue.queue_status, queue.lease_owner
               FROM publication_attempt attempt
               JOIN publication ON publication.publication_id = attempt.publication_id
               JOIN sync_queue_item queue
                 ON queue.sync_queue_item_id = attempt.sync_queue_item_id
               WHERE attempt.publication_attempt_id = ?""",
            (publication_attempt_id,),
        ).fetchone()
        if attempt is None:
            raise ValueError("Publication attempt does not exist")
        if attempt[4] != "Leased" or attempt[2] != lease_owner or attempt[5] != lease_owner:
            raise ValueError("Publication attempt is not held by this worker")
        terminal = connection.execute(
            """SELECT 1 FROM publication_attempt_event
               WHERE publication_attempt_id = ?
                 AND attempt_status IN ('Retry Pending', 'Conflict', 'Failed', 'Published')""",
            (publication_attempt_id,),
        ).fetchone()
        if terminal:
            raise ValueError("Publication attempt is already terminal")
        if outcome == "Published":
            if observed_remote_hash != attempt[3]:
                outcome = "Failed"
                detail = "Remote package hash does not match the immutable publication manifest"
            else:
                connection.execute(
                    """INSERT INTO publication_attempt_event VALUES
                       (?, ?, 'Remote Verification', ?, ?, ?)""",
                    (uuid7(), publication_attempt_id, observed_remote_hash,
                     "Remote package hash matches immutable manifest", completed_at_utc),
                )
                connection.execute(
                    """INSERT INTO publication_status_event VALUES
                       (?, ?, 'Remote Verification', ?, ?)""",
                    (uuid7(), attempt[0],
                     "Remote package hash matches immutable manifest", completed_at_utc),
                )
        queue_status = "Complete" if outcome == "Published" else outcome
        connection.execute(
            """UPDATE sync_queue_item SET queue_status = ?,
               retry_count = retry_count + CASE WHEN ? = 'Retry Pending' THEN 1 ELSE 0 END,
               lease_owner = NULL, lease_expires_at_utc = NULL,
               last_error = CASE WHEN ? = 'Published' THEN NULL ELSE ? END
               WHERE sync_queue_item_id = ?""",
            (queue_status, outcome, outcome, detail.strip(), attempt[1]),
        )
        connection.execute(
            "INSERT INTO publication_attempt_event VALUES (?, ?, ?, ?, ?, ?)",
            (uuid7(), publication_attempt_id, outcome, observed_remote_hash,
             detail.strip(), completed_at_utc),
        )
        connection.execute(
            "INSERT INTO publication_status_event VALUES (?, ?, ?, ?, ?)",
            (uuid7(), attempt[0], outcome, detail.strip(), completed_at_utc),
        )
        append_audit_event(
            connection, audit,
            {"publication_attempt_id": publication_attempt_id,
             "publication_id": attempt[0], "outcome": outcome,
             "observed_remote_hash": observed_remote_hash, "detail": detail.strip()},
        )
    return outcome


def recover_expired_publication_leases(
    connection: sqlite3.Connection,
    *,
    observed_at_utc: str,
    audit: AuditContext,
) -> tuple[str, ...]:
    expired = connection.execute(
        """SELECT queue.sync_queue_item_id, queue.publication_id,
                  attempt.publication_attempt_id
           FROM sync_queue_item queue
           JOIN publication_attempt attempt
             ON attempt.sync_queue_item_id = queue.sync_queue_item_id
           WHERE queue.queue_status = 'Leased'
             AND queue.lease_expires_at_utc <= ?
             AND attempt.attempt_number = (
                 SELECT MAX(latest.attempt_number) FROM publication_attempt latest
                 WHERE latest.sync_queue_item_id = queue.sync_queue_item_id)
             AND NOT EXISTS (
                 SELECT 1 FROM publication_attempt_event terminal
                 WHERE terminal.publication_attempt_id = attempt.publication_attempt_id
                   AND terminal.attempt_status IN
                       ('Retry Pending', 'Conflict', 'Failed', 'Published'))
           ORDER BY queue.sync_queue_item_id""",
        (observed_at_utc,),
    ).fetchall()
    if not expired:
        return ()
    recovered: list[str] = []
    with immediate_transaction(connection):
        for queue_id, publication_id, attempt_id in expired:
            detail = "Worker lease expired before remote publication verification"
            connection.execute(
                """UPDATE sync_queue_item SET queue_status = 'Retry Pending',
                   retry_count = retry_count + 1, lease_owner = NULL,
                   lease_expires_at_utc = NULL, last_error = ?
                   WHERE sync_queue_item_id = ?""", (detail, queue_id),
            )
            connection.execute(
                """INSERT INTO publication_attempt_event VALUES
                   (?, ?, 'Retry Pending', NULL, ?, ?)""",
                (uuid7(), attempt_id, detail, observed_at_utc),
            )
            connection.execute(
                """INSERT INTO publication_status_event VALUES
                   (?, ?, 'Retry Pending', ?, ?)""",
                (uuid7(), publication_id, detail, observed_at_utc),
            )
            recovered.append(str(queue_id))
        append_audit_event(
            connection, audit,
            {"recovered_sync_queue_item_ids": recovered,
             "reason": "Expired publication worker leases",
             "observed_at_utc": observed_at_utc},
        )
    return tuple(recovered)


def verify_restore_candidate(
    checkpoint_path: Path,
    expected_manifest: dict[str, object] | None = None,
) -> list[str]:
    failures = validate_database(checkpoint_path)
    connection = connect(checkpoint_path, read_only=True)
    try:
        failures.extend(verify_audit_chain(connection))
        required = {"schema_migration", "commodity_database", "pbd_observation", "audit_event"}
        actual = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        missing = sorted(required - actual)
        failures.extend(f"missing_table:{name}" for name in missing)
        if not missing and expected_manifest is not None:
            reproduction_hash, counts = representative_reproduction(connection)
            identity = connection.execute(
                "SELECT database_id, commodity_id, schema_version FROM commodity_database"
            ).fetchone()
            audit_sequence = int(connection.execute(
                "SELECT COALESCE(MAX(recorded_sequence), 0) FROM audit_event"
            ).fetchone()[0])
            comparisons = {
                "database_id": str(identity[0]),
                "commodity_id": str(identity[1]),
                "schema_version": str(identity[2]),
                "database_hash": hash_file(checkpoint_path),
                "audit_event_sequence": audit_sequence,
                "record_counts": counts,
                "representative_reproduction_hash": reproduction_hash,
            }
            for key, actual_value in comparisons.items():
                if expected_manifest.get(key) != actual_value:
                    failures.append(
                        f"checkpoint_manifest_mismatch:{key}:"
                        f"expected={expected_manifest.get(key)!r}:actual={actual_value!r}"
                    )
    finally:
        connection.close()
    return failures


def run_automated_restore_test(
    connection: sqlite3.Connection,
    *,
    checkpoint: CheckpointResult,
    restored_path: Path,
    started_at_utc: str,
    completed_at_utc: str,
    audit: AuditContext,
) -> RestoreTestResult:
    """Restore an exact checkpoint copy and record terminal validation evidence."""
    if restored_path.exists():
        raise FileExistsError(f"Refusing to overwrite restore target: {restored_path}")
    stored = connection.execute(
        """SELECT checkpoint.database_hash, manifest.manifest_payload
           FROM local_checkpoint checkpoint
           JOIN local_checkpoint_verification_manifest manifest
             ON manifest.local_checkpoint_id = checkpoint.local_checkpoint_id
           WHERE checkpoint.local_checkpoint_id = ?""",
        (checkpoint.checkpoint_id,),
    ).fetchone()
    if stored is None:
        raise ValueError("Checkpoint record does not exist")
    failures: list[str] = []
    if hash_file(checkpoint.checkpoint_path) != stored[0]:
        failures.append("source_checkpoint_hash_mismatch")
    restored_path.parent.mkdir(parents=True, exist_ok=True)
    if not failures:
        shutil.copy2(checkpoint.checkpoint_path, restored_path)
        manifest = json.loads(str(stored[1]))
        manifest["registry_versions"] = [
            tuple(row) for row in manifest.get("registry_versions", [])
        ]
        failures.extend(verify_restore_candidate(restored_path, manifest))
    status = "Verified" if not failures else "Failed"
    restore_id = uuid7()
    result_text = canonical_json(failures) if failures else "ok"
    with immediate_transaction(connection):
        connection.execute(
            """INSERT INTO restore_event
               (restore_event_id, local_checkpoint_id, target_instance_id,
                restore_type, restore_status, integrity_result,
                audit_chain_result, schema_result, reproduction_result,
                started_at_utc, completed_at_utc)
               VALUES (?, ?, ?, 'Automated Test', ?, ?, ?, ?, ?, ?, ?)""",
            (restore_id, checkpoint.checkpoint_id, uuid7(), status,
             result_text, result_text, result_text, result_text,
             started_at_utc, completed_at_utc),
        )
        append_audit_event(connection, audit, {
            "restore_event_id": restore_id,
            "local_checkpoint_id": checkpoint.checkpoint_id,
            "restore_status": status,
            "restored_database_hash": None if failures else hash_file(restored_path),
            "failures": failures,
        })
    return RestoreTestResult(restore_id, status, restored_path, tuple(failures))
