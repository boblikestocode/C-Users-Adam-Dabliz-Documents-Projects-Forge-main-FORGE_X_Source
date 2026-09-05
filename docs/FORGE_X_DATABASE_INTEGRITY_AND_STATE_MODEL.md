# Forge X Database Integrity and State Model

Version: 0.1  
Recorded: September 2, 2026  
Status: Proposed implementation baseline

## 1. Integrity tiers

Forge X enforces rules at the lowest reliable layer:

1. Database keys and checks prevent structurally impossible data.
2. Transactional services enforce cross-row and authority rules.
3. Versioned analytical rules determine eligibility and calculations.
4. Audit events explain every consequential transition.
5. Integrity jobs verify hashes, manifests, projections, and reproduction.

Application validation improves usability but never replaces database enforcement.

## 2. Key and immutability matrix

| Entity family | Primary identity | Important alternate key | Update policy | Delete policy |
|---|---|---|---|---|
| Registry identities | Immutable technical ID | Effective-dated normalized business code | New version for governed change | No normal physical delete |
| Source workbook/worksheet/cell | Immutable occurrence ID | Parent + ordinal/location + fingerprint purpose | Append-only | Never |
| PBD observation | Immutable observation ID | Selected source occurrence commit | Append-only | Never |
| Cost/material/operation/formula evidence | Immutable detail ID | Observation + source location + semantic role | Append-only | Never |
| Sourcing event | Immutable event ID | Commodity + active source-package number | Readable metadata versioned | Never |
| Scope/GST baseline | Immutable version ID | Parent + version number | Append-only versions | Never |
| Supplier quote round | Immutable round ID | Event + supplier + positive round number | Metadata correction versioned | Never |
| Round membership/activity | Immutable event ID | Parent + observation + role | Append-only | Never |
| Activation/carry-forward decisions | Immutable decision ID | Parent + recorded sequence | Append-only; newer decision supersedes | Never |
| Functional family | Immutable family ID | Versioned readable name within commodity | New family version | Never |
| Knowledge | Stable item ID + immutable version ID | Scoped type + validity window | New bitemporal version | Never |
| Scenario/result/snapshot | Immutable revision/result ID | Parent + revision/result dimensions | Append-only | Never |
| Buyer action | Stable action ID + immutable state version | Event + issue identity where deterministic | New state version | Never |
| Audit/publication/restore | Immutable event ID | Monotonic sequence or package hash | Append-only | Never |
| Search/profile projection | Generation-scoped ID | Scope + generation + metric | Replace complete generation atomically | Old generation may be pruned after verified rebuild policy |
| Draft/queue/lease | Technical workflow ID | Idempotency key | Controlled mutable state | Only after terminal audit and retention rule |

## 3. Required database constraints

### 3.1 Universal

- Primary IDs are non-null and never reused.
- `recorded_at_utc` is non-null on authoritative records.
- `business_valid_to` is null or later than `business_valid_from`.
- A row cannot supersede itself.
- A superseding row belongs to the same stable parent identity and version domain.
- Controlled codes reference active or historically valid definitions; stored history remains valid after a code is deactivated.
- Currency and normalized unit are mandatory for analytically eligible commercial values.
- A precision-blocked value cannot be marked analytically eligible.
- Polymorphic lineage references are validated through a controlled entity-type registry and service-level existence check inside the same transaction.

### 3.2 Source reconciliation

- Every source workbook has one or more worksheet rows.
- Every scanned worksheet has at least one terminal source occurrence or an explicit ignored/failed worksheet result.
- Every source occurrence reaches exactly one terminal disposition: `Committed`, `Duplicate`, `Blocked`, `Ignored`, or `Failed`.
- A committed occurrence resolves to exactly one committed PBD observation.
- A duplicate occurrence identifies the governing fingerprint and prior occurrence.
- Import reconciliation counts equal the complete scanned worksheet/occurrence population before a transaction can be marked complete.

### 3.3 PBD evidence

- Every semantic submitted datum references a source datum.
- Exactly one typed representation is populated for a submitted datum unless the field explicitly stores a paired representation such as submitted text plus parsed exact decimal.
- Aggregate PBD classifications do not require operation rows.
- A `Detailed and Reconciled` classification requires the approved reconciliation results.
- Formula-derived opportunities require formula evidence, parser/rule version, and calculation lineage.
- PCE observations cannot be members of supplier quotation rounds or supplier behavior populations.

### 3.4 Sourcing and rounds

- Supplier round numbers are positive whole numbers and unique within event/supplier scope.
- New round imports begin pending and cannot become active without an explicit activation decision.
- Carry-forward references an earlier commercial observation and never changes that observation's source round or date.
- Evidence older than the approved active window cannot be carried forward without new confirmation evidence.
- A green complete-package status requires 100% confirmed coverage.
- Finalization requires supplier identity and take-rate blocking controls to be resolved as approved, but may retain other open actions with explicit disclosure.

### 3.5 Knowledge

- `Observed Evidence` requires one or more evidence links and cannot be selected as a corporate standard.
- `Confirmed Knowledge` requires confirmer, rationale, scope, business-valid period, and supporting evidence.
- `Corporate Standard` requires master-authorized approval and a governed taxonomy/rule identity.
- A candidate suggestion cannot be referenced as governing calculation lineage.
- Equal-specificity conflicts prevent automatic selection until an authorized resolution exists.
- Knowledge corrections create new versions and do not edit the prior payload or validity record.

### 3.6 Analysis and finalization

- Every completed calculation result references one completed calculation run.
- Every calculation run pins engine, rule, and calculation versions.
- Every scenario revision has an explicit evidence manifest containing included evidence and considered-but-excluded candidates with reasons.
- Every result has direct or transitive calculation lineage to evidence and explicit inputs.
- A finalized snapshot references exactly one completed scenario revision and calculation run.
- Finalized snapshots, results, manifests, actions, and presentation versions are immutable.

