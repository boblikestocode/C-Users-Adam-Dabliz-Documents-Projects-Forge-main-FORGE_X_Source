-- Forge X PCE should-cost evidence isolation and round lineage enforcement

PRAGMA foreign_keys = ON;
BEGIN IMMEDIATE;

CREATE TRIGGER validate_round_batch_context
BEFORE INSERT ON round_batch
WHEN NOT EXISTS (
    SELECT 1 FROM supplier_quote_round round_row
    JOIN import_transaction transaction_row
      ON transaction_row.import_transaction_id = NEW.import_transaction_id
    JOIN import_session session
      ON session.import_session_id = transaction_row.import_session_id
    WHERE round_row.quote_round_id = NEW.quote_round_id
      AND session.import_context_type = 'Sourcing Event'
      AND session.context_id = round_row.event_id
)
BEGIN SELECT RAISE(ABORT, 'round batch requires a matching sourcing-event import'); END;

CREATE TRIGGER validate_round_observation_context
BEFORE INSERT ON round_observation
WHEN NOT EXISTS (
    SELECT 1 FROM supplier_quote_round round_row
    JOIN round_batch batch
      ON batch.round_batch_id = NEW.round_batch_id
     AND batch.quote_round_id = round_row.quote_round_id
    JOIN pbd_observation observation
      ON observation.observation_id = NEW.observation_id
    JOIN event_part event_scope
      ON event_scope.event_part_id = NEW.event_part_id
     AND event_scope.part_id = observation.part_id
    JOIN scope_version scope
      ON scope.scope_version_id = event_scope.scope_version_id
    JOIN source_package package
      ON package.source_package_id = scope.source_package_id
     AND package.event_id = round_row.event_id
    WHERE round_row.quote_round_id = NEW.quote_round_id
      AND observation.observation_context = 'Sourcing Event'
      AND observation.context_id = round_row.event_id
      AND observation.supplier_id = round_row.supplier_id
)
BEGIN SELECT RAISE(ABORT, 'round observation requires matching sourcing-event, supplier, part, and batch lineage'); END;

CREATE TRIGGER no_update_round_batch BEFORE UPDATE ON round_batch
BEGIN SELECT RAISE(ABORT, 'round_batch is immutable'); END;
CREATE TRIGGER no_delete_round_batch BEFORE DELETE ON round_batch
BEGIN SELECT RAISE(ABORT, 'round_batch cannot be deleted'); END;
CREATE TRIGGER no_update_round_observation BEFORE UPDATE ON round_observation
BEGIN SELECT RAISE(ABORT, 'round_observation is immutable'); END;
CREATE TRIGGER no_delete_round_observation BEFORE DELETE ON round_observation
BEGIN SELECT RAISE(ABORT, 'round_observation cannot be deleted'); END;

INSERT INTO schema_migration VALUES (
    '0032', 'pce_evidence_boundaries', '__FORGE_MIGRATION_SHA256__',
    strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'schema-prototype-0.32'
);

COMMIT;
