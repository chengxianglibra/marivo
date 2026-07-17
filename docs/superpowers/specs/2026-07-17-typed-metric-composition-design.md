# Marivo Typed Metric Composition Design

Status: proposed

Date: 2026-07-17

Related issue: [#28](https://git.bilibili.co/ace-lab/marivo/-/issues/28)

## Summary

Add typed metric composition as a first-class `marivo.analysis` capability.
The initial and normative operator in this design is a session-owned ratio over
two existing single-metric `MetricFrame`s:

```python
failed = session.observe(
    query_count,
    time_scope=window,
    grain="hour",
    slice_by={state: "FAILED"},
)
total = session.observe(
    query_count,
    time_scope=window,
    grain="hour",
)

failure_rate = session.compose.ratio(
    numerator=failed,
    denominator=total,
    label="failure_rate",
    zero_division="null",
    analysis_purpose="Relate query failure rate to cache hit rate.",
)
```

The result is a canonical, legal `MetricFrame`, not a scratch result, terminal
projection, or second-class frame. It participates in the ordinary analysis
algebra according to the same shape, identity, additivity, component, coverage,
and replay preconditions as a catalog-derived ratio frame. Downstream operators
must not gate on whether the frame originated from the catalog or from session
composition.

The ratio operands are deliberately narrower than the result's downstream
surface. Under this design they must retain catalog metric authority and a
registered, replayable value-semantics descriptor. A session-composed frame is a
legal downstream `MetricFrame`, but it cannot be fed back into
`session.compose.ratio`. Nested composition is outside this algebra.

Typed metric composition does **not** add dynamic semantic objects. A composed
frame has a session-composition identity and complete computational lineage, but
it has no `MetricRef`, catalog entry, readiness status, domain owner, or project-
wide business authority. It cannot be promoted from SQL, Ibis, pandas, or an
arbitrary callback, and it does not weaken the terminal raw-data boundary.

This design deliberately separates two independent properties:

```text
computational canonicality: canonical typed artifact | terminal/non-canonical data
semantic authority:         catalog metric           | session composition
```

A session-composed ratio is canonical computation with session-scoped semantic
authority.

## Decision

Introduce one grouped public capability family:

```text
session.compose.ratio(MetricFrame, MetricFrame) -> MetricFrame
```

The operator composes already-materialized typed artifacts. It does not accept a
pre-materialization expression, build an arbitrary expression AST, execute user
code, or push an unverified query back into typed analysis.

The final contract in this design supports ratio composition across the ordinary
`MetricFrame` shapes (`scalar`, `time_series`, `segmented`, and `panel`) when
operand key schemas are compatible, using typed-key union alignment that
preserves missing components as null. It also supports complete session recovery,
component-aware compare and attribution, safe missing-axis replay, quality and
coverage propagation, and the normal downstream analysis surface. Delivery may
be phased, but the final architecture does not retain an origin-based downstream
capability restriction.

Future composition operators such as a commensurable linear combination require
their own explicit design admission against the extension rules in this document.
This design does not pre-authorize a generic formula language.

## Relationship to Existing Contracts

The current 0.3.2 live surface does not implement this design. It still exposes
`session.observe(...)` as the sole `MetricFrame` producer.

This design preserves the terminal raw-data boundary established by
`2026-07-14-terminal-raw-sql-boundary-design.md`: arbitrary SQL, Ibis, pandas,
callbacks, and terminal results still have no inbound path to typed artifacts.
It narrows and supersedes only the older statement that every value entering a
typed `MetricFrame` must first become a catalog semantic object. A registered
typed-to-typed composition is not raw-data promotion.

On implementation, the active analysis operator algebra is amended from
"`observe` is the sole canonical `MetricFrame` producer" to:

> `observe` is the sole producer of an initial `MetricFrame` from semantic
> inputs; registered composition operators may produce canonical `MetricFrame`s
> only from compatible canonical typed artifacts.

The active specs, live help, packaged skills, latest documentation, telemetry,
and drift tests must move atomically with that cutover. Until then, the installed
live surface remains authoritative.

## Problem

### Exploratory ratios currently force the wrong lifecycle choice

An analysis often needs a ratio that is meaningful for one investigation but is
not yet an approved project metric. Common examples include:

- failed requests divided by all requests;
- one status or cohort divided by the corresponding total;
- conversions divided by attempts;
- resource use divided by capacity;
- cost divided by events, users, or requests;
- one observed metric divided by another aligned observed metric.

The operands already have governed metric meaning and typed observation scope.
The missing operation is a deterministic, auditable ratio of those observations.

Without typed composition, the caller must choose between two false extremes:

1. edit the persistent semantic model and create a project-wide business object
   for a one-off investigation; or
2. call `frame.to_pandas()`, calculate the ratio manually, and permanently leave
   typed analysis, recovery, lineage, evidence, and downstream operators.

The first pollutes the durable model and introduces unnecessary coordination.
The second discards information Marivo already possesses.

### Arbitrary derive is not the solution

The removed `derive_metric_frame` and promotion surfaces accepted caller-built
Ibis, pandas, or SQL-shaped results and attached canonical frame metadata after
the fact. Marivo could validate columns and types but could not prove that the
query implemented the claimed metric, window, grain, slices, composition, fold,
or re-aggregation semantics.

Typed metric composition has a different trust boundary:

```text
arbitrary data + caller-authored metadata -> canonical frame      rejected
canonical frames + registered closed operator -> canonical frame accepted
```

Marivo owns the operation and derives all output metadata. The caller supplies
operands, a closed policy value, an optional display label, and an analysis
purpose; the caller cannot supply identity, unit, additivity, axes, composition,
coverage, or canonicality claims.

### `MetricFrame` currently conflates identity with a catalog metric id

Current frame, component, compare, correlate, and evidence paths commonly assume
that every arity-one `MetricFrame` has a non-null catalog `metric_id`. That is too
narrow once a registered analysis operator can produce a metric-shaped canonical
artifact.

Filling `metric_id` with an invented name such as `failure_rate` would be semantic
laundering: downstream evidence could merge unrelated same-named expressions and
catalog lookup could appear to succeed conceptually when no catalog object exists.

The identity contract must represent both catalog metrics and session
compositions explicitly.

### Legal frames need capability parity, not origin-based allowlists

A legal `MetricFrame` is not universally accepted by every operator. Cumulative,
non-additive, multi-metric, misaligned, incomplete, or unreplayable frames already
have capability-specific restrictions. Those restrictions are properties of
persisted semantics and current state.

Session composition must follow the same model. A downstream operation may reject
a composed frame because its shapes differ, its expression identities differ,
its component path is incomplete, its coverage is insufficient, or its replay
descriptor cannot materialize a requested axis. It must not reject the frame
merely because `identity.kind == "session_composition"`.

## Goals

1. Keep common one-off ratios inside typed, recoverable Marivo analysis.
2. Produce a canonical `MetricFrame` with truthful identity, composition,
   components, unit, additivity, coverage, lineage, and replay metadata.
3. Give catalog-derived and session-composed ratio frames the same finite ratio
   evaluation and downstream capability behavior when their persisted semantics
   match.
4. Preserve the distinction between computational canonicality and governed
   business authority.
5. Keep arbitrary SQL, Ibis, pandas, Python callbacks, and terminal results out
   of typed analysis.
6. Make reruns deterministic and content-addressed across script turns.
7. Support phased delivery without making a permanently reduced frame subtype.
8. Keep the public surface small, grouped, and discoverable.

## Non-goals

This design does not add:

- a dynamic semantic catalog or runtime `MetricRef`;
- automatic semantic authoring or promotion;
- a generic `metric(...)`, `derive(...)`, `formula(...)`, or `expression(...)`
  entry point;
- operator overloading on frames (`+`, `-`, `*`, `/`);
- arbitrary Ibis, pandas, SQL, NumPy, or callback inputs;
- pre-materialization expression planning or query pushdown;
- user-authored unit, additivity, axes, identity, or composition metadata;
- nested non-linear composition;
- scalar literals, conditionals, clipping, logarithms, window functions, lag
  expressions, or free-form functions;
- catalog discovery, readiness, owner assignment, or project-wide evidence
  authority for a session composition;
- cross-session frame consumption;
- automatic equivalence between a session expression and a later catalog metric;
- a global cleanup of catalog `weighted_average`, `linear`, or other composition
  evaluators outside ratio.

## Vocabulary

### Catalog metric

A project-level semantic metric with a stable `MetricRef`, explicit Python source,
domain ownership, business context, verification/readiness behavior, and a
definition that can be materialized under new legal scopes.

### Session composition

A typed deterministic expression over canonical `MetricFrame` artifacts owned by
one analysis session. It is persisted and recoverable inside that session but is
not a project semantic object.

### Canonical analysis artifact

An artifact produced by a registered Marivo operator whose inputs, computation,
metadata derivation, identity, persistence, and evidence commit are controlled by
Marivo. Both catalog observations and session compositions can be canonical.

### Semantic authority

The provenance of business-object authority:

- `catalog`: the identity resolves to a governed semantic object;
- `session_composition`: the identity resolves to a persisted expression over
  typed artifacts in one session.

Semantic authority does not determine computational capability.

## Architecture Overview

```text
                          project semantic authority
                                    |
CatalogMetricIdentity ---- session.observe -------------------+
                                                               |
                                                               v
                                                        MetricFrame
                                                               |
                                                               | eligible catalog operands only
                                                               v
                                            session.compose.ratio
                                                               |
                                                               v
                                      MetricFrame[session composition]
                                                               |
                         +----------------+----------------------+----------------+
                         |                |                      |                |
                      compare         correlate              transform        quality
                         |                |                      |                |
                       delta         association              frame            report
                         |
                      attribute

frame.to_pandas --------------------------------------------------> terminal data
md.raw_sql -------------------------------------------------------> terminal data
terminal data ------------------------------------------------X--> typed artifacts
```

`session.compose.ratio` belongs to the `artifact_production` capability group.
`observe` remains the sole producer of an **initial** `MetricFrame` from semantic
inputs. Composition is the only additional producer authorized by this design.
It consumes only existing canonical `MetricFrame`s that retain catalog metric
identity and satisfy the closed operand-value-semantics contract below. The
output does not loop back into composition.

## Public Surface

### Namespace

The session exposes a grouped namespace:

```python
session.compose.ratio(...)
```

Grouping avoids adding one flat Session method per future admitted composition
kind and gives live help one bounded family target:

```text
mv.help("compose")
mv.help("compose.ratio")
```

No module-level constructor builds a detached expression. Composition always
happens in an owning session because persistence, frame ownership, recovery, and
evidence are part of the operation.

### Ratio signature

```python
session.compose.ratio(
    numerator: MetricFrame,
    denominator: MetricFrame,
    *,
    label: str | None = None,
    zero_division: Literal["null", "error"] = "null",
    analysis_purpose: str | None = None,
) -> MetricFrame
```

`label` is bounded display metadata. It is excluded from expression identity and
must not be accepted where a catalog id or `MetricRef` is required.

`zero_division="null"` produces a null result for a present zero denominator and
records the affected-row count in quality metadata. `"error"` fails without
persisting a result when any aligned denominator is zero. There is no `"zero"`,
infinity, fallback literal, or arbitrary fill policy.

### Shared ratio evaluation contract

Catalog `ms.ratio(...)` observation and `session.compose.ratio(...)` use one
versioned `RatioEvaluationV1` implementation, persisted as
`value_evaluator_contract="ratio-evaluation/v1"`. The contract is:

- align component rows over the union of complete typed keys;
- preserve an absent component as null, never as inferred zero;
- produce null when either operand is absent or null;
- treat a present zero denominator according to the zero-division policy;
- never persist positive or negative infinity;
- emit the same per-key presence facts and aggregate quality counters.

Catalog ratios use the fixed `zero_division="null"` policy because the current
semantic authoring surface does not declare a policy. A session ratio with that
policy is therefore numerically equivalent when its operands and scopes match.
`zero_division="error"` is an explicitly stricter session materialization policy
and remains part of expression identity.

The catalog path already evaluates division compositions under the fixed
`zero_division="null"` policy: a present zero denominator yields null (never
infinity), the affected-row count is persisted as
`quality_summary.zero_denominator_rows`, and the policy participates in observe
artifact identity so cached frames computed under the older bare-division
semantics are never reused. The Phase 1 cutover replaces that path's outer-merge
division with this shared evaluator, adding the remaining per-key presence facts
and role-specific missing-key counters. The evaluator contract version
participates in artifact identity so pre-cutover cached catalog-ratio artifacts
cannot be reused as if they satisfied V1.

This is an operator-local ratio cutover, not a global derived-metric evaluator
cleanup. Catalog `weighted_average` remains on its existing value/weight
evaluator, and catalog `linear` remains on its existing evaluator; neither is
routed through `RatioEvaluationV1` by this design. Their missing, zero, and
non-finite behavior requires a separate review and contract. Consequently,
“never persist infinity” is a guarantee of `RatioEvaluationV1`, not yet a claim
about every catalog composition kind.

### Type algebra

```text
MetricFrame, MetricFrame -> compose.ratio -> MetricFrame
```

The capability registry owns the signature, accepted family, output family,
constraints, help target, and artifact affordances. `MetricFrame.contract()`
derives downstream affordances from the same registry and the frame's persisted
state.

## Metric Identity

### Discriminated identity

Replace catalog-only identity assumptions with a closed union:

```python
MetricIdentity = CatalogMetricIdentity | SessionCompositionIdentity

class CatalogMetricIdentity:
    kind: Literal["catalog_metric"]
    metric_ref: MetricRef

class SessionCompositionIdentity:
    kind: Literal["session_composition"]
    operator: Literal["ratio"]
    expression_fingerprint: str
```

The optional display label is stored beside the identity in frame/result
metadata. It is never part of identity equality or canonical keys.

Every arity-one `MetricFrame`, `DeltaFrame`, `ComponentFrame`, and relevant
metric-bearing result carries a `MetricIdentity`. Metric-bearing evidence
subjects carry the matching identity; evidence such as an association subject
may remain explicitly metric-less. Multi-metric frames continue to carry an
ordered collection of identities.

Persisted frame schemas store the discriminated value, not an ambiguous string.
Public convenience accessors may expose `metric_ref` or catalog `metric_id` only
for `CatalogMetricIdentity`; they return `None` for session composition. Internal
operators use `MetricIdentity`, never a fabricated catalog id.

### Expression fingerprint

The expression fingerprint identifies the calculation independently of one
materialization window:

```text
sha256(
  schema_version,
  ratio_evaluation_contract_version,
  operator,
  numerator.value_semantics_fingerprint,
  denominator.value_semantics_fingerprint,
  zero_division,
  derived unit/additivity contract
)
```

It excludes:

- `label`;
- `analysis_purpose`;
- input artifact refs;
- row values;
- current/baseline window bounds;
- creation time;
- session id.

The exact expression schema is versioned. Definition-affecting changes bump the
schema version and therefore the fingerprint.

### Operand value semantics

`MetricIdentity` alone does not prove the values carried by an operand. A legal
frame may have passed through normalization, rollup, slicing, or another
family-preserving transform while retaining the same catalog metric identity.

Every composition operand therefore exposes a stable
`value_semantics_fingerprint` derived from the following closed descriptor. This
is the complete v1 field set; implementations must not interpret “all relevant
metadata” or another open-ended bag as part of identity:

```text
CatalogOperandValueSemanticsV1:
  schema: "catalog-operand-value-semantics/v1"
  metric_identity: CatalogMetricIdentity
  catalog_definition_fingerprint: str
  selected_measure: str
  normalized_slice: ordered map[DimensionRef, TypedSlicePredicateV1]
  aggregation: closed aggregation descriptor
  fold: closed fold descriptor | null
  cumulative: closed cumulative descriptor | null
  unit_contract: versioned derived unit descriptor
  additivity_contract: versioned additivity/reaggregation descriptor
  component_contract_version: str
  transforms: ordered list[OperandTransformSemanticsV1]
```

Materialization scope is a separate closed descriptor:

```text
OperandMaterializationScopeV1:
  axes: ordered list[(DimensionRef | TimeDimensionRef, role)]
  grain: str | null
  report_timezone: str | null
  window: normalized half-open bounds
  semantic_model: str
  datasource_compatibility_domain: str
  snapshot_and_coverage_refs: ordered list[str]
```

`TypedSlicePredicateV1` is a closed `{op, operand}` value. `op` is one of
`==`, `!=`, `in`, `>`, `>=`, `<`, `<=`, or `between`; `operand` preserves the
typed scalar or ordered collection required by that operator. Shorthand scalar,
range, and set inputs are normalized to this form before fingerprinting. Thus
`state == "FAILED"` and `state != "FAILED"` can never share a fingerprint.

For this design, `datasource_compatibility_domain` v1 is the canonical
datasource semantic id resolved by the observe plan. It is persisted on the
operand scope when the frame is produced and compared by exact equality. A
matching backend type, connection kind, or DSN does not make two datasource ids
compatible, and compose/recovery must not reconstruct the value heuristically.
This intentionally conservative v1 definition can only be widened by the
separate plural-source design checkpoint.

Scope participates in operand compatibility, artifact identity, coverage, and
replay, but not expression identity. This is what lets the same ratio expression
be materialized in a different window or replayed with an additional dimension
without changing its expression fingerprint. A `transform.rollup` remains
definition-changing because it changes the persisted value-producing operation,
not merely the requested observation scope.

Canonical serialization sorts map keys, preserves transform order and typed
scalar distinctions, and rejects unknown fields. The fingerprint is SHA-256 over
that versioned canonical serialization. It does not use `default=str`, Python
callable names, reprs, or runtime object addresses.

The following table is the exhaustive transform/projection admission table for
this design:

| Value-producing path | Operand eligibility | Fingerprint/replay treatment |
| --- | --- | --- |
| direct `session.observe` of one catalog metric | Phase 1 | Source descriptor above. |
| `observe(..., slice_by=...)` | Phase 1 | Normalized typed slice is definition-changing and included. |
| `observe` window bounds | Phase 1 | Scope-only: excluded from value fingerprint, retained in artifact identity/alignment/replay scope. |
| `observe` axes, grain, and report timezone | Phase 1 | Scope-only: excluded from value fingerprint, required to match for direct composition and retained for replay. |
| arity-one typed metric projection from an observed multi-metric frame | Phase 3 | Included as a closed selected-metric projection; requires a projection replay descriptor. |
| `transform.window` | Phase 3 | Scope-only: bounds excluded from value fingerprint; ordered window replay remains persisted. |
| `transform.slice` | Phase 3 | Membership-changing: normalized dimension refs and typed predicates are included and replayed. |
| `transform.rollup` | Phase 3 | Value-changing: target grain/drop axes and the proven fold/reaggregation contract are included and replayed. Ineligible when the fold is unknown. |
| `transform.normalize` (`index`, `share`, `pct_change`, `per_unit`, `z_score`) | ineligible | Value-changing and partly data-dependent; no normalization descriptor is admitted by this design. |
| `transform.filter` | ineligible | Accepts a callback and cannot be canonically serialized or replayed. |
| `transform.topk` / `bottomk` | ineligible | Data-dependent membership selection. |
| `transform.rank` | ineligible | Adds a data-dependent measure and makes selected-measure semantics ambiguous at this boundary. |
| any `SessionCompositionIdentity` frame | ineligible | Nested composition, including ratio-of-ratio, is outside this design. |

For admitted paths, the descriptor includes definition-changing facts only.
Materialized row values, artifact refs, all materialization-scope fields,
creation time, session id, display labels, and analysis purpose are excluded.
Exact input artifact refs and scope still participate in output artifact identity
and alignment validation.

This table is closed under change: a new transform, transform mode, or projection
is operand-ineligible until the registry adds a typed descriptor variant, replay
behavior, canonicalization tests, and an explicit row here. Silent inheritance of
operand eligibility is forbidden.

A `MetricFrame` is ratio-operand eligible only when its identity is
`CatalogMetricIdentity` and every value-producing step appears in the admitted
table. This is an operator-specific algebra rule, not a blanket downstream
origin restriction: a session-composed output remains legal for ordinary
downstream capabilities but cannot become another ratio operand.

### Artifact identity

The output artifact id remains content-addressed through the ordinary commit
path. It includes:

- numerator and denominator artifact refs;
- operation parameters;
- expression fingerprint;
- alignment contract;
- output metadata contract version.

Two windows of the same ratio share an expression fingerprint but have different
artifact ids. Re-running the same operation over the same inputs produces the
same prospective artifact id and can reuse the persisted frame.

### Equality by operation

- `compare` requires equal `MetricIdentity` plus equal
  `ComparableValueSemanticsV1`. For session compositions, identity equality
  means equal operator and expression fingerprint; labels are irrelevant.
  `ComparableValueSemanticsV1` is separate from ratio operand admission and is
  available for catalog and session frames. It canonically includes selected
  measure, normalized slice predicates, aggregation, fold, cumulative, unit,
  additivity, normalization, `value_evaluator_contract`, and every admitted
  post-production transform that changes values or membership. A normalized
  frame is comparable only with a frame carrying the exact same normalized
  normalization descriptor; normalized versus raw is rejected. A pre-V1 catalog
  ratio is normalized to `value_evaluator_contract="catalog-ratio/pre-v1"` and
  is never comparable with `"ratio-evaluation/v1"`. Callback/data-dependent
  transforms (`filter`, `topk`/`bottomk`, and `rank`) are compare-ineligible until
  a later design defines a deterministic equality descriptor. This is a Phase 2
  catalog-side behavior change, not part of the zero-behavior-change Phase 0
  cutover.
- `correlate` and paired hypothesis testing allow different identities subject to
  their existing shape/alignment constraints.
- evidence never merges a session composition with a catalog metric merely
  because their labels or rendered formulas match.

## MetricFrame Contract

### Canonicality

A successful ratio result has:

```text
kind: metric_frame
is_canonical: true
semantic_authority: session_composition
metric_identity: SessionCompositionIdentity(...)
```

It is not an `ExplorationResult`, terminal result, preview, candidate, or proposed
semantic object.

### Derived metadata

Marivo derives, and the caller cannot override:

- output shape and axes;
- output window and grain;
- common output materialization scope plus separate normalized numerator and
  denominator slice/scope descriptors;
- output unit;
- additivity and re-aggregation policy;
- composition kind and component roles;
- component sidecar identity;
- coverage and quality summaries;
- lineage and replay descriptor;
- metric identity and artifact identity;
- semantic source set;
- blocking issues and affordances.

### Unit

Ratio unit follows the same deterministic unit algebra as catalog ratios:

- equal known units produce `"1"`;
- known numerator and denominator units produce a quotient unit;
- unknown operands produce an unknown output unit and a richness/quality caveat,
  not a guessed unit;
- no caller-supplied override is accepted.

### Additivity and re-aggregation

A ratio is non-additive and not directly reaggregatable. The output carries
component-aware ratio semantics so operations that know how to recompute from
numerator and denominator components may do so. An operator must not sum or
average ratio values as a substitute for component recomputation.

### Semantic sources

The output records the ordered, deduplicated set of catalog models, datasources,
and metric identities represented by its operands. A legacy singular
`semantic_model` field is insufficient for a composition over different sources;
the persisted contract must expose a plural typed source descriptor before
cross-model composition is admitted.

Semantic sources are lineage, not authority for a new catalog object.

## Input and Alignment Contract

### Base input requirements

Both operands must be:

- `MetricFrame` instances with `CatalogMetricIdentity`;
- arity one;
- owned by the active session;
- persisted and recoverable;
- produced through one of the explicitly admitted value-producing paths in the
  closed table above;
- backed by the corresponding stable value-semantics fingerprint and typed
  replay descriptor;
- numeric in their selected measure column;
- free of blocking issues that invalidate the requested calculation.

Terminal DataFrames, raw SQL results, manually constructed frames, foreign-
session frames, and multi-metric frames are rejected before computation.
Callback-derived or otherwise non-serializable typed frames fail the explicit
composition-operand eligibility precondition. A session-composed frame also
fails this operator-specific precondition even though it is a legal canonical
`MetricFrame` for ordinary downstream analysis.

### Shape compatibility

The operands must have the same semantic shape:

- scalar with scalar;
- time series with time series;
- segmented with segmented;
- panel with panel.

They must also have the same normalized output axes, grain, report timezone, and
window. Ratio composition does not perform calendar alignment, current/baseline
alignment, resampling, rollup, or implicit broadcasting.

Callers use ordinary typed operations first when scopes differ. Composition is
not a hidden planner.

### Key alignment

Alignment uses the complete persisted axis key:

- scalar: one row per operand;
- time series: time bucket key;
- segmented: ordered dimension key set;
- panel: time bucket plus ordered dimension key set.

Duplicate keys fail closed. Column position is never treated as semantic
alignment when keys exist.

### Missing keys and empty-value semantics

An outer key mismatch is not automatically a zero. A missing numerator bucket
may mean zero matching events, incomplete source coverage, a dropped null group,
or a failed observation.

Ratio alignment therefore uses the union of both complete typed key sets. On an
asymmetric key, the absent operand stays null, the output value is null, and the
row remains present in both the `MetricFrame` and `ComponentFrame`. The persisted
component row records numerator-present and denominator-present facts; the
quality summary separately counts missing-numerator keys and missing-denominator
keys. Coverage for that key is incomplete or unknown according to the available
operand coverage, never complete by construction.

This is missingness preservation, not empty-value inference. The implementation
does not fill, impute, drop, or interpret the absent value as zero, including for
count metrics. It also does not fail the whole ratio merely because the key sets
differ. `CompositionKeySetMismatchError` is therefore not part of the V1 public
contract; incompatible key *schemas*, duplicate keys, or unalignable typed key
values remain errors.

Safe zero-fill or another metric-specific empty-value policy is not specified or
authorized by this document. Before a later phase may admit one, a separate
reviewed design must define the semantic-authoring assertion, observe-time
persistence, scope/coverage proof, composition propagation, user-visible
disclosure, and migration contract. No implementation may infer an empty value
from metric name, dtype, aggregation kind, or observed data.

### Null values

Existing null operand values remain null. Ratio composition does not impute,
forward-fill, interpolate, or drop rows silently. The output quality summary
records source nulls, key-absence nulls by operand role, and nulls produced by
zero denominators separately.

### Cross-model and cross-datasource operands

Cross-model and cross-datasource composition is not authorized by this document.
It remains a possible Phase 4 extension only after a separate reviewed design
specifies at least:

- typed axes and normalized keys match exactly;
- report timezone, grain, and window match;
- each operand is independently canonical and recoverable;
- source/snapshot/coverage metadata is retained as a plural input set;
- the fail/allow rule when common-snapshot consistency cannot be proven.

Until that design amends the contract, both operands must resolve to the same
semantic model and datasource compatibility domain and failures are structured.
The restriction is about unproved source compatibility, not frame origin.

## Component Contract

Every composed ratio persists a `ComponentFrame` with:

- numerator and denominator columns;
- per-key numerator-present and denominator-present facts;
- output value column;
- complete output axes;
- operand `MetricIdentity` values;
- operand artifact refs;
- operand units, additivity, aggregation, folds, cumulative descriptors, and
  coverage refs;
- normalized operand scopes/filters;
- composition kind and schema version.

The component sidecar is part of the successful operation. A ratio frame is not
committed as fully successful and later patched opportunistically with
components. Either the output and sidecar commit atomically, or the operation
fails/reports an incomplete artifact according to the ordinary persistence
contract.

Component metadata is self-contained. Compare and attribute do not query the
catalog to reconstruct semantics that may have changed after composition.

## Replay Contract

### Why replay is required

Some downstream operations consume only existing rows. Others, notably
attribution over a missing axis, replay source observations with additional
dimensions before recomputing the result. A session-composed ratio cannot be
fully equivalent to a catalog-derived ratio without composition-aware replay.

### Typed replay descriptor

Persist a closed replay tree:

```text
CatalogOperandReplay =
  ObserveReplay
  | MetricProjectionReplay(parent: CatalogOperandReplay)
  | WindowReplay(parent: CatalogOperandReplay)
  | SliceReplay(parent: CatalogOperandReplay)
  | RollupReplay(parent: CatalogOperandReplay)

RatioReplay:
  expression_fingerprint
  numerator: CatalogOperandReplay
  denominator: CatalogOperandReplay
  zero_division
  output contract version
```

`ObserveReplay` remains the terminal typed descriptor for an original
observation. Wrapper variants are admitted only with their matching row in the
closed operand table. `RatioReplay` cannot appear under either operand, so the
replay type makes nested composition unrepresentable. No variant contains an
arbitrary callable or source code.

### Replay operations

When an operator requests a compatible new scope, grain, or dimension set:

1. validate that the requested change is legal for both operand replay trees;
2. replay numerator and denominator independently through ordinary typed
   observation/replay contracts;
3. rerun `compose.ratio` over the replayed frames;
4. verify the resulting expression fingerprint matches the source expression;
5. continue the downstream operator.

If either operand cannot replay the requested axis or scope, the downstream
operation fails with the existing missing-axis/path repair vocabulary extended
with the exact failing operand. It does not fall back to raw SQL or pandas.

## Coverage and Quality

The output coverage contract is derived from both operands:

- output coverage cannot be stronger than either operand coverage;
- per-key coverage uses the conservative intersection/minimum appropriate to the
  persisted coverage kinds;
- a missing or incompatible coverage basis is visible as unknown, not complete;
- zero-denominator rows, source-null operands, missing-numerator keys,
  missing-denominator keys, and snapshot-source caveats are counted separately;
- the component sidecar retains operand coverage refs for diagnosis.

`assess_quality` accepts the composed frame exactly as it accepts any legal
`MetricFrame`. It reads persisted metadata and rows; it does not downgrade the
result solely because its authority is session-scoped.

## Evidence Contract

### Typed metric subject

`Subject.metric: str | None` becomes
`Subject.metric_identity: MetricIdentity | None`, with the non-null field
represented by a discriminated metric subject parallel to frame identity:

```text
MetricSubject = CatalogMetricSubject | SessionCompositionSubject
```

The session-composition subject contains:

- expression fingerprint;
- operator kind;
- operand metric identities;
- owning session id.

Display label remains artifact presentation metadata and is not part of either
subject variant. Session-composition canonical keys use a versioned v2
projection containing operator, expression fingerprint, operand identities, and
owning session id. Catalog-subject canonical keys use the legacy catalog
projection described in the persistence migration below so existing evidence
keys remain stable.

### Seeding and aggregation

Evidence produced from a session composition is valid evidence about the
composed artifact and expression in that session. It may support correlations,
hypothesis tests, deltas, forecasts, quality assessments, and attributed results
with complete lineage.

It is not automatically project-wide evidence about a governed business metric.
The evidence store must not merge it with:

- a same-labeled composition from another session;
- a same-labeled catalog metric;
- a later catalog metric that happens to use an equivalent-looking formula.

Cross-session/project evidence consolidation requires a catalog metric identity
or a separately designed equivalence/approval boundary. This design adds no such
boundary.

### Findings remain factual

The expression label and analysis purpose do not become business definitions or
conclusions. Findings state the computed expression identity, inputs, scope,
quality, and result. The agent remains responsible for interpreting whether the
ratio is meaningful.

### Human caliber closeout

Session composition intentionally avoids catalog authoring, but it must not hide
the business choice that authoring would have grilled. Whenever a material claim
or report uses a composed metric, the analysis closeout must disclose:

- numerator catalog identity and normalized slice/scope;
- denominator catalog identity and normalized slice/scope;
- `zero_division` policy and affected-row count;
- missing-numerator and missing-denominator key counts;
- display label, if present, clearly marked non-authoritative;
- `semantic_authority=session_composition` and the owning analysis scope;
- relevant coverage, null, source, and quality caveats.

The closeout asks the human to judge whether that caliber is meaningful for the
decision at hand. Repeated use or a need for governed reuse routes to explicit
semantic authoring; Marivo does not infer promotion. The implementation cutover
must add this obligation to `marivo-analysis` without turning the skill into an
operator manual or duplicating live help.

## Downstream Capability Contract

### Origin-blind rule

No downstream analysis capability consuming the ratio result may use
`metric_identity.kind` as a blanket allow/deny rule. It validates the same
semantic facts for every `MetricFrame`. The catalog-identity check at the
`compose.ratio` operand boundary is the explicit operator-algebra exception
defined above; it cannot leak into consumers of the output.

| Capability | Final behavior for a session-composed ratio |
| --- | --- |
| `show` / `render` | Display expression identity, label, formula roles, scope, authority, quality, and caveats. |
| `contract` | Derive ordinary affordances from shape and persisted semantics; disclose session-scoped authority separately. |
| `components` | Return the persisted numerator/denominator component sidecar. |
| `coverage` | Return composed coverage when present. |
| transforms | Apply the same transform-specific preconditions as any ratio `MetricFrame`. |
| `correlate` | Accept when shapes/alignment satisfy the existing contract; identities may differ. |
| `hypothesis_test` | Accept under the existing paired-frame constraints. |
| `forecast` | Accept when the frame has a supported time shape, continuity, and no existing forecast blocker. |
| `assess_quality` | Accept as an ordinary metric frame. |
| discover objectives | Accept according to the same shape and quality gates as any metric frame. |
| `compare` | Accept when both frames have equal expression identity, equal `ComparableValueSemanticsV1`, and satisfy ordinary alignment/cumulative constraints. |
| `attribute` | Use ratio-mix attribution when component semantics are complete; materialize missing axes through composition replay. |
| `to_pandas` | Remain the one-way terminal exit. |

If a phased implementation has not yet delivered a required replay or metadata
capability, the frame contract exposes that precise missing capability as a
precondition/blocking issue. `origin=session_composition` is never the reason.

## Supported End-State Use Cases

The final architecture supports:

1. **Conditional rates** — filtered count/sum numerator divided by the matching
   unfiltered total; buckets absent from one observation remain visible with a
   null result and explicit missing-component quality facts.
2. **Cohort or status shares** — one typed slice divided by its compatible total.
3. **Conversion and failure rates** — distinct governed event metrics composed
   over matching scopes.
4. **Per-unit metrics** — cost, bytes, duration, or revenue divided by a compatible
   count/capacity metric with derived unit metadata.
5. **Scalar, time-series, segmented, and panel ratios** — typed-key union
   alignment with no positional guessing, silent row loss, or inferred zero.
6. **Exploratory association and testing** — correlate or hypothesis-test a
   composed ratio against another typed metric.
7. **Period comparison** — compose the same expression over two windows, compare
   by expression identity, and persist a component-aware delta.
8. **Ratio-mix attribution** — attribute a composed ratio delta using persisted
   numerator/denominator components.
9. **Missing-axis attribution** — replay both operands with the requested axis,
   recompose, and then attribute.
10. **Discovery, forecasting, transforms, and quality assessment** — wherever the
    ordinary shape/quality contract permits them.
11. **Cross-script continuation** — reload the composed frame, component sidecar,
    replay tree, and evidence without reconstructing Python closures.

## Explicitly Unsupported End-State Use Cases

The final architecture does not support:

1. creating a project KPI, dashboard metric, alert contract, or reusable
   `MetricRef` without semantic authoring;
2. naming a composition and treating the label as business authority;
3. browsing session compositions through the semantic catalog;
4. running readiness, preview, or parity on a session expression as though it
   were a semantic object;
5. consuming raw SQL, pandas, Ibis, NumPy, arbitrary frames, or callbacks;
6. automatic promotion or equivalence to a catalog metric;
7. arbitrary arithmetic or a free-form expression tree;
8. any composed frame as a composition operand, including ratio-of-ratio;
9. implicit resampling, broadcasting, calendar alignment, join-path discovery,
   or missing-axis guessing inside composition;
10. silent zero fill, null imputation, infinite values, silent key dropping, or
    caller-authored unit and additivity claims;
11. cross-session frame composition;
12. project-wide evidence aggregation under a session expression identity;
13. missing-key zero-fill or cross-model/cross-datasource composition unless a
    later, separately reviewed design explicitly admits it.

Typed frames whose value semantics or replay path cannot be serialized
deterministically are also ineligible as operands, even though they remain legal
for their existing non-composition capabilities.

## Catalog Promotion Boundary

Promotion is a human/agent semantic-authoring decision, not a runtime operation.
A composition that becomes reusable should be explicitly authored through the
ordinary semantic workflow, for example as `ms.ratio(...)` over approved metric
components, then verified, previewed when required, and made analysis-ready.

The session artifact can provide lineage evidence to the authoring decision, but
Marivo does not copy its label, formula, unit, or business meaning into semantic
source automatically. The new catalog metric has a new catalog identity. No
alias/equivalence relationship is inferred.

## Extension Rules for Future Composition Operators

A future operator may join `session.compose` only when all of the following are
specified and mechanically enforceable:

1. closed typed operand families;
2. one fixed output artifact family;
3. deterministic value computation;
4. deterministic unit algebra;
5. deterministic additivity/re-aggregation semantics;
6. complete component representation;
7. exact identity and fingerprint rules;
8. exact key/alignment and missing-value rules;
9. coverage/quality propagation;
10. typed replay behavior;
11. downstream capability behavior;
12. evidence subject and seeding behavior;
13. bounded live help and structured errors;
14. no arbitrary data or caller-authored semantic metadata.

`linear` may be considered later for sums/differences of commensurable additive
frames, with flattening rather than nested linear trees. Weighted-average naming
is not admitted merely because existing internals divide two component columns;
its operand meaning must be unambiguous at the public boundary. Generic multiply,
divide trees, scalar functions, and conditionals are not candidates under this
design.

## Persistence and Recovery

The persisted schema includes:

- `MetricIdentity`;
- composition descriptor and schema version;
- expression fingerprint;
- operand artifact refs and identities;
- operand value-semantics fingerprints;
- comparable value-semantics descriptor/fingerprint;
- ratio evaluation contract version;
- component sidecar ref;
- replay descriptor;
- plural semantic source descriptor;
- derived unit/additivity/reaggregation facts;
- alignment, coverage, and zero-division facts;
- ordinary lineage, quality, blocking, artifact state, and evidence summary.

Recovery validates the persisted schema and referenced artifacts. Missing or
incompatible operand/component/replay state fails with a structured recovery
error; it does not fabricate a partial catalog identity or silently downgrade to
a terminal DataFrame.

### Phase 0 migration policy

The identity-only cutover preserves existing project-local sessions. Deleting
old session state or silently treating it as unreadable is not an accepted
migration strategy.

Frame metadata loaders recognize the legacy catalog-only `metric_id` shape and
normalize it in memory to `CatalogMetricIdentity(metric_ref=...)`. Newly written
or rewritten frame metadata uses only the discriminated identity schema. This
compatibility decoder is one-way: no new writer emits the legacy shape, and an
invalid or ambiguous legacy id fails with a structured recovery error.

Evidence persistence uses this exact migration:

1. bump `judgment.db` `PRAGMA user_version` from 1 to 2;
2. in one immediate SQLite transaction, rewrite legacy
   `artifacts.subject_payload` and `findings.subject_payload` values from
   `metric: str | null` to the v2 discriminated subject representation and bump
   the affected payload schema version;
3. define the v2 canonical-key projection for a catalog subject as the exact
   legacy payload `{metric, entity, slice, grain, analysis_axis}` with the same
   canonical JSON and null handling;
4. before commit, compute both the legacy and v2 catalog-key projections for
   every migrated subject payload, require byte-for-byte equality, and verify
   each stored proposition subject key that resolves through those findings
   remains present in the migrated key set;
5. leave `propositions.subject_key`, `proposition_id`, assessment snapshot ids,
   assessment edges, finding ids, and artifact ids unchanged;
6. roll back the whole database migration and raise `MigrationFailedError` on
   any parse, identity, or key-stability failure.

Because catalog canonical keys remain byte-for-byte stable, existing knowledge
lookups and assessment graphs continue to resolve without a cascading primary-
key rewrite. Session-composition subjects have no v1 predecessor and use the new
session-scoped v2 key projection. A successfully migrated old session opens
normally; a corrupt v1 session fails closed with a repair that names the path and
preserves the original database for recovery. Higher unknown schema versions
continue to raise `SchemaVersionMismatchError`.

### Phase 2 comparable-semantics derivation for existing frames

Phase 2 does not require Phase 0/1 artifacts to already contain a persisted
`ComparableValueSemanticsV1`. On recovery or first compare, the loader derives it
deterministically from immutable persisted metadata: metric identity, selected
measure, normalized slice, aggregation, fold, cumulative, unit, additivity,
normalization, composition metadata, transform lineage, and the persisted or
derivable evaluator contract.

The derivation is closed and conservative:

- a Phase 1 ratio carrying the shared evaluator version derives
  `value_evaluator_contract="ratio-evaluation/v1"`;
- an older catalog ratio with no evaluator version derives
  `value_evaluator_contract="catalog-ratio/pre-v1"`, never V1 by assumption;
- ordinary non-ratio frames derive the exact registered producer/evaluator
  contract for their persisted frame schema;
- a callback/data-dependent transform or any missing/ambiguous required fact has
  no comparable descriptor.

A frame without a derivable descriptor remains readable for its otherwise legal
capabilities, but `compare` rejects it with a structured value-semantics
incompatibility. New Phase 2 writers persist the derived descriptor and
fingerprint; existing artifacts need no eager rewrite or bulk migration.

## Errors

Composition errors remain structured `AnalysisError` subclasses or registered
variants with stable fields and typed repair. Required categories include:

- input not a `MetricFrame`;
- multi-metric operand;
- cross-session operand;
- unsupported composition operand semantics;
- shape/axis/grain/window/report-timezone mismatch;
- duplicate keys, incompatible key schemas, or unalignable typed key values;
- denominator zero under `zero_division="error"`;
- incompatible or unknown unit contract when the operation requires certainty;
- input blocking issue;
- component persistence failure;
- replay unavailable/incompatible;
- semantic-model or datasource compatibility-domain mismatch;
- persisted expression schema mismatch.

Errors state expected, received, location, and one concrete repair grounded in
the current operands. They do not recommend raw SQL promotion or invent a
semantic metric.

Missing-key rows are valid V1 results, not errors. Their dynamic guidance lists
the affected keys and operand role, then points to source/coverage inspection or
an explicitly catalog-authorized dense/empty-value semantic contract if the
analyst needs a numeric value. It must never recommend narrowing the window or
dropping to the shared key subset: that would silently change the population and
can bias a conditional rate. Key-schema errors follow the same repair vocabulary
and likewise cannot suggest intersecting away unmatched keys.

## Live Help and Agent Surface

The active live surface adds:

```text
Artifact production:
  session.observe(...)
  mv.help("compose")

Type algebra:
  MetricFrame, MetricFrame -> compose.ratio -> MetricFrame
```

Focused `compose.ratio` help is self-contained for one correct call and teaches:

- operand-eligible catalog-identity inputs only;
- same-session and compatibility requirements;
- composed and ineligible transformed frames cannot be operands;
- label versus identity;
- zero-division behavior;
- canonical artifact versus semantic authority;
- result and component inspection;
- no raw-data re-entry or automatic promotion.

`MetricFrame.show()` and `.contract()` expose authority without ranking catalog
frames above session-composed frames. The packaged analysis skill retains the
boundary rule that registered typed composition stays in typed analysis, missing
governed business meaning returns to semantic authoring, and terminal data
cannot re-enter. Its closeout obligation additionally requires the bounded
caliber disclosure above; operator syntax and repair remain owned by live help.

## Telemetry

Register one operation identity:

```text
marivo.analysis.compose.ratio
```

Telemetry records bounded structural attributes such as shape, component source
count, zero-division policy, and success/error category. It
does not record labels, business definitions, raw values, filter values, full
expression descriptors, SQL, or metric contents.

## Phased Delivery

Phases are delivery boundaries, not permanent type tiers. Every phase keeps
artifacts truthful and exposes precise missing capability preconditions.

### Phase 0 — identity-only refactor with zero behavior change

- introduce discriminated metric identity across arity-one frames and evidence;
- normalize every existing producer and recovered legacy frame to
  `CatalogMetricIdentity`;
- replace internal catalog-string assumptions with typed identity matching or an
  explicit catalog-only accessor;
- migrate evidence subjects and `judgment.db` v1 to v2 using the key-preserving
  policy above;
- update frame/component/delta/result persistence and recovery round trips;
- keep `session.observe` as the sole `MetricFrame` producer and add no compose
  namespace, capability, help entry, telemetry event, or changed affordance;
- prove zero behavior change with public-surface snapshots, artifact recovery,
  compare/evidence round trips, and existing test suites.

Phase 0 is independently reviewable and releasable. Phase 1 does not begin until
all catalog-only behavior passes on the discriminated identity representation.

### Phase 1 — canonical ratio and ordinary consumption

- implement the closed `CatalogOperandValueSemanticsV1` descriptor for direct
  arity-one catalog observations, including `observe(slice_by=...)`;
- persist the exact v1 datasource compatibility domain from the observe plan;
- introduce the shared `RatioEvaluationV1` path for catalog and session ratios,
  with typed-key union alignment, missing-component nulls, finite output, and
  role-specific quality counters;
- add `session.compose.ratio` for same-session arity-one operands;
- support scalar/time-series typed-key union alignment within one semantic model
  and datasource compatibility domain;
- derive unit, additivity, components, lineage, coverage, and expression identity;
- persist/recover output, component sidecar, and replay descriptor schema;
- support show/contract/components/coverage, transforms, correlate,
  hypothesis-test, forecast, quality, and compatible discover paths;
- keep transformed and composed frames ineligible as ratio operands;
- preserve asymmetric-key rows as null; add no empty-value zero-fill;
- update `marivo-analysis` closeout to require composed-caliber disclosure;
- keep terminal boundaries unchanged.

### Phase 2 — compare and component-complete deltas

- add `ComparableValueSemanticsV1` for both catalog and session frames;
- deterministically derive it for readable Phase 0/1 artifacts from persisted
  metadata, treating an absent catalog-ratio evaluator version as
  `catalog-ratio/pre-v1` and failing compare when derivation is ambiguous;
- deliberately tighten catalog compare so normalized/raw, unequal slice,
  unequal rollup/fold/cumulative, and other unequal persisted value semantics
  are rejected; conservatively reject compare after callback/data-dependent
  transforms that have no deterministic equality descriptor;
- compare equal session-composition identities across different windows;
- propagate ratio components into `DeltaFrame` and evidence;
- enforce the same cumulative/component compatibility rules as catalog ratios;
- extend the Phase 0 typed-identity paths to session-expression equality in
  compare, cache, and evidence.

### Phase 3 — composition replay and attribution parity

- admit only the projection/window/slice/rollup operand paths enumerated in the
  closed table after their descriptor and replay tests pass;
- deliver segmented and panel typed-key union alignment before enabling replay
  that adds a dimension;
- replay operands under compatible added dimensions/scopes;
- recompose and verify expression identity;
- support missing-axis materialization;
- support ratio-mix attribution with the same persisted-semantic gate as catalog
  ratios.

### Phase 4 — downstream completeness and separate design checkpoints

- complete downstream capability parity and remove transitional restrictions;
- propose empty-value semantics separately; do not enable zero-fill without its
  approved cross-layer contract;
- propose plural-source and cross-model/cross-datasource composition separately;
  do not enable it without an approved snapshot/compatibility contract.

The Phase 4 label is not authorization for either deferred extension. Each needs
its own design approval and may remain unsupported without making the ratio
capability incomplete. In this checkpoint, “empty-value semantics” means a
semantic assertion that an absent key denotes a numeric value such as zero; it
does not include V1's lossless null preservation over the key union.

No phase adds arbitrary expressions, terminal-result re-entry, or semantic
promotion.

## Testing Strategy

### Identity and authority

- catalog and session identities round-trip through persistence;
- Phase 0 preserves all catalog-only observable behavior and affordance snapshots;
- every row in the closed operand-admission table has positive/negative
  fingerprint and replay tests;
- operand value-semantics fingerprints distinguish normalized slices,
  aggregation/fold/cumulative contracts, and admitted definition-changing
  transforms while ignoring axes, grain, timezone, window bounds, and
  display-only metadata;
- materialization-scope compatibility and artifact identity do distinguish axes,
  grain, timezone, window, source domain, and snapshot/coverage refs;
- golden fixtures pin the canonical bytes and SHA-256 values for every v1
  expression/value-semantics fingerprint; intentional changes require a schema
  version bump and new fixtures;
- unknown transform variants and all ineligible rows fail operand admission;
- labels do not affect expression or artifact identity;
- definition-affecting fields do affect expression identity;
- `ComparableValueSemanticsV1` distinguishes normalized/raw and unequal
  slice/rollup/fold/cumulative variants for catalog and session identities;
- its equality includes `value_evaluator_contract`; pre-V1 catalog ratios are
  incompatible with `ratio-evaluation/v1` frames even when other metadata match;
- compare rejects callback/data-dependent transformed frames until they have a
  deterministic comparable-semantics descriptor;
- session identities never resolve through catalog lookup;
- evidence subjects do not merge catalog and session identities.

### Computation and alignment

- scalar/time-series/segmented/panel ratio values;
- typed-key union alignment and deterministic ordering;
- duplicate-key and incompatible-key-schema failures;
- asymmetric-key rows remain present with null output, component-presence facts,
  role-specific quality counts, and no zero-fill, including for count operands;
- source null, missing component, and present zero denominator remain distinct;
  `zero_division="error"` rejects only present zero denominators;
- catalog and session ratios share `RatioEvaluationV1`; catalog ratios and
  `zero_division="null"` session ratios never produce infinity;
- unit and additivity derivation;
- same-model/source-domain enforcement.

### Components, replay, and downstream parity

- atomic component sidecar persistence;
- same-expression compare and different-expression rejection;
- ratio component delta propagation;
- existing-axis and replayed missing-axis attribution;
- Phase 3 replay that adds a dimension recomposes segmented/panel operands
  through the same typed-key union evaluator;
- correlate, hypothesis-test, forecast, discover, transforms, and quality use the
  same preconditions for catalog and session ratio frames;
- no downstream operator contains an origin-based allow/deny branch.

### Boundaries

- pandas, Ibis, SQL, callbacks, terminal results, arbitrary frames, multi-metric
  frames, and foreign-session frames are rejected;
- composed frames, normalization, callback filters, top/bottom selection, and
  rank transforms are rejected as ratio operands;
- frame arithmetic remains rejected;
- missing-component guidance never recommends narrowing to the shared key set;
- no runtime `MetricRef`, catalog entry, readiness, preview, parity, or promotion
  path is created;
- live help, capability registry, docs, skills, telemetry, and public imports
  agree.

### Recovery and versioning

- cold-start recovery of output, component sidecar, evidence, and replay tree;
- `judgment.db` v1 to v2 migration rewrites subject payloads while catalog
  canonical keys, proposition ids, assessments, and edges remain stable;
- legacy canonical-JSON compatibility has byte-for-byte fixtures for values
  whose v1 encoding exercises `default=str`, including date/time-like slice
  values, while new expression serialization continues to reject that fallback;
- Phase 0/1 frame fixtures derive the expected comparable descriptor without an
  eager rewrite; absent legacy ratio evaluator versions map to
  `catalog-ratio/pre-v1`, while ambiguous metadata stays readable but compare-
  ineligible;
- migration failure rolls back and preserves the original v1 database;
- deterministic cache reuse;
- missing/corrupt operands and schema mismatches fail closed;
- replays preserve expression identity while output artifact ids change with
  materialized scope.

## Documentation and Cutover Surface

An implementation updates together:

- analysis capability registry, help renderer, and CLI help;
- `Session`, compose namespace, and public type algebra;
- frame/component/delta/result identity and persistence schemas;
- evidence subject, canonical keys, seeding, and knowledge reads;
- compare, attribute, correlate, hypothesis-test, forecast, discover, transforms,
  quality, and recovery paths that assume a catalog metric id;
- active analysis and semantic specs;
- latest English and Chinese site documentation;
- `marivo-analysis` composed-caliber closeout and both skill boundary wording;
- telemetry declarations;
- public-surface, help-drift, persistence, evidence, and operator tests.

Historical versioned docs and superseded design artifacts remain historical
unless the release policy explicitly says otherwise.

## Success Criteria

The design is fully delivered when:

1. `session.compose.ratio` accepts only compatible, operand-eligible,
   catalog-identity arity-one `MetricFrame`s and produces a canonical
   `MetricFrame`.
2. The output carries a truthful session-composition identity, complete
   components, coverage, lineage, quality, and replay metadata.
3. No catalog id, `MetricRef`, readiness, owner, or project authority is
   fabricated.
4. Downstream operators gate only on ordinary persisted semantic/state
   preconditions, never composition origin.
5. A composed ratio using `zero_division="null"` has finite numerical and
   computational capability parity with an equivalent catalog-derived ratio
   frame when their persisted contracts match; both preserve asymmetric keys as
   null with the same quality facts.
6. Compare uses expression identity; attribute supports ratio-mix and missing-
   axis replay; evidence uses a typed session-composition subject.
7. Cold-start recovery and content-addressed reruns preserve expression and
   artifact identity correctly.
8. Terminal pandas/SQL/Ibis data still cannot enter typed analysis.
9. The public surface contains no generic expression, arbitrary arithmetic, or
   automatic promotion path.
10. Missing-key zero-fill and cross-model/cross-datasource composition remain
    disabled unless separately reviewed designs amend their contracts.
11. Live help, contracts, docs, skills, telemetry, and tests teach the same
    boundary.

## Final Judgment

Typed metric composition is a core analysis capability because it closes a
high-value gap between governed observation and exploratory analysis without
requiring every useful intermediate ratio to become a project semantic object.

The safe capability is not "dynamic metrics" or "custom formulas." It is a
small, registered algebra over canonical artifacts whose complete computation,
identity, metadata, replay, persistence, evidence, and downstream compatibility
are owned by Marivo.

The ratio operator is worth implementing. A generic expression system is not.
