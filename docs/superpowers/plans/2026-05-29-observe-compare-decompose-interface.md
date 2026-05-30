# observe / compare / decompose Interface Conformance & Agent Ergonomics Plan

**Goal:** Bring the three core analysis intents (`observe`, `compare`, `decompose`)
into conformance with the committed target-state operator design
(`docs/specs/analysis/python-analysis-operator-design.md`) and make them
predictable for general agents, by making output shape an explicit declarable
contract, moving compatibility checks ahead of backend execution, and reconciling
the component-aware contract with the "fixed output shape" principle — without
breaking the single-entry-point workflow the component contract guarantees. The
win is that shape and failure become **predictable and checkable before
execution**, not that the intents get fewer cases: `observe` keeps its single
entry point and its full shape matrix by design.

**Architecture:** Do not rewrite the execution/alignment/attribution math. Land
one standalone bugfix, then wrap the existing intent executors with two thin
layers and one reconciliation, in this dependency order:
1. a **typed shape model** so callers can declare and read frame shape
   (`MetricFrame[scalar|time_series|segmented|panel]`, `DeltaFrame[...]`,
   `AttributionFrame[sum|ratio_mix|weighted_mix]`) instead of discovering
   `semantic_kind` after execution — and so each intent's output shape is a
   deterministic function of its inputs;
2. a **pre-submit validation layer**: internal validators the three intents call
   *before* any backend work (raising the existing structured exceptions
   earlier), plus an optional thin `session.validate(intent, *frames, **params)`
   that runs the same checks over concrete in-hand inputs and returns issues
   without raising. **No backend execution.** A `StepRequest`-based multi-step
   plan validator and cost-bearing `session.estimate` are both deferred (see
   Deferred follow-ups);
3. reconcile the **component-aware output** as a declared/predictable shape
   instead of implicit dispatch on a hidden `component_ref`.
Then consolidate `observe`'s four copy-pasted persistence tails into one.

**Tech Stack:** Existing `marivo.analysis.intents.*`, `marivo.analysis.frames.*`,
`marivo.analysis.session.*`, the existing structured `SemanticError`/`AnalysisError`
exception hierarchy (`marivo.analysis.errors`), pydantic frame metas, repository
`make test`, `make typecheck`, `make lint`, `make format`, `make examples-check`.

**Source of truth:** `docs/specs/analysis/python-analysis-operator-design.md`
(operator design) and
`docs/superpowers/specs/2026-05-28-component-aware-frame-contract-design.md`
(component contract). Where this plan changes a public contract, the locked
decision must be written back into those specs and into
`marivo-skills/marivo-analysis/references/examples/` in the same change.

---

## Diagnosis

All three intents share one root problem for agent consumers: **output shape and
schema are implicit (derived from a parameter matrix or hidden state) and every
incompatibility is a runtime error after the backend has already run.** Concretely:

- **`observe` shape is a 2×2 implicit matrix.** `semantic_kind` is `scalar /
  time_series / segmented / panel` purely as a function of `window.grain` and
  whether `dimensions` is set
  ([observe.py:1539-1672](../../../marivo/analysis/intents/observe.py)). The
  signature returns a bare `MetricFrame`; the shape is only knowable after
  execution. The matrix also has runtime-only holes
  (`WindowedTimeSeriesUnsupported`, `SegmentedMultiDatasetUnsupported`,
  `DerivedTimeSeriesUnsupported`, …). The operator design instead specifies
  `MetricFrame[scalar|time_series|segmented|panel]` as a **compile-time / plan-time
  gate** (operator design §"Shape-aware 签名", and the final operator table).
- **No pre-submit validation.** Cross-shape `compare`, `alignment` not applicable
  to a shape ([compare.py:511-547](../../../marivo/analysis/intents/compare.py)),
  axis-not-in-panel ([decompose.py:402-417](../../../marivo/analysis/intents/decompose.py)),
  and the `observe` unsupported holes are all raised *after* execution starts.
  For an agent this turns N expensive failed round-trips into 1 cheap check.
