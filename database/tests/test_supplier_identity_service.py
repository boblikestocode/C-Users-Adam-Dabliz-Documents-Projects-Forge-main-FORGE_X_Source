from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from database.services.audit import AuditContext, verify_audit_chain
from database.services.connection import connect
from database.services.supplier_identities import confirm_supplier_identity_batch
from database.validation.generate_synthetic import PROFILES, populate

ROOT = Path(__file__).resolve().parents[2]
WHEN = "2026-09-04T20:00:00Z"


def audit() -> AuditContext:
    return AuditContext("Supplier Identity Confirmed", "buyer-1", "Buyer", WHEN,
                        "America/New_York", "identity-test", "test",
                        "Automated Test", "SUPPLIER_IDENTITY")


class SupplierIdentityServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / "database" / "tests")
        self.path = Path(self.temp.name) / "identity.db"
        populate(self.path, PROFILES["smoke"])
        self.connection = connect(self.path)
        source = self.connection.execute(
            "SELECT * FROM pbd_observation LIMIT 1"
        ).fetchone()
        occurrence = self.connection.execute(
            "SELECT * FROM source_occurrence WHERE occurrence_id = ?", (source["occurrence_id"],)
        ).fetchone()
        self.connection.execute(
            """INSERT INTO source_occurrence
               (occurrence_id, worksheet_id, region_locator, detection_rule_version_id,
                detection_result, terminal_status, recorded_at_utc)
               VALUES ('unresolved-occurrence', ?, 'ZZ1:ZZ2', ?, 'PBD', 'Pending', ?)""",
            (occurrence["worksheet_id"], occurrence["detection_rule_version_id"], WHEN),
        )
        self.connection.execute(
            """INSERT INTO staged_observation VALUES
               ('unresolved-staged', 'unresolved-occurrence', NULL, ?, ?,
                ?, ?, 'Ready to Commit', 0, ?)""",
            (source["submitted_supplier_name"], source["submitted_part_number"],
             source["observation_context"], source["context_id"], WHEN),
        )
        self.observation_id = "unresolved-observation"
        self.connection.execute(
            """INSERT INTO pbd_observation
               (observation_id, staged_observation_id, occurrence_id,
                observation_context, context_id, supplier_id, supplier_plant_id,
                part_id, submitted_supplier_name, submitted_part_number,
                submitted_part_description, economic_date, economic_date_precision,
                structure_category, recorded_at_utc)
               VALUES (?, 'unresolved-staged', 'unresolved-occurrence',
                       ?, ?, NULL, NULL, ?, ?, ?, ?, ?, ?, 'Valid Aggregate', ?)""",
            (self.observation_id, source["observation_context"], source["context_id"],
             source["part_id"], source["submitted_supplier_name"],
             source["submitted_part_number"], source["submitted_part_description"],
             source["economic_date"], source["economic_date_precision"], WHEN),
        )

    def tearDown(self) -> None:
        self.connection.close()
        self.temp.cleanup()

    def test_selected_identity_confirmation_preserves_original_observation(self) -> None:
        confirmations = confirm_supplier_identity_batch(
            self.connection, observation_ids=(self.observation_id,),
            confirmed_supplier_id="supplier-confirmed", confirmed_supplier_code="SUP-900",
            confirmation_reason="Buyer matched submitted supplier evidence",
            confirmed_by_user_id="buyer-1", recorded_at_utc=WHEN, audit=audit(),
        )
        self.assertEqual(len(confirmations), 1)
        self.assertIsNone(self.connection.execute(
            "SELECT supplier_id FROM pbd_observation WHERE observation_id = ?",
            (self.observation_id,),
        ).fetchone()[0])
        effective = self.connection.execute(
            """SELECT supplier_id, confirmed_supplier_code
               FROM v_effective_pbd_observation WHERE observation_id = ?""",
            (self.observation_id,),
        ).fetchone()
        self.assertEqual(tuple(effective), ("supplier-confirmed", "SUP-900"))
        with self.assertRaisesRegex(Exception, "immutable"):
            self.connection.execute(
                "UPDATE observation_supplier_identity_confirmation SET confirmed_supplier_code = 'X'"
            )
        self.assertEqual(verify_audit_chain(self.connection), [])

    def test_confirmation_requires_explicit_unresolved_selection(self) -> None:
        confirm_supplier_identity_batch(
            self.connection, observation_ids=(self.observation_id,),
            confirmed_supplier_id="supplier-confirmed", confirmed_supplier_code="SUP-900",
            confirmation_reason="Buyer confirmation", confirmed_by_user_id="buyer-1",
            recorded_at_utc=WHEN, audit=audit(),
        )
        with self.assertRaisesRegex(ValueError, "already has"):
            confirm_supplier_identity_batch(
                self.connection, observation_ids=(self.observation_id,),
                confirmed_supplier_id="other", confirmed_supplier_code="SUP-900",
                confirmation_reason="Implicit remap", confirmed_by_user_id="buyer-1",
                recorded_at_utc=WHEN, audit=audit(),
            )


if __name__ == "__main__":
    unittest.main()
