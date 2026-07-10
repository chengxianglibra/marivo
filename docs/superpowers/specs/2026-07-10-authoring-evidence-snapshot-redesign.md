# Authoring Evidence Snapshot Redesign

## Status

Accepted target design. Not yet implemented. The current runtime still exposes
the existing `inspect_*`, `discover_*`, and authoring validation paths. Do not
use the target API examples in this document as current runtime instructions.

Implementation requires a separate plan and must update code, skills, help,
canonical runtime specs, and latest site documentation together.

Date: 2026-07-10

This design supersedes the workflow and discovery portions of
[`2026-07-08-semantic-authoring-public-guidance-design.md`](2026-07-08-semantic-authoring-public-guidance-design.md).
It follows the typed object graph, lookup rules, navigation matrix, and runtime
handoff contract accepted in
[`2026-07-10-catalog-object-navigation-design.md`](2026-07-10-catalog-object-navigation-design.md).

## Context

Marivo semantic authoring must give an agent enough runtime evidence to author
entities, dimensions, time dimensions, measures, metrics, and relationships
without turning discovery into an uncontrolled scan. That requirement becomes
critical for tables measured in terabytes or petabytes, where a query with a
small result `LIMIT` may still scan a large amount of data.

The current design has the right high-level intent: discovery is bounded,
evidence-only, and split into focused calls. The implementation and public
workflow nevertheless leave important gaps:

- top-level authoring help does not teach a runnable inspect-and-scope path;
- discovery calls repeat source inspection and sampling instead of reusing one
  acquisition;
- a row limit bounds returned rows but is easy to misread as a scan-cost guard;
- partition metadata does not always distinguish known, absent, and unknown
  partitioning strongly enough;
- timeout configuration is not useful unless an adapter actually enforces it;
- sample statistics can be mistaken for exact source statistics;
- dimension-value completeness can be inferred incorrectly from a truncated
  top-values list;
- bounded `.show()` output can omit important later columns even though the
  underlying result contains evidence;
- sampled uniqueness does not distinguish an observed fact from a business-key
  decision;
- fixed date and hour encodings such as `YYYYMMDD` and `HH` need deterministic
  recognition without expanding discovery into speculative semantic inference;
- authoring validation, runtime smoke checking, and readiness do not expose a
  sufficiently explicit evidence-level contract.

These are one workflow problem. The current public surface asks an agent to
coordinate independent functions and remember implicit safety rules. The
replacement should make the safe order visible in the object model, acquire
data once, and preserve a strict boundary between observations and semantic
judgment.

## Goals

- Provide one low-cognitive-load authoring path from source inspection to a
  catalog object that is safe to hand to `marivo.analysis`.
- Put physical extent, partition state, and enforceable execution capabilities
  before any data-reading operation.
- Require an explicit scope for every authoring operation that reads user data.
- Acquire one bounded sample and reuse it for all discovery projections.
- Make evidence coverage, exactness, cache identity, and truncation impossible
  to confuse with full-source truth.
- Recognize only documented deterministic formats and syntax rules; leave
  non-deterministic semantic interpretation to the agent.
- Keep semantic objects authored explicitly in Python, with Python and Git as
  the source of truth.
- Align post-authoring navigation and runtime handoff with the accepted catalog
  object graph.
- Remove obsolete and competing public paths in one breaking change.

## Non-goals

- A general-purpose profiling engine.
- Random or statistically representative sampling across every backend.
- A cross-backend promise that `LIMIT` bounds bytes scanned.
- Automatic primary-key, business-key, aggregation, unit, additivity, timezone,
  relationship-cardinality, or business-definition selection.
- A scoring or ranking model for semantic candidates.
- Arbitrary date-format synthesis, epoch-unit guessing, or multi-column search.
- A planner or wizard that authors semantic Python for the agent.
- Persisting agent decisions, grill answers, or generated semantic definitions.
- Redesigning analysis APIs after the readiness handoff.
- Redesigning the catalog object graph currently being implemented.
- Backward compatibility for the removed discovery and source-declaration
  surfaces.

