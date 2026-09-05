from __future__ import annotations

import argparse
import hashlib
import sqlite3
import uuid
from dataclasses import dataclass
from pathlib import Path

from database.migration_runner import apply_migrations
from database.services.audit import canonical_json
from database.services.registry_cache import registry_cache_manifest
from database.services.summary_projections import build_event_summary_projection


NAMESPACE = uuid.UUID("563f890f-0d0b-4d25-b28b-e586634f0048")
UTC = "2026-01-15T12:00:00Z"


def uid(*parts: object) -> str:
    return str(uuid.uuid5(NAMESPACE, "|".join(map(str, parts))))


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class SyntheticProfile:
    suppliers: int
    parts: int
    rounds: int
    operations_per_observation: int
    materials_per_observation: int

    @property
    def observations(self) -> int:
        return self.suppliers * self.parts * self.rounds

    @property
    def detail_rows(self) -> int:
        return self.observations * (
            self.operations_per_observation + self.materials_per_observation
        )


PROFILES = {
    "smoke": SyntheticProfile(3, 20, 3, 4, 2),
    "medium": SyntheticProfile(10, 200, 4, 12, 4),
    "acceptance": SyntheticProfile(50, 1_000, 5, 30, 10),
}


def insert_foundation(connection: sqlite3.Connection) -> dict[str, str]:
    ids = {
        "database": uid("database"),
        "commodity": uid("commodity"),
        "engine": uid("engine"),
        "extraction_rule": uid("rule", "extraction"),
        "calculation_rule": uid("rule", "calculation"),
        "event": uid("event"),
        "package": uid("package"),
        "scope": uid("scope"),
        "session": uid("session"),
        "transaction": uid("transaction"),
        "registry_cache": uid("registry-cache"),
    }
    connection.execute(
        "INSERT INTO commodity_database VALUES (?, ?, ?, '0.2', ?)",
        (ids["database"], ids["commodity"], UTC, uid("nonce")),
    )
    registry_payload = canonical_json({
        "commodity_id": ids["commodity"], "commodity_code": "SYNTHETIC",
        "locked_name": "Synthetic Commodity", "status": "Active",
    })
    registry_entity_hash = sha256(registry_payload)
    _, registry_publication_hash = registry_cache_manifest(
        "synthetic-registry-publication", "synthetic-registry-v1",
        [("Commodity", ids["commodity"], registry_payload, registry_entity_hash)],
    )
    connection.execute(
        """INSERT INTO registry_cache_generation VALUES
           (?, 'synthetic-registry-publication', 'synthetic-registry-v1', ?,
            'Verified', ?, '2099-12-31T23:59:59Z', 'Active')""",
        (ids["registry_cache"], registry_publication_hash, UTC),
    )
    connection.execute(
        """INSERT INTO registry_entity_cache VALUES
           (?, ?, 'Commodity', ?, ?, ?)""",
        (uid("registry-cache-row"), ids["registry_cache"], ids["commodity"],
         registry_payload, registry_entity_hash),
    )
    connection.execute(
        """INSERT INTO registry_cache_activation_event VALUES
           (?, ?, 'Active', 'Synthetic verified registry cache', ?)""",
        (uid("registry-cache-activation"), ids["registry_cache"], UTC),
    )
    connection.execute(
        "INSERT INTO engine_version VALUES (?, 'synthetic-0.1', 'validation', ?, ?)",
        (ids["engine"], sha256("synthetic-engine"), UTC),
    )
    for rule_id, domain in (
        (ids["extraction_rule"], "extraction"),
        (ids["calculation_rule"], "calculation"),
    ):
        connection.execute(
            """INSERT INTO rule_version VALUES
               (?, ?, '1.0.0', ?, ?, 'synthetic', ?)""",
            (rule_id, domain, sha256(domain), UTC, UTC),
        )
    connection.execute(
        """INSERT INTO sourcing_event VALUES
           (?, ?, 'Synthetic Sourcing Event', 'BUYER-001', 'synthetic-user', 'Active', ?)""",
        (ids["event"], ids["commodity"], UTC),
    )
    connection.execute(
        """INSERT INTO sourcing_event_status_event VALUES
           (?, ?, 'Active', 'Synthetic event fixture', 'synthetic-user', NULL, ?)""",
        (uid("event-status"), ids["event"], UTC),
    )
    connection.execute(
        """INSERT INTO source_package VALUES
           (?, ?, 'SP-SYNTHETIC-001', 'SP-SYNTHETIC-001', 'Primary', ?)""",
        (ids["package"], ids["event"], UTC),
    )
    connection.execute(
        """INSERT INTO scope_version VALUES
           (?, ?, 1, NULL, 'Synthetic initial scope', 'synthetic-user', ?)""",
        (ids["scope"], ids["package"], UTC),
    )
    connection.execute(
        """INSERT INTO scope_activation VALUES
           (?, ?, ?, 'Activate', 'synthetic-user', 'Synthetic baseline', ?)""",
        (uid("scope-activation"), ids["package"], ids["scope"], UTC),
    )
    connection.execute(
        """INSERT INTO import_session
           (import_session_id, import_context_type, context_id,
            initiated_by_user_id, engine_version_id, status, started_at_utc)
           VALUES (?, 'Sourcing Event', ?, 'synthetic-user', ?, 'Staging', ?)""",
        (ids["session"], ids["event"], ids["engine"], UTC),
    )
    connection.execute(
        """INSERT INTO import_transaction
           (import_transaction_id, import_session_id, transaction_sequence,
            status, started_at_utc)
           VALUES (?, ?, 1, 'Started', ?)""",
        (ids["transaction"], ids["session"], UTC),
    )
    connection.execute(
        """INSERT INTO import_session_status_event VALUES
           (?, ?, 'Staging', 'Synthetic fixture at staging', NULL, ?)""",
        (uid("session-status-staging"), ids["session"], UTC),
    )
    connection.execute(
        """INSERT INTO import_transaction_status_event VALUES
           (?, ?, 'Started', ?, 'Synthetic transaction started', NULL, ?)""",
        (uid("transaction-status-started"), ids["transaction"],
         canonical_json({"discovered": 0, "committed": 0, "duplicate": 0,
                         "blocked": 0, "ignored": 0, "failed": 0}), UTC),
    )
    return ids


