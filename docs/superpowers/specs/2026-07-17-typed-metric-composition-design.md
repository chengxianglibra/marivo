# Marivo Typed Metric Composition Design

Status: proposed

Date: 2026-07-17

Last revised: 2026-07-19

Revision policy: destructive cutover to the best final public and internal
contract. Current signatures, aliases, planner types, payloads, and persisted
state have no compatibility weight; there is no migration path.

Design priority:

- choose the smallest coherent end-state API and graph-native implementation,
  even when that requires deleting or narrowing a current public shape;
- retain a current behavior only when it is independently selected as the best
  final contract, never merely to avoid breakage;
- add no compatibility alias, deprecation shim, flat-plan adapter, dual
  reader/writer, lazy rewrite, or old-payload interpretation;
- update implementation, types, help, docs, skills, tests, and persisted schema
  atomically as one breaking cutover.

Selected decisions:

- runtime typed metrics are closed, immutable, recursively composable expression
  values;
- `session.observe(...)` remains the only public materialization entry and
  accepts only exact `MetricRef | RuntimeMetricExpr` roots with deliberate
  singleton/list/tuple ergonomics;
- all analysis-layer semantic-object inputs use exact ref subclasses: metric
  roots use `MetricRef`, while observation dimensions, attribution/decomposition
  axes, discovery search spaces, and transform slice/drop axes use
  `AnalysisDimensionRef`;
- missing aligned keys remain present with null values (E0);
- one expression remains within one semantic model and one datasource
  compatibility domain (S0);
- legal nested derived catalog metrics become analysis-authorized semantic
  objects under operator-specific rules; the blanket nesting ban is removed;
- catalog/runtime graphs share a maximum root-to-leaf depth of 10 and a
  pre-CSE occurrence budget of 256;
- leaf acquisition and aggregation execute through backend Ibis/SQL lowering,
  while recursive composition defaults to registered Marivo evaluators;
- branch-local slice wrappers are canonically pushed to reachable leaves, so
  distribution-equivalent trees share one value identity while labels remain a
  separate occurrence-mapped presentation overlay;
- the bounded unit algebra governs automatic derivation for both catalog and
  runtime nodes without narrowing catalog `unit=` authoring or overrides;
- downstream admission is state-driven and origin-blind (Phase 4 A).

