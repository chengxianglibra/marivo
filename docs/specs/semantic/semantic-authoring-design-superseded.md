# Semantic Layer Authoring Design (Historical)

> **Superseded:** use
> `docs/specs/semantic/stepwise-authoring-design.md` for the active
> `help -> discover -> settle/grill -> author -> verify` workflow. This
> document consolidates historical context from three previous pipeline
> designs and remains only as reference material.

Status: superseded.

Historical note: this document predates removal of the public semantic
`prepare_*` authoring stage. The active normal workflow is defined in
`docs/specs/semantic/stepwise-authoring-design.md` as
`help -> discover -> settle/grill -> author -> verify`. Remaining `prepare_*`
text below is historical context only.

---


## Part 1: Agent Semantic Layer Authoring Design

The following content was previously in `agent-semantic-layer-authoring-design.md`.

The active normal workflow is defined in
`docs/specs/semantic/stepwise-authoring-design.md`. The older phased workflow
in this document is retained as background, but standalone check choreography is
superseded. Current agents should use:

1. `ms.help(...)` for static constructor contracts.
2. `md.discover_*` for bounded datasource evidence.
3. Evidence settlement or one-at-a-time grill questions.
4. Authoring exactly one semantic object.
5. `ms.verify_object(...)` after writing.
6. A single `ms.readiness(...)` closeout.

This document defines the end-to-end contract for Claude Code, Codex, and other
coding agents that build Marivo semantic layers. It complements
`docs/specs/semantic/python-semantic-layer.md`: that document defines the
Python-native semantic API, while this one defines how agents should gather
evidence, author semantic objects, validate previews, and decide whether a
semantic layer is ready for `marivo.analysis`.

Where this document repeats object-level decision rules from
`python-semantic-layer.md`, those rules are included only to make the agent
workflow executable. The API-level source of truth remains
`python-semantic-layer.md` and the live `ms.help(...)` catalog.
This document owns the evidence, preview, readiness, and agent handoff contract.

## Purpose

Marivo semantic authoring should be evidence-driven. Agents must not infer
business meaning from table names or column names alone. They should inspect the
project, datasource metadata, table comments, bounded data previews, supplied
knowledge-base content, source SQL, and existing semantic objects before writing
Python semantic definitions.

The goal is a repeatable authoring loop aligned to the authoring pipeline
design.

```text
discover project
  -> inspect datasource and source evidence
  -> assess each candidate object
  -> author Python semantic objects
  -> produce one readiness report
  -> hand off stable refs to analysis
```

The source of truth remains Python files under `models/semantic/<model>/`.
Preview rows, knowledge-base snippets, and agent reasoning are evidence used to
author and validate the semantic layer; they are not a second semantic DSL.

## Non-Goals

- Do not use preview rows to automatically infer business definitions.
- Do not persist preview rows into semantic object definitions.
- Do not introduce a YAML, JSON, or prompt-only semantic layer alongside Python
  authoring files.
- Do not allow raw SQL as metric executable bodies. SQL belongs in datasource
  views, provenance, or parity fixtures.
- Do not make preview a full profiling engine. Preview is bounded shape
  inspection.
- Do not let `marivo.analysis` define metric business logic directly. Missing
  business objects should be added to `marivo.semantic` first.

## Current Capability Baseline

The current Marivo Python-native surface already provides the core semantic
registry and validation pieces:

- `marivo.semantic.SemanticProject`
- `ms.load()` returning `SemanticCatalog`
- `catalog.list(...)` / `catalog.get(...)` / `catalog.preview(...)`
- `project.parity_check(...)`
- `ms.help(...)` and `ms.help("constraints")`
- `md.register(...)`, `all()`, `describe()`, `build_backend()`,
  and `test()`
- analysis frame `preview(limit=...)`

The current gaps are:

- no readiness report API
- no unified datasource table metadata/comment inspection API
- skill documentation mentions limited sample inspection but does not yet make
  preview and readiness a full authoring loop

This design specifies the target contract while allowing phased adoption.

## Available Today vs Target APIs

Agents reading this document today must use the Phase 0 APIs unless a target API
has landed in their installed Marivo version.

| Capability | Available today | Target API |
| --- | --- | --- |
| Find and load semantic project | `ms.load()` | same |
| Inspect semantic objects | `catalog.list(...)`, `catalog.get(...)` | same |
| Build backend from datasource | `md.connect(name)` | same |
| Test datasource | `md.test(name)` | same |
| Raw table preview | `md.preview(...)` for bounded datasource rows | same |
| Semantic dataset/field/metric preview | `catalog.preview(...)` | same |
| Metric SQL parity | `project.parity_check(...)` | same |
| Readiness report | agent-authored closeout from load, preview, and parity evidence | `project.readiness(...)` |
| Table metadata/comments | `md.inspect_source(...)` | same |

When calling parity or readiness APIs, use the project-owned datasource
connection runtime. Agent-facing preview goes through the catalog:

```python
import marivo.semantic as ms

catalog = ms.load()
preview = catalog.preview("sales.revenue")
```

Semantic internals that need Ibis expressions use resolver/materializer
primitives rather than `SemanticProject` public methods.

## End-To-End Authoring Loop

### 1. Discover

The agent starts by finding and loading the semantic project:

```python
import marivo.semantic as ms

catalog = ms.load()
```

The agent then inspects existing objects before proposing anything new:

```python
catalog.list().show()
catalog.list("sales", kind="entity").show()
catalog.list("sales", kind="metric").show()
catalog.get("sales.revenue").details()
```

`catalog.list(...)` returns a `SemanticObjectList` without writing stdout.
Call `.show()` for human-readable display and use `.objects` / `.refs()` for
programmatic consumption.

Rule: reuse existing semantic refs when their `business_definition`,
guardrails, dependencies, and provenance match the user intent. Add new objects
only when existing objects are missing, conflicting, or at the wrong grain.

### 2. Inspect Datasource

For every candidate datasource, the agent must:

- confirm the datasource exists
- read its redacted description
- test reachability when live access is required
- identify backend type and accessible table namespace

Current API:

```python
import marivo.datasource as md

md.list()
md.describe("warehouse")
md.test("warehouse")
backend = md.connect("warehouse")
```

Target APIs for richer inspection are described later in this document.

Use `md.DatasourceSpec(...)` plus `md.datasource(spec)` in
`models/datasources/<name>.py` when authoring datasource files directly. Use
`md.register(md.DatasourceSpec(...))` when a script or agent wants
Marivo to create or replace the datasource file through the public registry API.
Semantic model files should reference project datasources with `md.ref(...)`;
datasource configuration itself does not belong inside semantic model files.

### 3. Collect Table Evidence

Before declaring a dataset, the agent must collect:

- table names and physical namespace
- column names and Ibis types
- table and column comments from the datasource metadata catalog
- nullable, partition, and key hints where available
- bounded raw table preview rows
- time-like column value samples
- enum/status/code value samples
- join key samples when relationships are needed