- **`decompose` returns two different column schemas from one signature.** With a
  hidden `component_ref` it emits `value_effect / mix_effect / residual / shares
  / …`; without it, `contribution / pct_contribution / rank`
  ([decompose.py:213-231](../../../marivo/analysis/intents/decompose.py) vs
  [decompose.py:467-479](../../../marivo/analysis/intents/decompose.py)). This is
  **deliberate** per the component contract (single entry point, workflow
  unchanged), but it contradicts the operator design's fixed-output-shape
  principle and is unpredictable for agents. The two specs must be reconciled.

Secondary issues: `decompose(measure_column: str)` is a stringly-typed param not
present in the operator-design `decompose` signature and is silently rejected on
the component path; `axis` is typed as `DimensionRef` but resolved through a
4-step string heuristic ([decompose.py:57-94](../../../marivo/analysis/intents/decompose.py));
`SlicePredicate.value` is `Any` ([_types.py:10-16](../../../marivo/analysis/intents/_types.py));
`_triggered_by` leaks into the public `decompose` signature
([decompose.py:271-277](../../../marivo/analysis/intents/decompose.py)).

Separately and independently of the redesign, `observe` has four near-identical
persistence/lineage/evidence tails, one of which (derived + segmented,
[observe.py:1391-1481](../../../marivo/analysis/intents/observe.py)) persists via
`write_frame_to_disk` and **skips `commit_result`**, unlike every other `observe`
path — so derived segmented metrics appear not to seed evidence like their
non-derived twins. This is a latent correctness bug, fixed standalone in Phase 0.

---

## Scope

This plan covers only the public interface and pre-submit ergonomics of
`observe`, `compare`, `decompose`, the standalone evidence bugfix, and the
internal consolidation. It does **not**:

- change alignment, attribution, or component math results;
- add new core operators or composite operators;
- implement derived time-series/panel or multi-dataset federation that the code
  currently rejects (those holes are *declared* up front, not filled);
- introduce a `StepRequest` / plan model or a multi-step pre-submit DAG validator
  (deferred — needs its own committed spec; see Deferred follow-ups);
- implement cost-bearing `session.estimate(...)` (scanned rows / bytes / latency /
  fanout) — deferred; this plan ships compatibility validation only;
- change `discover`, `correlate`, `test`, `forecast`, `assess_quality` — but the
  shape model and validators are designed so those intents can reuse them later
  rather than growing a second validation dialect.

---

## Resolved decisions (D1–D4)

**D1 — Single entry point + typed `semantic_shape` discriminant + narrowing
accessors. (Chosen: a)**
- Decision: keep single `observe`/`compare`/`decompose`. Expose the semantic shape
  as a public discriminant `semantic_shape` (wrapping the existing
  `meta.semantic_kind` `Literal["scalar","time_series","segmented","panel"]`) plus
  typed narrowing accessors (`as_time_series()`, `as_panel()`, …) that assert and
  raise structured errors. Add optional `expect_shape=` to `observe` as a guard.
  Shape is *derived* from inputs, never a new required arg.
- Why: the operator design final table commits to a single
  `observe -> MetricFrame[scalar|time_series|segmented|panel]`, and §"Typed shape
  narrowing" uses assert-accessors; closed typed shapes within a family are
  explicitly sanctioned.
- Rejected (b) split entry points: contradicts the committed single-`observe`
  contract and fragments the unified metric entry.
- Gotcha: **`shape` is already taken** — `frame.shape` returns dataframe
  `(rows, cols)` ([base.py:163](../../../marivo/analysis/frames/base.py)). Use
  `semantic_shape` for the discriminant; do not overload `.shape`.