Related issue: [#28](https://git.bilibili.co/ace-lab/marivo/-/issues/28)

## Summary

Add runtime typed metric expressions as a first-class input to
`session.observe(...)`.

The public API separates pure expression construction from session-bound
planning and execution:

In the examples, `request_count` is a catalog `MeasureRef`, `state` is a catalog
`DimensionRef`, `cost_metric`, `requests`, and `baseline_failure_rate` are
catalog `MetricRef` values, and `window` is a `TimeScopeInput`.

```python
failed = mv.runtime_metric.aggregate(
    measure=request_count,
    agg="sum",
    slice_by={state: "FAILED"},
    label="failed_requests",
)
total = mv.runtime_metric.aggregate(
    measure=request_count,
    agg="sum",
    label="all_requests",
)
failure_rate = mv.runtime_metric.ratio(
    numerator=failed,
    denominator=total,
    zero_division="null",
    label="failure_rate",
)

frame = session.observe(
    failure_rate,
    time_scope=window,
    grain="hour",
    analysis_purpose="Relate query failures to surrounding system behavior.",
)
```

`mv.runtime_metric.*` constructs only immutable typed descriptors. It does not
resolve a model, plan queries, access data, create a session artifact, or invent
catalog authority. `session.observe(...)` resolves the complete expression,
validates it against one semantic model and datasource domain, plans it as a
recursive expression DAG, executes it, and returns an ordinary canonical
`MetricFrame`.

Runtime expressions may recursively contain other runtime expressions or
catalog `MetricRef` operands. Recursion is not itself unsafe. Safety comes from
the closed node union, typed operands, operator-specific admission rules,
bounded graph size, canonical serialization, and the absence of arbitrary code,
SQL, Ibis, pandas, callbacks, literals, or caller-authored semantic metadata.

The implementation retains only final-contract services such as leaf resolution,
entity/path planning, bucketing, registered aggregate/ratio evaluation, coverage,
and artifact persistence. It does **not** wrap or preserve the current flat
derived-metric planner. The current one-level
`DerivedObservePlan` and `nested-derived-unsupported` boundary are replaced by
recursive graph planning and bottom-up evaluation.

Runtime typed metrics are not dynamic semantic objects. They receive no
`MetricRef`, catalog entry, readiness status, owner, domain, project-wide
definition, or durable semantic authority. They are canonical analysis
expressions with session-scoped authority once observed.

## Decision

Introduce one expression namespace and replace the observation input contract:

```text
mv.runtime_metric.aggregate(MeasureRef, ...) -> RuntimeAggregateExpr
mv.runtime_metric.weighted_mean(MeasureRef, MeasureRef, ...) -> RuntimeWeightedMeanExpr
mv.runtime_metric.slice(MetricExprInput, ...) -> RuntimeSliceExpr
mv.runtime_metric.ratio(MetricExprInput, MetricExprInput, ...) -> RuntimeRatioExpr

ObserveMetricInput = MetricRef | RuntimeMetricExpr
session.observe(ObserveMetricInput | non-empty sequence[ObserveMetricInput], scope...)
    -> MetricFrame
```

The public v1 expression algebra is recursive but closed:

```text
MetricExprInput = MetricRef | RuntimeMetricExpr

ObserveMetricInput = MetricRef | RuntimeMetricExpr

AnalysisDimensionRef = DimensionRef | TimeDimensionRef

RuntimeMetricExpr =
    RuntimeAggregateExpr
  | RuntimeWeightedMeanExpr
  | RuntimeSliceExpr
  | RuntimeRatioExpr
```

`RuntimeSliceExpr` is included because branch-local typed filtering is required
for conditional rates and cohort ratios. It is definition-changing and distinct
from the outer `observe(slice_by=...)`, which applies one global observation
scope to the complete expression.

There is no parallel `session.compose.*` execution surface. A second entry would
duplicate scope parameters, split planner ownership, make nested expressions
awkward, and teach agents two ways to obtain the same artifact. Expression
construction stays session-free; all materialization stays under `observe`.

The final surface accepts the exact `MetricRef` returned by semantic authoring or
typed catalog `.refs()` navigation. It rejects loaded catalog objects, generic
`CatalogObject`, generic `SemanticRef`, and every wrong-kind semantic value at
the type boundary rather than accepting a convenience mega-union and repairing
it later. Runtime constructors use the same exact refs for every operand that
overlaps semantic authoring.

The cutover also makes catalog typing truthful rather than requiring casts:

```text
CatalogObject[RefT]
CatalogCollection[ObjectT, RefT]

Metric.ref                                -> MetricRef
CatalogCollection[Metric, MetricRef].refs -> tuple[MetricRef, ...]
Measure.ref                               -> MeasureRef
Dimension.ref                             -> DimensionRef
TimeDimension.ref                         -> TimeDimensionRef
```

The corresponding typed collections preserve those exact return types. This is
an annotation/model cleanup over the already kind-specific runtime refs, not a
fallback accepting generic `SemanticRef` at analysis boundaries.

This exact-ref policy is analysis-wide, not observe-specific. The breaking
cutover removes the broad analysis-layer `SemanticInput`, `MetricInput`, and
`DimensionInput` aliases. Public observe, attribute, discover, decompose, and
frame-transform parameters use `MetricRef`, `RuntimeMetricExpr`, or
`AnalysisDimensionRef` as appropriate. Internal replay reconstructs the same
exact ref subclasses from persisted typed identities; it does not reintroduce a
bare-id or generic-ref entrance. Loaded catalog objects remain navigation/read
results whose `.ref` values cross into analysis.

A non-empty list or tuple may contain catalog metric inputs, runtime metric
expressions, or both. The sequence is normalized to an ordered expression forest
with one shared outer observation scope. Cross-root common subexpressions may be
reused, but every root retains its own identity and output column. All roots must
satisfy the final multi-root shape contract plus the graph-wide S0 model/source
rule. A one-element sequence deliberately normalizes to the arity-one contract;
this is chosen final ergonomics, not a compatibility branch.

This design also changes semantic authorization for catalog metrics: a nested
derived catalog metric is legal and analysis-ready when every node in its
dependency graph satisfies its operator-specific semantic, unit, fold, source,
and planning rules. `nested_derived_unsupported` is removed as a blanket
validator/readiness/observe policy. This is a semantic-layer contract change,
not merely an analysis implementation detail.

Catalog readiness includes the graph budget, not only operator legality. For
each selected catalog metric root, readiness uses the same catalog lowerer,
slice canonicalization, and pre-CSE occurrence expansion as observation, then
requires depth at most 10 and occurrences at most 256. A root at depth 11 or 257
occurrences is excluded from `analysis_ready_refs` with a structured issue that
reports the observed count, limit, and responsible dependency path. This prevents
a catalog metric from becoming authorized while being intrinsically impossible
to observe. The total occurrence budget for a caller-supplied multi-root forest
is still checked by `observe`, because it is a property of that materialization
request rather than any individual catalog metric.

## Relationship to Existing Contracts

The current live surface does not implement this design. It accepts catalog
metrics at `session.observe(...)`, represents derived observation plans as a
flat layer over immediate components, and rejects nested derived metrics.

This design is a deliberate breaking refactor of that internal boundary. It
preserves the terminal raw-data boundary established by
`2026-07-14-terminal-raw-sql-boundary-design.md`: arbitrary SQL, Ibis, pandas,
callbacks, terminal results, and user-created frames still have no inbound path
to typed analysis.

The active operator contract becomes:

> `observe` is the sole public producer of an initial canonical `MetricFrame`.
> Its subject may be an analysis-ready catalog metric or a closed runtime typed
> metric expression. Both lower to one versioned internal metric expression
> graph and use the same recursive planner and evaluator contracts.

Active specs, live help, packaged skills, latest documentation, telemetry, and
drift tests move atomically with the breaking release. Historical documentation
remains historical unless release policy explicitly requires an update.

## Problem

### Exploratory metrics force the wrong lifecycle choice

An analysis frequently needs a temporary aggregate or composed metric that is
meaningful for one investigation but is not yet an approved project metric:

- sum, mean, percentile, or distinct count over a governed measure;
- the same measure or metric under two typed branch-local slices;
- failed requests divided by all requests;
- conversions divided by attempts;
- resource use divided by capacity;
- a ratio of ratios or another nested combination whose children retain exact
  typed semantics.

Today the caller must either pollute the persistent semantic model with
investigation-specific metrics or export to pandas and leave Marivo's typed
analysis, lineage, evidence, quality, replay, and downstream operators.

The governed objects and registered evaluators already contain enough
information for a safer middle path. What is missing is a controlled runtime
expression value and a recursive planner, not a generic formula language.

### A frame-based composition API is the wrong abstraction

Making ratio construction consume previously materialized frames creates
several avoidable problems:

- every branch must be executed before the whole expression is known;
- shared subexpressions cannot be planned or deduplicated as one graph;
- scope and alignment are split across multiple calls;
- recursive expressions become a chain of session artifacts rather than a
  typed definition;
- planner optimizations and replay must reverse-engineer an expression from
  frames;
- agents need to decide between `observe` and a parallel composition API.

A pure recursive expression followed by one `observe` call gives the planner
the complete intent and keeps one artifact-production boundary.

### Arbitrary derive remains unsafe

A generic callback, string formula, SQL expression, Ibis expression, pandas
operation, or overloaded frame arithmetic cannot guarantee:

- governed leaf provenance;
- valid entity and join paths;
- exact unit and additivity derivation;
- missing-key and zero-division behavior;
- recursive component lineage and replay;
- stable canonical identity;
- evidence subject integrity;
- safe downstream admission.

This design therefore expands recursive structure without expanding node
authority. Unknown nodes and untyped operands fail closed.

### Origin is not a capability

Once a runtime expression has been observed, its frame may have the same proven
shape, units, components, coverage, replay, and comparable semantics as an
equivalent catalog metric. Downstream operators must inspect those facts, not
the origin label. Catalog authority and computational capability remain
separate properties.

## Goals

1. Let agents construct useful runtime aggregates, conditional branches, ratios,
   and nested ratios without using raw-data escape hatches.
2. Keep runtime construction inside a small typed algebra over governed semantic
   references.
3. Use `observe` as the one execution, scope, planner, cache, persistence, and
   artifact boundary.
4. Align shared runtime parameters with semantic authoring parameter types and
   normalizers wherever their meanings match.
5. Lower catalog and runtime metrics into one recursive internal graph.
6. Derive identity, units, additivity, coverage, quality, components, replay,
   and comparable semantics bottom-up.
7. Support common-subexpression elimination and reuse of compatible persisted
   leaf materializations.
8. Preserve the terminal raw-data boundary and catalog authority boundary.
9. Deliver state-driven, origin-blind downstream parity when persisted facts
   satisfy an operator's ordinary preconditions.

## Non-goals

This design does not add:

- dynamic catalog objects or runtime `MetricRef` creation;
- caller-authored names, units, domains, owners, descriptions, or `ai_context`;
- arbitrary arithmetic, literals, formulas, callbacks, SQL, Ibis, or pandas;
- automatic promotion of runtime expressions into the semantic model;
- cross-model or cross-datasource composition;
- implicit missing-key zero fill;
- compatibility decoding or migration for old persisted sessions;
- automatic admission of every future operator or every child-node combination.

## Vocabulary

### Catalog metric

A persistent semantic metric addressed by `MetricRef`, validated by the semantic
layer, and carrying project-wide authority.

### Runtime metric expression

An immutable, session-free value built from the closed `mv.runtime_metric`
constructors. It has computational intent but no artifact identity or catalog
authority.

### Metric expression input

A `MetricRef` or another `RuntimeMetricExpr` accepted as a child of a runtime
operator or as the subject of `session.observe(...)`.

### Expression graph

The canonical versioned internal DAG produced after resolving and lowering the
public expression tree. Equivalent subtrees share node identities.

### Branch-local slice

A typed slice that changes one expression branch before aggregation. It is part
of expression identity and is replayed with that branch.

### Observation scope

The outer `observe` parameters plus the session report timezone: time scope,
grain, dimensions, global slice, explicit time dimension, expected shape, and
related materialization settings. They apply to the complete expression or
expression forest and contribute to artifact identity.

### Semantic authority

The right to act as a persistent project business definition. Runtime
expressions never acquire it merely by being computable or canonical.

## Architecture Overview

```text
MetricRef ---------------------------+
                                      |
MeasureRef -> runtime aggregate ------+--> normalize/resolve/lower
MetricExprInput -> runtime slice ------+             |
MetricExprInput x 2 -> runtime ratio --+             v
                                             MetricExpressionGraphV1
                                                       |
                                      validate -> recursively plan -> execute
                                                       |
                                                       v
                                                canonical MetricFrame
```

Catalog metrics and runtime expressions enter through the same observation
method but keep distinct root identities. An arity-one frame has one root; a
multi-root observation has an ordered root collection:

```text
Catalog MetricRef root       -> CatalogMetricIdentity
RuntimeMetricExpr root       -> RuntimeExpressionIdentity(expression_fingerprint)
multi-metric forest          -> ordered tuple[MetricIdentity, ...]
```

Both routes lower into the same internal graph and share all evaluator and
artifact contracts after root identity is established.

## Public Surface

### Namespace

The constructors are exposed from a stateless namespace:

```python
mv.runtime_metric.aggregate(...)
mv.runtime_metric.slice(...)
mv.runtime_metric.ratio(...)
```

The returned descriptors are frozen values. Construction performs local type
normalization and cheap structural checks only. Model-dependent validation is
deferred to `session.observe(...)`. Labels are presentation annotations, not
node identity; their storage is defined separately below so DAG interning never
chooses one label nondeterministically.

### Aggregate

```python
mv.runtime_metric.aggregate(
    measure: MeasureRef,
    *,
    agg: AggKind,
    fold: AggregateFoldInput = None,
    slice_by: Mapping[AnalysisDimensionRef, SliceValue] | None = None,
    label: str | None = None,
) -> RuntimeAggregateExpr
```

The shared fields align with authoring and observation contracts:

| Parameter | Type/normalizer source | Runtime meaning |
|---|---|---|
| `measure` | exact `ms.aggregate` `MeasureRef` input | governed value leaf |
| `agg` | exact authoring `AggKind` | registered aggregation |
| `fold` | exact authoring `AggregateFoldInput` | re-aggregation contract |
| `slice_by` | exact typed observation slice key/value normalization | branch-local pre-aggregation slice |
| `label` | analysis display label | excluded from value identity and authority |

`name`, `unit`, `domain`, owner, description, `ai_context`, and raw authoring
`filter` are intentionally absent. Unit, entity, source, and value semantics are
derived from the measure and registered aggregate contract. Branch predicates
use typed semantic dimensions and typed slice values rather than raw-column
authoring expressions.

The fold value set becomes one shared public contract across
`ms.aggregate(...)`, `ms.semi_additive(...)`, and
`mv.runtime_metric.aggregate(...)`:

```text
AggregateFoldValue =
    Literal["mean", "min", "max", "first", "last"]
  | tuple[Literal["percentile"], float]

AggregateFoldInput = AggregateFoldValue
  | None
```

The canonical default is `None`, meaning “use the governed measure's declared
semi-additive fold when one exists; otherwise apply no time-fold override.” The
breaking release narrows the current broad `str` annotation on
`ms.aggregate(fold=...)` and `mv.runtime_metric.aggregate(fold=...)` to
`AggregateFoldInput`. It also narrows the required
`ms.semi_additive(fold=...)` parameter to the non-null `AggregateFoldValue`.
All three surfaces call the same normalizer and percentile-range validation;
values outside the closed aliases fail validation.

### Weighted mean

```python
mv.runtime_metric.weighted_mean(
    value: MeasureRef,
    weight: MeasureRef,
    *,
    slice_by: Mapping[AnalysisDimensionRef, SliceValue] | None = None,
    label: str | None = None,
) -> RuntimeWeightedMeanExpr
```

Both refs must resolve to the same entity, datasource, and physical row grain;
`weight` must be additive while `value` may be non-additive. The node lowers to
the same `WeightedMeanAggregate` contract as catalog `ms.weighted_mean`:
paired non-null rows produce `SUM(value * weight)` and `SUM(weight)`, and a zero
paired weight sum produces null. The result inherits the value measure's unit.

### Slice

```python
mv.runtime_metric.slice(
    metric: MetricExprInput,
    *,
    by: Mapping[AnalysisDimensionRef, SliceValue],
    label: str | None = None,
) -> RuntimeSliceExpr
```

The slice is definition-changing. When applied above a composed child it is
logically pushed to every reachable leaf during planning. Planning fails if the
dimension cannot be resolved consistently for every affected branch.

`aggregate(..., slice_by=...)` is syntactic convenience normalized to the same
slice semantics as `slice(aggregate(...), by=...)`. Canonical fingerprints must
therefore be equal for the two equivalent forms.

This pushdown is value canonicalization, not merely a physical-planner rewrite.
Before fingerprinting, `slice(E, X)` distributes through every admitted composed
node until `X` is attached to the affected leaves; nested slice maps are
normalized and conjoined with the same typed slice normalizer. Equal predicates
deduplicate, while incompatible predicates fail with a structured slice
conflict. Consequently:

```text
slice(ratio(a, b), X)
== ratio(slice(a, X), slice(b, X))
```

The two forms have the same canonical graph, expression fingerprint, CSE/cache
key, comparable value semantics, and evidence subject. The aggregate
`slice_by=` equivalence is a special case of this general rule.

Presentation extraction occurs before this value rewrite. A non-null label on
an eliminated slice wrapper labels the rewritten subtree root; when the wrapper
label is absent, the wrapped child's root label is retained. Synthetic leaf
slice occurrences created by pushdown receive no copied label. Existing labels
inside the wrapped subtree remain attached to their remapped descendant
occurrence paths. Thus value identity is maximally canonical without turning one
composite label into several misleading leaf labels.

### Ratio

```python
mv.runtime_metric.ratio(
    numerator: MetricExprInput,
    denominator: MetricExprInput,
    *,
    zero_division: Literal["null", "error"] = "null",
    label: str | None = None,
) -> RuntimeRatioExpr
```

`numerator` and `denominator` use the authoring ratio role names. Their types are
wider than catalog authoring because runtime expressions may be recursive.
`zero_division` uses the same registered ratio evaluation policy used by catalog
ratio observation.

### Observe

The breaking cutover replaces the broad analysis input aliases with exact
kind-specific unions:

```text
ObserveMetricInput = MetricRef | RuntimeMetricExpr
AnalysisDimensionRef = DimensionRef | TimeDimensionRef
```

Loaded catalog objects and the generic `CatalogObject | SemanticRef` alias are
removed from these public boundaries. Catalog navigation exposes exact refs for
handoff; authoring already returns them.

```python
session.observe(
    metric: ObserveMetricInput
        | list[ObserveMetricInput]
        | tuple[ObserveMetricInput, ...],
    *,
    time_scope: TimeScopeInput = None,
    grain: GrainInput = None,
    dimensions: list[AnalysisDimensionRef] | None = None,
    slice_by: Mapping[AnalysisDimensionRef, SliceValue] | None = None,
    time_dimension: TimeDimensionRef | None = None,
    expect_shape: SemanticShape | None = None,
    analysis_purpose: str | None = None,
) -> MetricFrame
```

This is the final replacement signature. Singleton/list/tuple observation,
`time_dimension`, and `expect_shape` remain because they form one useful
materialization capability, not because the old signature must be accepted.
Generic semantic values and wrong-kind refs fail immediately. Report timezone is
deliberately session-owned through `session.report_tz_name`; there is no per-call
`timezone=` parameter.

For a multi-root forest, `expect_shape` applies to the resulting shared frame and
`time_dimension` must be valid for every root that needs it. Mixed catalog/runtime
roots are legal only when they can share the requested axes, grain, time
dimension, semantic model, and datasource compatibility domain. Otherwise
planning returns a structured graph-planning error with the responsible root and
node path.

Outer `slice_by` applies globally to every reachable leaf. It is an observation-
scope field but also changes scoped value semantics. A runtime slice applies
only to its subtree and belongs to definition-level expression semantics. The
two may coexist; their normalized conjunction is planned per leaf while their
identities remain distinguishable.

The definition-only `expression_fingerprint` includes branch-local slices but
excludes outer scope. `ScopedValueSemanticsV1` combines the expression
fingerprint with normalized outer global slice and other value-affecting scope
facts. Artifact identity includes the full materialization scope. This split
allows different windows of the same population to share expression identity
without treating different populations as comparable.

### Examples

Catalog metric plus runtime aggregate:

```python
cost_per_request = mv.runtime_metric.ratio(
    numerator=cost_metric,
    denominator=mv.runtime_metric.aggregate(
        measure=request_count,
        agg="sum",
    ),
    label="cost_per_request",
)
frame = session.observe(cost_per_request, time_scope=window, grain="day")
```

Nested ratio:

```python
relative_failure_rate = mv.runtime_metric.ratio(
    numerator=mv.runtime_metric.ratio(
        numerator=mv.runtime_metric.slice(requests, by={state: "FAILED"}),
        denominator=requests,
    ),
    denominator=baseline_failure_rate,
    label="relative_failure_rate",
)
frame = session.observe(relative_failure_rate, time_scope=window, grain="hour")
```

Nested structure is accepted only if each node passes its own typed child,
unit, scope, source, replay, and evaluation rules.

## Runtime Expression Algebra

### Public node union

The public descriptors are a discriminated, frozen union:

```text
RuntimeAggregateExprV1 {
  kind: "aggregate"
  measure: MeasureRef
  agg: AggKind
  fold: AggregateFoldInput
  slice_by: TypedSliceMap
  label?: DisplayLabel
}

RuntimeWeightedMeanExprV1 {
  kind: "weighted_mean"
  value: MeasureRef
  weight: MeasureRef
  slice_by: TypedSliceMap
  label?: DisplayLabel
}

RuntimeSliceExprV1 {
  kind: "slice"
  metric: MetricExprInput
  by: TypedSliceMap
  label?: DisplayLabel
}

RuntimeRatioExprV1 {
  kind: "ratio"
  numerator: MetricExprInput
  denominator: MetricExprInput
  zero_division: ZeroDivisionPolicy
  label?: DisplayLabel
}
```

No generic base node permits arbitrary payloads. Exhaustive matching is
required in serialization, validation, help rendering, telemetry, planning,
replay, and recovery.

### Internal graph

After model resolution, both catalog and runtime roots lower to
`MetricExpressionGraphV1`. The internal graph may contain more node kinds than
the public runtime algebra because it must represent existing catalog behavior:

```text
CatalogBodyLeaf
Aggregate
WeightedMeanAggregate
Slice
Cumulative
Ratio
Linear
```

The internal set is also closed and versioned. A catalog metric is not treated
as an opaque scalar leaf when its body is needed for recursive planning,
components, replay, or bottom-up semantics. Its catalog root identity remains
attached separately from its lowered computation graph.

Catalog-lowered nodes also carry catalog-authoritative semantic facts that are
not available to runtime constructors, including an explicit authoring unit
override when present. Such facts are versioned graph inputs and participate in
the resolved semantic dependency digest. Runtime nodes cannot fabricate or
override them.

Public runtime v1 exposes only aggregate, weighted_mean, slice, and ratio. The presence of an
internal node does not pre-authorize its public constructor.

### Shared graph ownership

The closed graph IR, catalog-node lowering, canonicalization, semantic dependency
projection, and depth/occurrence validator are internal semantic computation
contracts owned below the analysis planner. Concretely they live in an internal
semantic module or an equally lower-level dependency-neutral module; semantic
readiness must never import `marivo.analysis`.

Semantic readiness invokes the catalog-only lowerer and shared pure validator.
Analysis imports the same graph contract, lowers public `RuntimeMetricExpr`
descriptors into it, adds observation scope and physical planning, and executes
it. There is one catalog lowering algorithm and one graph-budget implementation,
not parallel readiness and observe approximations.

### Recursion and operator admission

Admission is checked per node:

- a ratio may consume another ratio when both child graphs have complete unit
  state (known factorization or explicit unknown), coverage, missingness,
  component, and replay semantics; unknown unit state remains legal but cannot
  satisfy a later unit-required capability;
- a slice may wrap any subtree whose leaves can all resolve the sliced
  dimensions;
- an aggregate consumes only an analysis-ready `MeasureRef`, not another metric
  expression;
- cumulative and linear nodes retain their own child restrictions;
- weighted-mean aggregate leaves consume two same-row measure refs and expose
  exact numerator/weight components rather than derived child metrics;
- an operator may reject a legal graph as an immediate child without making the
  entire expression system non-recursive.

For example, ratio-of-ratio is not rejected merely because it is nested.
Cumulative-over-ratio may remain illegal if the cumulative evaluator cannot
prove a valid fold and ordering contract.

### Semantic dependency digest

Public runtime descriptors contain refs but do not contain a resolved semantic
definition and therefore do not receive their final expression fingerprint at
construction time. During `observe`, resolution produces one
`SemanticDependencyDigestV1` over the exact dependency closure used by the
graph. Its canonical entries include, as applicable:

- semantic kind and canonical id;
- the object's definition/body digest (`body_ast_hash` or its versioned
  replacement);
