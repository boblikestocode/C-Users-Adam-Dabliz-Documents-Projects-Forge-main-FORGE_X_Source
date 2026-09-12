# Forge X cost-evidence and comparability specification — September 11, 2026

Status: proposed design contract for the first evidence-to-action vertical slice.

This specification traces three representative PBD calculation structures inspected in the read-only collection at `D:\All PBDS`. It records field roles, relationships, and schema implications without copying supplier identities, part numbers, filenames, or commercial values into the repository. The local inspection records are `template_001`, `template_006`, and `template_027` under `C:\Users\Adam Dabliz\forge_pbd_logic_review`.

This document does not approve a broad schema rewrite. The extensions in section 7 are the minimum concepts needed to preserve these three traces without manufacturing detail.

## 1. Common evidence contract

Every normalized cost fact must resolve to immutable source evidence and retain:

- workbook, worksheet, source occurrence, cell or range, submitted label, submitted lexeme, formula text, cached value, and extraction version;
- semantic measure, amount/rate/quantity type, normalized unit, currency where applicable, and cost basis;
- manufacturing subject such as material line, operation, cost pool, part, plant, or program;
- interpretation or mapping version, confirmer where required, and applicability scope;
- analytical eligibility by use, because price, cost-structure, formula, and benchmark eligibility may differ;
- dependencies and calculation output boundary, including the exact rule/formula version and rounding behavior.

The submitted workbook structure and Forge interpretation remain separate. A source formula is evidence about the supplier submission. A Forge calculation rule is a versioned analytical assertion. Neither replaces the other.

### 1.1 Canonical cost relationship

A cost relationship is a directed, versioned relationship with:

- one or more typed inputs;
- one or more typed outputs;
- an expression or rule identity;
- ordered dependency edges with roles such as `Rate`, `Quantity`, `Time`, `Allocation Denominator`, `Markup Base`, `Adjustment`, or `Output`;
- source-formula, supplier-stated, buyer-confirmed, or Forge-rule provenance;
- calculation unit, currency, output rounding boundary, and reconciliation status;
- applicability scope and effective/recorded dates when the relationship is reusable knowledge.

This supports both a simple `rate × quantity = amount` relationship and a graph such as annual staffing cost -> per-vehicle allocation -> loaded manufacturing cost. A single `amount/rate/basis` tuple is not sufficient for the observed structures.

### 1.2 Comparability decision

Comparability is a recorded decision about a measure in a defined population; it is not an attribute of a supplier or a cost label alone. Each decision must pin:

- target measure and grain;
- candidate evidence population and evidence cutoff;
- normalized unit and currency;
- economic period and source role;
- geography, supplier plant, commodity, part/family, process, equipment, specification, volume, and commercial basis as applicable;
- each member's inclusion status and reason;
- governing, directional, or not-comparable use;
- rule/mapping versions, confirmations, and unresolved limitations.

Exact values with unlike units, currencies, bases, periods, or economic roles remain evidence but do not enter a governing comparison.

## 2. Trace A — standard component operation and material cost

Inspection record: `template_001`. Governing worksheet role: supplier detailed PBD containing submitted inputs and calculated outputs. The associated instruction worksheet is supporting interpretation evidence, not a governing calculation source.

### 2.1 Operation path

Representative operation row fields are:

| Source field | Source column | Normalized concept | Unit/basis |
|---|---:|---|---|
| Operation reference/type/description | A, C, D | Operation identity and submitted process | Text |
| Equipment description | F | Submitted equipment | Text |
| Pieces per cycle | G | Production output per cycle | Pieces/cycle |
| Assembly cycle time | H | Cycle duration | Seconds/cycle |
| Direct operators | I | Direct labor staffing | Operators |
| Direct labor hourly rate | J | Labor rate | Currency/hour |
| Quantity per assembly | K | Operation repetitions | Operations/assembly |
| Direct labor cost | L | Direct labor amount | Currency/assembly |
| Setup time / labor flag / batch size | M, N, O | Changeover assumptions | Hours, boolean, good pieces/batch |
| Machine burden rate/cost | P, Q | Equipment rate and amount | Currency/hour, currency/assembly |
| Scrap rate/cost | R, S | Conversion scrap and amount | Percent, currency/assembly |
| Workshop added value | T | Conversion output | Currency/assembly |
| Loaded WAV | U | Conversion plus overhead/profit | Currency/assembly |
| Related material references | V | Source-declared operation/material relationship | Submitted references |

