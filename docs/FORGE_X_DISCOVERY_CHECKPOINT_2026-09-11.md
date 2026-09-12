# Forge X discovery checkpoint — September 11, 2026

## Resume instruction

Resume design work from `FORGE_X_DESIGN_HANDOFF_2026-09-11.md`. Do not begin a broad schema rewrite until the first three worked cost traces are agreed: operation costing, annual plant allocation, and wiring material/labor costing.

Read before the next implementation decision:

1. `..\AGENTS.md`
2. `docs\FORGE_X_MODEL_REQUIREMENTS.md`
3. `docs\FORGE_X_DATABASE_DESIGN_HANDOFF.md`
4. `docs\FORGE_X_DATABASE_LOGICAL_DESIGN.md`
5. `docs\FORGE_X_WORKBOOK_EXTRACTION.md`
6. `docs\FORGE_X_DESIGN_HANDOFF_2026-09-11.md`

## Verified discussion state

- User wants Forge X to be portable across companies, commodities, manufacturing processes, and supplier PBD formats.
- The database is intended to be the persistent evidence and knowledge backbone for normalized inputs and repeatable analyses.
- The engine must produce actionable sourcing and supplier-challenge information, including why a quote is not competitive and what action to take.
- Astra is intended as orchestrator; Sol-high is intended for bounded implementation and verification tasks when building resumes.
- Installed user skills: `statistical-analysis`, `statsmodels`, and `jupyter-notebook`.
- The sample PBD collection is at `D:\All PBDS`.
- A read-only structural scan cataloged 627 files, including 621 OOXML workbooks and six legacy XLS files, with 29 worksheet-layout groups.
- Representative logic review is recorded in `C:\Users\Adam Dabliz\forge_pbd_logic_review` and summarized in the design handoff. This review folder is outside the Forge X repository and is not part of the product source.

## Current decisions

- Preserve supplier-submitted structure and Forge analytical interpretation as separate representations.
- Preserve source evidence even when it is incomplete, ambiguous, aggregated, or excluded from a governing calculation.
- Never manufacture a labor/material/burden split that the supplier did not support.
- Treat source worksheet roles as content/dependency decisions, not universal name-based exclusions.
- Keep shared manufacturing knowledge, market evidence, and each company's private supplier evidence separately scoped.
- Treat regression and statistical output as evidence for investigation and decision support, with uncertainty and comparability conditions recorded.
- Do not infer a confirmed saving solely from a model residual, peer difference, or repeated observation.

## Next design task

Create a compact cost-evidence and comparability specification from three actual PBD traces:

1. Standard cockpit/fascia operation and material cost.
2. Seat annual burden and volume allocation.
3. Wiring circuit/material/labor cost with linked worksheet inputs.

For each trace, document source fields, units, dependencies, normalized concepts, validation rules, analytical eligibility, benchmark dimensions, and possible actions. Then review the existing schema against the specification and list only the required extensions.

## Do not claim yet

- Universal support for all workbook formats.
- Correct interpretation of every formula or instruction sheet.
- A complete market benchmark dataset.
- Production encryption, deployment, or identity integration.
- Confirmed supplier savings from the sampled workbooks.

## Validation and recovery

The prior implementation checkpoint remains authoritative for the current Forge X code and test history. This discovery checkpoint records design direction and PBD inspection only; it does not supersede the implementation validation in `FORGE_X_BUILD_2026-09-05.md`.
