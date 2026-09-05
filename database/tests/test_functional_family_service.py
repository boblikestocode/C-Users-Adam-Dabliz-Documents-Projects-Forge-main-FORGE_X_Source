from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from database.services.audit import AuditContext, verify_audit_chain
from database.services.connection import connect
from database.services.functional_families import (
    REQUIRED_COMPARISON_FIELDS,
    FamilyMemberInput,
    FamilyVersionInput,
    comparison_permission,
    create_functional_family,
    resolve_comparable_parts,
    revise_functional_family,
)
from database.validation.generate_synthetic import PROFILES, populate


ROOT = Path(__file__).resolve().parents[2]


class FunctionalFamilyServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / "database" / "tests")
        path = Path(self.temp.name) / "families.db"
        populate(path, PROFILES["smoke"])
        self.connection = connect(path)
        self.part_ids = tuple(row[0] for row in self.connection.execute(
            "SELECT DISTINCT part_id FROM event_part ORDER BY part_id LIMIT 3"
        ))

    def tearDown(self) -> None:
        self.connection.close()
        self.temp.cleanup()

    @staticmethod
    def _audit(time: str) -> AuditContext:
        return AuditContext(
            event_type="Functional Family Confirmed", actor_user_id="buyer-1",
            effective_authority="Primary Buyer", occurred_at_utc=time,
            display_timezone="America/New_York", workstation_session="test",
            application_version="test", action_method="Unit Test",
            reason_code="BUYER_CONFIRMED", reason_text="Engineering continuity",
        )

    def _definition(self, *, piece_price: str = "Directional", members: int = 2) -> FamilyVersionInput:
        permissions = {field: "Not Comparable" for field in REQUIRED_COMPARISON_FIELDS}
        permissions["Piece Price"] = piece_price
        permissions["Labor"] = "Governing"
        return FamilyVersionInput(
            readable_name="Front Bracket Family", relationship_type="Predecessor / Successor",
            business_rationale="Buyer confirmed common function and manufacturing process",
            members=tuple(FamilyMemberInput(part_id, "Comparable Member") for part_id in self.part_ids[:members]),
            comparison_permissions=permissions,
            supporting_context={"programs": ["DT", "DT2"], "evidence": "Engineering review"},
        )

    def test_create_and_revise_preserve_versions_and_permissions(self) -> None:
        family_id = create_functional_family(
            self.connection, definition=self._definition(), created_by_user_id="buyer-1",
            confirmed_by_user_id="buyer-1", recorded_at_utc="2026-09-04T12:00:00Z",
            audit=self._audit("2026-09-04T12:00:00Z"),
        )
        self.assertEqual(comparison_permission(
            self.connection, functional_family_id=family_id, measure_or_category="Piece Price"
        ), "Directional")
        revise_functional_family(
            self.connection, functional_family_id=family_id,
            definition=self._definition(piece_price="Governing", members=3),
            confirmed_by_user_id="buyer-1", recorded_at_utc="2026-09-04T12:01:00Z",
            audit=self._audit("2026-09-04T12:01:00Z"),
        )
        self.assertEqual(comparison_permission(
            self.connection, functional_family_id=family_id, measure_or_category="Piece Price"
        ), "Governing")
        self.assertEqual(self.connection.execute(
            "SELECT COUNT(*) FROM family_version WHERE functional_family_id = ?", (family_id,)
        ).fetchone()[0], 2)
        self.assertEqual(verify_audit_chain(self.connection), [])

    def test_every_required_field_must_have_explicit_permission(self) -> None:
        definition = self._definition()
        permissions = dict(definition.comparison_permissions)
        del permissions["Tooling"]
        incomplete = FamilyVersionInput(
            definition.readable_name, definition.relationship_type,
            definition.business_rationale, definition.members, permissions,
            definition.supporting_context,
        )
        with self.assertRaisesRegex(ValueError, "Tooling"):
            create_functional_family(
                self.connection, definition=incomplete, created_by_user_id="buyer-1",
                confirmed_by_user_id="buyer-1", recorded_at_utc="2026-09-04T12:00:00Z",
                audit=self._audit("2026-09-04T12:00:00Z"),
            )

    def test_different_part_numbers_never_become_exact_part_identity(self) -> None:
        family_id = create_functional_family(
            self.connection, definition=self._definition(piece_price="Governing"),
            created_by_user_id="buyer-1", confirmed_by_user_id="buyer-1",
            recorded_at_utc="2026-09-04T12:00:00Z", audit=self._audit("2026-09-04T12:00:00Z"),
        )
        members = self.connection.execute(
            """SELECT member.part_id FROM v_current_family_version version
               JOIN family_member member ON member.family_version_id = version.family_version_id
               WHERE version.functional_family_id = ? ORDER BY member.part_id""", (family_id,)
        ).fetchall()
        self.assertEqual([row[0] for row in members], sorted(self.part_ids[:2]))
        self.assertNotEqual(members[0][0], members[1][0])
        with self.assertRaisesRegex(Exception, "immutable"):
            self.connection.execute(
                "UPDATE family_member SET part_id = ? WHERE part_id = ?",
                (members[0][0], members[1][0]),
            )

    def test_comparison_resolution_obeys_field_permission_and_discloses_evidence_class(self) -> None:
        family_id = create_functional_family(
            self.connection, definition=self._definition(piece_price="Directional"),
            created_by_user_id="buyer-1", confirmed_by_user_id="buyer-1",
            recorded_at_utc="2026-09-04T12:00:00Z", audit=self._audit("2026-09-04T12:00:00Z"),
        )
        piece_price = resolve_comparable_parts(
            self.connection, anchor_part_id=self.part_ids[0], measure_or_category="Piece Price"
        )
        self.assertEqual(piece_price[0].evidence_class, "Exact Part")
        self.assertEqual(piece_price[0].candidate_part_id, self.part_ids[0])
        self.assertEqual(piece_price[1].evidence_class, "Buyer-Confirmed Functional Family")
        self.assertEqual(piece_price[1].comparison_eligibility, "Directional")
        self.assertEqual(piece_price[1].functional_family_id, family_id)
        self.assertEqual(len(resolve_comparable_parts(
            self.connection, anchor_part_id=self.part_ids[0],
            measure_or_category="Piece Price", include_directional=False,
        )), 1)
        self.assertEqual(len(resolve_comparable_parts(
            self.connection, anchor_part_id=self.part_ids[0], measure_or_category="Labor"
        )), 2)
        self.assertEqual(len(resolve_comparable_parts(
            self.connection, anchor_part_id=self.part_ids[0], measure_or_category="Tooling"
        )), 1)


if __name__ == "__main__":
    unittest.main()