**D2 — Component-aware output is a closed shape within the attribution family;
single entry point. (Chosen: a)**
- Decision: model as `AttributionFrame[sum|ratio_mix|weighted_mix]`. The output
  frame records its own shape via the `method` field decompose already writes
  (`sum`/`ratio_mix`/`weighted_mix`). The **pre-call prediction** of which shape a
  given delta will produce must read the *input* delta, **not** `method` (which
  does not exist on a `DeltaFrame` — [delta.py:17-29](../../../marivo/analysis/frames/delta.py)
  has only `component_ref` and `decomposition`). Prediction rule: `component_ref
  is None` → `sum`; otherwise map `decomposition_kind` (`ratio` → `ratio_mix`,
  `weighted_average` → `weighted_mix`). The authoritative source is the linked
  `ComponentFrameMeta.decomposition_kind`
  ([component.py:20](../../../marivo/analysis/frames/component.py)); for a cheap
  read without loading the component frame, `DeltaFrameMeta.decomposition["kind"]`
  mirrors it. `decompose` stays the single entry point.
- Why: the component contract commits to "decompose remains the single user entry
  point / workflow unchanged"; the operator design fixes the *family*
  (`attribution_frame`) but explicitly allows closed typed shapes within a family.
- Rejected (b) `decompose_mix(...)`: violates the component contract's
  single-entry-point goal.

**D3 — Remove `measure_column` from public `decompose` as an explicit breaking
change. (Chosen: drop — ratified.)**
- Decision: remove `measure_column` from the `decompose` intent
  ([decompose.py:271](../../../marivo/analysis/intents/decompose.py)) and the
  `Session.decompose` wrapper
  ([core.py:382](../../../marivo/analysis/session/core.py)); attribute the `delta`
  column only. Reintroduce later only behind a typed selector if a real need
  appears.
- Why: the operator-design `decompose` has no measure selector, and `decompose`
  answers "who contributed to the *change* (delta)". Attributing `pct_change` is
  not additive (per-segment `pct_change` does not sum to the overall
  `pct_change`), and attributing `current`/`baseline` levels is segmented-`observe`
  territory; the component path already rejects non-`"delta"`.
- Correction to the prior draft: this is **not** unused — the earlier "nobody
  references it" was a bad grep (`grep -v "decompose.py"` also excluded
  `tests/test_analysis_decompose.py`). It is deliberately supported and tested:
  `test_decompose_sum_delta_still_accepts_non_default_measure_column`
  ([test_analysis_decompose.py:283](../../../tests/test_analysis_decompose.py),
  which decomposes `pct_change`), a missing-column error case via
  `measure_column="missing"`
  ([test_analysis_decompose.py:247](../../../tests/test_analysis_decompose.py)),
  and the component-path rejection
  ([test_analysis_component_compare_decompose.py:429](../../../tests/test_analysis_component_compare_decompose.py)).
  So this is a **breaking removal of tested behavior**. (Skill docs/examples are
  unaffected: their `measure_column` is all `promote_metric_frame`, not
  `decompose`.)
- Rejected (b) keep + tighten: keep `measure_column`, document `delta` as
  canonical, retain sum-path flexibility — no break, but diverges from the
  operator-design signature.
- Consequence: this is a public-contract break handled in Phase 3 with full
  test/wrapper migration; Phase 2 validators reference the `delta` column, never
  `measure_column`.

**D4 — Internal validators now + a thin concrete-input `session.validate`; no
StepRequest/plan API; cost `estimate` deferred. (Chosen: a, narrowed)**
- Decision (answers the open question): this change set ships (1) **internal
  validators** the three intents call before any backend work, raising the
  *existing* structured exceptions earlier (so error types/messages are preserved
  by construction); and (2) an optional thin, non-raising
  `session.validate(intent, *frames, **params) -> ValidationIssue[]` that runs the
  same validators over **concrete frames already in hand** (no plan/DAG). It does
  **not** introduce a `StepRequest`, planned-upstream-step refs, or multi-step
  pre-submit plan validation — that needs its own `StepRequest`/`ValidationResult`
  spec and is deferred.
- Issue model: do **not** reuse evidence `BlockingIssue`
  ([followups.py:46](../../../marivo/analysis/followups.py)) — its `kind` enum is
  quality/evidence/comparability-oriented and has no shape-mismatch / unsupported-
  matrix / axis-not-in-panel / alignment-not-applicable variants. Define a small
  dedicated `ValidationIssue` that carries the originating exception's class name
  and its existing structured `details` (which already include a `kind` code such
  as `"AlignmentPolicyNotApplicable"`, `"SegmentDimensionMismatch"`). This keeps
  raising and non-raising paths from drifting and avoids polluting an evidence type.
