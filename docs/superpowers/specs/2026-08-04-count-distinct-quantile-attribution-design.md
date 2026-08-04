# Marivo Count-Distinct and Quantile Attribution Design

Status: proposed

Date: 2026-08-04

## Summary

Extend the existing `session.attribute(...)` operator to support three Tier-1
non-additive aggregate forms:

- `count_distinct`;
- `median`;
- `percentile(q)`.

The public workflow remains unchanged:

```python
current = session.observe(
    metric,
    time_scope=current_window,
)
baseline = session.observe(
    metric,
    time_scope=baseline_window,
)
delta = session.compare(current, baseline)
drivers = session.attribute(delta, axes=[country])
```

The canonical example intentionally observes the scalar metric without
`dimensions=[country]`. `attribute` owns replaying the requested axis. A caller
may pass a previously segmented delta, but its per-segment distinct counts or
quantiles are explanatory point-estimate changes, not contribution inputs; the
operator still reconciles to the independently replayed scalar delta.

Agents do not select a mathematical method, repeat the distinct key, repeat a
quantile, construct a distribution object, or choose a new operator. The
persisted metric aggregation and datasource evidence determine one legal path:

| Aggregate | `AttributionFrame.attribution_shape` | Method |
| --- | --- | --- |
| `count_distinct` | `distinct_membership` | Exact equal-membership allocation |
| `median` | `quantile_replacement` | Distribution-replacement Shapley at `q=0.5` |
| `percentile(q)` | `quantile_replacement` | Distribution-replacement Shapley at `q` |

Existing additive, semi-additive, ratio-mix, weighted-mix, funnel, and
cumulative mathematical boundaries remain unchanged. `decompose` remains an
internal primitive. No `attribute_distinct`, `attribute_percentile`,
distribution frame, or public policy constructor is added.

For these two new methods, an ordered-prefix result is explicitly requested as
`mode="multiresolution"`, never `mode="hierarchy"`. Every prefix resolution is
a separate attribution game that reconciles independently to the same observed
delta. Prefix rows are not parent rollups and must not be summed across
resolutions. The result reuses the existing flattened prefix-row columns but
does not claim the additive parent-equals-descendants invariant. Existing
rollup-safe methods retain `mode="hierarchy"`; the two public words therefore
never denote method-dependent arithmetic.

As a coupled public-contract cleanup, newly produced additive hierarchy frames
use `attribution_shape="sum", attribution_mode="hierarchy"`; they no longer
write the legacy mode-contaminated `method="ordered_hierarchy_sum"` tag. Older
persisted frames remain readable through one public-property normalization to
`sum`; `meta.method` retains the raw legacy tag for audit and their row values
are not rewritten.

This is deliberately a scalar-change attribution design. A future question
about the whole distribution -- tail thickening, multimodality, divergence, or
several quantiles together -- is a different analytical intent and is outside
this operator.

## Problem

The semantic layer accepts `count_distinct`, `median`, and `percentile(q)`, and
`observe` and `compare` can produce their point estimates and deltas. Ordinary
axis attribution rejects them because the current implementation groups and
sums segment deltas.

That rejection is correct. Neither distinct counts nor quantiles are additive:

```text
count_distinct(A union B) != count_distinct(A) + count_distinct(B)
quantile(A union B)       != quantile(A) + quantile(B)
```

Removing the additivity gate would produce rows that look reconciled while
answering the wrong question. Ratio and Tier-1 mean attribution do not provide
a precedent for doing so: those paths retain additive numerator/denominator or
sum/count components. A quantile point estimate does not retain a closed
additive component algebra, and a distinct count loses set overlap.

The missing capability is therefore not another public verb. It is an
aggregation-specific evidence basis and attribution method behind the existing
verb.

## Goals

- Keep one agent-facing intent: attribute a scalar delta over explicit axes.
- Derive all method inputs from the compared metric and its replay lineage.
- Produce contributions that reconcile mechanically to the observed delta.
- Make source reproducibility and effective supported/blocked admission visible
  without requiring an agent to combine those states itself.
- Persist enough typed method evidence for `.show()`, `.contract()`, recovery,
  and evidence extraction to remain truthful.
- Avoid persisting raw distinct keys or raw sample values in the result.
- Preserve the existing single-axis, joint, flattened prefix-row, and panel row
  layouts while naming the new independent multi-resolution semantics
  truthfully.

## Non-Goals

- Attribution for `min`, `max`, arbitrary Tier-2 metrics, non-linear
  compositions, cumulative metrics, or sampled `time_fold` values.
- Causal attribution. Every result keeps `causal_claim="none"`.
- Explaining an entire distribution or automatically selecting several
  quantiles.
- Accepting caller-supplied identity columns, samples, sketches, methods,
  seeds, permutation counts, tolerances, or execution budgets.
- Making every datasource's approximate percentile implementation
  attributable. Unsupported approximation methods fail closed.
- Persisting a new public `DistributionFrame`, `DistinctSetFrame`, or
  `ComponentFrame` variant.

## Public Contract

### One unchanged entry point

The generic signature does not change:

```python
session.attribute(
    frame: DeltaFrame,
    *,
    axes: list[SemanticInput[DimensionKind | TimeDimensionKind]],
    mode: AttributionMode | None = None,
    target: FunnelLossRate | None = None,
    analysis_purpose: str | None = None,
) -> AttributionFrame
```

The existing funnel-only `target=` overload is unaffected.

