# Forge X Model Requirements

Recorded: August 23, 2026  
Status: Consolidated design record for future implementation  
Scope: Buyer-facing local intelligence database, sourcing-event analysis, historical supplier profiling, multi-tab PBD ingestion, APV/NPV analysis, SharePoint continuity, and the future manager-facing boundary.

## 1. Purpose of This Document

This document is the authoritative working reference for the new Forge X product discussed with the product owner. Forge X will be designed and programmed as a clean-slate application. Forge 8.0 is a read-only behavioral and analytical baseline, not a codebase to be reprogrammed into Forge X. This document records approved decisions separately from unresolved design questions so future implementation does not silently convert suggestions into business rules.

The update has two equally important objectives:

1. Reduce processing time by extracting each PBD once, storing structured evidence, and generating future analyses directly from the database.
2. Build longitudinal supplier intelligence that identifies quotation behavior, economic inconsistency, cost-bucket movement, and negotiation opportunities across quote rounds and sourcing events.

The database is intended to eliminate repeated operational dependence on the Excel PBDs after successful ingestion. Source PBDs remain the original evidence and must never be deleted or overwritten.

## 2. Non-Negotiable Evidence and Safety Principles

- Never delete, overwrite, rename destructively, or otherwise remove source PBDs or prior results.
- Never overwrite an earlier database observation. New or corrected submissions create new immutable versions.
- Preserve the original supplier name, part number, region, plant, workbook name, worksheet name, source location, cell lineage, source fingerprint, and import timestamp.
- Detect identical reimports through fingerprints so the same file or tab does not create a false quotation version.
- Keep objective source facts separate from buyer-entered classifications and notes.
- Never invent missing cost elements, operation detail, or unsupported precision.
- Separate confirmed calculations from directional estimates.
- Completed analyses remain frozen and reproducible even when new evidence or a later engine version becomes available.
- New analyses use the evidence available at their run time and record their exact evidence population, engine version, rule version, and assumptions.
- Approved calculation logic should remain stable and deterministic. Versioning exists for controlled fixes and validated methodology changes, not silent analytical drift.

## 3. Operating Architecture

### 3.1 Persistent local database

- Maintain one persistent local commodity intelligence database across analyses and sourcing events.
- Event folders contain event-specific inputs, buyer templates, analysis packages, and generated reports; they do not contain separate independent databases.
- Open and extract each source workbook once, then run later calculations and report generation against structured database records.
- Target repeat-analysis runtime after ingestion is seconds to several minutes, with workbook rendering expected to become the main remaining duration.
- Use indexed normalized records and reusable evidence rather than repeatedly opening hundreds of Excel workbooks.
- A full rebuild option must remain available for validation and troubleshooting.

### 3.2 Data layers

The database should distinguish at least these layers:

- Source workbook and source worksheet identity.
- Immutable raw extracted values and formulas where relevant.
- Normalized supplier, part, plant, region, cost-element, operation, and unit records.
- Sourcing event, quote round, quote header, and buyer classification.
- Active-version designation separate from immutable version history.
- Calculation result with evidence population, assumptions, confidence, and engine/rule versions.
- Publication snapshot and recovery metadata.
- Audit history for buyer confirmations, ownership changes, restores, and publications.

### 3.3 Historical comparison window

- Use `First Analyzed Date` as the initial aging clock because dates embedded in retrieved PBDs cannot be relied upon.
- The first analyzed date is immutable for the same source fingerprint; reanalysis does not reset it.
- Keep records active for historical comparison for three years from first analyzed date.
- Preserve records older than three years for audit, but exclude them from governing comparisons.
- Legacy records imported into the first database release are active for three years and labeled as having an unverified business-effective date.
- Preserve an optional economic-effective or business date for future use, but do not require it for the initial release.

## 4. Source Retention, Backup, and Recovery

- Original PBDs remain untouched outside the database and are not required for routine reanalysis after verified ingestion.
- Create an automatic recoverable database backup before each import or version update.
- Provide a buyer-facing `Export Database Backup` function.
- Use timestamped, non-destructive backups.
- Encrypt local databases and published SharePoint packages.
- Access must be limited to authorized buyers and managers through controlled Forge functions rather than shared passwords.

### 4.1 SharePoint continuity

- Support an organization-approved SharePoint folder for each commodity.
- Do not operate directly on a live desktop database inside a synchronized SharePoint folder; synchronization and concurrent file access create corruption and conflict risk.
- Operate on a fast local database and automatically publish a validated, integrity-checked snapshot after successful imports or confirmed version updates.
- If SharePoint is unavailable, complete the local work, queue the publication, and retry automatically when connectivity returns.
- Display last successful publication time, pending publication status, and failures.
- Never replace a newer cloud snapshot with an older queued snapshot.
- A new buyer can restore the latest validated snapshot after fingerprint, integrity, schema, and version checks.

## 5. Roles and Future Manager Boundary

### 5.1 Approved permission model

- Use a single-writer, multi-reader model.
- The assigned commodity buyer controls PBD imports, version classifications, notes, and active-version decisions.
- Managers receive read-only access to published commodity snapshots, dashboards, analyses, supplier trends, and supporting evidence.
- Manager access must not lock or modify the buyer's local working database.
- A manager-controlled ownership-transfer function reassigns a commodity to a new buyer, grants the new buyer write authority, changes the prior buyer to read-only, and preserves all history.
- Record the authorizing manager, prior owner, new owner, and effective timestamp.

### 5.2 Separate future manager program

The manager-facing program is a future product and is not part of the immediate buyer model implementation. Retained requirements include:

- Read-only connection to each authorized commodity database snapshot.
- Consolidated portfolio reporting across buyers and commodities.
- Supplier trend and behavior views across authorized populations.
- APV, NPV, sourcing-event, opportunity, and evidence-status reporting.
- Commodity assignment and controlled ownership transfer.
- Snapshot freshness, backup integrity, publication health, and restore status.
- Role-based access and complete audit history.

## 6. PBD Ingestion Requirements

### 6.1 Supported workbook structures

Forge must support both:

- One PBD per workbook.
- Many PBDs on separate worksheets within one workbook.

Do not assume one workbook equals one PBD and do not rely on a single exact worksheet name.

### 6.2 Multi-tab discovery

- Open a workbook only once and enumerate every visible and hidden worksheet.
- Identify PBD worksheets through deterministic structural markers such as part number, supplier, plant, operation/material sections, and total selling price.
- Do not use fuzzy AI judgment as the governing tab classifier.
- Extract one independent PBD observation from each qualifying worksheet.
- Preserve workbook name and worksheet name for every extracted record and field.
- Ignore instruction, cover, and summary tabs as calculation sources.
- Flag ambiguous or incomplete PBD-like tabs for buyer review instead of silently skipping them.
- Report scanned tabs, extracted PBDs, ignored non-PBD tabs, exceptions, and reasons.
- Validate that the number of extracted database records reconciles to the detected qualifying PBD tabs.

### 6.3 Validated real test case

Test source:

`C:\Users\Adam Dabliz\Documents\Projects\Cost Models\Components PBDs\All Component PBDs\Quote with multi-tabbed PBDs\Mayco Quote_ JL-JT IP Components (6.16.2026).xlsx`