- Why: validators are the high-leverage, backend-free, contract-free ergonomics
  fix; the planning API and cost estimation are the parts that ballooned scope.
- Rejected (b) helpers-only with no public surface: fine, but the thin
  concrete-input `validate` is cheap and useful and needs no new contract.
- Consequence: Phase 1 still precedes Phase 2 because validators reason in the
  shape vocabulary, but the strict "planned-step output-shape function" coupling
  is gone with the deferred plan API.

---

## Phases

### Phase 0 — Standalone evidence bugfix (independent of the redesign; ship first)

- [ ] Confirm against `docs/specs/analysis/python-track-evidence-surface.md`
  whether segmented derived metrics are intended to seed evidence (the
  non-derived segmented path does, via
  [observe.py:1733](../../../marivo/analysis/intents/observe.py)).
- [ ] If intended, route the derived+segmented path
  ([observe.py:1446](../../../marivo/analysis/intents/observe.py)) through
  `commit_result` like every other observe path instead of bare
  `write_frame_to_disk`.
- [ ] Tests: derived segmented observe seeds evidence identically to non-derived
  segmented; expect evidence-store fixture/snapshot churn and update fixtures.

### Phase 1 — Typed, declarable shape model (prerequisite for Phase 2)

- [ ] Expose the semantic discriminant as a public `semantic_shape` property over
  the existing `meta.semantic_kind` (no new persisted fields). **Do not name it
  `shape`** — `frame.shape` is the dataframe `(rows, cols)`
  ([base.py:163](../../../marivo/analysis/frames/base.py)).
- [ ] Add typed narrowing accessors (`as_time_series()`, `as_panel()`,
  `as_segmented()`, `as_scalar()`; `AttributionFrame.as_sum()/as_ratio_mix()/...`)
  that assert and raise a structured error on mismatch.
- [ ] Express each intent's output shape as a pure function of its inputs —
  observe: the 2×2 matrix + derived-kind; compare: `shape_for(input)`; decompose:
  `AttributionFrame[...]` predicted from the input delta's `component_ref` +
  `decomposition_kind` per D2 (**not** `method`).
- [ ] Add optional `expect_shape=` to `observe` (and accept shape assertions on
  `compare`/`decompose` inputs) validated locally before submit.
- [ ] Surface `semantic_shape` in `summary()` / frame repr.
- [ ] Tests: declared-shape mismatch raises a structured error; the output-shape
  function matches executed `semantic_kind` across the matrix; the decompose shape
  predictor matches the `method` actually written by decompose; shape round-trips
  through persistence.

### Phase 2 — Pre-submit validators (built on Phase 1)

Extract the currently-runtime compatibility checks into validators that need the
semantic IR and frame metadata but **no backend execution**.

- [x] Add `marivo/analysis/intents/_validate.py`, generalizing the existing
  `validate_shape_columns(...)`
  ([_candidate_columns.py:188](../../../marivo/analysis/intents/_candidate_columns.py))
  pattern. **Design it shape-keyed for reuse by `discover`/`correlate`/`test`**,
  not hardcoded to three intents:
  - compare: same `metric_id`, same `semantic_kind`, segment-column / panel-grain
    match, and `alignment.kind` × `semantic_kind` applicability (mirrors
    [compare.py:457-547](../../../marivo/analysis/intents/compare.py)).
  - decompose: `axis` resolves to a real column; for panel, axis ∈ panel
    dimensions; the attributed `delta` column is numeric (mirrors
    [decompose.py:316-417](../../../marivo/analysis/intents/decompose.py)). **No
    `measure_column` check — it is removed in Phase 3.**
  - observe: the supported (shape × derived-kind × dataset-count) matrix, rejecting
    the known holes here instead of mid-execution. **Deferred to Phase 4** — the
    observe validator's holes sit at non-adjacent sites with a dimension-resolution
    step between them; consolidating them without changing error precedence is best
    done alongside the Phase 4 observe-tail consolidation.
