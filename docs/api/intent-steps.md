# Intent Action Surface

This document defines the external HTTP contract for submitting typed analysis intents in Factum.

The path acts as the intent discriminator — request bodies do not contain a `step_type` or `intent` field. The `/intents/` prefix distinguishes this surface from legacy step endpoints (removed in Phase 2).

## Purpose

Use these endpoints when a client needs to:

- execute a typed atomic analysis intent
- execute a typed derived analysis intent that expands into a deterministic internal DAG
- submit analysis work in a session without exposing SQL-shaped execution contracts

Do not use these endpoints as a substitute for:

- session root lifecycle management
- session state or proposition context retrieval
- generic projection retrieval

Step submission is a write surface. Session state and proposition context remain separate canonical read surfaces.

## Canonical Resources

| Intent family | Endpoint | Canonical success payload |
|---------------|----------|---------------------------|
| `observe` | `POST /sessions/{session_id}/intents/observe` | `ObserveResponse` |
| `compare` | `POST /sessions/{session_id}/intents/compare` | `CompareResponse` |
| `decompose` | `POST /sessions/{session_id}/intents/decompose` | `DecomposeResponse` |
| `correlate` | `POST /sessions/{session_id}/intents/correlate` | `CorrelateResponse` |
| `detect` | `POST /sessions/{session_id}/intents/detect` | `DetectResponse` |
| `test` | `POST /sessions/{session_id}/intents/test` | `TestResponse` |
| `forecast` | `POST /sessions/{session_id}/intents/forecast` | `ForecastResponse` |
| `attribute` | `POST /sessions/{session_id}/intents/attribute` | `AttributeResponse` |
| `diagnose` | `POST /sessions/{session_id}/intents/diagnose` | `DiagnoseArtifact` |
| `validate` | `POST /sessions/{session_id}/intents/validate` | `ValidateResponse` |

This target-state contract intentionally does not define:

- a generic `POST /sessions/{session_id}/steps`
- a generic `POST /sessions/{session_id}/steps/{step_type}` that accepts arbitrary discriminated unions
- SQL-shaped step submission

The path is the intent discriminator. Request bodies for these per-intent endpoints omit top-level `step_type` / `intent` discriminator fields even if the design drafts include them.

## Common Submission Contract

### Transport Rules

- method: `POST`
- content type: `application/json`
- success status: `201 Created`
- error envelope: standard error contract from [`errors.md`](errors.md)

The `{session_id}` path parameter is authoritative for session ownership. Successful step submissions always create a new step execution and a new canonical artifact lineage; they do not mutate a prior step result in place.

### Session Preconditions

- the session must exist
- the session must be `open`
- session root metadata must not be used as a hidden execution-scope carrier

Step-level execution semantics belong in typed request fields such as `time_scope`, `scope`, `left_ref`, `right_ref`, or other intent-native fields. The session root must not silently inject canonical execution scope.

### Typed Ref Rules

Ref-consuming intents must use structured typed refs, not bare IDs.

Required invariants:

- refs must identify the producing session, step, and artifact lineage when the underlying intent contract requires them
- refs must point to canonical artifacts, not projection-only objects
- cross-session refs are invalid unless an intent-specific contract explicitly allows them; v1 does not allow them
- ref types are part of validation, for example `compare` only accepts `observe` refs and `decompose` only accepts `compare` refs

Representative ref shapes:

```json
{
  "session_id": "sess_123",
  "step_id": "step_obs_abc",
  "step_type": "observe",
  "artifact_id": "art_abc",
  "observation_type": "time_series"
}
```

```json
{
  "session_id": "sess_123",
  "step_id": "step_cmp_abc",
  "step_type": "compare",
  "artifact_id": "art_cmp_abc",
  "comparison_type": "scalar_delta"
}
```

### Response Boundary

On success, each endpoint returns the canonical artifact or derived bundle for that intent. The success payload may include:

- typed step lineage
- artifact identity
- provenance and source lineage
- validation or readiness metadata
- bounded projection metadata when that metadata is part of the artifact contract

The success payload must not inline:

- `SessionStateView`
- `PropositionContextView`
- narrative-only synthesis
- generic session summaries

### Artifact And Projection Separation

These write endpoints submit analysis work and return canonical artifacts or derived bundles. Projection rules remain deterministic, but projections do not replace artifact identity.

Fixed rules:

- downstream refs must target canonical artifacts
- projection refs, when present, are lookup handles only
- a write response must not silently downshift from artifact to projection

### Validation Layers

Each step submission passes through the following validation layers in order:

1. Transport validation: JSON shape, path parameters, enum values, and primitive types
2. Request normalization: intent-specific defaults and canonical normalization
3. Semantic validation: metric capability, scope legality, mode legality, and bounded-output constraints
4. Ref validation: referenced step/artifact existence, completion, type compatibility, and same-session ownership
5. Intent execution validation: comparability, detectability, attributability, alignment, forecastability, or inferential compatibility as required by the intent
6. Execution and artifact materialization

Derived intents add one more fixed layer:

7. Deterministic expansion validation: the request must expand into a fully determined internal DAG without planner-style branching

### Common Error Behavior

These endpoints use the standard error envelope from [`errors.md`](errors.md).

Common transport statuses:

| Status | Scenario |
|--------|----------|
| `400` | semantic request invalid, invalid filter, unsupported operation, not comparable, not attributable, not aligned, insufficient history, insufficient data |
| `404` | session not found, typed ref not found |
| `409` | session is not open, or a referenced semantic object is active but not ready for runtime use |
| `422` | request body fails schema validation |
| `500` | unexpected server-side failure while executing or materializing an artifact |
| `503` | metadata store or analytics engine unavailable |

Step submission errors may include additional structured fields such as:

- `code`: stable semantic failure class such as `INVALID_ARGUMENT`, `INVALID_FILTER`, `STEP_NOT_FOUND`, `NOT_COMPARABLE`, or `INSUFFICIENT_HISTORY`
- `issues`: typed validation issues when the failing intent contract defines them
- `ref`: the typed ref or path target associated with the failure when useful

When intent compilation hits an object-level readiness gate, the endpoint returns `409` with the
same readiness payload used by `GET /semantic/resolve/{typed_ref}`:

- `message`
- `code`
- `category`
- `subject_ref`
- `object_kind`
- `lifecycle_status`
- `readiness_status`
- `blocking_requirements`
- `capabilities`
- `dependency_refs`

When compilation passes object readiness but the current request is incompatible with the resolved
semantic objects, the endpoint also returns `409`, but with a distinct compatibility payload:

- `message`
- `code`: `semantic_request_incompatible`
- `category`: `compatibility`
- `subject_ref`
- `issues`: structured compiler compatibility issues
- `request_context`

## Atomic Intents

### `POST /sessions/{session_id}/intents/observe`

Submits the `observe` atomic intent.

Request body fields:

- `metric`: published semantic metric name
- `result_mode`: `standard`, `numeric_sample_summary`, or `rate_sample_summary`; defaults to `standard`
- `time_scope`: required canonical time scope
- `calendar_policy_ref`: optional fixed calendar alignment policy ref; only accepted on `observe`
- `scope`: optional non-time scope
- `granularity`: allowed only for `standard` time-series observations
- `dimensions`: allowed only for `standard` segmented observations

Supported outputs:

- scalar observation
- time-series observation
- segmented observation
- numeric sample summary
- rate sample summary

Invalid combinations include:

- `granularity` and `dimensions` together
- non-`standard` `result_mode` with `granularity` or `dimensions`
- `granularity = "hour"` with date-only or timezone-aware `time_scope.kind = "range"` boundaries; hour grain requires naive datetime strings such as `2026-04-09 00:00:00`
- `calendar_policy_ref` with an hour-grain observe window; calendar alignment policies only support day/week/month windows in v1
- time conditions inside `scope`
- unsupported metric capability for the requested observation mode
- metrics without a per-row value expression for sample-summary modes; typed metrics may support
  `standard` observation while still rejecting `numeric_sample_summary` or `rate_sample_summary`
  when their contract only compiles to aggregate SQL