## Design principles

The redesign is governed by these principles:

1. **Inspect before reading.** Metadata and cost context are available before
   an agent can acquire user data.
2. **Explicit scope for every read.** No authoring data read has a default
   unpruned path.
3. **One acquisition, many projections.** Discovery projections are pure views
   over one reusable snapshot.
4. **Evidence is scoped.** Every statistic names whether it describes the
   sample or an exhaustively read scope.
5. **Unknown is not none.** Unknown partition state never silently becomes an
   unpartitioned-source decision.
6. **Only enforceable guards are guarantees.** Unsupported timeout or byte
   controls are capability gaps, not pretend safeguards.
7. **Rules are deterministic or absent.** A rule must be documented,
   reproducible, threshold-free, and accompanied by match/failure evidence.
8. **Marivo observes; the agent decides.** Discovery never converts sampled
   facts into business semantics.
9. **Rendering is not the data API.** Bounded display can omit rows; structured
   result fields remain complete.
10. **One public path per capability.** The object lifecycle itself teaches the
    safe next operation.

## Decision

Marivo authoring becomes an evidence-snapshot state machine:

```text
browse/help
  -> inspect metadata and cost
  -> choose explicit scope
  -> acquire reusable snapshot
  -> project semantic-shaped evidence
  -> agent settle/grill
  -> author one Python object
  -> load typed CatalogObject
  -> static verification
  -> explicit scoped runtime preview
  -> readiness
  -> marivo.analysis handoff
```

The primary target API is:

```python
import marivo.datasource as md
import marivo.semantic as ms

warehouse = md.ref("datasource.warehouse")
source = md.table("orders")

inspection = md.inspect(warehouse, source)
inspection.show()
inspection.partitions().show()

snapshot = inspection.sample(
    scope=md.partition({"log_date": "20260710", "log_hour": "13"}),
    columns=(
        "query_id",
        "self",
        "region",
        "log_date",
        "log_hour",
        "amount",
    ),
)

snapshot.entity(columns=("query_id", "self")).show()
snapshot.dimensions(columns=("region",)).show()
snapshot.time_dimensions(columns=("log_date", "log_hour")).show()
snapshot.measures(columns=("amount",)).show()

# The agent uses help, evidence, catalog facts, and user answers to write
# semantic Python explicitly, then reloads the project.
catalog = ms.load()
revenue = catalog.domains.get("sales").metrics.get("revenue")

catalog.verify_object(revenue).show()
catalog.preview(revenue, using=snapshot).show()
catalog.readiness(refs=[revenue]).show()
```

## Public ownership

### Datasource ownership

`marivo.datasource` owns physical source descriptors, inspection, explicit
scope, acquisition, snapshots, and runtime evidence.

There is one source descriptor family:

```python
md.table(...)
md.parquet(...)
md.csv(...)
md.json(...)
```

The same descriptor is reused by inspection and semantic entity authoring.
`marivo.semantic` does not duplicate these constructors.

`md.raw_sql(..., reason=...)` remains the bounded, read-only diagnostic escape
hatch defined by the datasource layer. It is outside the ordinary authoring
evidence path, does not create or enrich a snapshot, and is never suggested as
the primary schema or semantic discovery route. Its explicit SQL, reason, and
bounded-result contract remain visible in its own help topic.

### Semantic ownership

`marivo.semantic` owns static authoring help, explicit semantic constructors,
typed refs, project loading, the catalog object graph, verification, runtime
preview, and readiness.

### Agent ownership

The agent owns semantic judgment:

- selecting the business identity;
- selecting the canonical event time and timezone;
- choosing dimension meaning and privacy treatment;
- choosing aggregation, unit, and additivity;
- deciding relationship cardinality and business constraints;
- writing the Python definition.

