from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from database.migration_runner import apply_migrations, validate_database


ROOT = Path(__file__).resolve().parents[2]
REGISTRY_MIGRATIONS = ROOT / "database" / "registry_migrations"


class RegistryCoreSchemaTests(unittest.TestCase):
    def test_registry_migration_is_repeatable_and_valid(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT / "database" / "tests") as temp_dir:
            path = Path(temp_dir) / "registry.db"
            first = apply_migrations(path, REGISTRY_MIGRATIONS)
            second = apply_migrations(path, REGISTRY_MIGRATIONS)
            self.assertEqual(first[0].status, "Applied")
            self.assertEqual(second[0].status, "Already Applied")
            self.assertEqual(validate_database(path), [])

    def test_active_identity_uniqueness_and_audit_immutability(self) -> None:
        connection = sqlite3.connect(":memory:")
        connection.execute("PRAGMA foreign_keys = ON")
        migration = REGISTRY_MIGRATIONS / "0001_registry_core.sql"
        connection.executescript(migration.read_text(encoding="utf-8"))
        try:
            base = (
                "commodity-v1", "commodity-stable", "C100", "Interior Trim",
                "INTERIOR TRIM", "Active", "2026-09-02", None, "master",
                "2026-09-02T00:00:00Z", None,
            )
            connection.execute(
                "INSERT INTO commodity VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                base,
            )
            duplicate = list(base)
            duplicate[0] = "commodity-v2"
            duplicate[1] = "commodity-other"
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "INSERT INTO commodity VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    duplicate,
                )

            connection.execute(
                """INSERT INTO registry_audit_event
                   (audit_event_id, recorded_sequence, event_type, occurred_at_utc,
                    application_version, reason_code, event_payload, event_hash)
                   VALUES ('audit-1', 1, 'Registry Created',
                           '2026-09-02T00:00:00Z', 'test', 'SYSTEM', '{}', 'hash-1')"""
            )
            with self.assertRaisesRegex(sqlite3.IntegrityError, "registry_audit_event is immutable"):
                connection.execute(
                    "UPDATE registry_audit_event SET event_type = 'Changed' WHERE audit_event_id = 'audit-1'"
                )
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
