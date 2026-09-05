from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from typing import Callable

from .audit import AuditContext, append_audit_event, canonical_json
from .connection import immediate_transaction
from .ids import uuid7


SignatureVerifier = Callable[[bytes, str], bool]


@dataclass(frozen=True)
class RegistryCacheEntity:
    entity_type: str
    entity_id: str
    payload: dict[str, object]


def registry_cache_manifest(
    registry_publication_id: str,
    registry_version: str,
    entities: list[tuple[str, str, str, str]],
) -> tuple[bytes, str]:
    payload = {
        "registry_publication_id": registry_publication_id,
        "registry_version": registry_version,
        "entities": entities,
    }
    encoded = canonical_json(payload).encode("utf-8")
    return encoded, hashlib.sha256(encoded).hexdigest()


def install_registry_cache(
    connection: sqlite3.Connection,
    *,
    registry_publication_id: str,
    registry_version: str,
    entities: tuple[RegistryCacheEntity, ...],
    digital_signature: str,
    signature_verifier: SignatureVerifier,
    imported_at_utc: str,
    expires_at_utc: str | None,
    audit: AuditContext,
) -> str:
    if not registry_publication_id.strip() or not registry_version.strip():
        raise ValueError("Registry publication identity and version are required")
    if not entities:
        raise ValueError("Registry cache publication must contain entities")
    keys = [(item.entity_type, item.entity_id) for item in entities]
    if len(keys) != len(set(keys)):
        raise ValueError("Registry cache entities must be unique by type and ID")
    rows: list[tuple[str, str, str, str]] = []
    for entity in sorted(entities, key=lambda item: (item.entity_type, item.entity_id)):
        payload = canonical_json(entity.payload)
        rows.append((entity.entity_type, entity.entity_id, payload,
                     hashlib.sha256(payload.encode("utf-8")).hexdigest()))
    manifest_bytes, publication_hash = registry_cache_manifest(
        registry_publication_id, registry_version, rows
    )
    verified = bool(digital_signature) and signature_verifier(
        manifest_bytes, digital_signature
    )
    generation_id = uuid7()
    with immediate_transaction(connection):
        connection.execute(
            """INSERT INTO registry_cache_generation
               (cache_generation_id, registry_publication_id, registry_version,
                publication_hash, signature_status, imported_at_utc,
                expires_at_utc, activation_status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (generation_id, registry_publication_id, registry_version,
             publication_hash, "Verified" if verified else "Invalid",
             imported_at_utc, expires_at_utc,
             "Active" if verified else "Rejected"),
        )
        connection.executemany(
            """INSERT INTO registry_entity_cache
               (cache_row_id, cache_generation_id, entity_type, entity_id,
                entity_payload, content_hash) VALUES (?, ?, ?, ?, ?, ?)""",
            [(uuid7(), generation_id, *row) for row in rows],
        )
        if verified:
            active = connection.execute(
                "SELECT cache_generation_id FROM v_current_active_registry_cache"
            ).fetchall()
            for prior in active:
                connection.execute(
                    """INSERT INTO registry_cache_activation_event VALUES
                       (?, ?, 'Superseded', 'New verified registry publication activated', ?)""",
                    (uuid7(), prior[0], imported_at_utc),
                )
        connection.execute(
            """INSERT INTO registry_cache_activation_event VALUES (?, ?, ?, ?, ?)""",
            (uuid7(), generation_id, "Active" if verified else "Rejected",
             "Signature verified" if verified else "Signature validation failed",
             imported_at_utc),
        )
        append_audit_event(
            connection, audit,
            {"cache_generation_id": generation_id,
             "registry_publication_id": registry_publication_id,
             "registry_version": registry_version,
             "publication_hash": publication_hash,
             "signature_status": "Verified" if verified else "Invalid",
             "entity_count": len(rows)},
        )
    return generation_id