Marivo does not introduce a public `prepare` stage or an automatic authoring
planner.

## Source inspection

`md.inspect(datasource, source)` is metadata-only. It returns a
`SourceInspection` and does not execute a query against user table data.

The high-priority inspection contract is:

```text
source
physical_extent
partitioning
execution_capabilities
next_safe_action
schema
```

`SourceInspection` exposes the same information through typed structured
properties. An agent must not need to parse rendered text to obtain row count,
size, partition columns, or capabilities.

### Physical extent

Physical extent contains:

```text
row_count
row_count_kind: exact | estimated | unknown
size_bytes
size_kind: exact | estimated | unknown
source
notes
```

Whole-table estimates are useful context but not a prediction of the cost of a
specific scope. When backend metadata can provide per-partition extent, the
inspection includes it. When it cannot, the value is `unknown`.

### Partition state

Partitioning is a closed state:

```text
known
none
unknown
```

- `known` means partition metadata identified the partition fields.
- `none` means metadata positively established that the source is not
  partitioned.
- `unknown` means inspection could not establish either state.

The partition result also exposes columns, transform information, value source,
bounded values, value completeness, and truncation. `unknown` is never treated
as `none`.

`inspection.partitions()` remains metadata-only. It reads connector or system
catalog metadata, not table rows. When partition values are unavailable, the
result says so instead of falling back to a sample query.

### Execution capabilities

Inspection reports capabilities that matter before acquisition:

```text
partition_predicate_supported
transformed_partition_supported
timeout_enforced
byte_estimate_supported
byte_limit_enforced
```

The values describe real adapter behavior. A configuration field is not a
capability unless the adapter applies it during execution.

## Explicit scope

There are two normal authoring scopes:

```python
md.partition(
    {"log_date": "20260710", "log_hour": "13"},
    max_rows=1000,
    timeout_seconds=30,
)

md.unpruned(
    max_rows=1000,
    timeout_seconds=30,
)
```

The scope argument is mandatory for `SourceInspection.sample()` and every other
authoring operation that reads user data.

### Partition scope rules

For `partition_state=known`:

- the scope must cover every partition field the adapter can express;
- every predicate must be validated and pushed down;
- an unsupported transformed partition blocks before query execution;
- the query records the actual pushed predicate.

For `partition_state=none`, the agent still supplies `md.unpruned(...)` to
acknowledge that no partition pruning is available.

For `partition_state=unknown`, only an explicit `md.unpruned(...)` can proceed.
The result retains the unknown state and a warning. Marivo does not silently
claim that the source is unpartitioned.

### Scope is not a scan-cost guarantee

`max_rows` controls returned rows through `LIMIT max_rows + 1`; it does not
universally control scanned bytes. Partition pruning can reduce the scan but
does not prove that a partition is small. `md.unpruned(...)` is explicit risk
acknowledgement, not a claim of low cost.

Timeout must be enforced by the adapter. If it is not enforceable, acquisition
fails before reading data. Byte limits are exposed only for backends that can
enforce them reliably; the design does not invent a fake cross-backend byte
guard.

## Snapshot acquisition

`SourceInspection.sample(scope=..., columns=...)` is the only ordinary
authoring acquisition entry point.

The selected columns are explicit and non-empty. This matters for wide sources
and columnar scan cost. Inspection exposes complete schema names so an agent
can choose the required columns before reading data.

Before execution, acquisition performs a preflight that checks:

- explicit scope presence;
- current partition state and complete partition coverage;
- predicate expressibility and pushdown;
- real timeout support;
- source and schema fingerprints;
- available backend estimates and enforceable guards.

No separate public `plan_sample()` or authoring session object is introduced.
The inspection already exposes the decision inputs; `sample()` performs the
mandatory safety validation immediately before execution.

### Query behavior

One acquisition performs one user-data query:

```sql
SELECT <explicit columns>
FROM <source>
WHERE <validated scope predicate>
LIMIT <max_rows + 1>
```