For non-additive aggregates, the target delta is never obtained by summing the
rows of a segmented source frame. `attribute` replays the two source scopes
without the requested attribution axes and evaluates the same aggregate once
per comparison bucket. Those unsegmented endpoints define the independently
observed target delta. The axis-bearing replay supplies membership or
distribution evidence for allocating that target. Both replays use the source
frames' persisted window, slice, cohort, status-time, snapshot, and semantic
dependency identities. Runtime catalog loading may execute those recorded refs
only after the existing fingerprint and replay checks; it does not infer new
meaning or broaden the population.

The operator dispatch order is closed:

1. funnel attribution;
2. existing ratio/weighted-mean component attribution;
3. existing additive or valid semi-additive attribution;
4. `count_distinct` attribution when the persisted distinct basis has
   `reproduction.status="reproducible"` and the method is installed;
5. `median` / `percentile(q)` attribution when the persisted quantile basis has
   `reproduction.status="reproducible"` and the method is installed;
6. the existing typed rejection for every other case.

There is no fallback from steps 4 or 5 to sum attribution.

### Agent-visible behavior

`DeltaFrame.show()` states one of:

```text
attribute: supported attribution_shape=distinct_membership
attribute: supported attribution_shape=quantile_replacement q=0.95 source=exact
attribute: blocked: approximate quantile method reservoir_sampling is not mergeable
attribute: blocked: distinct key type array<string> cannot form a private membership basis
```

`DeltaFrame.contract()` carries the same result as typed preconditions. A
blocked precondition names the aggregate, source method, missing evidence, and
the only valid repair. It never recommends numerator/denominator authoring for
a pure quantile. This effective admission projection is the only public
authority for whether calling `attribute` is currently legal. Persisted source
reproduction evidence and the installed method registry are diagnostic inputs
to that projection, not additional agent decisions.

`AttributionFrame.show()` identifies the allocation shape without requiring agents to
inspect raw metadata:

```text
AttributionFrame ... attribution_shape=distinct_membership rows=...
method_evidence: allocation=equal_membership_shapley identities_persisted=false
reconciliation: status=reconciled ...
```

or:

```text
AttributionFrame ... attribution_shape=quantile_replacement rows=...
method_evidence: q=0.95 source=exact/linear_interpolation coalition=permutation_shapley
reconciliation: status=reconciled ...
```

For multi-resolution output the same card additionally states:

```text
multiresolution: rollup_safe=false resolutions=...
reconciliation: status=reconciled scope=each_bucket_and_resolution ...
```

`AttributionFrame.attribution_shape` returns the new allocation-shape tag. No
new public `as_*` narrowing methods are added in v1; adding two methods solely
to mirror the tags would enlarge help and the frame protocol without enabling
another legal continuation.

`AttributionMode` becomes
`Literal["joint", "hierarchy", "multiresolution"]`. `joint` remains legal for
every supported aggregate. `hierarchy` remains the rollup-safe ordered-prefix
layout for existing methods. `multiresolution` is the ordered-prefix layout for
`count_distinct`, `median`, and `percentile`; each resolution is independent.
For multiple axes the delta contract lists exactly the legal modes for its
predicted shape. A distinct or quantile call with `mode="hierarchy"` fails
before replay and repairs to `mode="multiresolution"`; an existing rollup-safe
method does the inverse. Single-axis calls continue to omit `mode`.

The closed generic shape type becomes:

```python
AttributionShape = Literal[
    "sum",
    "ratio_mix",
    "weighted_mix",
    "distinct_membership",
    "quantile_replacement",
]
```

`attribution_shape` identifies mathematical allocation only;
`attribution_mode` identifies the row layout and its arithmetic contract. New
rollup-safe frames never emit
`ordered_hierarchy_sum`. When loading an older frame with that persisted method,
the public `attribution_shape` projection and `as_sum()` narrowing normalize it
to `sum`; `repr`, `.show()`, and `.contract()` use that normalized shape,
`.show()` renders `legacy_method=ordered_hierarchy_sum` once, and raw
`meta.method` remains available for audit. No help text or new artifact teaches
the legacy tag as a selectable shape.

`DeltaFrame.predicted_attribution_shape()` remains a pure, query-free shape
projection. It answers which mathematical allocation family `attribute` would
use if the call were admitted; it does not answer whether the installed runtime
can execute that family. It reads the persisted attribution basis before the
generic non-component branch:

1. any valid distinct basis, reproducible or blocked -> `distinct_membership`;
2. any valid quantile basis, reproducible or blocked -> `quantile_replacement`;
3. ratio/weighted component basis -> the existing mix shape;
4. additive or valid semi-additive semantics -> `sum`;
5. otherwise raise `AttributionShapeUnavailableError`, which states only that
   no closed mathematical shape can be projected and points to `.contract()`
   for admission and repair.

The prediction is independent of `mode` and of the installed method registry.
It never repeats a blocked precondition or repair owned by `.contract()`.

### One effective admission projection

`DeltaFrame.contract()` projects one installed-runtime result from persisted
source reproduction evidence and the versioned method registry:

```python
AttributeAdmissionV1 = Annotated[
    SupportedAttributeAdmissionV1 | BlockedAttributeAdmissionV1,
    Field(discriminator="status"),
]

class AttributeModeAdmissionV1(BaseModel):
    single_axis: Literal["omit"]
    multiple_axes: tuple[AttributionMode, ...] = Field(min_length=1)

class SupportedAttributeAdmissionV1(BaseModel):
    status: Literal["supported"]
    attribution_shape: AttributionShape
    mode: AttributeModeAdmissionV1

class BlockedAttributeAdmissionV1(BaseModel):
    status: Literal["blocked"]
    attribution_shape: AttributionShape | Literal["unavailable"]
    blocker: Literal[
        "unsupported_key_type",
        "point_estimate_only",
        "non_mergeable_sample",
        "missing_method_metadata",
        "matching_evaluator_unavailable",
        "operator_method_not_installed",
        "legacy_missing_basis",
        "unsupported_aggregate",
    ]
    repair: AnalysisRepair
```