- definition-changing metric, measure, dimension, time-dimension, entity, and
  relationship IR fields;
- referenced datasource identity and semantic-model revision facts required to
  reproduce path planning;
- catalog-authoritative unit/fold/filter/composition metadata.

The digest contains no executable callable or source code. It is a canonical
hash of the versioned semantic IR projection required for computation. The
projection schema must be explicit; object reprs and incidental source
locations are excluded.

Every resolved leaf node fingerprint includes the value-relevant projection of
its dependency entry, so reauthoring an object under the same ref changes that
node and every parent expression fingerprint. The full dependency digest is
persisted for cache/replay validation but is not folded wholesale into
computational value equality: authority-only fields such as owner, source
location, and catalog object name cannot prevent a catalog aggregate and an
equivalent runtime aggregate from sharing normalized value semantics. A
catalog metric wrapper is lowered away for value comparison when its complete
registered computation is representable; its distinct `CatalogMetricIdentity`
remains intact.

Artifact-cache reuse requires exact dependency-digest equality. Internal
node/physical-plan reuse may occur across different roots only when the
normalized node fingerprint and all required source/snapshot facts are equal.
Replay re-resolves the dependency closure and fails with a
semantic-definition-changed repair when the full digest differs; it never
silently executes or returns an artifact under the old identity.

### Canonicalization and fingerprints

Public trees/forests are normalized and interned into a DAG. Each
value-producing node has a stable content fingerprint computed from:

- schema and node version;
- discriminant and registered evaluator contract;
- canonical child node fingerprints and role order;
- governed ref identity and value-relevant resolved semantic dependency
  projection;
- normalized aggregation, fold, branch-local slice, and zero-division fields;
- all other definition-changing typed fields.

Fingerprints exclude presentation annotations, analysis purpose, time-window
bounds, requested output dimensions, session report timezone, and artifact ids.

Canonical serialization:

- uses explicit typed encodings, stable field order, and stable scalar
  normalization;
- applies the general slice-pushdown rewrite before node fingerprinting, so
  distribution-equivalent trees have one value graph;