Observed structure:

- 53 worksheets total.
- 52 valid detailed PBD tabs.
- One `PBD Summary` tab.
- Detailed tabs run from `201 FCA PBD` through `254 FCA PBD`, with 227 and 228 absent.
- Each of the 52 detailed tabs contains the required PBD markers and a distinct part record for Mayco International LLC.
- Required ingestion result: one workbook open, 53 tabs scanned, 52 PBDs extracted, one non-PBD tab ignored.

### 6.4 Summary-tab rule

- Ignore supplier-created PBD summary tables as analytical inputs.
- The individual detailed PBD tab is the master evidence source.
- Do not use a summary price to override, average, fill, or reconcile a detailed PBD value.
- The detailed PBD price is expected to align with GST and is the contractually relevant commercial evidence.

## 7. Quote Completeness and Field-Level Eligibility

Forge must separate commercial-price eligibility from detailed cost-structure eligibility.

### 7.1 Commercial eligibility

- A valid final piece price on the detailed PBD remains eligible for supplier comparison, APV, NPV, and full-package ranking even when supporting cost details are incomplete.
- An incompletely substantiated supplier can still be the lowest commercial option and can be highlighted in green.
- Place `Cost Substantiation Required` beside the result when detailed support is incomplete.
- Use professional notes, for example: `Commercial quote included in APV and NPV evaluation. Detailed cost substantiation is incomplete. Buyer should obtain a completed PBD and reconcile the supporting cost elements with the supplier before final commercial alignment.`

### 7.2 Cost-element eligibility

- Use field-level validity rather than discarding every value from an incomplete PBD.
- Retain valid labor rates, labor dollars, net burden rates or dollars, overhead, profit, purchased-component, and other disclosed evidence for the calculations each field supports.
- A missing component lowers the scope or confidence of the detailed analysis; it does not automatically remove a valid final price from commercial evaluation.
- Add labor dollars and net burden dollars when both share a dollars-per-part basis.
- Do not add unlike rates or units, such as dollars per hour and percentages, without first converting them to a common supported dollar basis.
- Clearly disclose which parts of the record were included in price analysis, benchmark analysis, and cost-structure analysis.

## 8. Quote Versioning and Requotes

- Maintain a complete immutable version chain for repeated supplier submissions.
- Detect candidate versions using supplier, exact part number, region, program/sourcing event, and source identity.
- Assign an objective sequence such as V1, V2, V3, and so on.
- Allow buyer classifications: `Initial Quote`, `Requote`, `Correction`, `Final Offer`, and `Unclassified`.
- Allow a concise buyer description or note.
- Store the buyer identity and confirmation timestamp separately from source evidence.
- Show movement from the prior version and from the initial version.
- Track total price plus labor, burden, overhead, profit, purchased components, and other cost buckets to expose shifts between categories.
- Do not infer dishonest intent. Describe unsupported or shifted recovery factually and require substantiation where appropriate.
- Support batch classification and confirmation when one supplier submits a requote package covering many parts.
- A genuinely changed file or worksheet creates the next immutable version; an identical fingerprint does not.

### 8.1 Active version control

- A newly imported candidate does not silently replace the active quote.
- Store it as `Pending Version Review` until the buyer confirms that it is the latest PBD or quote for the exact supplier, part number, region, and sourcing event/program.
- The previously confirmed active version remains in current calculations until confirmation.
- Permit bulk confirmation of a complete supplier requote round.

## 9. Sourcing-Event Workflow

### 9.0 Controlled commodity setup window

- Provide an in-program commodity setup and selection window before sourcing-event analysis.
- Display buyer-entered fields for commodity code, buyer code, and the fixed business-facing commodity name.
- On first use of a commodity code, allow the authorized buyer to enter a name such as `Door Panel and Trim`.
- After confirmation, permanently bind the fixed commodity name to the commodity code.
- On later events, selecting the commodity code automatically retrieves the approved commodity name in a drop-down; buyers do not retype or rename it.
- Permit creation of a new commodity code/name relationship for a genuinely new commodity, such as `Instrument Panels and Dash` or `Consoles`.
- Link the permanent name to the commodity code, not the buyer code. Buyer responsibility can change without changing commodity identity or history.
- Treat buyer code as ownership/responsibility metadata that can be reassigned through the controlled manager workflow.
- Normal buyers and managers cannot break, rename, merge, or reassign a confirmed commodity-code/name relationship.
- Reserve commodity master-data correction authority to the product owner/master-user role.
- Record every master correction as a non-destructive audit event containing the prior value, revised value, reason, authorizing master user, and timestamp.
- Apply approved commodity master data consistently to database records, sourcing events, supplier profiles, traffic-light conclusions, SharePoint publication paths, and manager portfolio reporting.
- Do not use description inference to override a confirmed commodity-code/name relationship.
- Enforce a one-to-one relationship between each commodity code and one locked commodity name.
- Do not create or maintain a separate subcommodity naming hierarchy; it is redundant for the approved operating model.
- Keep supplier scoring and reporting governed by the confirmed commodity code and commodity name.
- Model buyer-code assignments separately from commodity identity as a many-to-many relationship.
- Permit one commodity code to have multiple active buyer codes when responsibility is shared.
- Permit one buyer code to be assigned to multiple commodity codes.
- Preserve assignment effective dates and history so ownership changes do not alter prior sourcing-event or analysis records.
- Do not rename, duplicate, or split the locked commodity record merely because buyer assignments change.
- Link each buyer code to the buyer's organizational identity and display name.
- Use the authenticated identity to attribute imports, quote-version confirmations, buyer notes, active-version decisions, publications, and other controlled actions without repeated manual name entry.
- Preserve the buyer code and authenticated identity on each audit event even after commodity ownership changes.

### 9.1 Event setup

When the buyer selects a sourcing scenario:

1. Enter a sourcing-event name.
2. Enter the first program year and program duration.
3. Start the event.
4. Forge creates a folder named for that sourcing event and a controlled volume template.

Recommended event structure:

```text
Forge X\Sourcing Scenarios\[Sourcing Event]\
├── 01 Volume Input\
├── 02 PBD Inputs\
├── 03 Analysis\
├── 04 Outputs\
└── Technical\
```

### 9.2 Buyer-entered volume template

- The buyer enters part number and part description.
- Provide one FPV column for each program year.
- The buyer manually transfers official FPV values from GST into Forge's stable, versioned template.
- Do not parse, learn, or automatically map the GST workbook because its format can change.
- Do not use CPV in cost calculations. CPV supports capacity/JPW planning, while Forge commercial calculations hold suppliers accountable to FPV.
- Buyer-entered FPV governs all suppliers in the event.
- Supplier-submitted PBD volume can be retained for audit and exception reporting but does not govern APV, NPV, spend, or opportunity.

### 9.3 Volume calculations