`table.schema()` is not enough because it does not expose comments or business
meaning. Comments and knowledge-base content are primary semantic evidence;
preview validates physical shape.

### 4. Ingest Knowledge

The agent should parse user-provided knowledge bases, BI definitions, source
SQL, metric descriptions, and owner notes into structured evidence:

- business definition
- guardrails and exclusions
- synonyms
- example natural-language questions
- source SQL, dialect, and document reference
- decomposition hints
- numerator and denominator metric references
- owner confirmations

Knowledge evidence can override field-name guesses, but it cannot silently
override contradictory metadata or preview evidence. Conflicts become blockers
or require user confirmation.

### 5. Propose Semantic Plan

Before editing Python files, the agent should propose a concise plan:

- target model
- datasets and physical tables
- fields and time fields
- metrics and decomposition
- relationships
- provenance status
- previews required
- unresolved blockers

The plan is the checkpoint where the agent decides whether it has enough
evidence to author the semantic layer.

### 6. Author Python Semantic Objects

Python files remain the source of truth:

```text
marivo.toml
marivo/
  datasources/
    warehouse.py
  semantic/
    sales/
      _domain.py
      revenue.py
```

The agent should use `marivo.semantic` decorators and builders:

- `ms.domain(...)`
- `ms.entity(...)`
- `@ms.dimension(...)`
- `@ms.time_dimension(...)`
- `@ms.simple_metric(...)`
- `ms.ratio(...)` / `ms.weighted_average(...)` / `ms.linear(...)`
- `ms.relationship(...)`
- `ms.sum()`
- `ms.ratio(...)`
- `ms.weighted_average(...)`

The agent should inspect `ms.help("<symbol>")` and
`ms.help("constraints")` instead of guessing allowed shapes.

### 7. Semantic Preview

After authoring, the agent validates semantic objects with bounded previews:

- dataset preview confirms table access, stable filters, projections, and casts
- field preview confirms row-level expressions with bounded parent dataset context
- time field preview validates parsing, grain, and null behavior through field preview rows
- metric preview confirms materialization or compilation; scalar metrics return a one-row `value`

Use the standard preview APIs:

```python
catalog.preview("sales.orders", limit=20)
catalog.preview("sales.orders.order_date", limit=20)
catalog.preview("sales.revenue", limit=20)
```

Preview failure does not always mean project load failure, but it is a readiness blocker for the affected object.

### 8. Check And Parity

The agent reloads the project and fixes all structured errors:

```python
result = project.load()
```

For metrics with SQL provenance, the agent runs parity:

```python
project.parity_check("sales.revenue", backend_factory=backend_factory)
```

`drifted` and `unverified` parity findings are readiness warnings. They should
be reported in closeout, but they do not by themselves block analysis handoff.

### 9. Readiness Report

The final authoring step is a structured readiness report. It states which
semantic refs are analysis-ready, which objects are blocked, which warnings
remain, and which evidence was used.

Readiness is the implemented closeout API based on load errors, preview
evidence, materialization or compile results, parity results, and richness
warnings.

### 10. Analysis Handoff

Only stable, readiness-approved semantic refs should be handed to
`marivo.analysis`. Analysis operators consume semantic refs and materialized
Ibis expressions; they do not redefine business logic.

## Evidence Model

The authoring loop uses evidence to decide what to author and whether it is
ready. Evidence is not itself a semantic object.

### Project Evidence

Project evidence includes existing models, datasets, fields, time fields,
metrics, relationships, datasources, dependency graphs, and object
descriptions.

Source APIs:

- `ms.load()`
- `catalog.list(...)`
- `catalog.get(...)`
- `catalog.preview(...)`

### Datasource Evidence

Datasource evidence includes datasource name, backend type, redacted literal
fields, environment references, connection test status, and reachable table
namespace.

Source APIs:

- `md.list()`
- `md.describe(...)`
- `md.test(...)`
- target `md.inspect(...)`

### Table Metadata Evidence

Table metadata evidence includes table name, physical namespace, column names,
Ibis types, table comments, column comments, nullable flags, partition fields,
and key hints.

Expected sources include Ibis schema plus datasource catalog queries, for
example Trino `information_schema.columns`, MySQL `SHOW FULL COLUMNS`,
DuckDB `PRAGMA table_info`, or ClickHouse `system.columns`.

### Raw Preview Evidence

Raw preview evidence is a bounded row sample from a physical table. It helps
validate:

- time and partition value formats
- enum/status/code values
- amount units and sign behavior
- null and empty-string behavior
- JSON or nested field shape
- join key shape
- whether comments and physical values appear consistent

Preview is evidence of physical shape, not proof of business meaning.

### Knowledge Evidence

Knowledge evidence includes user-provided documentation, metric SQL, BI
definitions, natural-language examples, business guardrails, owner notes, and
explicit user confirmations.

It maps to semantic authoring fields:

- `description`
- `ai_context.business_definition`
- `ai_context.guardrails`
- `ai_context.synonyms`
- `ai_context.examples`
- `ai_context.instructions`
- `ai_context.owner_notes`
- `source_sql`
- `source_dialect`
- `verification_mode` (inferred from `source_sql` presence)

### Runtime Evidence

Runtime evidence comes after authoring:

- load result
- semantic object previews
- compiled SQL
- materialization errors
- parity result
- dependency/dependent graphs

### User Confirmation Evidence

The agent asks the user only for information that cannot be fetched or inferred
safely from available evidence. Examples:

- amount unit is unclear
- status code meaning is undocumented
- multiple time axes are plausible
- source SQL and comments conflict
- metric decomposition is ambiguous
- source SQL is unavailable and the metric may need to be `python_native`

The agent should not ask for column lists, types, existing objects, or preview
values when the datasource can provide them.

## Preview API Contract

Marivo should provide standard preview APIs because agent-written ad hoc
`limit(...).execute()` snippets produce inconsistent output, skip redaction,
hide backend errors, and cannot be reused by readiness, MCP tools, CLI, or UI.

### PreviewResult DTO

Preview APIs should return a structured result:

```python
from dataclasses import dataclass
from typing import Literal, TypedDict

@dataclass(frozen=True)
class PreviewResult:
    kind: Literal[
        "datasource_table",
        "semantic_dataset",
        "semantic_field",
        "semantic_metric",
        "analysis_frame",
    ]
    ref: str
    columns: tuple[str, ...]
    types: dict[str, str]
    rows: tuple[dict[str, object], ...]
    requested_limit: int
    returned_row_count: int
    is_truncated: bool
    warnings: tuple["PreviewWarning", ...]
    sample_policy: "PreviewSamplePolicy"
```

```python
@dataclass(frozen=True)
class PreviewWarning:
    kind: Literal[
        "redacted_column",
        "wide_table",
        "null_heavy_column",
        "constant_column",
        "time_parse_risk",
        "empty_preview",
        "backend_limit_unknown",
    ]
    message: str
    columns: tuple[str, ...] = ()
```