Observed relationship shape, with the exact supplier formula retained separately:

```text
run_labor = labor_rate / 3600 × operators × cycle_seconds / pieces_per_cycle × quantity_per_assembly
setup_labor = operators × quantity_per_assembly × setup_hours × labor_rate / batch_good_pieces
direct_labor = run_labor + conditional setup_labor

run_machine = machine_rate × quantity_per_assembly × cycle_seconds / pieces_per_cycle / 3600
setup_machine = machine_rate × quantity_per_assembly × setup_hours / batch_good_pieces
machine_cost = run_machine + conditional setup_machine

scrap_cost = conversion_scrap_factor × (direct_labor + machine_cost)
workshop_added_value = direct_labor + machine_cost + scrap_cost
loaded_wav = workshop_added_value × overhead relationship × profit relationship
```

The source formula, its intermediate rounding, and the Forge-normalized relationship must all be retained. The instruction text and the sampled machine formula disagree in how the `3600` factor is described; this is a formula-reconciliation concern, not authority to silently repair the source.

### 2.2 Material path

| Source field | Source column | Normalized concept | Unit/basis |
|---|---:|---|---|
| Material reference, identity, description, specification | A–F | Material-line identity | Text |
| Special category | D | Submitted commercial/material treatment | Controlled interpretation required |
| Sub-supplier and location | G, H | Material source context | Text/normalized identity |
| Quantity, gross usage, net usage | I–K | Quantity and yield inputs | Submitted unit/assembly |
| Offal indicator | L | Recovery applicability | Boolean |
| Unit and purchase currency | M, N | Measurement and currency context | Controlled unit/currency |
| Unit cost and material cost | O, P | Material rate and extended amount | Currency/unit, currency/assembly |
| Transportation and duties | Q, R | Adders | Currency/unit |
| Scrap rate and cost | S, T | Material scrap | Percent, currency/assembly |
| Material with transport/scrap | U | Intermediate material amount | Currency/assembly |
| Material overhead rate/cost | V, W | Markup and amount | Percent and currency/assembly with explicit base |
| Loaded material | X | Intermediate output | Currency/assembly |
| Offal quantity, rate, value | Y–AA | Recovery relationship | Unit, currency/unit, currency/assembly |
| Net material after offal | AB | Material output | Currency/assembly |

Special-category rules change which inputs enter the cost base. The category therefore cannot be stored as descriptive text only; its confirmed interpretation and rule version must be pinned to any governing result.

### 2.3 Validation and eligibility

- Require positive pieces per cycle when cycle-based labor or machine cost governs.
- Require compatible time, quantity, rate, and output units before recalculation.
- Apply setup cost only when the applicable source conditions and a positive batch denominator are present.
- Reconcile labor, machine, scrap, WAV, material, overhead, offal, and section totals independently at the defined four-decimal output boundaries.
- Retain zero, blank, and not-applicable as distinct states.
- A formula discrepancy may remain commercially eligible while being excluded from formula-governing or structure-governing populations.
- Material and operation benchmark use requires confirmed category, process/equipment context, unit, currency, plant/region, and economic period.

### 2.4 Benchmark dimensions and possible actions

Operation benchmarks require process, equipment/signature, cycle unit, pieces per cycle, operators, labor-rate inclusion basis, machine-rate inclusion basis, quantity per assembly, scrap, plant/region, economic year, currency, and production volume/batch assumptions. Material benchmarks require specification, supplier/source, physical unit, gross/net usage, yield/scrap, special category, transport/duty terms, overhead base, geography, economic date, and volume.

Supported actions include requesting cycle or staffing substantiation, challenging a machine-rate inclusion, reconciling a formula, requesting the missing overhead base, validating yield/offal recovery, or negotiating a comparable rate/assumption. A peer difference alone is directional until the comparison conditions are confirmed.

## 3. Trace B — seat annual plant allocation

Inspection record: `template_006`. Governing worksheet role: supplier cost model containing program assumptions, annual cost pools, allocation formulas, and per-vehicle outputs.

### 3.1 Source and normalized path