`DeltaFrame.show()` renders this exact projection as bounded text. It may also
render source method details, but it never prints `supported` merely because
the persisted source is reproducible. `attribute` validates the same projection
before replay. For distinct and quantile shapes, `multiple_axes` is
`("joint", "multiresolution")`; for existing rollup-safe shapes it is
`("joint", "hierarchy")`. The shape predictor does not construct or validate
admission.

## Persisted Admission Evidence

The current `MetricFrameMeta.aggregation` / `DeltaFrameMeta.aggregation`
strings are enough to display an aggregate name but not enough to prove that
attribution can reproduce it. The basis therefore has one explicit lifecycle:

```text
metric expression graph
  -> observe builds and persists MetricFrameMeta.attribution_basis
  -> compare accepts two exactly compatible bases
  -> DeltaFrameMeta.attribution_basis owns self-contained source reproduction
  -> DeltaFrame.contract projects installed-runtime admission
  -> attribute validates both source graphs against the authority before query
```

Both `MetricFrameMeta` and `DeltaFrameMeta` gain the same optional discriminated
field:

```python
AttributionBasisV1 = Annotated[
    DistinctAttributionBasisV1 | QuantileAttributionBasisV1,
    Field(discriminator="kind"),
]
```

### Aggregate authority

The basis contains one immutable canonical projection of the already-persisted
aggregate node. It does not repeat a key/value node id or a standalone
quantile:

```python
class AggregateAttributionAuthorityV1(BaseModel):
    schema: Literal["aggregate-attribution-authority/v1"]
    aggregate_node_id: str
    expression_graph_fingerprint: str
    aggregate_node: AggregateNodeV1
    aggregate_node_fingerprint: str
```

`aggregate_node.target_ref` is the only value/key authority.
`aggregate_node.agg` is the only aggregate and quantile authority. The
authority factory accepts one exact `AggregateNodeV1` from the metric graph and
computes both fingerprints from canonical graph/node serialization; callers
cannot provide the projection fields independently. Its model validator
recomputes `aggregate_node_fingerprint` from `aggregate_node`.

At execution, each loaded source frame must contain the same graph fingerprint,
node id, canonical node payload, and node fingerprint before that graph may be
replayed. A mismatch is an integrity error, never a reason to trust the copied
projection or consult the current catalog. This preserves self-contained
admission while keeping the metric graph as the sole execution authority.

### Distinct basis

```python
class DistinctAttributionBasisV1(BaseModel):
    schema: Literal["distinct-attribution-basis/v1"]
    kind: Literal["count_distinct"]
    authority: AggregateAttributionAuthorityV1
    null_policy: Literal["exclude"]
    reproduction: DistinctAttributionReproductionV1

DistinctAttributionReproductionV1 = Annotated[
    ReproducibleDistinctAttributionV1 |
    BlockedDistinctAttributionReproductionV1,
    Field(discriminator="status"),
]

class ReproducibleDistinctAttributionV1(BaseModel):
    status: Literal["reproducible"]
    source_method: Literal["exact_distinct_membership"]

class BlockedDistinctAttributionReproductionV1(BaseModel):
    status: Literal["blocked"]
    source_dtype: str
    blocker: Literal["unsupported_key_type"]
```

The basis validator requires `authority.aggregate_node.agg ==
"count_distinct"`. The distinct expression is obtained only from
`authority.aggregate_node.target_ref` during validated replay.
As with quantiles, a known unsupported key type persists blocked reproduction
evidence so effective admission is truthful without executing a membership
query.

### Quantile basis

```python
class QuantileAttributionBasisV1(BaseModel):
    schema: Literal["quantile-attribution-basis/v1"]
    kind: Literal["quantile"]
    authority: AggregateAttributionAuthorityV1
    null_policy: Literal["exclude"]
    reproduction: QuantileAttributionReproductionV1

QuantileAttributionReproductionV1 = Annotated[
    ReproducibleQuantileAttributionV1 |
    BlockedQuantileAttributionReproductionV1,
    Field(discriminator="status"),
]

class ReproducibleQuantileAttributionV1(BaseModel):
    status: Literal["reproducible"]
    source_mode: Literal["exact", "approximate"]
    source_method: str
    source_dtype: str
    distribution_representation: Literal[
        "exact_value_frequency",
        "mergeable_sketch",
    ]

class BlockedQuantileAttributionReproductionV1(BaseModel):
    status: Literal["blocked"]
    source_mode: Literal["exact", "approximate", "unknown"]
    source_method: str | None
    blocker: Literal[
        "point_estimate_only",
        "non_mergeable_sample",
        "missing_method_metadata",
        "matching_evaluator_unavailable",
    ]
```

The quantile basis validator accepts only
`authority.aggregate_node.agg == "median"` or
`("percentile", q)`. Its `effective_q` projection returns `0.5` for `median`
and the tuple's governed `q` for percentile; no second persisted `q` can drift
from the graph. `median` therefore does not create a third execution path.

Blocked datasource methods still persist the exact graph authority and typed
blocked reproduction evidence. This is source evidence, not installed-runtime
admission: `.show()` and `.contract()` can name the source method and blocker
without a catalog read, while `attribute` rejects it before distribution
materialization. A blocked basis never falls through to sum attribution and is
not treated as a legacy missing basis. Replay eligibility is checked separately
at execution; no redundant `replayable` flag is persisted.