- Annual supplier spend = final piece price multiplied by buyer-entered annual FPV.
- Annual opportunity = supported opportunity per part multiplied by buyer-entered annual FPV.
- Lifecycle spend and opportunity use the sum of applicable annual values.
- Show every program year separately.
- Identify and visually highlight the program's peak-volume year.
- Determine peak year from total program FPV across included parts, not from supplier-specific figures.
- Family take-rate validation is a future/iterative capability. Buyer enters descriptions; Forge may later propose normalized functional families and positions for buyer confirmation.

### 9.4 Part-cost scope

- Forge X evaluates recurring piece-price economics and separately evaluates SFT, PST, ED&D, and other supported one-time targets and supplier quotations.
- Do not blend one-time costs into recurring APV without an explicitly supported amortization view.
- Preserve separate program-target, PCE should-cost, supplier-quote, amortization, and lump-sum comparisons.
- Provide an all-in program view only when every included category and recovery basis is supported and clearly disclosed.

## 10. Sourcing Comparison and Award Logic

### 10.1 Exact part-number rule

- The sourcing package is frozen; suppliers are expected to quote the exact same part numbers.
- Use exact normalized part-number matching as the governing sourcing-event rule.
- Revisions such as `TRMAA` and `TRMAB` remain separate parts.
- Related revisions may appear as supporting historical family evidence but cannot replace exact-match primary comparisons.

### 10.2 Quote coverage

- Missing required parts are `Not Quoted`, never zero cost.
- Calculate each supplier's quoted-part coverage percentage.
- Exclude incomplete-coverage suppliers from a full-package award comparison.
- Preserve their quotations for part-level negotiation intelligence.

### 10.3 Package award

- Default to one supplier receiving the complete book of business.
- Do not create a misleading synthetic award by combining each part's lowest supplier price.
- A supplier may price one family aggressively only because it expects the full package.
- Part-level lowest prices remain useful negotiation evidence but do not define the recommended package award.
- Split awards are rare, require explicit buyer override, and must warn that quoted economics may no longer remain valid.

### 10.4 Supplier layout and scale

- Support a flexible bidder count.
- Most events are expected to have no more than eight actual bidders, although more may occur.
- Adapt supplier panels for one to four, five to eight, and more than eight bidders.
- Preserve every submitted quote in an `All Quotes` long-form table even when the main comparison view is compact.
- Keep the main sourcing decision view one row per part.
- Use supplier names across columns in detailed bid leveling, with comparable cost buckets arranged consistently for across-the-row reading.

## 11. APV and NPV Requirements

### 11.1 APV

- Show annual purchase value for each year of the single program being sourced.
- Highlight the peak-volume year.
- Evaluate complete supplier packages, not fragmented lowest-price combinations.

### 11.2 Interactive NPV scenario tab

- Create a dedicated `NPV Scenario` tab rather than crowding the opening decision sheet.
- Buyer inputs one common annual LTA reduction assumption applied equally to all suppliers.
- Do not store or automatically apply supplier-specific LTA offers in this scenario tool; offers can change frequently during negotiation.
- Buyer inputs an analysis period of one to five whole years, limited to available FPV years.
- Buyer inputs the discount rate; default it to 7%.
- Highlight the lowest eligible complete supplier package in green.
- A commercially eligible supplier with incomplete cost substantiation may remain green but must show `Cost Substantiation Required` nearby.
- Assume a common currency, normally USD. Do not perform currency conversion in the initial release; clearly disclose the assumption.

### 11.3 LTA timing

- Year 1 receives the supplier's full quoted price for 12 months.
- The first LTA reduction applies in Year 2.
- LTA compounds annually thereafter.
- Example at 2%: 100%, 98%, 96.04%, and 94.1192% across four years.

### 11.4 NPV timing

- Year 1 is not discounted.
- The first discount occurs at the beginning of the second year / one period after Year 1.
- Formula basis: `NPV = APV1 + APV2/(1+r) + APV3/(1+r)^2 + ...`.
- Use year-specific buyer-entered FPV and the compounded common LTA assumption.

## 12. Buyer-First Workbook Presentation

### 12.1 Progressive disclosure

Opening tabs should be concise and decision-oriented:

1. Sourcing Decision.
2. Supplier Comparison.
3. Negotiation Plan.
4. Historical Challenge.

Place technical calculations, long explanations, and source evidence on later tabs or at the far right in collapsed column groups.

### 12.2 Comparison-table design

- Organize the sourcing comparison by part number.
- Keep supplier columns aligned left to right for easy comparison.
- Show submitted piece price, normalized comparable price, material, labor, burden, overhead, profit, future tooling/amortization treatment, Forge model cost, and negotiation opportunity when supported.
- Use Excel column grouping, compact widths, clear section dividers, frozen identifying columns, and restrained status colors.
- Do not force buyers to scroll through secondary percentages before reaching price, model cost, or total opportunity.

### 12.3 Neutral analytical language

- Forge presents evidence, calculations, confidence, and financial impact.
- Do not assign subjective `Large`, `Medium`, `Small`, `Good`, or `Bad` opportunity labels.
- Sortable annual savings and percentage fields let the reader determine significance.
- Annual FPV impact is the more important cash-flow measure; savings percentage remains visible as supporting context.
- A directional estimate is labeled `Directional Estimate — Low Confidence`, not `Large Potential`.

## 13. Cross-Border Exact-Part Methodology

### 13.1 Governing relationship rule

- An exact finished-part number in U.S. and Mexico PBDs establishes the primary production link even when supplier legal names differ.
- Supplier aliases such as Antolin/Grupo Antolin or Ultra Manufacturing/Mitchell Plastics can be retained as a secondary supplier-identity feature.
- Preserve supplier names exactly as submitted for audit.
- The exact part link prevents a cross-border opportunity from being missed solely because legal entity names differ.

### 13.2 U.S.-to-Mexico reconstruction

For an exact U.S./Mexico part pair:

1. Use the detailed U.S. PBD as the physical cost-structure baseline when it is the strongest valid evidence.
2. Preserve supported raw material, purchased components, labor hours, burden structure, and other legitimate physical costs.
3. Replace the U.S. labor rate with the lowest valid, recordable Mexico labor-rate benchmark for the aggressive case.
4. The prior evidence population indicated a benchmark of approximately $7.30/hour, but this must not be permanently hard-coded. Select it from valid evidence and disclose the source PBD, supplier, plant, part, and worksheet.
5. Recalculate labor dollars using supported labor hours.
6. Recalculate overhead dollars on the adjusted conversion-cost base using supported submitted percentage rates.
7. Recalculate profit dollars after adjusted overhead using the supported submitted profit percentage.
8. Compare the reconstructed Mexico model cost with the submitted Mexico price.
9. Show direct labor, burden, overhead, profit, total component opportunity, model cost, and any remaining price bridge without double-counting.

### 13.3 Trigger behavior

- Do not limit review to Mexico prices that equal or exceed the U.S. price.
- A Mexico quote that is slightly below the U.S. price may still contain a significant location-normalization opportunity.
- Attempt the supported reconstruction for every relevant exact U.S./Mexico pair.

### 13.4 Incomplete cross-border evidence

