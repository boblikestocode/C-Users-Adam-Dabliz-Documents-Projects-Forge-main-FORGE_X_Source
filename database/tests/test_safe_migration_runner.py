from __future__ import annotations

import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from database.migration_runner import (
    DEFAULT_MIGRATIONS, apply_migrations, database_hash, safe_apply_migrations,
)


ROOT = Path(__file__).resolve().parents[2]


class SafeMigrationRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / "database" / "tests")
        self.base = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _copy_migrations(self, destination: Path, maximum: str | None = None) -> None:
        destination.mkdir()
        for source in sorted(DEFAULT_MIGRATIONS.glob("[0-9][0-9][0-9][0-9]_*.sql")):
            if maximum is None or source.name[:4] <= maximum:
                shutil.copy2(source, destination / source.name)

    def test_existing_database_gets_recovery_checkpoint_and_execution_evidence(self) -> None:
        old_migrations = self.base / "old-migrations"
        self._copy_migrations(old_migrations, "0025")
        database = self.base / "existing.db"
        apply_migrations(database, old_migrations)
        result = safe_apply_migrations(
            database, recovery_dir=self.base / "recovery",
        )
        self.assertEqual(sum(item.status == "Applied" for item in result.migrations), 26)
        self.assertIsNotNone(result.recovery_checkpoint_path)
        self.assertTrue(result.recovery_checkpoint_path.exists())
        self.assertEqual(result.recovery_checkpoint_hash,
                         database_hash(result.recovery_checkpoint_path))
        recovery_connection = sqlite3.connect(result.recovery_checkpoint_path)
        try:
            self.assertEqual(recovery_connection.execute(
                "SELECT MAX(migration_version) FROM schema_migration"
            ).fetchone()[0], "0025")
            self.assertIsNone(recovery_connection.execute(
                """SELECT 1 FROM sqlite_master
                   WHERE type = 'table' AND name = 'schema_migration_execution'"""
            ).fetchone())
        finally:
            recovery_connection.close()
        self.assertIsNotNone(result.execution_id)
        second = safe_apply_migrations(database, recovery_dir=self.base / "recovery")
        self.assertIsNone(second.recovery_checkpoint_path)
        self.assertIsNone(second.execution_id)

    def test_failed_dry_run_does_not_touch_target_database(self) -> None:
        database = self.base / "current.db"
        apply_migrations(database)
        before_hash = database_hash(database)
        broken = self.base / "broken-migrations"
        self._copy_migrations(broken)
        (broken / "0027_broken.sql").write_text(
            "BEGIN IMMEDIATE; THIS IS NOT SQL; "
            "SELECT '__FORGE_MIGRATION_SHA256__'; COMMIT;",
            encoding="utf-8",
        )
        with self.assertRaises(Exception):
            safe_apply_migrations(database, migrations_dir=broken,
                                  recovery_dir=self.base / "recovery")
        self.assertEqual(database_hash(database), before_hash)
        self.assertFalse((self.base / "recovery").exists())


if __name__ == "__main__":
    unittest.main()