`reproduction.status="reproducible"` has exactly one meaning: the datasource
evidence can reproduce the observed aggregate. It never means that `attribute`
is callable. `.contract()` combines reproduction with the installed, versioned
attribute-method registry into `AttributeAdmissionV1`. Until the corresponding
execution slice is active, effective admission is `blocked` with
`blocker="operator_method_not_installed"`, even though source reproduction is
available. Runtime activation is deterministic code state, not a catalog lookup
or artifact inference. The predictor still returns the mathematical shape.

`observe` builds this basis only for an arity-one, Tier-1 aggregate whose
metric graph root resolves to one exact `AggregateNodeV1` and whose datasource
capability can identify its observation method. It persists either reproducible
or typed blocked reproduction evidence. `compare` copies the basis only when
both inputs have identical authority, null policy, and reproduction evidence.
It includes the complete canonical basis in comparison artifact identity.
If both sides have non-null bases that differ, `compare` fails with a structured
comparison-semantics mismatch and produces no `DeltaFrame`; it never drops the
mismatch to `attribution_basis=None`. A null basis is allowed only for a legacy
input and produces the documented legacy-blocked delta.

The complete basis also participates in the persisted MetricFrame and
DeltaFrame artifact identities. Load-time identity verification therefore
rejects a tampered basis before `.show()` or `.contract()` can advertise it;
source-graph validation at execution is the second, replay-specific integrity
check.

The observe artifact schema version is bumped when `MetricFrameMeta` gains the
basis. The complete basis participates in `MetricArtifactIdentityV1` and the
observe cache key, including graph/node fingerprints and quantile
method/representation. Re-running `observe` after upgrading therefore cannot
return a legacy cached `MetricFrame` that lacks admission evidence.

The basis is deliberately self-contained on the `DeltaFrame`: its
`.show()`/`.contract()` admission result must not depend on loading source
frames or consulting a catalog that may have changed. Execution still loads
the source frames and validates their replay graphs before querying data.

Existing persisted metric and delta frames have `attribution_basis=None` and
remain blocked for these aggregates. The repair is to re-run `observe` and
`compare`; the observe schema/version change guarantees a new frame rather than
a legacy cache hit. There is no graph inference from old display strings and no
basis migration path.

## Count-Distinct Attribution

### Interpretation

For each current or baseline scope, materialize unique pairs of:

```text
(distinct_key, requested_axis_partition)
```

If key `k` belongs to `d_s(k)` partitions in scope `s`, each membership receives
weight `1 / d_s(k)`. For partition `g`:

```text
allocated_s(g) = sum(1 / d_s(k) for each unique membership (k, g))
contribution(g) = allocated_current(g) - allocated_baseline(g)
```

Therefore:

```text
sum_g allocated_s(g) = count_distinct_s
sum_g contribution(g) = observed current delta baseline
```

This equal-membership allocation is the Shapley value of each partition in the
set-union count game. It removes axis-order bias, handles keys that appear in
several partitions, and produces fractional contributions only when overlap
actually exists.

It does not claim that a fractional key existed. The fraction is allocation of
one distinct-count unit across observed memberships.

### Execution

The backend performs distinct-pair reduction and membership-degree arithmetic.
Only partition-level aggregates cross into the persisted frame. Raw keys are
never written to frame data, job parameters, lineage, evidence findings, logs,
or errors.

For each deepest joint partition, output rows contain:

| Column | Meaning |
| --- | --- |
| requested axis columns | Stable partition identity |
| `current_observed_distinct` | Ordinary per-partition distinct count; may overlap other rows |
| `baseline_observed_distinct` | Ordinary per-partition distinct count; may overlap other rows |
| `current_allocated_distinct` | Reconciled current membership allocation |
| `baseline_allocated_distinct` | Reconciled baseline membership allocation |
| `contribution` | Allocated current minus allocated baseline |
| existing three share columns | Existing explicit denominator contract |
| `rank` | Existing absolute-contribution rank |

The observed columns are explanatory facts and are never summed for
reconciliation. Only `contribution` and allocated values participate in the
contract.

Single-axis and joint modes use the same deepest-partition calculation.
`mode="multiresolution"` recomputes membership degrees independently for each
ordered prefix resolution, then presents those separate games in the existing
flattened prefix-row columns. Each resolution reconciles independently to the
observed delta, but a prefix contribution is not the sum of its apparent
descendant rows.

For example, if one key belongs to `A/x`, `A/y`, and `B/z`, the deepest game
allocates `1/3` to each leaf while the `A`/`B` prefix game allocates `1/2` to
each prefix. The `A` row is therefore `1/2`, not the `2/3` sum of its displayed
children. `AttributionFrame.show()` and `.contract()` render
`mode=multiresolution rollup_safe=false`; evidence extraction retains the
resolution coordinate in every finding key and must never aggregate across
resolutions. Within one comparison bucket and one selected resolution,
contributions may be summed exactly once.

Panel and time-series inputs perform the calculation independently inside each
comparison bucket. A key can receive a different membership allocation in
different buckets because each bucket is a different observed population.

## Median and Percentile Attribution

### Interpretation

Let each requested deepest axis partition be one group. Define a replacement
game over the union of current and baseline groups:

```text
v(S) = Q_q(
    current values for groups in S
    union
    baseline values for groups not in S
)
```

Then:

```text
v(empty) = baseline quantile
v(all)   = current quantile
```

The contribution of a group is its Shapley value: the average marginal change
when that group's baseline distribution is replaced by its current
distribution. This captures population-size, composition, and within-group
value changes together. V1 does not label those effects separately because a
separate mix/within decomposition would require another public interpretation
and another reconciliation contract.