```python
@dataclass(frozen=True)
class PreviewSamplePolicy:
    method: Literal["head", "bounded_limit", "ordered_limit"]
    limit: int
    order_by: tuple[str, ...] = ()
    filters: tuple["PreviewFilter", ...] = ()
```

The DTO should normalize backend-specific values into JSON-safe scalars where
possible and should preserve column order.

### Datasource Table Preview

Available API.

```python
import marivo.datasource as md

preview = md.preview(
    "warehouse",
    table="orders",
    database="sales_mart",
    columns=["order_id", "created_at", "amount", "status"],
    limit=20,
    include_types=True,
)
```

Rules:

- default `limit` is 20
- maximum `limit` is 100
- no full-table scan
- `columns=` should be supported to avoid wide-table context flooding
- default redaction should warn or mask likely secrets, tokens, emails, phone
  numbers, and sensitive identifiers
- failures should be structured and suitable for readiness issues

### Semantic Object Preview

Available APIs.

```python
catalog.preview("sales.orders", limit=20)
catalog.preview("sales.orders.order_date", limit=20)
catalog.preview("sales.revenue", limit=20)
```

Rules:

- dataset preview materializes the dataset and returns bounded rows
- field preview returns bounded values with enough dataset context to validate
  expression shape
- time field preview validates parsing, grain, and null behavior
- metric preview validates materialization or compilation; scalar metrics return
  a one-row value
- preview failures block readiness for affected objects

### Analysis Frame Preview

Existing `frame.preview(limit=...)` remains the analysis result read surface.
It is not a replacement for raw datasource preview or semantic object preview.

## Semantic Object Authoring Contract

### Model

Models are business domains, not physical schemas. Agents should reuse an
existing model when it matches the business domain.

```python
import marivo.semantic as ms

ms.domain(
    name="sales",
    description="Sales analytics semantic model.",
    ai_context={
        "business_definition": "Commercial order and revenue analytics.",
        "guardrails": ["Do not use for marketing session attribution."],
    },
)
```

### Dataset

A dataset is a fact or entity table logical view. Before declaring one, the
agent must have datasource evidence, metadata evidence, comments when
available, and raw preview evidence.

Mapping:

- table comments and knowledge definitions -> `description` and
  `ai_context.business_definition`
- datasource name -> `datasource=`
- key evidence -> `primary_key`
- physical table access -> `source=ms.table(...)` or `source=ms.file(...)`

Datasets should not contain metric aggregation logic.

### Field

A field is a row-level reusable attribute. The agent should create a field when
an expression is used by multiple metrics, filters, slices, relationships, or
business definitions.

Status codes, platform names, normalized region fields, join keys, and reusable
flags are strong field candidates.

### Time Field

A time field is the explicit time axis. It is not inferred from names such as
`dt` or `created_at` alone.

Selection priority:

1. user or knowledge base explicitly defines the business time axis
2. source SQL uses a specific time field
3. partition field matches the business time axis
4. event/create/update/ingestion/snapshot time is chosen with an explicit reason

The agent must ask the user when multiple plausible axes remain. String and
integer time fields require raw preview samples before choosing casts, formats,
or granularity.

### Metric

Base metrics read datasets:

```python
@ms.simple_metric(
    name="revenue",
    datasets=[orders],
    additivity="additive",

    source_sql="select sum(amount) from orders where pay_status = 1",
    source_dialect="trino",
    ai_context={
        "business_definition": "Paid order revenue.",
        "guardrails": ["Excludes unpaid orders.", "Does not net out refunds."],
        "synonyms": ["gmv", "paid sales"],
    },
)
def revenue(orders):
    return orders.filter(orders.pay_status == 1).amount.sum()
```

Derived metrics combine components:

```python
aov = ms.ratio(
    name="aov",
    numerator=revenue,
    denominator=orders_count,
)
```

Rules:

- do not default to `ms.sum()` when decomposition is unclear
- ratios and averages require explicit components
- source SQL provenance should be preserved when available
- no-source base metrics are trusted as semantically expressed (no `verification_mode` needed)
- base metrics with `source_sql` automatically enable SQL parity verification;
  status stays `unverified` until parity succeeds; `source_dialect` is required when
  `source_sql` is set
- derived metric readiness inherits component status; derived metrics must omit
  `source_sql` and `source_dialect`

### Relationship

Relationships describe join paths between datasets. They should be declared
only when cross-dataset analysis requires them and join semantics are confirmed
by metadata, comments, preview, knowledge, or user confirmation.

Join keys should be field or time field refs, not bare physical column names.

```python
ms.relationship(
    name="orders_to_users",
    from_dataset=orders,
    to_dataset=users,
    from_fields=[order_user_id],
    to_fields=[user_id],
)
```

### AI Context

All semantic objects can use the fixed AI context schema:

```python
ai_context={
    "business_definition": "...",
    "guardrails": ["..."],
    "synonyms": ["..."],
    "examples": ["..."],
    "instructions": "...",
    "owner_notes": "...",
}
```

Guidance:

- `business_definition` describes business meaning, not implementation detail
- `guardrails` describe misuse boundaries
- `examples` are natural-language questions, not SQL snippets
- `owner_notes` can record migration or confirmation context
- unknown keys fail closed

## Agent Decision Rules

### Reuse Before Add

Agents must inspect existing semantic objects before adding new ones:

```python
catalog.list("sales", kind="metric").show()
catalog.get("sales.revenue").details()
```

Reuse is required when the existing object matches the requested business
definition, guardrails, dependencies, and provenance.

### Field Name Is Only A Candidate Signal

Column names can suggest candidates but cannot establish business semantics.

Examples:

- `amount` may be cents, dollars, gross, net, tax-inclusive, or refund-adjusted
- `status = 1` may mean paid, active, successful, online, or valid
- `create_time` may be business creation time or ingestion time
- `dt` may be event date or partition load date

Final semantics require comments, source SQL, knowledge evidence, preview
support, or user confirmation.

### Field vs Metric

- row-level per-record expression -> `dimension` or `time_dimension`
- cross-row aggregate -> `metric`
- reusable row-level expression -> field
- complex cast, filter, or case expression -> usually field first
- one-off simple column access inside one metric -> may remain in metric body

### Time Axis Selection

The agent may proceed automatically when comments, source SQL, knowledge, and
preview agree on one time field. It must ask the user when event time, create
time, update time, ingestion time, and partition date are all plausible or when
source SQL conflicts with comments.

### Decomposition Selection

- additive absolute quantities -> `ms.sum()`
- numerator divided by denominator -> `ms.ratio(...)`
- mix effect with weight -> `ms.weighted_average(...)`

If the decomposition cannot be proven from formula, source SQL, existing
components, or user confirmation, the agent must stop and ask.