def insert_event_parts(
    connection: sqlite3.Connection, ids: dict[str, str], profile: SyntheticProfile
) -> list[str]:
    event_part_ids = [uid("event-part", index) for index in range(profile.parts)]
    connection.executemany(
        """INSERT INTO event_part
           (event_part_id, scope_version_id, part_id, submitted_part_number,
            submitted_description, vehicle_position, scope_action, recorded_at_utc)
           VALUES (?, ?, ?, ?, ?, ?, 'Added', ?)""",
        [
            (
                event_part_ids[index],
                ids["scope"],
                uid("part", index),
                f"PART-{index:06d}",
                f"Synthetic Component {index:06d}",
                f"POS-{index % 8}",
                UTC,
            )
            for index in range(profile.parts)
        ],
    )
    return event_part_ids


def populate(database_path: Path, profile: SyntheticProfile) -> dict[str, int]:
    if database_path.exists():
        raise FileExistsError(f"Refusing to overwrite existing database: {database_path}")
    apply_migrations(database_path)
    connection = sqlite3.connect(database_path)
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        ids = insert_foundation(connection)
        event_part_ids = insert_event_parts(connection, ids, profile)
        observation_count = 0
        operation_count = 0
        material_count = 0

        for supplier_index in range(profile.suppliers):
            supplier_id = uid("supplier", supplier_index)
            plant_id = uid("plant", supplier_index)
            for round_number in range(1, profile.rounds + 1):
                round_id = uid("round", supplier_index, round_number)
                batch_id = uid("batch", supplier_index, round_number)
                workbook_id = uid("workbook", supplier_index, round_number)
                discovery_id = uid("discovery", supplier_index, round_number)
                pending_disposition_id = uid("discovery-pending", supplier_index, round_number)
                connection.execute(
                    """INSERT INTO supplier_quote_round VALUES
                       (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        round_id, ids["event"], supplier_id, round_number,
                        f"Synthetic Round {round_number}", f"2026-0{round_number}-15", UTC,
                    ),
                )
                connection.execute(
                    """INSERT INTO round_batch VALUES
                       (?, ?, ?, 'synthetic-user', ?)""",
                    (batch_id, round_id, ids["transaction"], UTC),
                )
                connection.execute(
                    """INSERT INTO import_discovery_item
                       (import_discovery_item_id, import_transaction_id,
                        discovery_ordinal, submitted_filename, source_locator,
                        expected_file_hash_sha256, discovered_at_utc)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        discovery_id, ids["transaction"],
                        supplier_index * profile.rounds + round_number - 1,
                        f"supplier-{supplier_index:03d}-round-{round_number}.xlsx",
                        f"synthetic://supplier/{supplier_index}/round/{round_number}",
                        sha256(f"workbook-{supplier_index}-{round_number}"), UTC,
                    ),
                )
                connection.execute(
                    """INSERT INTO import_discovery_disposition
                       (import_discovery_disposition_id, import_discovery_item_id,
                        disposition_status, recorded_at_utc)
                       VALUES (?, ?, 'Pending', ?)""",
                    (pending_disposition_id, discovery_id, UTC),
                )
                connection.execute(
                    """INSERT INTO source_workbook VALUES
                       (?, ?, ?, ?, ?, ?, NULL, NULL, ?)""",
                    (
                        workbook_id,
                        ids["transaction"],
                        f"supplier-{supplier_index:03d}-round-{round_number}.xlsx",
                        f"synthetic://supplier/{supplier_index}/round/{round_number}",
                        profile.parts * 4096,
                        sha256(f"workbook-{supplier_index}-{round_number}"),
                        UTC,
                    ),
                )
                connection.execute(
                    """INSERT INTO fingerprint
                       (fingerprint_id, entity_type, entity_id, purpose,
                        algorithm, digest, recorded_at_utc)
                       VALUES (?, 'Source Workbook', ?, 'Exact File Duplicate',
                               'SHA-256', ?, ?)""",
                    (
                        uid("workbook-fingerprint", supplier_index, round_number),
                        workbook_id,
                        sha256(f"workbook-{supplier_index}-{round_number}"), UTC,
                    ),
                )
                connection.execute(
                    """INSERT INTO source_workbook_availability_event VALUES
                       (?, ?, 'Unknown', NULL, 'Synthetic Fixture', 'synthetic-user',
                        'External source has not been re-verified', NULL, ?)""",
                    (uid("workbook-availability", supplier_index, round_number), workbook_id, UTC),
                )
                connection.execute(
                    """INSERT INTO import_discovery_disposition
                       (import_discovery_disposition_id, import_discovery_item_id,
                        disposition_status, workbook_id,
                        supersedes_disposition_id, recorded_at_utc)
                       VALUES (?, ?, 'Registered', ?, ?, ?)""",
                    (
                        uid("discovery-registered", supplier_index, round_number),
                        discovery_id, workbook_id, pending_disposition_id, UTC,
                    ),
                )

                for part_index, event_part_id in enumerate(event_part_ids):
                    worksheet_id = uid("worksheet", supplier_index, round_number, part_index)
                    occurrence_id = uid("occurrence", supplier_index, round_number, part_index)
                    staged_id = uid("staged", supplier_index, round_number, part_index)
                    observation_id = uid("observation", supplier_index, round_number, part_index)
                    price_source_id = uid("source-price", supplier_index, round_number, part_index)
                    price_datum_id = uid("price-datum", supplier_index, round_number, part_index)
                    price_1e4 = 100_000 + supplier_index * 750 + part_index * 11 - round_number * 125
                    price_lexeme = f"{price_1e4 / 10_000:.4f}"

                    connection.execute(
                        """INSERT INTO source_worksheet VALUES
                           (?, ?, ?, ?, 'Visible', 'A1:Z100', ?, ?)""",
                        (
                            worksheet_id, workbook_id, part_index,
                            f"PART-{part_index:06d}",
                            sha256(f"sheet-{supplier_index}-{round_number}-{part_index}"), UTC,
                        ),
                    )
                    connection.execute(
                        """INSERT INTO source_occurrence VALUES
                           (?, ?, 'A1:Z100', ?, ?, 'PBD', 'Committed', NULL, ?)""",
                        (
                            occurrence_id, worksheet_id,
                            sha256(f"logical-{supplier_index}-{round_number}-{part_index}"),
                            ids["extraction_rule"], UTC,
                        ),
                    )
                    connection.execute(
                        """INSERT INTO source_occurrence_status_event VALUES
                           (?, ?, 'Committed', 'Synthetic committed evidence', NULL, ?)""",
                        (uid("occurrence-status", supplier_index, round_number, part_index),
                         occurrence_id, UTC),
                    )
                    connection.execute(
                        """INSERT INTO staged_observation
                           (staged_observation_id, occurrence_id,
                            provisional_supplier_code, provisional_supplier_name,
                            provisional_part_number, import_context_type,
                            import_context_id, status, recorded_at_utc)
                           VALUES (?, ?, ?, ?, ?, 'Sourcing Event', ?, 'Committed', ?)""",
                        (
                            staged_id, occurrence_id, f"SUP-{supplier_index:03d}",
                            f"Synthetic Supplier {supplier_index:03d}",
                            f"PART-{part_index:06d}", ids["event"], UTC,
                        ),
                    )
                    connection.execute(
                        """INSERT INTO staged_observation_status_event VALUES
                           (?, ?, 'Committed', 0, 'Synthetic committed staging', NULL, ?)""",
                        (uid("staged-status", supplier_index, round_number, part_index),
                         staged_id, UTC),
                    )
                    connection.execute(
                        """INSERT INTO source_datum VALUES
                           (?, ?, 'B12', ?, NULL, ?, ?, ?)""",
                        (
                            price_source_id, worksheet_id, price_lexeme, price_lexeme,
                            sha256(f"{worksheet_id}|B12|{price_lexeme}|None"), UTC,
                        ),
                    )
                    connection.execute(
                        """INSERT INTO pbd_observation
                           (observation_id, staged_observation_id, occurrence_id,
                            observation_context, context_id, supplier_id,
                            supplier_plant_id, part_id, submitted_supplier_name,
                            submitted_part_number, submitted_part_description,
                            economic_date, economic_date_precision,
                            structure_category, recorded_at_utc)
                           VALUES (?, ?, ?, 'Sourcing Event', ?, ?, ?, ?, ?, ?, ?, ?,
                                   'Day', 'Detailed and Reconciled', ?)""",
                        (
                            observation_id, staged_id, occurrence_id, ids["event"], supplier_id,
                            plant_id, uid("part", part_index),
                            f"Synthetic Supplier {supplier_index:03d}",
                            f"PART-{part_index:06d}", f"Synthetic Component {part_index:06d}",
                            f"2026-0{round_number}-15", UTC,
                        ),
                    )
                    connection.execute(
                        """INSERT INTO submitted_datum VALUES
                           (?, ?, 'PIECE_PRICE', ?, ?, ?, 4, ?, NULL, NULL, NULL,
                            'USD/PART', 'USD', 'Eligible', ?)""",
                        (
                            price_datum_id, observation_id, price_source_id, price_lexeme,
                            str(price_1e4), price_1e4, UTC,
                        ),
                    )
                    connection.execute(
                        """INSERT INTO round_observation VALUES
                           (?, ?, ?, ?, ?, 'Submitted', ?)""",
                        (
                            uid("round-observation", supplier_index, round_number, part_index),
                            round_id, batch_id, observation_id, event_part_id, UTC,
                        ),
                    )
                    connection.execute(
                        """INSERT INTO supplier_activity VALUES
                           (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            uid("activity", supplier_index, round_number, part_index),
                            supplier_id, plant_id, ids["event"], round_id, event_part_id,
                            "Addition" if round_number == 1 else "Price Change",
                            None if round_number == 1 else "PBD Observation",
                            None if round_number == 1 else uid("observation", supplier_index, round_number - 1, part_index),
                            "PBD Observation", observation_id,
                            f"2026-0{round_number}-15T12:00:00Z", UTC,
                        ),
                    )

                    connection.executemany(
                        """INSERT INTO operation_line
                           (operation_line_id, observation_id, operation_sequence,
                            submitted_operation, recorded_at_utc)
                           VALUES (?, ?, ?, ?, ?)""",
                        [
                            (
                                uid("operation", supplier_index, round_number, part_index, op),
                                observation_id, op, f"Synthetic Operation {op:02d}", UTC,
                            )
                            for op in range(profile.operations_per_observation)
                        ],
                    )
                    connection.executemany(
                        """INSERT INTO material_line
                           (material_line_id, observation_id, line_ordinal,
                            submitted_identity, submitted_description, recorded_at_utc)
                           VALUES (?, ?, ?, ?, ?, ?)""",
                        [
                            (
                                uid("material", supplier_index, round_number, part_index, material),
                                observation_id, material, f"MAT-{material:03d}",
                                f"Synthetic Material {material:03d}", UTC,
                            )
                            for material in range(profile.materials_per_observation)
                        ],
                    )
                    observation_count += 1
                    operation_count += profile.operations_per_observation
                    material_count += profile.materials_per_observation

                connection.execute(
                    """INSERT INTO round_activation VALUES
                       (?, ?, ?, ?, 'Activate', 'synthetic-user', 'Synthetic activation', ?)""",
                    (uid("activation", supplier_index, round_number), ids["event"], supplier_id, round_id, UTC),
                )
                connection.commit()

        connection.execute(
            """UPDATE import_transaction
               SET status = 'Committed', discovered_count = ?, committed_count = ?,
                   completed_at_utc = ? WHERE import_transaction_id = ?""",
            (observation_count, observation_count, UTC, ids["transaction"]),
        )
        connection.execute(
            """UPDATE import_session SET status = 'Completed', completed_at_utc = ?
               WHERE import_session_id = ?""",
            (UTC, ids["session"]),
        )
        connection.execute(
            """INSERT INTO import_transaction_status_event VALUES
               (?, ?, 'Committed', ?, 'Synthetic import reconciled', ?, ?)""",
            (uid("transaction-status-committed"), ids["transaction"],
             canonical_json({"occurrence_count": observation_count,
                             "committed_count": observation_count,
                             "duplicate_count": 0, "blocked_count": 0,
                             "ignored_count": 0, "failed_count": 0,
                             "pending_count": 0}),
             uid("transaction-status-started"), UTC),
        )
        prior_status = uid("session-status-staging")
        for phase in ("Committing", "Reconciling", "Completed"):
            status_id = uid("session-status", phase)
            connection.execute(
                """INSERT INTO import_session_status_event VALUES (?, ?, ?, ?, ?, ?)""",
                (status_id, ids["session"], phase, f"Synthetic {phase}", prior_status, UTC),
            )
            prior_status = status_id
        connection.commit()
        build_event_summary_projection(connection, evidence_cutoff_utc=UTC)
        return {
            "observations": observation_count,
            "operations": operation_count,
            "materials": material_count,
            "detail_rows": operation_count + material_count,
        }
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate deterministic Forge X validation data")
    parser.add_argument("database", type=Path)
    parser.add_argument("--profile", choices=sorted(PROFILES), default="smoke")
    args = parser.parse_args()
    profile = PROFILES[args.profile]
    print(
        f"Generating {args.profile}: {profile.observations:,} observations and "
        f"{profile.detail_rows:,} detail rows"
    )
    counts = populate(args.database, profile)
    for name, count in counts.items():
        print(f"{name}: {count:,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
