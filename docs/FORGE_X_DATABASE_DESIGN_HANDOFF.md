# Forge X Database Design Handoff

Recorded through: August 31, 2026  
Status: Living approved design record; implementation has not started  
Scope: Database boundaries, identity, evidence, traceability, versioning, analysis snapshots, SharePoint continuity, and supplier-profile foundations

## 1. Purpose

This document records the product-owner decisions governing the Forge X data model. It supplements `FORGE_X_MODEL_REQUIREMENTS.md` and translates the approved product behavior into an implementable database foundation.

Forge 7.0 - Stellantis Shareable is the clean analytical foundation and behavioral reference. Its extraction, evidence, calculation, and workbook-generation stages should inform Forge X. Forge X must not simply persist the existing transient CLIXML objects as one large table. The new database must normalize durable identities and evidence while preserving the validated analytical behavior selected from Forge 7.

This document distinguishes approved rules from remaining design work. Suggestions must not become governing business rules until confirmed by the product owner.

## 2. Approved System Boundary

- Maintain one protected commodity database per commodity code.
- Commodity code is the permanent database identity.
- Buyer code is an effective-dated authority assignment, not part of the permanent database key.
- One designated primary buyer has write authority at a time.
- Supporting buyers and managers are read-only unless authority is explicitly delegated.
- The designated Forge X administrator controls primary-buyer assignments, ownership transfers, delegations, and authority changes.
- Support a controlled administrator list with one primary administrator for continuity.
- Microsoft organizational identity and SharePoint access establish identity and site access. Forge X authorization establishes the user's application role.
- Primary-buyer transfers, delegations, revocations, and recovery-administrator changes require an online connection to, and validation against, the current central authority registry.
- Authority assignments cannot be created or modified offline.
- Every authority grant, revocation, delegation, and ownership transfer is an immutable audit event.
- Commodity databases are writable only through Forge X. Direct database editing is unsupported.

## 3. Local-First SharePoint Continuity

- SharePoint is the authoritative publication and recovery location, not the live database runtime.
- Forge X downloads or restores a protected local working copy, performs transactions locally, validates the database, and publishes a new encrypted snapshot to SharePoint.
- Never edit a database file in place on a synchronized SharePoint location.
- Buyers may continue importing and analyzing while SharePoint is unavailable.
- Offline changes enter a local append-only journal and a pending publication queue.
- Before publishing, Forge X revalidates write authority, expected predecessor snapshot, schema compatibility, database integrity, and audit-chain integrity.
- If SharePoint contains a newer snapshot, Forge X blocks automatic replacement and requires administrator-guided reconciliation.
- Competing branches are preserved. Reconciliation compares their audit histories and never silently discards either branch.
- Publish a new uniquely named encrypted snapshot first. Verify it before changing the small pointer or manifest that identifies the latest valid snapshot.
- An interrupted or failed upload must leave the prior validated SharePoint snapshot untouched.
- Retain the latest 10 validated SharePoint publications plus monthly milestone snapshots.
- Every published snapshot includes at least commodity code, database version, schema version, last audit-event sequence, UTC timestamp, database fingerprint, record counts, integrity result, publishing identity, and pinned master-registry versions.
- The interface must display local database health, last checkpoint, last successful SharePoint publication, pending sync state, snapshot version, authority state, and conflicts.

## 4. Recovery and Migration