The implementation trims the extra row after using it to determine truncation.
It does not use `ORDER BY random()` or imply random sampling. The snapshot
records the actual sampling method and predicate.

All column profiles and bounded evidence inputs are derived locally from this
single result. A projection must not reinspect the source or execute another
data query.

## Discovery snapshot

Acquisition returns an immutable `DiscoverySnapshot`. It contains derived
evidence rather than a persisted copy of the full result rows.

Persisted snapshot content includes:

- datasource and source fingerprints without resolved secrets;
- schema fingerprint;
- explicit scope and pushed predicate;
- selected columns;
- acquisition and sampling method;
- observed and retained row counts;
- scope exhaustion;
- per-column profiles;
- a bounded frequency dictionary;
- limited display samples needed for evidence review;
- evidence format version;
- creation, expiry, and cache status.

It does not persist:

- full raw result rows;
- business decisions;
- grill answers;
- selected semantic parameters;
- generated semantic definitions.

### Pure projections

The snapshot exposes semantic-shaped projections:

```python
snapshot.entity(columns=(...))
snapshot.dimensions(columns=(...))
snapshot.values("region", limit=50)
snapshot.time_dimensions(columns=(...))
snapshot.measures(columns=(...))
snapshot.relationships(other_snapshot, left=(...), right=(...))
```

All projections are pure local transformations of stored snapshot evidence.
They execute no metadata or user-data query.

## Evidence contract

Every evidence item separates three categories:

```text
observations
deterministic_matches
unresolved
```

### Observations

Observations are direct sample facts such as physical type, null count,
distinct count, min/max, lengths, character patterns, and displayed values.

### Deterministic matches

A deterministic rule is allowed only when it is:

- precisely defined and documented;
- reproducible;
- independent of business context;
- free of an empirical acceptance threshold;
- able to report checked, matched, and failed counts;
- unable to masquerade as a semantic decision.

Each match exposes a stable rule ID and its exact evidence:

```text
rule: date.yyyymmdd
checked: 1000
matched: 999
failed: 1
```

Marivo does not turn `999/1000` into a hidden pass or fail. The agent decides
whether the mismatch is acceptable.

### Unresolved decisions

Evidence explicitly lists decisions that the sample cannot prove. These remain
agent-owned even when a deterministic syntax rule matches every sampled value.

## Coverage and exactness

Ambiguous names such as `distinct_count`, `null_count`, and `complete` are not
used without a scope qualifier.

Common fields include:

```text
sample_row_count
sample_null_count
sample_distinct_count
scope_distinct_count
scope_distinct_lower_bound
sample_exactness
scope_exhaustion
sampling_method
```

Sample statistics can be exact about the acquired sample. They are not exact
about the scope or source unless acquisition proved that the scope was
exhausted. `scope_distinct_count` exists only for an exhaustively read scope;
otherwise evidence exposes a lower bound.

The only ordinary proof of scope exhaustion is receiving no extra row from the
`LIMIT max_rows + 1` acquisition. Exactness always applies to the explicit
scope, never automatically to the full source.

## Time evidence

Automatic time recognition is deliberately narrow. It covers only documented
fixed rules:

- native date and timestamp physical types;
- strict ISO date and datetime strings;
- strict `YYYYMMDD` strings and integers;
- strict `00..23` hour components;
- strict date-plus-hour composition when the agent explicitly selects both
  columns.

For example:

```python
snapshot.time_dimensions(columns=("log_date", "log_hour"))
```

can produce:

```text
log_date:
  rule: date.yyyymmdd
  matched: 1000/1000

log_hour:
  rule: time.hour_00_23
  matched: 1000/1000
  role: component_only

explicit_composition:
  columns: [log_date, log_hour]
  rule: datetime.yyyymmdd_hour
  matched: 1000/1000
```

An hour component is not reported as an independently parseable timestamp.
Date-plus-hour composition is checked only because the agent passed those
columns together; discovery does not search arbitrary column combinations.

