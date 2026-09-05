from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import statistics
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Sequence


@dataclass(frozen=True)
class BenchmarkResult:
    query_id: str
    iterations: int
    rows: int
    median_ms: float
    p95_ms: float
    max_ms: float
    target_ms: float
    within_target: bool
    plan_fingerprint: str
    plan_steps: tuple[str, ...]


@dataclass(frozen=True)
class QuerySpec:
    query_id: str
    sql: str
    values: tuple[str, ...]
    target_ms: float
    minimum_rows: int = 0


def percentile(values: Sequence[float], fraction: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(len(ordered) * fraction + 0.999999) - 1))
    return ordered[index]


def parameters(connection: sqlite3.Connection) -> dict[str, str]:
    part_id = connection.execute(
        "SELECT part_id FROM pbd_observation WHERE part_id IS NOT NULL LIMIT 1"
    ).fetchone()[0]
    supplier_id = connection.execute(
        "SELECT supplier_id FROM pbd_observation WHERE supplier_id IS NOT NULL LIMIT 1"
    ).fetchone()[0]
    event_id = connection.execute("SELECT event_id FROM sourcing_event LIMIT 1").fetchone()[0]
    transaction_id = connection.execute(
        "SELECT import_transaction_id FROM import_transaction LIMIT 1"
    ).fetchone()[0]
    observation_id = connection.execute(
        "SELECT observation_id FROM pbd_observation LIMIT 1"
    ).fetchone()[0]
    fingerprint = connection.execute(
        """SELECT digest FROM fingerprint
           WHERE entity_type = 'Source Workbook' LIMIT 1"""
    ).fetchone()[0]
    return {
        "part": part_id,
        "supplier": supplier_id,
        "event": event_id,
        "transaction": transaction_id,
        "observation": observation_id,
        "fingerprint": fingerprint,
    }


