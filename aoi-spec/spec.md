# AOI v0.1 — Analysis Operation Interface

**Date:** 2026-05-07
**Status:** Draft
**Scope:** Spec-first design for a public, schema-only standard that defines analysis-operation contracts, asymmetric to OSI (which defines analysis-object contracts).

---

## 1. Scope & Objective

AOI (Analysis Operation Interface) is a schema-only standard that defines the typed contracts for analysis operations: how an analyst, agent, or downstream system invokes a primitive analysis intent and consumes its result.

**v0.1 surface, locked**:

- **Foundation primitives package** (shared across all intents)
- **7 atomic intents**: `observe`, `compare`, `decompose`, `correlate`, `detect`, `test`, `forecast`

The filter and expression model reuses OSI's multi-dialect `Expression` pattern (`{dialects: [{dialect, expression}]}`). AOI is positioned as a sibling standard to OSI; reusing OSI's expression conventions is part of the consolidation strategy, not a parallel reinvention.

**Explicitly out of scope for v0.1**: derived intents (`attribute` / `diagnose` / `validate`), composition / DAG meta-schema, session state, evidence-engine objects, transport binding, conformance test suite (deferred), governance ceremony.

**Strategy**: Spec-first. The spec is published as an artifact independent of any implementation. Marivo serves as the reference implementation but its alignment refactor is a separate downstream project; spec quality is not gated on Marivo refactor completeness.

**Why this shape**: An analysis of the current Marivo intent schemas (≈6,000 lines across 10 intents) surfaced 11 distinct version field names, 9 distinct ready-status keywords, two parallel artifact reference shapes, two `Direction` enums, and 18–19 `comparability` / `validation` issue codes. v0.1 collapses these into a small, consolidated core. Comparison mode is promoted into AOI core because YoY / MoM / WoW and calendar-aligned variants are common analysis operation controls, not implementation escape hatches; implementation-specific execution metadata remains outside the standard.

---

## 2. Architecture: Three-Layer Model

```
┌─────────────────────────────────────────────────────────────┐
│ Layer 1: AOI Core Spec v0.1                                  │
│                                                              │
│  ┌──────────────────────────────────────────────┐           │
│  │ Foundations (shared primitives)              │           │
│  └──────────────────────────────────────────────┘           │
│                          ▲                                   │
│  ┌───────────────────────┴──────────────────────┐           │
│  │ Atomic Intents (7) — the entire v0.1 surface │           │
│  │  observe · compare · decompose · correlate · │           │
│  │  detect · test · forecast                    │           │
│  └──────────────────────────────────────────────┘           │
│                                                              │
│  All schemas: additionalProperties: false                    │
│  No private metadata envelope in v0.1                         │
└─────────────────────────────────────────────────────────────┘
┌─────────────────────────────────────────────────────────────┐
│ Layer 2: Implementation-Internal (out of spec scope)         │
│  Storage, planner, runtime, session, transport               │
└─────────────────────────────────────────────────────────────┘
```

### 2.1 Architectural Principles

1. **AOI is schema-only.** It does not specify transport (HTTP / MCP / RPC), storage, or runtime architecture. Any implementation that emits and accepts JSON conforming to AOI schemas is conformant.
2. **No private-field surface in v0.1.** Core schemas use `additionalProperties: false`; all v0.1 wire fields must be defined by AOI core. Implementation-specific metadata stays outside AOI artifacts and requests.
3. **Atomic-only.** The seven atomic intents are the complete v0.1 surface. Derived intents are not part of the standard. They may exist in implementations as private product layers but they are not AOI.
4. **No transport.** Wire format is JSON. Transport binding (HTTP path, RPC method, streaming) is implementation-defined.
5. **No runtime model.** Sessions, evidence graphs, planning, caching are out of scope.

### 2.2 What v0.1 Says vs. What It Doesn't

| Says | Doesn't say |
|------|-------------|
| Shape of every atomic-intent request and artifact | How to transport requests |
| Blocking failure structure (`failure.code` + `message`) | When implementations should re-evaluate failures |
| `artifact_id` resolution protocol (logical) | How implementations store or look up artifacts |
| No private metadata envelope in v0.1 | How implementations expose private metadata outside AOI |
| Stability tiers and `since` annotations | A versioning policy for private implementation metadata |

---

## 3. Foundations Primitives

The canonical schema defines these shared foundation primitives under `$defs.primitives`. Request and artifact definitions must reuse those anchors rather than redefining primitive shapes locally.

### 3.1 Reference & Identity

AOI uses direct string identifiers instead of reference wrapper primitives:

```jsonc
"artifact_id": "string"
```

**Constraint**: every artifact must be uniquely addressable by its `artifact_id`. The artifact identifier does not wrap extra type metadata. Producing-step identity such as Marivo's `step_id` is implementation logic outside the AOI artifact spec. Every list-shaped artifact (compare segmented rows, decompose contribution rows, detect candidates) must assign a stable `item_id` to each item.

### 3.2 Expression, Time

AOI v0.1 has no `Scope` wrapper. Filter conditions are expressed directly through an `Expression` (the OSI multi-dialect expression pattern). Time range and bucketing are two separate concerns: `TimeScope` carries `field`, `start`, and `end`; `TimeGranularity` is its own primitive that intents reference where bucketing applies.

```jsonc
// Expression — OSI-style multi-dialect boolean expression
// Same shape as OSI Expression; AOI uses it for boolean filter purpose.
{
  "dialects": [
    {
      "dialect": "string",       // e.g. "ANSI_SQL", "POSTGRESQL",
                                 // "BIGQUERY", "SNOWFLAKE", "DUCKDB".
                                 // Defaults to "ANSI_SQL" if omitted.
      "expression": "string"     // boolean SQL expression in named dialect
    }
  ]
}

// TimeScope — dataset time field plus pure time range, no bucketing concept
{
  "field": "string",
  "start": "ISO8601",
  "end": "ISO8601"
}

// TimeGranularity — bucketing enum, separate primitive
"hour" | "day" | "week" | "month" | "quarter" | "year"
```