- The planned production database stack is SQLite with SQLCipher Enterprise backed by a FIPS 140-3 validated cryptographic provider.
- Final procurement and production approval require a representative prototype that verifies the FIPS operating state, encryption and tamper detection, key recovery on an authorized replacement computer, transactional recovery, encrypted checkpoint and publication handling, and acceptable import and analysis performance.
- Production encryption and key-protection operations require a FIPS 140-3 validated cryptographic provider.
- Forge X must verify the required cryptographic-provider status at runtime and fail closed for governing database access, recovery, and publication if the approved provider is unavailable or not operating in the required mode.
- The selected database-encryption library, provider, packaging, and deployment configuration must preserve the applicable FIPS validation boundary; using an approved algorithm alone is insufficient.
- Commodity-database encryption keys require organization-managed recovery.
- A designated Forge X administrator must be able to restore an encrypted commodity database on an authorized replacement computer through a controlled, audited recovery process.
- One authorized administrator is sufficient to approve and perform a key-recovery operation; dual-administrator approval is not required.
- Maintain one primary recovery administrator and two backup recovery administrators for continuity.
- In normal operation, an assigned buyer unlocks an authorized commodity database through the buyer's verified Microsoft/Windows organizational identity without entering a separate database password.
- The commodity-database encryption key remains separate from the user's credentials and is released only after Forge X validates the applicable identity and application authority.
- A buyer's previously verified write authority remains usable offline for an absolute maximum of 14 days from the last successful online authority verification.
- The offline authority period does not renew when Forge X opens or through other offline activity.
- Offline write access requires the same authorized organizational identity and registered device; every offline transaction is identified and audited as offline activity.
- When the 14-day period expires, the commodity database becomes read-only until authority is successfully revalidated online.
- Reconnection applies authority revocations immediately. SharePoint publication always requires fresh authority revalidation regardless of the remaining offline-authority period.
- Forge X closes the commodity database and releases decrypted database-key material from application memory when Windows locks, the user signs out, the computer enters sleep, or Forge X has been inactive for 15 minutes.
- Resuming access requires organizational identity and Forge X authority revalidation under the applicable online or approved offline-authority rules.
- When safe and valid, incomplete user input is preserved in encrypted staging before automatic closure so that relocking does not unnecessarily discard work. Staged drafts remain analytically ineligible until explicitly completed and committed.
- No individual user account or original workstation may be the sole dependency for database recovery.
- Recovery authority does not grant routine access; every key-recovery operation requires authorization and an immutable audit event.
- Encryption keys must never be stored in source code, ordinary configuration files, logs, or inside the database they protect.
- Create a verified local recovery checkpoint before every import, correction, activation, schema migration, or other material update.
- Retain at least the latest five validated local recovery checkpoints and every checkpoint created within the preceding 30 days.
- Never remove the last checkpoint that precedes unpublished local changes.
- A checkpoint becomes eligible for retention cleanup only after a newer database state has passed integrity validation and been successfully published to SharePoint.
- Checkpoint cleanup is an audited maintenance action. All retained checkpoints remain encrypted and subject to the same controlled key-recovery model as the working database.
- Material database changes are transactional: the approved commit succeeds completely or does not apply.
- Schema upgrades are backup-first, transactional, and reversible.
- Preserve the prior schema snapshot until post-migration integrity tests pass.
- A failed migration cannot replace the last validated database.
- Committed evidence and audit records are never physically deleted through normal Forge X operation.
- Corrections and exclusions supersede prior state through new records.
- Exceptional legal or security removal requires a separate administrator-governed procedure that is not yet designed.

## 5. Central Governed Registries

Maintain small central registries separately from commodity commercial evidence:

- Part registry
- Supplier identity registry
- Supplier plant registry when plant information is available
- Commodity registry
- Vehicle-line registry
- Program registry
- Unit-of-measure catalog
- User, buyer-code, role, and authority registry

Central registries follow the protected-local-copy and validated-SharePoint-publication model. Commodity databases keep versioned local caches for validation and offline use. Every commodity snapshot and finalized analysis pins the exact registry versions it used.

### 5.1 Part identity