def query_catalog(params: dict[str, str]) -> list[QuerySpec]:
    return [
        QuerySpec(
            "Q01_SOURCE_PACKAGE",
            """SELECT sp.source_package_id, sp.event_id, se.readable_name
               FROM source_package sp JOIN sourcing_event se ON se.event_id = sp.event_id
               WHERE sp.normalized_package_number = 'SP-SYNTHETIC-001'""",
            (), 200, 1,
        ),
        QuerySpec(
            "Q02_EVENT_SUMMARY",
            """SELECT summary.event_id, summary.source_package_number,
                      summary.event_name, summary.event_status,
                      summary.supplier_count, summary.open_action_count
               FROM event_summary_projection summary
               JOIN event_summary_generation_manifest generation
                 ON generation.projection_generation_id = summary.projection_generation_id
               WHERE summary.buyer_code_id = 'BUYER-001'
                 AND generation.projection_generation_id = (
                     SELECT projection_generation_id
                     FROM event_summary_generation_manifest
                     ORDER BY evidence_cutoff_utc DESC,
                              projection_generation_id DESC LIMIT 1)
               ORDER BY summary.latest_activity_at_utc DESC, summary.event_id""",
            (), 2000, 1,
        ),
        QuerySpec(
            "Q03_EXACT_PART_HISTORY",
            """SELECT observation_id, supplier_id, supplier_plant_id, economic_date
               FROM pbd_observation
               WHERE part_id = ? ORDER BY economic_date DESC, observation_id""",
            (params["part"],), 2000, 1,
        ),
        QuerySpec(
            "Q04_ACTIVE_SUPPLIER_ROUNDS",
            """SELECT supplier_id, quote_round_id, recorded_at_utc
               FROM v_active_supplier_round WHERE event_id = ?""",
            (params["event"],), 2000, 1,
        ),
        QuerySpec(
            "Q05_COMMON_PART_MATRIX",
            """SELECT membership.event_part_id, active.supplier_id,
                      membership.membership_status, datum.decimal_coefficient,
                      datum.decimal_scale
               FROM v_active_supplier_round active
               JOIN round_observation membership
                 ON membership.quote_round_id = active.quote_round_id
               LEFT JOIN submitted_datum datum
                 ON datum.observation_id = membership.observation_id
                AND datum.field_code = 'PIECE_PRICE'
               WHERE active.event_id = ?
               ORDER BY membership.event_part_id, active.supplier_id""",
            (params["event"],), 2000, 1,
        ),
        QuerySpec(
            "Q06_OBSERVATION_LINEAGE",
            """SELECT sd.field_code, sd.submitted_lexeme, src.cell_or_range,
                      ws.submitted_name, wb.submitted_filename
               FROM submitted_datum sd
               JOIN source_datum src ON src.source_datum_id = sd.source_datum_id
               JOIN source_worksheet ws ON ws.worksheet_id = src.worksheet_id
               JOIN source_workbook wb ON wb.workbook_id = ws.workbook_id
               WHERE sd.observation_id = ?""",
            (params["observation"],), 2000, 1,
        ),
        QuerySpec(
            "Q07_SUPPLIER_ACTIVITY_TIMELINE",
            """SELECT activity_type, event_part_id, quote_round_id, occurred_at_utc
               FROM supplier_activity WHERE supplier_id = ?
               ORDER BY occurred_at_utc DESC, supplier_activity_id LIMIT 500""",
            (params["supplier"],), 2000, 1,
        ),
        QuerySpec(
            "Q08_KNOWLEDGE_AS_OF",
            """SELECT version.knowledge_version_id, version.knowledge_level,
                      version.structured_payload
               FROM knowledge_version version
               WHERE version.status = 'Active'
                 AND version.recorded_from_utc <= '2026-12-31T23:59:59Z'
               ORDER BY version.recorded_from_utc DESC,
                        version.knowledge_version_id LIMIT 100""",
            (), 500,
        ),
        QuerySpec(
            "Q09_LATEST_SUPPLIER_PROFILE",
            """SELECT run.supplier_profile_run_id, run.supplier_id,
                      metric.metric_code, metric.decimal_coefficient,
                      metric.decimal_scale
               FROM supplier_profile_run run
               LEFT JOIN supplier_profile_metric metric
                 ON metric.supplier_profile_run_id = run.supplier_profile_run_id
               WHERE run.supplier_id = ? AND run.run_status = 'Complete'
               ORDER BY run.evidence_cutoff_utc DESC LIMIT 500""",
            (params["supplier"],), 2000,
        ),
        QuerySpec(
            "Q10_IMPORT_RECONCILIATION",
            """SELECT occurrence_count, committed_count, duplicate_count,
                      blocked_count, ignored_count, failed_count, pending_count
               FROM v_source_tab_reconciliation WHERE import_transaction_id = ?""",
            (params["transaction"],), 2000, 1,
        ),
        QuerySpec(
            "Q11_ANALYSIS_EVIDENCE_POPULATION",
            """SELECT round.quote_round_id, membership.event_part_id,
                      membership.observation_id, membership.membership_status
               FROM supplier_quote_round round
               JOIN round_observation membership
                 ON membership.quote_round_id = round.quote_round_id
               WHERE round.event_id = ?
               ORDER BY round.supplier_id, round.round_number,
                        membership.event_part_id""",
            (params["event"],), 60000, 1,
        ),
        QuerySpec(
            "Q12_FINALIZED_SNAPSHOT",
            """SELECT snapshot.finalized_snapshot_id, result.result_code,
                      result.supplier_id, result.part_id,
                      result.decimal_coefficient, result.decimal_scale
               FROM finalized_snapshot snapshot
               JOIN snapshot_result result
                 ON result.finalized_snapshot_id = snapshot.finalized_snapshot_id
               WHERE snapshot.analysis_id IN (
                   SELECT analysis_id FROM analysis WHERE event_id = ?)
               ORDER BY snapshot.finalized_at_utc DESC, result.result_code LIMIT 500""",
            (params["event"],), 2000,
        ),
        QuerySpec(
            "Q13_OPEN_BUYER_ACTIONS",
            """SELECT action.buyer_action_id, current.action_status,
                      current.owner_user_id, action.supplier_id
               FROM buyer_action action
               JOIN v_current_buyer_action current
                 ON current.buyer_action_id = action.buyer_action_id
               WHERE action.event_id = ?
                 AND current.action_status NOT IN ('Resolved', 'Accepted Exception')
               ORDER BY current.recorded_at_utc DESC, action.buyer_action_id""",
            (params["event"],), 2000,
        ),
        QuerySpec(
            "Q14_PUBLICATION_READINESS",
            """SELECT event.event_id,
                      (SELECT COUNT(*) FROM buyer_action action
                       JOIN v_current_buyer_action current
                         ON current.buyer_action_id = action.buyer_action_id
                       WHERE action.event_id = event.event_id
                         AND current.action_status NOT IN ('Resolved', 'Accepted Exception'))
               FROM sourcing_event event WHERE event.event_id = ?""",
            (params["event"],), 5000, 1,
        ),
        QuerySpec(
            "Q15_FINGERPRINT_LOOKUP",
            """SELECT entity_type, entity_id FROM fingerprint
               WHERE purpose = 'Exact File Duplicate' AND algorithm = 'SHA-256'
                 AND digest = ?""",
            (params["fingerprint"],), 200, 1,
        ),
    ]


