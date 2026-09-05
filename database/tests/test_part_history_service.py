from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from database.services.audit import AuditContext
from database.services.connection import connect
from database.services.functional_families import (
    REQUIRED_COMPARISON_FIELDS, FamilyMemberInput, FamilyVersionInput,
    create_functional_family,
)
from database.services.integrity import run_health_gate
from database.services.part_history import build_part_history_projection
from database.validation.generate_synthetic import PROFILES, populate


ROOT = Path(__file__).resolve().parents[2]


class PartHistoryServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / "database" / "tests")
        self.path = Path(self.temp.name) / "history.db"
        populate(self.path, PROFILES["smoke"])
        self.connection = connect(self.path)
        self.parts = [str(row[0]) for row in self.connection.execute(
            "SELECT DISTINCT part_id FROM pbd_observation ORDER BY part_id LIMIT 2"
        )]

    def tearDown(self) -> None:
        self.connection.close()
        self.temp.cleanup()

    def _audit(self) -> AuditContext:
        return AuditContext("Functional Family Created", "buyer", "Buyer",
                            "2026-02-01T00:00:00Z", "America/New_York",
                            "history-test", "test", "Automated Test", "CONFIRM")

    def test_exact_and_family_evidence_remain_distinct(self) -> None:
        permissions = {field: "Not Comparable" for field in REQUIRED_COMPARISON_FIELDS}
        permissions["Piece Price"] = "Directional"
        family_id = create_functional_family(
            self.connection,
            definition=FamilyVersionInput(
                "Confirmed Alternatives", "Predecessor/Successor", "Buyer-confirmed context",
                (FamilyMemberInput(self.parts[0], "Anchor"),
                 FamilyMemberInput(self.parts[1], "Alternative")), permissions, {"source": "test"},
            ), created_by_user_id="buyer", confirmed_by_user_id="buyer",
            recorded_at_utc="2026-02-01T00:00:00Z", audit=self._audit(),
        )
        generation = build_part_history_projection(
            self.connection, anchor_part_id=self.parts[0],
            evidence_cutoff_utc="2026-12-31T23:59:59Z",
        )
        classes = self.connection.execute(
            """SELECT evidence_class, comparison_eligibility,
                      evidence_part_id, functional_family_id
               FROM part_history_projection WHERE part_history_generation_id = ?
               GROUP BY evidence_class, comparison_eligibility,
                        evidence_part_id, functional_family_id""", (generation,),
        ).fetchall()
        self.assertIn(("Exact Part", "Governing", self.parts[0], None),
                      [tuple(row) for row in classes])
        self.assertIn(("Buyer-Confirmed Functional Family", "Directional",
                       self.parts[1], family_id), [tuple(row) for row in classes])
        self.assertNotIn("PART_HISTORY_GENERATION_MISMATCH",
                         {finding.code for finding in run_health_gate(self.connection)})

    def test_generation_before_family_confirmation_remains_reproducible(self) -> None:
        generation = build_part_history_projection(
            self.connection, anchor_part_id=self.parts[0],
            evidence_cutoff_utc="2026-01-31T23:59:59Z",
        )
        permissions = {field: "Not Comparable" for field in REQUIRED_COMPARISON_FIELDS}
        permissions["Piece Price"] = "Governing"
        create_functional_family(
            self.connection,
            definition=FamilyVersionInput(
                "Later Family", "Successor", "Later buyer confirmation",
                (FamilyMemberInput(self.parts[0], "Anchor"),
                 FamilyMemberInput(self.parts[1], "Successor")), permissions, {},
            ), created_by_user_id="buyer", confirmed_by_user_id="buyer",
            recorded_at_utc="2026-02-01T00:00:00Z", audit=self._audit(),
        )
        self.assertEqual(self.connection.execute(
            """SELECT COUNT(*) FROM part_history_projection
               WHERE part_history_generation_id = ?
                 AND evidence_class = 'Buyer-Confirmed Functional Family'""",
            (generation,),
        ).fetchone()[0], 0)
        self.assertNotIn("PART_HISTORY_GENERATION_MISMATCH",
                         {finding.code for finding in run_health_gate(self.connection)})

    def test_health_detects_forged_manifest(self) -> None:
        self.connection.execute(
            """INSERT INTO part_history_generation_manifest VALUES
               ('forged-history', ?, '2026-12-31T23:59:59Z', ?, 0,
                'Complete', '2026-12-31T23:59:59Z')""",
            (self.parts[0], "0" * 64),
        )
        self.assertIn("PART_HISTORY_GENERATION_MISMATCH",
                      {finding.code for finding in run_health_gate(self.connection)})


if __name__ == "__main__":
    unittest.main()
