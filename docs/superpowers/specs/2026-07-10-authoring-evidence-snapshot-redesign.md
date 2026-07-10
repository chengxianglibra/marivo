# Authoring Evidence Snapshot Redesign

## Status

Accepted target design. Not yet implemented. The current runtime still exposes
the existing `inspect_*`, `discover_*`, and authoring validation paths. Do not
use the target API examples in this document as current runtime instructions.

Implementation requires a separate plan and must update code, skills, help,
canonical runtime specs, and latest site documentation together.

Date: 2026-07-10

This design supersedes these sections of
[`2026-07-08-semantic-authoring-public-guidance-design.md`](2026-07-08-semantic-authoring-public-guidance-design.md):
**Workflow Level**, **`md.help("authoring")`**, **`ms.help("authoring")`**,
**Semantic Constructor Workflow Lines**, **Table And Schema Discovery
Bypassing Marivo**, **Discovery-To-Authoring Handoff Gap**, **Batch Authoring
Before Verification**, and **Readiness Repair Gap**, together with their
workflow-specific acceptance and rollout items. It supersedes **Result
Next-Step Affordances** only for methods removed or changed here. The earlier
design's CLI routing, datasource/backend contract help, secret guidance,
`ai_context` help, and other unaffected sections remain in force.

This design follows the typed object graph, lookup rules, navigation matrix,
and object handoff contract implemented from
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
- Require an explicit scope for every user-data read on the canonical authoring
  path.
- Acquire one bounded sample and reuse it for all discovery projections.
- Make evidence coverage, exactness, cache identity, and truncation impossible
  to confuse with full-source truth.
- Recognize only documented deterministic formats and syntax rules; leave
  non-deterministic semantic interpretation to the agent.
- Keep semantic objects authored explicitly in Python, with Python and Git as
  the source of truth.
- Align post-authoring navigation and runtime handoff with the implemented catalog
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
- Injecting structured scope into arbitrary provenance SQL used by parity
  diagnostics.
- Redesigning analysis APIs after the readiness handoff.
- Redesigning the implemented catalog object graph.
- Backward compatibility for the removed discovery and source-declaration
  surfaces.

## Design principles

The redesign is governed by these principles:

1. **Inspect before reading.** Metadata and cost context are available before
   an agent can acquire user data.
2. **Explicit scope for every canonical read.** Snapshot acquisition and runtime
   preview have no default unpruned path. Explicit diagnostic escape hatches are
   outside the safe authoring state machine and disclose that boundary.
3. **One acquisition, many projections.** Discovery projections are pure views
   over one reusable snapshot.
4. **Evidence is scoped.** Every statistic names whether it describes the
   sample or an exhaustively read scope.
5. **Unknown is not none.** Unknown partition state never silently becomes an
   unpartitioned-source decision.
6. **Only enforceable guards are guarantees.** Unsupported timeout is a
   capability gap, and byte estimates remain evidence rather than pretend
   safeguards.
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
    scope=md.partition(
        {"log_date": "20260710", "log_hour": "13"},
        max_rows=1000,
        timeout_seconds=30,
    ),
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

CSV and JSON are not self-describing enough to satisfy metadata-only inspection
through backend inference. Their authoring descriptors therefore require a
typed physical schema:

```python
md.csv("orders.csv", schema={"order_id": "string", "amount": "decimal(18,2)"})
md.json("events.json", schema={"event_id": "string", "occurred_at": "timestamp"})
```

The mapping uses backend-independent Marivo type strings documented by the
datasource contract. A CSV or JSON descriptor without `schema=` is rejected
before opening the source. The redesign does not retain a separate untyped
`columns=` shortcut for these source kinds. Catalog tables use catalog schema;
Parquet uses footer schema.

`md.raw_sql(..., reason=...)` remains the bounded, read-only diagnostic escape
hatch defined by the datasource layer. It is outside the ordinary authoring
evidence path, does not create or enrich a snapshot, and is never suggested as
the primary schema or semantic discovery route. Its explicit SQL, reason, and
bounded-result contract remain visible in its own help topic.

`ms.parity_check(...)` is the equivalent semantic provenance diagnostic. Its
arbitrary source SQL cannot be safely partition-rewritten by this design, so it
is not part of the scoped authoring state machine, is never required for
readiness, and must disclose that it may execute full metric and provenance-SQL
scans. An unverified parity check may remain a readiness warning, never a
blocker. This design does not claim parity is bounded by a snapshot.

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

