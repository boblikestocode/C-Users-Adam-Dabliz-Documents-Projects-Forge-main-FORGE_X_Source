from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from database.services.audit import AuditContext, verify_audit_chain
from database.services.connection import connect
from database.services.knowledge import (
    KnowledgeContext,
    KnowledgeScope,
    create_knowledge_version,
    resolve_knowledge,
)
from database.validation.generate_synthetic import PROFILES, populate


ROOT = Path(__file__).resolve().parents[2]


def audit(event_type: str, timestamp: str) -> AuditContext:
    return AuditContext(
        event_type=event_type,
        actor_user_id="knowledge-owner",
        effective_authority="Master",
        occurred_at_utc=timestamp,
        display_timezone="America/New_York",
        workstation_session="knowledge-test",
        application_version="test",
        action_method="Automated Test",
        reason_code="TEST",
    )


class KnowledgeServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / "database" / "tests")
        self.database_path = Path(self.temp.name) / "knowledge.db"
        populate(self.database_path, PROFILES["smoke"])
        self.connection = connect(self.database_path)
        row = self.connection.execute(
            """SELECT observation_id, supplier_id, supplier_plant_id, part_id
               FROM pbd_observation LIMIT 1"""
        ).fetchone()
        self.observation_id, self.supplier_id, self.plant_id, self.part_id = row
        self.commodity_id = self.connection.execute(
            "SELECT commodity_id FROM commodity_database"
        ).fetchone()[0]

    def tearDown(self) -> None:
        self.connection.close()
        self.temp.cleanup()

    def _create(self, *, scope: KnowledgeScope, payload: dict, level: str = "Confirmed Knowledge", timestamp: str = "2026-09-03T12:00:00Z") -> str:
        _, version_id = create_knowledge_version(
            self.connection,
            knowledge_type="LABOR_RATE_PATTERN",
            knowledge_level=level,
            payload=payload,
            scope=scope,
            business_valid_from="2026-01-01",
            business_valid_to=None,
            recorded_at_utc=timestamp,
            created_by_user_id="knowledge-owner",
            confirmed_by_user_id="knowledge-owner" if level != "Observed Evidence" else None,
            approval_authority_type="Master" if level == "Corporate Standard" else "Buyer",
            business_rationale="Supported validation evidence" if level != "Observed Evidence" else None,
            evidence=(("PBD Observation", self.observation_id, "Supporting"),),
            audit=audit("Knowledge Version Created", timestamp),
        )
        return version_id

    def test_exact_plant_part_overrides_company_standard(self) -> None:
        company_version = self._create(
            scope=KnowledgeScope(company_wide=True),
            payload={"rate": "50.00", "currency": "USD"},
            level="Corporate Standard",
        )
        specific_version = self._create(
            scope=KnowledgeScope(supplier_plant_id=self.plant_id, part_id=self.part_id),
            payload={"rate": "47.25", "currency": "USD"},
            timestamp="2026-09-03T12:00:01Z",
        )
        resolution = resolve_knowledge(
            self.connection,
            knowledge_type="LABOR_RATE_PATTERN",
            context=KnowledgeContext(
                supplier_id=self.supplier_id,
                supplier_plant_id=self.plant_id,
                commodity_id=self.commodity_id,
                part_id=self.part_id,
            ),
            business_date="2026-09-03",
            recorded_cutoff_utc="2026-09-03T13:00:00Z",
        )
        self.assertEqual(resolution.status, "Resolved")
        self.assertEqual(resolution.selected_knowledge_version_id, specific_version)
        self.assertNotEqual(resolution.selected_knowledge_version_id, company_version)
        self.assertEqual(resolution.specificity, 1)
        self.assertEqual(verify_audit_chain(self.connection), [])

    def test_equal_specificity_conflict_is_not_silently_selected(self) -> None:
        scope = KnowledgeScope(supplier_plant_id=self.plant_id, commodity_id=self.commodity_id)
        first = self._create(scope=scope, payload={"rate": "45.00"})
        second = self._create(
            scope=scope,
            payload={"rate": "55.00"},
            timestamp="2026-09-03T12:00:01Z",
        )
        resolution = resolve_knowledge(
            self.connection,
            knowledge_type="LABOR_RATE_PATTERN",
            context=KnowledgeContext(
                supplier_id=self.supplier_id,
                supplier_plant_id=self.plant_id,
                commodity_id=self.commodity_id,
                part_id=self.part_id,
            ),
            business_date="2026-09-03",
            recorded_cutoff_utc="2026-09-03T13:00:00Z",
        )
        self.assertEqual(resolution.status, "Conflict")
        self.assertEqual(set(resolution.conflicting_version_ids), {first, second})

    def test_future_recorded_knowledge_is_invisible_to_past_analysis(self) -> None:
        self._create(
            scope=KnowledgeScope(company_wide=True),
            payload={"rate": "50.00"},
            level="Corporate Standard",
            timestamp="2026-09-03T12:00:00Z",
        )
        resolution = resolve_knowledge(
            self.connection,
            knowledge_type="LABOR_RATE_PATTERN",
            context=KnowledgeContext(commodity_id=self.commodity_id),
            business_date="2026-09-03",
            recorded_cutoff_utc="2026-09-03T11:59:59Z",
        )
        self.assertEqual(resolution.status, "Not Found")

    def test_corporate_standard_requires_master_authority(self) -> None:
        with self.assertRaisesRegex(ValueError, "master authority"):
            create_knowledge_version(
                self.connection,
                knowledge_type="TEST",
                knowledge_level="Corporate Standard",
                payload={"value": 1},
                scope=KnowledgeScope(company_wide=True),
                business_valid_from=None,
                business_valid_to=None,
                recorded_at_utc="2026-09-03T12:00:00Z",
                created_by_user_id="buyer",
                confirmed_by_user_id="buyer",
                approval_authority_type="Buyer",
                business_rationale="Not authorized",
                evidence=(("PBD Observation", self.observation_id, "Supporting"),),
                audit=audit("Knowledge Version Created", "2026-09-03T12:00:00Z"),
            )


if __name__ == "__main__":
    unittest.main()