def benchmark(database_path: Path, iterations: int = 50) -> list[BenchmarkResult]:
    connection = sqlite3.connect(f"file:{database_path}?mode=ro", uri=True)
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        params = parameters(connection)
        results: list[BenchmarkResult] = []
        for spec in query_catalog(params):
            plan_steps = tuple(str(row[3]) for row in connection.execute(
                "EXPLAIN QUERY PLAN " + spec.sql, spec.values
            ).fetchall())
            plan_fingerprint = hashlib.sha256(
                "\n".join(plan_steps).encode("utf-8")
            ).hexdigest()
            warm_rows = connection.execute(spec.sql, spec.values).fetchall()
            if len(warm_rows) < spec.minimum_rows:
                raise AssertionError(
                    f"{spec.query_id} returned {len(warm_rows)} rows; "
                    f"expected at least {spec.minimum_rows}"
                )
            timings: list[float] = []
            row_count = 0
            for _ in range(iterations):
                started = time.perf_counter_ns()
                rows = connection.execute(spec.sql, spec.values).fetchall()
                elapsed_ms = (time.perf_counter_ns() - started) / 1_000_000
                timings.append(elapsed_ms)
                row_count = len(rows)
            results.append(
                BenchmarkResult(
                    query_id=spec.query_id,
                    iterations=iterations,
                    rows=row_count,
                    median_ms=round(statistics.median(timings), 4),
                    p95_ms=round(percentile(timings, 0.95), 4),
                    max_ms=round(max(timings), 4),
                    target_ms=spec.target_ms,
                    within_target=percentile(timings, 0.95) <= spec.target_ms,
                    plan_fingerprint=plan_fingerprint,
                    plan_steps=plan_steps,
                )
            )
        return results
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark critical Forge X database queries")
    parser.add_argument("database", type=Path)
    parser.add_argument("--iterations", type=int, default=50)
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()
    if args.iterations < 1:
        parser.error("--iterations must be positive")
    results = benchmark(args.database, args.iterations)
    if args.as_json:
        print(json.dumps([asdict(result) for result in results], indent=2))
    else:
        for result in results:
            print(
                f"{result.query_id}: rows={result.rows} median={result.median_ms:.4f}ms "
                f"p95={result.p95_ms:.4f}ms max={result.max_ms:.4f}ms"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
