from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from decimal import Decimal, DivisionByZero, InvalidOperation
from typing import Any

from .audit import AuditContext, append_audit_event, canonical_json
from .connection import immediate_transaction
from .decimals import ExactDecimal
from .ids import uuid7


TOKEN = re.compile(
    r"\s*(?:(?P<number>(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][+-]?\d+)?)|"
    r"(?P<cell>\$?[A-Za-z]{1,3}\$?\d+)|(?P<name>[A-Za-z_][A-Za-z0-9_.]*)|"
    r"(?P<op>[+\-*/%^(),]))"
)


class FormulaError(ValueError):
    pass


class UnsupportedFormula(FormulaError):
    pass


class MissingDependency(FormulaError):
    pass


@dataclass(frozen=True)
class FormulaAnalysis:
    parse_status: str
    parsed_expression: str | None
    dependencies: tuple[str, ...]
    exact_result: ExactDecimal | None
    error: str | None


class Parser:
    def __init__(self, formula: str):
        text = formula.strip()
        if text.startswith("="):
            text = text[1:]
        self.tokens: list[tuple[str, str]] = []
        position = 0
        while position < len(text):
            match = TOKEN.match(text, position)
            if not match:
                raise UnsupportedFormula(f"Unsupported token at position {position + 1}")
            kind = next(name for name, value in match.groupdict().items() if value is not None)
            self.tokens.append((kind, match.group(kind)))
            position = match.end()
        self.position = 0

    def current(self) -> tuple[str, str] | None:
        return self.tokens[self.position] if self.position < len(self.tokens) else None

    def accept(self, value: str) -> bool:
        token = self.current()
        if token and token[1] == value:
            self.position += 1
            return True
        return False

    def parse(self) -> Any:
        if not self.tokens:
            raise FormulaError("Formula is blank")
        node = self.expression()
        if self.current() is not None:
            raise UnsupportedFormula(f"Unexpected token {self.current()[1]!r}")
        return node

    def expression(self) -> Any:
        node = self.term()
        while self.current() and self.current()[1] in ("+", "-"):
            operator = self.current()[1]
            self.position += 1
            node = (operator, node, self.term())
        return node

    def term(self) -> Any:
        node = self.power()
        while self.current() and self.current()[1] in ("*", "/"):
            operator = self.current()[1]
            self.position += 1
            node = (operator, node, self.power())
        return node

    def power(self) -> Any:
        node = self.factor()
        if self.accept("^"):
            node = ("^", node, self.power())
        return node

    def factor(self) -> Any:
        if self.accept("+"):
            return ("unary+", self.factor())
        if self.accept("-"):
            return ("unary-", self.factor())
        node = self.primary()
        while self.accept("%"):
            node = ("percent", node)
        return node

    def primary(self) -> Any:
        token = self.current()
        if token is None:
            raise FormulaError("Unexpected end of formula")
        if token[0] == "number":
            self.position += 1
            return ("number", token[1])
        if token[0] == "cell":
            self.position += 1
            return ("cell", token[1].replace("$", "").upper())
        if token[0] == "name":
            self.position += 1
            name = token[1].upper()
            if not self.accept("("):
                raise UnsupportedFormula(f"Named ranges are not supported: {name}")
            arguments: list[Any] = []
            if not self.accept(")"):
                while True:
                    arguments.append(self.expression())
                    if self.accept(")"):
                        break
                    if not self.accept(","):
                        raise FormulaError("Expected ',' or ')' in function")
            if name not in ("SUM", "MIN", "MAX"):
                raise UnsupportedFormula(f"Unsupported function: {name}")
            return ("function", name, arguments)
        if self.accept("("):
            node = self.expression()
            if not self.accept(")"):
                raise FormulaError("Missing closing parenthesis")
            return node
        raise UnsupportedFormula(f"Unsupported token: {token[1]}")


def _dependencies(node: Any) -> set[str]:
    if node[0] == "cell":
        return {node[1]}
    result: set[str] = set()
    for child in node[1:]:
        if isinstance(child, tuple):
            result.update(_dependencies(child))
        elif isinstance(child, list):
            for item in child:
                result.update(_dependencies(item))
    return result