- A valid part number is mandatory before PBD commitment.
- Part numbers are exactly 10 alphanumeric characters.
- Part numbers cannot contain spaces, hyphens, punctuation, or other special characters.
- Canonical part numbers are uppercase.
- Preserve the exact supplier-submitted text separately from the canonical identity.
- Invalid part numbers remain blocked in staging and cannot affect calculations, weighting, benchmarks, or supplier profiles.
- Buyers may correct an invalid supplier-submitted part number in staging. Preserve the original value, corrected value, reason, buyer identity, and timestamp.
- Each part number has exactly one governing commodity code.
- A central administrator-controlled registry enforces the permanent `Part Number -> Commodity Code -> Canonical Description` relationship across commodity databases.
- A part may apply to multiple vehicle lines and programs through separate applicability records.
- Routine batches of new parts may be submitted and confirmed by the assigned commodity buyer after deterministic validation.
- Questionable or conflicting parts are separated and blocked for administrator resolution.

### 5.2 Part description

- A non-empty part description is mandatory before PBD commitment.
- If the PBD contains a description, accept it without a separate buyer confirmation.
- If the PBD lacks a description, prompt the buyer to supply and confirm it.
- One part number has one canonical description.
- Description comparison ignores capitalization and repeated or surrounding whitespace.
- Substantive wording differences for an existing part number are blocked pending resolution.
- Preserve the exact supplier-submitted description and any buyer-supplied description as separate evidence.

### 5.3 Supplier identity

- Supplier code and confirmed supplier name are mandatory before PBD commitment.
- Missing or conflicting supplier identity remains in recoverable staging and cannot affect calculations or profiles.
- Preserve the supplier-submitted name exactly as evidence.
- Store canonical supplier identity separately in the central supplier registry.
- Supplier pricing, behavior, formula integrity, and profiles remain commodity-specific rather than becoming central-registry fields.
- Plant is optional. Missing plant information has no adverse effect on commercial eligibility or calculation.
- When present, plants are reusable child identities beneath the central supplier identity.
- Governing plant-specific profiles use supplier code + plant + commodity. When plant is unavailable, use supplier code + commodity and do not manufacture plant conclusions.

### 5.4 Vehicle line and program

- Vehicle line is the durable vehicle family, for example `Ram 1500`.
- Program is a time-bounded generation or program code, for example `DT` or `DT2`.
- Programs may change while the vehicle line remains continuous.
- Program records may include effective periods and explicit predecessor/successor relationships.
- Buyers submit requests for new vehicle lines and program codes.
- A designated administrator approves, rejects, or returns each request.
- Buyers may prepare a sourcing event in staging while approval is pending, but cannot commit governing evidence or analyses to unapproved master data.
- Part applicability connects one permanent part identity to one or more vehicle lines and programs while retaining one governing commodity.

## 6. PBD Observation Boundary

- One committed PBD observation represents exactly one supplier and one part number from one qualifying worksheet.
- A multi-tab workbook produces one observation per qualifying detailed PBD worksheet.
- Materials, operations, rates, overhead, profit, below-the-line items, and other details are child records of the observation.
- Workbook, worksheet, import batch, and source occurrence are separate provenance records.
- Preserve the original section hierarchy and row/order position of the PBD.
- Preserve each recognized element's submitted section, label, value, unit, formula status, worksheet, cell address, and source lineage.
- Store frequently queried validated summaries, including final piece price, without discarding detailed child evidence.
- Persist frequently used four-decimal derived summaries, including piece price, material total, conversion total, and round APV, to support responsive screens and analyses.
- Persisted summaries are performance accelerators rather than independent evidence. They are updated in the same transaction as their supporting detail and identify the calculation-rule version that produced them.
- Integrity validation can independently recalculate persisted summaries from detailed evidence. A mismatch is an integrity failure that blocks publication and analysis finalization until resolved.

## 7. Source Retention and Extraction Scope

- Do not embed or copy original PBD workbooks into the commodity database or SharePoint evidence package.
- Retain the original file location, workbook and worksheet identity, cryptographic fingerprints, file metadata, import timestamp, recognized values, formulas, units, cell lineage, importing identity, extraction-engine version, and reconciliation results.
- If the external workbook later becomes unavailable, stored evidence remains analyzable but must be marked as no longer externally re-verifiable.
- Store recognized business fields and the supporting cells required to prove their formulas and calculations.
- Do not persist every non-empty worksheet cell.
- Potentially relevant unmapped content becomes an extraction exception for review.
- Open sources read-only with macros disabled and external links blocked.
- Preserve external-link formulas as evidence but mark them not independently reproducible when their dependencies are unavailable.

