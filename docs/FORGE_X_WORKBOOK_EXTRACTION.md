# OOXML extraction and recoverable staging

Migration `0052` adds immutable workbook and worksheet extraction receipts.
The `OOXML Evidence v1` adapter uses the Python standard library. It opens the
source file once, hashes that same open file, and reads each declared worksheet
without Excel automation, macro execution, or external-link retrieval.

## Inspect a workbook

```powershell
python -m app.cli discover-workbook 'C:\path\supplier.xlsx'
python -m app.cli discover-workbook 'C:\path\supplier.xlsx' --profile '.\approved-detection-profile.json'
```

The command returns JSON with scanned, qualifying, review, ignored, and failed
tab counts, visibility, cell counts, and diagnostic reasons. It does not modify
the source or a database. Supported containers are `.xlsx`, `.xlsm`, `.xltx`, and
`.xltm`. Legacy binary `.xls` and password-encrypted Office files require a
separately validated adapter.

## Detection boundary

The built-in profile requires exact normalized labels from all six structural
groups: part number, supplier, plant, operations, materials, and selling price.
It accepts visible, hidden, and very-hidden tabs with arbitrary names.
Partially recognized tabs become `Review Required`; unrecognized tabs are
explicitly non-PBD. `PBD Summary` is always excluded, including when a custom
profile omits it from its exclusion list. Cover/instruction exclusions and
marker aliases are configurable in a versioned JSON profile.

`default_detection_profile()` returns the complete profile structure for a
template-specific, reviewed variant. The entire profile is retained and hashed
with each extraction. Structural detection establishes a candidate; it does not
confirm supplier identity, currency, economic date, units, or a source-cell map.

## Retained evidence

The adapter preserves raw worksheet/workbook XML, exact numeric lexemes,
resolved shared and inline strings, formulas and their cached results,
formula attributes, cell style references, style/shared-string XML, merged
ranges, workbook relationships, and traditional cell-comment text/XML.
It retains detailed and unrecognized cell rows, including rows/columns hidden
in the source XML. Workbook-level XML retains date-system and defined-name
context without converting dates or resolving named formulas automatically.

Shared, array, and data-table formulas remain explicit expansion-review items.
Missing formula caches and Excel error cells remain visible diagnostics. These
unsupported cells cannot become governing mapped fields. No expression is
executed during extraction. Advanced drawing, threaded-comment, and embedded
object interpretation is outside this adapter; the source file remains the
retained original for those items.

## Integration with the existing import workflow

1. Run existing authority/checkpoint preflight and create an import transaction
   plus its discovery inventory.
2. Call `extract_workbook(path)` once. Optional progress and cancellation
   callbacks operate before database registration.
3. Call `register_extracted_workbook(...)` with that extraction, transaction,
   discovery item, detection rule, and audit context. It records all source tabs
   and atomically stores the complete set of extraction receipts. Registration
   and receipt persistence are separate recoverable boundaries; the operation
   can resume against an already registered discovery item.
4. Call `stage_workbook_receipts(...)`. Qualifying/review candidates are blocked
   for required commercial confirmations. Summary/non-PBD tabs are explicitly
   ignored; failed parses are explicitly failed; duplicates remain duplicates.
   Retry does not create another staged observation or undo buyer review.
5. Use `load_extracted_worksheet(...)` to inspect retained evidence after the
   source file is unavailable. `mapped_field(...)` prepares a typed field from
   an explicitly selected source cell; it does not infer cell locations or units.
6. Record buyer decisions through `record_staging_resolution`. A structurally
   incomplete candidate requires the explicit `Confirmed Detailed PBD` decision
   on its `Extraction Mapping Review` issue before commercial commit.
7. Commit using the existing `commit_observation` gate, then reconcile the
   import. The gate validates mapped source lexemes, formulas, caches, and
   numeric values against the immutable extraction before creating any
   governing observation. Summary and failed tabs cannot supply those fields.

The health gate reconstructs parsed extraction evidence from retained XML and
compares inventory, source fingerprints, payloads, and hashes. It reports
`WORKBOOK_EXTRACTION_REPRODUCTION_MISMATCH` for inconsistent receipts.

## Validation and remaining acceptance

Synthetic tests exercise a 53-tab workbook with 52 detailed candidates and one
summary, hidden tabs, exact decimals, formulas/caches, style changes, comments,
partial and malformed sheets, external relationship rejection, cancellation,
immutable receipts, source-unavailable commit, duplicate reimports, explicit
structure review, and the read-only CLI.

The complete regression suite passed 189 tests. The final 12 adapter/staging
tests also passed after workbook-level XML and relationship retention was added.

The Mayco workbook referenced in the model requirements was not found at its
documented location. The synthetic 52-PBD result is not a claim of validation
against that real workbook. Its current location or another representative
supplier workbook is needed for template-specific mapping and acceptance.
Production encryption/identity integration remains a separate unresolved
deployment dependency.

Format references: Microsoft's [cell value documentation](https://learn.microsoft.com/en-us/office/open-xml/spreadsheet/how-to-retrieve-the-values-of-cells-in-a-spreadsheet)
and [formula documentation](https://learn.microsoft.com/en-us/office/open-xml/spreadsheet/working-with-formulas).
