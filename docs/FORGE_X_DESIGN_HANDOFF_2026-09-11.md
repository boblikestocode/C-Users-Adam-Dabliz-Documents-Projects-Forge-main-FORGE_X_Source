# Forge X design handoff — September 11, 2026

Status: design and discovery handoff. No Forge X implementation changes are included in this handoff.

Project: `C:\Users\Adam Dabliz\Documents\Projects\Forge-main\FORGE_X_Source`

## Product direction

Forge X is intended to be a portable supplier cost intelligence engine that can be taken to different companies, commodities, manufacturing processes, and supplier document formats. Its purpose is to turn supplier-provided price breakdowns into traceable cost understanding, market comparisons, and actionable sourcing decisions.

The intended result is not only a normalized price table. Forge should be able to explain which cost or assumption creates a gap, why the comparison is valid, what evidence supports the conclusion, what evidence is missing, and what challenge, negotiation, sourcing, or process action should be considered.

## Foundation model

The engine should keep these layers distinct and connected:

1. **Source evidence** — original workbook/file identity, worksheet role, labels, values, formulas, notes, units, currencies, cell lineage, and unresolved content.
2. **Manufacturing structure** — parts, materials, operations, equipment, labor, time, quantities, yields, scrap, packaging, tooling, logistics, and production volumes.
3. **Cost meaning** — amount, rate, quantity, unit, basis, allocation method, included cost categories, dependencies, and calculation relationship.
4. **Economic evidence** — supplier quotes, transactions where available, external market observations, engineering estimates, dates, geography, specification, volume, and commercial terms.
5. **Knowledge and interpretation** — mappings, process classifications, unit conventions, reusable calculation rules, applicability scope, confidence, conflicts, and human confirmation.
6. **Analysis and action** — reconciliations, benchmarks, statistical models, scenarios, findings, uncertainty, proposed supplier questions, and sourcing actions.

The same source value may support several analyses, but each analysis must record its evidence population, interpretation versions, model/rule versions, assumptions, and limitations.

## Portability boundary

Reusable manufacturing knowledge should be separate from each company's private workspace. Company-specific identifiers, accounting conventions, approval policies, supplier evidence, contracts, and negotiated terms must remain scoped to that company. Shared or broadly reusable knowledge must carry evidence, provenance, applicability, effective dates, and approval status.

AI-assisted interpretation may propose worksheet roles, mappings, classifications, relationships, or knowledge candidates. It must not silently create governing knowledge, change immutable evidence, or turn a statistical association into a confirmed saving.

## PBD logic observed

The sample collection at `D:\All PBDS` contains 627 workbooks: cockpit components, cockpit labor, fascia, seats, and wiring. A structural scan found 621 OOXML workbooks, six legacy XLS files, and 29 worksheet-layout groups. Representative logic includes:

- cockpit and fascia material cost based on quantity, usage, unit price, transport, scrap, overhead, offal/resale, and special material categories;
- operation cost based on labor rate, operator count, cycle time, pieces per cycle, quantity per assembly, machine burden, and setup/batch assumptions;
- seat economics based on annual volume, shifts, hours, labor and fringe rates, variable and fixed burden, plant costs, capital, engineering, packaging, and amortization;
- wiring economics based on circuits, wire gauge, length, copper/aluminum weight, component quantities, skill-level labor rates, operation minutes, per-item/per-meter units, burden, and markup;
- scenario and generated-analysis workbooks mixed with supplier PBDs, requiring document-role classification before evidence enters market populations.

Worksheet names cannot govern inclusion by themselves. For example, a wiring workbook uses a `PBD Summary` worksheet as an input to linked calculations, while other workbooks use summary sheets that should not become independent analytical observations. Roles must be inferred from content, formulas, dependencies, and source context, then validated under the applicable policy.

## Actionable finding contract

Every supplier challenge or sourcing recommendation should identify:

- the challenged cost, rate, assumption, or relationship;
- the exact supporting evidence and source lineage;
- the comparison population and comparability conditions;
- the supported calculation or model result;
- the potential financial or commercial impact;
- the confidence and uncertainty;
- the supplier question, negotiation action, sourcing alternative, or information request;
- the evidence required to confirm or reject the action.

Findings must distinguish formula discrepancies, peer-price differences, modeled opportunities, directional signals, and confirmed commercial savings.

## Recommended design sequence

1. Define the common cost-evidence and comparability model.
2. Trace one complete operation-cost example, one annual-plant-allocation example, and one wiring material/labor example through source evidence, interpretation, normalized storage, calculation, and finding.
3. Compare those traces with the existing Forge X tables and identify missing concepts before extending the schema.
4. Establish the evidence and validation rules for a first market benchmark population.
5. Add statistical and regression models only after the evidence population, units, bases, and comparability rules are reliable.

The first implementation milestone should be a traceable evidence-to-action vertical slice, not a broad attempt to support every commodity at once.

## Open design questions

- What is the canonical representation for a cost relationship with multiple inputs and outputs?
- Which unit conventions must be company-configurable, including CWT/hundredweight and other commodity-specific bases?
- How should Forge classify a document as supplier evidence, buyer scenario, market benchmark, engineering estimate, or generated analysis?
- What evidence is sufficient to transfer a confirmed mapping from one supplier/template/process scope to another?
- Which external market sources are permitted, and how are their dates, definitions, and reliability recorded?
- Which findings may be published automatically, and which require buyer confirmation?
- How are private company data and shared manufacturing knowledge isolated in deployment?

## Boundary and limitations

This handoff records product and data-model direction. It does not claim that every workbook has been economically validated, that the current adapter supports every format, or that market-condition data is already available. The six legacy XLS files require a separately validated formula-capable adapter. Shared formulas, missing formula caches, errors, and summary-sheet roles remain evidence and validation concerns.