| Source field | Representative cell | Normalized concept | Unit/basis |
|---|---:|---|---|
| CPV and discount factor | D3, D4 | Capacity-plan volume and adjustment | Vehicles/year, percent |
| FPV | D5 | Allocating production volume | Vehicles/year |
| Program years/life volume | D8, D9 | Program period and total volume | Years, vehicles/program |
| Production days, shifts, hours | D10–D12 | Annual work pattern | Days/year, shifts/day, hours/shift |
| Direct heads and labor/fringe rates | C17–C19 | Staffing and loaded labor inputs | Heads/shift, currency/hour, percent/rate |
| Annual direct labor and per-vehicle labor | E18–E21, F18–F21 | Direct labor pool and allocated outputs | Currency/year, currency/vehicle |
| Indirect labor and other variable burden | rows 26–32 | Variable plant cost pool | Mixed annual drivers and currency/year |
| Management, facility, utilities, insurance, IT, OME | rows 35–40 | Fixed plant cost pool | Annual amounts or driver × rate |
| Launch, packaging, tooling, capital | rows 41–44 | One-time/amortized cost candidates | Lump sum, rate, term, currency/vehicle |
| Fixed burden, total value add, hourly burden | rows 45–49 | Allocated outputs | Currency/year, currency/vehicle, currency/hour |

Observed relationship shape:

```text
fpv = cpv × (1 - volume_adjustment)
program_life_volume = fpv × program_years
annual_direct_labor = heads_per_shift × shifts × production_days × hours_per_shift × hourly_rate
annual_fringe = applicable_labor_base × fringe_rate
annual_variable_burden = sum(variable cost pools)
annual_fixed_burden = sum(fixed cost pools and supported amortization)

per_vehicle_line = annual_line / explicit allocation_volume
vehicle_value_add = direct_labor_per_vehicle + variable_burden_per_vehicle + fixed_burden_per_vehicle
hourly_labor_burden = annual_value_add / explicit annual operating_hours
```

The sampled sheet uses both FPV- and CPV-based denominators in nearby per-vehicle calculations. Forge must record the denominator and basis for each relationship rather than infer one workbook-wide allocation basis.

### 3.2 Validation and eligibility

- Record the period, numerator cost pool, denominator measure, denominator source, and allocation grain for every allocated output.
- Require positive allocation volume and positive operating hours where used.
- Reconcile annual pool totals independently from per-vehicle outputs.
- Distinguish headcount per shift, total scheduled heads, salary headcount, annual salary, hourly rate, and fringe percentage.
- Keep variable, fixed, capital, launch, packaging, tooling, and engineering pools separate.
- Do not treat a PMT-style amortization as recurring operating cost without confirmed term, rate, volume basis, ownership, and payment treatment.
- Exclude a line from governing allocation comparisons when its denominator is ambiguous; retain the annual source amount.

### 3.3 Benchmark dimensions and possible actions

Required dimensions include plant/region, economic year, currency, vehicle/part allocation grain, CPV versus FPV basis, annual volume, program duration, production days, shifts, hours, utilization/capacity assumptions, staffing category, labor/fringe inclusion, cost-pool category, facility scale, amortization term/rate, and included/excluded cost categories.

Supported actions include challenging staffing or overtime assumptions, requesting fringe support, reconciling CPV/FPV allocation, testing fixed-cost sensitivity to volume, requesting OME detail, and separating capital or launch recovery from recurring cost. An annual-cost difference is not a per-vehicle saving until the allocation basis is comparable.

## 4. Trace C — wiring material and labor

Inspection record: `template_027`. Worksheet roles are linked and multi-purpose:

- `PBD Summary`: supplier input plus calculated-output worksheet;
- `Circuit Bom`: circuit/material input and reference worksheet;
- `Component Bom`: component input worksheet;
- `Labor`: labor-standard input and quantity worksheet;
- `Price Model Formula`: supporting calculation explanation.

`PBD Summary` cannot be ignored by name because other governing worksheets depend on its part identity, commodity rates, labor rates, and markup assumptions.

### 4.1 Circuit and component material path

| Source field | Source location | Normalized concept | Unit/basis |
|---|---|---|---|
| Copper/aluminum commodity rates and dates | `PBD Summary` G5/G7 and adjacent date fields | Metal market input | Currency/lb and economic date |
| Circuit name, wire code, gauge | `Circuit Bom` B–D | Circuit and wire specification | Text/gauge |
| Base wire cost per meter | `Circuit Bom` E | Non-metal wire rate | Currency/meter |
| Copper/aluminum weight per meter | `Circuit Bom` F/G | Metal content intensity | Lb/meter |
| Length by harness part | `Circuit Bom` H onward | Part-specific usage | Meter/part |
| Component identities/descriptions | `Component Bom` B–E | Purchased component identity | Text |
| Component base cost and metal weight | `Component Bom` F–H | Component cost inputs | Currency per submitted unit, lb/unit |
| Component quantity by harness part | `Component Bom` I onward | Part-specific usage | Each/meter/gram per part |
| Material IBT/scrap markup | `PBD Summary` K6 | Material adder | Percent with explicit base |