For CSV and JSON this guarantee depends on the required descriptor schema;
inspection validates and returns that declaration without invoking backend
schema inference. It never calls `read_csv()` or `read_json()` merely to learn
types. Missing or invalid typed schema raises a structured blocked error with
`query_executed=false`.

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

`next_safe_action` is one canonical call shape with placeholders derived from
the current partition state. It never chooses a partition value or claims that
only one business-relevant scope exists; bounded partition values remain
separate evidence for the agent to evaluate.

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

The scope argument is mandatory for `SourceInspection.sample()`.
`catalog.preview(...)` receives the same guard through its required `using=`
snapshot. Explicit diagnostics outside the canonical state machine follow the
separate boundary described under public ownership.

`max_rows` and `timeout_seconds` are required positive arguments; neither has a
silent default or `None` escape. If an adapter cannot enforce the requested
timeout, authoring acquisition is unavailable on that adapter even for a small
explicit partition. Inspection exposes this before the agent constructs the
scope.

### Partition scope rules

For `partition_state=known`:

- the scope must cover every partition field the adapter can express;
- every predicate must be validated and pushed down;
- an unsupported transformed partition blocks before query execution;
- the query records the actual pushed predicate.

`md.partition(...)` accepts the physical partition values exposed by
`inspection.partitions()`. It does not translate a logical date or timestamp
into an engine-specific partition transform. If metadata describes a transform
that the structured predicate path cannot express, acquisition blocks instead
of guessing the physical value.

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
fails before reading data. `byte_estimate_supported` tells the agent whether a
backend estimate is available; it is evidence, not an execution control. This
design adds no cross-backend `max_bytes` parameter or `byte_limit_enforced`
promise. Engine- or account-level quotas remain datasource configuration until
a separate design can prove a portable authoring contract.

## Snapshot acquisition

`SourceInspection.sample(scope=..., columns=..., persist_values=False,
refresh=False)` is the only ordinary authoring acquisition entry point.

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
- per-column type, count, null, length, and deterministic-rule profiles;
- evidence format version;
- creation, expiry, and cache status.

Value-bearing evidence such as min/max values, frequency keys, and display
samples remains available in the live snapshot but is memory-only by default.
An agent may explicitly opt into project-local plaintext persistence:

```python
inspection.sample(
    scope=scope,
    columns=columns,
    persist_values=True,
)
```

With `persist_values=False`, a later process can reuse types, counts, nulls,
lengths, rule matches, and coverage but value-bearing fields report
`value_evidence_unavailable`; they do not query automatically.
`persist_values=True` persists only bounded min/max facts, the bounded
frequency dictionary, and bounded display samples, never the complete acquired
rows.

It does not persist:

- full raw result rows;
- business decisions;
- grill answers;
- selected semantic parameters;
- generated semantic definitions.

`.marivo/authoring` is a plaintext local cache, not a security boundary.
Directories use owner-only permissions and files use owner read/write
permissions. Resolved credentials are never written. Help must disclose that
`persist_values=True` may store PII or other business values and show how to
remove or refresh the cache.

The immutable public identity needed for teaching errors and explicit
reacquisition is available as `snapshot.datasource`, `snapshot.source`,
`snapshot.scope`, and `snapshot.columns`. These properties expose authored
refs/descriptors and guard values, never resolved secrets or a live connection.

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

V1 persisted evidence is column-local. A projection may accept several column
names for convenience, but it evaluates each column independently. It does not
claim row-aligned composition, tuple uniqueness, multi-column overlap, or other
statistics that cannot be reconstructed from per-column sufficient statistics.
Unsupported cross-column evidence is reported as `unavailable`, never estimated
and never followed by an implicit query.

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

The initial closed time-rule list is:

```text
type.native_date
type.native_timestamp
date.iso
datetime.iso
date.yyyymmdd
time.hour_00_23
```