### Preview Requirements

Raw preview is required for:

- every new dataset candidate table
- time-like string or integer columns
- amount, status, enum, code, and join key columns
- columns used by source SQL but not explained by comments

Semantic preview is required for:

- every new dataset
- every new time field
- fields with casts, filters, or case logic
- every new metric, at least via materialization or compilation
- metrics with parity drift or compile differences

### When To Ask The User

Ask only for information that cannot be fetched:

- conflicting business definitions
- ambiguous amount unit
- undocumented status code semantics
- multiple plausible time axes
- refund, cancellation, test-data, or exclusion rules
- whether no-source metrics can be `python_native`

Do not ask for column lists, types, comments, sample values, existing objects,
or datasource shape when the system can fetch them.

## Stop Conditions

The agent must not hand off refs to `marivo.analysis` when any of these remain:

- project load or check failed
- datasource is unreachable for required live validation
- new dataset lacks required raw preview
- required comments, knowledge, or user confirmation are missing
- time field preview or cast failed
- metric materialization or compilation failed
- relationship join key is unconfirmed
- metric spans multiple datasources in a workflow that does not support
  federation
- metric body requires raw SQL to express the business logic

Warnings may still allow handoff:

- source SQL parity drift is present
- metric parity is unverified
- metric is explicitly `python_native`
- preview sample is small but materialization succeeds
- primary key uniqueness was not sampled
- string refs resolve but are refactor-fragile
- comments are missing but source SQL, knowledge, and user confirmation are
  sufficient

## Validation And Readiness

Readiness summarizes whether semantic refs can safely flow into analysis.

Available API. Use this as the standard final validation step after load, raw previews, semantic previews, materialization, and parity checks. The API does not replace Phase 4 datasource metadata inspection; table comments and catalog metadata still come from explicit evidence until the metadata API lands.

```python
project.bind_datasource_access(
    inspect_source=inspect_source,
    backend_factory=lambda name: md.connect(name),
)

report = project.readiness(
    refs=("sales.revenue",),
    demand=ms.DemandSignal(example_questions=("daily revenue by region",)),
    preview_limit=20,
    parity_rel_tol=1e-6,
)
```

Target CLI/check shape:

```bash
.venv/bin/python -m marivo.semantic.check --format=json --readiness
```

### ReadinessReport

```python
@dataclass(frozen=True)
class ReadinessReport:
    status: Literal["ready", "ready_with_warnings", "blocked"]
    analysis_ready_refs: tuple[str, ...]
    blockers: tuple["ReadinessIssue", ...]
    warnings: tuple["ReadinessIssue", ...]
    input_summary: "ReadinessInputSummary"
    preview_summary: "PreviewSummary"
    parity_summary: "ParitySummary"
    richness_summary: "RichnessSummary"
    checked_at: str
```

### ReadinessIssue

```python
@dataclass(frozen=True)
class ReadinessIssue:
    kind: Literal[
        "load_error",
        "datasource_unreachable",
        "missing_schema",
        "missing_comments",
        "missing_raw_preview",
        "raw_preview_failed",
        "dataset_preview_failed",
        "field_preview_failed",
        "missing_knowledge_definition",
        "ambiguous_time_axis",
        "time_dimension_preview_failed",
        "metric_materialize_failed",
        "metric_compile_failed",
        "unverified_metric",
        "parity_drifted",
        "relationship_unconfirmed",
        "sensitive_preview_column",
        "cross_datasource_unfederated",
        "requires_raw_sql",
        "primary_key_unsampled",
        "fragile_string_ref",
        "missing_business_definition",
        "missing_guardrails",
    ]
    severity: Literal["blocker", "warning"]
    refs: tuple[str, ...]
    message: str
    suggested_action: str
```

### ReadinessInputSummary

```python
@dataclass(frozen=True)
class ReadinessInputSummary:
    datasources: tuple[str, ...]
    refs: tuple[str, ...]
    tables: tuple[str, ...]
    decision_records: tuple[str, ...]
```

### PreviewSummary

```python
@dataclass(frozen=True)
class PreviewSummary:
    required_previews: tuple[str, ...]
    completed_previews: tuple[str, ...]
    failed_previews: tuple[str, ...]
    warnings: tuple[PreviewWarning, ...]
```

### ParitySummary

```python
@dataclass(frozen=True)
class ParitySummary:
    verified_metrics: tuple[str, ...]
    unverified_metrics: tuple[str, ...]
    drifted_metrics: tuple[str, ...]
    unsupported_metrics: tuple[str, ...]
    skipped_metrics: tuple[str, ...]
```

### RichnessSummary

```python
@dataclass(frozen=True)
class RichnessSummary:
    gaps: tuple[str, ...]
```

Status rules:

- any blocker -> `blocked`
- no blockers and at least one warning -> `ready_with_warnings`
- no blockers and no warnings -> `ready`

`drifted` and `unverified` parity findings are warnings. Derived metric
readiness inherits component parity status for summary purposes, but parity
warnings do not by themselves create blockers.

### Agent Closeout Format

Agents should close authoring work with a concise report:

```text
Semantic readiness: ready_with_warnings

Analysis-ready refs:
- sales.revenue
- sales.orders_count

Warnings:
- sales.aov derives readiness from sales.revenue and sales.orders_count.
- sales.orders primary_key was declared but uniqueness was not sampled.

Blocked refs:
- none

Evidence used:
- datasource warehouse tested
- orders schema/comments fetched
- orders raw preview completed
- revenue source SQL parity passed
```

## Skill Updates

`marivo/skills/marivo-semantic/SKILL.md` should stay short and route the agent
through the authoring loop. Detailed procedures should move to references:

- `references/workflow.md`
- `references/authoring-patterns.md`
- `references/evidence-and-ledger.md`
- `references/preview.md`
- `references/closeout.md`

Existing references should remain:

- `references/datasource.md`
- `references/pitfalls.md`
- `references/examples/`

The skill should enforce:

- inspect existing registry before adding objects
- datasource test, metadata, comments, and raw preview before new datasets
- raw preview before string/integer time fields
- semantic preview after new datasets, time fields, and metrics
- parity for metrics with source SQL
- explicit `python_native` or visible `unverified` status for no-source metrics
- no analysis handoff while readiness is blocked

## Phased Implementation

### Phase 0: Current Capability

Use existing project loading, reader methods, datasource registry, help catalog,
materialization, parity, and frame preview. Skill guidance may use ad hoc Ibis
preview as a fallback until standard preview APIs exist.

### Phase 1: Documentation And Skill Contract

Add this spec and update semantic skill references to require evidence-driven
authoring, raw preview, semantic preview, and readiness reporting.

### Phase 2: Preview API

Add:

- `PreviewResult`
- `PreviewWarning`
- `PreviewSamplePolicy`
- preview value normalization
- redaction helpers
- backend-specific bounded preview tests
- `md.preview(...)` provides bounded raw datasource previews; preview rows are
  not persisted in semantic definitions