def _evaluate(node: Any, cells: dict[str, Decimal]) -> Decimal:
    operation = node[0]
    if operation == "number":
        return Decimal(node[1])
    if operation == "cell":
        if node[1] not in cells:
            raise MissingDependency(f"Missing cell value: {node[1]}")
        return cells[node[1]]
    if operation == "unary+":
        return _evaluate(node[1], cells)
    if operation == "unary-":
        return -_evaluate(node[1], cells)
    if operation == "percent":
        return _evaluate(node[1], cells) / Decimal(100)
    if operation in ("+", "-", "*", "/", "^"):
        left, right = _evaluate(node[1], cells), _evaluate(node[2], cells)
        if operation == "+":
            return left + right
        if operation == "-":
            return left - right
        if operation == "*":
            return left * right
        if operation == "/":
            return left / right
        if right != right.to_integral_value():
            raise UnsupportedFormula("Non-integral exponents are not supported")
        return left ** int(right)
    if operation == "function":
        values = [_evaluate(argument, cells) for argument in node[2]]
        if not values:
            raise FormulaError(f"{node[1]} requires at least one argument")
        if node[1] == "SUM":
            return sum(values, Decimal(0))
        if node[1] == "MIN":
            return min(values)
        return max(values)
    raise UnsupportedFormula(f"Unsupported parsed operation: {operation}")


def analyze_formula(formula: str, cell_values: dict[str, ExactDecimal]) -> FormulaAnalysis:
    try:
        node = Parser(formula).parse()
        dependencies = tuple(sorted(_dependencies(node)))
        normalized_cells = {
            coordinate.replace("$", "").upper(): value.as_decimal()
            for coordinate, value in cell_values.items()
        }
        result = _evaluate(node, normalized_cells)
        exact = ExactDecimal.parse(format(result.normalize(), "f"))
        return FormulaAnalysis("Parsed", canonical_json(node), dependencies, exact, None)
    except MissingDependency as error:
        return FormulaAnalysis("Missing Dependency", None, (), None, str(error))
    except UnsupportedFormula as error:
        return FormulaAnalysis("Unsupported", None, (), None, str(error))
    except (FormulaError, DivisionByZero, InvalidOperation, ZeroDivisionError) as error:
        return FormulaAnalysis("Invalid", None, (), None, str(error))


def persist_formula_integrity(
    connection: sqlite3.Connection,
    *,
    observation_id: str,
    source_datum_id: str,
    submitted_formula: str,
    cell_values: dict[str, ExactDecimal],
    expected_value: ExactDecimal | None,
    parser_rule_version_id: str,
    integrity_rule_version_id: str,
    currency_id: str | None,
    normalized_unit_id: str | None,
    recorded_at_utc: str,
    audit: AuditContext,
) -> tuple[str, str, FormulaAnalysis]:
    analysis = analyze_formula(submitted_formula, cell_values)
    formula_id, integrity_id = uuid7(), uuid7()
    if analysis.parse_status == "Parsed" and expected_value is not None:
        classification = (
            "Reconciled"
            if analysis.exact_result.governing_1e4() == expected_value.governing_1e4()
            else "Formula Reconciliation Exception"
        )
    else:
        classification = "Insufficient Evidence"
    variance = None
    if analysis.exact_result is not None and expected_value is not None:
        variance_decimal = expected_value.as_decimal() - analysis.exact_result.as_decimal()
        variance = ExactDecimal.parse(format(variance_decimal.normalize(), "f"))
    with immediate_transaction(connection):
        source = connection.execute(
            """SELECT formula_text FROM source_datum
               WHERE source_datum_id = ? AND worksheet_id IN (
                   SELECT so.worksheet_id FROM pbd_observation po
                   JOIN source_occurrence so ON so.occurrence_id = po.occurrence_id
                   WHERE po.observation_id = ?)""",
            (source_datum_id, observation_id),
        ).fetchone()
        if source is None:
            raise ValueError("Formula source datum does not belong to the observation worksheet")
        if source[0] is not None and source[0].strip() != submitted_formula.strip():
            raise ValueError("Submitted formula does not match immutable source evidence")
        connection.execute(
            """INSERT INTO formula_evidence
               (formula_evidence_id, observation_id, source_datum_id,
                submitted_formula_text, parsed_expression,
                parser_rule_version_id, parse_status, recorded_at_utc)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                formula_id, observation_id, source_datum_id,
                submitted_formula, analysis.parsed_expression,
                parser_rule_version_id, analysis.parse_status,
                recorded_at_utc,
            ),
        )
        external_formula = "[" in submitted_formula and "]" in submitted_formula
        connection.execute(
            """INSERT INTO formula_dependency_status_event VALUES
               (?, ?, ?, NULL, NULL, ?, ?, NULL, ?)""",
            (uuid7(), formula_id,
             "External Dependency Unverified" if external_formula else "Internally Reproducible",
             audit.actor_user_id,
             "External formula dependency requires verification" if external_formula
             else "Formula depends only on retained internal evidence",
             recorded_at_utc),
        )
        actual = analysis.exact_result
        connection.execute(
            """INSERT INTO formula_integrity_event
               (formula_integrity_event_id, observation_id,
                formula_evidence_id, integrity_rule_version_id,
                classification, expected_coefficient, expected_scale,
                recalculated_coefficient, recalculated_scale,
                variance_coefficient, variance_scale, currency_id,
                normalized_unit_id, evidence_payload, recorded_at_utc)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                integrity_id, observation_id, formula_id,
                integrity_rule_version_id, classification,
                expected_value.coefficient if expected_value else None,
                expected_value.scale if expected_value else None,
                actual.coefficient if actual else None,
                actual.scale if actual else None,
                variance.coefficient if variance else None,
                variance.scale if variance else None,
                currency_id, normalized_unit_id,
                canonical_json({"dependencies": analysis.dependencies, "parse_error": analysis.error}),
                recorded_at_utc,
            ),
        )
        append_audit_event(
            connection, audit,
            {"formula_evidence_id": formula_id, "formula_integrity_event_id": integrity_id, "observation_id": observation_id, "parse_status": analysis.parse_status, "classification": classification},
        )
    return formula_id, integrity_id, analysis