The implementation adds this same closed list to the canonical
[`datasource-layer.md`](../../specs/semantic/datasource-layer.md) discovery
contract and pins it with tests. Semantic authoring parse formats continue to
follow
[`2026-06-08-semantic-time-field-format-design.md`](2026-06-08-semantic-time-field-format-design.md).
Adding a new automatic rule requires a design change; adapters cannot add
private heuristics.

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
scope_exhaustion
sampling_method
```

All locally computed sample statistics are exact about the retained sample, so
there is no separate `sample_exactness` field. They are not exact about the
scope or source unless acquisition proved that the scope was exhausted.
`scope_distinct_count` exists only for an exhaustively read scope; otherwise
evidence exposes a lower bound.

The only ordinary proof of scope exhaustion is receiving no extra row from the
`LIMIT max_rows + 1` acquisition. Exactness always applies to the explicit
scope, never automatically to the full source.

## Time evidence

Automatic time recognition is deliberately narrow. It covers only documented
fixed rules:

- native date and timestamp physical types;
- strict ISO date and datetime strings;
- strict `YYYYMMDD` strings and integers;
- strict `00..23` hour components.

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
```

An hour component is not reported as an independently parseable timestamp.
Passing multiple columns returns independent column evidence; it does not
compute row-aligned composition evidence. Choosing and validating a composite
event time remains an agent decision outside the v1 cached projection model.

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

Discovery does not search or validate composite keys. Passing multiple columns
returns independent observations for each column:

```python
snapshot.entity(columns=("order_id", "line_number"))
```

The result does not claim tuple uniqueness because the persisted snapshot does
not retain the row alignment required to prove it.

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
visible in the result. A reloaded snapshot created with
`persist_values=False` reports `value_evidence_unavailable` instead of an empty
value set. `snapshot.values()` never issues a follow-up query.

## Relationship evidence

Relationship discovery is fully explicit:

```python
orders_snapshot.relationships(
    customers_snapshot,
    left=("customer_id",),
    right=("customer_id",),
)
```

Marivo does not search for join columns. V1 accepts exactly one column on each
side and reports only column-local observations available from the two
snapshots: physical type compatibility, nulls, sample uniqueness, retained-value
overlap, and retained-value orphan observations. Multi-column join keys return
`unavailable` without a query; Marivo does not add row-level persistence or a
second acquisition protocol to support them.

When retained evidence cannot support an overlap calculation, the result says
`unavailable`; it does not rescan either source. The result displays both
physical scopes and always records `scope_comparability` as unresolved for
different sources. It does not infer that differently encoded partitions
represent the same business window. Business scope alignment, cardinality, and
relationship constraints remain agent-owned.

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
- value-persistence policy;
- evidence format version.

Each entry records `created_at`, `expires_at`, and cache status. A snapshot is
invalidated by datasource, source, scope, selected-column, schema, evidence
version, or TTL changes. Explicit reacquisition uses the public signature:

```python
inspection.sample(scope=scope, columns=columns, refresh=True)
```

TTL is an evidence-freshness policy, not a scan-safety control. A persisted
preview check cannot outlive any snapshot it depends on. Readiness never
refreshes or re-previews automatically; when evidence has expired, it reports
the required reacquisition and preview calls once, avoiding a hidden retry or
query loop.

Cache use is never hidden. Every result identifies the snapshot and reports
whether the evidence is fresh, cached, stale, or mismatched.

## Semantic authoring surface

The authoring surface remains helper-first and explicit.

Entity source ownership follows the same datasource descriptor used during
inspection:

```python
orders = ms.entity(
    name="orders",
    datasource=md.ref("datasource.warehouse"),
    source=md.table("orders"),
    primary_key=["order_id"],
    ai_context=ms.ai_context("One row per business order."),
)
```

There is no `ms.table(...)` equivalent.

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

This design follows the implemented catalog object navigation contract:

- concrete `CatalogObject` subclasses are the primary loaded values;
- global and scoped `CatalogCollection` properties are the browse path;
- typed IDs are directly reusable with `catalog.get(...)`;
- `details()` exposes rich facts without becoming a second browse API;
- the old `SemanticObject`, `SemanticObjectList`, and `catalog.list(...)` model
  are not restored.

Post-authoring validation uses the existing catalog method names rather than a
new parallel `validate/check` vocabulary.

### Changed contracts on retained methods

This redesign intentionally changes behavior on retained method names:

- `catalog.verify_object(...)` and `ms.verify_object(...)` lose
  `scope=` and entity connectivity preview; verification becomes static and
  performs zero user-data queries for every object kind;
- `catalog.preview(...)` gains a hard-required `using=` parameter and has no
  unscoped authoring execution path;
- `catalog.readiness(...)` and `ms.readiness(...)` remain
  connection-free but now require fresh persisted preview evidence for directly
  handed executable objects;