- If exact savings cannot be calculated, flag the PBD for buyer review and supplier completion.
- When partial evidence supports a range, show an aggressive and conservative directional range rather than one precise estimate.
- Label it `Directional Estimate — Low Confidence` and disclose missing fields, assumptions, benchmark sources, U.S. price, and Mexico price.
- Exclude directional estimates from confirmed savings totals.
- Require completed/substantiated evidence before promoting the result to confirmed or negotiation-ready.
- Do not estimate without an exact part-number relationship and sufficient supporting evidence.

## 14. Detailed and Aggregate Conversion Structures

### 14.1 Detailed operation match

- For operation-level cross-border burden calculations, require both the exact finished-part number and an exact normalized labor/burden operation match.
- A 100% confidence burden match requires all available identifying fields to agree, including normalized equipment description, size/capacity, process type, and unit basis, or an exact equipment identifier when available.
- Do not allow fuzzy text matching to generate burden savings.
- When detail is insufficient, show the rates as reference evidence without calculating an operation-level opportunity.

### 14.2 Expected cross-border behavior

- For an exact matched supplier/part/process, labor and burden descriptions should align after basic formatting normalization.
- Labor hours, equipment, cycle assumptions, and burden basis should remain consistent unless a process change is explained.
- Mexico labor, burden, and overhead rates should generally be lower than comparable U.S. rates.
- Equal profit percentages across U.S. and Mexico are reasonable and are not an opportunity by themselves.

### 14.3 Aggregate submissions

Some suppliers report generic totals or combine operations. Forge must support this without inventing detail:

- Preserve total quoted labor dollars and burden dollars.
- Preserve aggregate labor hours and rates when disclosed.
- If aggregate hours, labor dollars, and burden dollars reconcile mathematically, permit a confirmed aggregate conversion calculation.
- Identify the method as `Aggregate Conversion Comparison` rather than operation-level analysis.
- If the totals do not reconcile, provide only directional low-confidence evidence and request supporting detail.
- If only a combined labor-and-burden amount is available, preserve it as `Total Conversion Cost` and do not manufacture a labor/burden split.
- Compare supported combined conversion cost against the exact-part U.S./Mexico counterpart.
- When supported overhead and profit percentages and their bases are traceable, preserve the percentages and recalculate their dollar amounts from the adjusted combined conversion base.
- If the calculation basis is unclear, do not invent the cascade.

## 15. Supplier Historical Intelligence

### 15.1 Dual purpose

Supplier history supports both:

- Supplier profile and behavior/trend analysis.
- Current quote challenge and model-cost development.

Historical evidence should inform and contextualize current results but must not silently alter a current quote calculation. Present current evidence, historical evidence, and any combined interpretation distinctly.

### 15.2 Same-year cross-event comparison

- Separate sourcing events such as R7P, D6X, and Rampage remain distinct events and quote populations.
- Quotes from the same supplier across different source packages in the same year are the strongest economic-consistency comparison because the supplier would generally be expected to use similar contemporary economics.
- Do not assume consistency; analyze and report whether the economics are actually aligned.
- Older evidence within the active three-year window remains supporting history.

### 15.3 Appropriate comparison fields

Primary same-year economic consistency fields:

- Labor rates.
- Factory, head-office, R&D, and other overhead percentage rates where defined consistently.
- Profit percentage rates.

These should normally show limited variance within the same supplier, country/region, plant where relevant, and time period unless a commercial or operational explanation exists.

Physical quantities are part/process-specific:

- Labor hours.
- Cycle time.
- Material quantity.
- Equipment assumptions.
- Part-specific purchased content.

Compare physical inputs only when the parts or manufacturing processes are genuinely comparable.

### 15.4 Geographic separation

- Build separate supplier economic populations by country/region and, when supported, by plant.
- Compare U.S. rates with other U.S. evidence and Mexico rates with other Mexico evidence.
- Do not assume overhead or burden rates are identical across countries.
- Cross-country comparisons require the exact-part and exact-process rules defined above.

### 15.5 Burden and purchased-component caution

- Burden rates are difficult to compare without exact equipment/process evidence.
- Similar-sounding descriptions such as `100T press` are insufficient unless every required identifier agrees.
- Purchased components are difficult to compare by description alone.
- Do not generate purchased-component savings from fuzzy description similarity.
- Retain uncertain values for reference and review without creating a modeled opportunity.

### 15.6 Supplier position and behavior trajectory

- Present `Current Market Position` separately from `Supplier Consistency`.
- Current Market Position compares the supplier with the supported contemporary supplier market set.
- Supplier Consistency compares the supplier with its own same-year quotations across sourcing events and with its own active three-year history.
- Do not limit the profile to one calendar-year snapshot. Show the recent direction and the broader active-history trajectory.
- Explain whether supported behavior has improved, remained materially unchanged, deteriorated, or remained consistently competitive.
- Ground every trajectory conclusion in comparable evidence and show the underlying quotation periods, sourcing events, ranges, and sample counts.
- Records older than the approved three-year active window remain accessible for audit but do not govern the comparison or trend conclusion.
- Distinguish a supplier that quotes consistently but above market from one that is both consistent and competitive.
- Distinguish temporary improvement in one quote from a recurring improvement across multiple supported observations.
- Evaluate labor, burden, overhead, profit, purchased components, and below-the-line costs as separate trend categories.
- Do not allow improvement in one cost category to mathematically offset deterioration or unsupported recovery in another category through a blended score.
- Preserve a separate evidence population, confidence level, direction, and explanation for each cost category.

## 16. Supplier Profile and Executive Brief

### 16.1 Opening supplier profile

The opening profile should provide a compact same-year economics view:

- Labor-rate range.
- Overhead-rate ranges.
- Profit-rate range.
- Number of relevant quotes/PBDs.
- Movement across quotation rounds.
- Comparison to the supported same-region market set.

Detailed PBD and calculation evidence belongs on later tabs.

### 16.2 Supplier Executive Brief concept

Create a manager- and buyer-ready first page that prepares the reader for a supplier call. Use a three-column structure:

1. `Commercial Signal` — concise topic such as purchased content, labor economics, profit, or below-the-line costs.
2. `Executive Context` — why the topic affects the supplier's competitive position.
3. `Evidence and Recommended Discussion` — exact rates, ranges, counts, examples, benchmark, and professional buyer action.

Example style:

- Purchased content is the primary contributor to the supplier's higher modeled cost versus the supported comparison set; request component-level substantiation and validate make-versus-buy assumptions.
- Labor rates are elevated but closer to the competitive range than other cost elements; show the exact submitted rate and supported market median.
- Profit rates are materially above the supported comparison set across multiple PBDs; show affected/support counts and request consistent package alignment.
- Royalties, licenses, or amortized below-the-line costs materially increase quoted price; request cost basis, recovery period, volume assumption, and contractual applicability.

Use commercially direct, professional, evidence-based wording. Say `requires substantiation`, `unsupported recovery`, or `warrants validation` rather than asserting that a supplier is hiding costs unless intent is actually proven.

### 16.3 Confidence hierarchy and color treatment