**Expression constraints**:

- `dialects[]` must contain at least one entry.
- A given `dialect` value appears at most once in `dialects[]`.
- The named `expression` must evaluate to a boolean in the declared dialect. AOI core schema cannot syntactically validate this; implementations check at query-plan time.
- **Field-reference resolution**: identifiers in the expression resolve to fields of the metric's `observed_dataset` (same semantic as OSI `metric.filters`). Cross-dataset references are not in v0.1 scope.
- **Dialect registry**: AOI v0.1 defines no central dialect registry. Implementations must accept any dialect strings they claim through their own product documentation, but conforming AOI payloads use the OSI-style `{dialects: [{dialect, expression}]}` shape.
- **Expression safety**: AOI does not specify how implementations defend against SQL injection or other expression-level attacks. Safe parameterization is implementation responsibility; AOI defines only the wire shape.

**TimeScope / TimeGranularity notes**:

- `TimeScope` represents the dataset time field plus "what time range is the analysis over". It does not carry granularity because bucketing is meaningless for some outputs (e.g. a scalar observation has no buckets) and belongs in request-specific fields where it does apply.
- `TimeScope.field` is a required string that references the OSI dataset field used as the time axis for the slice.
- `TimeGranularity` is referenced from intents that need bucketing — `observe.granularity`, `detect.granularity`, and bucketed producing steps where downstream consumers need to know the bucket size.
- There is no separate `ResolvedTimeScope` in core; calendar-resolution details (matched-bucket counts, calendar policy summaries, holiday alignment) are execution/audit metadata outside v0.1.
- Named relative ranges (`"last_7_days"`) are out of scope. Callers resolve to absolute timestamps before invoking AOI; relative-range semantics depend on `now()` and timezone, which the spec does not define.

### 3.3 CompareType

`CompareType` is a core request control for the comparison mode. It names how the left and right references should be interpreted without standardizing implementation-owned calendar datasets, holiday/event registries, or bucket-pairing traces.

```jsonc
"normal" | "yoy" | "mom" | "wow"
| "holiday_aligned_yoy" | "weekday_aligned_yoy" | "weekday_aligned_mom"
```

**CompareType constraints**:

- It is optional. Omitted means `normal`.
- Unsupported values or unavailable calendar/event data produce a blocking `failure` with portable `failure.code`.
- The producing compare step records the selected value so downstream ref-type intents can detect mode mixing through implementation-owned step metadata.
- AOI core defines only the enum and propagation rule. It does not define holiday calendars, event catalogs, bucket-pairing algorithms, reuse policy, or matched-bucket audit metadata.

### 3.4 AnalysisFailure

AOI v0.1 does not define a "warning" or "info" channel. The only failure signal in core is **`AnalysisFailure`** — a single, blocking root cause that prevents the analysis from being executed. An artifact carries either a `result` or an `AnalysisFailure`, never both (Section 4.2).

```jsonc
// AnalysisFailure — singular, blocking-only
{
  "code": "string",        // intent-specific closed enum, only blocking codes
  "message": "string",
}
```

**Design rules**:

- Singular, not an array. A blocking failure has one root cause; if multiple conditions are detected, the implementation chooses the most-specific code and reports it. Spec does not require accumulation.
- No `severity`. Only blocking failures appear here; non-blocking caveats (low confidence, partial overlap, small sample size) are encoded in explicit result-body fields when the intent defines them, not in a separate channel.
- `code` is a per-intent closed enumeration. Each intent schema enumerates its codes; spec does not impose a hard numeric ceiling, but blocking codes are kept minimal (typically 4–6 per intent) and only added through spec PR.

#### What does NOT exist in v0.1 core

The earlier design's `Status` enum, `Gate envelope` structure, `Truncation` primitive, and `Provenance` primitive have all been removed:

- **No `Status` three-state vocabulary**: presence-or-absence of `failure` is the signal. Successful artifacts have no failure field; failed artifacts have no result.
- **No `Gate envelope`**: gate names (`comparability`, `detectability`, ...) were decorative; the result schema already identifies the operation.
- **No `Truncation` primitive**: when a request specifies a bound (`detect.limit`, `decompose.limit`), the response naturally honors it; there is no spec-level field for "could have been more". If a future common use case needs this signal, it must become an AOI core field rather than a private extension.
- **No `Provenance` primitive**: AOI does not require artifacts to carry version, execution time, or engine identity. Provenance-like data — `executed_at`, `engine_id`, `query_hash` — stays outside AOI v0.1 and can be reconsidered only through a future AOI revision with a concrete reproducibility use case.

This collapses four previously named primitives into a single, narrowly-scoped `AnalysisFailure` type.

### 3.5 Hypothesis

```jsonc
// Hypothesis — used by `test`
{
  "family": "two_sample_mean" | "two_sample_proportion"
          | "paired_mean",
  "alternative": "two_sided" | "greater" | "less",
  "alpha": number,
  "label": "string" | null
}
```

## 4. Atomic Intent Contract Pattern

Every atomic intent has a **request contract** (what callers send) and a **response contract** (what implementations return). v0.1 defines these as two separate schemas. They share no embedded structures: a request never carries result fields, and an artifact never echoes a request payload as `inputs`. Producing-step context is implementation-owned metadata outside the AOI artifact body.

### 4.1 Request Contract

The request contract is per-intent. Every request is either **source-type** (takes a metric and a slice spec) or **ref-type** (takes artifact ID fields pointing to upstream artifacts). An intent picks one and sticks to it. Requests do not carry `intent` or `spec_version`; the request schema or operation name already identifies the intent, and AOI v0.1 is followed as a complete standard rather than negotiated per request.