## 8. Duplicate Control and Fingerprints

- A duplicate import creates an immutable import-attempt audit event but no new analytical observation or quote version.
- Duplicate imports have zero effect on quote counts, weighting, averages, medians, benchmarks, supplier profiles, coverage, APV, NPV, opportunity, or round progression.
- Enforce duplicate protection with database constraints, not only interface warnings.
- Separate physical-file, worksheet-content, and normalized-PBD fingerprints.
- The same qualifying PBD copied into a differently named workbook remains one analytical observation.
- Retain every discovered file location as a source occurrence attached to the one observation.
- Formatting, workbook metadata, or unrelated-tab changes do not create a new commercial observation when recognized PBD evidence is unchanged.
- Compare full submitted precision, not rounded display values.
- Identical commercial data with formulas replaced by fixed values remains one commercial observation and receives no additional analytical weight.
- Formula-presence or formula-integrity changes are separate evidence-quality events attached to the observation.
- Never deduplicate across supplier identities. Two suppliers quoting the same part remain separate evidence even when price and structure are identical.

## 9. Submitted Structure and Normalized Categories

Preserve two parallel representations:

1. **Submitted structure**: exactly what the supplier disclosed, including lump sums, aggregate conversion, detailed operations, materials, rates, percentages, formulas, labels, sections, and hierarchy.
2. **Forge analytical interpretation**: controlled categories that enable valid comparisons without rewriting the source.

Approved structure classifications include:

- Detailed and Reconciled
- Valid Aggregate
- Lump-Sum Submission
- Incomplete
- Formula Reconciliation Exception

Rules:

- Never manufacture a split for a combined supplier lump sum.
- A lump sum remains eligible only at the supported aggregate level.
- Preserve supplier-submitted cost labels exactly.
- Normalize synonymous labels into controlled analytical categories, for example `Direct Labour`, `Production Wages`, and `DL Cost` may map to `Labor`.
- An unclear label requires buyer confirmation before it affects category-level analytics.
- Store buyer interpretation separately with identity, reason, timestamp, and source context.
- Reuse a confirmed mapping automatically only at high confidence: exact supplier, template structure, section, label, unit, and calculation context.
- Low, medium, changed, or conflicting context requires buyer confirmation.
- Buyers may reject an automatic mapping. The correction creates a new mapping version; prior observations and finalized analyses retain their original mapping version.

## 10. Formula Integrity

Formula behavior is a first-class supplier-profile dimension, separate from commercial price movement.

For every recognized formula field, retain:

- Exact supplier formula text
- Excel-displayed value
- Forge independently calculated or reconciled value
- Exact submitted decimal representation
- Worksheet and cell lineage
- Formula dependencies when recognized
- Formula-integrity classification

Track at least:

- Formula preserved unchanged
- Formula changed with the same calculated result
- Formula replaced by a fixed value
- Formula broken or returning an error
- Formula producing a value inconsistent with the submitted amount
- Formula improved or repaired in a later submission

Formula behavior is analyzed at supplier code + plant + commodity when plant is available, otherwise supplier code + commodity. Corporate-family reporting is a secondary roll-up and cannot hide plant-level differences.

## 11. Precision, Currency, and Units

