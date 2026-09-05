-- Append-only quote/PBD candidate version review and explicit activation.

PRAGMA foreign_keys = ON;
BEGIN IMMEDIATE;

CREATE TABLE quote_version_candidate (
    quote_version_candidate_id TEXT PRIMARY KEY,
    observation_id TEXT NOT NULL UNIQUE REFERENCES pbd_observation(observation_id),
    event_id TEXT NOT NULL REFERENCES sourcing_event(event_id),
    supplier_id TEXT NOT NULL,
    part_id TEXT NOT NULL,
    region_code TEXT NOT NULL,
    source_identity TEXT NOT NULL,
    version_number INTEGER NOT NULL CHECK (version_number > 0),
    detected_at_utc TEXT NOT NULL,
    UNIQUE (event_id, supplier_id, part_id, region_code, version_number),
    UNIQUE (event_id, supplier_id, part_id, region_code, source_identity)
) STRICT;

CREATE TABLE quote_version_review_event (
    quote_version_review_event_id TEXT PRIMARY KEY,
    quote_version_candidate_id TEXT NOT NULL UNIQUE
        REFERENCES quote_version_candidate(quote_version_candidate_id),
    review_decision TEXT NOT NULL
        CHECK (review_decision IN ('Confirm Active', 'Reject Candidate')),
    buyer_classification TEXT NOT NULL
        CHECK (buyer_classification IN
               ('Initial Quote', 'Requote', 'Correction', 'Final Offer', 'Unclassified')),
    buyer_note TEXT,
    reviewed_by_user_id TEXT NOT NULL,
    reviewed_at_utc TEXT NOT NULL
) STRICT;

CREATE TABLE quote_version_activation (
    quote_version_activation_id TEXT PRIMARY KEY,
    quote_version_review_event_id TEXT NOT NULL UNIQUE
        REFERENCES quote_version_review_event(quote_version_review_event_id),
    quote_version_candidate_id TEXT NOT NULL UNIQUE
        REFERENCES quote_version_candidate(quote_version_candidate_id),
    supersedes_activation_id TEXT UNIQUE
        REFERENCES quote_version_activation(quote_version_activation_id),
    activated_at_utc TEXT NOT NULL,
    CHECK (supersedes_activation_id IS NULL OR
           supersedes_activation_id <> quote_version_activation_id)
) STRICT;

CREATE VIEW v_quote_version_review AS
SELECT candidate.*,
       COALESCE(review.review_decision, 'Pending Version Review') AS review_status,
       review.buyer_classification, review.buyer_note,
       review.reviewed_by_user_id, review.reviewed_at_utc
FROM quote_version_candidate candidate
LEFT JOIN quote_version_review_event review
  ON review.quote_version_candidate_id = candidate.quote_version_candidate_id;

CREATE VIEW v_active_quote_version AS
SELECT candidate.*, activation.quote_version_activation_id,
       review.buyer_classification, review.buyer_note,
       review.reviewed_by_user_id, activation.activated_at_utc
FROM quote_version_activation activation
JOIN quote_version_review_event review
  ON review.quote_version_review_event_id = activation.quote_version_review_event_id
JOIN quote_version_candidate candidate
  ON candidate.quote_version_candidate_id = activation.quote_version_candidate_id
WHERE NOT EXISTS (
    SELECT 1 FROM quote_version_activation newer
    WHERE newer.supersedes_activation_id = activation.quote_version_activation_id
);

CREATE INDEX ix_quote_version_scope
ON quote_version_candidate(event_id, supplier_id, part_id, region_code, version_number);