- Place the strongest, highest-confidence supplier conclusions first in the Supplier Executive Brief.
- Order remaining observations from highest to lowest confidence so the reader encounters the most defensible commercial conclusions before investigation leads.
- Keep medium- and low-confidence observations in a separate `Items Requiring Validation` section below the primary executive conclusions.
- Use restrained confidence-based color coding consistently across the brief.
- Reserve green for supported competitive alignment, red for high-confidence material exceptions, amber for evidence requiring validation or buyer attention, and gray for unsupported or unavailable conclusions.
- Do not use color to imply supplier intent or an award decision. Color communicates analytical status, evidence strength, and need for attention.
- Display the confidence label in text as well as color so the meaning remains clear when printed or viewed without color.

### 16.4 Overall supplier traffic-light conclusion

- Provide an overall supplier traffic-light conclusion, but do not calculate it as a blended numerical score that allows unrelated categories to offset each other.
- `Green` is intentionally difficult to earn. It requires both a high-quality, sufficiently complete and reconcilable PBD population and the most competitive or demonstrably leading supported cost position.
- `Yellow` applies when the supplier provides sufficient usable evidence and is generally aligned with the market but is not the most competitive, or when limited correctable concerns prevent a green conclusion.
- `Red` applies when high-confidence evidence shows material uncompetitiveness, repeated serious cost-structure concerns, pervasive lack of substantiation, or numerous material exceptions requiring commercial intervention.
- A supplier with opaque or consistently incomplete PBDs cannot receive green merely because its submitted final prices appear low.
- A supplier with excellent PBD quality cannot receive green if its supported costs are materially uncompetitive.
- Always place a concise professional explanation beside the color, identifying the governing cost categories, evidence quality, recurrence, and recommended buyer focus.
- Retain the separate labor, burden, overhead, profit, purchased-component, and below-the-line conclusions beneath the overall status. The traffic light summarizes; it does not replace the evidence.
- One high-confidence red cost category governs the overall supplier status as red when the issue has meaningful commercial impact or recurs across multiple PBDs.
- Competitive results in other categories do not offset or average away a governing material red exception.
- Calculate and explain traffic-light conclusions at the commodity level before producing an overall supplier conclusion.
- Preserve different statuses across commodities; do not allow strong performance in one commodity to conceal weak performance in another.
- A high-confidence, materially red commodity governs the overall supplier status as red, while the executive brief identifies the exact commodity and cost categories responsible.

## 17. Confirmed Output Priorities

- Present quoted piece price early.
- Place labor, burden, profit, and overhead opportunities directly after it, followed by total opportunity and model cost.
- Keep annual FPV cash impact highly visible.
- Preserve detailed percentages and technical fields in collapsed groups or later tabs.
- Put narrative explanations at the far right or on later evidence tabs so they do not widen the first-read view.
- Use restrained column dividers and grouping to make the workbook easy to scan, compress, and expand.
- Always consider the buyer and manager reader; avoid confusing layouts and unsupported characterization.

## 18. Implementation and Validation Requirements

- Build a migration path from existing extracted Forge evidence into the new database without changing current validated calculations.
- Benchmark the 455-PBD population and record extraction, calculation, query, and rendering times separately.
- Add regression tests for one-workbook/one-PBD, multi-tab PBD workbooks, duplicate imports, multiple requotes, incomplete structures with valid prices, exact U.S./Mexico pairs, aggregate conversion records, and SharePoint offline publication queues.
- Require zero silently omitted workbooks or PBD tabs.
- Require database-to-workbook reproduction tests that regenerate the same results without reopening source PBDs.
- Validate every generated output against its stored evidence population and calculation version.
- Validate workbook readability in desktop Excel, including outline groups, frozen panes, input cells, NPV formulas, conditional formatting, and bidder scaling.
- Make all buyer inputs and overrides traceable.
- Keep confirmed savings, model ranges, and low-confidence directional estimates mathematically and visually separate.

## 19. Explicitly Deferred Features

- Direct GST file parsing or learned GST-layout mapping.
- CPV-based commercial calculations.
- Automated supplier alias/corporate-family management beyond necessary exact-part cross-border relationships.
- Fuzzy burden/equipment matching that generates calculated savings.
- Fuzzy purchased-component description matching that generates calculated savings.
- True concurrent multi-buyer writes to one shared database.
- Full manager-facing application and enterprise portfolio service.
- Currency conversion.

## 20. Unresolved Decisions and Next Questions

These items were not yet approved and must remain open:

1. Final quantitative evidence thresholds for supplier trend conclusions beyond the approved exact-match and confidence rules.
2. Final database technology, encryption implementation, SharePoint package format, and organization identity integration.
3. Final database schema, indexing strategy, migration tooling, and recovery procedure.
4. Final UI wireframes for database import, version review, sourcing setup, NPV scenario inputs, supplier profiles, and publication status.
5. Final definition and buyer-confirmation workflow for functional part families and take-rate sense checks.
6. Future input and treatment for tooling, ED&D, capital, and other one-time costs.
7. Future manager-facing dashboard measures and ownership-administration workflow.

## 21. Decision Summary

The approved direction is a non-destructive, local-first, encrypted supplier intelligence system that reads each PBD once, supports both single- and multi-tab workbooks, retains every quote version, performs fast repeat analysis from structured evidence, publishes validated SharePoint snapshots automatically, and produces concise buyer- and manager-ready commercial intelligence. It combines exact sourcing comparisons, FPV-driven APV/NPV package evaluation, cross-border location normalization, supplier economic consistency analysis, and fully traceable evidence without replacing buyer judgment with subjective labels.

## 22. Design Questionnaire Checkpoint: Questions 80–89

Recorded: August 23, 2026  
Status: Approved product-owner decisions.

### Identity and master authority

- Use the existing Microsoft organizational sign-in only for necessary identity, permissions, audit attribution, and authorized SharePoint access. Do not create a separate Forge X username/password system or unnecessary identity-management complexity.
- The product owner's organizational identity is the sole initial Forge X master account.
- Do not configure a backup master initially.
- Only the master user can correct, rename, merge, break, or otherwise revise a confirmed commodity-code/name relationship.

### Commodity creation and duplicate prevention

- A new commodity code/name relationship becomes active immediately after the buyer completes a clear confirmation step; it does not require advance master approval.
- Later correction of the locked relationship requires the master user.
- Block creation when the proposed commodity name already exists under another commodity code.
- Display clear language such as: `This commodity already exists. Select the existing commodity or contact the Forge X master user if a correction is required.`
- Do not create a second commodity history from a duplicate name.

### Sourcing-event identity and buyer context

- Assign every sourcing event an automatically generated, immutable Forge X event ID.
- Keep the buyer-entered event/program name as the readable label; similar names cannot merge histories because the event IDs remain distinct.
- After organizational sign-in, show buyers only the commodities assigned to their buyer codes.
- Managers and the master user can view their broader authorized populations.
- When one person has multiple buyer codes, require selection of the applicable buyer code during sourcing-event creation.
- Permanently record the selected buyer code with the event and retain the authenticated person in the audit history.

### Primary writer and delegated continuity