- [x] Have `compare`/`decompose` call these validators before any
  backend work; on failure they raise the **existing** structured exception types
  (just earlier), so messages/types are unchanged. (`observe` wiring deferred to
  Phase 4.)
- [x] Add an optional thin `session.validate(intent, *frames, **params)` that runs
  the same validators over concrete in-hand frames and returns a dedicated
  `ValidationIssue[]` (exception class + existing `details`) without raising. **Do
  not** reuse evidence `BlockingIssue`; **do not** introduce a `StepRequest` or
  plan-level validator (deferred). Covers `compare` and `decompose` only;
  `observe` extension deferred to Phase 4.
- [x] Tests: each previously runtime incompatibility now fails before backend
  execution with the same exception type; `session.validate(...)` over the same
  concrete inputs returns the corresponding `ValidationIssue` and raises nothing.

### Phase 3 — Reconcile component-aware output as a declared shape (D2, D3)

- [x] Introduce `AttributionFrame[sum|ratio_mix|weighted_mix]` shape (output tag
  from the persisted `method`; predictor per D2) so the schema is predictable and
  assertable; `decompose` stays the single entry point.
- [x] Make the branch observable up front: given an input `DeltaFrame`, the agent
  and `session.validate` determine the resulting `AttributionFrame[...]` from
  `DeltaFrameMeta.component_ref` + `decomposition["kind"]`
  (`ComponentFrameMeta.decomposition_kind` authoritative) before calling.
- [x] Remove `measure_column` (D3, ratified) from the intent
  ([decompose.py:271](../../../marivo/analysis/intents/decompose.py)) and the
  `Session.decompose` wrapper
  ([core.py:382](../../../marivo/analysis/session/core.py)); fix the value column
  to `delta` and delete the now-dead `measure_column != "delta"` component-path
  guard; update the `decompose` docstring and the operator-design + component
  specs. Migrate tests: remove/repurpose
  `test_decompose_sum_delta_still_accepts_non_default_measure_column` and the
  `measure_column="missing"` case
  ([test_analysis_decompose.py:247,283](../../../tests/test_analysis_decompose.py)),
  keeping missing/non-numeric `delta`-column error coverage on the default path;
  remove the component-path rejection test
  ([test_analysis_component_compare_decompose.py:429](../../../tests/test_analysis_component_compare_decompose.py));
  add a test asserting `decompose(..., measure_column=...)` raises `TypeError`.
  Skill examples are unaffected (their `measure_column` is all
  `promote_metric_frame`).
- [x] Tests: ratio/weighted/sum each produce the declared shape and match the
  pre-call prediction; assertion errors are structured; existing contribution-
  reconciliation tests unchanged; public `decompose` no longer accepts
  `measure_column`.

### Phase 4 — Internal consolidation + API hygiene

- [x] **(Moved from Phase 2)** Add the `observe` pre-submit validator to
  `_validate.py` for the two multi-dataset holes (`WindowedTimeSeriesUnsupported`,
  `SegmentedMultiDatasetUnsupported`) and wire `observe` to call it before backend
  work, raising the same `MetricShapeUnsupportedError`.
- [x] Collapse `observe`'s four persistence/lineage/evidence tails
  into one shared helper `_commit_observe_metric_frame(...)`. Per-branch meta,
  component-attach, and job-record writing remain in the branches; only the
  `commit_result(...)` invocation is shared. The derived-segmented branch was
  migrated from `write_frame_to_disk` to `commit_result` as part of this
  consolidation (Phase 0 had already fixed the standalone bug, but the branch
  still used the old persistence path).
- [x] Remove `_triggered_by` from the public `decompose` signature and its
  `TriggeredByFollowup` import; stop passing it from the followup runner. The
  parameter was dead (never referenced in the body) — `decompose` now has the
  clean signature `(frame, *, axis, session)`.
