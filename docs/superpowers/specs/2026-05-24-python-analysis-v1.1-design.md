# Python Analysis Library (analysis_py) v1.1 Design

Status: design approved (2026-05-24)

## Background

`marivo.analysis_py` v1 introduced the Python-native analysis runtime with
project-local sessions, persisted `MetricFrame` / `DeltaFrame` outputs, and
two atomic intents: `observe` and `compare`.

This v1.1 spec adds the first derived-analysis layer on top of those frames:
`decompose` for delta frames, `correlate` / `detect` for metric frames, an
`AttributionFrame` family, and richer slice predicates for `observe`.

The design preserves the v1 dual-track boundary. `analysis_py` still consumes
only `marivo.semantic_py` and must not import the OSI/MCP runtime, evidence
layer, AOI generated contracts, or adapter stack.

## Scope

In scope:

- Add `AttributionFrame` as a persisted frame family under
  `marivo.analysis_py.frames`.
- Add three public intents:
  - `mv.decompose(frame, by=...)`
  - `mv.correlate(a, b, ...)`
  - `mv.detect(frame, ...)`
- Keep `decompose`, `correlate`, and `detect` frame-local: they operate on
  materialized frame data and do not call semantic readers or backends.
- Extend `observe(..., slice=...)` from equality-only filters to a small
  structured predicate language.
- Preserve all v1 session, lineage, persistence, immutability, and
  cross-session safety rules.

Out of scope:

- New backend reads inside `decompose`, `correlate`, or `detect`.
- Multi-step DAG submission.
- Evidence objects such as findings, propositions, assessments, or action
  proposals.
- MCP / HTTP transport adapters.
- `SampleFrame`, `ForecastFrame`, or a dedicated `CorrelationFrame`.
- Multi-level decomposition trees.
- Causal attribution, statistical significance testing, or calendar-aware
  baselines.
- Boolean predicate trees with nested `and` / `or`.
- Slice predicates across joined datasets or cross-backend metrics.

## Design Principles

1. **Derived intents stay frame-local.** v1.1 derived intents transform already
   materialized frames. `observe` remains the only v1.1 API that reads from a
   semantic backend.
2. **One new frame family is enough.** `AttributionFrame` carries decompositions,
   correlations, and anomaly candidates in a common derived-analysis shape.
3. **Keep predicate syntax explicit.** The old equality shorthand remains, but
   non-equality filters require structured `{"op": ..., "value": ...}` forms.
4. **Persist everything.** Every successful v1.1 intent writes a frame and a
   job record exactly like `observe` / `compare`.
5. **No silent statistical claims.** Correlation and anomaly detection return
   descriptive metrics only; they do not imply causality or significance.

## Module Layout

```text
marivo/analysis_py/
├── __init__.py
├── frames/
│   ├── __init__.py
│   └── attribution.py          # AttributionFrame + AttributionFrameMeta
├── intents/
│   ├── __init__.py
│   ├── decompose.py            # decompose()
│   ├── correlate.py            # correlate()
│   └── detect.py               # detect()
└── executor/
    └── runner.py               # extended slice predicate translation
```

No v1 module is replaced. The new files follow the same style as
`frames/metric.py`, `frames/delta.py`, `intents/observe.py`, and
`intents/compare.py`.

## Public API

```python
import marivo.analysis_py as mv

mv.decompose(
    frame: mv.DeltaFrame,
    *,
    by: str | None = None,
    value: str = "delta",
    session: mv.session.Session | None = None,
) -> mv.AttributionFrame

mv.correlate(
    a: mv.MetricFrame,
    b: mv.MetricFrame,
    *,
    value_a: str | None = None,
    value_b: str | None = None,
    align: Literal["sample", "bucket", "segment_key"] = "sample",
    method: Literal["pearson"] = "pearson",
    session: mv.session.Session | None = None,
) -> mv.AttributionFrame

mv.detect(
    frame: mv.MetricFrame,
    *,
    value: str | None = None,
    method: Literal["zscore"] = "zscore",
    threshold: float = 3.0,
    session: mv.session.Session | None = None,
) -> mv.AttributionFrame
```

The public namespace exports `AttributionFrame`, `AttributionFrameMeta`,
`decompose`, `correlate`, and `detect`.

## AttributionFrame

`AttributionFrame` is a thin immutable wrapper around a pandas DataFrame plus
Pydantic metadata, matching the v1 frame contract.

```python
class AttributionFrameMeta(BaseFrameMeta):
    kind: Literal["attribution_frame"] = "attribution_frame"
    metric_ids: list[str]
    source_refs: list[str]
    attribution_kind: Literal["decomposition", "correlation", "anomaly"]
    driver_field: str | None
    value_column: str | None
    contribution_column: str | None
    method: str
    params: dict[str, Any]
    semantic_kind: Literal["scalar", "time_series", "segmented", "panel"]
    semantic_model: str
```

