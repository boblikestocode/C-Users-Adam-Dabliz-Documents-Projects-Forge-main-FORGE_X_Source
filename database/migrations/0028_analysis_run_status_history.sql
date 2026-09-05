-- Forge X append-only analysis and calculation-run lifecycle

PRAGMA foreign_keys = ON;
BEGIN IMMEDIATE;

CREATE TABLE analysis_status_event (
    analysis_status_event_id TEXT PRIMARY KEY,
    analysis_id TEXT NOT NULL REFERENCES analysis(analysis_id),
    analysis_status TEXT NOT NULL CHECK (analysis_status IN
        ('Working', 'Calculating', 'Draft Revision Complete', 'Finalizing',
         'Finalized', 'Failed', 'Cancelled')),
    status_reason TEXT NOT NULL,
    decided_by_user_id TEXT,
    supersedes_status_event_id TEXT REFERENCES analysis_status_event(analysis_status_event_id),
    recorded_at_utc TEXT NOT NULL,
    CHECK (supersedes_status_event_id IS NULL OR
           supersedes_status_event_id <> analysis_status_event_id)
) STRICT;

CREATE TABLE calculation_run_status_event (
    calculation_run_status_event_id TEXT PRIMARY KEY,
    calculation_run_id TEXT NOT NULL REFERENCES calculation_run(calculation_run_id),
    run_status TEXT NOT NULL CHECK (run_status IN ('Started', 'Completed', 'Failed', 'Cancelled')),
    output_manifest_hash TEXT,
    status_detail TEXT NOT NULL,
    supersedes_status_event_id TEXT REFERENCES calculation_run_status_event(calculation_run_status_event_id),
    recorded_at_utc TEXT NOT NULL,
    CHECK ((run_status = 'Completed') = (output_manifest_hash IS NOT NULL)),
    CHECK (supersedes_status_event_id IS NULL OR
           supersedes_status_event_id <> calculation_run_status_event_id)
) STRICT;

INSERT INTO analysis_status_event
SELECT 'initial-analysis-' || analysis.analysis_id, analysis.analysis_id,
       CASE WHEN EXISTS (SELECT 1 FROM finalized_snapshot snapshot
                         WHERE snapshot.analysis_id = analysis.analysis_id)
            THEN 'Finalized'
            WHEN EXISTS (SELECT 1 FROM calculation_run run
                         JOIN scenario_revision scenario
                           ON scenario.scenario_revision_id = run.scenario_revision_id
                         WHERE scenario.analysis_id = analysis.analysis_id
                           AND run.status = 'Completed')
            THEN 'Draft Revision Complete' ELSE 'Working' END,
       'Initial lifecycle state migrated from authoritative records',
       analysis.created_by_user_id, NULL,
       COALESCE((SELECT MAX(snapshot.finalized_at_utc) FROM finalized_snapshot snapshot
                 WHERE snapshot.analysis_id = analysis.analysis_id), analysis.created_at_utc)
FROM analysis;

INSERT INTO calculation_run_status_event
SELECT 'initial-run-' || calculation_run_id, calculation_run_id, status,
       output_manifest_hash, 'Initial status migrated from calculation run',
       NULL, COALESCE(completed_at_utc, started_at_utc)
FROM calculation_run;

CREATE VIEW v_current_analysis_status AS
SELECT status.* FROM analysis_status_event status
WHERE NOT EXISTS (SELECT 1 FROM analysis_status_event newer
    WHERE newer.supersedes_status_event_id = status.analysis_status_event_id);

CREATE VIEW v_current_calculation_run_status AS
SELECT status.* FROM calculation_run_status_event status
WHERE NOT EXISTS (SELECT 1 FROM calculation_run_status_event newer
    WHERE newer.supersedes_status_event_id = status.calculation_run_status_event_id);

CREATE INDEX ix_analysis_status_current ON analysis_status_event(
    analysis_id, recorded_at_utc DESC, analysis_status_event_id DESC);
CREATE INDEX ix_calculation_run_status_current ON calculation_run_status_event(
    calculation_run_id, recorded_at_utc DESC, calculation_run_status_event_id DESC);

CREATE TRIGGER no_update_analysis BEFORE UPDATE ON analysis
BEGIN SELECT RAISE(ABORT, 'analysis is immutable'); END;
CREATE TRIGGER no_delete_analysis BEFORE DELETE ON analysis
BEGIN SELECT RAISE(ABORT, 'analysis cannot be deleted'); END;
CREATE TRIGGER no_update_scenario_revision BEFORE UPDATE ON scenario_revision
BEGIN SELECT RAISE(ABORT, 'scenario_revision is immutable'); END;
CREATE TRIGGER no_delete_scenario_revision BEFORE DELETE ON scenario_revision
BEGIN SELECT RAISE(ABORT, 'scenario_revision cannot be deleted'); END;
CREATE TRIGGER no_update_calculation_run BEFORE UPDATE ON calculation_run
BEGIN SELECT RAISE(ABORT, 'calculation_run is immutable'); END;
CREATE TRIGGER no_delete_calculation_run BEFORE DELETE ON calculation_run
BEGIN SELECT RAISE(ABORT, 'calculation_run cannot be deleted'); END;
CREATE TRIGGER no_update_analysis_status BEFORE UPDATE ON analysis_status_event
BEGIN SELECT RAISE(ABORT, 'analysis_status_event is immutable'); END;
CREATE TRIGGER no_delete_analysis_status BEFORE DELETE ON analysis_status_event
BEGIN SELECT RAISE(ABORT, 'analysis_status_event cannot be deleted'); END;
CREATE TRIGGER no_update_calculation_run_status BEFORE UPDATE ON calculation_run_status_event
BEGIN SELECT RAISE(ABORT, 'calculation_run_status_event is immutable'); END;
CREATE TRIGGER no_delete_calculation_run_status BEFORE DELETE ON calculation_run_status_event
BEGIN SELECT RAISE(ABORT, 'calculation_run_status_event cannot be deleted'); END;

INSERT INTO schema_migration VALUES (
    '0028', 'analysis_run_status_history', '__FORGE_MIGRATION_SHA256__',
    strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'schema-prototype-0.28'
);

COMMIT;
