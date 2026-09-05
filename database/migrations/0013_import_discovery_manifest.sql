-- Forge X append-only import discovery manifest and completeness gate

PRAGMA foreign_keys = ON;
BEGIN IMMEDIATE;

CREATE TABLE import_discovery_item (
    import_discovery_item_id TEXT PRIMARY KEY,
    import_transaction_id TEXT NOT NULL REFERENCES import_transaction(import_transaction_id),
    discovery_ordinal INTEGER NOT NULL CHECK (discovery_ordinal >= 0),
    submitted_filename TEXT NOT NULL,
    source_locator TEXT,
    expected_file_hash_sha256 TEXT CHECK (
        expected_file_hash_sha256 IS NULL OR length(expected_file_hash_sha256) = 64
    ),
    discovered_at_utc TEXT NOT NULL,
    UNIQUE (import_transaction_id, discovery_ordinal),
    UNIQUE (import_transaction_id, source_locator)
) STRICT;

CREATE TABLE import_discovery_disposition (
    import_discovery_disposition_id TEXT PRIMARY KEY,
    import_discovery_item_id TEXT NOT NULL REFERENCES import_discovery_item(import_discovery_item_id),
    disposition_status TEXT NOT NULL CHECK (
        disposition_status IN ('Pending', 'Registered', 'Ignored', 'Failed')
    ),
    workbook_id TEXT REFERENCES source_workbook(workbook_id),
    disposition_reason TEXT,
    supersedes_disposition_id TEXT REFERENCES import_discovery_disposition(import_discovery_disposition_id),
    recorded_at_utc TEXT NOT NULL,
    CHECK (
        (disposition_status = 'Registered' AND workbook_id IS NOT NULL AND disposition_reason IS NULL)
        OR (disposition_status = 'Pending' AND workbook_id IS NULL AND disposition_reason IS NULL)
        OR (disposition_status IN ('Ignored', 'Failed') AND workbook_id IS NULL
            AND length(trim(disposition_reason)) > 0)
    ),
    CHECK (
        supersedes_disposition_id IS NULL
        OR supersedes_disposition_id <> import_discovery_disposition_id
    )
) STRICT;

CREATE UNIQUE INDEX ux_import_discovery_registered_workbook
ON import_discovery_disposition(workbook_id)
WHERE workbook_id IS NOT NULL;

CREATE INDEX ix_import_discovery_transaction
ON import_discovery_item(import_transaction_id, discovery_ordinal);

CREATE INDEX ix_import_discovery_current
ON import_discovery_disposition(import_discovery_item_id, recorded_at_utc DESC,
    import_discovery_disposition_id DESC);

CREATE VIEW v_current_import_discovery_disposition AS
SELECT disposition.*
FROM import_discovery_disposition disposition
WHERE NOT EXISTS (
    SELECT 1
    FROM import_discovery_disposition newer
    WHERE newer.supersedes_disposition_id = disposition.import_discovery_disposition_id
);

CREATE VIEW v_import_discovery_reconciliation AS
SELECT item.import_transaction_id,
       COUNT(*) AS discovered_workbook_count,
       SUM(CASE WHEN current.disposition_status = 'Registered' THEN 1 ELSE 0 END)
           AS registered_workbook_count,
       SUM(CASE WHEN current.disposition_status = 'Ignored' THEN 1 ELSE 0 END)
           AS ignored_workbook_count,
       SUM(CASE WHEN current.disposition_status = 'Failed' THEN 1 ELSE 0 END)
           AS failed_workbook_count,
       SUM(CASE WHEN current.disposition_status = 'Pending' THEN 1 ELSE 0 END)
           AS pending_workbook_count
FROM import_discovery_item item
JOIN v_current_import_discovery_disposition current
  ON current.import_discovery_item_id = item.import_discovery_item_id
GROUP BY item.import_transaction_id;

CREATE TRIGGER no_update_import_discovery_item BEFORE UPDATE ON import_discovery_item
BEGIN SELECT RAISE(ABORT, 'import_discovery_item is immutable'); END;
CREATE TRIGGER no_delete_import_discovery_item BEFORE DELETE ON import_discovery_item
BEGIN SELECT RAISE(ABORT, 'import_discovery_item cannot be deleted'); END;
CREATE TRIGGER no_update_import_discovery_disposition BEFORE UPDATE ON import_discovery_disposition
BEGIN SELECT RAISE(ABORT, 'import_discovery_disposition is immutable'); END;
CREATE TRIGGER no_delete_import_discovery_disposition BEFORE DELETE ON import_discovery_disposition
BEGIN SELECT RAISE(ABORT, 'import_discovery_disposition cannot be deleted'); END;

INSERT INTO schema_migration VALUES (
    '0013', 'import_discovery_manifest', '__FORGE_MIGRATION_SHA256__',
    strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'schema-prototype-0.13'
);

COMMIT;