```jsonc
// Per-intent request payload (shape depends on the selected intent contract)
{
  // intent-specific inputs only (see table)
}
```

#### 4.1.1 Per-intent input typing

| Intent      | Input mode      | Required inputs                                                                                                                            |
| ----------- | --------------- | ------------------------------------------------------------------------------------------------------------------------------------------ |
| `observe`   | source          | `metric`, `time_scope`, `filter?`, `granularity?`, `dimensions?` (mutually exclusive mode selectors, see 4.1.2)                            |
| `detect`    | source          | `metric`, `time_scope`, `granularity: TimeGranularity`, `filter?`, `dimension?`, `strategy: "point_anomaly" \| "period_shift"`, `sensitivity?`, `limit?` |
| `test`      | source (paired) | `metric`, `left: { time_scope, filter? }`, `right: { time_scope, filter? }`, `kind: "numeric" \| "rate"`, `hypothesis: Hypothesis` |
| `forecast`  | ref             | `source_artifact_id: string`, `horizon`, `profile?`                                                                             |
| `compare`   | ref             | `left_artifact_id: string`, `right_artifact_id: string`, `compare_type?: CompareType`                                                             |
| `decompose` | ref             | `compare_artifact_id: string`, `dimension`, `limit?`                                                                                          |
| `correlate` | ref             | `left_artifact_id: string`, `right_artifact_id: string` (both time_series), `method?`                                                            |

`test` is source-type with a *paired* slice spec: it embeds two slices directly rather than referencing upstream observations. Sample-summary statistics (count, mean, std_dev for numeric kind; numerator, denominator, rate for rate kind) are computed inside `test` from the underlying data — they are not exposed as a separate AOI artifact. This keeps the standard from carrying a primitive whose only consumer is `test`.

#### 4.1.2 `observe` output mode inference

`observe` is the one intent whose output sub-type the caller picks explicitly. It does this with top-level mode selector fields instead of a nested `shape` wrapper:

```jsonc
{
  "metric": "string",
  "time_scope": TimeScope,
  "filter": Expression | null,

  // exactly one mode branch:
  // - scalar: omit both granularity and dimensions, or set both to null
  // - time_series: set granularity to a non-null TimeGranularity
  // - segmented: set dimensions to a non-empty array
  "granularity": TimeGranularity | null,
  "dimensions": [string] | null  // when present, minItems: 1
}
```

Mapping to result shape:

| Request selectors | Derived mode | Result schema |
|-------------------|--------------|---------------|
| no `granularity`, no `dimensions`; or both explicitly `null` | `scalar` | `scalar_observation_result` |
| non-null `granularity`, no `dimensions` or `dimensions: null` | `time_series` | `time_series_observation_result` |
| no `granularity` or `granularity: null`, non-empty `dimensions` | `segmented` | `segmented_observation_result` |

Schema enforces the three branches with `oneOf`. Non-null `granularity` and non-null `dimensions` are mutually exclusive; `dimensions: []` is invalid rather than a scalar alias.

### 4.2 Response (Artifact) Contract

The response contract is also per-intent. Every artifact follows one uniform envelope:

```jsonc
{
  "artifact_id": "string",

  // Mutually exclusive — exactly one of `result` and `failure`:
  "result": { /* schema-specific body, see 4.2.3 */ },
  "failure": AnalysisFailure
}
```

**Result-vs-failure invariant**: every artifact has exactly one of `result` or `failure` populated. JSON Schema enforces this with `oneOf`. Successful artifacts carry only `result`; blocked artifacts carry only `failure`. There is no "succeeded with warnings" middle state.

The canonical schema defines one `Artifact` envelope. The `result` field is a union over artifact-specific result schemas, such as `ScalarObservationResult` or `TimeSeriesDeltaResult`; there are no separate concrete artifact wrapper schemas.

`artifact_id` is preserved on both success and failure artifacts. Producing-step identifiers such as Marivo's `step_id` may be stored in implementation-owned records outside the AOI artifact spec. Failed artifacts are still legitimate analysis events.

#### 4.2.1 Result schema catalog

Eleven result schemas, no extensibility in v0.1 (observe and compare each produce three result shapes):

```
scalar_observation_result
time_series_observation_result
segmented_observation_result
scalar_delta_result
time_series_delta_result
segmented_delta_result
delta_decomposition_result
anomaly_candidates_result
association_result
hypothesis_test_result
forecast_series_result
```

#### 4.2.2 Step identity

Artifacts do not carry `step_id` or an embedded `subject` in AOI v0.1. The producing step remains the implementation-owned place for request metadata such as metric, filter expression, comparison mode, source artifact IDs, or hypothesis settings. This keeps artifact payloads focused on analytical output and avoids duplicating step information.

#### 4.2.3 Result-body specifications

Result bodies are minimal: only analytical output, with no request or step metadata repeated. `DimensionKeyMap` is `Record<string, string>` and is used for segmented row keys and split-series keys.

```jsonc
// scalar_observation_result
result: { "value": number | null }

// time_series_observation_result
result: { "points": [ { "bucket_start": "ISO8601",
                        "value": number | null } ] }

// segmented_observation_result
result: { "rows": [ { "item_id": string,
                      "keys": DimensionKeyMap,
                      "value": number | null } ] }

// scalar_delta_result
result: { "left_value": number | null,
          "right_value": number | null,
          "delta": number | null,
          "matched_time_scope": TimeScope | null }

// time_series_delta_result
result: { "points": [ { "bucket_start": "ISO8601",
                        "left_value": number | null,
                        "right_value": number | null,
                        "delta": number | null } ],
          "matched_time_scope": TimeScope | null }

// segmented_delta_result
result: { "rows": [ { "item_id": string,
                      "keys": DimensionKeyMap,
                      "left_value": number | null,
                      "right_value": number | null,
                      "delta": number | null } ],
          "matched_time_scope": TimeScope | null }

// delta_decomposition_result
result: { "items": [ { "item_id": string,
                       "key": /* dim value */,
                       "contribution": number,
                       "share": number } ] }

// anomaly_candidates_result
result: { "items": [ { "item_id": string,
                       "bucket_start": "ISO8601",
                       "value": number,
                       "score": number,
                       "series_keys": DimensionKeyMap | null } ] }

// association_result
result: { "coefficient": number,
          "p_value": number | null,
          "n_pairs": integer,
          "matched_time_scope": TimeScope | null }

// hypothesis_test_result
result: { "statistic": number,
          "p_value": number,
          "decision": { "reject_null": boolean | null },
          "assumption_notes": [string] }

// forecast_series_result
result: { "points": [ { "bucket_start": "ISO8601",
                        "value": number,
                        "ci_low": number | null,
                        "ci_high": number | null } ] }
```