Marivo does not automatically synthesize unusual date formats, guess Unix
epoch units, rank mixed-format candidates, infer timezone, choose the business
event time, or choose a default time dimension. Unmatched columns expose direct
samples, lengths, character patterns, and failure counts.

## Entity evidence

Sample uniqueness is an observation, not a key decision. For example:

```text
query_id:
  sample_unique: true
  sample_distinct_count: 1000
  sample_null_count: 0
  name_suffix: "_id"
  url_syntax_matches: 0/1000

self:
  sample_unique: true
  sample_distinct_count: 1000
  sample_null_count: 0
  name_suffix: none
  url_syntax_matches: 1000/1000
```

These are transparent lexical and syntax facts. Marivo does not label either
column as a business key, locator, canonical identifier, or recommended key.
Business identity, cross-partition uniqueness, temporal stability, and key
reuse remain unresolved.

Discovery does not search arbitrary composite keys. The agent may explicitly
ask for observations about a chosen tuple:

```python
snapshot.entity(columns=("order_id", "line_number"))
```

## Dimension and measure evidence

Dimension evidence contains direct observations:

- sample distinct, null, and empty counts;
- bounded top values and frequency evidence;
- physical type;
- string length and basic character patterns;
- partition role.

It does not infer category meaning, label semantics, privacy policy, or business
definition.

Measure evidence contains direct observations:

- physical numeric type;
- min/max;
- null, zero, and negative counts;
- decimal precision and scale;
- sample distinct count.

It does not infer aggregation, unit, additivity, or business definition.

### Dimension values completeness

The snapshot retains a bounded frequency dictionary. `snapshot.values(...)`
reports storage completeness separately from scope completeness:

```text
sample_distinct_count: 15
returned_value_count: 10
sample_values_complete: false
scope_values_complete: false
status: incomplete
```

`scope_values_complete` is true only when both conditions hold:

```text
sample_values_complete
and scope_exhaustion == exhaustive
```

The result cannot infer completeness from `returned_value_count <= requested
limit`. The frequency-dictionary capacity is recorded in the snapshot and
visible in the result. `snapshot.values()` never issues a follow-up query.

## Relationship evidence

Relationship discovery is fully explicit:

```python
orders_snapshot.relationships(
    customers_snapshot,
    left=("customer_id",),
    right=("customer_id",),
)
```

Marivo does not search for join columns. It reports only observations available
from the two snapshots, including physical type compatibility, nulls, sample
uniqueness, retained-value overlap, orphan observations, and whether the two
scopes are comparable.

When retained evidence cannot support an overlap calculation, the result says
`unavailable`; it does not rescan either source. Incomparable scopes produce an
`incomplete` or `blocked` result, not an inferred cardinality. Business
cardinality and relationship constraints remain unresolved.

## Snapshot persistence and invalidation

Snapshots are persisted project-locally:

```text
.marivo/authoring/snapshots/<snapshot-id>.json
```

Snapshot identity includes:

- datasource specification fingerprint without resolved secrets;
- physical source descriptor;
- explicit scope;
- selected columns;
- schema fingerprint;
- evidence format version.

Each entry records `created_at`, `expires_at`, and cache status. A snapshot is
invalidated by datasource, source, scope, selected-column, schema, evidence
version, or TTL changes. `refresh=True` explicitly reacquires it.

Cache use is never hidden. Every result identifies the snapshot and reports
whether the evidence is fresh, cached, stale, or mismatched.

## Semantic authoring surface

The authoring surface remains helper-first and explicit.

Direct column-backed objects use focused helpers such as:

```python
ms.dimension_column(...)
ms.time_dimension_column(...)
ms.measure_column(...)
```

Decorators remain available for expression bodies. Marivo does not add a
mega-constructor or convert discovery evidence into semantic Python.

Analyzable entity, dimension, time dimension, measure, and metric definitions
require business definition and AI context at construction time. A concise
form remains available:

```python
ai_context=ms.ai_context("Business definition", ...)
```

Static help owns the constructor contract. Each help entry includes:

- one complete runnable constructor;
- required and optional parameters;
- static constraints;
- the matching snapshot evidence projection;
- the next `catalog.verify_object(...)` step.

Discovery output names the relevant help topic, evidence subject, and unresolved
decisions. It does not emit recommended constructor arguments.

Typed refs remain the cross-file and forward-reference mechanism. Bare strings
are rejected. Python definitions and Git remain the semantic source of truth;
catalog browsing is read-only.

## Catalog alignment and validation

This design follows the accepted catalog object navigation contract:

- concrete `CatalogObject` subclasses are the primary loaded values;
- global and scoped `CatalogCollection` properties are the browse path;
- typed IDs are directly reusable with `catalog.get(...)`;
- `details()` exposes rich facts without becoming a second browse API;
- the old `SemanticObject`, `SemanticObjectList`, and `catalog.list(...)` model
  are not restored.

Post-authoring validation uses the existing catalog method names rather than a
new parallel `validate/check` vocabulary.

### Static verification

```python
catalog.verify_object(revenue).show()
```

Static verification performs no datasource query. It checks reference and
dependency integrity, types, cycles, expression binding, time format contracts,
aggregation properties, and relationship structure as applicable.

The result explicitly reports:

```text
validation_level: static
runtime_checked: false
```

The canonical path passes a concrete catalog object. An appropriate
`SemanticRef` remains a valid handoff input under the catalog design.

### Scoped runtime preview

```python
catalog.preview(revenue, using=snapshot).show()
```

`using` is mandatory in authoring guidance because preview reads user data. It
binds preview to the snapshot's datasource, source, explicit scope, and schema
fingerprint. A mismatch blocks before execution.

Single-source objects use one snapshot. Cross-entity metrics and relationships
use the required snapshot tuple. Preview compiles and executes the smallest
bounded query needed for an object-level smoke check and returns limited rows,
actual scope, backend, warnings, and runtime status.

Preview proves that the current definition executes for the current backend
and explicit scope. It does not prove every future analysis query shape.

Preview results may be persisted under:

```text
.marivo/authoring/checks/<check-id>.json
```

Their identity includes semantic and dependency fingerprints, snapshot IDs,
backend, and check format version.

### Readiness

```python
catalog.readiness(refs=[revenue]).show()
```

Readiness performs no datasource query. It expands the dependency closure and
reads fresh static verification and runtime-preview evidence. It reports:

```text
structurally_valid
runtime_checked
handoff_ready
```

Statuses are:

```text
ready
ready_with_warnings
blocked
```

Missing, stale, or mismatched runtime evidence blocks handoff for directly
handed executable objects and produces an exact
`catalog.preview(..., using=...)` next call. Relationship and cross-entity
objects require evidence for every involved source. Optional richness
diagnostics are not readiness blockers.

The catalog object navigation design's examples, plan, help, and tests must be
coordinated so authoring preview examples include explicit snapshot scope. This
is a dependent refinement of runtime preview, not a replacement catalog graph.

## Progressive guidance

The public workflow is:

```text
md.help("authoring")
  -> md.inspect(...)
  -> inspection.show()
  -> inspection.sample(scope=..., columns=...)
  -> snapshot.<projection>()
  -> ms.help("<constructor>")
  -> agent settle/grill
  -> write Python object
  -> ms.load()
  -> catalog typed navigation
  -> catalog.verify_object(obj)
  -> catalog.preview(obj, using=snapshot)
  -> catalog.readiness(refs=[obj])
  -> marivo.analysis
```

Every result suggests only methods that exist on the current object and are
valid in the current state. Suggestions are derived from live inspection,
snapshot, or catalog state rather than hardcoded examples.

`md.help("authoring")` contains one complete runnable path and teaches explicit
scope. Constructor help remains the source of parameter contracts. Skills own
workflow and routing only; they do not duplicate parameter tables.