def record_formula_dependency_status(
    connection: sqlite3.Connection, *, formula_evidence_id: str,
    dependency_status: str, dependency_reference: str,
    status_detail: str, checked_by_user_id: str,
    recorded_at_utc: str, audit: AuditContext,
    dependency_manifest_hash: str | None = None,
) -> str:
    if dependency_status not in {"External Dependency Verified", "External Dependency Unavailable"}:
        raise ValueError("External dependency status must be Verified or Unavailable")
    if not dependency_reference.strip() or not status_detail.strip() or not checked_by_user_id.strip():
        raise ValueError("External dependency decision requires reference, detail, and user")
    if dependency_status == "External Dependency Verified":
        if dependency_manifest_hash is None or len(dependency_manifest_hash) != 64:
            raise ValueError("Verified external dependency requires a manifest SHA-256")
        try:
            int(dependency_manifest_hash, 16)
        except ValueError as error:
            raise ValueError("Dependency manifest SHA-256 is not hexadecimal") from error
    elif dependency_manifest_hash is not None:
        raise ValueError("Unavailable external dependency cannot have a manifest hash")
    event_id = uuid7()
    with immediate_transaction(connection):
        formula = connection.execute(
            "SELECT submitted_formula_text FROM formula_evidence WHERE formula_evidence_id = ?",
            (formula_evidence_id,),
        ).fetchone()
        if formula is None:
            raise ValueError("Formula evidence does not exist")
        if "[" not in formula[0] or "]" not in formula[0]:
            raise ValueError("Formula does not contain an external dependency")
        current = connection.execute(
            "SELECT formula_dependency_status_event_id FROM v_current_formula_dependency_status WHERE formula_evidence_id = ?",
            (formula_evidence_id,),
        ).fetchone()
        connection.execute(
            "INSERT INTO formula_dependency_status_event VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (event_id, formula_evidence_id, dependency_status, dependency_reference.strip(),
             dependency_manifest_hash, checked_by_user_id.strip(), status_detail.strip(),
             current[0], recorded_at_utc),
        )
        append_audit_event(connection, audit, {"formula_dependency_status_event_id": event_id,
            "formula_evidence_id": formula_evidence_id, "dependency_status": dependency_status,
            "dependency_reference": dependency_reference.strip()})
    return event_id
