from __future__ import annotations

import hashlib
import re
import sqlite3
from dataclasses import dataclass

from .audit import canonical_json
from .connection import immediate_transaction
from .ids import uuid7
from .events import standardized_event_name


@dataclass(frozen=True)
class SearchResult:
    search_document_id: str
    entity_type: str
    entity_id: str
    title: str
    subtitle: str | None
    commodity_id: str | None
    event_id: str | None
    supplier_id: str | None
    supplier_plant_id: str | None
    part_id: str | None
    status: str | None


def _text(*values: object) -> str:
    return " ".join(str(value).strip() for value in values if value is not None and str(value).strip())


def search_source_rows(
    connection: sqlite3.Connection, evidence_cutoff_utc: str
) -> list[tuple[object, ...]]:
    """Build one deterministic search document per authoritative entity."""
    documents: list[tuple[object, ...]] = []
    events = connection.execute(
        """SELECT event.event_id, event.commodity_id, event.readable_name,
                  event.buyer_code_id,
                  (SELECT status.event_status FROM sourcing_event_status_event status
                   WHERE status.event_id = event.event_id AND status.recorded_at_utc <= ?
                   ORDER BY status.recorded_at_utc DESC,
                            status.sourcing_event_status_event_id DESC LIMIT 1),
                  GROUP_CONCAT(COALESCE(
                    (SELECT correction.corrected_displayed_package_number
                     FROM source_package_number_correction correction
                     WHERE correction.source_package_id = package.source_package_id
                       AND correction.recorded_at_utc <= ?
                     ORDER BY correction.recorded_at_utc DESC,
                              correction.source_package_number_correction_id DESC LIMIT 1),
                    package.displayed_package_number), ' ')
           FROM sourcing_event event LEFT JOIN source_package package
             ON package.event_id = event.event_id AND package.recorded_at_utc <= ?
           WHERE event.created_at_utc <= ?
           GROUP BY event.event_id ORDER BY event.event_id""",
        (evidence_cutoff_utc, evidence_cutoff_utc, evidence_cutoff_utc, evidence_cutoff_utc),
    ).fetchall()
    for event_id, commodity_id, name, buyer, status, packages in events:
        title = standardized_event_name(
            connection, event_id=str(event_id), evidence_cutoff_utc=evidence_cutoff_utc
        )
        documents.append((f"event:{event_id}", "Sourcing Event", event_id, title,
                          packages, _text(name, buyer, status, packages), commodity_id,
                          event_id, None, None, None, status))

    for row in connection.execute(
        """SELECT package.source_package_id, package.event_id,
                  COALESCE((SELECT correction.corrected_displayed_package_number
                            FROM source_package_number_correction correction
                            WHERE correction.source_package_id = package.source_package_id
                              AND correction.recorded_at_utc <= ?
                            ORDER BY correction.recorded_at_utc DESC,
                                     correction.source_package_number_correction_id DESC LIMIT 1),
                           package.displayed_package_number),
                  COALESCE((SELECT correction.corrected_normalized_package_number
                            FROM source_package_number_correction correction
                            WHERE correction.source_package_id = package.source_package_id
                              AND correction.recorded_at_utc <= ?
                            ORDER BY correction.recorded_at_utc DESC,
                                     correction.source_package_number_correction_id DESC LIMIT 1),
                           package.normalized_package_number), package.package_role
           FROM source_package package WHERE package.recorded_at_utc <= ?
           ORDER BY package.source_package_id""",
        (evidence_cutoff_utc, evidence_cutoff_utc, evidence_cutoff_utc),
    ):
        package_id, event_id, displayed, normalized, role = row
        commodity_id = connection.execute(
            "SELECT commodity_id FROM sourcing_event WHERE event_id = ?", (event_id,)
        ).fetchone()[0]
        documents.append((f"package:{package_id}", "Source Package", package_id,
                          displayed, role, _text(displayed, normalized, role), commodity_id,
                          event_id, None, None, None, role))

    seen: set[tuple[str, str]] = set()
    def latest_observations(dimension: str) -> list[sqlite3.Row]:
        if dimension not in {"part_id", "supplier_id", "supplier_plant_id"}:
            raise ValueError("Unsupported search-document dimension")
        resolved_dimension = "effective_supplier_id" if dimension == "supplier_id" else dimension
        return connection.execute(
            f"""WITH resolved AS (
                    SELECT observation.*,
                           COALESCE((SELECT identity.confirmed_supplier_id
                                     FROM observation_supplier_identity_confirmation identity
                                     WHERE identity.observation_id = observation.observation_id
                                       AND identity.recorded_at_utc <= ?
                                     ORDER BY identity.recorded_at_utc DESC,
                                              identity.supplier_identity_confirmation_id DESC LIMIT 1),
                                    observation.supplier_id) AS effective_supplier_id
                    FROM pbd_observation observation)
                SELECT context_id, effective_supplier_id, supplier_plant_id, part_id,
                       submitted_supplier_name, submitted_part_number,
                       submitted_part_description, structure_category
                FROM resolved observation
                WHERE observation.{resolved_dimension} IS NOT NULL
                  AND recorded_at_utc <= ? AND NOT EXISTS (
                    SELECT 1 FROM resolved newer
                    WHERE newer.{resolved_dimension} = observation.{resolved_dimension}
                      AND newer.recorded_at_utc <= ?
                      AND (newer.recorded_at_utc > observation.recorded_at_utc OR
                           (newer.recorded_at_utc = observation.recorded_at_utc AND
                            newer.observation_id > observation.observation_id)))
                ORDER BY observation.{resolved_dimension}""",
            (evidence_cutoff_utc, evidence_cutoff_utc, evidence_cutoff_utc),
        ).fetchall()

    for row in latest_observations("part_id"):
        event_id, supplier_id, plant_id, part_id, supplier_name, part_number, description, structure = row
        commodity = connection.execute(
            "SELECT commodity_id FROM sourcing_event WHERE event_id = ?", (event_id,)
        ).fetchone() if event_id else None
        commodity_id = commodity[0] if commodity else None
        documents.append((f"part:{part_id}", "Part", part_id, part_number,
                          description, _text(part_number, description, structure),
                          commodity_id, event_id, None, None, part_id, structure))
    for row in latest_observations("supplier_id"):
        event_id, supplier_id, plant_id, _, supplier_name, *_ = row
        commodity = connection.execute(
            "SELECT commodity_id FROM sourcing_event WHERE event_id = ?", (event_id,)
        ).fetchone() if event_id else None
        documents.append((f"supplier:{supplier_id}", "Supplier", supplier_id,
                          supplier_name, None, _text(supplier_name, supplier_id),
                          commodity[0] if commodity else None, event_id,
                          supplier_id, None, None, None))
    for row in latest_observations("supplier_plant_id"):
        event_id, supplier_id, plant_id, _, supplier_name, *_ = row
        commodity = connection.execute(
            "SELECT commodity_id FROM sourcing_event WHERE event_id = ?", (event_id,)
        ).fetchone() if event_id else None
        documents.append((f"plant:{plant_id}", "Supplier Plant", plant_id,
                          str(plant_id), supplier_name, _text(plant_id, supplier_name),
                          commodity[0] if commodity else None, event_id,
                          supplier_id, plant_id, None, None))

    for row in connection.execute(
        """SELECT action.buyer_action_id, action.event_id, action.supplier_id,
                  action.part_id, action.issue_type, version.action_status,
                  version.required_supplier_action, version.working_note
           FROM buyer_action action JOIN buyer_action_version version
             ON version.buyer_action_id = action.buyer_action_id
           WHERE action.created_at_utc <= ? AND version.recorded_at_utc <= ?
             AND NOT EXISTS (SELECT 1 FROM buyer_action_version newer
                 WHERE newer.buyer_action_id = action.buyer_action_id
                   AND newer.recorded_at_utc <= ?
                   AND (newer.recorded_at_utc > version.recorded_at_utc OR
                        (newer.recorded_at_utc = version.recorded_at_utc AND
                         newer.buyer_action_version_id > version.buyer_action_version_id)))
           ORDER BY action.buyer_action_id""",
        (evidence_cutoff_utc, evidence_cutoff_utc, evidence_cutoff_utc),
    ):
        action_id, event_id, supplier_id, part_id, issue, status, required, note = row
        commodity_id = connection.execute(
            "SELECT commodity_id FROM sourcing_event WHERE event_id = ?", (event_id,)
        ).fetchone()[0]
        documents.append((f"action:{action_id}", "Buyer Action", action_id, issue,
                          required, _text(issue, required, note, status), commodity_id,
                          event_id, supplier_id, None, part_id, status))

    for row in connection.execute(
        """SELECT item.knowledge_item_id, item.knowledge_type,
                  version.structured_payload, version.knowledge_level,
                  scope.commodity_id, scope.supplier_id, scope.supplier_plant_id,
                  scope.part_id
           FROM knowledge_item item JOIN knowledge_version version
             ON version.knowledge_item_id = item.knowledge_item_id
           LEFT JOIN knowledge_scope scope
             ON scope.knowledge_version_id = version.knowledge_version_id
           WHERE version.status = 'Active' AND version.recorded_from_utc <= ?
             AND (version.recorded_to_utc IS NULL OR version.recorded_to_utc > ?)
           ORDER BY item.knowledge_item_id, scope.scope_signature""",
        (evidence_cutoff_utc, evidence_cutoff_utc),
    ):
        item_id, knowledge_type, payload, level, commodity_id, supplier_id, plant_id, part_id = row
        key = ("Knowledge", str(item_id))
        if key in seen:
            continue
        seen.add(key)
        documents.append((f"knowledge:{item_id}", "Knowledge", item_id,
                          knowledge_type, level, _text(knowledge_type, payload, level),
                          commodity_id, None, supplier_id, plant_id, part_id, level))
    return sorted(documents, key=lambda row: str(row[0]))