## Result and display protocol

Inspection, evidence, verification, preview, and readiness results begin with
a common decision header:

```text
status
scope
rows_observed
scope_exhaustion
exactness
cache
warnings
next
```

Status values are:

- `complete`: the operation completed with the stated coverage;
- `incomplete`: evidence is usable but not exhaustive;
- `blocked`: the operation did not perform an unsafe or invalid action.

Bounded `render()` and `.show()` are presentation surfaces. Typed collection
properties such as `.items`, `.columns`, and `.evidence_by_column` expose the
complete structured result. Display truncation states displayed, total, and
omitted counts before detail is omitted.

Evidence display prioritizes partition, candidate-key observations, time,
dimensions, measures, and remaining schema. Single-purpose projections accept
explicit columns so an agent can request a small relevant rendering.

Authoring guidance does not depend on a hidden `max_output_bytes=None` escape.
Any public display parameter must be visible in the Python signature and help,
but complete evidence consumption uses structured fields instead of unbounded
text.

This follows the catalog collection rule: rendered rows can be bounded while
the structured collection remains complete.

## Error contract

Authoring errors follow the shared semantic error model and expose:

```text
code
stage
status: blocked
expected
received
reason
query_executed
scope_state
next_calls
```

`next_calls` are executable calls generated from current state. They are not
generic prose suggestions.

For an incomplete known partition scope:

```text
code: incomplete_partition_scope
query_executed: false
required_partition_fields: [log_date, log_hour]
received: {log_date: "20260710"}

next_calls:
  inspection.sample(
      scope=md.partition({
          "log_date": "20260710",
          "log_hour": "13",
      }),
      columns=(...),
  )
```

For unknown partition state:

```text
code: partition_state_unknown
query_executed: false
reason: metadata could not prove whether the source is partitioned

next_calls:
  inspection.show()
  inspection.sample(
      scope=md.unpruned(max_rows=1000, timeout_seconds=30),
      columns=(...),
  )
```

Blocked operations always state `query_executed=false`. Cache staleness, schema
mismatch, unsupported timeout, unsupported partition transform, and snapshot
source mismatch have equivalent structured teaching errors.

## Cost safety contract

Marivo guarantees:

- cost and partition evidence is exposed before acquisition;
- every authoring data read has an explicit scope;
- known partition predicates are validated and pushed down;
- unknown state remains visible;
- configured timeouts are enforced or the operation is blocked;
- returned rows and output are bounded;
- query and cache provenance is recorded.

Marivo does not guarantee:

- that `LIMIT` bounds bytes scanned;
- that a partition is small;
- that a backend estimate is exact;
- that an unpruned query is inexpensive;
- that a first-N sample is representative;
- that a successful scoped preview proves every analysis execution shape.

These limits are part of normal result rendering and documentation, not hidden
in an advanced caveat.

## Breaking removal

The implementation removes the old datasource authoring path:

- `md.inspect_table(...)`
- `md.inspect_partitions(...)`
- `md.discover_entity(...)`
- `md.discover_dimensions(...)`
- `md.discover_dimension_values(...)`
- `md.discover_time_dimensions(...)`
- `md.discover_measures(...)`
- `md.discover_relationship(...)`
- top-level public sample/discover shortcuts that bypass inspection
- the old public `ScanScope` construction path
- authoring guidance that depends on hidden unbounded `.show()` output

It also removes duplicate semantic source descriptors:

- `ms.table(...)`
- `ms.parquet(...)`
- `ms.csv(...)`
- `ms.json(...)`

The replacement surface is:

```text
md.inspect(...)
SourceInspection
inspection.partitions()
md.partition(...) / md.unpruned(...)
DiscoverySnapshot
snapshot.entity(...)
snapshot.dimensions(...)
snapshot.values(...)
snapshot.time_dimensions(...)
snapshot.measures(...)
snapshot.relationships(...)
```