- Preserve every submitted decimal place.
- Store exact submitted numeric representation plus a high-precision decimal calculation value.
- The initial governing calculation limit is 38 significant decimal digits with up to 18 digits after the decimal point.
- Forge X must not round or alter an imported supplier value stored as source evidence.
- Governing calculations use the exact supported input values without pre-rounding the operands.
- Each defined governing calculation output is rounded to four digits after the decimal point. Intermediate operations within that defined calculation retain full supported precision until its output boundary.
- Midpoint values round away from zero at the governing output boundary, consistent with Excel's standard `ROUND` behavior. Banker’s rounding is not used unless a separately approved, versioned business rule explicitly requires it.
- Standard displayed commercial values are rounded to two digits after the decimal point while retaining the four-decimal governing calculation result underneath.
- Calculation-output boundaries and rounding behavior are explicit, centrally implemented, versioned rules so imports, analyses, screens, and exports produce deterministic results.
- If a value cannot be represented at the approved governing calculation precision, preserve the exact submitted representation and surface an explicit precision exception rather than rounding or truncating it.
- A precision exception remains preserved in recoverable staging but is blocked from governing calculations until reviewed and resolved.
- Precision-blocked evidence cannot affect weighting, benchmarks, supplier profiles, targets, opportunity, APV, NPV, or recommendations.
- Do not use binary floating point for governing commercial amounts.
- Report presentation applies its approved versioned display rules without changing the stored source evidence or governing four-decimal calculation result.
- Within the approved accuracy, evidence, and traceability requirements, physical storage and calculation design should prioritize efficient imports, responsive analysis, and fast repeatable queries.
- Currency is mandatory for analytical eligibility.
- If missing, the observation remains staged until buyer confirmation.
- Do not silently assume USD.
- Currency conversion is outside the initial release. Unlike currencies cannot be combined in a governing comparison.
- Every analytically eligible cost or rate requires a confirmed unit of measure.
- Preserve supplier-submitted unit labels separately from the normalized controlled unit.
- Buyers select missing or ambiguous units from a searchable, grouped controlled catalog rather than unrestricted text.
- The unit catalog covers manufacturing measures such as USD/part, USD/hour, USD/lb, USD/kg, USD/CWT, USD/ton, weight/part, time/part, time/cycle, pieces/time, length, area, energy, percent, and lump sum.
- Administrators may add or deactivate units without a software update.
- Historical unit definitions cannot be deleted or redefined.
- Forge X does not automatically convert physical units in governing evidence or primary comparisons.
- Future conversions are separate, explicitly versioned derived analyses.
- Different units remain valid evidence but are excluded from direct comparison and flagged `Unit Alignment Required`.

## 12. Staging and Commit

- Extraction may proceed into recoverable staging before all confirmation work is complete.
- Records missing mandatory identity, part description, currency, units, or required commercial context cannot enter the governing database.
- A large import may commit valid observations while unresolved observations remain staged.
- One invalid PBD cannot block hundreds of valid PBDs.
- Every scanned source tab ends with an explicit status: committed, duplicate, blocked, ignored, or failed.
- Import summaries reconcile all source tabs and explain every status.
- Blocked observations cannot affect analysis, weighting, benchmarks, profiles, or totals.
- A sourcing analysis may run with blocked PBDs, but the affected supplier package is incomplete and cannot receive a complete-package recommendation.
- A sourcing analysis may be finalized with open supplier actions when the immutable snapshot prominently identifies excluded evidence, incomplete coverage, actions, and limitations.

## 13. Import Context and Economic Age

Every PBD import requires one mutually exclusive commercial context:

### 13.1 Sourcing event

- Requires sourcing-event identity and source package number.
- Use the buyer-confirmed supplier-round submission date for economic aging.

### 13.2 Historical baseline

- Requires the buyer-entered estimated supplier quote year or date.
- Do not use the repository retrieval date or displayed PBD date as the economic date; those dates are rarely reliable submission dates.
- Preserve displayed PBD dates as source evidence only.

### 13.3 Three-year rule