- `ms.parity_check(...)` remains an explicit potentially unbounded provenance
  diagnostic outside the safe authoring path; it is never required for
  readiness;
- `ms.richness(...)` remains advisory and is not a readiness blocker.

These changes belong in the breaking migration even though the three catalog
method names remain.

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

`using` is a hard-required public parameter because preview reads user data. It
has no default and cannot be omitted. It binds preview to each snapshot's
datasource, source, explicit scope, and schema fingerprint. A mismatch blocks
before execution.

Single-entity objects use one snapshot. Multi-entity metrics and relationships
use an entity-keyed mapping rather than a positional tuple:

```python
catalog.preview(
    customer_revenue,
    using={
        orders_entity: orders_snapshot,
        customers_entity: customers_snapshot,
    },
).show()
```

The public input type is conceptually:

```text
DiscoverySnapshot
| Mapping[Entity | SemanticRef, DiscoverySnapshot]
```

Mapping keys must cover exactly the dependency entities required by the
previewed object; every `SemanticRef` key must have entity kind. Bare entity
names, datasource names, positional tuples, missing entries, and unrelated
extra entries are rejected before execution. Preview then compiles and executes
the smallest bounded query needed for an object-level smoke check and returns
limited rows, actual scopes, backend, warnings, and runtime status.

Preview proves that the current definition executes for the current backend
and explicit scope. It does not prove every future analysis query shape.

Preview check evidence may be persisted under:

```text
.marivo/authoring/checks/<check-id>.json
```

Their identity includes semantic and dependency fingerprints, snapshot IDs,
backend, and check format version.

The check cache stores status, fingerprints, scopes, coverage, types, and
warnings only. Preview result rows are returned to the current process but are
never persisted.

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

Inspection, evidence, verification, preview, and readiness share a rendering
floor, not one optional-field result schema:

```text
bounded one-line repr with concrete result type
deterministic render/show ordering
warnings or issues when present
complete structured properties
available methods/properties
state-derived next calls
```

Each closed result family keeps its own meaningful state and fields:

- inspection and discovery evidence use `complete | incomplete` and expose a
  coverage block only when rows were observed;
- static verification uses `passed | failed` plus `validation_level=static`;
- runtime preview uses `passed` plus a data-coverage block; pre-execution
  rejection is a structured error rather than a preview result full of nulls;
- readiness uses `ready | ready_with_warnings | blocked`;
- structured errors use `status=blocked` and `query_executed=false` when no
  query ran.

The data-coverage block contains `scope`, `rows_observed`,
`scope_exhaustion`, `scope_exactness`, and cache provenance as applicable.
Static verification and aggregate readiness do not expose meaningless null
scope or row fields.

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
      }, max_rows=1000, timeout_seconds=30),
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

For a projection column that was not acquired:

```text
code: snapshot_column_missing
query_executed: false
requested: [amount]
available: [query_id, region, log_date]

next_calls:
  md.inspect(snapshot.datasource, snapshot.source).sample(
      scope=snapshot.scope,
      columns=(*snapshot.columns, "amount"),
  )
```

The projection never broadens the snapshot or issues a query implicitly. The
canonical reacquisition call is built from public snapshot properties and the
actual missing columns.

Blocked operations always state `query_executed=false`. Cache staleness, schema
mismatch, unsupported timeout, unsupported partition transform, and snapshot
source mismatch have equivalent structured teaching errors.

## Cost safety contract

Marivo guarantees:

- cost and partition evidence is exposed before acquisition;
- every user-data read in the canonical snapshot/preview authoring path has an
  explicit scope;
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

`md.raw_sql(...)` and `ms.parity_check(...)` are explicitly outside these
canonical-path guarantees. Their help and results must identify them as
diagnostics that can execute expensive SQL; neither is a readiness requirement.

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
- `md.preview(...)` as an unscoped top-level user-data read
- top-level public sample/discover shortcuts that bypass inspection
- the old public `ScanScope` construction path
- authoring guidance that depends on hidden unbounded `.show()` output

`PreviewResult` remains the shared bounded result type used by scoped
`catalog.preview(...)`; removing `md.preview(...)` does not require a duplicate
snapshot-preview result class or a type rename.