- When multiple buyers share a commodity, assign one primary buyer as the sourcing event's writer.
- Other buyers assigned to the commodity have read-only event access by default.
- Permit a manager to grant temporary event write authority to another assigned buyer when the primary buyer is unavailable.
- Temporary delegation requires a start date, end date, delegating manager, recipient buyer, scope, and immutable audit record.
- Delegation does not change the permanent commodity identity or erase the primary buyer's ownership history.

### Standardized SharePoint structure

- Forge X automatically creates and uses a standardized SharePoint commodity location derived from the locked commodity code and name.
- Do not allow arbitrary buyer-selected publication folders that could fragment or lose commodity intelligence.
- Create separate controlled areas beneath each commodity folder:

```text
[Commodity Code] - [Locked Commodity Name]\
├── Database Snapshots\
├── Sourcing Events\
├── Published Reports\
└── Recovery History\
```

- `Database Snapshots` contains validated encrypted database publications.
- `Sourcing Events` contains standardized event-specific folders and approved event inputs.
- `Published Reports` contains buyer- and manager-facing deliverables rather than technical working artifacts.
- `Recovery History` contains integrity-checked recovery packages, restore evidence, and recovery audit records.

## 23. Design Questionnaire Checkpoint: Questions 90–99

Recorded: August 23, 2026  
Status: Approved product-owner decisions.

### Source package as the governing business identifier

- Require the buyer to enter the source package number when creating every sourcing event.
- Treat the source package number as the primary business lookup and grouping field used by buyers and managers.
- Retain the automatically generated immutable Forge X event ID as the technical identity that prevents collisions.
- One source package number represents one sourcing event.
- Store initial quotations, requotes, corrections, analyses, and final offers as separate rounds or versions beneath that source package.
- Display the source package number prominently in searches, dashboards, manager views, report titles, workbook names, and SharePoint paths.
- If an entered source package number already exists, open the existing sourcing event and offer to add a new quote round; do not create a duplicate sourcing event.

### Partial requotes and unchanged parts

- Continue to ingest and assess every supplier upload, even when only a subset of parts changed.
- Distinguish an identical duplicate file, an unchanged/reaffirmed part in a new round, a changed part, an added or removed part, and a partial requote.
- Use workbook and worksheet/record fingerprints so a changed multi-tab workbook does not cause unchanged tabs to become false cost changes.
- For every supplier round, report the count and percentage of changed, unchanged, added, removed, and omitted parts.
- Show total package-price movement and the cost categories that changed.
- Retain supplier behavior showing repeated selective changes across quotation rounds.
- When a supplier submits only changed PBDs, require buyer confirmation of whether omitted parts carry forward at the prior active price or become `Not Quoted` in the new round.
- Permit the buyer to apply one omission treatment to the full supplier package and then change individual exceptions before confirmation.
- Preserve the omission treatment, exceptions, confirming buyer, and timestamp in the audit history.

### Standard source-package naming

- Begin sourcing-event folder and report names with the source package number, followed by the readable event name.
- Example: `SP123456 - D6X Instrument Panel`.
- Apply this convention consistently to local event folders, SharePoint event folders, workbooks, reports, dashboards, and search results.

### Source-package mismatch controls

- When a PBD contains a readable source package number, compare it with the buyer-entered event number.
- Never silently move or reassign a PBD when the two values disagree.
- Show both values and flag `Source Package Mismatch — Review Required` for buyer confirmation.
- Permit a buyer to correct an incorrectly entered event source package number through a controlled correction.
- Record the original number, corrected number, required reason, buyer identity, and timestamp.
- Safely update linked event records and standardized folder/report naming without deleting historical evidence.
- If the supplier's PBD contains the wrong number, allow the analysis to continue after buyer confirmation.
- Keep the PBD in the buyer-confirmed event under `Supplier Correction Required` and preserve the mismatch evidence.
- Do not allow a supplier administrative error to stop the full sourcing analysis.

### Consolidated buyer action list

- Create one consolidated action list for every sourcing event.
- Include source package mismatches, incomplete cost details, reconciliation issues, missing support, pending version confirmations, supplier corrections, and other unresolved evidence items.
- Use one row per supplier/part issue.
- Keep these fields visible in the compact opening view: `Supplier`, `Part Number`, `Issue`, `Financial Impact`, `Required Supplier Action`, `Owner`, and `Status`.
- Place technical evidence, assumptions, source lineage, and longer explanations in expandable columns to the right or linked evidence views.
- Link each action directly to the governing PBD record, worksheet, calculation, and supporting evidence.
- Use professional, concise action language and avoid scattering unresolved items across unrelated tabs.

## 24. Design Questionnaire Checkpoint: Questions 100–109

Recorded: August 23, 2026  
Status: Approved product-owner decisions.

### Buyer action status and closure controls

- Use a fixed action-status dropdown: `Open`, `Sent to Supplier`, `Supplier Response Received`, `Resolved`, and `Accepted Exception`.
- Provide an optional working-note field without allowing free-text replacement of the controlled status.
- Selecting `Accepted Exception` requires a business rationale and automatically records the authenticated buyer and acceptance date.
- Selecting `Resolved` requires a concise resolution note, responsible buyer, and resolution timestamp.
- When a later quotation round appears to supply the missing information for an open issue, Forge X suggests a potential match and possible resolution.
- Automated matching never closes the issue; the buyer must confirm resolution.

### GST reference without file duplication

- Do not copy, attach, or store duplicate PBD workbooks merely to support an action item or resolution record.
- The governing PBD already resides in GST; Forge X stores the structured extracted data required for future analysis and supplier intelligence.
- Retain identifiers and lineage such as source package number, supplier, part number, quotation round, source filename/worksheet when available, import fingerprint, and buyer resolution note.
- Do not require a corrected PBD number because supplier corrections normally arrive as later quotation rounds rather than newly numbered PBDs.

### Supplier-specific quotation rounds

- Prompt the buyer to confirm the quotation round during import.
- Track quote rounds separately for each supplier within a source package because suppliers can progress through negotiation at different speeds.
- Do not cap the number of rounds at eight; support any positive whole-numbered round.
- Permit an optional concise round description, such as `Post-Tech Review` or `Final Commercial`.
- Store whether the confirmed round becomes the supplier's active commercial position.
- Link issue resolution to the later structured quote-round record rather than to a duplicated attachment.

### In-program supplier/round confirmation

- When an upload contains PBDs for multiple suppliers, show all detected suppliers in an in-program review window.
- Require the buyer to confirm the applicable round for each supplier before import.
- Do not create an Excel template or external setup file for supplier/round confirmation.
- Support batch confirmation and efficient keyboard/filter controls for large populations.

### Staggered and conflicting submissions

- Permit additional PBD batches to be added to an existing supplier round because suppliers commonly deliver one commercial round in stages.
- Check additions for duplicate fingerprints and conflicting part records.
- If the same supplier submits a different price or structure for the same part within an existing round, preserve both immutable observations.
- Require the buyer to decide whether the newer submission replaces the earlier active record within that round or belongs to a new round.
- Never overwrite or silently activate the newer record.
- Preserve the prior observation, buyer decision, reason/context, and timestamp.

## 25. Design Questionnaire Checkpoint: Questions 110–119

Recorded: August 23, 2026  
Status: Approved product-owner decisions.

### Active round and comparison context

