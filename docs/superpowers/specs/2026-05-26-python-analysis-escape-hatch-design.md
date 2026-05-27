# Python Analysis Escape Hatch Design

Date: 2026-05-26
Status: approved for implementation planning

## Context

`docs/specs/analysis/python-analysis-operator-design.md` defines escape hatch as
a controlled boundary around canonical analysis artifacts:

- Ibis and pandas results are scratch by default.
- Scratch results cannot feed core operators directly.
- Scratch results must be promoted before they become canonical frames.
- Promotion must fail closed when schema, semantic metadata, or provenance is
  incomplete.

The current `marivo.analysis_py` implementation has canonical frame families and
`MetricFrame.from_dataframe(...)`, but it does not yet expose a first-class
`ExplorationResult`, `PromotionPolicy`, `from_pandas`, `explore_ibis`, or
promotion API. This design adds the v1 escape hatch as a strict bridge rather
than a permissive alternate analysis path.

## Goals

- Add a non-canonical scratch artifact for pandas and Ibis exploration.
- Add explicit promotion APIs for `metric_frame`, `delta_frame`, and
  `attribution_frame`.
- Keep core operators closed to scratch artifacts.
- Preserve session-local persistence, lineage, and cross-session ownership
  checks.
- Use typed policy and structured errors instead of free-form metadata dicts.
- Keep v1 automatic inference conservative; missing or ambiguous metadata fails
  with actionable details.

## Non-Goals

- Do not make arbitrary SQL, pandas, or sklearn wrappers into core operators.
- Do not infer semantic metadata from natural language.
- Do not seed canonical findings from scratch artifacts.
- Do not add HTTP, MCP, or CLI transports for the Python track.
- Do not replace existing core operators or relax their input gates.

## Public API

The v1 public API adds:

```python
scratch = mv.from_pandas(df, session=session, description="manual cohort scan")

scratch = mv.explore_ibis(
    lambda con: con.table("orders")
    .filter(lambda t: t.country == "US")
    .group_by("device")
    .aggregate(value=lambda t: t.revenue.sum()),
    session=session,
    datasource="orders",
)

metric = mv.promote_metric_frame(
    scratch,
    policy=mv.PromotionPolicy(),
    session=session,
    metric=mv.MetricRef("sales.revenue"),
    semantic_kind="segmented",
    measure_column="value",
    axes={"device": mv.DimensionRef("device")},
    semantic_model="sales",
)

delta = mv.promote_delta_frame(
    scratch,
    policy=mv.PromotionPolicy(),
    session=session,
    current=mv.ArtifactRef(current.ref),
    baseline=mv.ArtifactRef(baseline.ref),
    delta_column="delta",
)

attribution = mv.promote_attribution_frame(
    scratch,
    policy=mv.PromotionPolicy(),
    session=session,
    source_delta=mv.ArtifactRef(delta.ref),
    driver_field="device",
    contribution_column="contribution",
)
```

`from_pandas` and `explore_ibis` return `ExplorationResult`. Promotion accepts
either an `ExplorationResult` or a direct `pandas.DataFrame`. When a DataFrame is
passed directly, promotion first creates an internal scratch lineage entry so the
canonical frame still records its non-canonical origin.

`explore_ibis` receives a callback over the resolved Ibis backend:

```python
def explore_ibis(
    builder: Callable[[Any], Any],
    *,
    datasource: str,
    session: Session | None = None,
    description: str | None = None,
    source_artifacts: list[ArtifactRef] | None = None,
) -> ExplorationResult
```

The callback must return an Ibis table or expression with an executable
`to_pandas()` method. v1 records a compiled SQL string when Ibis exposes one; if
the backend cannot compile the expression, `source_query` remains `None` and the
scratch artifact is still valid.

Promotion uses typed top-level parameters for target-specific metadata and
`PromotionPolicy` for shared inference and missing-data behavior. This keeps
common required fields visible at the call site while preserving one shared
policy object.