`value` semantics: for observation bodies, `value` is the metric's aggregated value over the slice (the single scalar produced by applying the metric's aggregation function to the data inside the requested `time_scope` and `filter`, using `time_scope.field` as the time axis). It is always numeric; `null` signifies "no observation existed for this slice/bucket/segment", never "actual zero" and never "computation failed".

Numeric result semantics:

| Field family | Range | High/low interpretation |
| --- | --- | --- |
| Observation `value`, anomaly candidate `value`, forecast `value`, and compare `left_value` / `right_value` | Metric domain; nullable fields use `null` only for absent observations. | Higher/lower follows the metric definition. AOI does not encode whether high is good or bad. |
| Compare `delta` | Metric-domain difference, nullable when not computable. | Positive means left/current is higher than right/baseline; negative means lower; larger absolute value means a larger change. |
| `delta_decomposition_result.items[].contribution` | Signed metric-domain contribution; finite number with no fixed schema bound. | Positive increases the compared delta; negative offsets it; larger absolute value means a stronger driver. |
| `delta_decomposition_result.items[].share` | Signed ratio to total delta; finite number with no fixed schema bound because offsetting contributors may produce negative or greater-than-1 shares. | Same sign as the total delta reinforces the change; opposite sign offsets it; larger absolute value means higher relative importance. |
| `anomaly_candidates_result.items[].score` | Non-negative number, `[0, +infinity)`. | Higher means more anomalous within the same implementation and scoring profile. Scores are not portable severity labels. |
| `association_result.coefficient` | `[-1, 1]`. | Higher positive values mean stronger positive association; values near `0` mean weak/no association under the chosen method; lower negative values mean stronger inverse association. |
| `p_value` fields | `[0, 1]`. | Lower means stronger evidence against the relevant null model; higher means weaker evidence. |
| `hypothesis_test_result.statistic` | Hypothesis-family specific finite number. | Sign and magnitude are interpreted by `hypothesis.family` and `alternative`; larger absolute values usually mean stronger separation from the null model. |
| `association_result.n_pairs` | Integer `[0, +infinity)`. | Higher usually means a more stable estimate, subject to data quality. |
| Forecast `ci_low` / `ci_high` | Metric domain or `null`; when both are present, `ci_low <= ci_high`. | Wider intervals mean more forecast uncertainty; bounds represent lower and upper plausible outcomes in metric units. |

Non-blocking caveat fields are narrow and intent-specific. `matched_time_scope` records the overlap actually used when a comparison or association succeeds with only partial temporal overlap; when present it uses the same `field` + range shape as `TimeScope`, and `null` means the full requested/reference scope was used. `assumption_notes` records portable, non-blocking hypothesis-test caveats such as "normality not assessed"; an empty array means no caveat was reported. Implementations must not use these fields for blocking conditions that should be represented as `failure`.

#### 4.2.4 List-with-items strict constraint

Every row in `*_observation.rows[]`, `*_delta.rows[]`, `delta_decomposition.items[]`, and `anomaly_candidates.items[]` must carry an `item_id`. Consumers use `item_id` for stable row addressing within list-shaped artifacts.

#### 4.2.5 Forecast is not an observation

Forecast output uses the `forecast_series_result` result schema and is **not** under the observation family. This separates epistemic status: observations are read from data; forecasts are projected by models. The two are different categories of artifact.

### 4.3 Failure Codes

Each intent declares its blocking-failure codes as a **closed enumeration** in its schema.

- Format: `<intent>.<lower_snake_case_code>`. Examples: `compare.scope_mismatch`, `detect.insufficient_data`, `decompose.metric_not_attributable`.
- Codes are scoped per intent (the intent name is the namespace prefix); there is no shared code namespace across intents.
- v0.1 does not impose a numeric ceiling on codes per intent, but the spec author is expected to keep blocking codes minimal — typically 4–6 per intent — and only add codes through spec PR. The earlier draft cap of "≤ 8" per validation envelope is removed because gates no longer exist as a wire concept; the gate-name table that previously sat here (`compare → comparability`, `detect → detectability`, etc.) is also removed.

Implementation-specific debug traces stay outside AOI v0.1. Blocking diagnostics that belong on the wire use `failure.code` and `failure.message`.

### 4.4 Projection Layer

Each intent's spec must define both:

- The **complete artifact** (the canonical reference target for downstream steps).
- An **agent-facing projection rule** — a deterministic derivation from the artifact.

Projection rules in v0.1 must:

- Be derivable by any consumer from the artifact alone.
- Not introduce new schema types.
- Not recompute analysis. If projection summarizes a list (e.g. top-K rows for an agent), it may only summarize rows already present in the complete artifact — projection cannot widen or contradict the artifact's bounds.

Projection is not a separate artifact schema. The `DiagnoseProjection`-style separate top-level type is rejected for v0.1.

---

## 5. Conformance Boundary

AOI v0.1 defines no private metadata envelope. A conforming AOI request or artifact must contain only fields defined by AOI core; schema validation rejects unknown fields through `additionalProperties: false`.