The frame body is intentionally simple:

- `decomposition`: one row per `by` value, or one total row for scalar delta
  frames.
- `correlation`: one row summarizing the aligned pair.
- `anomaly`: one row per input row with score and anomaly flags.

`AttributionFrame` does not get a `from_dataframe` entry boundary in v1.1.
External entry remains limited to `MetricFrame.from_dataframe(...)`.

## Decompose Intent

`decompose(frame, by=None, value="delta")` explains the composition of an
existing delta frame. It supports `DeltaFrame` inputs with
`semantic_kind in {"scalar", "time_series", "segmented"}`. `panel` delta frames
are out of scope for v1.1.

Behavior:

- Resolve `session` through the v1 active-session chain.
- Reject frames from a different session with `CrossSessionFrameError`.
- Read a defensive pandas copy through `frame.to_pandas()`.
- Reject non-`DeltaFrame` inputs with `SemanticKindMismatchError`.
- Reject `semantic_kind="panel"` with `SemanticKindMismatchError`.
- Resolve `value`:
  - default to the `delta` column;
  - otherwise require the named column to exist and be numeric.
- Resolve `by`:
  - for `scalar`, `by` must be omitted and the output is one total row;
  - for `time_series`, use the provided `by` column or the first non-numeric
    column as the bucket field;
  - for `segmented`, use the provided `by` column or the first non-numeric
    column as the segment field.
- For `time_series` / `segmented`, group by `by` and compute:
  - `value`
  - `contribution`
  - `pct_contribution`
  - `rank`
- For `scalar`, emit one row with `driver="total"`, the selected `value`,
  `contribution`, `pct_contribution=1.0`, and `rank=1`.
- Sort by descending absolute contribution.
- Persist the result as an `AttributionFrame`.

This is descriptive delta decomposition, not causal attribution.

## Correlate Intent

`correlate(a, b, ...)` describes pairwise linear association between two
metric frames.

Behavior:

- Resolve `session` and enforce both frames belong to it.
- Require both frames to have the same `semantic_model`.
- Allow different `metric_id` values within that model because correlation is
  often a cross-metric operation.
- Require matching `semantic_kind`; mixed-shape correlation is out of scope for
  v1.1.
- Resolve numeric columns independently using `value_a` / `value_b` or the
  single-numeric-column rule.
- Align rows:
  - `sample`: truncate both frames to the shortest length after resetting
    indexes.
  - `bucket` / `segment_key`: join on all common non-numeric columns, or raise
    `AlignmentFailedError` if none exists. Key tuples must be unique on both
    sides; duplicate key tuples fail closed before merge.
- Drop rows with nulls in either value column.
- Compute Pearson correlation, input row count, aligned row count, and dropped
  row count. Undefined Pearson results, including zero-variance inputs, raise
  `AlignmentFailedError` before any frame or job is persisted.
- Persist one summary row as an `AttributionFrame` with
  `attribution_kind="correlation"`.

If fewer than two aligned rows remain, raise `AlignmentFailedError`.

## Detect Intent

`detect(frame, value=None, method="zscore", threshold=3.0)` marks outliers in
an existing metric frame.

Behavior:

- Resolve `session` and enforce frame ownership.
- Resolve `value`:
  - if provided, it must name a numeric column;
  - if omitted, use the only numeric column;
  - if multiple numeric columns exist, raise `SemanticKindMismatchError`.
- Require `threshold > 0`.
- Compute population z-score for the value column.
- Add:
  - `score`
  - `is_anomaly`
  - `direction` (`"high"`, `"low"`, `"normal"`)
  - `threshold`
- Preserve all original input columns in the output.
- Persist the result as an `AttributionFrame` with
  `attribution_kind="anomaly"`.

If the standard deviation is zero or the input has fewer than two non-null
values, return rows with `score=0`, `is_anomaly=False`, and
`direction="normal"`.

## Slice Predicate Expansion

The existing equality shorthand remains valid:

```python
mv.observe("sales.revenue", slice={"region": "north"})
```

v1.1 adds structured predicates:

```python
mv.observe(
    "sales.revenue",
    slice={
        "region": {"op": "in", "value": ["north", "south"]},
        "amount": {"op": ">=", "value": 100},
        "created_at": {"op": "between", "value": ["2026-07-01", "2026-09-30"]},
    },
)
```

Supported operators:

| Operator | Value shape | Ibis translation |
|---|---|---|
| `==` | scalar | `field == value` |
| `!=` | scalar | `field != value` |
| `in` | non-empty list / tuple / set | `field.isin(values)` |
| `>` | scalar | `field > value` |
| `>=` | scalar | `field >= value` |
| `<` | scalar | `field < value` |
| `<=` | scalar | `field <= value` |
| `between` | two-item list / tuple | `(field >= lower) & (field <= upper)` |