The Python API keeps the current snake_case `to_pandas()` method. The camelCase
`toPandas()` spelling in the operator design document remains a target-state
name, not a v1 requirement.

## Data Model

### ExplorationResult

Add `marivo.analysis_py.frames.exploration`:

- `ExplorationResultMeta`
  - `kind: Literal["exploration_result"]`
  - inherits shared frame ownership fields from `BaseFrameMeta`
  - `source_kind: Literal["pandas", "ibis"]`
  - `description: str | None`
  - `source_query: str | None`
  - `source_datasource: str | None`
  - `source_artifact_refs: list[str]`
  - `promotion_refs: list[str]`
- `ExplorationResult`
  - subclasses `BaseFrame`
  - supports `summary()`, `head()`, and `to_pandas()`
  - is not a canonical input to core operators

`load_frame` should be able to load `exploration_result` so scratch artifacts can
be inspected across a session. Core intents continue to reject it through their
existing family/type gates.

### PromotionPolicy

Add `PromotionPolicy` and a typed anchors model to
`marivo.analysis_py.policies`:

```python
class PromotionSemanticAnchors(BaseModel):
    metric: MetricRef | None = None
    subject: DimensionRef | None = None
    time_axis: DimensionRef | None = None
    source_metric: ArtifactRef | None = None
    source_delta: ArtifactRef | None = None
    current: ArtifactRef | None = None
    baseline: ArtifactRef | None = None
    axis: DimensionRef | None = None

class PromotionPolicy(BaseModel):
    auto_infer: bool = True
    semantic_anchors: PromotionSemanticAnchors = PromotionSemanticAnchors()
    required_fields: list[str] = []
    on_missing: Literal["fail_closed"] = "fail_closed"
```

`ArtifactRef` should be a typed ref alongside `MetricRef`, `DimensionRef`, and
`CalendarRef`. The v1 policy intentionally supports only `fail_closed`; the enum
is present so the public contract can grow without reworking call sites.

## Promotion Rules

### MetricFrame Promotion

`promote_metric_frame` must determine:

- `metric_id`
- `semantic_kind`
- `measure` and measure column
- `axes`
- `semantic_model`
- optional `window` and slice metadata
- source lineage from scratch and any source artifacts

It may infer from `MetricRef`, `DimensionRef`, DataFrame columns, and explicit
policy fields. If the measure, semantic model, subject, time axis, or required
lineage cannot be determined, it raises `PromotionFailedError`.

Signature:

```python
def promote_metric_frame(
    source: ExplorationResult | pd.DataFrame,
    *,
    policy: PromotionPolicy | None = None,
    session: Session | None = None,
    metric: MetricRef | None = None,
    semantic_kind: Literal["scalar", "time_series", "segmented", "panel"] | None = None,
    measure_column: str | None = None,
    axes: dict[str, DimensionRef] | None = None,
    semantic_model: str | None = None,
    window: WindowInput | None = None,
    slice: dict[str, Any] | None = None,
) -> MetricFrame
```

The output is a persisted `MetricFrame` with a `promote_metric_frame` lineage
step.

### DeltaFrame Promotion

`promote_delta_frame` must determine:

- `metric_id`
- `semantic_kind`
- `semantic_model`
- current and baseline provenance
- alignment metadata
- delta measure column or a verifiable current-minus-baseline relationship
- unit compatibility when source metric frames are available

If `policy.semantic_anchors.current` and `baseline` refer to source
`MetricFrame`s in the same session, promotion loads them and inherits compatible
metric, semantic kind, semantic model, and alignment defaults. If source refs are
not available, the caller must provide enough explicit metadata for the v1
contract to remain canonical.

Signature:

```python
def promote_delta_frame(
    source: ExplorationResult | pd.DataFrame,
    *,
    policy: PromotionPolicy | None = None,
    session: Session | None = None,
    current: ArtifactRef | None = None,
    baseline: ArtifactRef | None = None,
    metric: MetricRef | None = None,
    semantic_kind: Literal["scalar", "time_series", "segmented", "panel"] | None = None,
    semantic_model: str | None = None,
    delta_column: str | None = None,
    current_column: str | None = None,
    baseline_column: str | None = None,
    alignment: AlignmentPolicy | None = None,
) -> DeltaFrame
```

