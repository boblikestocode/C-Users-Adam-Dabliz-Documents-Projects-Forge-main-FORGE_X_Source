from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from database.migration_runner import apply_migrations
from database.services.connection import connect
from database.services.registry import (
    RegistryAuditContext,
    correct_commodity,
    correct_part_description,
    create_commodity,
    create_part,
    create_supplier,
    decide_supplier_family_proposal,
    propose_supplier_family,
    registry_publication_entities,
    remove_supplier_from_family,
    verify_registry_audit_chain,
    verify_registry_integrity,
)


ROOT = Path(__file__).resolve().parents[2]
REGISTRY_MIGRATIONS = ROOT / "database" / "registry_migrations"


def audit(event_type: str, actor: str, timestamp: str) -> RegistryAuditContext:
    return RegistryAuditContext(
        event_type=event_type,
        actor_user_id=actor,
        occurred_at_utc=timestamp,
        application_version="test",
        reason_code="TEST",
        reason_text="Registry test",
    )


class RegistryServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / "database" / "tests")
        self.database_path = Path(self.temp.name) / "registry.db"
        apply_migrations(self.database_path, REGISTRY_MIGRATIONS)
        self.connection = connect(self.database_path)
        self.connection.execute(
            """INSERT INTO role_definition
               (role_version_id, stable_role_id, role_code, role_name,
                permission_payload, permission_payload_hash,
                business_valid_from, recorded_at_utc)
               VALUES ('role-v1', 'role-master', 'MASTER', 'Forge X Master',
                       '{}', 'hash', '2026-01-01', '2026-01-01T00:00:00Z')"""
        )
        self.connection.execute(
            """INSERT INTO authority_assignment
               (authority_version_id, stable_authority_id, stable_user_id,
                stable_role_id, scope_type, business_valid_from,
                approved_by_user_id, approval_reason, recorded_at_utc)
               VALUES ('authority-v1', 'authority-master', 'master-user',
                       'role-master', 'Company', '2026-01-01', 'master-user',
                       'Initial master authority', '2026-01-01T00:00:00Z')"""
        )

    def tearDown(self) -> None:
        self.connection.close()
        self.temp.cleanup()

    def test_commodity_correction_appends_version_and_requires_master(self) -> None:
        stable_id, first_version = create_commodity(
            self.connection,
            commodity_code="C100",
            locked_name="Interior Trim",
            confirmed_by_user_id="buyer-1",
            business_valid_from="2026-09-01",
            recorded_at_utc="2026-09-03T10:00:00Z",
            audit=audit("Commodity Created", "buyer-1", "2026-09-03T10:00:00Z"),
        )
        with self.assertRaisesRegex(PermissionError, "master authority"):
            correct_commodity(
                self.connection,
                stable_commodity_id=stable_id,
                corrected_code="C100",
                corrected_name="Interior Trim Systems",
                master_user_id="buyer-1",
                correction_reason="Name clarification",
                business_valid_from="2026-09-03",
                recorded_at_utc="2026-09-03T10:01:00Z",
                audit=audit("Commodity Corrected", "buyer-1", "2026-09-03T10:01:00Z"),
            )
        second_version = correct_commodity(
            self.connection,
            stable_commodity_id=stable_id,
            corrected_code="C100",
            corrected_name="Interior Trim Systems",
            master_user_id="master-user",
            correction_reason="Approved name clarification",
            business_valid_from="2026-09-03",
            recorded_at_utc="2026-09-03T10:02:00Z",
            audit=audit("Commodity Corrected", "master-user", "2026-09-03T10:02:00Z"),
        )
        history = self.connection.execute(
            """SELECT commodity_version_id, locked_name,
                      supersedes_commodity_version_id
               FROM commodity WHERE stable_commodity_id = ?
               ORDER BY recorded_at_utc""",
            (stable_id,),
        ).fetchall()
        self.assertEqual(len(history), 2)
        self.assertEqual(tuple(history[0]), (first_version, "Interior Trim", None))
        self.assertEqual(tuple(history[1]), (second_version, "Interior Trim Systems", first_version))
        current = self.connection.execute(
            "SELECT commodity_version_id FROM v_current_commodity WHERE stable_commodity_id = ?",
            (stable_id,),
        ).fetchall()
        self.assertEqual([row[0] for row in current], [second_version])
        with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
            self.connection.execute(
                "UPDATE commodity SET locked_name = 'Changed' WHERE commodity_version_id = ?",
                (first_version,),
            )
        self.assertEqual(verify_registry_audit_chain(self.connection), [])
        self.assertEqual(verify_registry_integrity(self.connection), [])

    def test_duplicate_current_commodity_name_is_blocked(self) -> None:
        create_commodity(
            self.connection, commodity_code="C100", locked_name="Interior Trim",
            confirmed_by_user_id="buyer-1", business_valid_from="2026-09-01",
            recorded_at_utc="2026-09-03T10:00:00Z",
            audit=audit("Commodity Created", "buyer-1", "2026-09-03T10:00:00Z"),
        )
        with self.assertRaisesRegex(sqlite3.IntegrityError, "already exists"):
            create_commodity(
                self.connection, commodity_code="C200", locked_name=" interior   trim ",
                confirmed_by_user_id="buyer-2", business_valid_from="2026-09-01",
                recorded_at_utc="2026-09-03T10:01:00Z",
                audit=audit("Commodity Created", "buyer-2", "2026-09-03T10:01:00Z"),
            )
        self.assertEqual(self.connection.execute("SELECT COUNT(*) FROM commodity").fetchone()[0], 1)
        self.assertEqual(self.connection.execute("SELECT COUNT(*) FROM registry_audit_event").fetchone()[0], 1)

    def test_supplier_and_part_identity_duplicates_are_blocked(self) -> None:
        commodity_id, _ = create_commodity(
            self.connection, commodity_code="C100", locked_name="Interior Trim",
            confirmed_by_user_id="buyer-1", business_valid_from="2026-09-01",
            recorded_at_utc="2026-09-03T09:59:00Z",
            audit=audit("Commodity Created", "buyer-1", "2026-09-03T09:59:00Z"),
        )
        create_supplier(
            self.connection, supplier_code="SUP-001", display_name="Supplier One",
            legal_name=None, confirmed_by_user_id="buyer-1",
            business_valid_from="2026-09-01", recorded_at_utc="2026-09-03T10:00:00Z",
            audit=audit("Supplier Created", "buyer-1", "2026-09-03T10:00:00Z"),
        )
        with self.assertRaisesRegex(sqlite3.IntegrityError, "supplier code already exists"):
            create_supplier(
                self.connection, supplier_code="SUP-001", display_name="Different Supplier",
                legal_name=None, confirmed_by_user_id="buyer-1",
                business_valid_from="2026-09-01", recorded_at_utc="2026-09-03T10:01:00Z",
                audit=audit("Supplier Created", "buyer-1", "2026-09-03T10:01:00Z"),
            )
        create_part(
            self.connection, part_number="68501234AA", canonical_description="Carrier",
            stable_commodity_id=commodity_id, commodity_assignment_reason="Buyer-confirmed commodity ownership",
            confirmed_by_user_id="buyer-1", business_valid_from="2026-09-01",
            recorded_at_utc="2026-09-03T10:02:00Z",
            audit=audit("Part Created", "buyer-1", "2026-09-03T10:02:00Z"),
        )
        with self.assertRaisesRegex(sqlite3.IntegrityError, "part number already exists"):
            create_part(
                self.connection, part_number="68501234aa", canonical_description="Other",
                stable_commodity_id=commodity_id, commodity_assignment_reason="Duplicate test",
                confirmed_by_user_id="buyer-1", business_valid_from="2026-09-01",
                recorded_at_utc="2026-09-03T10:03:00Z",
                audit=audit("Part Created", "buyer-1", "2026-09-03T10:03:00Z"),
            )
        self.assertEqual(verify_registry_integrity(self.connection), [])

    def test_invalid_part_number_is_rejected_without_partial_registry_evidence(self) -> None:
        commodity_id, _ = create_commodity(
            self.connection, commodity_code="C100", locked_name="Interior Trim",
            confirmed_by_user_id="buyer-1", business_valid_from="2026-09-01",
            recorded_at_utc="2026-09-03T10:00:00Z",
            audit=audit("Commodity Created", "buyer-1", "2026-09-03T10:00:00Z"),
        )
        with self.assertRaisesRegex(ValueError, "exactly 10"):
            create_part(
                self.connection, part_number="6850-1234-AA", canonical_description="Carrier",
                stable_commodity_id=commodity_id, commodity_assignment_reason="Invalid test",
                confirmed_by_user_id="buyer-1", business_valid_from="2026-09-01",
                recorded_at_utc="2026-09-03T10:01:00Z",
                audit=audit("Part Created", "buyer-1", "2026-09-03T10:01:00Z"),
            )
        self.assertEqual(self.connection.execute("SELECT COUNT(*) FROM part").fetchone()[0], 0)

    def test_part_description_correction_requires_master_and_preserves_ownership(self) -> None:
        commodity_id, _ = create_commodity(
            self.connection, commodity_code="C100", locked_name="Interior Trim",
            confirmed_by_user_id="buyer-1", business_valid_from="2026-09-01",
            recorded_at_utc="2026-09-03T10:00:00Z",
            audit=audit("Commodity Created", "buyer-1", "2026-09-03T10:00:00Z"),
        )
        part_id, first_version = create_part(
            self.connection, part_number="68501234AA", canonical_description="Carrier",
            stable_commodity_id=commodity_id,
            commodity_assignment_reason="Initial commodity ownership",
            confirmed_by_user_id="buyer-1", business_valid_from="2026-09-01",
            recorded_at_utc="2026-09-03T10:01:00Z",
            audit=audit("Part Created", "buyer-1", "2026-09-03T10:01:00Z"),
        )
        with self.assertRaisesRegex(PermissionError, "master authority"):
            correct_part_description(
                self.connection, stable_part_id=part_id,
                corrected_description="Carrier Assembly", master_user_id="buyer-1",
                correction_reason="Description clarification", business_valid_from="2026-09-03",
                recorded_at_utc="2026-09-03T10:02:00Z",
                audit=audit("Part Corrected", "buyer-1", "2026-09-03T10:02:00Z"),
            )
        second_version = correct_part_description(
            self.connection, stable_part_id=part_id,
            corrected_description="Carrier Assembly", master_user_id="master-user",
            correction_reason="Approved canonical description clarification",
            business_valid_from="2026-09-03", recorded_at_utc="2026-09-03T10:03:00Z",
            audit=audit("Part Corrected", "master-user", "2026-09-03T10:03:00Z"),
        )
        history = self.connection.execute(
            """SELECT part_version_id, normalized_part_number, canonical_description,
                      supersedes_part_version_id FROM part
               WHERE stable_part_id = ? ORDER BY recorded_at_utc""", (part_id,),
        ).fetchall()
        self.assertEqual([tuple(row) for row in history], [
            (first_version, "68501234AA", "Carrier", None),
            (second_version, "68501234AA", "Carrier Assembly", first_version),
        ])
        ownership = self.connection.execute(
            """SELECT stable_commodity_id, COUNT(*) FROM part_commodity_assignment
               WHERE stable_part_id = ?""", (part_id,),
        ).fetchone()
        self.assertEqual(tuple(ownership), (commodity_id, 1))
        self.assertEqual(verify_registry_integrity(self.connection), [])

    def test_supplier_family_is_buyer_proposed_master_governed_and_non_merging(self) -> None:
        supplier_id, supplier_version = create_supplier(
            self.connection, supplier_code="SUP-001", display_name="Supplier One",
            legal_name="Supplier One LLC", confirmed_by_user_id="buyer-1",
            business_valid_from="2026-09-01", recorded_at_utc="2026-09-04T10:00:00Z",
            audit=audit("Supplier Created", "buyer-1", "2026-09-04T10:00:00Z"),
        )
        proposal_id = propose_supplier_family(
            self.connection, stable_supplier_id=supplier_id,
            proposed_family_name="Supplier One Holdings",
            proposal_reason="Buyer observed common corporate ownership",
            proposed_by_user_id="buyer-1", proposed_at_utc="2026-09-04T10:01:00Z",
            audit=audit("Supplier Family Proposed", "buyer-1", "2026-09-04T10:01:00Z"),
        )
        with self.assertRaisesRegex(PermissionError, "master authority"):
            decide_supplier_family_proposal(
                self.connection, supplier_family_proposal_id=proposal_id,
                decision_status="Approved", decision_reason="Unauthorized",
                master_user_id="buyer-1", decided_at_utc="2026-09-04T10:02:00Z",
                business_valid_from="2026-09-04",
                audit=audit("Family Approval Attempted", "buyer-1", "2026-09-04T10:02:00Z"),
            )
        _, family_id = decide_supplier_family_proposal(
            self.connection, supplier_family_proposal_id=proposal_id,
            decision_status="Approved", decision_reason="Master verified corporate ownership",
            master_user_id="master-user", decided_at_utc="2026-09-04T10:03:00Z",
            business_valid_from="2026-09-04",
            audit=audit("Supplier Family Approved", "master-user", "2026-09-04T10:03:00Z"),
        )
        self.assertIsNotNone(family_id)
        family_entity = next(
            entity for entity in registry_publication_entities(self.connection)
            if entity["entity_type"] == "Supplier Family"
        )
        self.assertEqual(family_entity["entity_id"], family_id)
        self.assertEqual(family_entity["payload"]["member_supplier_ids"], (supplier_id,))
        self.assertEqual(self.connection.execute(
            "SELECT COUNT(*) FROM supplier WHERE stable_supplier_id = ?", (supplier_id,)
        ).fetchone()[0], 1)
        self.assertEqual(self.connection.execute(
            "SELECT supplier_version_id FROM v_current_supplier WHERE stable_supplier_id = ?",
            (supplier_id,),
        ).fetchone()[0], supplier_version)
        removal_version = remove_supplier_from_family(
            self.connection, stable_supplier_id=supplier_id,
            removal_reason="Master confirmed corporate separation",
            master_user_id="master-user", removed_at_utc="2026-09-05T10:00:00Z",
            audit=audit("Supplier Family Removed", "master-user", "2026-09-05T10:00:00Z"),
        )
        history = self.connection.execute(
            """SELECT membership_version_id, business_valid_to,
                      supersedes_membership_version_id
               FROM supplier_family_member WHERE stable_supplier_id = ?
               ORDER BY recorded_at_utc""", (supplier_id,),
        ).fetchall()
        self.assertEqual(len(history), 2)
        self.assertEqual(history[1][0], removal_version)
        self.assertEqual(history[1][2], history[0][0])
        self.assertEqual(self.connection.execute(
            """SELECT COUNT(*) FROM v_current_supplier_family_member
               WHERE stable_supplier_id = ? AND business_valid_to IS NULL""", (supplier_id,),
        ).fetchone()[0], 0)
        removed_family = next(
            entity for entity in registry_publication_entities(self.connection)
            if entity["entity_type"] == "Supplier Family"
        )
        self.assertEqual(removed_family["payload"]["member_supplier_ids"], ())
        with self.assertRaisesRegex(Exception, "append-only"):
            self.connection.execute(
                "UPDATE supplier_family_member SET approval_reason = 'changed'"
            )
        self.assertEqual(verify_registry_audit_chain(self.connection), [])


if __name__ == "__main__":
    unittest.main()