- Default current sourcing comparisons to each supplier's latest buyer-confirmed active round.
- Permit the buyer to select earlier supplier rounds for negotiation-history analysis.
- Display the selected supplier-specific round number and buyer-confirmation date directly beneath or beside the supplier name in comparison views.
- Preserve the exact selected rounds in every analysis snapshot so later active-round changes do not alter completed results.

### Compact Quote Round Evolution

- Create a high-level `Quote Round Evolution` view with one column per supplier round.
- Show the overall percentage movement from each prior round and cumulative movement from the initial round.
- Use total package APV calculated from the same buyer-entered FPV as the governing financial basis.
- Keep detailed part-level changes available in later drill-down views rather than crowding the opening evolution table.
- When scope differs between rounds, calculate the displayed price-movement percentage only on the common-part population.
- Label common-scope calculations clearly and disclose added, removed, omitted, unchanged, and carried-forward parts separately.
- Do not allow a changing part population to create a false impression of negotiated price improvement or deterioration.

### Package coverage safeguards

- Show both `Part Coverage %` and `FPV-Weighted Coverage %` for each supplier and selected round.
- Part Coverage % equals commercially covered required parts divided by all required parts.
- FPV-Weighted Coverage % uses total lifecycle FPV across the selected analysis years.
- Separately identify missing parts with significant peak-year volume.
- A supplier with genuinely missing or `Not Quoted` parts cannot receive a green package status or be identified as the lowest complete-package award option.
- Prior-round prices count toward current full-package coverage only when the buyer explicitly confirms they remain valid and carries them forward.
- Otherwise label APV and NPV as `Partial Package — Not Comparable for Award`.
- Require 100% confirmed package coverage for a green sourcing-event status because the normal award is all-or-nothing.

### Current event versus historical supplier profile

- Display `Current Event Competitiveness` separately from the broader `Historical Supplier Profile` traffic light.
- Do not let a historical red status overwrite an independently supported current-event result.
- A supplier may be green in the current event and red historically.
- When this occurs, preserve the green current result and display a prominent historical-risk notice explaining the prior supported behavior.
- Treat current improvement as a positive signal and continue tracking whether it persists in later sourcing events and quote rounds.
- Do not erase or revise the prior historical statuses when current behavior improves.

## 26. Design Questionnaire Checkpoint: Questions 120–129

Recorded: August 23, 2026  
Status: Approved product-owner decisions.

### Recent versus sustained supplier improvement

- Label one supported improved sourcing event as `Recent Improvement`.
- Reserve `Sustained Improvement` for competitive behavior that persists across multiple later sourcing events or supported quote populations.
- Do not let one good quotation erase a longer-term pattern.
- Preserve prior red and yellow history while showing current improvement as a positive development to continue monitoring.

### Build Historical Baseline function

- Add an in-program `Build Historical Baseline` function for buyers to ingest the latest PBDs available for their commodity.
- The available files represent the supplier's current PBD structures, not necessarily original award-price evidence; technical changes may have altered current price and operations.
- Accept folders containing single-PBD workbooks and multi-tab PBD workbooks.
- Extract and store the supplier-provided data exactly as available, including incomplete PBDs and problematic formula structures.
- Deduplicate identical evidence without deleting source records or hiding the duplicate-import event.
- Permit baseline records with or without source package numbers.
- Store a source package number when readable; do not require or manufacture one when absent.
- Keep historical-baseline populations separate from formal sourcing events unless a valid source-package relationship is supported.
- Use the first analyzed date as the initial baseline aging date under the approved three-year active comparison rule.
- Commit the available baseline population automatically without a pre-import approval screen.
- After import, show an informational completion summary containing imported record count, duplicates detected, incomplete PBD count, exceptions, and fields commonly missing by supplier.
- The completion summary does not block or require buyer approval.
- Use future submissions to measure whether supplier PBD completeness, formula integrity, and economic behavior improved, remained unchanged, or deteriorated relative to the observed baseline.

### Formula integrity after technical changes

- Recognize that technical changes can add or remove operations and alter the current piece price without changing the governing rate structure.
- Preserve labor rates, burden rates, overhead percentages, profit percentages, calculation bases, formulas, and submitted dollar amounts from each observed PBD.
- Detect when a supplier retains a stated percentage rate but submits overhead or profit dollars greater than the amount produced by the current reduced cost base.
- When the PBD clearly provides the stated rate, calculation base, and submitted amount, calculate the internally provable excess as a confirmed opportunity.
- Recalculate in dependency order: current labor/burden base, factory overhead, head-office overhead, R&D overhead, then profit on the correct adjusted base.
- Show each formula exception separately and then one reconciled total piece-price opportunity without double-counting.
- Use professional language such as `Formula Reconciliation Exception` and request a revised PBD with functioning formulas and aligned commercial pricing.
- Repeated confirmed formula-reconciliation exceptions across multiple PBDs create a red supplier-profile finding for the affected commodity, even when the displayed percentage rates appear market-aligned.

### Accepted PBD structures and quality categories

- Distinguish a valid aggregate conversion submission from a genuinely incomplete PBD.
- Do not penalize a supplier merely because it uses an accepted consolidated labor-and-burden format.
- Treat an aggregate PBD as sufficiently substantiated when it provides a valid final price, identifiable material/purchased content, total conversion cost, stated overhead and profit rates, and mathematically reconcilable dollar amounts.
- Do not require individual operation detail to classify such a record as valid aggregate evidence.
- Use transparent structure categories rather than a numerical PBD-quality score:
  - `Detailed and Reconciled`
  - `Valid Aggregate`
  - `Incomplete`
  - `Formula Reconciliation Exception`
- Preserve specific missing fields and reconciliation evidence beneath the category.

## 27. Design Questionnaire Checkpoint: Questions 130–139

Recorded: August 24, 2026  
Status: Approved product-owner decisions.

### Supplier identity hierarchy

- Use the PBD supplier code as the primary supplier identifier when available.
- Preserve the supplier name exactly as submitted, together with legal entity, plant, and region evidence.
- Keep different supplier codes distinct by default even when they appear to belong to the same corporate organization.
- A buyer may propose a supplier-family relationship.
- Only the master user can approve, change, merge, or remove a supplier-family relationship.
- An approved family relationship enables executive corporate roll-up reporting but does not merge or overwrite the underlying supplier identities.
- Keep supplier code, legal entity, plant, and region separate for labor, burden, overhead, profit, and other economic benchmarking so corporate roll-ups do not distort local economics.

### Missing and conflicting supplier codes

- A missing supplier code does not stop PBD extraction or the sourcing analysis.
- Continue current-event commercial analysis under the supplier name exactly as submitted.
- Mark the record `Supplier Code Confirmation Required`.
- Exclude unresolved identities from historical supplier roll-ups and corporate-family conclusions until confirmed.
- Place every unresolved supplier-code item in the consolidated buyer action list.
- Present all unresolved supplier-code records together in one in-program post-analysis confirmation queue.
- Allow explicit batch confirmation for selected records; never infer that every similar unresolved record belongs to the same identity without buyer confirmation.
- The buyer can select an existing confirmed supplier code or enter the correct code.
- If an entered supplier code conflicts with a different confirmed supplier identity, block final assignment and require review and final relationship confirmation; master approval governs any identity merge or family relationship.
- Once confirmed, apply the identity to every buyer-selected matching provisional historical record while preserving the original submitted names, prior provisional identity, confirming buyer, and timestamp.
- Require confirmation of all unresolved identity items before the sourcing event can be finalized.