- Economic relevance is governed by the best supported supplier submission date, not Forge import date.
- Evidence older than three years is retained permanently as `Historical Context Only`.
- Context-only evidence cannot affect supplier-profile status, market benchmarks, weighted populations, cost targets, opportunity, formula-performance totals, or current commercial conclusions.
- First-analyzed date remains immutable audit metadata but does not govern economic relevance.
- Year-only evidence that overlaps the rolling three-year cutoff remains context-only unless a more precise date establishes eligibility.
- Date confirmation can be applied to visible batches of no more than 20 observations from the same supplier and import batch.
- Do not provide a global apply-to-all action.
- Each row displays part number, description, supplier, source workbook, and worksheet before confirmation.
- Each observation retains its own date and whether it was assigned individually or through a batch action.

## 14. Source Packages and Scope Versions

- A sourcing-event import requires a source package number.
- The governing rule is one source package per commodity.
- Rare multi-commodity source packages require buyer confirmation and a reason.
- Partition a multi-commodity exception into commodity-specific populations. Each part and financial value enters only its governing commodity database and is never counted twice.
- One sourcing event may contain a primary source package plus revised, supplemental, or replacement source packages.
- Preserve all prior package identities and scope versions.
- When scope changes, classify each part as added, removed, replaced, corrected, or unchanged.
- Removed parts remain historical evidence but leave the governing current scope in the new baseline version.
- Scope changes cannot silently create supplier coverage failures or false APV movement.

## 15. Quote Rounds, Activation, and Carry-Forward

- A supplier's new PBD population initially forms a pending supplier round.
- Status is normally governed at supplier-round/package level rather than one part at a time.
- Before activation, compare the pending round with the prior active round on a common part and FPV population.
- Show parts reduced, increased, unchanged, added, removed, or missing; gross reductions; gross increases; net APV movement; prior-round movement; and cumulative movement from the initial round.
- The buyer activates the complete supplier round through one audited decision.
- Individual part-level activation is a documented exception.
- A new import never silently replaces the active supplier position.
- If a newer round omits a prior part, do not carry the prior price automatically.
- Show the missing part, description, last price, currency, source round, submission date, age, FPV significance, and available buyer action.
- Available decisions include `Carry Forward`, `Not Quoted`, and `Buyer Review Required`.
- Carry-forward may be confirmed in visible groups of no more than 20 parts from the same supplier round.
- Each carried price retains its original observation lineage; the newer round stores the buyer validity decision and does not pretend the supplier resubmitted it.
- Evidence older than three years cannot be carried into active status without new supplier confirmation or a new quotation.
- Carry-forward validates only the commercial piece price unless the supplier explicitly reconfirms the cost structure.
- Old cost details remain attributed to their original submission and age.

## 16. Historical Cross-Supplier Part Exceptions

- Multiple suppliers quoting the same part number is expected within a sourcing event.
- The same part under different suppliers always remains separate evidence.
- In historical-baseline imports, the same part under a different supplier is unusual and requires buyer confirmation and a reason, such as transition, dual source, or another documented exception.

## 17. Incumbency and Program Continuity

- Record incumbency at sourcing-event and part level, with buyer-confirmed batch assignment available.
- Incumbency is not a permanent supplier attribute.
- Do not ask buyers to estimate an unknown plant or capital reuse percentage.
- For successor programs with new part numbers, the buyer selects the predecessor program or source package representing the incumbent's prior business.
- Compare the incumbent's new quotation against both the broader current market and the supplier's own prior-program PBD/cost-model history.
- Evaluate piece price and supported cost structure, including burden, overhead, labor, operations, capital recovery, profit, and other disclosed buckets.
- Conclusions may state more expensive, relatively unchanged, improved, more competitive, or not comparable.
- Do not claim how much plant, equipment, or capital was reused without evidence.
- Begin with supplier program-level and cost-structure distributions. Use exact or buyer-confirmed part-family mappings only when a more direct comparison is justified.

## 18. GST, FPV, Targets, and Baselines

- Official GST and other governing sourcing inputs are immutable baseline versions.
- Corrections create a new baseline version with lineage, reason, identity, and timestamp.
- Finalized analyses continue referencing the baseline version originally used.
- Store FPV and related planning volumes as high-precision decimals and preserve exact submitted representation and units.
- Source-package scope versions distinguish official additions, removals, replacements, and corrections.

