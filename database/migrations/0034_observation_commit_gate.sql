-- Forge X governing observation commit boundary

PRAGMA foreign_keys = ON;
BEGIN IMMEDIATE;

CREATE TRIGGER validate_pbd_observation_commit
BEFORE INSERT ON pbd_observation
WHEN NEW.part_id IS NULL
  OR trim(NEW.submitted_supplier_name) = ''
  OR trim(NEW.submitted_part_number) = ''
  OR NEW.submitted_part_description IS NULL
  OR trim(NEW.submitted_part_description) = ''
  OR NOT EXISTS (
      SELECT 1 FROM staged_observation staged
      JOIN source_occurrence occurrence ON occurrence.occurrence_id = staged.occurrence_id
      JOIN source_worksheet worksheet ON worksheet.worksheet_id = occurrence.worksheet_id
      JOIN source_workbook workbook ON workbook.workbook_id = worksheet.workbook_id
      JOIN import_transaction transaction_row
        ON transaction_row.import_transaction_id = workbook.import_transaction_id
      JOIN import_session session ON session.import_session_id = transaction_row.import_session_id
      WHERE staged.staged_observation_id = NEW.staged_observation_id
        AND staged.occurrence_id = NEW.occurrence_id
        AND staged.import_context_type = NEW.observation_context
        AND staged.import_context_id IS NEW.context_id
        AND session.import_context_type = NEW.observation_context
        AND session.context_id IS NEW.context_id
  )
  OR (NEW.observation_context = 'Sourcing Event' AND NOT EXISTS (
      SELECT 1 FROM sourcing_event event_row WHERE event_row.event_id = NEW.context_id
  ))
  OR (NEW.observation_context = 'Historical Baseline' AND
      (NEW.economic_date IS NULL OR NEW.economic_date_precision = 'Unknown'))
BEGIN SELECT RAISE(ABORT, 'observation does not satisfy governing identity, description, date, or context requirements'); END;

CREATE TRIGGER validate_submitted_datum_typed_value
BEFORE INSERT ON submitted_datum
WHEN ((NEW.decimal_coefficient IS NOT NULL) + (NEW.text_value IS NOT NULL) +
      (NEW.date_value IS NOT NULL) + (NEW.boolean_value IS NOT NULL)) <> 1
  OR (NEW.precision_status = 'Eligible' AND NEW.decimal_coefficient IS NOT NULL
      AND NEW.normalized_unit_id IS NULL)
  OR (NEW.field_code = 'PIECE_PRICE' AND
      (NEW.decimal_coefficient IS NULL OR NEW.precision_status <> 'Eligible'
       OR NEW.normalized_unit_id IS NULL OR NEW.currency_id IS NULL))
BEGIN SELECT RAISE(ABORT, 'submitted datum does not satisfy typed-value or analytical eligibility requirements'); END;

CREATE TRIGGER validate_staged_observation_commit_gate
BEFORE UPDATE OF status ON staged_observation
WHEN NEW.status = 'Committed' AND (
    NEW.blocking_issue_count <> 0 OR NOT EXISTS (
        SELECT 1 FROM pbd_observation observation
        JOIN submitted_datum price ON price.observation_id = observation.observation_id
        WHERE observation.staged_observation_id = NEW.staged_observation_id
          AND price.field_code = 'PIECE_PRICE'
          AND price.decimal_coefficient IS NOT NULL
          AND price.precision_status = 'Eligible'
          AND price.normalized_unit_id IS NOT NULL
          AND price.currency_id IS NOT NULL
    )
)
BEGIN SELECT RAISE(ABORT, 'staged observation cannot commit without an eligible piece price'); END;

INSERT INTO schema_migration VALUES (
    '0034', 'observation_commit_gate', '__FORGE_MIGRATION_SHA256__',
    strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'schema-prototype-0.34'
);

COMMIT;