- [ ] ~~Tighten `axis` resolution to a single catalog-resolved step~~
  **Deferred.** The failure is already structured (Phase 2's
  `validate_decompose_columns` raises `SemanticKindMismatchError` with
  `requested_axis`/`available_columns`). Replacing the dotted-id/ref/normalized
  resolution with a single catalog-resolved step is a behavior change that risks
  breaking tested resolutions (e.g.
  `axis=DimensionRef("trino_query.department")` → column `department`,
  [test_analysis_decompose.py:143]). Low value, real risk.
- [ ] ~~Tighten `SlicePredicate.value` typing~~ **Deferred.** The runtime
  validation already exists and is exhaustive:
  `_validate_slice_value_shape` ([runner.py:928-943]) rejects malformed
  predicates (scalar ops → scalar, `in` → non-empty collection, `between` →
  exactly two values). The only residual is narrowing the `value: Any`
  TypedDict field, which `TypedDict` cannot express per-op without a verbose
  union-of-TypedDicts that changes the public type for negligible added safety.
- [x] Tests: `validate_observe` unit tests; observe multi-dataset holes still raise
  via the validator; public `decompose` signature is `frame`/`axis`/`session` only
  (guarded by `test_analysis_decompose_signature.py`).

---

## Success criteria

- Every compatibility failure for the three intents is raised **before backend
  execution** by the internal validators with the existing exception types, and is
  also reportable (non-raising) via `session.validate(...)` over concrete inputs.
- The returned frame's `semantic_shape` is declarable and readable without
  executing; the output-shape function matches executed `semantic_kind`; and
  `decompose`'s output schema is predictable from the input delta before the call.
- Existing persisted frames (with and without `component_ref`) still load.
- `make test`, `make typecheck`, `make lint`, `make format`, `make examples-check`
  pass; component reconciliation and existing alignment/attribution numeric tests
  are unchanged.
- Operator design + component contract specs and analysis skill examples reflect
  any locked public-contract change in the same change set.

## Risks

- Phase 0 and Phase 4 change evidence-store contents for the derived-segmented
  path → fixture/snapshot churn; gate Phase 0 on confirming the intended contract
  in the evidence-surface spec first.
- Phase 3 touches a deliberately-simple user workflow; keep the single entry point
  and treat shape as additive metadata, not a new required argument.
- Phase 4 tail consolidation is larger than a mechanical extraction because the
  four branches build different metas; land it behind passing Phase 0–3 tests.

## Deferred follow-ups

- A `StepRequest` / `ValidationResult` spec and a **multi-step pre-submit plan
  validator** (validate an unsubmitted observe→compare→decompose chain before any
  step runs, propagating predicted shapes — including a planned component
  descriptor — through planned outputs). This is the planning API the first review
  flagged; it needs a committed contract before implementation.
- Cost-bearing `session.estimate(...)` / `estimate_many(...)` (scanned rows,
  bytes, latency class, fanout risk, suggested limits) per operator design
  §"Pre-submit estimate" — needs backend statistics; `validate` is built to be
  wrappable by it.
- **`SlicePredicate.value` static typing** ([_types.py:10-12]). The runtime
  validation (`_validate_slice_value_shape`, [runner.py:928-943]) already rejects
  malformed predicates exhaustively. The only residual is narrowing the `value: Any`
  TypedDict field per-op, which `TypedDict` cannot express without a verbose
  union-of-TypedDicts that changes the public type for negligible added safety.
  Deferred as an isolated typing cleanup.
- **`decompose` axis-resolution tightening** ([decompose.py:57-94]). The failure
  is already structured (`validate_decompose_columns` raises
  `SemanticKindMismatchError` with `requested_axis`/`available_columns`).
  Replacing the dotted-id/ref/normalized resolution with a single catalog-resolved
  step is a behavior change that risks breaking tested resolutions. Low value,
  real risk. Deferred.
- A non-raising `session.validate("observe", ...)` — observe has no input frames;
  pre-validation happens inside `observe()`. A pre-check would need semantic-project
  metric resolution and is a separate follow-up.