- `distribution_metric` standard observation depends on the routed engine's supported percentile
  kernel; percentile/quantile metrics compile through engine-specific SQL, while
  `distribution_spec.kind="histogram_ready"` is not supported by standard `observe` in v1

Success returns `ObserveResponse`, a union of the five canonical observation artifact types. All success payloads include `step_ref`, `artifact_id`, resolved `time_scope`, normalized `scope`, `resolved_policy_summary`, and analytical / execution metadata. `resolved_policy_summary` is `null` when no calendar alignment policy was resolved.

`calendar_policy_ref` is an observe-only input boundary in v1. Downstream typed-ref intents such as `compare`, `attribute`, and `validate` must reuse the upstream frozen alignment metadata instead of accepting a second policy input.

When `calendar_policy_ref` is present, the returned observation artifact freezes the compiler-resolved alignment plan in `resolved_policy_summary`, including the final policy ref, calendar source/version, baseline window, bucket pairing, coverage summary, and comparability warnings. Downstream intents must treat this field as the artifact-level reuse surface rather than reconstructing policy semantics from the original request.

Calendar provenance attached to that frozen summary may omit optional lineage branches that were not configured for the resolved snapshot. In particular, `holiday_yoy` must still succeed when only holiday lineage is available; missing optional event lineage should surface through coverage / comparability metadata rather than as an `observe` hard failure.
If an optional `event_source` lineage branch is empty or partial, runtime metadata normalization treats it as absent and omits it from the persisted calendar binding.

In v1, `resolved_policy_summary.bucket_pairing` remains metadata on the observation artifact. Factum does not expose a separate bucket-pairing artifact id or typed ref.

When `calendar_policy_ref` is present on a `week` or `month` observation, the request granularity still controls the returned observation shape, but the compiler resolves calendar alignment at day granularity for comparability metadata. `calendar_policy.weekday_wow` specifically means "day-aligned within the compared weeks", not "whole-week black-box to whole-week black-box".

Recommended semantic error codes:

- `INVALID_ARGUMENT`
- `INVALID_FILTER`
- `UNSUPPORTED_OPERATION`

### `POST /sessions/{session_id}/intents/compare`

Submits the `compare` atomic intent.

Request body fields:

- `left_ref`: required `observe` ref
- `right_ref`: required `observe` ref
- `mode`: `auto`, `scalar`, `segmented`, or `time_series`; defaults to `auto`

`compare` does not accept `calendar_policy_ref`; any calendar alignment semantics must come from the referenced upstream observations.

Supported comparisons:

- scalar vs scalar -> `scalar_delta`
- segmented vs segmented with identical dimensions -> `segmented_delta`
- time_series vs time_series with identical granularity -> `time_series_delta`

Unsupported comparisons include:

- scalar vs segmented
- segmented vs scalar
- different metrics
- segmented inputs with different dimensions
- time_series inputs with different granularity

Success returns `CompareResponse`, which is one of `ScalarDeltaArtifact`, `SegmentedDeltaArtifact`, or `TimeSeriesDeltaArtifact`. All success payloads include comparability metadata, resolved input summary, source lineage, and execution metadata.

`time_series_delta` returns ordered bucket-level delta rows plus aligned-window summary fields. This compare artifact is currently a compare-only output boundary: downstream v1 consumers such as `decompose`, `attribute`, and `diagnose` still only accept `scalar_delta`.

Recommended semantic error codes:

- `INVALID_ARGUMENT`
- `STEP_NOT_FOUND`
- `UNSUPPORTED_COMPARISON`
- `NOT_COMPARABLE`

### `POST /sessions/{session_id}/intents/decompose`