The result is descriptive distribution attribution, not causality.

### Distribution representations

The operator must evaluate every coalition with the same quantile semantics as
the source observations:

- Exact sources use a value-frequency table keyed by comparison bucket,
  partition, and value. Frequencies preserve the full empirical distribution
  without persisting repeated raw rows.
- Approximate sources are supported only when the datasource adapter exposes a
  mergeable sketch whose merge and quantile operations match the method used by
  `observe`.
- A backend method that returns only a point estimate or a non-mergeable sample
  is blocked. Marivo does not silently substitute pandas, another backend's
  quantile, a fresh reservoir sample, or a linear interpolation result.

Value-frequency tables and mergeable sketch payloads are execution-local
auxiliary state. They are never registered as frames, included in job params or
lineage, written to evidence, logged in errors/telemetry, or retained after the
call. Only partition-level counts/contributions and bounded typed method
evidence cross the persistence boundary. Frequency values with count one are
treated as raw sample values for this rule.

The initial support matrix is capability-driven, not engine-name-driven:

| Source evidence | Admission |
| --- | --- |
| Exact value-frequency reconstruction with matching interpolation | Supported |
| Matching mergeable sketch and quantile evaluator | Supported |
| Approximate point estimate without mergeable state | Blocked |
| Missing method/mode metadata | Blocked |
| Current/baseline authority, method, mode, representation, or effective `q` mismatch | Rejected by `compare`; no DeltaFrame |

This matrix must be rendered by live help and the `DeltaFrame` contract. A
datasource is not advertised as supporting percentile attribution merely
because it supports percentile observation.

### Shapley execution policy

The policy is operator-owned and versioned, not caller-configurable:

- per comparison bucket and resolution, at most 8 partitions: enumerate
  exact Shapley coalitions;
- per comparison bucket and resolution, 9 through 64 partitions: use 128
  deterministic permutations;
- any bucket/resolution with more than 64 partitions: fail before distribution
  materialization and name the failing bucket/resolution plus a coarser or
  lower-cardinality axis repair;
- exact value-frequency evidence is capped at 250,000 rows across the call;
- permutation order is derived from the source delta artifact id, axis ids,
  comparison bucket, resolution, and operator version -- never
  process-global randomness.

Each permutation telescopes from `v(empty)` to `v(all)`, so both exact and
sampled Shapley contribution sums reconcile to the method's endpoint delta.
Permutation sampling affects allocation stability, not reconciliation.

The output includes:

| Column | Meaning |
| --- | --- |
| requested axis columns | Stable partition identity |
| `current_count` | Non-null current observations in the partition |
| `baseline_count` | Non-null baseline observations in the partition |
| `contribution` | Mean marginal quantile change across evaluated orders |
| `contribution_std_error` | Permutation sampling standard error; `0` for exact Shapley |
| existing three share columns | Existing explicit denominator contract |
| `rank` | Existing absolute-contribution rank |

Source-sketch approximation uncertainty and permutation uncertainty are
different facts. `contribution_std_error` covers only the latter. If the source
sketch does not provide a valid error bound, metadata records
`source_error_bound=None` and the result carries a non-blocking evidence issue;
Marivo does not manufacture confidence.

`mode="multiresolution"` likewise evaluates a separate replacement game at each
ordered prefix resolution. Shapley values from a deeper game do not generally
roll up to the Shapley value of a
coarser game, so `rollup_safe=false` applies here as well. Panel and time-series
inputs evaluate each resolution independently per comparison bucket. Empty
current or baseline groups contribute through an empty distribution on that side; an
entirely empty endpoint remains blocked by the same undefined-quantile rule as
`observe`.

## Attribution Result Metadata

Keep the existing `AttributionFrame` family and generic dataframe protocol.
Version its generic row contract and add one typed, discriminated method-
evidence field to the existing metadata instead of adding two public frame
families:

```python
AttributionMethodEvidenceV1 = Annotated[
    DistinctMembershipEvidenceV1 | QuantileReplacementEvidenceV1,
    Field(discriminator="kind"),
]

class AttributionResolutionReconciliationV1(BaseModel):
    schema: Literal["attribution-resolution-reconciliation/v1"]
    status: Literal["reconciled"] = "reconciled"
    axis_refs: tuple[RefPayloadV1, ...]
    bucket_key: tuple[tuple[str, JsonScalar], ...]
    partition_count: int = Field(ge=0)
    total_delta: float
    contribution_sum: float
    residual: float
    max_abs_residual: float = Field(ge=0)
    quantile_execution: QuantileResolutionExecutionV1 | None = None

class QuantileResolutionExecutionV1(BaseModel):
    schema: Literal["quantile-resolution-execution/v1"]
    coalition: Literal["exact_shapley", "permutation_shapley"]
    permutation_count: int = Field(ge=0)
    deterministic_seed_fingerprint: str | None = None

MultiresolutionScopeV1 = Annotated[
    CompleteMultiresolutionScopeV1 | SelectedMultiresolutionScopeV1,
    Field(discriminator="kind"),
]

class CompleteMultiresolutionScopeV1(BaseModel):
    kind: Literal["complete"]

class SelectedMultiresolutionScopeV1(BaseModel):
    kind: Literal["selected"]
    axis_refs: tuple[RefPayloadV1, ...] = Field(min_length=1)

class IndependentMultiresolutionEvidenceV1(BaseModel):
    schema: Literal["independent-multiresolution/v1"]
    rollup_safe: Literal[False]
    scope: MultiresolutionScopeV1
    resolution_reconciliations: tuple[AttributionResolutionReconciliationV1, ...]

class AttributionFrameMeta(BaseFrameMeta):
    ...
    row_contract_version: Literal["generic-attribution-rows/v2"] | None = None
    causal_claim: Literal["none"] = "none"
    method_evidence: AttributionMethodEvidenceV1 | None = None
```

