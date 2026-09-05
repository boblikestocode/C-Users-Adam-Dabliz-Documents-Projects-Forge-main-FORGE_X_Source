from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .audit import AuditContext, append_audit_event, canonical_json
from .connection import immediate_transaction
from .ids import uuid7
from .events import standardized_event_name


@dataclass(frozen=True)
class OutputContract:
    finalized_snapshot_id: str
    evidence_manifest_hash: str
    calculation_output_manifest_hash: str
    presentation_rule_version_ids: tuple[str, ...]
    results: tuple[dict[str, object], ...]
    actions: tuple[dict[str, object], ...]
    contract_hash: str


@dataclass(frozen=True)
class GeneratedOutput:
    generated_output_artifact_id: str
    content_hash_sha256: str
    size_bytes: int
    output_contract_hash: str


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def required_output_filename_prefix(
    connection: sqlite3.Connection, *, finalized_snapshot_id: str,
) -> str:
    row = connection.execute(
        """SELECT analysis.event_id, snapshot.finalized_at_utc
           FROM finalized_snapshot snapshot JOIN analysis analysis
             ON analysis.analysis_id = snapshot.analysis_id
           WHERE snapshot.finalized_snapshot_id = ?""", (finalized_snapshot_id,),
    ).fetchone()
    if row is None:
        raise ValueError("Finalized snapshot does not exist")
    name = standardized_event_name(
        connection, event_id=str(row[0]), evidence_cutoff_utc=str(row[1])
    )
    return "".join("_" if character in '<>:"/\\|?*' else character for character in name).strip()


def build_output_contract(
    connection: sqlite3.Connection, *, finalized_snapshot_id: str
) -> OutputContract:
    snapshot = connection.execute(
        """SELECT snapshot.evidence_manifest_hash, status.output_manifest_hash
           FROM finalized_snapshot snapshot
           JOIN calculation_run run
             ON run.calculation_run_id = snapshot.calculation_run_id
           JOIN v_current_calculation_run_status status
             ON status.calculation_run_id = run.calculation_run_id
           WHERE snapshot.finalized_snapshot_id = ? AND status.run_status = 'Completed'""",
        (finalized_snapshot_id,),
    ).fetchone()
    if snapshot is None:
        raise ValueError("Output contract requires a finalized snapshot and completed calculation")
    result_rows = connection.execute(
        """SELECT result_code, supplier_id, part_id, program_year, category_code,
                  decimal_coefficient, decimal_scale, text_value,
                  normalized_unit_id, currency_id, presentation_rule_version_id
           FROM snapshot_result WHERE finalized_snapshot_id = ?
           ORDER BY result_code, supplier_id, part_id, program_year, category_code,
                    snapshot_result_id""",
        (finalized_snapshot_id,),
    ).fetchall()
    if not result_rows:
        raise ValueError("Output contract requires frozen snapshot results")
    result_names = (
        "result_code", "supplier_id", "part_id", "program_year", "category_code",
        "decimal_coefficient", "decimal_scale", "text_value",
        "normalized_unit_id", "currency_id", "presentation_rule_version_id",
    )
    results = tuple(dict(zip(result_names, tuple(row))) for row in result_rows)
    action_rows = connection.execute(
        """SELECT buyer_action_id, buyer_action_version_id, frozen_status, frozen_payload
           FROM snapshot_action WHERE finalized_snapshot_id = ?
           ORDER BY buyer_action_id""",
        (finalized_snapshot_id,),
    ).fetchall()
    action_names = (
        "buyer_action_id", "buyer_action_version_id", "frozen_status", "frozen_payload"
    )
    actions = tuple(dict(zip(action_names, tuple(row))) for row in action_rows)
    rule_ids = tuple(sorted({str(row[10]) for row in result_rows}))
    payload = {
        "finalized_snapshot_id": finalized_snapshot_id,
        "evidence_manifest_hash": str(snapshot[0]),
        "calculation_output_manifest_hash": str(snapshot[1]),
        "presentation_rule_version_ids": rule_ids,
        "results": results,
        "actions": actions,
    }
    contract_hash = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
    return OutputContract(
        finalized_snapshot_id, str(snapshot[0]), str(snapshot[1]), rule_ids,
        results, actions, contract_hash,
    )


