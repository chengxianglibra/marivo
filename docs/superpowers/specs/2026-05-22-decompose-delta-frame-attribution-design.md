# Decompose Delta Frame Attribution Design

Date: 2026-05-22

Status: approved design draft for implementation planning.

## Purpose

Marivo's `decompose` intent should follow the analysis operation target architecture:

```text
decompose(delta_frame, dimension) -> attribution_frame(ranked_contributions)
```

The intent explains the overall change represented by a `delta_frame`. It should not
rename delta rows into contribution rows, and it should not expose the old
`delta_decomposition` result contract.

This task is intentionally a breaking semantic upgrade. It does not preserve old
committed artifacts, old public `DeltaDecompositionResult` output, or old tests that
assert the legacy shape.

## Chosen Approach

Keep the current request field name for this iteration:

```json
{
  "compare_artifact_id": "art_cmp_123",
  "dimension": "channel",
  "limit": 5
}
```

Internally, treat `compare_artifact_id` as a transitional field name for the source
`delta_frame` artifact id. The semantic contract is a delta source, not a
compare-private result.

This avoids pulling the full AOI v0.3 `ArtifactInput` and transform DSL into this
change, while still moving the decompose semantics and output family to the target
architecture.

Out of scope:

- replacing the request with `source: artifact_input`
- inline transform DSL
- migration or compatibility for old `delta_decomposition` artifacts
- preserving old AOI `DeltaDecompositionResult` output
- broad outcome envelope redesign

## Input Contract

`decompose` must validate the referenced artifact through AOI v0.3 artifact-family
semantics:

```json
{
  "artifact_family": "delta_frame",
  "shape": "scalar_delta",
  "capabilities": ["decomposable"],
  "axes": [],
  "payload": {
    "series": []
  }
}
```

The first guard checks:

- `artifact_family` is `delta_frame`
- `shape` is one of `scalar_delta`, `time_series_delta`, `segmented_delta`, or
  `panel_delta`
- `capabilities` contains `decomposable`
- axes match the declared shape
- payload or series contains the measures required by the shape
- lineage can resolve the metric plus current and baseline source scopes
- requested `dimension` is a semantic dimension available for the metric
- the metric's decomposition semantics has a supported attribution strategy

`comparison_type` is not a consumer contract. Compare may continue to write it during
the transition if that reduces implementation churn, but `decompose` and downstream
code must not rely on it as the primary type discriminator.

## Delta Shape Semantics

All supported input shapes keep the same user-facing meaning:

```text
Explain the total change represented by this delta frame along the requested dimension.
```

### Scalar Delta

`scalar_delta` represents one scope-level delta. `decompose` resolves the current and
baseline scopes from the source artifact lineage, runs grouped current and baseline
metric queries for the requested dimension, then computes ranked contributions.

### Time Series Delta

`time_series_delta` represents matched time buckets plus a summary delta. `decompose`
uses the source artifact's matched current and baseline time boundaries, then recomputes
grouped current and baseline values for the requested dimension.

It outputs one attribution table for the summary delta. It does not output one
attribution table per bucket.

### Segmented Delta

`segmented_delta` represents a delta frame already carrying a dimension axis. It is
supported, but the output remains attribution, not a delta-row projection.

If the source axis dimension equals the requested attribution dimension and the source
artifact proves that its series is complete and untruncated, runtime may use the delta
rows as a fast path. The fast path must still compute contribution shares and reconcile
the row sum against the source scope delta.

If the requested dimension differs from the source dimension, or completeness cannot be
proven, runtime must recompute grouped current and baseline values from lineage.

### Panel Delta

`panel_delta` represents a `time x dimension` delta frame. If the requested dimension
equals the panel dimension and matched time boundaries plus completeness can be proven,
runtime may aggregate panel rows by dimension and compute contributions.

If the requested dimension differs from the panel dimension, or the source artifact
lacks matched time boundaries, scope summary, or completeness proof, runtime must
recompute from lineage or fail closed.

## Attribution Calculation Rules

Attribution strategy remains governed by metric decomposition semantics. Shape-specific
normalization must not hard-code attribution math that bypasses the existing strategy
dispatcher.

Shared rules:

- `limit` only affects returned rows, not reconciliation.
- Contribution rows must be computed before limiting.
- Successful output must reconcile to the source scope delta within tolerance.
- Missing rows, incomplete source series, unsupported dimensions, or unsupported metric
  semantics should produce typed failure instead of plausible but invalid attribution.
- If the source delta has no meaningful scope delta and the strategy cannot define
  contribution share, the operation fails.

## Output Contract

Successful `decompose` output is an AOI-style artifact envelope payload with:

- `artifact_family`: `attribution_frame`
- `shape`: `ranked_contributions`
- `axes`: one dimension axis for the attribution dimension
- `measures`: `contribution_abs` and `contribution_pct`
- `capabilities`: `filterable`
- `lineage`: operation `decompose` and the source delta artifact id
- `payload.series`: ranked attribution members

Example:

```json
{
  "artifact_family": "attribution_frame",
  "shape": "ranked_contributions",
  "subject": {
    "kind": "comparison",
    "metric_ref": "revenue",
    "current": {"time_scope": {"start": "2026-05-01", "end": "2026-05-08"}},
    "baseline": {"time_scope": {"start": "2026-04-24", "end": "2026-05-01"}}
  },
  "axes": [{"kind": "dimension", "name": "channel"}],
  "measures": [
    {"id": "contribution_abs", "value_type": "number", "nullable": false},
    {"id": "contribution_pct", "value_type": "number", "nullable": true}
  ],
  "capabilities": ["filterable"],
  "lineage": {
    "operation": "decompose",
    "source_artifact_ids": ["art_cmp_123"]
  },
  "payload": {
    "series": [
      {
        "keys": {"channel": "paid"},
        "points": [
          {
            "contribution_abs": 1200.0,
            "contribution_pct": 0.6,
            "current_value": 7000.0,
            "baseline_value": 5800.0,
            "presence": "both",
            "rank": 1
          }
        ]
      }
    ],
    "scope": {
      "current_value": 10000.0,
      "baseline_value": 8000.0,
      "delta_abs": 2000.0,
      "delta_pct": 0.25,
      "direction": "increase"
    },
    "quality": {
      "reconciliation_status": "within_tolerance",
      "unexplained_delta_abs": 0.0,
      "unexplained_pct": 0.0
    }
  }
}
```

Use `contribution_abs` and `contribution_pct` as public measure ids, matching the AOI
v0.3 registry. Documentation may describe `contribution_pct` as share, but the wire
measure id should not be `share`.

## Compare Output Dependency

For `decompose` to consume family-based inputs, compare must emit delta-frame metadata:

- scalar compare emits `artifact_family=delta_frame`, `shape=scalar_delta`
- time-series compare emits `artifact_family=delta_frame`, `shape=time_series_delta`
- segmented compare emits `artifact_family=delta_frame`, `shape=segmented_delta`
- panel compare emits `artifact_family=delta_frame`, `shape=panel_delta` when panel
  compare is supported by the runtime
- all emitted delta frames that are valid decompose inputs include `decomposable` in
  `capabilities`

The implementation may retain legacy scalar summary aliases temporarily for internal
helpers, but public AOI projection and new tests should assert the delta-frame family.

## Downstream Changes

`attribute` should treat atomic decompose outputs as `attribution_frame` artifacts.
Its public derived output should align with "attribute outputs attribution_frame" and
should stop describing drivers as `delta_decomposition`.

`diagnose` can still produce a diagnosis result or bundle, but driver rows should be
read from attribution-frame series.

Runtime helpers should move from legacy row helpers toward attribution helpers, for
example replacing `read_decompose_rows_from_series` with an attribution-frame-oriented
reader.

Evidence extraction should register against `attribution_frame` with shape
`ranked_contributions`. The existing finding type may remain `decomposition_item` if
renaming it would create unrelated evidence-engine churn, but the artifact family and
payload shape should be attribution-oriented.

AOI projection, HTTP response models, MCP schemas, and user docs must stop projecting
decompose as `DeltaDecompositionResult`.

## AOI Spec And Generated Contracts

Update these contract surfaces in the implementation:

- `aoi-spec/schema/aoi.schema.json`
- `aoi-spec/schema/aoi.schema.yaml`
- `aoi-spec/spec.md`
- `aoi-spec/README.md`
- `docs/specs/analysis/aoi-spec.schema.yaml`
- generated `marivo/contracts/generated/aoi.py`
- AOI lowering/projection code
- HTTP and MCP response models
- user-facing MCP/API docs that describe decompose output

Generated models must be regenerated from schema changes using the repository script,
not edited by hand.

## Failure Semantics

Fail before execution when:

- source artifact is missing or not visible in the session
- source is not `artifact_family=delta_frame`
- source shape is unsupported
- source lacks `decomposable`
- axes, measures, payload, or lineage are incomplete for the shape
- requested dimension is not valid for the metric
- metric decomposition semantics has no supported strategy

Fail after calculation when:

- no contribution rows can be formed
- contribution rows cannot reconcile to source scope delta
- segmented or panel fast path lacks completeness proof
- source delta lacks the scope values needed to define contribution share

Do not produce partial-success attribution artifacts for these cases in this task.

## Test Plan

Targeted tests should cover:

- scalar delta input recomputes attribution and emits `attribution_frame`
- time-series delta input uses matched summary boundaries and emits one attribution table
- segmented same-dimension complete input can use the fast path
- segmented input with different dimension recomputes from lineage
- panel same-dimension complete input aggregates across matched time buckets
- panel input with missing completeness or matched boundary fails closed
- missing `decomposable` capability is rejected
- malformed `delta_frame` axes or measures are rejected
- `limit` does not affect reconciliation
- attribute and diagnose consume attribution-frame rows
- evidence extractor registers and extracts from `attribution_frame`
- AOI projection and transport docs no longer expose `DeltaDecompositionResult`

Use repository entrypoints for verification. For targeted Python tests, use
`make test TESTS='...'` or explicit `.venv/bin/...` commands, not bare `pytest`.