Implementation-private metadata may exist in SDKs, logs, database rows, traces, HTTP headers, or implementation-specific APIs, but it is not part of AOI v0.1 and must not be required to interpret a conforming AOI artifact.

### 5.1 Row-level data

AOI v0.1 has no row-level private metadata attachment point. Every list-shaped core result row carries `item_id` for stable reference, but no implementation-specific row metadata is attached to AOI artifacts.

Current Marivo row-level candidates remain outside AOI v0.1:

| Row-level candidate | v0.1 decision | Reason |
|---------------------|---------------|--------|
| Calendar bucket-pairing rows (`bucket_pairing[]`, `pairing_reason`, `strictness_level`, `is_reused_baseline_bucket`) | Do not include | These explain how an implementation resolved a comparison, but consumers can consume the resulting `*_delta` values without them. `compare_type` on the producing step is sufficient to prevent mode mixing; detailed pairing is operator/debug audit data outside AOI v0.1. |
| Matched bucket counts and coverage ratios on time-series rows | Do not include | Partial overlap is represented portably by result-level `matched_time_scope` where the successful analytical result needs that caveat. Per-row coverage ratios create a second warning surface and are not required to interpret `value: null`, `left_value`, `right_value`, or `delta`. |
| Detect candidate display labels (`flag_level`, row severity labels) | Do not include | Core `anomaly_candidates.items[].score` is the machine-readable contract. Labels are thresholded presentation choices and can be derived by UI/SDK layers without changing the artifact. |
| Compare/decompose row `direction` and `presence` | Do not include | `direction` is derivable from the sign of `delta` or `contribution`; `presence` is derivable from the left/right null pattern. Shipping both would allow disagreement with core numeric fields. |
| Row `unit` echoes | Do not include | Unit belongs to the OSI metric definition, not to each AOI row. Repeating it per row increases drift risk and is unnecessary for consumers that can resolve metric metadata. |
| Additivity or dimension-policy annotations on contribution rows | Do not include | Successful `delta_decomposition.items[]` already carries `contribution` and `share`. If additivity prevents a valid decomposition, that is a blocking failure with `failure.message`, not row metadata on a successful artifact. |
| Projection/pagination row counts (`returned_row_count`, `total_row_count`, `is_truncated`) | Do not include | AOI request limits define the bounded result. Pagination, UI projection, or "more rows existed" signals belong outside AOI v0.1 unless a future AOI revision defines a concrete transport/projection contract. |

### 5.2 Failure-detail data

Failure diagnostics belong in core `failure.code` and `failure.message`. `code` is the portable machine-readable classifier; `message` is the portable human-readable explanation. Implementation-specific structured diagnostics stay outside AOI v0.1.

---

## 6. Versioning

### 6.1 Single spec version

AOI v0.1 has a single version number:

```text
0.1.0
```

The whole spec — foundations and atomic intents — evolves as one atomic unit. There is no per-intent or per-foundation version. Per-artifact version metadata is intentionally absent from core (see Section 8.1); implementation-private engine / cache / lineage versions stay outside AOI v0.1 artifacts.

### 6.2 Stability tiers and `since`

The spec document annotates each primitive and intent with `since` and `stability`:

| Stability | Meaning | v0.1 assignment |
|-----------|---------|-----------------|
| `stable` | Field semantics fixed; minor bumps may only add fields or enum values | `observe`, `compare`, `decompose`, `detect` |
| `experimental` | Minor bumps may break | `correlate`, `test`, `forecast` |

`since` and `stability` are spec-document metadata. They do not appear on the wire.

### 6.3 Full conformance requirement

AOI v0.1 does not define a capability declaration manifest or partial-support matrix. A conforming implementation follows the full v0.1 standard: all seven atomic intents, all core artifact contracts, all core `CompareType` values, and the expression wire shape defined by AOI. Implementations may expose product-specific readiness or feature discovery outside AOI, but those surfaces are not AOI contracts.

### 6.4 Compatibility promises

| Phase | Promise |
|-------|---------|
| v0.x (experimental) | Breaking changes permitted. Each minor bump publishes a changelog enumerating breaks. |
| v1.0+ minor (`1.x → 1.(x+1)`) | Add-only. No field removal, no semantic redefinition, no enum-value removal. |
| v1.0+ patch | Documentation and clarification only. |
| Major bump (`1.x → 2.0`) | Breaks permitted; must publish migration guide. |

`experimental`-tier elements may break in minor bumps even after v1.0, with mandatory `since` updates.

### 6.5 Governance (intentionally minimal in v0.1)

v0.1 does **not** define:

- Trademarks, formal governance bodies, or voting mechanisms.
- Certification flows.

Spec changes are managed by PR + linked design document. Once external adoption exists, formal governance can be added in a later spec revision.

---

## 7. Repository Layout

### 7.1 Directory structure

The spec is materialized as this self-contained `aoi-spec/` directory, designed to be extractable later.

```
aoi-spec/
  README.md                         # repo face: what AOI is, status, links
  VERSION                           # contents: 0.1.0
  CHANGELOG.md
  spec.md                           # authoritative narrative spec

  schema/
    aoi.schema.json                 # canonical JSON Schema, all $defs inline
    aoi.schema.yaml                 # OSI-style YAML readable contract

  examples/
    observe/
      scalar-success.json
      time-series-success.json
      failed.json
    compare/
      scalar-delta.json
      comparability-failed.json
    decompose/
      top-contributors-success.json

```

`schema/aoi.schema.json` is the single validation entry point. It uses top-level `$defs` sections instead of cross-file schema fragments. `schema/aoi.schema.yaml` is an OSI-style readable contract view with top-level enumerations and snake_case schema names:

| `$defs` section | Contains |
|-----------------|----------|
| `primitives` | `Expression`, `TimeScope`, `TimeGranularity`, `CompareType`, `AnalysisFailure`, `Hypothesis` |
| `requests` | `observe`, `compare`, `decompose`, `correlate`, `detect`, `test`, `forecast` |
| `artifacts` | All eleven artifact envelope/result shapes |

This keeps the public artifact easy to copy, validate, and review while preserving internal navigation through `$defs` anchors.

### 7.2 Schema readability discipline

- `schema/aoi.schema.json` is the canonical validation schema. `schema/aoi.schema.yaml` is the human-readable contract view.
- Use `$defs` anchors and stable ordering: primitives first, requests second, artifacts third.
- Avoid cross-file `$ref` for v0.1. Users should not need to clone a folder tree or chase relative references to understand or validate AOI.
- Keep examples outside the schema file. Examples remain separate JSON files under `examples/`.
- If future maintainers want split files for authoring, they may generate the single public schema during release, but the published v0.1 source of truth remains `schema/aoi.schema.json`.

### 7.3 spec.md outline

```
1. Overview & status
2. Architecture
3. Foundations primitives
4. Atomic intents
5. Conformance boundary
6. Versioning
7. Conformance & validation (lightweight in v0.1; suite deferred)
8. Out of scope & future directions
Appendix A: Issue code reference
Appendix B: result schema catalog
Appendix C: Glossary
```

### 7.4 Conformance test suite

**Deferred to v0.2.** v0.1 ships only spec + schemas + minimal examples. A formal `conformance/fixtures/` directory and CI validation will land in a later release once spec text stabilizes.

### 7.5 Self-containment rule

`aoi-spec/` must not import, link, or reference any path outside itself. This preserves the option to extract it as a standalone repository later.

### 7.6 v0.1 location

This directory is the AOI v0.1 publication artifact. It is self-contained and can be copied into a standalone repository without resolving paths outside `aoi-spec/`.

---

## 8. Mapping to Current Marivo Schemas

Concrete evidence of consolidation. This table is also the input list for Marivo's downstream alignment refactor.

### 8.1 Version / provenance fields — fully removed from core

| Current Marivo field | AOI v0.1 destination |
|----------------------|----------------------|
| `schema_version`, `artifact_schema_version`, `derivation_version`, `detector_version`, `derived_logic_version`, `source_schema_version`, `source_contract_version`, `metric_contract_version`, `observation_schema_version`, `intent_contract_version`, `projection_version` | **All removed from artifact wire**. AOI version is declared by the published `VERSION` file; per-artifact version fields are not in core. **11 → 0** in core. |
| `query_hash`, `engine`, `executed_at` | Removed from AOI artifacts. Reconsider only if a concrete cross-implementation reproducibility workflow requires a future AOI core revision. |
| `CanonicalVersionMetadata`, `SourceLineageMetadata`, `ExecutionMetadata` | Deleted from AOI core; implementation-private lineage metadata stays outside AOI v0.1 artifacts. |

### 8.2 Status / direction / issue vocabulary

| Current inconsistency | AOI v0.1 |
|-----------------------|----------|
| 9 distinct ready-keywords (`comparable`, `attributable`, `aligned`, `detectable`, `valid`, `validated`, `forecastable`, `diagnosable`, `ready`) | **All removed**. AOI v0.1 has no status vocabulary on the wire: presence of `result` means success, presence of `failure` means blocked. No "needs_attention" middle state. |
| `direction: increase\|decrease\|flat\|undefined` (most intents) vs `up\|down\|flat\|undefined` (detect) | **`Direction` removed from spec.** Direction is `sign(value)`, derivable by consumers; "flat" requires an epsilon the spec never defined. Result bodies expose values; consumers classify direction locally. |
| `presence: both\|left_only\|right_only` on compare delta rows and decompose contribution rows | **`Presence` removed from spec.** Presence is `null pattern` of (left_value, right_value); derivable. Spec stipulates instead: `value: null` means "no observation on that side"; one-sided rows must be retained. |
| `unit` echoed on Observation, delta, contribution rows | **Removed from artifacts.** Unit is a metric-definition property in OSI; consumers retrieve unit through OSI metric metadata, not through AOI artifacts. |
| `decision.reject_null: boolean\|null` (atomic test) vs `"reject_null"\|"fail_to_reject"\|"undetermined"` (validate) | derived intents removed; atomic `test` retains `boolean\|null`. |
| 18 `ComparabilityIssue` codes (half calendar-specific, mostly warnings) | Core enumerates only blocking codes (typically 4–6); non-blocking caveats are encoded in the result body (e.g. `compare.matched_time_scope`). Calendar-specific blockers use portable core failure codes. |
| 19 `TestIssue` codes | Same treatment: core enumerates only blocking codes; assumption-violation warnings live in the result body; blocked-execution diagnostics use core `failure.message`. |
| 7 `DeltaAttributionIssue`, 7 `CorrelationIssue`, 8 `DetectabilityIssue`, 12 `ForecastabilityIssue` | Each intent enumerates only blocking codes; quality / confidence signals move into the artifact's `result` body where they belong. |

### 8.3 Reference and identity

| Current | AOI v0.1 |
|---------|----------|
| `ObservationRef` (no `artifact_id`) and `ObservationArtifactRef` (with `artifact_id`) coexist | **Direct `artifact_id` string**. Redundant implementation-specific reference wrappers are omitted because `artifact_id` resolves the artifact record. |
| `CompareArtifactRef`, `DecomposeArtifactRef`, `DetectArtifactRef`, `TestArtifactRef`, `ObservationArtifactRef` separately defined | All collapse into direct artifact ID string fields such as `left_artifact_id` and `compare_artifact_id`. |
| `DetectCandidateRef = {artifact_id, item_ref}` | **Removed from AOI v0.1**. List-shaped artifacts expose stable row `item_id` values, but v0.1 has no request or artifact field that references an individual row by contract. |
| `compare.segmented_delta.rows[].keys` (multi-dim) vs `decompose.rows[].key` (single-value) | Both retained at result-body level (different result schemas, different shapes); but every row carries `item_id`. |