def register_generated_output(
    connection: sqlite3.Connection,
    *,
    finalized_snapshot_id: str,
    artifact_type: str,
    output_path: Path,
    rendered_contract_hash: str,
    renderer_version: str,
    created_by_user_id: str,
    created_at_utc: str,
    audit: AuditContext,
) -> GeneratedOutput:
    if not output_path.is_file():
        raise FileNotFoundError(output_path)
    if output_path.stat().st_size <= 0:
        raise ValueError("Generated output cannot be empty")
    required_prefix = required_output_filename_prefix(
        connection, finalized_snapshot_id=finalized_snapshot_id
    )
    if not output_path.name.startswith(required_prefix):
        raise ValueError(f"Generated output filename must begin with {required_prefix}")
    contract = build_output_contract(
        connection, finalized_snapshot_id=finalized_snapshot_id
    )
    if rendered_contract_hash != contract.contract_hash:
        raise ValueError("Rendered output contract does not match the finalized snapshot")
    content_hash = _hash_file(output_path)
    artifact_id = uuid7()
    with immediate_transaction(connection):
        connection.execute(
            """INSERT INTO generated_output_artifact
               (generated_output_artifact_id, finalized_snapshot_id, artifact_type,
                relative_filename, content_hash_sha256, size_bytes,
                output_contract_hash, evidence_manifest_hash,
                calculation_output_manifest_hash, renderer_version,
                created_by_user_id, created_at_utc)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                artifact_id, finalized_snapshot_id, artifact_type, output_path.name,
                content_hash, output_path.stat().st_size, contract.contract_hash,
                contract.evidence_manifest_hash,
                contract.calculation_output_manifest_hash, renderer_version,
                created_by_user_id, created_at_utc,
            ),
        )
        connection.execute(
            """INSERT INTO generated_output_validation_event
               (generated_output_validation_event_id, generated_output_artifact_id,
                validation_status, observed_content_hash_sha256,
                validation_detail, validated_at_utc)
               VALUES (?, ?, 'Verified', ?, ?, ?)""",
            (
                uuid7(), artifact_id, content_hash,
                "File hash and finalized output contract verified at registration",
                created_at_utc,
            ),
        )
        append_audit_event(
            connection, audit,
            {"generated_output_artifact_id": artifact_id,
             "finalized_snapshot_id": finalized_snapshot_id,
             "artifact_type": artifact_type, "content_hash_sha256": content_hash,
             "output_contract_hash": contract.contract_hash},
        )
    return GeneratedOutput(
        artifact_id, content_hash, output_path.stat().st_size, contract.contract_hash
    )


def validate_generated_output(
    connection: sqlite3.Connection,
    *,
    generated_output_artifact_id: str,
    output_path: Path,
    validated_at_utc: str,
    audit: AuditContext,
) -> bool:
    stored = connection.execute(
        """SELECT finalized_snapshot_id, content_hash_sha256, output_contract_hash
           FROM generated_output_artifact
           WHERE generated_output_artifact_id = ?""",
        (generated_output_artifact_id,),
    ).fetchone()
    if stored is None:
        raise ValueError("Generated output artifact does not exist")
    observed_hash = _hash_file(output_path) if output_path.is_file() else "0" * 64
    contract = build_output_contract(connection, finalized_snapshot_id=str(stored[0]))
    verified = observed_hash == stored[1] and contract.contract_hash == stored[2]
    detail = (
        "File hash and finalized output contract remain valid"
        if verified else "File hash or finalized output contract validation failed"
    )
    with immediate_transaction(connection):
        connection.execute(
            """INSERT INTO generated_output_validation_event
               (generated_output_validation_event_id, generated_output_artifact_id,
                validation_status, observed_content_hash_sha256,
                validation_detail, validated_at_utc)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (uuid7(), generated_output_artifact_id,
             "Verified" if verified else "Failed", observed_hash, detail,
             validated_at_utc),
        )
        append_audit_event(
            connection, audit,
            {"generated_output_artifact_id": generated_output_artifact_id,
             "validation_status": "Verified" if verified else "Failed",
             "observed_content_hash_sha256": observed_hash},
        )
    return verified
