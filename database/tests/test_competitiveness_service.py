from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from database.services.audit import AuditContext, verify_audit_chain
from database.services.competitiveness import (
    build_supplier_improvement_assessment,
    confirm_supplier_competitiveness,
)
from database.services.connection import connect
from database.services.integrity import run_health_gate
from database.validation.generate_synthetic import PROFILES, populate


ROOT = Path(__file__).resolve().parents[2]


def audit(event_type: str, occurred_at_utc: str) -> AuditContext:
    return AuditContext(
        event_type=event_type, actor_user_id="buyer-competitiveness",
        effective_authority="Buyer", occurred_at_utc=occurred_at_utc,
        display_timezone="America/New_York", workstation_session="competitive-test",
        application_version="test", action_method="Automated Test",
        reason_code="SUPPORTED_COMPETITIVENESS",
    )


class CompetitivenessServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / "database" / "tests")
        self.database_path = Path(self.temp.name) / "competitiveness.db"
        populate(self.database_path, PROFILES["smoke"])
        self.connection = connect(self.database_path)
        rows = self.connection.execute(
            """SELECT quote_round_id, event_id, supplier_id
               FROM supplier_quote_round
               WHERE (event_id, supplier_id) = (
                   SELECT event_id, supplier_id FROM supplier_quote_round
                   GROUP BY event_id, supplier_id HAVING COUNT(*) >= 3 LIMIT 1)
               ORDER BY round_number LIMIT 3"""
        ).fetchall()
        self.rounds = [str(row[0]) for row in rows]
        self.event_id, self.supplier_id = str(rows[0][1]), str(rows[0][2])
        self.commodity_id = self.connection.execute(
            "SELECT commodity_id FROM sourcing_event WHERE event_id = ?",
            (self.event_id,),
        ).fetchone()[0]

    def tearDown(self) -> None:
        self.connection.close()
        self.temp.cleanup()

    def _confirm(self, index: int, status: str, timestamp: str) -> str:
        return confirm_supplier_competitiveness(
            self.connection, event_id=self.event_id, supplier_id=self.supplier_id,
            quote_population_id=self.rounds[index], competitiveness_status=status,
            evidence_entity_type="Quote Round", evidence_entity_id=self.rounds[index],
            explanation_payload={"basis": "supported package comparison", "status": status},
            confirmed_by_user_id="buyer-competitiveness", confirmed_at_utc=timestamp,
            audit=audit("Supplier Competitiveness Confirmed", timestamp),
        )

    def test_recent_then_sustained_improvement_retains_prior_red(self) -> None:
        red_id = self._confirm(0, "Red", "2026-09-01T10:00:00Z")
        self._confirm(1, "Green", "2026-09-02T10:00:00Z")
        recent = build_supplier_improvement_assessment(
            self.connection, supplier_id=self.supplier_id,
            commodity_id=self.commodity_id,
            evidence_cutoff_utc="2026-09-02T23:59:59Z",
            generated_at_utc="2026-09-02T23:59:59Z",
            audit=audit("Supplier Improvement Assessed", "2026-09-02T23:59:59Z"),
        )
        self.assertEqual(recent.assessment_status, "Recent Improvement")
        self.assertEqual(recent.trailing_competitive_population_count, 1)
        self._confirm(2, "Green", "2026-09-03T10:00:00Z")
        sustained = build_supplier_improvement_assessment(
            self.connection, supplier_id=self.supplier_id,
            commodity_id=self.commodity_id,
            evidence_cutoff_utc="2026-09-03T23:59:59Z",
            generated_at_utc="2026-09-03T23:59:59Z",
            audit=audit("Supplier Improvement Assessed", "2026-09-03T23:59:59Z"),
        )
        self.assertEqual(sustained.assessment_status, "Sustained Improvement")
        self.assertEqual(sustained.trailing_competitive_population_count, 2)
        retained = self.connection.execute(
            """SELECT competitiveness_status
               FROM supplier_competitiveness_observation
               WHERE supplier_competitiveness_observation_id = ?""", (red_id,),
        ).fetchone()[0]
        self.assertEqual(retained, "Red")
        evidence = self.connection.execute(
            """SELECT COUNT(*) FROM supplier_improvement_evidence
               WHERE supplier_improvement_assessment_id = ?""",
            (sustained.assessment_id,),
        ).fetchone()[0]
        self.assertEqual(evidence, 3)
        self.assertEqual(verify_audit_chain(self.connection), [])
        codes = {finding.code for finding in run_health_gate(self.connection)}
        self.assertNotIn("SUPPLIER_COMPETITIVENESS_LINEAGE_MISMATCH", codes)
        self.assertNotIn("SUPPLIER_IMPROVEMENT_ASSESSMENT_MISMATCH", codes)
        with self.assertRaisesRegex(Exception, "immutable"):
            self.connection.execute(
                "UPDATE supplier_competitiveness_observation SET competitiveness_status = 'Green'"
            )
        self.connection.execute("DROP TRIGGER no_update_supplier_improvement")
        self.connection.execute(
            """UPDATE supplier_improvement_assessment
               SET assessment_status = 'No Current Improvement'
               WHERE supplier_improvement_assessment_id = ?""",
            (sustained.assessment_id,),
        )
        self.assertIn(
            "SUPPLIER_IMPROVEMENT_ASSESSMENT_MISMATCH",
            {finding.code for finding in run_health_gate(self.connection)},
        )


if __name__ == "__main__":
    unittest.main()