Submits the `decompose` atomic intent.

Request body fields:

- `compare_ref`: required `compare` artifact ref
- `dimension`: required semantic dimension
- `method`: optional; v1 only supports `delta_share`

Supported inputs:

- `compare_ref` must resolve to `scalar_delta`
- the metric must be additive
- the metric must declare the requested dimension decomposable

Unsupported inputs include:

- direct scope input
- `segmented_delta` as the primary input contract
- multi-dimension decomposition
- non-additive metrics
- alternative attribution methods such as `shapley`

Success returns `DecomposeResponse`, the canonical `delta_decomposition` artifact. The payload includes attribution status, contribution rows, unexplained remainder fields, source lineage, version metadata, and execution metadata.

Recommended semantic error codes:

- `INVALID_ARGUMENT`
- `STEP_NOT_FOUND`
- `NOT_ATTRIBUTABLE`

### `POST /sessions/{session_id}/intents/correlate`

Submits the `correlate` atomic intent.

Request body fields:

- `left_ref`: required `observe(time_series)` ref
- `right_ref`: required `observe(time_series)` ref
- `method`: `pearson` or `spearman`; defaults to `spearman`
- `min_pairs`: optional minimum aligned bucket count; defaults to `5`

Supported inputs:

- both refs must resolve to complete `observe(time_series)` artifacts
- both series must use the same granularity
- bucket alignment uses only `intersection_by_time_bucket`

Unsupported inputs include:

- scalar or segmented inputs
- projection refs
- direct `metric + scope`
- multiple methods in one request
- automatic lag search or `control_for`

Success returns `CorrelateResponse`, the canonical `pairwise_time_series_association` artifact. The payload includes alignment status, statistic, sign, significance, analytical metadata, source lineage, and execution metadata.

Recommended semantic error codes:

- `INVALID_ARGUMENT`
- `STEP_NOT_FOUND`
- `NOT_ALIGNED`
- `INSUFFICIENT_DATA`

### `POST /sessions/{session_id}/intents/detect`

Submits the `detect` atomic intent.

Request body fields:

- `metric`: required semantic metric
- `time_scope`: required `single_window` detect scope
- `scope`: optional non-time scope
- `split_by`: optional single semantic dimension
- `profile`: optional detect profile; `null` normalizes to `auto`
- `sensitivity`: optional sensitivity; `null` normalizes to `balanced`
- `limit`: optional returned-candidate bound; `null` normalizes to a bounded system default
- `max_series`: optional scanned-series bound when `split_by` is present; `null` normalizes to a bounded system default when applicable

Invalid combinations include:

- non-`single_window` detect time scope
- non-positive `limit` or `max_series`
- unsupported profile / grain / metric combination
- time filters inside `scope`

Success returns `DetectResponse`, the canonical `anomaly_candidates` artifact. The payload includes detectability status, scan summary, ordered candidates, truncation metadata, provenance, and execution metadata.

Recommended semantic error codes:

- `INVALID_ARGUMENT`
- `INVALID_FILTER`
- `UNSUPPORTED_OPERATION`

### `POST /sessions/{session_id}/intents/test`

Submits the `test` atomic intent.

Request body fields:

- `left_ref`: required inferential-ready `observe` ref
- `right_ref`: required inferential-ready `observe` ref
- `hypothesis`: required difference-hypothesis contract
- `method`: `auto`, `welch_t`, or `two_proportion_z`; defaults to `auto`

The `test` typed refs are strict in v1:

- `artifact_id` is required on both refs and must match the committed upstream `observe` artifact
- `observation_type` is required on both refs and must be `numeric_sample_summary` or `rate_sample_summary`
- the request ref metadata must agree with the resolved committed artifact lineage

Normalization rules:

- `hypothesis.alternative` defaults to `two_sided`
- `hypothesis.alpha` defaults to `0.05`
- `method` defaults to `auto`

Supported inputs:

- both refs must resolve to complete `numeric_sample_summary` or `rate_sample_summary` artifacts
- the observation type must match across sides
- the metric must be the same, or belong to an explicitly cross-group comparable family

Unsupported inputs include:

- raw sample arrays
- direct `metric + scope`
- `compare`, `decompose`, or `detect` outputs as samples
- projection refs
- non-difference hypothesis families

Success returns `TestResponse`, the canonical `hypothesis_test` artifact. The payload includes normalized hypothesis, method, estimate, statistic, `p_value`, decision, assumptions, validation metadata, source lineage, and execution metadata.

Recommended semantic error codes:

- `INVALID_ARGUMENT`
- `STEP_NOT_FOUND`
- `NOT_COMPARABLE`
- `INSUFFICIENT_DATA`

### `POST /sessions/{session_id}/intents/forecast`

Submits the `forecast` atomic intent.

Request body fields:

- `source_ref`: required `observe(time_series)` ref
- `horizon`: required positive integer
- `profile`: optional forecast profile; defaults to `auto`
- `interval_level`: optional prediction-interval level; defaults to `0.95`

Supported inputs:

- `source_ref` must resolve to a complete `observe(time_series)` artifact
- source granularity must be one of `hour`, `day`, `week`, or `month`
- source series must be regular enough for the chosen profile

Unsupported inputs include:

- direct `metric + time_scope`
- non-time-series observations
- projection refs
- exogenous regressors
- raw model names or unbounded model-tuning knobs

Success returns `ForecastResponse`, the canonical `forecast_series` artifact. The payload includes forecastability status, history summary, complete future bucket sequence, source lineage, analytical metadata, and execution metadata.

Recommended semantic error codes:

- `INVALID_ARGUMENT`
- `STEP_NOT_FOUND`
- `UNSUPPORTED_OPERATION`
- `INSUFFICIENT_HISTORY`

## Derived Intents

### `POST /sessions/{session_id}/intents/attribute`

Submits the `attribute` derived intent.

Request body fields:

- `metric`: required semantic metric
- `left`: required scalar-observation input using the canonical `observe` subset
- `right`: required scalar-observation input using the canonical `observe` subset
- `dimensions`: required non-empty semantic dimension list
- `decomposition_method`: optional; defaults to `delta_share`
- `decomposition_limit`: optional bounded per-dimension row limit; omitted values normalize to a bounded system default

Deterministic expansion:

1. `observe(left)`
2. `observe(right)`
3. `compare(mode = "scalar")`
4. `decompose(...)` once per requested dimension

Unsupported inputs include:

- auto-selecting dimensions
- auto-deriving either side
- multi-metric attribution
- multi-dimension interaction attribution
- causal or recommendation-style outputs

Success returns `AttributeResponse`, the canonical `attribute_bundle`. The payload includes normalized left/right scopes, observation refs, compare ref, comparison summary, ordered driver sets, validation issues, and projection metadata including the normalized `decomposition_limit`.

`attribute` does not accept `calendar_policy_ref`. If either side requires calendar alignment semantics, the two internal `observe` steps freeze that metadata in `resolved_policy_summary`, and the derived intent reuses it only through the internal `compare(mode = "scalar")` step. `attribute` must not rebuild holiday / weekday / event pairing on its own.

Recommended semantic error codes:

- `INVALID_ARGUMENT`
- `NOT_COMPARABLE`
- `NOT_ATTRIBUTABLE`

### `POST /sessions/{session_id}/intents/diagnose`

Submits the `diagnose` derived intent.

Request body fields:

- `metric`: required semantic metric
- `time_scope`: required detect time scope; v1 requires `mode = "single_window"`
- `scope`: optional non-time scope
- `detect_split_by`: optional single semantic dimension
- `candidate_dimensions`: required non-empty semantic dimension list for follow-up attribution
- `profile`: optional detect profile
- `sensitivity`: optional detect sensitivity
- `candidate_limit`: optional bound for the internal `detect` candidate set
- `followup_limit`: optional bound for followed candidates
- `decomposition_limit`: optional bound for per-dimension driver rows