Observed wire relationship shape for one harness part:

```text
copper_weight = sum(component_quantity × component_copper_weight)
              + sum(circuit_length × wire_copper_weight_per_meter)
aluminum_weight = corresponding aluminum relationship

wire_cost = [sum(length × base_wire_cost_per_meter)
           + sum(length × copper_weight_per_meter) × copper_rate
           + sum(length × aluminum_weight_per_meter) × aluminum_rate]
          × (1 + material_ibt_scrap_markup)
```

Component cost follows the same pattern using component quantity, base cost, embedded metal weight, metal rate, and the material markup. Units such as each, meter, and gram cannot be collapsed into one generic quantity without preserving the submitted unit and confirmed normalized basis.

### 4.2 Labor and burden path

| Source field | Source location | Normalized concept | Unit/basis |
|---|---|---|---|
| Labor operation and category | `Labor` column B/section rows | Operation identity/category | Text |
| Unit of measure | `Labor` E | Activity quantity basis | Each or meter |
| Skill level | `Labor` F | Labor classification | Controlled code |
| Skill-level labor rate | `Labor` G, linked to `PBD Summary` I6:I8 | Labor rate | Currency/hour |
| Standard minutes | `Labor` H | Standard time per activity unit | Minutes/unit |
| Quantity by harness part | `Labor` I onward | Part-specific activity driver | Each or meter/part |
| Indirect labor markup | `PBD Summary` I9 | Labor adder | Percent with explicit base |
| Capital/fixed burden rates | `PBD Summary` K4:K5 | Conversion burden | Percent with explicit base |

Observed relationship shape:

```text
direct_labor = sum(standard_minutes × part_quantity × skill_rate) / 60
labor_cost = direct_labor × (1 + indirect_labor_markup)
plant_capital_burden = supported labor/time base × confirmed capital_and_burden_rate
sg_and_a = supported manufacturing base × sg_and_a_rate
profit = supported manufacturing base × profit_rate
sell_price = supported material + labor + burden + markups + logistics
```

### 4.3 Validation and eligibility

- Preserve part columns as separate observation grains; do not treat a worksheet-wide matrix as one part.
- Require the labor activity unit to match the part quantity unit before extension.
- Require skill level to resolve to the correct rate and rate-effective context.
- Reconcile metal weights, material costs, labor hours, labor costs, burden, and sell price at their output boundaries.
- Retain linked-sheet dependencies; a worksheet classified as `Summary` may still be required input evidence.
- Require commodity-rate date, currency, geography, and specification alignment for metal benchmarks.
- Generated gap-study workbooks require source-document-role confirmation before their values enter supplier or market populations.

### 4.4 Benchmark dimensions and possible actions

Wire comparisons require wire code/specification, gauge, conductor metal, weight/length, length/part, commodity rate and date, base conversion cost/meter, scrap/IBT basis, geography, and currency. Component comparisons require manufacturer/supplier identity, exact component/specification, unit, quantity, embedded metal treatment, and economic date. Labor comparisons require operation, activity unit, skill, standard minutes, quantity/part, rate inclusion, plant/region, year, and indirect-labor basis.

Supported actions include updating a metal-rate scenario, challenging length/weight or component quantity, requesting missing commodity-date evidence, comparing labor minutes for the same operation/unit, validating skill assignment, or requesting the basis for burden and markup. A gap-study output is a modeled or directional finding until its source role and evidence population are confirmed.

## 5. First benchmark population rules

The first governing population should be deliberately narrow:

1. one measure at a time;
2. supplier-evidence documents only, excluding generated analyses and buyer scenarios;
3. confirmed plant/region, economic year/date, currency, normalized unit, and comparison basis;
4. exact part for governing results unless a buyer-confirmed functional-family permission explicitly allows the field;
5. exact normalized process/equipment signature for operation rates where equipment affects cost;
6. explicit included and excluded members with reason codes;
7. separate raw-record and independent-event weighting;
8. immutable population, interpretation, rule, and engine versions.