Then add:

- `catalog.preview(...)`

### Phase 3: Readiness API

Implemented:

- `project.readiness(...)`
- `ReadinessReport`
- `ReadinessIssue`
- `ReadinessInputSummary`
- `PreviewSummary`
- `ParitySummary`
- `RichnessSummary`
- JSON output for CLI or check helper

### Phase 4: Metadata API

Implemented:

- `md.inspect_source(...)`
- table comments
- column comments
- nullable flags
- partition metadata
- backend-specific adapters for Trino, MySQL, DuckDB, and ClickHouse

### Phase 5: Agent Automation Tightening

Implemented:

- no-preview dataset authoring is not acceptable
- ambiguous time axis asks the user
- unverified metrics appear in readiness
- parity drift warns in readiness
- datasource preview redacts sensitive columns

## Acceptance Criteria

This design is successful when:

- agents know the fixed semantic authoring loop before editing files
- raw table preview is a required evidence step for new datasets
- semantic preview is a required validation step after authoring
- the Marivo API has a clear target for standard preview and readiness surfaces
- readiness clearly separates blockers, warnings, and analysis-ready refs
- skill documentation remains routing-focused and points to detailed references
- Python semantic files remain the only semantic source of truth

---

## Part 2: Authoring Pipeline Design

The following content was previously in `authoring-pipeline-design.md`.

This document defines the target-state authoring pipeline for Marivo semantic
layer construction. It replaces the `NextCheck`-driven choreography with a
three-phase authoring loop backed by one composed static-assessment API and
one closeout readiness gate.

It complements `docs/specs/semantic/python-semantic-layer.md` (which owns the
Python semantic object model and decorator contracts) and supersedes the
authoring workflow sections of both
`docs/specs/semantic/skill-semantic-layer-authoring-design.md` and
`docs/specs/semantic/agent-semantic-layer-authoring-design.md`. Where this
document conflicts with those documents' workflow and `NextCheck`-driven
choreography, this document is the target-state replacement.

## Problem Statement

The current authoring pipeline exposes a 13-step choreography via the
`NextCheck` enum:

```python
NextCheck = Literal[
    "inspect_source_context",  # removed; use md.inspect_table / md.inspect_columns
    "inspect_column_context",  # removed; use md.inspect_columns
    "check_authoring_inputs",
    "write_semantic_python",
    "reload_project",
    "inspect_authored_object",
    "preview_semantic_ref",
    "parity_check",
    "readiness",
    "richness",
    "ask_user",
]
```

This design has several structural problems:

1. **No state machine.** `AssessmentResult.next_checks` returns a tuple of
   `NextCheck` values, but there is no encoded transition logic. What comes
   after source inspection? The agent must infer from skill
   documentation, not from code.

2. **No data flow between steps.** Source inspection returns source
   facts; the agent must extract schema and column profiles from it and
   manually feed them into `check_authoring_inputs(columns=...)` and
   further column inspection. Method signatures do not accept
   prior-step outputs as inputs.

3. **Asymmetric write step.** `"write_semantic_python"` has no corresponding
   `SemanticProject` method — it is an instruction for the agent to write a
   file. The 12 API-call steps are interleaved with a non-API step, splitting
   the pipeline into pre-write and post-write halves with no structural
   acknowledgment of that split.

4. **Branch logic in prose.** `check_authoring_inputs` may return `blocked`,
   `needs_evidence`, or `supported`. The target contract renames the middle
   state to `needs_input`, but either shape requires different handling
   (supplement context → re-check → write → reload), and that branching is
   described only in skill markdown, not in executable code.

5. **Skill as orchestrator.** The 16 non-negotiable rules and 9-stage workflow
   in the skill document serve as the de facto pipeline orchestrator. If the
   skill document and the API drift apart, there is no compile-time or
   runtime signal.

## Core Principle

The project API provides facts and assessments. The agent provides creative
decisions — what to author, how to name it, what business definition to write.
`assess_authoring(...)` must only consume inputs that help Marivo inspect its
own current metadata, bounded samples, datasource access, decision ledger, and
semantic registry state. It must not accept draft authored-object content or
persist authoring evidence as a side effect. Source and column observations
are treated as fresh inspection results, not durable project truth. The
pipeline should compose the API's static assessment checks into fewer, larger
operations that the agent calls at natural decision points.
Runtime closeout checks that execute previews or parity are owned by
`readiness(...)`, which reports per-ref blockers and warnings without asking
the agent to choreograph separate validation calls.

## Persistence Boundary

The target pipeline does not define a persistent source, column, or authoring
evidence store. Persisting sampled source facts is fragile because datasource
content can change, and persisting loosely related authoring notes creates a
false sense of auditability for agent decisions.

Marivo persists only durable project artifacts:

- authored semantic Python files;
- explicit decision-ledger records for dangerous or user-confirmed choices;
- datasource definitions and project configuration.

Inspection APIs may return rich facts and may use short-lived in-process caches
within one `SemanticProject` session. Those observations are not reused across
sessions as authoritative input to assessment or readiness.

## Three-Phase Authoring Flow

### Phase 1: Discovery

The agent determines which tables belong to the semantic model and what
candidate semantic objects they might produce.

1. Load the project and inspect existing refs; search for reuse before
   authoring.
2. For each relevant table, collect source metadata:

```python
source_metadata = md.inspect_table(
    "warehouse",
    md.table("orders", database="sales_mart"),
)
column_inspection = md.inspect_columns(
    "warehouse",
    md.table("orders", database="sales_mart"),
    scope=md.ScanScope(max_rows=100, max_columns=50),
)
```

3. The agent ranks columns from source metadata (type, comments, nullable,
   partition hints, sampled values). Deep-dive a small set if needed:

```python
columns = md.inspect_columns(
    "warehouse",
    md.table("orders"),
    columns=("status", "amount"),
    scope=md.ScanScope(max_rows=100),
)
```

4. The agent decides candidates: which tables enter the semantic model, and
   what semantic objects (entity, time_dimension, dimension, metric) each table
   should produce.

The API provides information for the agent's judgment. It does not return a
candidate worklist or suggest what to author.

### Phase 2: Authoring

The agent authors semantic objects iteratively in the model's single
`_domain.py` file. No reload occurs during this phase.

#### 2.0 Create `_domain.py`

Every model has exactly one authoring file. It contains the model declaration,
datasource references, datasets, fields, metrics, relationships, and derived
metrics:

```python
# models/semantic/sales/_domain.py
import marivo.datasource as md
import marivo.semantic as ms

ms.domain(name="sales", description="Sales analytics")
warehouse = md.ref("warehouse")
```

#### 2.1 Per-source authoring

For each dataset derived from a single source:

**2.1.0 Inspect source context** — collect current metadata and bounded sample
facts for the source.

