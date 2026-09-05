from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from database.services.audit import AuditContext, verify_audit_chain
from database.services.connection import connect
from database.services.decimals import ExactDecimal
from database.services.payment_treatments import (
    PaymentTreatment,
    confirm_payment_treatment,
    observations_with_unconfirmed_payment_treatment,
    record_payment_cost_evidence,
)
from database.validation.generate_synthetic import PROFILES, populate


ROOT = Path(__file__).resolve().parents[2]


class PaymentTreatmentServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / "database" / "tests")
        path = Path(self.temp.name) / "payment-treatment.db"
        populate(path, PROFILES["smoke"])
        self.connection = connect(path)
        self.observation_id, self.source_datum_id = self.connection.execute(
            """SELECT observation.observation_id, datum.source_datum_id
               FROM pbd_observation observation
               JOIN source_occurrence occurrence ON occurrence.occurrence_id = observation.occurrence_id
               JOIN source_datum datum ON datum.worksheet_id = occurrence.worksheet_id
               LIMIT 1"""
        ).fetchone()
        self.rule_id = self.connection.execute(
            "SELECT rule_version_id FROM rule_version LIMIT 1"
        ).fetchone()[0]

    def tearDown(self) -> None:
        self.connection.close()
        self.temp.cleanup()

    @staticmethod
    def _audit(time: str) -> AuditContext:
        return AuditContext(
            event_type="Payment Treatment", actor_user_id="buyer-1",
            effective_authority="Primary Buyer", occurred_at_utc=time,
            display_timezone="America/New_York", workstation_session="test",
            application_version="test", action_method="Unit Test",
            reason_code="TEST", reason_text="Test evidence",
        )

    def _record(self) -> str:
        return record_payment_cost_evidence(
            self.connection, observation_id=self.observation_id,
            source_datum_id=self.source_datum_id, cost_type="SFT", program_year=2027,
            submitted_wording="Supplier Tooling", submitted_amount=ExactDecimal.parse("125000.00"),
            normalized_unit_id="USD", currency_id="USD",
            treatment_rule_version_id=self.rule_id,
            recorded_at_utc="2026-09-04T11:00:00Z", audit=self._audit("2026-09-04T11:00:00Z"),
        )

    def test_evidence_starts_unconfirmed_then_is_superseded(self) -> None:
        evidence_id = self._record()
        self.assertEqual(observations_with_unconfirmed_payment_treatment(self.connection), (self.observation_id,))
        confirm_payment_treatment(
            self.connection, payment_cost_evidence_id=evidence_id,
            treatment=PaymentTreatment("Separate Lump Sum"),
            treatment_rule_version_id=self.rule_id, confirmed_by_user_id="buyer-1",
            confirmation_reason="Supplier confirmed separate purchase order",
            recorded_at_utc="2026-09-04T11:01:00Z", audit=self._audit("2026-09-04T11:01:00Z"),
        )
        self.assertEqual(observations_with_unconfirmed_payment_treatment(self.connection), ())
        self.assertEqual(verify_audit_chain(self.connection), [])
        self.assertEqual(self.connection.execute(
            "SELECT COUNT(*) FROM payment_treatment_version WHERE payment_cost_evidence_id = ?", (evidence_id,)
        ).fetchone()[0], 2)

    def test_partial_treatment_requires_complete_allocation(self) -> None:
        evidence_id = self._record()
        audit_count = self.connection.execute("SELECT COUNT(*) FROM audit_event").fetchone()[0]
        with self.assertRaisesRegex(ValueError, "requires upfront"):
            confirm_payment_treatment(
                self.connection, payment_cost_evidence_id=evidence_id,
                treatment=PaymentTreatment("Partially Amortized", upfront_amount=ExactDecimal.parse("50000")),
                treatment_rule_version_id=self.rule_id, confirmed_by_user_id="buyer-1",
                confirmation_reason="Incomplete allocation",
                recorded_at_utc="2026-09-04T11:01:00Z", audit=self._audit("2026-09-04T11:01:00Z"),
            )
        self.assertEqual(self.connection.execute("SELECT COUNT(*) FROM audit_event").fetchone()[0], audit_count)

    def test_source_datum_must_belong_to_observation(self) -> None:
        other_observation = self.connection.execute(
            "SELECT observation_id FROM pbd_observation WHERE observation_id <> ? LIMIT 1",
            (self.observation_id,),
        ).fetchone()[0]
        with self.assertRaisesRegex(ValueError, "must belong"):
            record_payment_cost_evidence(
                self.connection, observation_id=other_observation,
                source_datum_id=self.source_datum_id, cost_type="ED&D", program_year=2027,
                submitted_wording="Engineering", submitted_amount=ExactDecimal.parse("10"),
                normalized_unit_id="USD", currency_id="USD",
                treatment_rule_version_id=self.rule_id,
                recorded_at_utc="2026-09-04T11:00:00Z", audit=self._audit("2026-09-04T11:00:00Z"),
            )


if __name__ == "__main__":
    unittest.main()