## 19. Analysis and Scenario Persistence

- Supplier-profile conclusions are versioned analytical results, not editable supplier-master fields.
- Buyer notes and classifications remain separate from calculated conclusions.
- Each profile identifies its evidence population, eligibility date, rule version, engine version, and calculation version.
- Working analyses may be recalculated from pinned evidence and assumptions.
- Finalized analyses store the complete result set plus exact inputs, assumptions, evidence membership, selected rounds, baseline versions, and governing rule versions.
- A finalized analysis is immutable.
- Each completed calculation run creates a draft scenario revision; incomplete keystrokes do not.
- Buyers may name important scenarios.
- Saved scenario revisions are never overwritten and remain available for later comparison.
- Every analysis snapshot contains an explicit evidence manifest listing included observations, excluded candidates, exclusion reasons, and analytical role.
- Do not rely solely on a query rule to reproduce a past population.

## 20. Supplier Profile Weighting and Comparability

- Calculate PBD-level measures to show behavior frequency across parts.
- Calculate event/round-level measures to show persistence across distinct commercial submissions.
- A large one-time package cannot manufacture a long-term pattern by record volume alone.
- Within the active three-year window, distinct events count equally.
- Display chronological and recency trends separately rather than applying an opaque recency weight.
- Display current-event competitiveness separately from sustained historical behavior.
- Calculate both raw quote-level and independent supplier-balanced market statistics.
- Supplier-balanced results govern market conclusions so suppliers with larger part populations do not dominate merely by record count.
- Always display quote count and independent supplier count separately.
- Outside an active sourcing event, use this comparability hierarchy:
  1. Exact part number.
  2. Buyer-confirmed comparable part family or process group.
  3. Broader commodity context labeled directional.
- Description similarity alone cannot establish economic comparability.
- Any family or process relationship affecting analytics is buyer-confirmed and versioned.

## 20.1 Initial capacity target

- Performance validation must test each commodity database with at least 250,000 committed PBD observations and 10 million detailed cost, formula, material, and operation records.
- This population is a minimum benchmark target, not a maximum supported size or retention limit.
- Forge X imposes no artificial evidence-retention-year limit and no hard-coded database-size ceiling below the practical limits of the approved database stack.
- The representative performance dataset must also exercise provenance, duplicate fingerprints, staging exceptions, quote-round membership, audit history, analysis evidence manifests, and persisted-summary integrity checks at scale.

## 20.2 Initial performance acceptance targets

- Measure local database performance on a defined standard approved business-laptop configuration at the full initial capacity benchmark.
- At the 95th percentile across repeated representative tests, local database identity validation, key release, integrity prechecks, and opening complete within 5 seconds, excluding any SharePoint transfer or restore time.
- Common searches, filters, summary screens, and part or supplier drill-down views return within 2 seconds.
- A full commodity-wide governing analysis recalculation completes within 60 seconds.
- Operations expected to exceed 2 seconds display meaningful progress and remain safely cancellable without leaving a partial governing transaction.
- Measure and report SharePoint download, upload, and publication verification separately because network and service conditions are external to local database-engine performance.
- On the same reference laptop, importing, fingerprinting, extracting, validating, and transactionally staging 1,000 qualifying PBD worksheets completes within 15 minutes.
- The import benchmark includes duplicate detection, recognized formula and value capture, source lineage, structure reconciliation, exception creation, and staging-transaction integrity, but excludes elapsed time waiting for buyer confirmations.
- Benchmark imports display meaningful progress and support safe cancellation without committing a partial observation or corrupting the recoverable staging population.

## 21. Audit Requirements