The initial useful candidates are exact-process labor rate, exact-process machine rate, and wiring labor minutes by operation/unit. Annual plant-cost comparisons should remain directional until volume, work pattern, pool definition, and allocation basis are confirmed.

## 6. Actionable finding contract

Every published finding must persist:

- finding class: `Formula Discrepancy`, `Peer Evidence Difference`, `Modeled Opportunity`, `Directional Signal`, or `Confirmed Commercial Saving`;
- challenged source measure, assumption, relationship, or output;
- linked calculation result and complete lineage;
- immutable comparison population and comparability decision;
- impact amount and basis when supported;
- confidence, uncertainty, exclusions, and missing evidence;
- proposed supplier question, negotiation step, sourcing alternative, or information request;
- confirmation state and evidence required to promote the finding.

Only a supported commercial decision may use `Confirmed Commercial Saving`. A residual, peer gap, model scenario, or formula exception cannot promote itself to that class.

## 7. Existing-schema review and required extensions

The current schema already provides the required immutable workbook/worksheet/cell lineage, typed submitted data, submitted cost sections, basic material and operation lines, formula evidence, eligibility decisions, analysis manifests, results, lineage, actions, audit, and finalized snapshots. Those concepts should be reused.

Only these extensions are required before the vertical slice:

| Required extension | Why the current model is insufficient | Minimum shape |
|---|---|---|
| Source document and worksheet role decisions | Detection status and worksheet name do not express supplier evidence, scenario, generated analysis, input, calculation, or mixed roles. | Immutable role decision/version plus evidence, confirmer/rule, scope, and worksheet-to-worksheet dependency edges. |
| Flexible line measures | `material_line` and `operation_line` expose too few fixed fields for gross/net usage, pieces/cycle, operators, setup/batch, scrap, skill, and each/meter drivers. | Typed `material_line_measure` and `operation_line_measure` records linked to `submitted_datum`, controlled measure code, role, ordinal, and interpretation version. |
| Normalized operation context | Submitted operation/equipment strings cannot govern process comparisons. | Versioned operation classification with process code, equipment identity or complete signature, activity unit, mapping evidence, scope, confidence, and confirmation. |
| Annual allocation model | No authoritative concept represents annual pools, periods, drivers, denominators, and allocated outputs. | Cost pool/allocation relationship with pool category, period, numerator evidence, denominator measure/evidence, allocation grain, output, and treatment version. |
| General cost-relationship graph | `cost_element` supports only one amount, rate, and basis; formula evidence does not provide queryable typed dependency edges. | Immutable relationship plus ordered typed input/output terms, expression/rule identity, provenance, output boundary, reconciliation state, and lineage. |
| Reproducible comparability population | Rate-specific interpretation and scenario manifests do not retain general dimension-by-dimension match decisions for material, operation, or allocation evidence. | Comparison context, candidate/member manifest, dimension facts, eligibility/use class, exclusion reason, cutoff, and rule/confirmation versions. |
| General analysis finding | Profile findings and buyer actions do not fully bind a vertical-slice finding class, challenged measure, comparison population, result, uncertainty, impact basis, and promotion state. | Immutable finding linked to calculation result, comparison context, lineage, action, confidence/limitations, and supported promotion history. |

Unit and currency definitions already belong in the governed central registry and should not be duplicated. Existing `submitted_datum`, `formula_evidence`, `evidence_manifest_entry`, `calculation_result`, `calculation_lineage`, `buyer_action`, and snapshot tables remain the authoritative primitives beneath these extensions.

## 8. Vertical-slice acceptance boundary

The first implementation slice is complete when Forge can ingest a synthetic, non-private equivalent of Trace A and:

1. retain every input/output source datum and worksheet role;
2. map one material line and one operation without losing their factors;
3. persist and independently reproduce their cost-relationship graphs;
4. record a narrow comparison population with explicit exclusions;
5. create one non-confirmed actionable finding with result, lineage, uncertainty, requested supplier evidence, and buyer action;
6. rebuild the result and finding from the pinned evidence/rule versions;
7. fail integrity checks when a dependency, population member, unit, or relationship term is altered or missing.

Trace B should then validate allocation support, and Trace C should validate cross-worksheet roles plus each/meter/skill/commodity dependencies. Statistical models remain out of scope until all three traces reproduce and their benchmark populations pass comparability validation.
