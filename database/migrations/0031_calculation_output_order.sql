-- Forge X reproducible calculation output ordering

PRAGMA foreign_keys = ON;
BEGIN IMMEDIATE;

CREATE TABLE calculation_output_entry_order (
    calculation_result_id TEXT PRIMARY KEY
        REFERENCES calculation_result(calculation_result_id),
    calculation_run_id TEXT NOT NULL
        REFERENCES calculation_run(calculation_run_id),
    output_ordinal INTEGER NOT NULL CHECK (output_ordinal >= 0),
    recorded_at_utc TEXT NOT NULL,
    UNIQUE (calculation_run_id, output_ordinal)
) STRICT;

INSERT INTO calculation_output_entry_order
    (calculation_result_id, calculation_run_id, output_ordinal, recorded_at_utc)
SELECT calculation_result_id, calculation_run_id,
       ROW_NUMBER() OVER (
           PARTITION BY calculation_run_id
           ORDER BY recorded_at_utc, calculation_result_id
       ) - 1,
       recorded_at_utc
FROM calculation_result;

CREATE INDEX ix_calculation_output_order_run
ON calculation_output_entry_order(calculation_run_id, output_ordinal);

CREATE TRIGGER no_update_calculation_output_order
BEFORE UPDATE ON calculation_output_entry_order
BEGIN SELECT RAISE(ABORT, 'calculation_output_entry_order is immutable'); END;
CREATE TRIGGER no_delete_calculation_output_order
BEFORE DELETE ON calculation_output_entry_order
BEGIN SELECT RAISE(ABORT, 'calculation_output_entry_order cannot be deleted'); END;

INSERT INTO schema_migration VALUES (
    '0031', 'calculation_output_order', '__FORGE_MIGRATION_SHA256__',
    strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'schema-prototype-0.31'
);

COMMIT;