### 8.4 Truncation — removed from spec

| Current | AOI v0.1 |
|---------|----------|
| `DetectTruncation`, `DecomposeProjection.{returned_row_count, total_row_count, is_truncated, ...}`, `DiagnoseDriverProjection.{...}` | **Removed.** Bounded outputs are governed by request-side limits (`detect.limit`, `decompose.limit`). "Could have been more" signals are SDK / transport concerns and out of AOI v0.1 scope. |

### 8.5 Filter / Expression / TimeScope — alignment with OSI Expression

| Current Marivo | AOI v0.1 |
|----------------|----------|
| `Scope = {constraints: Record<dim, value>, expression: ExpressionAST}` (two-field wrapper) | **`Scope` removed.** Filters use `filter: Expression \| null` directly on each intent. |
| Custom `Expression` AST with closed `op` enumeration (`and / or / not / eq / neq / in / gt / ...`) | **`Expression` re-modelled as OSI `Expression`**: `{dialects: [{dialect, expression}]}` — multi-dialect SQL boolean expression, same shape as OSI's metric/field/filter expression. |
| `TimeScope` union (`range \| named "last_7_days"`) | **`{field, start, end}` only.** Named relative ranges removed (caller resolves to absolutes). Granularity is no longer part of `TimeScope`. |
| `TimeGranularity` and time-range bundled in one primitive | **Split.** `TimeScope` carries the dataset time field plus time range; `TimeGranularity` is a separate primitive that intents reference where bucketing applies (`observe.granularity`, `detect`, bucketed artifact bodies). |
| `ResolvedTimeScope` (output form with `matched_bucket_count`, calendar resolution) | **Removed.** `matched_bucket_count` and calendar-resolution detail are execution/audit metadata outside AOI v0.1; unsupported or unmapped calendar data produces a blocking failure. |

**Why this alignment**: OSI already standardizes multi-dialect SQL expressions for metric / field / filter purposes. AOI is positioned as a sibling standard; reusing OSI's `Expression` shape for AOI's filter avoids parallel reinvention and inherits OSI's portability story (multiple dialects in one expression). The previous AST design would have been the cleaner abstract form, but it cannot express the filter expressiveness real analysis requires (`LOWER()`, `EXTRACT()`, arithmetic, CASE, UDFs) without effectively reinventing a SQL-grade AST.

**Why the merge of `constraints` + `expression`**: `constraints: {region: "US"}` is syntactic sugar for `expression: region = 'US'`. Two fields create the "which one do I use" ambiguity. With the OSI Expression model, even simple equality is just `"region = 'US'"` — short enough that the convenience wrapper has no value.

### 8.6 Derived intents — fully removed from spec

| Current | AOI v0.1 |
|---------|----------|
| `attribute`, `diagnose`, `validate` typed intents | **Not in v0.1 spec.** Marivo may retain them as private product compositions; they are not AOI standard. |
| `AttributeBundleVersion` (triple version), `DiagnoseProjection` separate top-level type, `share_suppression_policy`, `additivity_basis.capability_condition` | Not in spec; Marivo internal. |

### 8.7 Compare type / calendar / additivity

Section 5 explains why implementation-private metadata stays outside AOI v0.1. The migration summary is:

| Current field in atomic schema | Destination |
|--------------------------------|-------------|
| Legacy calendar policy selector | Replaced by AOI core `compare_type`. Values include `normal`, `yoy`, `mom`, `wow`, `holiday_aligned_yoy`, `weekday_aligned_yoy`, and `weekday_aligned_mom`. |
| `ResolvedPolicySummary`, `calendar_policy_summary` | **Not included**; `compare_type` identifies the mode, and missing builtin calendar data is a blocking failure. |
| `bucket_pairing[]`, `is_reused_baseline_bucket`, `data_coverage_summary` | **Not included in AOI v0.1**; future audit metadata requires a concrete minor revision. |
| `AnalyticalMetadata.additivity_constraints` and derived additivity basis fields | **Not included on successful artifacts**; additivity blockers use core `failure.message`. |
| `flag_level` and similar row display labels | **Not included**; UI/SDK layers may derive labels from core `score`. |
| Calendar-specific blocking issue codes | Portable core `failure.code` plus `failure.message`. |
| `pairing_basis` / `pairing_rule` single-value literals | **Deleted** (single-value literals are not contracts). |
| `gate`, `status`, `needs_attention`, `direction`, `presence`, `unit`, sample-summary observations, derived-intent bundles | **Not AOI v0.1 fields**; they are either derivable, presentation-only, folded into core result/failure fields, or private product-layer concepts. |

### 8.8 `observe.result_mode`, `forecast_series_result`, and sample-summary relocation

| Current | AOI v0.1 |
|---------|----------|
| `observe.result_mode: standard \| numeric_sample_summary \| rate_sample_summary` overloads observe with five output sub-types | `observe` now derives only three sub-types (`scalar` / `time_series` / `segmented`) from `granularity` / `dimensions` / neither. Sample-summary statistics are not produced by observe; they are computed inside `test`. |
| `numeric_sample_summary` and `rate_sample_summary` result schemas | **Removed from result schema catalog.** These are no longer wire artifacts — they were only ever consumed by `test`, so the sample-summary computation is folded into `test`'s implementation. |
| `test` was a ref-type intent consuming sample-summary observations | **`test` is now source-type with paired slice spec.** It takes `metric`, `left: { time_scope, filter }`, `right: { time_scope, filter }`, `kind`, `hypothesis` directly and computes summaries internally. |
| `Observation` discriminator with five sub-types | Three sub-types only (`scalar` / `time_series` / `segmented`). `Observation` is no longer a foundations primitive — per-result bodies are defined directly in Section 4.2.3. |
| `observe.observation_type: forecast_series` (forecast piggybacks on observe namespace) | **Removed**: forecast output uses the `forecast_series_result` result schema, not in `Observation`. |