The distinct evidence records the allocation rule, source basis fingerprint,
overlap-key count, `identities_persisted=False`, and an optional
`IndependentMultiresolutionEvidenceV1` required exactly when
`attribution_mode="multiresolution"`.

The quantile evidence records the effective `q` derived from the validated
aggregate authority, source mode/method/dtype, representation, exact,
permutation, or mixed bounded summary, evaluated partition count, permutation
count, deterministic seed fingerprint, source error bound when available,
operator version, and every bucket/resolution reconciliation in
`scope_reconciliations`. Each reconciliation carries its own exact or
permutation execution evidence, so a mixed game is never represented as one
global method. Multi-resolution mode additionally carries the same records in
its required multi-resolution evidence; `at_resolution(...)` filters both
collections and recomputes the bounded summary. The copied effective `q` is
result evidence only; dispatch and execution never read it instead of the
validated aggregate authority.

`generic-attribution-rows/v2` closes the method-specific row schemas described
above. Recovery validates required columns, forbidden method-only columns,
axis coordinates, bucket coordinates, and resolution coordinates against
`method_evidence` before constructing an `AttributionFrame`. Every newly
persisted generic attribution frame writes v2. Legacy frames with no row version
remain readable under the existing legacy path but are never used as templates
for new artifacts.

Multi-resolution v2 rows reuse the existing `level`, `axis`, `driver`, `path`,
and requested axis columns; single-axis and joint rows forbid those four generic
prefix-row columns. This is a storage-layout rule only. `path` expresses
navigation context and does not imply arithmetic parentage. Public consumption
never asks an agent to select a raw numeric `level`.

The existing untyped `params` field remains for current artifact compatibility,
but every semantic fact introduced by this design lives in the typed evidence
field. New code must not require parsing `params` to decide method, support,
uncertainty, or reconciliation.

`AttributionReconciliation` remains the common compatibility summary. For
single-axis and joint results, and for the deepest resolution of
multi-resolution results:

```text
total_delta             = independently observed metric delta
contribution_sum        = sum of deepest partition contributions
unattributed_sum        = 0
residual                 = total_delta - contribution_sum
status                   = reconciled only within the existing numeric tolerance
```

For multi-resolution results,
`method_evidence.multiresolution.resolution_reconciliations` additionally
contains one record per `(comparison bucket, ordered axis-ref prefix)` in
deterministic bucket/resolution order. Every record must reconcile
independently. The common summary still describes only the deepest resolution
and must not be read as proof that coarser rows roll up from deeper rows. Share
denominators and `rank` are likewise computed independently inside each bucket
and resolution.

### Safe resolution narrowing

`AttributionFrame` adds one typed continuation for multi-resolution results:

```python
def at_resolution(
    self,
    *,
    axes: list[SemanticInput[DimensionKind | TimeDimensionKind]],
) -> AttributionFrame: ...

country_drivers = drivers.at_resolution(axes=[country])
country_channel_drivers = drivers.at_resolution(axes=[country, channel])
```

`at_resolution(...)` accepts only one exact ordered prefix of the frame's
persisted axis refs. It resolves semantic inputs to refs, never accepts a raw
integer level, performs no datasource query, and returns an immutable derived
`AttributionFrame` containing only that resolution. The derived frame preserves
the source attribution ref and allocation evidence, changes the typed
multi-resolution scope from `kind="complete"` to
`kind="selected", axis_refs=(...)`, retains only matching resolution
reconciliations, and projects them into its common reconciliation card. Its
`.show()` states the selected axis prefix and that summing contributions once
per comparison bucket is safe. A missing, reordered, or non-prefix selection
fails with a structured error listing the valid axis-ref prefixes.

`AttributionFrame.contract()` emits one bounded, parameterized
`at_resolution(axes=[...])` affordance containing the exact valid ordered
axis-ref prefixes and catalog-based runnable snippets. This is the only public
route from a complete multi-resolution frame to rows safe for ordinary
summation. Direct `to_pandas()` remains available for advanced inspection, but
its contract warns that unselected multi-resolution rows must not be
aggregated. The focused live help target
`analysis.AttributionFrame.at_resolution` owns the runnable selection example.

There is no hidden residual allocation row. If the source representation cannot
reproduce the independently observed endpoints within tolerance, the operator
fails and persists no `AttributionFrame`.

## Runtime Structure

The minimum implementation keeps the public composite small:

```text
Session.attribute
  -> normalize axes and mode
  -> read persisted DeltaFrame attribution basis
  -> existing additive/component decompose path
     or _attribute_distinct_membership(...)
     or _attribute_quantile_replacement(...)
  -> shared share, rank, independent-multiresolution, reconciliation,
     persistence helpers
  -> AttributionFrame
```

Recommended private modules:

```text
marivo/analysis/intents/_attribute_distinct.py
marivo/analysis/intents/_attribute_quantile.py
marivo/analysis/attribution_basis.py
```

Do not add these methods to internal `decompose`. `decompose` owns arithmetic
over already-materialized delta/component rows; the new paths must replay
source scope and acquire set/distribution evidence. Keeping that distinction
prevents an internal helper from pretending that a point-estimate `DeltaFrame`
contains evidence it does not have.