- never uses `default=str` or language-object reprs;
- rejects unknown fields and unknown node variants;
- detects cycles defensively even though public frozen constructors cannot
  normally create them;
- enforces the v1 fixed constants `MAX_EXPRESSION_DEPTH = 10` and
  `MAX_EXPRESSION_OCCURRENCES = 256`;
- interns equal nodes for common-subexpression elimination.

Depth is the longest root-to-leaf path in the fully value-canonical graph, with
both root and leaf counted; every root in an ordered forest must be at most 10
nodes deep. Occurrences are counted across the complete ordered forest after all
value rewrites, including aggregate slice sugar and composite slice pushdown,
but before DAG interning/CSE. Repeating one otherwise identical subtree still
consumes the input budget, and a wrapper cannot bypass the budget by expanding
only after validation. This limits one observation graph, not the number of
metrics in the catalog.

Changing canonical bytes or evaluator semantics requires a new schema or
evaluator-contract version and new golden fixtures.

V1 exposes no public, project, environment, or session configuration for these
limits. Changing either constant is a reviewed schema/contract change with new
boundary fixtures, not an operational tuning knob.

### Presentation overlay

Normalization strips labels from the submitted tree before value rewrites and
records them in an `ExpressionPresentationV1` overlay keyed by the stable
canonical occurrence path, for example `root`, `root.numerator`, or
`root.denominator.numerator`. A deterministic submitted-to-canonical occurrence
map applies the slice-wrapper label rule above. This preserves different labels
for two occurrences of one interned node without retaining eliminated value
nodes merely for presentation.

- the root label determines the displayed runtime metric name and value-column
  label;
- child labels are optional component-display annotations;
- labels never affect expression equality, comparable semantics, evidence
  subjects, or computation-cache keys;
- a canonical presentation fingerprint participates in artifact identity, so
  two callers may materialize distinct labeled artifacts while reusing the same
  computed graph;
- absent labels receive a deterministic operator/fingerprint-derived display
  label, never one inferred from business meaning.

Component graph records reference presentation occurrence paths when rendering
labels. They do not store one mutable label on an interned value node.

## Recursive Planning and Execution

### Planning stages

`observe` performs these stages:

1. normalize the public root/ordered root collection, extract submitted
   presentation annotations, and normalize outer observation scope;
2. resolve every `MetricRef`, `MeasureRef`, `DimensionRef`, and
   `TimeDimensionRef` in one semantic model;
3. compute `SemanticDependencyDigestV1` for the resolved dependency closure;
4. lower catalog bodies and runtime nodes into one rooted graph or ordered-root
   `MetricExpressionGraphV1` forest;
5. canonicalize and intern the graph, then resolve submitted presentation
   occurrences through the deterministic submitted-to-canonical path map;
6. validate graph-wide source, entity, path, scope, depth, and occurrence limits;
7. derive node semantics bottom-up;
8. plan physical leaf acquisition and shared subexpressions;
9. evaluate registered nodes bottom-up;
10. persist the root frame, component graph, coverage, evidence subject, replay
   graph, and comparable semantics atomically.

### Shared planner responsibilities

The refactor may retain these services only after they are made direct owners of
the final graph contract; reuse must not introduce a wrapper or adapter around
the old planner:

- semantic ref resolution and readiness;
- datasource compatibility and entity/path discovery;
- time bucketing, dimensions, global slices, and report timezone;
- aggregate lowering and fold validation;
- catalog ratio value evaluation;
- typed-key alignment and deterministic ordering;
- cache, coverage, quality, artifacts, and evidence.

It replaces the flat assumptions that a derived plan has only immediate
base/cumulative component plans and that the evaluator consumes a one-level
component-column map. Planning and evaluation become recursive visitors over
the closed graph.

### Physical execution boundary

V1 uses a deliberate split rather than requiring the whole expression graph to
compile into one SQL statement:

- governed body leaves, typed branch/global slices, time bucketing, registered
  aggregate/fold operations, and other safely lowerable leaf work compile
  through Ibis and execute in the datasource backend;
- only their scoped, aggregated typed-key results cross back into Marivo;
- `Ratio` and `Linear` composition nodes evaluate bottom-up
  through registered Marivo evaluators over those aligned child results;
- `WeightedMeanAggregate` multiplies and aggregates its two measure inputs in
  one backend query before crossing the physical execution boundary;
- graph execution does not create one backend round trip per composition node:
  compatible leaves/subgraphs are planned together and reused through CSE/cache;
- catalog and runtime roots use the same placement rule.

Backend SQL is therefore a physical leaf-execution detail, not the canonical
definition of recursive composition semantics. In particular, typed-key union
alignment, absent-versus-null state, zero-division behavior, component facts,
coverage, and quality remain owned by the registered Marivo evaluator.

A later physical optimizer may fuse a closed compatible subtree into one backend
expression only when backend capability checks prove byte-for-byte-equivalent
keys, values, missingness, zero-division, components, coverage, and quality. Such
fusion cannot change expression identity or evaluator version and must pass the
same cross-backend conformance fixtures. V1 correctness does not depend on this
optional optimization.

### Common-subexpression elimination and cache reuse

Nodes with the same expression fingerprint and compatible requested scope are
planned once. The planner may reuse:

- one leaf query for repeated refs;
- one aggregate for repeated aggregate/slice nodes;
- compatible current-session or persisted materializations keyed by expression
  and scope fingerprint.

Reuse must verify snapshot, source-domain, scope, coverage, evaluator-contract,
schema, and exact `SemanticDependencyDigestV1` compatibility. Computation-cache
keys exclude the presentation overlay; artifact identity includes its
presentation fingerprint. A cached computation is an optimization, never an
alternate semantic input or implicit migration path.

### Evaluation contracts

Catalog and runtime aggregates share one registered `AggregateEvaluationV1`.
Catalog and runtime ratios share one registered `RatioEvaluationV1`.

For ratio evaluation:

- child key schemas must be compatible;
- keys are union-aligned with deterministic ordering;
- an absent child key remains distinguishable from a present null value;
- asymmetric keys remain present with null output (E0);
- a present zero denominator follows `zero_division`;
- `zero_division="null"` never produces infinity;
- role-specific presence, null, zero, and invalid counters are retained.

The same evaluator contract is used regardless of catalog/runtime origin or
nesting depth.

## Identity and Authority

### Root identity

```text
MetricIdentity = CatalogMetricIdentity | RuntimeExpressionIdentity

CatalogMetricIdentity {
  kind: "catalog"
  metric_id: str
}

RuntimeExpressionIdentity {
  kind: "runtime_expression"
  expression_schema: "metric-expression/v1"
  expression_fingerprint: SHA256
}
```

`CatalogMetricIdentity.metric_id` is the exact canonical `.id` of the resolved
`MetricRef` (for example `"sales.revenue"`). In-memory construction
starts from `MetricRef`; canonical payloads persist its string id. No new
canonical-ref wrapper type is introduced.

`RuntimeExpressionIdentity` is intentionally independent of operator kind and
session ownership. A recursive expression has one content identity and an
internal node graph rather than a proliferating union such as runtime
aggregate/runtime ratio/runtime ratio-of-ratio identities.

The descriptor receives this content identity only after `observe` resolves and
canonicalizes it. Session ownership belongs to the materialized artifact and
evidence subject, not to expression identity.

### Artifact identity

Artifact identity combines the root metric identity or ordered root-identity
tuple with normalized materialization scope, source compatibility domain,
semantic dependency digest,
snapshot/coverage references, presentation fingerprint, and artifact schema
version. Equal value expressions observed over different windows have equal
expression identity but different artifact identity. Equal computations with
different labels have equal expression identity and computation-cache keys but
different presentation/artifact identities.

### Authority

A runtime-observed frame:

- is a canonical typed artifact;
- may be persisted, recovered, compared, replayed, and used downstream when its
  state proves the required contracts;
- has complete governed leaf lineage.

It does not:

- resolve via catalog lookup;
- claim readiness, owner, business definition, or project domain;
- merge evidence identity with an equivalent catalog metric;
- become a semantic input to authoring APIs;
- acquire a `MetricRef` automatically.

## MetricFrame Contract

An observed runtime expression produces the same `MetricFrame` class as an
observed catalog metric. The frame records at least:

```text
metric_identity
metric_identities (ordered; multi-root frames)
expression_graph_ref
expression_fingerprint
semantic_dependency_digest
presentation_ref and presentation_fingerprint
artifact_identity
shape and key schema
unit
additivity/fold capability
semantic model and source compatibility domain
component graph ref
coverage and quality refs
replay graph ref
comparable value-semantics ref
```

These are derived facts, not user claims.

For an arity-one frame, `metric_identity` is populated and
`metric_identities=(metric_identity,)`. For a multi-root frame,
`metric_identity` is absent and `metric_identities` preserves root order. Public
convenience accessors and downstream arity gates use this as their final arity
contract; they never fabricate one identity for a multi-root frame.

### Unit

Units are derived bottom-up:

- aggregate unit follows the governed measure and registered aggregate rules;
- slice preserves unit;
- a catalog-lowered node preserves an explicit catalog authoring unit override;
- ratio applies one registered canonical quotient algebra shared by catalog and
  runtime evaluation;
- equal known units cancel to canonical dimensionless `"1"`;
- different known canonical units form a deterministic canonical compound unit
  (for example `CNY/{request}`), including cancellation through nested ratios;
- an unknown child unit produces an explicit unknown output unit and a
  unit-capability issue; observation remains legal, while downstream operations
  that require a known unit fail on that persisted state;
- a unit rejected by the existing authoring token validation fails at the
  responsible catalog node rather than being guessed;
- a valid governed unit outside the v1 quotient grammar remains authoritative
  on its catalog node, but a parent ratio that cannot factor it records unknown
  derived unit state instead of inventing a compound spelling.

No runtime API accepts a unit override.