Invalid operator names, invalid value shapes, ambiguous slice fields, and
unknown fields raise `SliceInvalidError` or the existing ambiguity-specific
slice error when applicable.

All predicates are combined with AND. Nested OR expressions are out of scope.

## Data Flow

`decompose`, `correlate`, and `detect` share this flow:

1. Resolve the session.
2. Ensure the session is writable.
3. Validate frame ownership.
4. Convert input frame data through `to_pandas()`.
5. Run a deterministic pandas transformation.
6. Build `AttributionFrameMeta` with composed lineage.
7. Persist frame data and metadata through the existing persistence helpers.
8. Write a job record with intent name, input refs, output ref, timings, and
   serialized params.

`observe` keeps the v1 flow but calls an expanded predicate translator before
metric materialization.

## Error Handling

Use existing error classes where they already express the failure:

- `CrossSessionFrameError`: input frame belongs to another session.
- `SessionStateError`: archived sessions cannot receive derived intent jobs.
- `SemanticKindMismatchError`: missing or ambiguous numeric columns.
- `AlignmentFailedError`: correlation alignment cannot produce enough rows.
- `SliceInvalidError`: unsupported predicate syntax or incompatible values.
- `SliceAmbiguousError`: unprefixed slice field resolves to multiple datasets.

No new error class is required for v1.1.

## Lineage

Each v1.1 output frame composes lineage from its source frames and appends one
new `LineageStep`:

- `intent="decompose"`, inputs `[frame.ref]`, metric IDs
  `[frame.meta.metric_id]`
- `intent="correlate"`, inputs `[a.ref, b.ref]`, metric IDs
  `[a.meta.metric_id, b.meta.metric_id]`
- `intent="detect"`, inputs `[frame.ref]`, metric IDs
  `[frame.meta.metric_id]`

`params_digest` is computed from the public parameters only. It must not include
raw DataFrame values.

## Persistence

`AttributionFrame` uses the same frame persistence path as `MetricFrame` and
`DeltaFrame`:

- metadata JSON under the session frame directory;
- parquet data under the session frame directory;
- job record under the session job directory;
- `mv.load_frame(ref, session=...)` restores it based on `meta.kind`.

The existing `load_frame` dispatch must recognize `attribution_frame`.

## Testing

Add focused tests following the repository's flat `tests/test_*` convention:

- `tests/test_analysis_py_frames_attribution.py`
- `tests/test_analysis_py_decompose.py`
- `tests/test_analysis_py_correlate.py`
- `tests/test_analysis_py_detect.py`
- `tests/test_analysis_py_slice_predicates.py`
- updates to import, persistence, and load-frame tests.

Required coverage:

- `AttributionFrame` construction, immutability, metadata validation, and
  `to_pandas()` copy behavior.
- `load_frame` round-trips an attribution frame.
- `decompose` accepts `DeltaFrame` inputs with `semantic_kind` equal to
  `scalar`, `time_series`, or `segmented`.
- `decompose` computes contribution, percent contribution, and rank.
- `decompose` rejects `MetricFrame`, `panel` delta frames, missing inferred
  `by`, and missing or non-numeric `value`.
- `correlate` works with `sample` alignment and common-key alignment.
- `correlate` rejects cross-session frames, cross-model frames, mixed-shape
  frames, and insufficient aligned rows.
- `detect` marks high and low z-score anomalies.
- `detect` handles zero standard deviation as no anomalies.
- Slice predicates cover every supported operator.
- Invalid predicate operator and invalid value shapes raise `SliceInvalidError`.
- Existing equality shorthand keeps working.

Verification commands:

```bash
.venv/bin/pytest tests/test_analysis_py_frames_attribution.py \
  tests/test_analysis_py_decompose.py \
  tests/test_analysis_py_correlate.py \
  tests/test_analysis_py_detect.py \
  tests/test_analysis_py_slice_predicates.py -v
make typecheck
make lint
```

## v1.1 Deliverables

The v1.1 implementation is complete when:

1. `AttributionFrame` is public and persisted.
2. `mv.decompose`, `mv.correlate`, and `mv.detect` are public and write job
   records.
3. `mv.load_frame` restores `AttributionFrame`.
4. `observe(..., slice=...)` supports equality shorthand and the structured
   predicate operators listed above.
5. Derived intents enforce cross-session safety and archived-session
   read-only behavior.
6. Focused tests, `make typecheck`, and `make lint` pass.

## Open Questions

None blocking. Causal attribution, richer statistics, evidence generation, and
transport adapters remain explicit future work.
