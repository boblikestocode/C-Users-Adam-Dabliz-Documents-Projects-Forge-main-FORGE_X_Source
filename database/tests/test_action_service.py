from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from database.services.actions import (
    create_buyer_action,
    review_resolution_suggestion,
    suggest_resolution,
    transition_buyer_action,
)
from database.services.audit import AuditContext, verify_audit_chain
from database.services.connection import connect
from database.services.decimals import ExactDecimal
from database.validation.generate_synthetic import PROFILES, populate


ROOT = Path(__file__).resolve().parents[2]


def audit(event_type: str, timestamp: str) -> AuditContext:
    return AuditContext(
        event_type=event_type,
        actor_user_id="buyer-1",
        effective_authority="Buyer",
        occurred_at_utc=timestamp,
        display_timezone="America/New_York",
        workstation_session="action-test",
        application_version="test",
        action_method="Automated Test",
        reason_code="TEST",
    )


class BuyerActionServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / "database" / "tests")
        self.database_path = Path(self.temp.name) / "actions.db"
        populate(self.database_path, PROFILES["smoke"])
        self.connection = connect(self.database_path)
        row = self.connection.execute(
            """SELECT context_id, supplier_id, part_id, observation_id
               FROM pbd_observation LIMIT 1"""
        ).fetchone()
        self.event_id, self.supplier_id, self.part_id, self.observation_id = row
        self.rule_id = self.connection.execute(
            "SELECT rule_version_id FROM rule_version LIMIT 1"
        ).fetchone()[0]

    def tearDown(self) -> None:
        self.connection.close()
        self.temp.cleanup()

    def _create_action(self) -> str:
        return create_buyer_action(
            self.connection,
            event_id=self.event_id,
            supplier_id=self.supplier_id,
            part_id=self.part_id,
            governing_entity_type="PBD Observation",
            governing_entity_id=self.observation_id,
            issue_type="Missing Cost Support",
            financial_impact=ExactDecimal.parse("12500.50"),
            currency_id="USD",
            required_supplier_action="Provide supporting cost detail",
            owner_user_id="buyer-1",
            created_by_user_id="buyer-1",
            created_at_utc="2026-09-03T17:00:00Z",
            audit=audit("Buyer Action Created", "2026-09-03T17:00:00Z"),
        )

    def test_action_transitions_append_versions(self) -> None:
        action_id = self._create_action()
        transition_buyer_action(
            self.connection,
            buyer_action_id=action_id,
            new_status="Sent to Supplier",
            required_supplier_action="Provide supporting cost detail",
            owner_user_id="buyer-1",
            recorded_by_user_id="buyer-1",
            recorded_at_utc="2026-09-03T17:01:00Z",
            transition_reason="Request sent by email",
            working_note="Awaiting response",
            audit=audit("Buyer Action Updated", "2026-09-03T17:01:00Z"),
        )
        transition_buyer_action(
            self.connection,
            buyer_action_id=action_id,
            new_status="Resolved",
            required_supplier_action="No further action",
            owner_user_id="buyer-1",
            recorded_by_user_id="buyer-1",
            recorded_at_utc="2026-09-03T17:02:00Z",
            transition_reason="Support received in confirmed quotation",
            working_note=None,
            audit=audit("Buyer Action Resolved", "2026-09-03T17:02:00Z"),
        )
        versions = self.connection.execute(
            "SELECT COUNT(*) FROM buyer_action_version WHERE buyer_action_id = ?",
            (action_id,),
        ).fetchone()[0]
        current = self.connection.execute(
            "SELECT action_status, resolution_reason FROM v_current_buyer_action WHERE buyer_action_id = ?",
            (action_id,),
        ).fetchone()
        self.assertEqual(versions, 3)
        self.assertEqual(tuple(current), ("Resolved", "Support received in confirmed quotation"))
        self.assertEqual(verify_audit_chain(self.connection), [])

    def test_accepted_suggestion_does_not_close_action(self) -> None:
        action_id = self._create_action()
        stable_suggestion_id = suggest_resolution(
            self.connection,
            buyer_action_id=action_id,
            source_entity_type="PBD Observation",
            source_entity_id=self.observation_id,
            suggestion_rule_version_id=self.rule_id,
            suggestion_reason="Later evidence may address the missing support",
            generated_at_utc="2026-09-03T17:01:00Z",
            audit=audit("Resolution Suggested", "2026-09-03T17:01:00Z"),
        )
        review_resolution_suggestion(
            self.connection,
            stable_suggestion_id=stable_suggestion_id,
            decision="Accepted by Buyer",
            reviewed_by_user_id="buyer-1",
            reviewed_at_utc="2026-09-03T17:02:00Z",
            review_reason="Evidence is relevant; action still requires explicit closure",
            audit=audit("Resolution Suggestion Reviewed", "2026-09-03T17:02:00Z"),
        )
        action_status = self.connection.execute(
            "SELECT action_status FROM v_current_buyer_action WHERE buyer_action_id = ?",
            (action_id,),
        ).fetchone()[0]
        suggestion_versions = self.connection.execute(
            """SELECT suggestion_status FROM buyer_action_resolution_suggestion
               WHERE stable_suggestion_id = ? ORDER BY rowid""",
            (stable_suggestion_id,),
        ).fetchall()
        self.assertEqual(action_status, "Open")
        self.assertEqual([row[0] for row in suggestion_versions], ["Pending Buyer Review", "Accepted by Buyer"])

    def test_terminal_action_requires_reason(self) -> None:
        action_id = self._create_action()
        with self.assertRaisesRegex(ValueError, "requires a reason"):
            transition_buyer_action(
                self.connection,
                buyer_action_id=action_id,
                new_status="Accepted Exception",
                required_supplier_action="None",
                owner_user_id="buyer-1",
                recorded_by_user_id="buyer-1",
                recorded_at_utc="2026-09-03T17:01:00Z",
                transition_reason="",
                working_note=None,
                audit=audit("Buyer Action Updated", "2026-09-03T17:01:00Z"),
            )
        self.assertEqual(
            self.connection.execute(
                "SELECT action_status FROM v_current_buyer_action WHERE buyer_action_id = ?",
                (action_id,),
            ).fetchone()[0],
            "Open",
        )


if __name__ == "__main__":
    unittest.main()
