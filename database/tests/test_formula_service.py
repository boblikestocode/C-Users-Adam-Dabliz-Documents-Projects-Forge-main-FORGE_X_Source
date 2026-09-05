from __future__ import annotations

import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from database.services.audit import AuditContext, verify_audit_chain
from database.services.connection import connect
from database.services.decimals import ExactDecimal
from database.services.formulas import (
    analyze_formula, persist_formula_integrity, record_formula_dependency_status,
)
from database.validation.generate_synthetic import PROFILES, populate


ROOT = Path(__file__).resolve().parents[2]


def audit() -> AuditContext:
    return AuditContext(
        event_type="Formula Integrity Evaluated",
        actor_user_id=None,
        effective_authority="System",
        occurred_at_utc="2026-09-03T20:00:00Z",
        display_timezone="America/New_York",
        workstation_session="formula-test",
        application_version="test",
        action_method="Automated Test",
        reason_code="FORMULA_VALIDATION",
    )


class FormulaParserTests(unittest.TestCase):
    def test_exact_arithmetic_precedence_and_percent(self) -> None:
        result = analyze_formula(
            "=SUM(A1,B2*2)*(1+C3%)",
            {
                "A1": ExactDecimal.parse("10.10"),
                "B2": ExactDecimal.parse("2.25"),
                "C3": ExactDecimal.parse("5"),
            },
        )
        self.assertEqual(result.parse_status, "Parsed")
        self.assertEqual(result.dependencies, ("A1", "B2", "C3"))
        self.assertEqual(result.exact_result.as_decimal(), Decimal("15.33"))

    def test_missing_dependency_and_unsupported_function_are_explicit(self) -> None:
        missing = analyze_formula("=A1+B2", {"A1": ExactDecimal.parse("1")})
        unsupported = analyze_formula("=VLOOKUP(A1,B1,2)", {"A1": ExactDecimal.parse("1")})
        self.assertEqual(missing.parse_status, "Missing Dependency")
        self.assertIn("B2", missing.error)
        self.assertEqual(unsupported.parse_status, "Unsupported")
        self.assertIn("VLOOKUP", unsupported.error)


class FormulaPersistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / "database" / "tests")
        self.database_path = Path(self.temp.name) / "formulas.db"
        populate(self.database_path, PROFILES["smoke"])
        self.connection = connect(self.database_path)
        self.observation_id, self.worksheet_id = self.connection.execute(
            """SELECT po.observation_id, so.worksheet_id
               FROM pbd_observation po
               JOIN source_occurrence so ON so.occurrence_id = po.occurrence_id
               LIMIT 1"""
        ).fetchone()
        self.rule_id = self.connection.execute(
            "SELECT rule_version_id FROM rule_version WHERE rule_domain = 'calculation'"
        ).fetchone()[0]

    def tearDown(self) -> None:
        self.connection.close()
        self.temp.cleanup()

    def _source_formula(self, source_id: str, cell: str, formula: str) -> None:
        self.connection.execute(
            """INSERT INTO source_datum
               (source_datum_id, worksheet_id, cell_or_range, formula_text,
                recorded_at_utc)
               VALUES (?, ?, ?, ?, '2026-09-03T20:00:00Z')""",
            (source_id, self.worksheet_id, cell, formula),
        )

    def test_reconciliation_and_exception_are_immutable_evidence(self) -> None:
        self._source_formula("formula-source-1", "Z90", "=A1+B1")
        _, reconciled_id, _ = persist_formula_integrity(
            self.connection,
            observation_id=self.observation_id,
            source_datum_id="formula-source-1",
            submitted_formula="=A1+B1",
            cell_values={"A1": ExactDecimal.parse("5.00"), "B1": ExactDecimal.parse("4.875")},
            expected_value=ExactDecimal.parse("9.875"),
            parser_rule_version_id=self.rule_id,
            integrity_rule_version_id=self.rule_id,
            currency_id="USD",
            normalized_unit_id="USD/PART",
            recorded_at_utc="2026-09-03T20:00:00Z",
            audit=audit(),
        )
        classification = self.connection.execute(
            "SELECT classification FROM formula_integrity_event WHERE formula_integrity_event_id = ?",
            (reconciled_id,),
        ).fetchone()[0]
        self.assertEqual(classification, "Reconciled")

        self._source_formula("formula-source-2", "Z91", "=A1+B1")
        _, exception_id, _ = persist_formula_integrity(
            self.connection,
            observation_id=self.observation_id,
            source_datum_id="formula-source-2",
            submitted_formula="=A1+B1",
            cell_values={"A1": ExactDecimal.parse("5.00"), "B1": ExactDecimal.parse("4.875")},
            expected_value=ExactDecimal.parse("10.00"),
            parser_rule_version_id=self.rule_id,
            integrity_rule_version_id=self.rule_id,
            currency_id="USD",
            normalized_unit_id="USD/PART",
            recorded_at_utc="2026-09-03T20:00:01Z",
            audit=audit(),
        )
        exception = self.connection.execute(
            """SELECT classification, variance_coefficient, variance_scale
               FROM formula_integrity_event WHERE formula_integrity_event_id = ?""",
            (exception_id,),
        ).fetchone()
        self.assertEqual(tuple(exception), ("Formula Reconciliation Exception", "125", 3))
        self.assertEqual(verify_audit_chain(self.connection), [])

    def test_formula_must_match_source_evidence(self) -> None:
        self._source_formula("formula-source-3", "Z92", "=A1+B1")
        with self.assertRaisesRegex(ValueError, "does not match"):
            persist_formula_integrity(
                self.connection,
                observation_id=self.observation_id,
                source_datum_id="formula-source-3",
                submitted_formula="=A1-B1",
                cell_values={"A1": ExactDecimal.parse("5"), "B1": ExactDecimal.parse("1")},
                expected_value=ExactDecimal.parse("4"),
                parser_rule_version_id=self.rule_id,
                integrity_rule_version_id=self.rule_id,
                currency_id="USD",
                normalized_unit_id="USD/PART",
                recorded_at_utc="2026-09-03T20:00:00Z",
                audit=audit(),
            )
        self.assertEqual(self.connection.execute("SELECT COUNT(*) FROM formula_evidence").fetchone()[0], 0)

    def test_external_formula_preserves_text_and_records_unavailable_dependency(self) -> None:
        formula = "='[Supplier Inputs.xlsx]Rates'!A1*B2"
        self._source_formula("external-formula-source", "Z93", formula)
        formula_id, _, analysis = persist_formula_integrity(
            self.connection, observation_id=self.observation_id,
            source_datum_id="external-formula-source", submitted_formula=formula,
            cell_values={}, expected_value=None,
            parser_rule_version_id=self.rule_id,
            integrity_rule_version_id=self.rule_id,
            currency_id="USD", normalized_unit_id="USD/PART",
            recorded_at_utc="2026-09-03T20:00:00Z", audit=audit(),
        )
        self.assertEqual(analysis.parse_status, "Unsupported")
        self.assertEqual(self.connection.execute(
            "SELECT dependency_status FROM v_current_formula_dependency_status WHERE formula_evidence_id = ?",
            (formula_id,),
        ).fetchone()[0], "External Dependency Unverified")
        record_formula_dependency_status(
            self.connection, formula_evidence_id=formula_id,
            dependency_status="External Dependency Unavailable",
            dependency_reference="Supplier Inputs.xlsx",
            status_detail="Referenced workbook was not supplied with the PBD",
            checked_by_user_id="buyer-1", recorded_at_utc="2026-09-03T20:00:01Z",
            audit=audit(),
        )
        stored = self.connection.execute(
            """SELECT evidence.submitted_formula_text, status.dependency_status
               FROM formula_evidence evidence JOIN v_current_formula_dependency_status status
                 ON status.formula_evidence_id = evidence.formula_evidence_id
               WHERE evidence.formula_evidence_id = ?""", (formula_id,),
        ).fetchone()
        self.assertEqual(tuple(stored), (formula, "External Dependency Unavailable"))
        self.assertEqual(verify_audit_chain(self.connection), [])


if __name__ == "__main__":
    unittest.main()