def build_search_projection(connection: sqlite3.Connection, *, evidence_cutoff_utc: str) -> str:
    generation_id = uuid7()
    rows = search_source_rows(connection, evidence_cutoff_utc)
    manifest_hash = hashlib.sha256(canonical_json(rows).encode("utf-8")).hexdigest()
    with immediate_transaction(connection):
        connection.execute(
            "INSERT INTO search_generation_manifest VALUES (?, ?, ?, ?, 'Complete', ?)",
            (generation_id, evidence_cutoff_utc, manifest_hash, len(rows), evidence_cutoff_utc),
        )
        connection.executemany(
            """INSERT INTO search_document_projection VALUES
               (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [(generation_id, *row, evidence_cutoff_utc, manifest_hash) for row in rows],
        )
        connection.executemany(
            "INSERT INTO search_document_fts VALUES (?, ?, ?)",
            [(generation_id, row[0], row[5]) for row in rows],
        )
    return generation_id


def search_documents(
    connection: sqlite3.Connection, *, generation_id: str, query: str = "",
    entity_type: str | None = None, commodity_id: str | None = None,
    event_id: str | None = None, supplier_id: str | None = None,
    status: str | None = None, after_document_id: str | None = None, limit: int = 50,
) -> tuple[SearchResult, ...]:
    if limit < 1 or limit > 500:
        raise ValueError("Search limit must be between 1 and 500")
    clauses = ["document.search_generation_id = ?"]
    values: list[object] = [generation_id]
    tokens = re.findall(r"[\w-]+", query, flags=re.UNICODE)
    join = ""
    if tokens:
        join = "JOIN search_document_fts fts ON fts.search_generation_id = document.search_generation_id AND fts.search_document_id = document.search_document_id"
        clauses.append("fts.searchable_text MATCH ?")
        values.append(" AND ".join(f'"{token.replace(chr(34), chr(34) * 2)}"' for token in tokens))
    for column, value in (("entity_type", entity_type), ("commodity_id", commodity_id),
                          ("event_id", event_id), ("supplier_id", supplier_id),
                          ("status", status)):
        if value is not None:
            clauses.append(f"document.{column} = ?")
            values.append(value)
    if after_document_id is not None:
        clauses.append("document.search_document_id > ?")
        values.append(after_document_id)
    values.append(limit)
    rows = connection.execute(
        f"""SELECT document.search_document_id, document.entity_type,
                   document.entity_id, document.title, document.subtitle,
                   document.commodity_id, document.event_id, document.supplier_id,
                   document.supplier_plant_id, document.part_id, document.status
            FROM search_document_projection document {join}
            WHERE {' AND '.join(clauses)}
            ORDER BY document.search_document_id LIMIT ?""", values,
    ).fetchall()
    return tuple(SearchResult(*map(lambda value: None if value is None else str(value), row)) for row in rows)
