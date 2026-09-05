# Supplier profile population review

Recorded: September 5, 2026

## Requirement and identified gap

The database design handoff section 13 excludes `Historical Context Only` evidence from supplier-profile status and formula-performance totals. Section 19 requires frozen analytical results to remain reproducible. Model requirements section 15.6 separates current conclusions from old supporting evidence.

The supplier activity population already consumed cutoff-specific economic-age decisions. The formula-exception population did not, allowing a context-only observation to contribute to a repeated-exception Red finding. It also selected round membership without checking when that membership was recorded.

## Implemented behavior

- Migration `0049` adds an immutable formula-population version to the profile reproduction manifest. Existing manifests receive `Legacy`; new profiles record `Economic Age and Cutoff v1`.
- New formula populations exclude an observation when its latest recorded economic-age decision at the profile cutoff is `Historical Context Only`.
- Later eligibility corrections affect later populations without changing a prior cutoff's membership.
- Observation, round membership, quote round, and sourcing-event creation must all exist by the cutoff.
- Source observations and formula exceptions remain retained. Exclusion changes the new analytical population only.
- The health gate reconstructs each profile under its retained population version. Migration does not rewrite prior findings, manifests, or traffic-light results.

The change consumes the existing economic-age ledger. It does not independently reclassify unassessed evidence or refresh stale age decisions. Prior profiles that used legacy rules remain historical records; rebuilding creates a new profile under the corrected rules.

## Regression coverage

Tests cover prevention of a current Red finding from context-only evidence, frozen cutoff reproduction after later decisions, restoration by a later eligible decision, exclusion of evidence whose source/round population did not yet exist, and preservation of legacy reproduction. Schema, migration, CLI, and full regression checks accompany this slice.

## Next development candidates

The broader engine remains incomplete. The next audit should examine:

1. Geographic scope: `build_supplier_profile` stores `region_code`, but its activity and formula-population selectors do not currently apply that region. Requirements section 15.4 calls for separate regional populations. Identify an authoritative regional evidence source before implementing this filter.
2. Economic-age freshness: current consumers rely on recorded eligibility decisions. Determine where the workflow must refresh aging for each new run and pin that decision without changing old snapshots.
3. Same-year supplier economics: implement supported labor, overhead, and profit rate distributions across distinct events, with compatible units and regional scope, before assigning any unapproved numerical traffic-light threshold.

Production SQLCipher/provider configuration and numerical supplier-profile thresholds remain pending approval, as recorded in the governing handoff.