Reuse the existing replay, missing-axis normalization, comparison-bucket,
flattened prefix-row presentation, persistence, and deepest-resolution
reconciliation helpers. Add a private independent-multiresolution helper for
per-resolution shares, ranks, and reconciliation; do not route these rows
through an additive rollup helper. Extract other shared code only when both old
and new paths need the same behavior.

## Failure Contract

Every rejection occurs before an expensive evidence query where the required
fact is already known.

| Condition | Error behavior |
| --- | --- |
| Unsupported aggregate | Existing `AttributionAdditivityError`, with aggregate-specific repair |
| Missing/legacy basis | Re-observe and compare; no inferred basis |
| `hierarchy` requested for a distinct/quantile shape, or `multiresolution` requested for a rollup-safe shape | Structured mode error containing the delta contract's exact legal values |
| Persisted basis fails its frame artifact identity | Existing `FrameMetaInvalidError` during load; frame is not constructed |
| Basis authority does not exactly match either source graph | `AttributionMaterializationError` with `recoverability_status="basis_source_graph_mismatch"` before replay or datasource access |
| Non-replayable or missing source frame | `AttributionMaterializationError` naming the missing source ref |
| Approximate quantile without matching mergeable sketch | Effective admission is blocked and names the source method plus valid repair |
| Quantile partition or distribution budget exceeded | Structured retry with actual count, limit, and coarser-axis repair |
| Source endpoints do not reproduce observed delta | Method-evidence mismatch error; no artifact persisted |
| Distinct key type unsupported by datasource | Structured backend capability error; no string coercion |
| Requested axis cannot be materialized in source scope | Existing missing-axis materialization error |

Errors must report `expected`, `received`, `location`, a typed repair, and
`marivo.help("analysis.attribute")`. They must not suggest changing a quantile
into a ratio or exposing raw identity/sample data.

## Agent-Facing Help and Documentation

The implementation is incomplete until these surfaces agree:

- `marivo.help("analysis.attribute")` lists the automatic method matrix and one
  scalar `observe -> compare -> attribute` example that works unchanged for all
  supported aggregates and demonstrates automatic axis replay;
- `DeltaFrame.contract()` owns one effective `AttributeAdmissionV1`, and
  `DeltaFrame.show()` renders that same installed-runtime result;
- `DeltaFrame.predicted_attribution_shape()` and the closed `AttributionShape`
  type return the same allocation-shape tag as successful execution without
  performing admission or repair;
- `AttributionFrame.show()` renders bounded method evidence and uncertainty;
- `AttributionFrame.attribution_shape` documents all five generic shapes:
  `sum`, `ratio_mix`, `weighted_mix`, `distinct_membership`, and
  `quantile_replacement`;
- active English and Chinese analysis workflow documentation explains why
  observed per-segment distinct counts or percentiles are not contributions;
- the packaged analysis skill tells agents to inspect the delta contract and
  then call the same `attribute` entry point, never to choose a method;
- `AttributionFrame.contract()` exposes typed `at_resolution(axes=[...])`
  affordances, and its focused help contains a runnable selection example;
- evidence digest extraction names distribution or membership attribution and
  carries the no-causal-claim and uncertainty boundaries; multi-resolution
  findings retain axis-ref prefixes and the non-rollup boundary.

No new root help target, public constructor, or public result/type export is
introduced. The only new navigation edge is the frame-local
`AttributionFrame.at_resolution(...)` continuation required to consume one
resolution safely.

## Validation Matrix

### Count distinct

- Disjoint partitions produce integer allocations equal to ordinary segment
  counts and reconcile exactly.
- One key in two partitions receives `0.5` in each and is counted once overall.
- A key moving between partitions across windows produces balanced partition
  contributions and the correct total delta.
- New-only and baseline-only partitions remain explicit.
- Null keys are excluded exactly as in `observe`; null axis values remain an
  explicit partition.
- Joint, time-series, and panel outputs reconcile at their defined deepest
  partition/bucket boundaries.
- Multi-resolution mode emits independent prefix games. The `A/x`, `A/y`, `B/z`
  overlap fixture proves that `A=1/2` may differ from the `2/3` sum of its
  displayed children, every resolution still reconciles, `rollup_safe=false`
  is persisted/rendered, and evidence never sums across resolutions.
- A known unsupported key dtype persists blocked distinct reproduction
  evidence; `.show()` and `.contract()` name the dtype, the predictor still
  returns `distinct_membership`, and none performs a membership query.
- No raw key appears in persisted frame files, job records, lineage, errors,
  telemetry, evidence, `repr`, or `.show()`.

### Median and percentile

- A constructed example where segment percentile deltas do not sum proves the
  operator uses distribution replacement rather than grouped point estimates.
- `median` and `percentile(0.5)` produce the same method evidence and result.
- Exact Shapley and deterministic permutation paths both reconcile.
- Multi-resolution mode evaluates and reconciles each prefix game independently,
  persists `rollup_safe=false`, and never claims that coarse Shapley values equal
  sums of deeper Shapley values.
- Repeating the same call across processes produces byte-equivalent method
  evidence and numerically identical rows.
- Current-only, baseline-only, null-heavy, tied-value, and empty-endpoint cases
  follow the documented contract.
- A matching mergeable sketch path reproduces observed endpoints.
- A non-mergeable approximate method is blocked in `.contract()` and performs
  no distribution query.
- Partition and value-frequency budget failures include executable coarsening
  repairs.
- Permutation error is not presented as source quantile error.
- Raw values, value-frequency rows, and sketch payloads never appear in frame
  files, auxiliary artifacts, jobs, lineage, errors, telemetry, evidence,
  `repr`, or `.show()`; frequency-one values receive no exception.

### Shared contract