**2.1.1 Assess each candidate object** — the agent calls
`project.assess_authoring(...)` for each candidate. The API internally
orchestrates current inspection checks and returns an `AuthoringAssessment`:

```python
assessment = project.assess_authoring(
    object_kind="metric",
    subject_ref="sales.revenue",
    sources=(
        ms.AuthoringSourceInput(
            role="primary",
            datasource="warehouse",
            source=ms.TableSource(table="orders"),
            columns=("amount", "paid"),
        ),
    ),
    semantic_refs=("sales.orders",),
)

if assessment.status == "blocked":
    # resolve blockers from assessment.issues, then re-assess
    pass
elif assessment.status == "needs_input":
    # ask user about assessment.questions, supplement context, re-assess
    pass
# status == "supported" → proceed to write
```

If the assessment reports that a business decision is unresolved, the agent
asks the user or consults external project context, then writes the resolved
semantic object content or an explicit decision-ledger record. The assessment
call itself does not persist source, column, or authoring evidence.

**2.1.2 Append confirmed objects to `_domain.py`** — the agent writes all
confirmed objects for this source in dependency order:

```python
orders = ms.entity(
    name="orders",
    datasource=warehouse,
    source=ms.table("orders"),
    primary_key=["order_id"],
    ai_context={
        "business_definition": "One row per order.",
        "guardrails": ["Exclude test orders when the table exposes a test flag."],
    },
)

@ms.time_dimension(entity=orders, name="log_date", granularity="day",
               parse=ms.strptime("%Y%m%d", data_type="string"))
def log_date(table):
    return table.dt

@ms.dimension(dataset=orders, name="region")
def region(table):
    return table.region

@ms.simple_metric(
    datasets=[orders],
    additivity="additive",

    name="revenue",
    source_sql="SELECT SUM(amount) AS revenue FROM orders",
    source_dialect="trino",
    ai_context={
        "business_definition": "Gross order amount before refunds.",
        "guardrails": ["Validate refund exclusions before using as net revenue."],
    },
)
def revenue(table):
    return table.amount.sum()
```

No reload is needed between writing different objects in `_domain.py`. Python
variable references (`orders`, `revenue`) resolve when the file is loaded in
Phase 3.

#### 2.2 Cross-dataset objects (multi-dataset models)

When the model contains multiple datasets, relationships, cross-dataset
metrics, and derived metrics are also authored in `_domain.py`.

**2.2.0 Assess each candidate** — use `assess_authoring` with one
`AuthoringSourceInput` per physical source role. A relationship must have
current from-side and to-side source context before it can be ready:

```python
assessment = project.assess_authoring(
    object_kind="relationship",
    subject_ref="sales.orders_to_customers",
    sources=(
        ms.AuthoringSourceInput(
            role="from",
            datasource="warehouse",
            source=ms.TableSource(table="orders"),
            columns=("customer_id",),
        ),
        ms.AuthoringSourceInput(
            role="to",
            datasource="warehouse",
            source=ms.TableSource(table="customers"),
            columns=("customer_id",),
        ),
    ),
    semantic_refs=(
        "sales.orders",
        "sales.customers",
        "sales.orders.order_customer_id",
        "sales.customers.customer_id",
    ),
)
```

Cross-dataset base metrics use multiple `primary` source roles rather than
`component` roles:

```python
assessment = project.assess_authoring(
    object_kind="metric",
    subject_ref="sales.revenue_per_active_customer",
    sources=(
        ms.AuthoringSourceInput(
            role="primary",
            datasource="warehouse",
            source=ms.TableSource(table="orders"),
            columns=("amount", "customer_id"),
        ),
        ms.AuthoringSourceInput(
            role="primary",
            datasource="warehouse",
            source=ms.TableSource(table="customers"),
            columns=("customer_id", "is_active"),
        ),
    ),
    semantic_refs=(
        "sales.orders",
        "sales.customers",
        "sales.orders.customer_id",
        "sales.customers.customer_id",
    ),
)
```

**2.2.1 Append to `_domain.py`** — use local decorated Python refs when the
target was declared earlier in the file. Use `ms.ref(...)` only for forward
references or generated tooling cases, and follow the current implementation's
plain semantic-id format:

```python
ms.relationship(
    name="orders_to_customers",
    from_dataset=orders,
    to_dataset=customers,
    from_fields=[order_customer_id],
    to_fields=[customer_id],
)
```

`ms.ref("sales.orders")` resolves to the deterministic semantic id, which does
not require an already-bound Python symbol.

### Phase 3: Validation

All semantic objects are validated after all code is written. Phase 3 uses
`readiness(...)` as the single closeout gate: it reloads the project state,
checks refs, runs required backend previews, runs eligible parity checks, and
folds richness findings into one `ReadinessReport`.

`readiness(...)` depends on backend access bound on the project, such as the
backend factory registered through `project.bind_datasource_access(...)`. The
agent must not pass a separate `backend_factory` at readiness time. If the
project has no bound backend access, readiness returns a blocker instead of a
fallback or degraded report.

```python
report = project.readiness(
    refs=("sales.orders", "sales.revenue"),
    demand=ms.DemandSignal(
        example_questions=("What was revenue by region last week?",),
        build_purpose="Revenue analysis",
    ),
    preview_limit=20,
    parity_rel_tol=1e-6,
)
```

Validation closeout rules:

- load errors, missing refs, invalid authored objects, missing backend access,
  datasource/backend failures, and preview/materialization failures are
  blockers because they can make later analysis fail;
- parity findings are warnings, including missing parity, unsupported parity,
  and drift from `source_sql`;
- readiness blockers prevent analysis handoff;
- richness findings are warnings and never block handoff.

### Why no reload in Phase 2

Same-file variable references work without reload. Forward references or
generated tooling refs use `ms.ref("sales.orders")` with the current
semantic-id format. Auto-recorded decisions (`metric_composition`,
`time_dimension_identity`) only affect validation, not authoring. Deferring reload
to Phase 3 reduces N reloads (N = number of objects) to 1.

## File Organization Contract

```
models/semantic/<model>/
  _domain.py         # all semantic declarations for the model
```

Rules:

- `_domain.py` always exists and is the only normal authoring file for a model.
- The file starts with `ms.domain(...)` and datasource refs, then declares
  datasets, fields, time fields, metrics, relationships, and derived metrics in
  dependency order when possible.
- Use local decorated Python refs for objects declared earlier in the file.
- Use `ms.ref("<semantic-id>")` only for forward references or generated
  tooling cases. This design follows the current implementation's semantic-id
  refs.
- The current loader can execute sibling files, but this authoring pipeline
  deliberately does not use them. Multi-file authoring remains a lower-level
  loader capability, not the normal agent authoring contract.

## New APIs

### `project.assess_authoring(...)`

