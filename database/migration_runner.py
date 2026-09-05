from __future__ import annotations

import argparse
import hashlib
import sqlite3
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


CHECKSUM_TOKEN = "__FORGE_MIGRATION_SHA256__"
DEFAULT_MIGRATIONS = Path(__file__).resolve().parent / "migrations"


@dataclass(frozen=True)
class MigrationResult:
    version: str
    path: Path
    checksum: str
    status: str


@dataclass(frozen=True)
class SafeMigrationResult:
    migrations: tuple[MigrationResult, ...]
    preflight_database_hash: str
    recovery_checkpoint_path: Path | None
    recovery_checkpoint_hash: str | None
    execution_id: str | None


def migration_files(migrations_dir: Path) -> list[Path]:
    files = sorted(migrations_dir.glob("[0-9][0-9][0-9][0-9]_*.sql"))
    versions = [path.name.split("_", 1)[0] for path in files]
    if len(versions) != len(set(versions)):
        raise ValueError("Migration versions must be unique")
    return files


def file_checksum(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def database_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def backup_database(source: Path, destination: Path) -> None:
    if destination.exists():
        raise FileExistsError(f"Refusing to overwrite database backup: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_connection = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    destination_connection = sqlite3.connect(destination)
    try:
        source_connection.backup(destination_connection)
    finally:
        destination_connection.close()
        source_connection.close()


def _current_and_pending(database_path: Path, migrations_dir: Path) -> tuple[str | None, int]:
    if not database_path.exists():
        return None, len(migration_files(migrations_dir))
    connection = sqlite3.connect(f"file:{database_path}?mode=ro", uri=True)
    try:
        applied: dict[str, str] = {}
        if connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_migration'"
        ).fetchone():
            applied = dict(connection.execute(
                "SELECT migration_version, migration_checksum FROM schema_migration"
            ))
        files = migration_files(migrations_dir)
        for path in files:
            version = path.name.split("_", 1)[0]
            if version in applied and applied[version] != file_checksum(path):
                raise RuntimeError(f"Applied migration {version} checksum mismatch")
        packaged = {path.name.split("_", 1)[0] for path in files}
        missing = sorted(set(applied) - packaged)
        if missing:
            raise RuntimeError(f"Applied migration files are missing: {', '.join(missing)}")
        return (max(applied) if applied else None,
                sum(path.name.split("_", 1)[0] not in applied for path in files))
    finally:
        connection.close()


def safe_apply_migrations(
    database_path: Path,
    migrations_dir: Path = DEFAULT_MIGRATIONS,
    recovery_dir: Path | None = None,
) -> SafeMigrationResult:
    """Dry-run the entire chain, checkpoint existing state, apply, and verify."""
    database_path = database_path.resolve()
    database_path.parent.mkdir(parents=True, exist_ok=True)
    from_version, pending_count = _current_and_pending(database_path, migrations_dir)
    started = datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
    with tempfile.TemporaryDirectory(dir=database_path.parent) as temp_dir:
        preflight_path = Path(temp_dir) / "preflight.db"
        if database_path.exists():
            backup_database(database_path, preflight_path)
        apply_migrations(preflight_path, migrations_dir)
        failures = validate_database(preflight_path)
        if failures:
            raise RuntimeError(f"Migration preflight integrity failed: {failures}")
        preflight_hash = database_hash(preflight_path)

    if pending_count == 0:
        return SafeMigrationResult(tuple(apply_migrations(database_path, migrations_dir)),
                                   preflight_hash, None, None, None)
    recovery_path: Path | None = None
    recovery_hash: str | None = None
    if database_path.exists():
        destination_dir = (recovery_dir or database_path.parent / ".forge-migration-recovery").resolve()
        recovery_path = destination_dir / (
            f"{database_path.stem}-before-{from_version or 'unversioned'}-{uuid.uuid4().hex}.db"
        )
        backup_database(database_path, recovery_path)
        recovery_hash = database_hash(recovery_path)

    results = apply_migrations(database_path, migrations_dir)
    failures = validate_database(database_path)
    if failures:
        raise RuntimeError(
            f"Post-migration integrity failed; recovery checkpoint={recovery_path}: {failures}"
        )
    applied_count = sum(result.status == "Applied" for result in results)
    execution_id: str | None = None
    if applied_count and results[-1].version >= "0026":
        execution_id = str(uuid.uuid4())
        completed = datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
        connection = sqlite3.connect(database_path)
        try:
            connection.execute(
                """INSERT INTO schema_migration_execution VALUES
                   (?, ?, ?, ?, ?, ?, ?, 'Verified', ?, ?)""",
                (execution_id, from_version, results[-1].version, applied_count,
                 preflight_hash, None if recovery_path is None else str(recovery_path),
                 recovery_hash, started, completed),
            )
            connection.commit()
        finally:
            connection.close()
    return SafeMigrationResult(tuple(results), preflight_hash, recovery_path,
                               recovery_hash, execution_id)


def applied_migration(connection: sqlite3.Connection, version: str) -> tuple[str] | None:
    exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_migration'"
    ).fetchone()
    if not exists:
        return None
    return connection.execute(
        "SELECT migration_checksum FROM schema_migration WHERE migration_version = ?",
        (version,),
    ).fetchone()


def apply_migrations(
    database_path: Path,
    migrations_dir: Path = DEFAULT_MIGRATIONS,
) -> list[MigrationResult]:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    results: list[MigrationResult] = []
    connection = sqlite3.connect(database_path)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        for path in migration_files(migrations_dir):
            version = path.name.split("_", 1)[0]
            checksum = file_checksum(path)
            prior = applied_migration(connection, version)
            if prior:
                if prior[0] != checksum:
                    raise RuntimeError(
                        f"Applied migration {version} checksum mismatch: "
                        f"database={prior[0]} file={checksum}"
                    )
                results.append(MigrationResult(version, path, checksum, "Already Applied"))
                continue

            sql = path.read_text(encoding="utf-8")
            if CHECKSUM_TOKEN not in sql:
                raise RuntimeError(f"Migration {path.name} is missing its checksum token")
            connection.executescript(sql.replace(CHECKSUM_TOKEN, checksum))
            stored = applied_migration(connection, version)
            if not stored or stored[0] != checksum:
                raise RuntimeError(f"Migration {version} did not record its verified checksum")
            results.append(MigrationResult(version, path, checksum, "Applied"))
    finally:
        connection.close()
    return results


def validate_database(database_path: Path) -> list[str]:
    connection = sqlite3.connect(f"file:{database_path}?mode=ro", uri=True)
    try:
        integrity = connection.execute("PRAGMA integrity_check").fetchall()
        foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
    finally:
        connection.close()
    failures = [row[0] for row in integrity if row[0] != "ok"]
    failures.extend(f"foreign_key:{row}" for row in foreign_keys)
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply and verify Forge X database migrations")
    parser.add_argument("database", type=Path, help="Path to the commodity SQLite/SQLCipher database")
    parser.add_argument("--migrations", type=Path, default=DEFAULT_MIGRATIONS)
    args = parser.parse_args()

    safe_result = safe_apply_migrations(args.database, args.migrations)
    for result in safe_result.migrations:
        print(f"{result.version}: {result.status} ({result.checksum})")
    failures = validate_database(args.database)
    if failures:
        for failure in failures:
            print(f"INTEGRITY FAILURE: {failure}")
        return 1
    print("Integrity check: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