### Finalized analysis snapshots

- Permit analysis to continue while supplier identities remain unresolved, but do not allow the event analysis to be marked `Finalized` until all supplier-code items are confirmed.
- Once finalized, make the analysis snapshot read-only and immutable.
- Preserve the selected supplier rounds, evidence population, FPV, assumptions, engine/rule version, calculations, statuses, and outputs used by that snapshot.
- Later supplier submissions create a new quotation round and new analysis snapshot rather than altering the finalized result.
- Finalized snapshots are mandatory for historical tracking, reproduction, and audit traceability.

## 28. Design Questionnaire Checkpoint: Questions 140–149

Recorded: August 24, 2026  
Status: Approved product-owner decisions.

### Finalization with open supplier actions

- Assess the business case using the evidence available at the time of analysis rather than waiting for an ideal, fully complete supplier population.
- Permit an analysis to be finalized while supplier action items remain unresolved.
- Freeze every open action, status, financial impact, required supplier action, owner, and evidence limitation into the finalized snapshot.
- Label the report `Finalized Analysis — Open Supplier Actions Remain` and show the open-action count prominently.
- Finalization records the decision state; it does not imply that every supplier issue was resolved.

### Assumption and negotiation scenario versioning

- Never rewrite a finalized result when FPV, LTA, discount rate, program-team inputs, cost-engineering assumptions, or supplier reductions change.
- Create a new scenario version with lineage to the finalized baseline.
- Support side-by-side comparison of the finalized baseline and later negotiation scenarios.
- Use progressive disclosure: high-level APV, NPV, incremental improvement, supplier ranking, target gaps, and open actions first; supplier, part, cost-bucket, and assumption detail on later tabs.
- Buyers may need additional LTA or price improvement after program-team or cost-engineering assessments; preserve each scenario and its assumptions independently.

### Company target baseline and round progression

- Define `Baseline APV` as the official company source-package target APV, not the supplier's first-round APV.
- Measure each supplier round against both the company target and the preceding comparable supplier round.
- Create a compact high-level round progression using visually separated column groups.
- Each supplier-round group should show APV, gap to company target, selected LTA lifecycle impact, program NPV, comparable-scope movement from the prior round, and package coverage.
- Use narrow colored divider columns between the company target and supplier rounds.
- Use green for supported cost improvement, red for supported cost increase, gray for no material movement, and amber when coverage or evidence prevents a clean comparison.
- Allow older round groups to collapse while keeping the company target and latest round visible.

### GST source-package inputs

- GST provides official part-level piece-price cost targets and volume data in fixed export templates.
- Use official GST file import as the recommended default input method.
- Retain an in-program table for manual entry, correction, copy/paste, and fallback.
- Do not require an external Forge Excel template for setup.
- Implement deterministic, version-controlled GST import adapters; do not train a model to guess changing layouts.
- Validate expected headers, data types, source package number, exact part numbers, descriptions, program years, FPV, cost targets, duplicate parts, missing parts, and totals.
- Reconcile the part population across the applicable two or three GST exports.
- If an unknown GST template is encountered, fail clearly and route the buyer to the in-program fallback rather than guessing column meanings.
- Show imported totals and validation results before the buyer confirms the official sourcing baseline.

### SFT, PST, and ED&D treatment

- Keep Supplier Forming Tooling (`SFT`), Peripheral Supplier Tooling (`PST`), and Engineering Design and Development (`ED&D`) as separate commercial categories.
- SFT is Stellantis-specific, design-specific supplier tooling generally paid as a lump sum.
- Preserve SFT targets and quotations against the exact GST-assigned part number or supported part group.
- PST is reusable supplier plant equipment or capital such as injection molding presses, wire-processing equipment, lasers, conveyors, and similar assets.
- Evaluate PST separately and expose ownership, reuse, utilization, and full-program-recovery assumptions rather than treating it as customer-specific SFT.
- ED&D is program-specific engineering and development and remains separate from physical tooling.
- Permit company and PCE should-cost targets for SFT, PST, and ED&D to be entered as total amounts because those categories are commercially assessed as lump sums.
- Do not automatically spread a lump-sum target across parts unless the buyer explicitly defines the allocation.
- If GST assigns the complete amount to one part number, preserve that assignment.
- Where amortization is evaluated, support a buyer-confirmed 3-year or 5-year recovery period using 80% of the applicable official FPV sum as the amortization-volume basis.
- Calculate and disclose the resulting amortized amount separately from the original lump-sum target or quotation.

### PCE North Star should-cost analysis

- Preserve three distinct benchmarks: official company program target, PCE North Star should cost, and supplier commercial quote.
- Treat the company target as the official RFQ-release baseline the buyer is expected to beat.
- Treat the PCE should cost as the ideal or North Star model, commonly based on highly efficient assumptions and generally received after initial supplier quotations.
- Allow the buyer to upload PCE revised PBDs and classify them explicitly as `PCE Should Cost`.
- Do not treat PCE PBDs as supplier quotation rounds or as supplier behavior evidence.
- Associate each PCE PBD with the source package, exact part number, region, and targeted supplier when applicable.
- PCE should costs may exist only for the likely award candidate; do not penalize other suppliers for lacking a PCE model.
- Process PCE PBDs through the same traceable cost-structure engine and preserve PCE labor, burden, overhead, profit, operation, and other supported assumptions.
- Compare the targeted supplier's selected commercial round with PCE at part, APV, and weighted-average vehicle cost levels.
- Calculate `Gap to PCE % = (Supplier Cost / PCE Should Cost) - 1` on a comparable scope.
- Classify supplier cost at or below PCE, or no more than 3% above PCE, as `Within North Star Range`.
- Above 3%, show the remaining supported gap without automatically deciding whether the supplier is awardable.
- Compare program target, PCE should cost, and current supplier quote separately for SFT, PST, and ED&D total amounts.

### RFQ-release APV year and take-rate baseline

- At RFQ release, import the official GST part-level piece-price targets and FPV.
- Calculate target APV by program year using the company target dataset.
- Select the year with the highest official target APV as the governing reporting year for this level of analysis.
- Calculate part take rates from that governing year's official target population and volumes.
- Use the standard weighted calculation: `Weighted Average Cost = sum(Part Price × Governing-Year Part FPV) / sum(Governing-Year Part FPV)`.
- Freeze the RFQ-release take-rate population as the common basis for the company target, later supplier rounds, and PCE should-cost comparisons.
- Do not allow supplier or later PCE datasets to redefine the baseline take rates.
- Continue to use the buyer-selected lifecycle years separately for NPV.
- If official GST volumes or targets change, import the revised official dataset as a new baseline version.
- Never rewrite the original RFQ-release baseline, take rates, or finalized analyses.