This requires a versioned shared unit contract (for example
`MetricUnitAlgebraV2`) rather than reusing the current `ratio_unit` behavior that
returns `None` for every non-equal pair. Phase 1 must update catalog lowering and
evaluation together: existing catalog-authoritative overrides remain final for
their catalog nodes, while runtime nodes can derive but never author business
unit metadata.

The bounded grammar is an automatic inference/normalization contract, not a new
catalog authoring validator. Its scope is:

- ordinary catalog/runtime aggregate nodes continue to use the same registered
  aggregate-unit rules; they do not need compound-unit parsing merely to
  aggregate a measure;
- catalog derived nodes without an explicit unit and all runtime composition
  nodes use the same bounded algebra for automatic unit derivation;
- an explicit authoring-valid catalog `unit=` remains authoritative even when it
  is outside the factorable grammar;
- runtime constructors accept no corresponding override;
- a parent that must combine an opaque child unit produces explicit unknown
  derived-unit state unless that parent is itself a catalog node with an
  authoritative override.

Using a runtime-only algebra is forbidden: equivalent catalog/runtime
computations must not receive different unit capability merely because of
origin. The algebra derives metadata only; it performs no numeric conversion.

### MetricUnitAlgebraV2 bounded grammar

`MetricUnitAlgebraV2` is a required Phase 1 sub-contract, not an open-ended UCUM
implementation. It supports only the factorable quotient subset needed for
deterministic nested ratio derivation:

```text
CanonicalUnitV1 := "1" | Product | Product "/" Product
Product         := Atom ("." Atom)*
Atom            := case-sensitive printable non-whitespace ASCII authoring
                   token without ".", "/", "(", or ")", other than reserved "1",
                   including "{singular_noun}"
```

The non-empty, printable-ASCII, no-whitespace boundary is the same one enforced
by the existing catalog `_validate_unit` authoring path; the additional reserved
characters above define only whether that otherwise valid token is factorable by
this bounded algebra.

The algebra stores a structured signed multiset of atoms internally; it does not
reparse a previously rendered parent string while walking a nested graph.
Reduction is exact and mechanical:

1. `"1"` is the empty product, so `U/1 = U`, `1/1 = 1`, and `1/U` remains
   `1/U`;
2. division subtracts denominator atom multiplicities from numerator
   multiplicities;
3. identical case-sensitive atoms cancel one-for-one across the quotient;
4. remaining numerator and denominator atoms are sorted bytewise and joined by
   `.`; an empty numerator renders as `1`, and an empty denominator is omitted;
5. no conversion or synonym rule is applied: `ms` does not cancel `s`, `%` does
   not cancel `1`, and `CNY` does not cancel `USD`;
6. repeated factors remain repeated in v1 (`s.s`), with no exponent syntax,
   parentheses, multiplication coefficients, or full UCUM parser.

For example, `(CNY/{request}) / (s/{request})` reduces to `CNY/s`,
`CNY/CNY` reduces to `1`, and `1/{request}` remains unchanged. Existing catalog
unit strings that match this grammar are factorized. Other authoring-valid unit
strings remain exact opaque catalog metadata but cannot be automatically
combined by a parent ratio; the parent receives unknown derived unit plus a
unit-capability issue unless that parent is a catalog node with its own explicit
authoritative unit override.

This grammar, its structured encoding, the opaque-unit fallback, and every
rendered canonical string are pinned by golden tests. Extending the grammar or
adding unit conversion requires a separate reviewed algebra version.

### Additivity and fold

Additivity and re-aggregation capability are also derived bottom-up. Ratio
outputs are not assumed additive. A downstream rollup or cumulative operation
is admitted only when the graph persists a valid fold/evaluation contract for
that operation.

## Scope, Alignment, and Source Contract

### One model and datasource domain (S0)

Every resolved leaf must belong to one semantic model and one exact datasource
compatibility domain. Multiple physical sources may participate only when their
resolved datasource identity and backend/connection-profile revision facts are
exactly equal. Cross-model or plural-domain composition fails with a structured
blocker.

V1 defines `DatasourceCompatibilityDomainV1` as that canonical resolved
datasource identity together with the backend/connection-profile revision facts
required to prevent an alias from changing meaning across recovery. Equality is
exact. The descriptor is
persisted in the graph, artifact identity, coverage, replay, and cache key. A
future design may widen compatibility beyond exact resolved datasource identity;
this design does not infer compatibility merely because two connections use the
same backend type.

### Entity and path compatibility

The planner resolves a common legal analysis path for all leaves under the
requested scope. It must not invent joins that the semantic graph cannot prove.
Ambiguous, disconnected, or fanout-unsafe paths fail before execution.

### Shape and keys

All child nodes are planned against the outer observation shape. A ratio
requires compatible typed key schemas after branch planning. Scalar,
time-series, segmented, and panel outputs are legal when this can be proven.

### Missing keys (E0)

Alignment uses typed-key union semantics. If a key is present in only one child:

- the row remains present;
- the composed value is null;
- component presence records which role is absent;
- quality and coverage record the asymmetry;
- no metric kind, including count, receives implicit zero fill.

Missing key, present null, and present zero denominator are distinct states.

## Component Graph

The one-level numerator/denominator sidecar is replaced by a recursive component
graph keyed by stable expression node id.

Each node record contains:

```text
node_id and node fingerprint
node kind and evaluator contract
ordered child roles and child node ids
presentation occurrence paths (labels remain in the presentation overlay)
derived unit/additivity/value semantics
key-presence and null/zero quality facts
coverage reference
governed leaf lineage
```

An arity-one output frame points to one root node; a multi-metric frame points to
an ordered root list. Shared subexpressions appear once and may have multiple
parents. Consumers can request a root summary or traverse the typed graph plus
its presentation overlay without reparsing labels or SQL.

Components are persisted atomically with the frame. Missing, corrupt, or
version-mismatched component graphs fail closed for operations that require
them.

## Replay Contract

Replay persists the complete logical expression graph plus typed scope
descriptors, never executable user code or generated SQL.

Replay may change an admitted observation scope, such as adding a compatible
dimension or changing a window, then recursively replan all affected leaves and
reevaluate the same graph. It must:

- preserve expression fingerprint;
- produce a new artifact identity for the new scope;
- resolve the same governed refs, exact semantic dependency digest, and
  evaluator versions;
- revalidate model/source/path/slice compatibility;
- recompute components, coverage, quality, and output;
- fail if any required semantic object or evaluator contract changed
  incompatibly.

Replay of only a stored output frame is insufficient. The whole graph is the
unit of recursive replay.

## Comparable Semantics

Compare is based on expression value semantics, not artifact id or origin.

`ComparableValueSemanticsV1` includes:

- the root expression fingerprint, which transitively includes every
  value-relevant semantic dependency projection;
- evaluator-contract versions;
- normalized outer global `slice_by` from `ScopedValueSemanticsV1`;
- definition-changing transformations;
- unit/fold semantics;
- any other fact required to claim equal value meaning.

It excludes window bounds so the same scoped metric may be compared across
windows. Compare requires exact normalized global-slice equality. Grain,
requested dimensions, explicit time dimension, report timezone, and expected
shape are validated by one shared final alignment/shape policy rather than
ignored.
Different global populations such as `country=CN` and `country=US` are not
comparable merely because their expression roots match.

Frames lacking complete comparable semantics are rejected. Callback- or
data-dependent transforms remain incomparable until a future closed descriptor
proves deterministic equality.

Equivalent catalog and runtime computations may be computationally comparable
without merging catalog and runtime-expression identities or evidence subjects.

For `compare(current, baseline)`, the delta is directional: `current` is the
subject and `baseline` is the comparator. Cross-identity comparison therefore
does not copy one root id and discard the other. The resulting `DeltaFrame`
persists:

```text
metric_identity: current.metric_identity
baseline_metric_identity: baseline.metric_identity
comparison_identity: DeltaComparisonIdentityV1 {
  current_metric_identity
  baseline_metric_identity
  current_artifact_id
  baseline_artifact_id
  comparable_value_semantics_fingerprint
  alignment_policy_fingerprint
}
```

The ordered `comparison_identity` is the canonical identity of the materialized
comparison operation; source artifacts and normalized alignment prevent equal
metric semantics over different inputs from colliding. Swapping the arguments
changes it and reverses delta direction.
Downstream operations that need a metric subject use the current identity, while
deduplication, recovery, delta evidence, and audit use the complete ordered
comparison identity. Neither side is allowed to impersonate the other merely
because their lowered value semantics are equal.

Replay may deliberately change a global slice. In that case it preserves the
definition-only expression fingerprint but produces new scoped comparable
semantics and artifact identity. Cache reuse also requires exact normalized
global-slice equality; a shared unsliced leaf scan may be reused only as an
internal physical optimization whose result is sliced and re-evaluated before
artifact commit.

## Coverage and Quality

Coverage and quality propagate bottom-up and retain node/role context:

- leaf acquisition coverage;
- branch-local and global slice coverage;
- missing aligned keys by child role;
- present null values by node and role;
- zero denominators and zero-division results;
- invalid/non-finite inputs and outputs;
- replay scope and snapshot coverage.

The root exposes an ordinary summary while the component graph retains enough
detail for diagnosis. Nested expressions must not collapse all child failures
into one root null counter.

## Evidence Contract

Evidence subjects use discriminated metric identity:

```text
TypedEvidenceSubject =
    CatalogMetricSubjectV1
  | RuntimeExpressionSubjectV1
  | DeltaMetricSubjectV1
```

A runtime-expression subject includes the owning `session_id`, root expression
fingerprint, and artifact/scope identity required by the finding. The canonical
subject key contains that session and artifact ownership even though
`RuntimeExpressionIdentity` itself remains reusable for expression equality. The
current session-directory placement of `judgment.db` is defense in depth, not
the only cross-session isolation mechanism. A runtime-expression subject may
reference governed catalog leaf lineage, but it never impersonates any one
catalog metric.