### 3.7 Audit and publication

- Audit sequences are strictly increasing within a database.
- Every audit event after the genesis event includes the preceding event hash.
- The event hash covers canonicalized event content, sequence, and prior hash.
- Publication manifests cover every package file and the final audit-chain anchor.
- A publication is successful only after remote verification of the uploaded package hash/manifest.
- A backup is valid only after an automated restore, integrity check, audit-chain verification, schema check, and representative reproduction check succeed.

## 4. State-transition model

### 4.1 Import session

```text
Created -> Discovering -> Extracting -> Awaiting Confirmation
        -> Staging -> Committing -> Reconciling -> Completed

Any active state -> Cancelling -> Cancelled
Any active state -> Failed
```

Rules:

- Cancellation completes the current safe boundary and rolls back any partial governing transaction.
- `Completed` requires a reconciled terminal status for every scanned source tab.
- Retry creates a new import transaction under the session; it does not erase the failed attempt.

### 4.2 Staged observation

```text
Extracted -> Needs Review -> Ready to Commit -> Committed
          -> Blocked
          -> Duplicate
          -> Ignored
          -> Failed
```

Rules:

- `Blocked` is recoverable through appended resolutions.
- Only `Ready to Commit` may transition transactionally to `Committed`.
- Commit creates the immutable observation and evidence graph atomically.

### 4.3 Supplier quote round

```text
Pending -> Under Review -> Confirmed Inactive -> Active
                         -> Rejected
Active  -> Superseded by later activation
```

Rules:

- Round records are not overwritten when state changes; decisions append to the activation history.
- Additional immutable batches may join a pending or confirmed round.
- A same-part conflict requires a decision before the affected record becomes governing.

### 4.4 Buyer action

```text
Open -> Sent to Supplier -> Supplier Response Received -> Resolved
  |                                                   -> Accepted Exception
  +--------------------------------------------------> Accepted Exception
```

Rules:

- Each transition creates a new `buyer_action_version`.
- `Resolved` requires resolution note, responsible buyer, and timestamp.
- `Accepted Exception` requires business rationale, authenticated buyer, and acceptance date.
- Automated matching may propose a resolution but cannot transition the action.

### 4.5 Knowledge candidate and knowledge version

```text
Suggested Candidate -> Dismissed
                    -> Confirmed Knowledge
Confirmed Knowledge -> Superseded
                    -> Expired
                    -> Proposed Corporate Standard
Proposed Corporate Standard -> Approved Corporate Standard
                            -> Rejected
```

Rules:

- Each approved assertion is a new immutable knowledge version.
- Status changes affecting business truth use supersession/versioning, not in-place payload edits.
- Workflow proposal state may be mutable but each transition is audited.

### 4.6 Analysis

```text
Working -> Calculating -> Draft Revision Complete
Working/Draft Revision Complete -> Finalizing -> Finalized
Calculating -> Failed or Cancelled
```

Rules:

- Each successfully completed calculation creates a scenario revision and result generation.
- Finalization pins all versions and copies durable snapshot results.
- Later changes create a new scenario or analysis snapshot; `Finalized` is terminal.

### 4.7 Publication

```text
Prepared -> Locally Verified -> Queued -> Uploading -> Remote Verification -> Published
                                      -> Retry Pending
                                      -> Conflict
                                      -> Failed
```

Rules:

- Queue processing uses an idempotency key based on publication ID and package hash.
- Retry never constructs a different package under the same publication identity.
- Conflict never resolves through silent last-writer-wins.

## 5. Transaction boundaries

The following operations must be atomic:

- committing one PBD observation and all required evidence/lineage rows;
- activating a supplier round and its explicitly selected exceptions;
- confirming a GST baseline and its validation results;
- finalizing an analysis snapshot and frozen result/action population;
- confirming or superseding a knowledge version;
- publishing a new profile/search projection generation;
- appending an audit event and updating the audit-chain anchor;
- applying one schema migration and its verification ledger result.

Large imports use many observation-level transactions inside one recoverable import session. One invalid observation cannot roll back hundreds of independently valid observations, but the reconciliation ledger must describe the complete population.

## 6. Concurrency model

The initial release assumes one local primary writer per commodity database.

- Opening for write acquires a database lease tied to authenticated user, workstation/session, database instance, and expiry.
- Read-only analysis and report rendering may use consistent database snapshots.
- Background projection builds read a pinned evidence cutoff and publish only a complete generation.
- SharePoint packages are immutable publications, not a shared live-database write surface.
- A conflicting remote publication pauses automatic publication and creates a reconciliation case.

## 7. Tamper-evident audit envelope

The canonical audit payload includes:

- database ID and audit sequence;
- event ID and event type;
- actor organizational identity and effective authority;
- UTC time and display time-zone identifier;
- workstation/session and application version;
- method and controlled reason code;
- affected entity IDs;
- canonical before and after state or content hashes;
- prior event-chain hash.

The event hash is computed only after canonical serialization is specified and versioned. Each publication digitally signs the applicable final chain value. Cryptographic algorithms and key custody remain part of the security implementation design.

## 8. Verification jobs

Forge X requires scheduled or event-triggered verification for:

- foreign-key and check-constraint health;
- audit-chain continuity;
- source and logical fingerprint consistency;
- completed-import reconciliation;
- finalized-snapshot manifest completeness;
- calculation-lineage reachability;
- projection generation consistency with its evidence cutoff;
- registry-cache signature and expiry status;
- publication manifest/package agreement;
- automated backup restore and representative analytical reproduction.

Failures create immutable integrity events and block silent publication or governing use as appropriate.