### 8.9 Single-value literal removals

| Field | Action |
|-------|--------|
| `pairing_rule: "intersection_by_time_bucket"` (correlate) | Delete. |
| `significance_level: 0.05` (correlate) | Delete; default is implementation-decided, not schema-declared. |
| `assumptions.independence: "assumed"` (test) | Delete. |
| Diagnose / attribute single-value literals | Removed with derived intents. |

### 8.10 Quantitative consolidation summary

| Dimension | Current Marivo | AOI v0.1 core | Reduction |
|-----------|---------------|---------------|-----------|
| Distinct version field names | 11 | 0 in AOI artifacts | -100% |
| Distinct ready-status keywords | 9 | 0 (no status field on wire; result-vs-failure is the signal) | -100% |
| Direction enum variants | 2 (4 + 4 values) | 0 (removed; derived by consumer) | -100% |
| Presence enum on delta / contribution rows | 1 (3 values) | 0 (removed; derived by consumer from null pattern) | -100% |
| `unit` field echoed on artifacts | per-artifact, every observation/delta/row | 0 (unit lives in OSI metric definition, not AOI artifact) | -100% |
| Comparability issue codes | 18 | typically 4–6 blocking codes; warnings encoded in result body | -67%+ |
| Test validation issue codes | 19 | typically 4–6 blocking codes; assumption signals in result body | -68%+ |
| Distinct artifact reference shapes | 5+ | 1 (`artifact_id`) | -80% |
| Top-level intent surface | 7 atomic + 3 derived | 7 atomic | -30% |
| Result schema catalog | 13 (incl. numeric/rate sample summaries) | 11 (sample summaries folded into `test`) | -15% |
| Observation sub-types | 5 | 3 (scalar / time_series / segmented; sample summaries removed) | -40% |
| Result-body fields duplicating request | metric / time_scope / filter / unit / direction / presence echoed in every observation and delta artifact | 0 (request and response contracts separated; implementation resolves producing-step context outside the artifact body) | full lift |
| Calendar / additivity fields in atomic core | dozens, inline | `compare_type` promoted to core; detailed calendar/additivity metadata removed from v0.1 artifacts | focused lift |
| Validation envelopes (`Gate`, `Status`, `Truncation`, `Provenance`) | 4 multi-field structures, every artifact | 1 (`AnalysisFailure`, optional, mutually exclusive with `result`) | -75% structural, plus most artifacts no longer carry it |

These numbers are the consolidation evidence v0.1 stakes its credibility on.

### 8.11 What this section does not commit to

- A schedule for Marivo to complete its alignment refactor.
- Migration / dual-write strategy in Marivo.
- Endorsement of Marivo's evolution path.

These are the responsibility of a separate Marivo refactor project, decoupled from AOI v0.1 publication.

---

## 9. Risks and Open Questions

### 9.1 Risks

- **Spec / implementation drift**: v0.1 publishes before Marivo aligns. If Marivo's refactor stalls, AOI may be perceived as a paper standard. Mitigation: explicit Marivo refactor milestone follows immediately after v0.1.
- **Blocking code set too coarse**: the core per-intent failure code set may hide details that multiple implementations eventually need. Keep v0.1 small, preserve `failure.message` clarity, and promote new codes only after cross-implementation evidence appears.
- **No conformance suite at v0.1**: harder to prove conformance externally. Mitigation: examples + clear schema + planned v0.2 suite.

### 9.2 Open questions

- Sample-summary statistics are computed inside `test` (not exposed as artifacts in v0.1). If a future use case beyond hypothesis testing emerges, a dedicated `summarize` intent could be added in v0.2; revisit then.
- Should AOI v0.1 add a minimal mandatory `executed_at` timestamp on artifacts? Currently no — `executed_at` is not in core. Reconsider if cross-implementation lineage / staleness detection proves a common need.
- **Dialect registry**: should AOI eventually maintain a central enumeration of recognized `dialect` values (mirroring how OSI may evolve)? v0.1 keeps dialect as an open string while standardizing the OSI-style expression shape; revisit once multiple implementations exist.
- **Cross-dataset filter references**: AOI v0.1 confines filter expressions to fields of the metric's `observed_dataset`. A future revision may permit references to relationship-resolved fields (mirroring OSI relationship semantics). Out of scope for v0.1.
- Naming: `AOI` vs another acronym. Currently provisional; rename is permitted before v0.1 publication.

---

## 10. Summary

AOI v0.1 is a schema-only, atomic-intent-only standard for analysis operations. Its v0.1 surface is exactly:

```
foundation primitives package
+ 7 atomic intents (observe, compare, decompose, correlate, detect, test, forecast)
```

That is the entire standard. No derived intents, no composition recipes, no transport binding, no governance ceremony, no private metadata envelope, no per-artifact provenance, no truncation envelope, no status vocabulary, no Direction or Presence enum, no unit echo. Consolidation against current Marivo schemas eliminates all 11 version fields from the wire, all 9 status keywords (replaced by a result-vs-failure invariant), all derived enums whose values can be computed from data (`Direction`, `Presence`), and the four heavy validation envelopes (`Gate`, `Status`, `Truncation`, `Provenance` collapse into a single optional `AnalysisFailure`). `observe` derives its output type from mutually exclusive top-level selectors: `granularity`, `dimensions`, or neither. Comparison mode is core via `compare_type`; detailed calendar/additivity audit metadata stays out of v0.1, and blocked-execution diagnostics use `failure.message`. Filter expressions reuse OSI's multi-dialect `Expression` shape directly.

The result is a small, consolidated, defensible v0.1 that can be published independently of any implementation refactor.