```python
def assess_authoring(
    self,
    *,
    object_kind: Literal[
        "entity", "dimension", "time_dimension", "metric", "derived_metric", "relationship"
    ],
    subject_ref: str,
    sources: Sequence[AuthoringSourceInput] = (),
    semantic_refs: Sequence[str] = (),
) -> AuthoringAssessment:
```

#### `AuthoringSourceInput` DTO

```python
@dataclass(frozen=True)
class AuthoringSourceInput:
    role: Literal["primary", "from", "to", "component"]
    datasource: str
    source: DatasetSource
    columns: tuple[str, ...] = ()
```

`sources=()` is allowed only for derived metrics or other objects whose
grounding is entirely semantic-ref based. Dataset, field, time-field, base
metric, and relationship assessment must include the relevant physical source
roles.

Source roles are interpreted as follows:

| Role | Use |
| --- | --- |
| `primary` | A source directly used by a dataset, field, time field, or base metric; cross-dataset base metrics pass one `primary` role per participating dataset |
| `from` | Relationship from-side source |
| `to` | Relationship to-side source |
| `component` | Source context for a metric component when the assessed object is component-driven but not a pure derived metric |

If an agent needs a non-default sample policy for exploratory judgment, it can
call `md.inspect_table(...)` or `md.inspect_columns(...)` with a custom
`ScanScope` in Phase 1 and use the returned facts directly.
Those observations are not durable truth. `assess_authoring(...)` uses the
default bounded inspection policy unless the future API explicitly adds a
per-call inspection policy.

Parameters:

| Parameter | Purpose |
| --- | ------- |
| `object_kind` | The kind of semantic object being assessed |
| `subject_ref` | Target semantic id using the current implementation format (e.g. `"sales.revenue"` or `"sales.orders.region"`) |
| `sources` | Physical source roles and columns that support the target object |
| `semantic_refs` | Semantic refs the target depends on (e.g. dataset ref for a metric, both dataset refs and join-key field refs for a relationship) |

`assess_authoring(...)` does not accept `ai_context`, inline `evidence`, or
explicit `evidence_refs`, and it does not read a persistent evidence store:

- `ai_context` is authored content. The agent writes it into `_domain.py` in
  Phase 2.1.2, and Phase 3 checks the loaded object.
- `source_sql`, BI definitions, and natural-language business definitions are
  authored content or user-facing context, not inputs to a Marivo assessment
  call.
- explicit `evidence_refs` make the agent pre-judge which evidence is
  relevant and create a false sense of auditability. Marivo should inspect the
  current datasource and registry state itself, then report what it can prove
  from those current observations.

Internal orchestration:

1. For each `AuthoringSourceInput`, inspect the current datasource/source with
   `BoundedProfilePolicy(limit=100, max_profiled_columns=50)` using project-
   bound datasource access.
2. For each source role, if `columns` is non-empty, inspect those columns in
   the current source.
3. Read explicit decision-ledger records relevant to dangerous choices such as
   metric decomposition, time-field identity, and relationship confirmation.
4. Call the breaking multi-source `check_authoring_inputs(...)` with the full
   `sources` tuple and the full `semantic_refs` tuple.
5. Return `AuthoringAssessment` with facts, issues, and questions that Marivo
   can derive from current project state.

This design intentionally changes `check_authoring_inputs(...)` from a
single-source guardrail to a multi-source guardrail. No compatibility shim is
defined for the old `datasource=...`, `source=...`, `columns=...` signature.
The checker owns role-aware physical-column validation, so a relationship
to-side column is never checked against the from-side source by accident.

The implementation work must update:

- `SemanticProject.check_authoring_inputs(...)` and
  `marivo.semantic.authoring_check.check_authoring_inputs(...)` signatures to
  accept `sources: Sequence[AuthoringSourceInput]` instead of
  `datasource`, `source`, and `columns`;
- column-existence checks to run per source role against that role's own
  source schema;
- status derivation for multi-source results (`blocked` dominates
  `needs_input`, which dominates `supported`);
- returned `AssessmentFact`, `AssessmentIssue`, and `AuthoringQuestion` payloads
  so source role and source identity remain visible to agents;
- tests and skill examples that call the old single-source
  `check_authoring_inputs(...)` signature.

The call is not a durable cache lookup. Re-assessing may re-inspect metadata or
bounded samples because datasource state can change. Implementations may keep a
short-lived in-process cache during one `SemanticProject` session, but cached
observations must never be treated as persisted project truth.

#### `AuthoringAssessment` DTO

```python
@dataclass(frozen=True)
class AuthoringAssessment:
    status: ReviewStatus
    facts: tuple[AssessmentFact, ...]
    issues: tuple[AssessmentIssue, ...]
    questions: tuple[AuthoringQuestion, ...]
```

`AuthoringAssessment.status` reuses the existing `ReviewStatus` vocabulary
used by `AssessmentResult`:

| Status | Meaning |
| --- | --- |
| `blocked` | A blocker issue or blocking question prevents authoring or handoff |
| `needs_input` | Required source context, semantic dependency, or user decision is missing but no blocker exists |
| `supported` | Required context is present and no blocking issue or question remains |

Status derivation:

- `blocked` when any issue has `severity="blocker"` or any question has
  `readiness_effect="blocks"`.
- `needs_input` when required context or a user decision is missing but no blocker exists.
- `supported` when required context is present and no blocking issue or
  question remains.

The API does **not** generate code. Code authoring is the agent's creative
decision — it has user prompts, knowledge bases, and business context that
the library does not.

### Internal `project.check_authoring_inputs(...)`

`check_authoring_inputs(...)` remains an implementation detail of
`assess_authoring(...)`, but its target shape is breaking and multi-source:

```python
def check_authoring_inputs(
    self,
    *,
    object_kind: AuthoringObjectKind,
    subject_ref: str,
    sources: Sequence[AuthoringSourceInput] = (),
    semantic_refs: Sequence[str] = (),
) -> AssessmentResult:
```

The old `datasource`, `source`, and `columns` parameters are removed. Source
identity and physical columns are carried by each `AuthoringSourceInput`.
The old explicit `evidence_refs` and `ai_context` inputs are also removed:
the checker works from current inspection facts, and authored `ai_context` is
checked after reload against the actual object.

### `project.readiness(...)`

`readiness(...)` is the Phase 3 closeout API. It is the normal agent workflow
for deciding whether semantic refs can be handed to analysis.

```python
def readiness(
    self,
    *,
    refs: Iterable[str] | None = None,
    demand: DemandSignal | None = None,
    preview_limit: int = 20,
    parity_rel_tol: float | None = None,
    parity_abs_tol: float | None = None,
    redact: bool = True,
) -> ReadinessReport:
```

When `refs` is `None`, checks all loaded handoff refs. When `refs` is
provided, checks only the specified refs and their required dependencies.
`readiness(...)` uses the project's bound backend access. It must not accept a
per-call `backend_factory`; callers configure backend access before Phase 3.
If no backend access is bound, the report is blocked.

Internal orchestration:

1. `project.load()`.
2. Resolve every requested ref and dependency from the loaded registry.
3. Run required backend previews/materialization for datasets, fields,
   time-fields, and metrics, using `preview_limit` and `redact`.
4. Run eligible metric parity checks using `parity_rel_tol` and
   `parity_abs_tol`.
5. Run readiness structural, provenance, and decision-ledger checks.
6. Run richness checks using `demand`.
7. Return one `ReadinessReport`.

The report must not collapse runtime detail into one opaque status. Preview,
parity, readiness, and richness findings must carry refs, severity, rule or
finding kind, and a repair hint.

#### `ReadinessReport` closeout fields

```python
@dataclass(frozen=True)
class ReadinessReport:
    status: Literal["ready", "ready_with_warnings", "blocked"]
    analysis_ready_refs: tuple[str, ...]
    blockers: tuple[ReadinessIssue, ...]
    warnings: tuple[ReadinessIssue, ...]
    input_summary: ReadinessInputSummary
    preview_summary: PreviewSummary
    parity_summary: ParitySummary
    richness_summary: RichnessSummary
    checked_at: str
```

Fields:

| Field | Content |
| --- | ------- |
| `status` | Aggregate closeout status |
| `analysis_ready_refs` | Refs without blockers |
| `blockers` | Hard failures that can make analysis fail |
| `warnings` | Non-blocking parity, richness, enrichment, provenance, or advisory findings |
| `input_summary` | Datasources, refs, metadata, and decision-ledger records used by the report |
| `preview_summary` | Required, completed, and failed backend previews |
| `parity_summary` | Eligible, verified, drifted, unverified, unsupported, and skipped metric parity refs |
| `richness_summary` | Advisory richness gaps folded into readiness warnings |

Status derivation:

- `blocked` when any blocker exists.
- `ready_with_warnings` when there are no blockers but at least one warning
  exists.
- `ready` when there are no blockers and no warnings.

Only hard operability failures become blockers. Parity and richness findings
are warnings even when parity drifts from `source_sql` or richness identifies
missing coverage.

## NextCheck Removal

The `NextCheck` type alias and the `next_checks` fields on
`AssessmentIssue` and `AssessmentResult` are removed from the public API.

`NextCheck` was a choreography hint — "the agent should call this method
next" — but `assess_authoring` composes the static assessment checks that used
to drive those hints, and `readiness(...)` composes Phase 3 closeout checks.
The agent no longer needs to dispatch on `next_checks`.

This is a breaking DTO change for every consumer of `AssessmentIssue` or
`AssessmentResult`, including `inspect_authored_object(...)` callers. The
implementation work must update:

- authoring-check code paths that currently populate `next_checks`;
- tests and skill examples that inspect `AssessmentResult.next_checks` or
  `AssessmentIssue.next_checks`.

If an issue needs a repair hint in the future, use a `suggested_action: str`
field (human-readable) rather than an agent-dispatchable enum.

## API Classification

### Stay public

These APIs have independent use cases beyond the authoring pipeline:

| API | Reason |
| --- | ------ |
| `md.inspect_table` | Standalone source exploration without authoring |
| `md.inspect_columns` | Standalone column inspection |
| `inspect_authored_object` | Debugging helper for post-reload static inspection; readiness calls the equivalent checks during closeout |
| `catalog.preview(...)` | Debugging helper for inspecting bounded semantic entity, field, or metric runtime values; readiness runs required previews during closeout |
| `parity_check` | Debugging helper for inspecting SQL-parity detail; readiness runs eligible parity checks and reports findings as warnings |
| `readiness` | Single closeout and analysis-handoff gate |
| `richness` | Debugging/advisory helper for richness-only reports; readiness folds richness gaps into warnings |
| `assess_authoring` | New composed API |

### Become internal

These APIs are implementation details of the composed static assessment flow and
no longer need to be part of the agent-facing authoring workflow:

| API | Called by |
| --- | --------- |
| `check_authoring_inputs` | `assess_authoring` |
| `collect_source_preview` | Removed; use `md.preview(...)` instead |
| `record_authoring_evidence`, `list_evidence`, `get_evidence_pack` | Removed from the normal authoring API; use authored object fields and decision-ledger records instead |

"Internal" means: removed from the skill's authoring workflow and from the
public `SemanticProject` documentation aimed at agents. The methods may still
exist on `SemanticProject` for direct use in debugging or advanced scenarios,
but the skill does not direct agents to call them during normal authoring.

## Superseded Sections

| Source document | Superseded section | Replacement |
| --- | --- | --- |
| `skill-semantic-layer-authoring-design.md` | "Skill Workflow" (9 stages) | Three-phase flow in this document |
| `skill-semantic-layer-authoring-design.md` | `NextCheck` type and `next_checks` semantics | Removed; `assess_authoring` composes internally |
| `skill-semantic-layer-authoring-design.md` | "Light Authoring Input Check" as a standalone step | `assess_authoring` composes it |
| `skill-semantic-layer-authoring-design.md` | Multi-file authoring examples | Single `_domain.py` authoring file |
| `agent-semantic-layer-authoring-design.md` | Phase 0 loop | Three-phase flow in this document |
| `python-semantic-layer.md` | General multi-file organization recommendation for agent-authored models | Single `_domain.py` authoring file for this pipeline |

Sections of those documents covering persisted evidence stores or evidence-ref
choreography are superseded by this design. Sections covering provenance,
decision-ledger requirements, and anti-goals remain authoritative unless they
conflict with this document.

## Interaction Summary

The complete agent interaction removes manual static-check and runtime-check
choreography:

1. **Phase 1:** `md.inspect_table` + optional `md.inspect_columns`
   (per source, not per object)
2. **Phase 2:** `assess_authoring` per candidate object + file write (no
   reload)
3. **Phase 3:** `readiness` for reload, ref checks, required backend previews,
   parity warnings, richness warnings, and handoff status

For a typical model with 3 datasets, 6 fields, 3 metrics, and 2
relationships, the current flow requires many individual API calls with
manual static orchestration. The target flow requires `assess_authoring` per
candidate object and one `readiness(...)` closeout call.

---

## Part 3: Skill / Semantic Layer Authoring Overall Design

The following content was previously in `skill-semantic-layer-authoring-design.md`.

The old standalone static-check workflow is no longer the public agent
contract. Normal semantic authoring now follows the stepwise authoring design:

1. `ms.help(...)` for static constructor contracts.
2. `md.discover_*` for bounded datasource evidence.
3. Evidence settlement or one-at-a-time grill questions.
4. Authoring exactly one semantic object.
5. `ms.verify_object(...)` after writing.
6. A single `ms.readiness(...)` closeout for the target refs.

The standalone authoring-input checker is an internal implementation detail of
`project.assess_authoring(...)`, not a normal skill call-site. Richness findings
are folded into readiness warnings and `richness_summary`; normal closeout does
not use a separate richness gate.