CREATE TRIGGER validate_quote_version_candidate
BEFORE INSERT ON quote_version_candidate
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1
        FROM pbd_observation observation
        JOIN round_observation membership
          ON membership.observation_id = observation.observation_id
        JOIN supplier_quote_round round
          ON round.quote_round_id = membership.quote_round_id
        JOIN event_part event_part
          ON event_part.event_part_id = membership.event_part_id
        WHERE observation.observation_id = NEW.observation_id
          AND observation.observation_context = 'Sourcing Event'
          AND observation.supplier_id = NEW.supplier_id
          AND observation.part_id = NEW.part_id
          AND round.event_id = NEW.event_id
          AND event_part.part_id = NEW.part_id
    ) THEN RAISE(ABORT, 'quote version candidate scope does not match observation lineage') END;
    SELECT CASE WHEN NEW.version_number <> 1 + COALESCE((
        SELECT MAX(prior.version_number) FROM quote_version_candidate prior
        WHERE prior.event_id = NEW.event_id AND prior.supplier_id = NEW.supplier_id
          AND prior.part_id = NEW.part_id AND prior.region_code = NEW.region_code
    ), 0) THEN RAISE(ABORT, 'quote version number must be the next scope sequence') END;
END;

CREATE TRIGGER validate_quote_version_activation
BEFORE INSERT ON quote_version_activation
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM quote_version_review_event review
        WHERE review.quote_version_review_event_id = NEW.quote_version_review_event_id
          AND review.quote_version_candidate_id = NEW.quote_version_candidate_id
          AND review.review_decision = 'Confirm Active'
    ) THEN RAISE(ABORT, 'activation requires its confirmed review event') END;
    SELECT CASE WHEN COALESCE(NEW.supersedes_activation_id, '') <>
        COALESCE((
            SELECT active.quote_version_activation_id
            FROM quote_version_activation active
            JOIN quote_version_candidate prior
              ON prior.quote_version_candidate_id = active.quote_version_candidate_id
            JOIN quote_version_candidate candidate
              ON candidate.quote_version_candidate_id = NEW.quote_version_candidate_id
            WHERE prior.event_id = candidate.event_id
              AND prior.supplier_id = candidate.supplier_id
              AND prior.part_id = candidate.part_id
              AND prior.region_code = candidate.region_code
              AND NOT EXISTS (
                  SELECT 1 FROM quote_version_activation newer
                  WHERE newer.supersedes_activation_id = active.quote_version_activation_id)
        ), '')
    THEN RAISE(ABORT, 'activation must directly supersede the current scoped quote') END;
    SELECT CASE WHEN EXISTS (
        SELECT 1
        FROM quote_version_activation active
        JOIN quote_version_candidate prior
          ON prior.quote_version_candidate_id = active.quote_version_candidate_id
        JOIN quote_version_candidate candidate
          ON candidate.quote_version_candidate_id = NEW.quote_version_candidate_id
        WHERE active.quote_version_activation_id = NEW.supersedes_activation_id
          AND candidate.version_number <= prior.version_number
    ) THEN RAISE(ABORT, 'activation cannot regress the scoped quote version') END;
END;

CREATE TRIGGER no_update_quote_version_candidate BEFORE UPDATE ON quote_version_candidate
BEGIN SELECT RAISE(ABORT, 'quote version candidate is immutable'); END;
CREATE TRIGGER no_delete_quote_version_candidate BEFORE DELETE ON quote_version_candidate
BEGIN SELECT RAISE(ABORT, 'quote version candidate cannot be deleted'); END;
CREATE TRIGGER no_update_quote_version_review BEFORE UPDATE ON quote_version_review_event
BEGIN SELECT RAISE(ABORT, 'quote version review is immutable'); END;
CREATE TRIGGER no_delete_quote_version_review BEFORE DELETE ON quote_version_review_event
BEGIN SELECT RAISE(ABORT, 'quote version review cannot be deleted'); END;
CREATE TRIGGER no_update_quote_version_activation BEFORE UPDATE ON quote_version_activation
BEGIN SELECT RAISE(ABORT, 'quote version activation is immutable'); END;
CREATE TRIGGER no_delete_quote_version_activation BEFORE DELETE ON quote_version_activation
BEGIN SELECT RAISE(ABORT, 'quote version activation cannot be deleted'); END;

INSERT INTO schema_migration VALUES
('0043', 'quote_version_review', '__FORGE_MIGRATION_SHA256__',
 strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'schema-prototype-0.43');

COMMIT;
