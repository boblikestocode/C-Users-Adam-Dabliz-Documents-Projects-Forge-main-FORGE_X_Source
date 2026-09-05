# Forge X

Forge X is a clean-slate supplier cost intelligence and sourcing analysis product.

## Product boundary

- Build new architecture and new code for Forge X.
- Use Forge 8.0 only as a validated analytical and workflow baseline where explicitly selected.
- Do not modify, migrate in place, delete, or overwrite Forge 8.0 source, pilot files, evidence, or outputs.
- Port a Forge 8.0 behavior only after its business rule, evidence requirements, and expected result have been documented and validated for Forge X.
- Preserve immutable source evidence and historical outputs throughout development.

## Current design reference

The consolidated approved requirements and open decisions are maintained in `docs/FORGE_X_MODEL_REQUIREMENTS.md`.

The initial database-design package is maintained in:

- `docs/FORGE_X_DATABASE_DESIGN_HANDOFF.md`
- `docs/FORGE_X_DATABASE_LOGICAL_DESIGN.md`
- `docs/FORGE_X_DATABASE_INTEGRITY_AND_STATE_MODEL.md`
- `docs/FORGE_X_DATABASE_QUERY_AND_INDEX_PLAN.md`

## Current implementation status

Requirements consolidation and database design are in progress. The logical model, integrity/state model, and initial query/index plan are defined. Executable commodity core/intelligence migrations, the central-registry migration, a checksum-verifying migration runner, deterministic validation data, query benchmarks, the safe database service layer, and the first application-facing workflow CLI are scaffolded.

The database service layer provides:

- exact base-10 decimal parsing with Excel-compatible four-decimal rounding;
- time-ordered immutable technical identifiers;
- hardened SQLite/SQLCipher-compatible connections and transaction boundaries;
- cryptographically chained audit-event creation and verification;
- transactional supplier-activity and active-round decisions with evidence validation;
- reconciled workbook ingestion with recoverable staging and duplicate retention;
- supplier-specific quote-round comparison and audited carry-forward decisions;
- bitemporal corporate-knowledge promotion and specificity-based resolution with explicit conflict handling;
- immutable analysis scenarios, evidence manifests, calculation lineage, and finalized snapshots;
- rebuildable supplier-profile projections with separate record and independent-event measures;
- immutable buyer-action workflows and evidence-linked resolution suggestions requiring explicit buyer closure;
- verified checkpoints, signed publication manifests, append-only publication status, idempotent queueing, and restore validation;
- append-only central-registry governance with duplicate prevention, master-controlled corrections, and registry audit-chain verification;
- a database health and analysis-readiness gate covering migrations, audit chains, exact decimals, lineage, import reconciliation, finalized snapshots, and non-blocking formula advisories;
- restricted Excel-formula parsing, exact recalculation, and immutable formula-integrity evidence.
- immutable active-round piece-price projection generations and buyer comparison matrices.
- audited partial-round coverage decisions with distinct submitted, carried-forward, omitted, not-quoted, and review-required states.
- evidence-gated supplier rankings, peer-low gaps, GST target gaps, and incumbent/FPV savings metrics.
- complete-package APV, lifecycle spend, LTA, peak-volume, coverage, and multi-year NPV calculations.
- official target-package APV, annual and lifecycle target gaps, and basis-safe complete-package rankings.
- FPV-weighted quote-round evolution with common-part prior/cumulative movement and explicit scope-change counts.
- atomic commercial-result persistence with scenario-manifest enforcement and complete APV/NPV/ranking/evolution lineage.
- append-only GST take-rate validation and buyer exception/correction decisions with finalization-safe current-state resolution.
- source-linked SFT/PST/ED&D evidence and versioned payment-treatment classifications that preserve unconfirmed limitations and prevent unsupported partial allocations.
- lifecycle cash-flow and NPV integration that adds confirmed separate or partially amortized upfront costs exactly once while withholding only affected total-program comparisons when treatment is unresolved.
- buyer-confirmed, versioned functional part families with immutable membership, supporting context, and explicit field-by-field governing, directional, or not-comparable permissions.
- comparison-population resolution that prioritizes exact-part evidence and admits functional-family evidence only under the current buyer-confirmed permission, with its evidence class and rationale disclosed.
- rolling three-year economic-age classification with immutable individual/batch date confirmations, conservative year-only cutoff handling, and a strict 20-observation same-supplier/import-batch limit.
- economic-eligibility consumption gates that retain context-only records while excluding their values from active price projections, round evolution, supplier profiles, and finalized-analysis populations.
- exact-part-only U.S./Mexico evidence pairing and source-disclosed labor-rate reconstruction, with four-decimal component bridges and low-confidence results that never invent unsupported savings.
- exact cross-border operation matching that calculates burden opportunity only for identical equipment IDs or complete normalized equipment/process/unit signatures with aligned hours, currency, and rate units.
- reconciled aggregate-conversion comparison that supports supplier combined totals without manufacturing a labor/burden split and suppresses savings when submitted totals do not reconcile.
- consolidated buyer-review actions for incomplete cross-border reconstructions, linked to the immutable result and its explicit missing-evidence list.
- cross-border scenario-manifest enforcement and commercial-result persistence, with confirmed savings coded separately from low-confidence directional outputs.
- application-facing event workflow assessment that separates analysis, finalization, and publication readiness while treating open actions and unconfirmed payment treatment as disclosed advisories where approved.
- append-only import discovery inventories that require every discovered workbook to be registered or explicitly dispositioned before reconciliation can complete.
- deterministic finalized-output contracts and immutable artifact-validation history that pin each generated file to its evidence manifest, calculation output, frozen results, actions, and presentation rules.
- recoverable offline publication attempts with fresh-authority evidence, expected-predecessor conflict detection, immutable retry history, and remote-hash verification before publication succeeds.
- operator-facing continuity status and expired-worker recovery for local health, checkpoints, snapshots, publication queues, authority verification, and conflicts.
- immutable online-authority evidence with same-user/same-device offline access, an absolute 14-day write limit, immediate revocation locking, and 15-minute inactivity enforcement without stored credentials or keys.
- immutable checkpoint verification manifests with database identity, schema, audit sequence, record counts, registry versions, and representative analytical reproduction hashes carried into publication metadata.
- cutoff-correct, atomically manifested active-round projections that can be independently rebuilt from detailed evidence, including valid zero-row generations.
- cutoff-correct supplier-profile populations with frozen active windows and independently reproducible metric and descriptive-finding manifests.
- versioned formula-exception populations that honor economic-age decisions and source/round evidence cutoffs; prior profile snapshots retain their original population rules for reproduction.
- signature-verified, content-hashed central-registry cache generations with append-only activation history and automatic scenario-version pinning.
- layered source-fingerprint verification for exact workbook hashes, worksheet declarations, logical-duplicate lineage, and source-cell context, backed by immutable provenance rows.
- atomic event-summary generations with cutoff-correct supplier/action counts, immutable manifests, and independent health-gate reconstruction.
- complete Q01-Q15 benchmark coverage with expected-result assertions, p50/p95/max latency, target compliance, and captured query-plan fingerprints.
- rebuildable full-text and faceted search generations for events, packages, parts, suppliers, plants, buyer actions, and knowledge, with keyset pagination and health-verified cache integrity.
- cutoff-rebuildable part-history generations that keep exact-part evidence separate from buyer-confirmed functional-family evidence and enforce field-specific governing/directional permissions.
- publication-gating automated restore tests with manifest reproduction, immutable restore evidence, and buyer-configured recurring recovery-test policy/status.
- safe schema evolution that dry-runs the full pending chain, verifies preflight integrity, creates a consistent recovery checkpoint for existing databases, revalidates production, and records immutable execution evidence.
- append-only sourcing-event lifecycle transitions with immutable setup identity, finalized-snapshot enforcement, audit attribution, and cutoff-correct workflow/search/summary status resolution.
- append-only analysis and calculation-run lifecycles, including durable failed/cancelled worker runs that cannot expose governing results and finalized-output consumers resolved through verified current state.
- append-only import-session, transaction, source-occurrence, and staged-observation state ledgers, with governed safe-boundary phase advancement and independent chain/current-state verification.
- recoverable import attempts with immutable failure/rollback evidence, same-session retry sequencing, and atomic whole-session cancellation that retains all prior source evidence.
- independently reproduced calculation input manifests and health-gated lineage completeness for every completed run, including rejection of lineage outside the scenario's included evidence population.
- immutable calculation-output ordinals that make every completed result manifest independently reproducible and health-gate missing, duplicated, or reordered output evidence.
- database-enforced sourcing-event, supplier, part, and batch lineage for quote-round evidence, with PCE should-cost observations excluded from both quote rounds and supplier behavior history.
- atomic round-batch registration that retains same-part collisions as immutable conflicts, plus append-only attributed buyer resolutions with controlled later-round moves.
- dual-layer observation commit gates requiring canonical part identity, non-empty source identities and description, matching import context, confirmed historical economic date, typed fields, and an eligible currency/unit-qualified piece price.
- independently reproducible finalized snapshots with verified evidence hashes, exact frozen result content, referenced immutable action payloads, open-action counts, scope relationships, and retained audit anchors.
- append-only external-source availability history that distinguishes unknown, hash-verified, unavailable, and hash-mismatched workbooks while keeping ingested evidence analyzable.
- immutable formula-dependency status that preserves external-link expressions while distinguishing unverified, manifest-verified, and unavailable dependencies from parser support.
- central-registry enforcement of 10-character uppercase alphanumeric canonical part numbers, mandatory descriptions, and one immutable governing commodity assignment per part identity.
- master-authorized canonical part-description corrections that append immutable versions while preserving the stable part number and permanent commodity ownership.
- buyer-attributed source-package-number corrections with immutable before/after history, required rationale, and stable linked event evidence.
- cutoff-correct standardized event names in the form `source package - readable event name` for search and downstream presentation consumers.
- independent health-gate reconstruction of source-package mismatch evidence, linked action cardinality, decision chains, and correction-state consistency.
- finalized-output filename enforcement using the cutoff-correct standardized event identity, so later package corrections never rename historical artifacts.
- append-only, explicit batch confirmation of selected unresolved supplier identities with preserved provisional codes, submitted names, buyer attribution, and effective finalization resolution.
- buyer-proposed, master-decided supplier-family relationships with immutable membership history and no merging of supplier-code identities.
- independent registry-integrity reconstruction of supplier-family proposals, decisions, entity lineage, version chains, and active-membership cardinality.
- deterministic central-registry publication entities that expose only approved current supplier-family memberships for signed commodity-cache generations.
- immutable supplier-family executive profile roll-ups pinned to a signed registry generation and every contributing supplier-code profile, preserving scoped drill-down evidence.
- reproducible Red supplier-profile findings when confirmed formula-reconciliation exceptions recur across multiple PBD observations in the same supplier/plant/commodity scope, without changing current-event competitiveness results.
- append-only, evidence-linked current-event competitiveness observations that remain separate from historical supplier-profile findings and retain corrected Red/Yellow/Gray history.
- cutoff-reproducible supplier improvement assessments that label one supported recovery population `Recent Improvement`, multiple continuing Green populations `Sustained Improvement`, and genuinely uninterrupted multi-population performance `Consistently Competitive`.
- immutable current-versus-historical supplier context snapshots that preserve a supported Green current-event result while attaching a prominent, evidence-linked historical-risk notice for retained Red profile findings.
- independent health-gate reconstruction of current status, historical Red findings, notice wording inputs, evidence membership, and context manifests.
- immutable commodity-level supplier traffic lights with separate PBD-quality, current-market, labor, burden, overhead, profit, purchased-component, and below-the-line conclusions.
- non-compensating overall-status governance: high-confidence material or recurring Red categories control the result, Gray marks incomplete support, and Green requires every governed category to be supported and Green.
- independent health-gate reconstruction of category order, evidence scope, governing categories, professional explanation, overall status, and traffic-light manifests.
- immutable supplier-wide traffic-light roll-ups that retain every commodity assessment and allow any governing Red commodity to control the overall supplier status without concealing stronger commodities.
- independent health-gate reconstruction of supplier/commodity scope, cutoff eligibility, retained member statuses, governing commodities, explanation, and overall manifests.
- an in-program historical-baseline boundary with first-analysis aging fallback and immutable informational completion summaries for imports, duplicates, incomplete structures, formula/staging exceptions, and observed cross-supplier missing fields.
- independent health-gate reproduction of historical-baseline completion counts, missing-field disclosures, and summary hashes from immutable import evidence.
- immutable exact-supplier/exact-part comparisons from a historical baseline to later submissions, separating structure, observed field coverage, and formula-integrity trends.
- independent health-gate reconstruction of baseline selection, identity matching, field deltas, formula exceptions, trend classifications, and comparison hashes.
- append-only quote-version candidates with exact supplier/part/region/event scope, objective V1/V2 sequencing, duplicate-source suppression, `Pending Version Review`, buyer classification, explicit activation, and atomic supplier-requote confirmation.
- independent health-gate reconstruction of quote-version lineage, sequence, review-to-activation cardinality, and direct non-regressing activation chains.
- immutable quote-version movement generations that compare total price, labor, burden, overhead, profit, purchased components, and other cost evidence to both the prior and initial versions while explicitly retaining missing/unit/currency limitations.
- independent health-gate reproduction of every quote-version component delta, percentage, evidence link, comparison status, baseline selection, and movement manifest.
- source-package mismatch evidence that preserves both submitted and event numbers, opens one consolidated buyer action, and supports explicit supplier-correction or event-correction decisions without moving the PBD.

