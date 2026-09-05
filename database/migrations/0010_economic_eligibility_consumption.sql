-- Forge X economic eligibility consumption views

PRAGMA foreign_keys = ON;
BEGIN IMMEDIATE;

CREATE VIEW v_economically_eligible_observation AS
SELECT observation.*
FROM pbd_observation observation
WHERE NOT EXISTS (
    SELECT 1 FROM v_latest_observation_eligibility eligibility
    WHERE eligibility.observation_id = observation.observation_id
      AND eligibility.analytical_role = 'Economic Age'
      AND eligibility.eligibility_status = 'Historical Context Only'
);

CREATE VIEW v_economically_eligible_supplier_activity AS
SELECT activity.*
FROM supplier_activity activity
WHERE NOT EXISTS (
    SELECT 1
    FROM v_latest_observation_eligibility eligibility
    WHERE eligibility.analytical_role = 'Economic Age'
      AND eligibility.eligibility_status = 'Historical Context Only'
      AND eligibility.observation_id IN (
          SELECT activity.before_entity_id
          WHERE activity.before_entity_type = 'PBD Observation'
          UNION ALL
          SELECT activity.after_entity_id
          WHERE activity.after_entity_type = 'PBD Observation'
          UNION ALL
          SELECT datum.observation_id FROM submitted_datum datum
          WHERE (activity.before_entity_type = 'Submitted Datum'
                 AND datum.submitted_datum_id = activity.before_entity_id)
             OR (activity.after_entity_type = 'Submitted Datum'
                 AND datum.submitted_datum_id = activity.after_entity_id)
          UNION ALL
          SELECT membership.observation_id FROM round_observation membership
          WHERE (activity.before_entity_type = 'Round Observation'
                 AND membership.round_observation_id = activity.before_entity_id)
             OR (activity.after_entity_type = 'Round Observation'
                 AND membership.round_observation_id = activity.after_entity_id)
      )
);

INSERT INTO schema_migration VALUES (
    '0010', 'economic_eligibility_consumption', '__FORGE_MIGRATION_SHA256__',
    strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'schema-prototype-0.10'
);

COMMIT;