- Existing sum, semi-additive, ratio-mix, weighted-mix, funnel, and cumulative
  numeric behavior remains green. Additive hierarchy expectations migrate from
  legacy `method="ordered_hierarchy_sum"` to
  `attribution_shape="sum", attribution_mode="hierarchy"` without changing
  rows.
- Persist/load round trips preserve the attribution basis, method evidence,
  row schema, reconciliation, lineage, issues, and contract.
- Metric and delta basis round trips reject tampered node payloads,
  fingerprints, standalone quantile drift, and current/baseline authority
  mismatch before datasource access.
- Reproducible and blocked source-reproduction variants round-trip distinctly;
  effective admission combines them with installed methods, and blocked
  distinct/quantile bases retain their exact source method or dtype repair and
  cannot enter either new execution path.
- A reproducible basis with an inactive installed method yields effective
  `status="blocked", blocker="operator_method_not_installed"` while the pure
  predictor still returns its distinct or quantile allocation shape.
- Re-observing after the artifact schema bump cannot reuse a legacy basis-free
  MetricFrame cache entry; comparing the refreshed frames produces a basis in
  both the DeltaFrame metadata and comparison identity.
- `DeltaFrame.predicted_attribution_shape()`, successful output
  `attribution_shape`, the closed type literal, help, and shape tests agree for
  all five methods, including blocked known bases; only `.contract()` owns
  effective admission and repair. Legacy `ordered_hierarchy_sum` frames
  normalize publicly to `sum`.
- `at_resolution(...)` accepts every exact ordered axis-ref prefix, rejects raw
  levels and invalid/reordered prefixes, preserves lineage/evidence, and returns
  rows whose contributions are safe to sum once per comparison bucket.
- `AttributionMode` help and validation allow `joint|multiresolution` for the
  two new shapes and `joint|hierarchy` for existing rollup-safe shapes; invalid
  crossings fail before replay with the delta contract's exact repair.
- The live-help canonical example observes an unsegmented scalar, then proves
  that `attribute(..., axes=[...])` performs the required axis replay.
- Live-help registry, exports, generated API docs, English/Chinese docs,
  packaged skills, evidence extraction, and drift snapshots agree.
- Observe cache identity changes when aggregate authority, source quantile
  method, or distribution representation changes. Attribute cache identity
  changes when effective graph-owned `q`, axis tuple, mode, multi-resolution
  semantics, or operator version changes.
- Focused tests, `make typecheck`, `make lint`, `make test`, docs validation,
  and `git diff --check` pass.

## Delivery Slices

### Slice 1: typed admission and truthful blocking

- Add graph-owned aggregate authority plus `AttributionBasisV1` to
  `MetricFrameMeta` and `DeltaFrameMeta`, bump the observe artifact schema/cache
  identity, and propagate only exactly compatible bases through `compare`.
- Update `DeltaFrame.show()`, `.contract()`,
  `.predicted_attribution_shape()`, the closed shape type, the single effective
  admission projection, and aggregate-specific errors. The predictor remains
  independent of installed-runtime admission.
- Normalize newly produced additive hierarchy shape metadata to
  `shape=sum, mode=hierarchy`, with read-only public normalization for legacy
  `ordered_hierarchy_sum` artifacts.
- Keep execution blocked for the new methods.

This slice removes the current generic and misleading repair text before
claiming new capability.

### Slice 2: exact count-distinct attribution

- Add backend membership allocation and result metadata.
- Cover single, joint, multi-resolution, `at_resolution`, panel, persistence,
  and privacy tests.
- Activate `distinct_membership` in help only after runtime verification.

### Slice 3: exact quantile attribution

- Add exact value-frequency acquisition and distribution-replacement Shapley.
- Support both `median` and exact `percentile(q)` through the same path.
- Add bounded deterministic execution and uncertainty evidence.

### Slice 4: mergeable approximate quantiles

- Add datasource sketch adapters one method at a time.
- Activate effective support per source reproduction method only after endpoint
  reproduction and merge-equivalence tests pass for that exact adapter.

No slice advertises a later slice as already supported. Each released state
keeps `DeltaFrame.contract()` aligned with installed runtime behavior.

## Acceptance Criteria

The design is implemented when:

1. an agent uses the existing `observe -> compare -> attribute` workflow for
   all supported aggregates without supplying a method-specific argument;
2. count-distinct contributions account for overlap and reconcile to the
   independently observed distinct-count delta;
3. median/percentile contributions are computed from reproducible distribution
   evidence and reconcile to the independently observed quantile delta;
4. non-additive ordered-prefix results use `mode="multiresolution"`, never
   `mode="hierarchy"`: every resolution reconciles, `rollup_safe=false` is
   visible, and no surface or evidence path treats parent-looking rows as sums
   of descendants;
5. unsupported approximate quantile methods are visibly blocked before
   execution instead of failing after an apparently accepted call;
6. persisted artifacts expose typed graph-owned source reproduction, method
   evidence, uncertainty, lineage, and reconciliation without storing raw keys
   or samples, while the delta contract exposes effective installed admission;
7. a refreshed observe cannot hit a basis-free legacy cache entry, and
   execution rejects any basis/source-graph mismatch before querying data;
8. `predicted_attribution_shape()` and successful output agree on the five
   canonical math shapes; additive hierarchy uses `sum` plus `mode="hierarchy"`;
9. an agent can select a safe multi-resolution prefix with
   `at_resolution(axes=[...])` without inspecting or filtering a numeric level;
10. current attribution behavior and public API remain otherwise unchanged;
11. live help, frame contracts, errors, evidence, tests, and active docs all tell
   the same installed-runtime truth.