`DeltaMetricSubjectV1` contains the ordered current/baseline identities, the two
source artifact ids, the comparable-value-semantics fingerprint, and the owning
session id. The current identity is explicitly marked as the subject role;
canonical keys and findings retain both sides. A catalog/runtime comparison can
therefore never collide with a catalog/catalog or reversed runtime/catalog
comparison that happens to yield the same numeric delta.

Findings remain factual and artifact-backed. Labels and `analysis_purpose` help
presentation but do not become semantic authority or canonical subject keys.

Evidence persistence, digests, and recovery switch destructively to the new
identity and graph schema. Old catalog-string-only or flat-component payloads
are rejected with recreate-session guidance.

### Human caliber closeout

Runtime metric expressions avoid durable semantic authoring, but they must not
hide the business choices that authoring would have required a human to settle.
Whenever a material claim or deliverable relies on a runtime-expression root,
analysis closeout discloses:

- the normalized expression tree and governed catalog leaf identities, with
  aggregation and fold choices at every aggregate node;
- branch-local slices by occurrence path and the outer global slice/time scope;
- every ratio's numerator/denominator roles and `zero_division` policy;
- missing-child, present-null, zero-denominator, and affected-result counts by
  node and child role rather than only one collapsed root count;
- the display label, if present, marked explicitly non-authoritative;
- `metric_identity.kind = "runtime_expression"` for an arity-one runtime root,
  or the corresponding `metric_identities` entry for a multi-root frame, plus the
  owning session/artifact analysis scope and the fact that equal computation
  does not create catalog authority;
- relevant unit, coverage, source, replay, and quality limitations.

The human remains responsible for judging whether that runtime caliber is
meaningful for the decision. Repeated use or a need for governed reuse routes to
explicit semantic authoring; Marivo never infers promotion.

At implementation cutover, the detailed disclosure checklist belongs in a
task-specific `marivo-analysis/references/runtime-metric-closeout.md` reference
loaded only when a material claim uses a runtime-expression root. The commonly
loaded `marivo-analysis/SKILL.md` keeps only the compact routing/closeout
obligation and does not duplicate constructor signatures, operator recipes, or
live help.

## Downstream Capability Contract

### State-driven, origin-blind rule (A)

After observation, operators inspect persisted facts:

- shape and key schema;
- unit and additivity/fold contract;
- component graph completeness;
- coverage and quality;
- replay capability;
- comparable semantics;
- entity/path and source compatibility.

They must not contain allow/deny branches based on catalog versus runtime
origin, aggregate versus ratio root kind, or nesting depth.

This does not mean every graph supports every operation. It means rejection is
explained by the missing semantic state. For example, attribution may reject a
nested expression if a required decomposition contract is absent, but the
blocker must name that absent state rather than say “runtime metric” or “nested
ratio” is unsupported.

Transforms that change values, keys, or scope must recompute or invalidate
component, replay, comparability, coverage, and quality metadata. Copying stale
capability state is forbidden.

## Supported End-State Use Cases

- observe an exact typed catalog `MetricRef` through the sole materializer;
- observe a runtime aggregate over an analysis-ready measure;
- observe an admitted catalog/runtime ordered root list over one shared scope;
- apply a typed branch-local slice to a catalog metric or runtime subtree;
- combine catalog metrics and runtime expressions as a ratio;
- recursively compose ratios, subject to node-specific validation;
- observe scalar, time-series, segmented, and panel shapes;
- compare equal expressions across compatible windows;
- inspect recursive components and quality;
- replay the expression under an admitted compatible scope;
- use transforms, correlate, hypothesis test, forecast, discover, quality, and
  attribution when their ordinary state preconditions are satisfied;
- recover current-schema runtime-expression frames after cold start.

## Explicitly Unsupported End-State Use Cases

- raw strings or unresolved bare ids in place of typed refs;
- loaded `CatalogObject` values, generic `SemanticRef` values, or wrong-kind refs
  in place of exact metric/measure/dimension ref types;
- raw columns, SQL, Ibis, pandas, callbacks, Python functions, or arbitrary AST
  nodes;
- literals and free arithmetic operators;
- caller-supplied unit, additivity, identity, owner, domain, or business context;
- observation of a runtime expression or multi-root forest spanning semantic
  models or datasource compatibility domains;
- implicit missing-key zero fill;
- construction from a `MetricFrame` or terminal artifact;
- use of a runtime expression as an authoring input;
- automatic catalog promotion;
- recovery or lazy rewriting of pre-cutover payloads;
- expression depth above 10 or pre-CSE occurrence count above 256;
- unknown node variants or evaluator versions.

## Catalog Promotion Boundary

Promotion remains an explicit semantic-authoring workflow. A human or agent may
use a successful runtime expression as evidence for authoring a persistent
metric, but must still provide the durable name, business definition, ownership,
domain, authoring expression, validation, preview, and readiness required by the
semantic layer.

No API in this design converts `RuntimeMetricExpr`, `RuntimeExpressionIdentity`, or
`MetricFrame` directly into `MetricRef`.

## Extension Rules

A new public runtime operator is admitted only when it defines:

1. a closed typed node schema and child algebra;
2. exact shared parameter types where authoring semantics overlap;
3. deterministic canonical serialization and fingerprinting;
4. model/source/entity/path validation;
5. registered value, unit, additivity, null, and missingness semantics;
6. recursive component and quality propagation;
7. replay behavior;
8. comparable semantics;
9. evidence representation;
10. state-based downstream capability effects;
11. persistence and recovery versions;
12. help, telemetry, tests, and terminal-boundary negatives.

Candidate future nodes include weighted average, commensurable linear
combination, and cumulative. Their existence in catalog lowering does not waive
this review.

## Persistence and Recovery

The breaking release introduces versioned payloads for:

- discriminated metric identity;
- canonical expression graph;
- semantic dependency digest and datasource compatibility domain;
- expression presentation overlay;
- artifact identity and scope;
- recursive component graph;
- replay graph;
- comparable value semantics;
- typed evidence subjects, findings, and digests;
- coverage and quality references.

Writers persist the complete final schema atomically. Readers accept only that
schema. Every store and artifact is rejected at its top-level version gate before
payload decoding when the version is not current. The repair is to discard the
old session/state and materialize again; readers do not inspect which historical
shape was present.

The evidence-store cutover is explicitly `judgment.db` SQLite
`PRAGMA user_version = 2 -> 3`; the implementation changes
`EXPECTED_SCHEMA_VERSION` to `3`. Version 3 owns the discriminated observation
subjects, ordered delta subjects, expression/component graph references, and
new artifact schema projection. Any `user_version != 3` fails before table or row
interpretation with recreate-session guidance.

There is no compatibility decoder, migration job, dual writer, lazy rewrite,
or fallback interpretation. Phases below are review checkpoints inside one
breaking release, not independently supported persistence generations.

## Errors

Errors are structured and identify the responsible graph node and repair when
possible. Required categories include:

- unknown or malformed runtime node;
- expression depth or pre-CSE occurrence limit exceeded;
- cycle detected;
- unresolved or not-analysis-ready metric/measure/dimension ref;
- resolved semantic dependency changed since persistence/replay;
- mixed semantic model;
- incompatible datasource domain;
- invalid or ambiguous entity/path plan;
- invalid aggregate or fold for a measure;
- branch-local slice unavailable for one or more leaves;
- incompatible ratio key schema or unit semantics;
- missing child key, source null, and zero denominator as distinct quality
  states;
- missing/corrupt component, replay, comparable-semantics, or evidence payload;
- unsupported schema/evaluator version;
- downstream precondition absent.

Messages must not recommend exporting to pandas as the normal repair for an
expression that can be represented by the closed algebra. They may recommend
semantic authoring when a genuinely missing governed object or operator is
required.

## Live Help and Agent Surface

Live help teaches one path:

```text
1. Build a closed runtime metric expression with mv.runtime_metric.* when a
   catalog MetricRef is not sufficient.
2. Observe one or a non-empty sequence of catalog/runtime metric inputs with
   session.observe(...).
3. Inspect show(), contract(), components(), and coverage().
4. Continue with ordinary typed analysis when state preconditions allow it.
```

Help includes:

- constructor signatures and shared parameter-type alignment;
- the chosen singleton/list/tuple observe contract, exact kind-specific inputs,
  `time_dimension`, and `expect_shape`;
- outer scope versus branch-local slice examples;
- recursive ratio examples;
- catalog-authority and terminal-boundary warnings;
- structured blocker/repair explanations;
- promotion guidance through semantic authoring rather than automatic conversion.

There is no `session.compose`, frame arithmetic, or generic formula guidance.
Packaged `marivo-analysis` and `marivo-semantic` skills must teach the same
boundary.

## Telemetry

Telemetry records bounded structural facts only:

- catalog versus runtime root;
- admitted public/internal node kinds, expression depth, and pre-CSE occurrence
  count;
- output shape;
- aggregate and zero-division policy enums;
- cache/CSE/replay use;
- structured success/error category;
- downstream capability blocker category.

It does not record labels, analysis purposes, business definitions, slice
values, raw values, complete expression payloads, SQL, or metric contents.

## Phased Delivery

Phases are implementation and review checkpoints inside one breaking release.
No intermediate schema is supported by the final runtime.

### Phase 0 — final identity and graph-schema foundation

- define the final frozen `CatalogMetricIdentity | RuntimeExpressionIdentity`,
  `MetricExpressionGraphV1`, node ids, ordered roots,
  `SemanticDependencyDigestV1`, presentation overlay, artifact/component/replay/
  comparable-semantics payloads, exact typed public input aliases, and
  ref-parameterized catalog object/collection types;
- implement pure canonical serialization, fingerprinting, slice rewriting,
  depth/occurrence validation, and golden fixtures against those final types;
- define the final ordered `DeltaComparisonIdentityV1` and
  `CatalogMetricSubjectV1 | RuntimeExpressionSubjectV1 | DeltaMetricSubjectV1`
  schemas;
- add no flat-plan projection, production read/write adapter, compatibility
  decoder, or temporary payload generation; Phase 0 defines and tests final
  foundations without wiring a transitional runtime;