- The audit model must answer who, what, when, why, and how.
- Store timestamps in UTC and display them with the applicable user time zone.
- Every consequential manual change requires a reason.
- Consequential changes include analytical eligibility, identity, classification, mapping, active round, carry-forward, authority, ownership, scope, unit, date, and master-data relationships.
- Each audit event stores before state, after state, verified user, UTC timestamp, display time zone, workstation/session context, application version, method, and reason.
- Automatic actions use controlled system reason codes.
- Buyer notes are append-only. Corrections supersede earlier notes without editing or deleting them.
- Audit events use unique identifiers and a monotonic sequence within the database.
- Every immutable audit event is cryptographically linked to the preceding event through a tamper-evident hash chain covering the event sequence, content, and prior-chain value.
- Each SharePoint publication manifest records and digitally signs the final audit-chain value for that snapshot.
- Database opening, integrity validation, publication, restore, and reconciliation verify the audit chain and its applicable signed publication anchor. Missing, reordered, or altered events are treated as integrity failures and cannot be silently repaired.
- Imports, duplicate attempts, failures, publications, restores, migrations, conflicts, and reconciliation actions are auditable.

## 22. Initial Logical Table Families

Exact physical names, keys, and columns remain to be designed, but the approved model requires at least these logical families:

### Central registry database

- Registry version and publication manifest
- Organizational identity and user
- Buyer code and buyer identity
- Role and authority assignment
- Delegation and ownership transfer
- Commodity
- Vehicle line
- Program and predecessor/successor relationship
- Part master and program/vehicle applicability
- Supplier identity, aliases, family relationship, and plant
- Unit-of-measure definition
- Master-data request, approval, rejection, and correction
- Registry audit event

### Commodity database

- Commodity database identity and schema version
- Master-registry version cache
- Import session and import transaction
- Source occurrence, workbook, worksheet, and fingerprint
- Extraction result and exception
- Staged observation and blocking issue
- PBD observation
- Submitted field/value/formula lineage
- Cost section and cost element
- Material line
- Operation line
- Rate, amount, percentage, basis, and unit reference
- Structure classification and formula-integrity event
- Buyer interpretation and mapping version
- Sourcing event
- Source package and scope version
- Event part population and FPV/target baseline version
- Supplier quote round and round observation membership
- Active-round decision and part-level exception
- Carry-forward decision
- Historical-baseline context and economic-age classification
- Incumbency and predecessor-program relationship
- Analysis run and scenario version
- Explicit evidence membership and exclusion
- Calculation result and supplier-profile result
- Finalized analysis snapshot
- Buyer action, note, and resolution
- Publication snapshot, local checkpoint, sync queue, and conflict
- Immutable audit event and audit-event detail

## 23. Remaining Design Work

The following items are not yet approved at implementation detail:

- Final SQLCipher Enterprise provider, licensing, packaging, and validated deployment configuration
- Exact table and column names
- Primary keys, foreign keys, alternate keys, and check constraints
- Indexing and query-performance strategy
- Decimal precision and scale by measure type
- Detailed unit catalog entries and categories
- Exact cost-category taxonomy and allowed hierarchy
- Formula parser and reconciliation representation
- Confidence calculation for reusable mappings
- Administrator recovery and exceptional removal procedure
- Conflict-reconciliation user interface and merge mechanics
- SharePoint identity, lease, and publication API details
- Central-registry cache refresh and offline-expiration rules
- Exact supplier-profile calculation rules and traffic-light thresholds
- Detailed report and screen designs
- Forge 7 evidence-field-to-Forge X table mapping
- Regression, migration, reproduction, and corruption-recovery test plans

## 24. Next Recommended Design Step

Continue the one-question-at-a-time product-owner review. After the remaining domain rules are settled, produce:

1. Entity-relationship diagram.
2. Table-by-table data dictionary.
3. Key, constraint, and immutability matrix.
4. State-transition definitions for staging, quote rounds, analyses, and publication.
5. Query and indexing plan.
6. Forge 7-to-Forge X evidence mapping.
7. Synthetic validation dataset and database acceptance tests.