## Database prototype

Blocked staged observations now recover through append-only, attributable issue resolutions. The service recomputes unresolved blockers, records status-chain evidence, and only returns an occurrence to pending when every blocking issue has a current resolution.

Run the application-facing migration, health, and event-readiness commands:

```powershell
python -m app.cli migrate .\development\forge-x.db
python -m app.cli health .\development\forge-x.db
python -m app.cli event-status .\development\forge-x.db EVENT_ID
python -m app.cli status .\development\forge-x.db
```

Apply all migrations to a new or existing development database:

```powershell
python -m database.migration_runner .\development\forge-x.db
```

Create or upgrade the governed central registry:

```powershell
python -m database.migration_runner .\development\forge-x-registry.db --migrations .\database\registry_migrations
```

Run the database integrity tests:

```powershell
python -m unittest discover -s database\tests -v
```

Generate deterministic non-private validation data and benchmark critical queries:

```powershell
python -m database.validation.generate_synthetic .\development\forge-x-synthetic.db --profile smoke
python -m database.validation.benchmark_queries .\development\forge-x-synthetic.db --iterations 50
```

The `acceptance` profile is defined for 250,000 PBD observations and 10 million operation/material detail rows; run it only on the designated benchmark machine.

The prototype uses SQLite `STRICT` tables and SQLCipher-compatible SQL. It does not enable production encryption until the approved SQLCipher provider and key-management design are selected.