- expose no runtime constructors and change no production execution path yet.

Phase 0 is independently reviewable but not releasable.

### Phase 1 — recursive planner and shared evaluator foundation

- delete flat `DerivedObservePlan`/`ComponentPlan` assumptions and switch catalog
  observation directly to recursive graph-native planning and bottom-up
  evaluation; no adapter translates an old flat plan into the new graph;
- cut frames, artifacts, cache keys, evidence, findings, digests, recovery, and
  storage directly to the final identity/graph schema, including `judgment.db`
  `user_version = 3`; existing catalog/catalog deltas write the ordered v1
  comparison/evidence subjects, and every non-current payload fails closed;
- remove the generic `nested-derived-unsupported` rejection for graph
  combinations admitted by their node rules;
- remove the matching `nested_derived_unsupported` validator/readiness/reader
  warning, error-registry entry, flatten repair, semantic spec text, and tests
  for legal nested graphs; replace genuinely illegal combinations with their
  operator-specific structured issue and repair;
- update the public `ms.ratio`, `ms.weighted_mean`, and `ms.linear` docstrings,
  semantic help, and `docs/specs/semantic/semantic-object-model.md` so catalog
  authoring/readiness no longer teaches the obsolete blanket nesting ban;
- lower existing catalog aggregate, weighted-mean, cumulative, ratio, and
  linear semantics into the internal closed graph as applicable;
- make catalog and future runtime aggregates share `AggregateEvaluationV1`;
- make catalog and future runtime ratios share `RatioEvaluationV1`;
- keep safely lowerable leaf/body/slice/aggregate/fold work in backend Ibis/SQL
  execution and make recursive composition execute through the registered Marivo
  evaluators by default; any later SQL subtree fusion must prove identical typed
  results and node facts across backends;
- implement the bounded `MetricUnitAlgebraV2` grammar and reduction contract,
  applying it to automatic catalog/runtime derivation while preserving the wider
  catalog authoring-token contract and explicit catalog-authoritative unit
  overrides; runtime receives no override and the implementation adds no full
  UCUM parser;
- implement recursive unit, fold, component, coverage, quality, and replay
  derivation;
- implement general composite-slice canonical pushdown, predicate conjunction,
  occurrence-path remapping, and post-rewrite depth/occurrence enforcement for
  catalog-lowered graphs and the shared graph schema;
- make semantic readiness lower and validate every selected catalog metric root
  with that shared graph implementation; depth 11 or occurrence 257 is a blocking
  readiness issue and never enters `analysis_ready_refs`;
- add exact semantic-dependency validation, DAG interning,
  common-subexpression planning, and compatible cache reuse;
- prove catalog observation values and downstream behavior against the semantic
  and registered evaluator contracts on the new planner, without treating old
  planner structure or incidental output encoding as an authority.

### Phase 2 — public runtime expressions through observe

- add frozen `mv.runtime_metric.aggregate`, `weighted_mean`, `slice`, and `ratio` descriptors;
- delete the broad analysis `SemanticInput`, `MetricInput`, and `DimensionInput`
  aliases; use exact `MetricRef`, `ObserveMetricInput`, and
  `AnalysisDimensionRef` annotations and normalizers across observe, attribute,
  discover, decompose, frame transforms, and internal replay;
- consume exact `MetricRef`, `MeasureRef`, `DimensionRef`, and `TimeDimensionRef`
  values directly from the ref-parameterized catalog surface with no casts or
  generic-ref fallback;
- implement the deliberately selected singleton/list/tuple normalization,
  `time_dimension`, `expect_shape`, and session-owned report-timezone contract;
- support ordered mixed catalog/runtime multi-root forests with one final arity,
  shape, and value-column contract;
- align `measure`, `agg`, `fold`, dimension, slice-value, and observe-scope
  parameter types/normalizers with authoring and observation contracts;
- narrow `ms.aggregate(fold=...)` and runtime aggregate to the shared
  `AggregateFoldInput` alias with canonical default `None`; narrow the required
  `ms.semi_additive(fold=...)` surface to the shared non-null
  `AggregateFoldValue`, with all three paths using the same normalizer;
- support mixed catalog/runtime children and recursive ratio children;
- enforce S0 model/source rules, entity/path compatibility, depth 10,
  pre-CSE occurrence 256, and node-specific admission;
- support scalar, time-series, segmented, and panel typed-key union alignment;
- implement E0 missing-key null semantics and role-specific quality facts;
- persist/recover complete runtime frames, graph, components, replay, coverage,
  comparable semantics, and evidence;
- publish live help, skills, docs, public-surface snapshots, and telemetry.

### Phase 3 — recursive compare, replay, and attribution

- compare equal expression semantics across compatible materialization scopes;
- require exact normalized outer global-slice equality in
  `ComparableValueSemanticsV1` while permitting compatible window differences;
- activate cross-identity compare over the ordered `DeltaComparisonIdentityV1`
  and `DeltaMetricSubjectV1` schemas defined in Phase 0 and wired for
  catalog/catalog deltas in Phase 1, keep `current` as the delta subject, retain
  `baseline` as comparator, and use both roots in delta evidence and canonical
  keys;
- propagate recursive component graphs into deltas and evidence;
- replay full graphs for compatible changed windows, dimensions, and global
  slices;
- recompose segmented/panel expressions after missing-axis acquisition;
- implement recursive attribution/decomposition for admitted node kinds;
- make unsupported decomposition a precise missing-state blocker, not a runtime
  or nesting-origin rejection;
- audit transforms for correct state recomputation/invalidation.

### Phase 4 — state-driven downstream completeness

- complete the downstream capability matrix using only persisted semantic/state
  preconditions;
- remove transitional origin-, root-kind-, and nesting-depth restrictions where
  state proves the ordinary contract;
- prove catalog/runtime computational symmetry for equivalent graphs without
  identity or evidence merging;
- prove transforms, correlate, hypothesis test, forecast, discover, quality,
  compare, replay, and attribution produce the same admission outcome for equal
  state;
- retain E0 missing-key null semantics and S0 one-model/source-domain scope;
- keep empty-value zero fill and plural-source composition outside the final
  design; compatibility and migration remain absent from every phase.

No phase adds arbitrary expressions, terminal-result re-entry, or automatic
semantic promotion.

## Testing Strategy

### Expression and identity

- frozen constructor and exhaustive-union tests;
- equivalent tree forms normalize to the same DAG and fingerprint;
- aggregate `slice_by` equals explicit slice wrapping;
- `slice(ratio(a, b), X)` and
  `ratio(slice(a, X), slice(b, X))` have identical canonical bytes,
  fingerprints, CSE/cache keys, comparable semantics, and evidence subjects;
- slice-pushdown presentation tests prove that an outer slice label moves only
  to the rewritten subtree root, an absent wrapper label preserves the child
  root label, descendant labels follow remapped occurrence paths, and no label
  is copied onto synthetic leaf slices;
- child role order changes ratio identity;
- labels and analysis purpose do not change value identity;
- labels at different occurrence paths survive DAG interning; root labels change
  presentation/artifact identity but not computation-cache identity;
- definition-changing fields do change identity;
- changing a value/path-relevant field on a measure, metric, dimension,
  relationship, or other resolved dependency under the same ref changes its
  semantic dependency digest, expression identity, and cache eligibility;
- changing only authority/presentation metadata changes the full audit digest
  when appropriate but does not make equivalent normalized computations
  incomparable;
- unchanged dependency closures produce stable digests independent of source
  location and object repr;
- shared subexpressions intern once;
- cycles and unknown nodes fail closed; depth 10/occurrence 256 are accepted
  while depth 11/occurrence 257 fail, and repeated submitted structure cannot
  bypass the pre-CSE limit;
- golden canonical bytes and SHA-256 fixtures are versioned;
- catalog and runtime-expression identities never merge or resolve through the
  wrong path;
- identical runtime expressions observed in two sessions may share expression
  identity but produce distinct session/artifact-owned evidence subject keys and
  are never consolidated as one governed subject.

### Parameter and evaluator alignment

- runtime aggregate accepts the exact authoring `MeasureRef`, `AggKind`, and fold
  inputs and uses the same normalizers;
- catalog `Metric`/`Measure`/`Dimension`/`TimeDimension` objects and their typed
  collections expose exact ref subclass annotations; analysis signatures require
  those subclasses and reject generic `SemanticRef` even when its runtime kind
  field happens to match;
- observe, attribute, discover, decompose, frame transforms, and replay use the
  same exact-ref normalizers; loaded catalog objects, generic refs, bare ids, and
  wrong-kind refs fail consistently at every analysis semantic-input boundary;
- both authoring and runtime aggregate reject values outside the closed fold
  alias, use `None` as the same canonical default, and enforce the same
  percentile range;
- `ms.semi_additive(fold=...)` accepts the same non-null fold values, rejects
  `None` and values outside `AggregateFoldValue`, and shares the same normalizer
  and percentile range with both aggregate surfaces;
- runtime/global slices use the exact typed dimension and slice-value
  normalization as observation;
- runtime aggregate, runtime slice, and observe `slice_by` all annotate
  read-only inputs as `Mapping[AnalysisDimensionRef, SliceValue]` and freeze one
  normalized copy before identity or planning;
- catalog/runtime aggregates share legal/illegal aggregate, unit, fold, null,
  and component behavior;
- catalog/runtime ratios share key alignment, zero division, unit, quality, and
  finite-output behavior;
- catalog/runtime derived nodes without an override share automatic unit
  derivation; catalog unit overrides survive lowering, known unequal factorable
  units produce a canonical compound unit, and unknown units remain explicit
  state that gates only unit-requiring consumers;
- an authoring-valid opaque catalog unit remains accepted and authoritative at
  its node even when the bounded algebra cannot factor it; the same grammar is
  not exposed as a runtime unit override;