The output is a persisted `DeltaFrame` with source frame refs and a
`promote_delta_frame` lineage step.

### AttributionFrame Promotion

`promote_attribution_frame` must determine:

- `source_delta_ref`
- `metric_ids`
- `semantic_kind`
- `semantic_model`
- `driver_field`
- `contribution_column`
- optional `value_column`
- method and params metadata
- coverage or residual metadata when available

If `policy.semantic_anchors.source_delta` is present, promotion loads the source
`DeltaFrame` and inherits metric, semantic kind, and semantic model. The
contribution column must exist and be numeric. The driver field must exist. If a
value column is provided, it must exist and be compatible with the intended
attribution semantics.

Signature:

```python
def promote_attribution_frame(
    source: ExplorationResult | pd.DataFrame,
    *,
    policy: PromotionPolicy | None = None,
    session: Session | None = None,
    source_delta: ArtifactRef | None = None,
    driver_field: str | None = None,
    contribution_column: str | None = None,
    value_column: str | None = None,
    method: str = "promotion",
    params: dict[str, Any] | None = None,
) -> AttributionFrame
```

The output is a persisted `AttributionFrame` with a `promote_attribution_frame`
lineage step.

## Error Handling

Add `PromotionFailedError(AnalysisError)`. It should include structured
details:

- `missing: list[str]`
- `ambiguous: list[str]`
- `available_columns: list[str]`
- `source_refs: list[str]`
- `target_kind: str`

Hints should provide a minimal pasteable correction, such as adding a missing
`MetricRef`, `ArtifactRef`, `measure_column`, or `driver_field`. Promotion never
creates a canonical frame after reporting missing or ambiguous metadata.

## Lineage And Provenance

Exploration creation records:

- scratch source kind (`pandas` or `ibis`)
- source query text when available
- datasource name for Ibis
- source artifact refs when supplied

Promotion records:

- the scratch ref or internal scratch entry
- source canonical frame refs from policy anchors
- promotion target family
- promotion policy digest

The promoted frame becomes the only object that downstream core operators see.
They do not consume raw Ibis expressions, pandas dataframes, or scratch metadata.

## Tests

Add focused tests for:

- `from_pandas` creates an `ExplorationResult`, persists it, loads it, and
  returns defensive pandas copies.
- `explore_ibis` materializes an Ibis expression into an `ExplorationResult` and
  records datasource/query provenance.
- `promote_metric_frame` succeeds with sufficient anchors and fails with
  structured missing metadata.
- `promote_delta_frame` succeeds from current/baseline source refs and fails
  when delta semantics or provenance are incomplete.
- `promote_attribution_frame` succeeds from a source delta ref and fails when
  driver or contribution columns are missing or non-numeric.
- Passing `ExplorationResult` directly to core operators is rejected.
- Public exports include the new frame, policy, refs, functions, and error.
- Skill examples document the scratch-then-promote workflow.

Relevant checks:

```bash
make test TESTS='tests/test_analysis_py_escape_hatch.py'
make typecheck
make lint
make examples-check
```

## Implementation Notes

- Keep escape hatch code in `marivo.analysis_py.escape_hatch` rather than
  mixing it into core intents.
- Extend `marivo.analysis_py.__init__` exports for the public API.
- Extend `_load.py` to recognize `exploration_result`.
- Reuse existing frame persistence helpers and session ownership checks.
- Keep `MetricFrame.from_dataframe(...)` for compatibility, but recommend
  promotion in docs and skill examples.
- Do not widen `Lineage` in v1. Store scratch provenance on
  `ExplorationResultMeta`; store promotion identity in the lineage step and
  existing canonical frame metadata.
- Coverage and residual metadata for attribution promotion are optional in v1
  because `AttributionFrameMeta` has no required slot for them. Validate those
  fields only if the implementation adds an explicit typed metadata field.
