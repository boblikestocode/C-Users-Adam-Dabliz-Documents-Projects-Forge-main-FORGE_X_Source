-- Forge X external formula dependency reproducibility history
PRAGMA foreign_keys = ON;
BEGIN IMMEDIATE;
CREATE TABLE formula_dependency_status_event (
    formula_dependency_status_event_id TEXT PRIMARY KEY,
    formula_evidence_id TEXT NOT NULL REFERENCES formula_evidence(formula_evidence_id),
    dependency_status TEXT NOT NULL CHECK (dependency_status IN
        ('Internally Reproducible', 'External Dependency Unverified',
         'External Dependency Verified', 'External Dependency Unavailable')),
    dependency_reference TEXT,
    dependency_manifest_hash TEXT CHECK (dependency_manifest_hash IS NULL OR length(dependency_manifest_hash) = 64),
    checked_by_user_id TEXT,
    status_detail TEXT NOT NULL,
    supersedes_dependency_status_event_id TEXT REFERENCES formula_dependency_status_event(formula_dependency_status_event_id),
    recorded_at_utc TEXT NOT NULL,
    CHECK (supersedes_dependency_status_event_id IS NULL OR supersedes_dependency_status_event_id <> formula_dependency_status_event_id),
    CHECK ((dependency_status = 'External Dependency Verified') = (dependency_manifest_hash IS NOT NULL))
) STRICT;
INSERT INTO formula_dependency_status_event
SELECT 'initial-formula-dependency-' || formula_evidence_id, formula_evidence_id,
       CASE WHEN instr(submitted_formula_text, '[') > 0 AND instr(submitted_formula_text, ']') > 0
            THEN 'External Dependency Unverified' ELSE 'Internally Reproducible' END,
       NULL, NULL, NULL, 'Initial dependency status derived during migration', NULL, recorded_at_utc
FROM formula_evidence;
CREATE VIEW v_current_formula_dependency_status AS
SELECT status.* FROM formula_dependency_status_event status
WHERE NOT EXISTS (SELECT 1 FROM formula_dependency_status_event newer
    WHERE newer.supersedes_dependency_status_event_id = status.formula_dependency_status_event_id);
CREATE INDEX ix_formula_dependency_status_current ON formula_dependency_status_event(formula_evidence_id, recorded_at_utc DESC);
CREATE TRIGGER no_update_formula_dependency_status BEFORE UPDATE ON formula_dependency_status_event
BEGIN SELECT RAISE(ABORT, 'formula_dependency_status_event is immutable'); END;
CREATE TRIGGER no_delete_formula_dependency_status BEFORE DELETE ON formula_dependency_status_event
BEGIN SELECT RAISE(ABORT, 'formula_dependency_status_event cannot be deleted'); END;
INSERT INTO schema_migration VALUES ('0036', 'formula_dependency_status', '__FORGE_MIGRATION_SHA256__',
    strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'schema-prototype-0.36');
COMMIT;