Deterministic expansion:

1. `detect(...)`
2. follow the top `K` candidates in detect ranking order
3. derive a fixed adjacent equal-length baseline for each followed candidate
4. `observe(current)` and `observe(baseline)`
5. `compare(mode = "scalar")`
6. `decompose(...)` for each requested candidate dimension

Unsupported inputs include:

- auto-selecting attribution dimensions
- custom baseline policies
- unbounded fan-out across candidates or dimensions
- planner-style branching based on intermediate results

Success returns `DiagnoseArtifact`, the canonical `diagnosis_bundle`. The payload includes top-level validation, source `detect` provenance, detect summary, and one result per followed candidate with compare refs, driver sets, and per-candidate issues.

Omitted bounded controls are allowed only when the service can normalize them to deployment-defined bounded defaults. Successful responses must disclose normalized values wherever the artifact contract exposes them.

Recommended semantic error codes:

- `INVALID_ARGUMENT`
- `UNSUPPORTED_OPERATION`
- `NOT_COMPARABLE`
- `NOT_ATTRIBUTABLE`

### `POST /sessions/{session_id}/intents/validate`

Submits the `validate` derived intent.

Request body fields:

- `metric`: required semantic metric
- `left`: required inferential-ready scalar-observation input
- `right`: required inferential-ready scalar-observation input
- `sample_kind`: `auto`, `numeric`, or `rate`; defaults to `auto`
- `hypothesis`: optional difference-hypothesis contract
- `method`: `auto`, `welch_t`, or `two_proportion_z`; defaults to `auto`

Normalization rules:

- `sample_kind` defaults to `auto`
- `hypothesis.family` defaults to `difference`
- `hypothesis.alternative` defaults to `two_sided`
- `hypothesis.alpha` defaults to `0.05`
- `method` defaults to `auto`

Deterministic expansion:

1. determine the inferential-ready observation mode from `sample_kind`
2. `observe(left, result_mode = inferred_mode)`
3. `observe(right, result_mode = inferred_mode)`
4. `test(left_ref, right_ref, hypothesis, method)`

Unsupported inputs include:

- auto-deriving either side
- non-difference hypothesis families
- raw sample arrays
- multi-arm or paired tests
- planner-style sample preparation beyond the declared contract

Success returns `ValidateResponse`, the canonical `validation_bundle`. The payload includes normalized left/right scopes, resolved `sample_kind`, normalized hypothesis, method, derived refs, validation issues, provenance, and the packaged inferential result.

`validate` does not accept `calendar_policy_ref`. If either side requires calendar alignment semantics, the two internal `observe` steps freeze that metadata in `resolved_policy_summary`, and the derived intent reuses it only through the internal `test(left_ref, right_ref, ...)` step. `validate` must not rebuild holiday / weekday / event pairing or reselect calendar versions on its own.

Recommended semantic error codes:

- `INVALID_ARGUMENT`
- `UNSUPPORTED_OPERATION`
- `NOT_COMPARABLE`
- `INSUFFICIENT_DATA`

## Relationship To Plans, State, And Context

This document defines direct step submission only.

Use other API surfaces for adjacent concerns:

- [`session-lifecycle.md`](session-lifecycle.md) for creating and managing sessions
- `planning.md` for validating and executing multi-step plans
- [`session-state.md`](session-state.md) for the canonical session-level read surface
- [`context-surface.md`](context-surface.md) for proposition-level closure

Clients that need validation, approval, dependency wiring, or multi-step orchestration should use plans rather than issuing a long chain of ad hoc step submissions.

## Non-goals

This contract does not define:

- legacy `metric_query`, `aggregate_query`, `attribute_change`, or generic step endpoints
- read APIs for step history or artifact retrieval
- projection-specific read resources
- free-form SQL execution
- template or planner semantics that require mid-execution branching decisions
