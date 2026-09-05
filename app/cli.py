from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from typing import Sequence

from database.migration_runner import safe_apply_migrations
from database.services.connection import connect
from database.services.integrity import assess_analysis_readiness
from database.services.operations import operational_status
from database.services.workflow import assess_event_workflow
from database.services.supplier_rates import derive_supplier_rate_distributions


def health_payload(database: Path) -> dict[str, object]:
    connection = connect(database, read_only=True)
    try:
        readiness = assess_analysis_readiness(connection)
        return {
            "database": str(database),
            "ready": readiness.can_proceed,
            "blocking_findings": [asdict(item) for item in readiness.blocking_findings],
            "advisory_findings": [asdict(item) for item in readiness.advisory_findings],
        }
    finally:
        connection.close()


def event_status_payload(database: Path, event_id: str) -> dict[str, object]:
    connection = connect(database, read_only=True)
    try:
        return asdict(assess_event_workflow(connection, event_id=event_id))
    finally:
        connection.close()


def operational_status_payload(database: Path) -> dict[str, object]:
    connection = connect(database, read_only=True)
    try:
        return operational_status(connection)
    finally:
        connection.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Forge X governed workflow commands")
    commands = parser.add_subparsers(dest="command", required=True)
    migrate = commands.add_parser("migrate", help="Apply verified database migrations")
    migrate.add_argument("database", type=Path)
    health = commands.add_parser("health", help="Run the database analysis-readiness gate")
    health.add_argument("database", type=Path)
    event = commands.add_parser("event-status", help="Assess an event's workflow readiness")
    event.add_argument("database", type=Path)
    event.add_argument("event_id")
    status = commands.add_parser("status", help="Show local health and publication continuity")
    status.add_argument("database", type=Path)
    rates = commands.add_parser("supplier-rates", help="Inspect same-year regional supplier rate distributions")
    rates.add_argument("database", type=Path)
    rates.add_argument("supplier_id")
    rates.add_argument("commodity_id")
    rates.add_argument("region_code")
    rates.add_argument("--cutoff", required=True, help="Evidence cutoff in UTC")
    rates.add_argument("--plant", default=None)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "migrate":
        safe_result = safe_apply_migrations(args.database)
        payload: dict[str, object] = {
            "database": str(args.database),
            "migrations": [asdict(item) | {"path": str(item.path)} for item in safe_result.migrations],
            "preflight_database_hash": safe_result.preflight_database_hash,
            "recovery_checkpoint_path": None if safe_result.recovery_checkpoint_path is None else str(safe_result.recovery_checkpoint_path),
            "recovery_checkpoint_hash": safe_result.recovery_checkpoint_hash,
            "migration_execution_id": safe_result.execution_id,
        }
        exit_code = 0
    elif args.command == "health":
        payload = health_payload(args.database)
        exit_code = 0 if payload["ready"] else 1
    elif args.command == "event-status":
        payload = event_status_payload(args.database, args.event_id)
        exit_code = 0 if payload["analysis_ready"] else 1
    elif args.command == "supplier-rates":
        connection = connect(args.database, read_only=True)
        try:
            payload = derive_supplier_rate_distributions(
                connection, supplier_id=args.supplier_id, commodity_id=args.commodity_id,
                region_code=args.region_code, evidence_cutoff_utc=args.cutoff,
                supplier_plant_id=args.plant,
            )
        finally:
            connection.close()
        exit_code = 0
    else:
        payload = operational_status_payload(args.database)
        exit_code = 0 if payload["database_health"]["ready"] else 1
    print(json.dumps(payload, indent=2, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
