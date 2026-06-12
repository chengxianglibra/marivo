# Agent Semantic Layer Authoring Design

> **Superseded for agent authoring:** use
> `docs/specs/semantic/stepwise-authoring-design.md` for the active
> stepwise prepare/verify/readiness workflow. This document remains only as
> historical context for the previous pipeline.

Status: superseded.

The active normal workflow is defined in
`docs/specs/semantic/stepwise-authoring-design.md`. The older phased workflow
in this document is retained as background, but standalone check choreography is
superseded. Current agents should use:

1. Discovery and source inspection.
2. `project.prepare_entity(...)` / `project.prepare_metric(...)` for each candidate object.
3. `project.verify_object(...)` after writing.
4. A single `project.readiness(...)` closeout.

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

The source of truth remains Python files under `.marivo/semantic/<model>/`.
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
`.marivo/datasource/<name>.py` when authoring datasource files directly. Use
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
.marivo/
  datasource/
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
- `@ms.metric(..., verification_mode="python_native",)`
- `ms.derived_metric(...)`
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
- `verification_mode`

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
@ms.metric(
    name="revenue",
    datasets=[orders],
    additivity="additive",
    decomposition=ms.sum(),
    verification_mode="sql_parity",
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
aov = ms.derived_metric(
    name="aov",
    decomposition=ms.ratio(
        numerator=revenue,
        denominator=orders_count,
    ),
    additivity="non_additive",
)
```

Rules:

- do not default to `ms.sum()` when decomposition is unclear
- ratios and averages require explicit components
- source SQL provenance should be preserved when available
- no-source base metrics must use `verification_mode="python_native"`
- SQL-backed base metrics must use `verification_mode="sql_parity"` with
  `source_sql` and `source_dialect`; status stays `unverified` until parity succeeds
- derived metric readiness inherits component status; derived metrics must omit
  `verification_mode`, `source_sql`, and `source_dialect`

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

`marivo-skills/marivo-semantic/SKILL.md` should stay short and route the agent
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