There are no deprecated aliases, compatibility warnings, or hidden acceptance
of the removed call shapes.

## Documentation and skill migration

The target design lands before implementation, so canonical runtime docs must
continue to teach the installed API until the implementation is complete. The
implementation updates all active surfaces in the same change:

- datasource and semantic public APIs;
- active canonical semantic specs;
- the accepted catalog navigation spec and implementation plan where preview
  scope is refined;
- `marivo-semantic` skill, references, and runnable examples;
- datasource and semantic help;
- latest English and Chinese site documentation;
- generated API documentation;
- public-surface, signature, help-drift, and example tests;
- project ignore rules for `.marivo/authoring` cache state.

Historical versioned site documentation and historical design records remain
unchanged. Superseded design documents may receive only a visible status link
to this design.

The repository-wide `agent-guide.md` authoring workflow text must be updated in
the implementation change because its current `help -> discover -> ...` route
would otherwise teach the removed top-level discovery model.

## Test and acceptance criteria

### Query-count invariants

Query-spy tests must prove:

| Operation | User-data queries |
|---|---:|
| `md.inspect(...)` | 0 |
| first `inspection.sample(...)` | 1 |
| every snapshot projection | 0 |
| matching fresh snapshot cache hit | 0 |
| `refresh=True` acquisition | 1 |
| `catalog.verify_object(obj)` | 0 |
| `catalog.preview(obj, using=snapshot)` | 1 |
| `catalog.readiness(...)` | 0 |

Metadata and system-catalog queries are counted separately from user-data
queries.

### Evidence regressions

Tests must prove:

- string and integer `YYYYMMDD` match the fixed date rule;
- `HH` matches an hour component rather than a standalone timestamp;
- date-plus-hour composition runs only for explicitly selected columns;
- uncommon date formats and epoch units are not inferred;
- `query_id` and URL-valued `self` retain the same sampled-uniqueness fact while
  exposing only transparent lexical/syntax observations;
- entity evidence emits no business-key recommendation;
- fifteen unique values with a ten-value retention/display cap are not marked
  complete;
- sample and scope exactness are separate;
- null, distinct, and value-frequency fields use explicit sample/scope names;
- display truncation never removes structured evidence;
- truncation and omitted counts appear before omitted detail.

### Scope and adapter tests

Each supported adapter must prove:

- validated partition predicate pushdown;
- blocking of unsupported transformed partitions;
- real timeout capability reporting;
- timeout setup, execution, and cleanup;
- `LIMIT max_rows + 1` truncation detection;
- schema-fingerprint invalidation;
- `unknown` rather than invented byte estimates;
- absence of `ORDER BY random()`;
- recording of the actual sampling method.

Known, absent, and unknown partition states have independent contract tests.

### Agent-surface tests

Tests execute examples instead of only checking for keywords:

- `md.help("authoring")` contains a runnable target workflow;
- every constructor help entry has a complete valid signature and example;
- inspection and scope-error next calls execute;
- projection help topics resolve;
- all public parameters are visible through Python signatures and help;
- concrete `CatalogObject` values pass directly into verification, preview,
  readiness, and analysis handoff;
- catalog lookup and navigation errors remain aligned with the accepted object
  navigation contract;
- readiness blockers produce executable scoped-preview calls;
- removed APIs are absent from exports, help, skills, latest docs, and generated
  API indexes.

### End-to-end acceptance

At least one runnable end-to-end test covers:

```text
inspect
  -> explicit scoped snapshot
  -> deterministic evidence
  -> authored semantic Python
  -> load concrete CatalogObject
  -> static verification
  -> scoped runtime preview
  -> readiness
  -> analysis handoff
```

Focused datasource, evidence, catalog handoff, help, and example tests run first.
Final implementation verification runs:

```bash
make test
make typecheck
make lint
```

The site content verification required by the repository also runs after latest
documentation and generated API updates.