- unit golden tests cover `1`, exact cancellation, unequal atoms, nested
  cancellation, deterministic factor order, repeated factors, opaque
  authoring-valid units, and unsupported grammar without attempting full UCUM
  conversion;
- no caller metadata override or raw authoring filter is accepted.

### Recursive planner

- catalog-derived graphs satisfy the final registered value, key, null, unit,
  fold, component, coverage, and quality contracts after flat-planner deletion;
- legal nested catalog metrics no longer emit
  `nested_derived_unsupported` through validator, details, readiness, help, or
  specs; illegal child kinds emit the responsible operator-specific issue;
- catalog readiness and observation use the same canonical lowerer and graph
  budget validator: per-root depth 10/occurrence 256 pass, depth 11/occurrence
  257 are excluded from `analysis_ready_refs` with count/limit/dependency-path
  repair facts, and multi-root observe independently enforces its forest budget;
- `ms.ratio`, `ms.weighted_mean`, and `ms.linear` docstrings plus
  `semantic-object-model.md` authorize admitted nested derived catalog graphs and
  retain operator-specific exclusions;
- nested ratios plan and evaluate correctly;
- illegal child combinations fail at the responsible node;
- branch-local slices push to all required leaves and reject unavailable
  dimensions;
- nested slice maps deduplicate equal predicates, reject conflicting predicates,
  and apply depth/occurrence limits only after the complete pushdown rewrite;
- leaf/body/slice/aggregate/fold work executes through backend Ibis/SQL lowering,
  recursive composition executes through the Marivo evaluator by default, and
  no composition layer creates an extra backend round trip;
- backend-specific SQL behavior cannot change typed-key union, missing/null,
  zero-division, component, coverage, or quality semantics;
- repeated leaves/subgraphs use one compatible physical plan;
- incompatible snapshots/scopes never reuse cached materializations;
- entity/path ambiguity and fanout risks fail before execution.

### Scope and alignment

- observe accepts only `MetricRef | RuntimeMetricExpr` roots and exact
  kind-specific dimension refs; loaded `CatalogObject`, generic `SemanticRef`,
  bare ids, and wrong-kind typed values fail at the boundary;
- singleton, one-element list/tuple, and multi-root inputs follow the selected
  final arity, `time_dimension`, `expect_shape`, and value-column contract;
- mixed catalog/runtime multi-root forests share scope and reuse common nodes or
  fail with a root-path-specific compatibility error;
- scalar, time-series, segmented, and panel results;
- typed-key union alignment and deterministic ordering;
- duplicate/incompatible keys fail;
- missing keys remain present with null output for every metric kind;
- missing child, present null, and present zero denominator remain distinct;
- `zero_division="error"` rejects only present zero denominators;
- global and branch-local slices remain semantically distinct;
- compare rejects unequal normalized global slices even when expression
  fingerprints match, while compatible window comparison remains legal;
- catalog-current/runtime-baseline and runtime-current/catalog-baseline compare
  when value semantics match; the DeltaFrame marks current as subject, retains
  both ordered identities/source artifacts/alignment identity, and swapping
  arguments changes comparison/evidence identity;
- replay with a changed global slice preserves definition identity but changes
  scoped comparable semantics and artifact identity;
- mixed model or datasource domain fails under S0.

### Components, replay, and downstream

- recursive component DAG persistence and shared-child traversal;
- node/role coverage and quality propagation;
- same-expression compare and different-expression rejection;
- cold-start replay of a full nested graph;
- missing-axis replay preserves expression identity and changes artifact identity;
- recursive delta/evidence propagation;
- Phase 1 catalog/catalog deltas write and recover the ordered v1
  comparison/evidence payloads, while Phase 3 adds cross-identity admission
  without another persistence schema change;
- runtime-expression material claims exercise the human-caliber closeout
  reference, including nested aggregation/slice/zero-division choices and
  node-role quality counts;
- downstream operators make equal decisions for equal state regardless of
  catalog/runtime origin or nesting depth;
- transforms clear or recompute invalidated state.

### Boundaries and recovery

- SQL, Ibis, pandas, callbacks, functions, literals, arbitrary nodes,
  `MetricFrame`, and terminal results are rejected;
- runtime expressions cannot enter semantic authoring or become `MetricRef`;
- every non-v3 evidence store and every non-current artifact schema fail at the
  top-level version gate without historical payload decoding; a fresh store is
  written with `PRAGMA user_version = 3`;
- corrupt current-version graph/component/replay/evidence payloads fail closed
  with path-specific diagnostics;
- public imports, live help, docs, skills, telemetry, and tests agree;
- no `session.compose` surface exists.

## Documentation and Cutover Surface

Implementation updates together:

- public `mv.runtime_metric` types and exports;
- ref-parameterized semantic catalog objects/collections and exact `.ref`/
  `.refs()` annotations for metric, measure, dimension, and time-dimension
  handoff;
- deletion of analysis `SemanticInput`/`MetricInput`/`DimensionInput`, plus exact
  ref signatures, normalizers, help, and tests for observe, attribute, discover,
  decompose, frame transforms, and replay;
- `Session.observe` signature, help, capability registry, and shared planner
  routing; no module-level `mv.observe` wrapper is added;
- semantic lowering, dependency-digest projection, shared fold/unit contracts,
  and recursive analysis planner/evaluator types;
- the `ms.aggregate` and `ms.semi_additive` signatures/docstrings/help plus their
  shared fold normalizer and public type exports;
- semantic validator, readiness, reader/details, errors, and help paths that
  currently publish `nested_derived_unsupported`;
- public authoring docstrings for `ms.ratio`, `ms.weighted_mean`, and
  `ms.linear`, plus `docs/specs/semantic/semantic-object-model.md`;
- frame, component, delta, result, replay, coverage, quality, cache, and
  persistence schemas;
- evidence subjects, canonical keys, findings, digests, and audit reads;
- compare, attribute, correlate, hypothesis test, forecast, discover,
  transforms, quality, and recovery paths;
- active analysis and semantic specs;
- latest English and Chinese documentation;
- packaged `marivo-analysis` and `marivo-semantic` skills, including the
  conditionally loaded
  `marivo/skills/marivo-analysis/references/runtime-metric-closeout.md` checklist
  rather than embedding its operator detail in the common skill kernel;
- telemetry declarations;
- public-surface, help-drift, persistence, evidence, planner, and operator tests.

## Success Criteria

The design is fully delivered when:

1. `mv.runtime_metric.*` builds only frozen closed aggregate, weighted_mean, slice, and ratio
   expressions from governed typed refs.
2. `session.observe(...)` remains the sole initial `MetricFrame` producer,
   accepts exact `MetricRef | RuntimeMetricExpr` singleton/list/tuple roots, owns
   `time_dimension`/`expect_shape`, and owns all scope, planning, execution, and
   persistence; every other analysis semantic-object parameter likewise accepts
   only its exact ref subclass or closed ref union.
3. Runtime parameter types align with authoring/observation types wherever the
   semantics are the same; both aggregate surfaces use the closed optional fold
   alias and default `None`, while `ms.semi_additive` uses the same closed
   non-null fold value alias and normalizer.
4. Catalog and runtime roots lower to one versioned recursive expression DAG or
   ordered-root forest with an exact semantic dependency digest.
5. Legal nested ratios work; illegal combinations fail by node-specific semantic
   rules rather than a blanket nesting ban.
6. Catalog/runtime aggregate and ratio nodes share their registered evaluator
   contracts; leaf aggregation lowers through backend Ibis/SQL while recursive
   composition defaults to Marivo-side evaluation with no per-node round trip.
7. Outputs preserve catalog unit overrides, apply the bounded
   `MetricUnitAlgebraV2` only to shared catalog/runtime automatic derivation, and
   carry truthful identity, fold, recursive components, coverage, quality,
   replay, comparable semantics, lineage, and evidence state.
8. Composite slices canonicalize to the same leaf-pushed graph as manually
   distributed slices; common subexpressions and compatible persisted
   materializations can be reused only when semantic dependency and scoped-value
   semantics match, while presentation labels remain occurrence-correct without
   changing computation identity.
9. Missing keys remain present as null, and missing/present-null/present-zero
   states remain distinguishable.
10. Compare rejects unequal normalized global slices while permitting its
    admitted cross-window alignment.
11. Cross catalog/runtime compare records current as subject, baseline as
    comparator, and both ordered root identities in DeltaFrame/evidence identity.
12. One expression or multi-root observation cannot cross semantic-model or
    datasource compatibility domains.
13. Legal nested catalog metrics no longer emit the obsolete
    `nested_derived_unsupported` planner/readiness guidance, and authoring
    docstrings/specs explicitly teach the operator-specific authorization.
14. Semantic readiness excludes any catalog root deeper than 10 or larger than
    256 normalized pre-CSE occurrences using the shared graph validator;
    observation enforces the same per-root limits plus the 256-occurrence budget
    across the submitted forest.
15. Downstream admission depends only on persisted capability state, not origin,
    root operator, or nesting depth.
16. No runtime catalog id, readiness, owner, unit override, domain authority, or
    promotion is fabricated.
17. Material claims using runtime-expression roots satisfy the conditional human
    caliber disclosure contract without treating labels as authority.
18. Terminal pandas/SQL/Ibis data cannot re-enter typed analysis.
19. `judgment.db` v3 and all new graph/identity payloads are written directly;
    old persistence is rejected rather than migrated or interpreted.
20. Live help, contracts, docs, skills, telemetry, and tests teach one consistent
    boundary with `session.observe(...)` as the only materializer and no
    `mv.observe` or `session.compose` escape hatch.

## Final Judgment

Runtime typed metric composition should be a recursive typed definition system,
not a sequence of frame arithmetic operations. The clean boundary is:

```text
construct a closed expression -> observe it once -> continue with ordinary typed analysis
```

This design gives agents materially more analytical range while keeping their
choices inspectable, replayable, evidence-aware, and governed by Marivo's typed
planner. Recursion is safe when nodes are closed and semantics are derived;
arbitrary expression authority is not.