Retained file-source constructors also change contract: `md.csv(...)` and
`md.json(...)` require typed `schema=`, and the untyped CSV `columns=` shortcut
is removed from the authoring surface. This is intentional so `md.inspect(...)`
can keep its zero-data-query guarantee.

Retained catalog methods also have the explicit breaking behavior changes
listed under **Changed contracts on retained methods**. Migration tooling and
docs must not treat name retention as signature compatibility.

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
- the implemented catalog navigation spec and runtime handoff guidance where
  preview scope is refined;
- `marivo-semantic` skill, references, and runnable examples;
- datasource and semantic help;
- latest English and Chinese site documentation;
- generated API documentation;
- public-surface, signature, help-drift, and example tests;
- project ignore rules for `.marivo/authoring` cache state.

Historical versioned site documentation remains unchanged. Existing design
records receive only supersession or contract-boundary notes that point here;
their historical decisions are not rewritten.

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
| single-entity `catalog.preview(obj, using=snapshot)` | 1 |
| `catalog.readiness(...)` | 0 |

Metadata and system-catalog queries are counted separately from user-data
queries. Multi-entity preview tests assert the explicit backend execution plan
and prove there are no extra inspection or discovery scans; they do not pretend
that every federation strategy is one physical query.

`md.inspect(...)` remains at zero only because CSV and JSON require typed schema
and are rejected before backend inference when it is absent.

### Evidence regressions

Tests must prove:

- string and integer `YYYYMMDD` match the fixed date rule;
- `HH` matches an hour component rather than a standalone timestamp;
- requesting several columns returns independent evidence and never claims
  row-aligned composition or tuple uniqueness;
- uncommon date formats and epoch units are not inferred;
- `query_id` and URL-valued `self` retain the same sampled-uniqueness fact while
  exposing only transparent lexical/syntax observations;
- entity evidence emits no business-key recommendation;
- fifteen unique values with a ten-value retention/display cap are not marked
  complete;
- sample and scope exactness are separate;
- null, distinct, and value-frequency fields use explicit sample/scope names;
- a projection requesting an unacquired column blocks with zero queries and an
  executable reacquisition call;
- multi-column relationship evidence reports `unavailable` with zero queries;
- cross-source relationship evidence reports both scopes and leaves
  `scope_comparability` unresolved;
- display truncation never removes structured evidence;
- truncation and omitted counts appear before omitted detail.

### Scope and adapter tests

Each supported adapter must prove:

- validated partition predicate pushdown;
- blocking of unsupported transformed partitions;
- real timeout capability reporting;
- omission or disabling of required timeout fails before execution;
- adapters without enforceable timeout cannot acquire authoring snapshots;
- timeout setup, execution, and cleanup;
- `LIMIT max_rows + 1` truncation detection;
- schema-fingerprint invalidation;
- `unknown` rather than invented byte estimates;
- no public `max_bytes` or byte-limit capability is advertised by this design;
- absence of `ORDER BY random()`;
- recording of the actual sampling method.

CSV/JSON contract tests also prove that typed schema inspection performs no
backend inference and missing schema is rejected before opening the source.

Known, absent, and unknown partition states have independent contract tests.

### Agent-surface tests

Tests execute examples instead of only checking for keywords:

- `md.help("authoring")` contains a runnable target workflow;
- every constructor help entry has a complete valid signature and example;
- inspection and scope-error next calls execute;
- projection help topics resolve;
- all public parameters are visible through Python signatures and help;
- result families retain their own closed status enums and static results do not
  expose null data-coverage fields;
- `catalog.preview(...)` rejects an omitted `using`, a positional snapshot
  tuple, missing entity bindings, and unrelated extra bindings;
- concrete `CatalogObject` values pass directly into verification, preview,
  readiness, and analysis handoff;
- entity verification performs no runtime query, while missing fresh preview
  evidence blocks readiness;
- parity help identifies the diagnostic as potentially unbounded, the canonical
  authoring workflow never calls it, and readiness never requires it;
- richness retains its documented advisory behavior;
- catalog lookup and navigation errors remain aligned with the accepted object
  navigation contract;
- readiness blockers produce executable scoped-preview calls;
- removed APIs are absent from exports, help, skills, latest docs, and generated
  API indexes, including the top-level `md.preview(...)` entry point.

Cache tests prove that value persistence defaults off, preview rows are never
persisted, resolved credentials never appear in cache files, owner-only cache
permissions are applied, and `persist_values=True` is included in snapshot
identity and help.

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
