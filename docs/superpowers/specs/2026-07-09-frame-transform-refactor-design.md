# Frame-scoped transform refactor

Date: 2026-07-09
Status: Design approved, pending implementation plan

## Problem

`session.transform.*` is the one analysis family whose receiver is wrong. Three
issues compound:

1. **Family preservation is a runtime promise, not a type fact.** The public
   helpers take `frame: object` and return `MetricFrame | DeltaFrame`, so every
   transform result must be re-narrowed by the caller (and by mypy). The spec
   guarantees `MetricFrame -> MetricFrame` and `DeltaFrame -> DeltaFrame`, but
   the signatures cannot express it. Worse, `normalize` is a `MetricFrame`-only
   operation whose `DeltaFrame` rejection is a runtime `TransformArgError`
   rather than an absent method.

2. **The `session=` parameter carries no choice.** Every transform requires
   `frame.meta.session_id == session.id` and an active current session, so the
   only legal value is "the frame's own session". It is a slot the agent must
   fill with the only correct answer.

3. **Transform pollutes evidence.** `commit_result` dispatches finding
   extraction by frame family without gating on `step_type`, so a transform
   output flows through the same `metric_frame` / `delta_frame` extractors as
   `observe()` and emits `observation` digests plus `metric_value` findings.
   `knowledge().observations()` filters only by `finding_type='observation'`,
   so every `topk` / `slice` variant leaks into the observations list. This
   contradicts the `knowledge()` docstring ("observe / derive_metric_frame
   commits") and the design principle that transform reshapes without producing
   new conclusions.

Internally, the flat `op`-string dispatch that powers the public helpers pays a
recurring tax: `_transform_dispatch` plus a 12-field `_TransformParams`, and
every `_op_*` handler opens with ~20 lines of `unsupported_kwargs` defense that
rejects the other ops' kwargs at runtime. This is the cost of a mega-method that
each new op must re-pay.

## Goals

- Move transform onto the frame as a typed namespace so family preservation and
  `normalize` availability become static type facts.
- Drop the `session=` parameter; resolve the session from the environment like
  `MetricFrame.metric()` does.
- Stop transform from writing findings into the evidence store; it keeps
  artifact + lineage + job provenance only.
- Dissolve the flat `op`-string dispatch core into precise per-op functions,
  deleting the `unsupported_kwargs` defense boilerplate.
- Collapse the `topk` / `bottomk` / `order` overlap into two unambiguous
  mirror methods.

## Non-goals

- AttributionFrame does not gain `transform` (v1 already rejects it; the spec's
  `attribution_frame -> attribution_frame` row is aspirational).
- `by=` continues to take a raw column-name string. Its inconsistency with
  `slice` / `rollup` (which take catalog refs) is a pre-existing issue, out of
  scope here.
- No deprecation shim for `session.transform`. The path is deleted outright
  (pre-1.0, agent-reconstructed surface, one-path-per-capability rule).

## Design

### 1. Typed frame transform namespaces

New module `marivo/analysis/frames/transforms.py`:

```python
TFrame = TypeVar("TFrame", MetricFrame, DeltaFrame)

class _FrameTransforms(Generic[TFrame]):
    _frame: TFrame
    def filter(self, *, predicate: Callable[[pd.DataFrame], pd.Series]) -> TFrame: ...
    def slice(self, *, slice_by: dict[DimensionInput, SliceValue]) -> TFrame: ...
    def rollup(self, *, drop_axes: list[DimensionInput]) -> TFrame: ...
    def topk(self, *, by: str, limit: int) -> TFrame: ...
    def bottomk(self, *, by: str, limit: int) -> TFrame: ...
    def rank(self, *, by: str, method: RankMethod = "ordinal",
             rank_column: str = "rank") -> TFrame: ...
    def window(self, *, window: TimeScopeInput) -> TFrame: ...

class MetricFrameTransforms(_FrameTransforms[MetricFrame]):
    def normalize(self, *, mode: NormalizeKind,
                  baseline: NormalizeBaseline | None = None) -> MetricFrame: ...

class DeltaFrameTransforms(_FrameTransforms[DeltaFrame]):
    pass  # no normalize -- statically absent on delta.transform
```

`NormalizeBaseline` names the existing baseline shape (a `{"value": number}`
dict or a `{axis_column: axis_value}` selector dict). The current
`TransformAPI.normalize` types this as `Any`; this refactor introduces a
concrete alias, since a new public method must not carry `Any` (agent-guide).

Each concrete method keeps its own `analysis_purpose: str | None = None` slot.
Every method accepts only its own parameters; there is no `op` argument and no
optional-field union.

Each method carries the full agent-guide public-API docstring (purpose,
parameters, return, usage example, constraints), carried over from the existing
`TransformAPI.<op>` docstrings rather than dropped in the move. The re-sourced
`transform` help topic (see Deletion) keeps a runnable `frame.transform.<op>`
example so `describe` / `help("transform")` still teach the surface.

### 2. Frame entry points

```python
# MetricFrame
@property
def transform(self) -> MetricFrameTransforms:
    return MetricFrameTransforms(self)

# DeltaFrame
@property
def transform(self) -> DeltaFrameTransforms:
    return DeltaFrameTransforms(self)
```

Call shape:

```python
delta.transform.bottomk(by="delta", limit=10)      # largest decline: most-negative delta
metric.transform.slice(slice_by={country: "US"})
metric.transform.normalize(mode="share")           # absent on delta.transform
```

### 3. Session resolution

Each method resolves the session with `require_current_session()`, enforces
`ensure_session_writable(session)`, and validates
`frame.meta.session_id == session.id`, raising `CrossSessionFrameError` on
mismatch. Session resolution and the ownership check mirror `project_metric`
(`MetricFrame.metric()`); the writable-session guard does not — `project_metric`
omits it, but the current transform path calls `ensure_session_writable`
([transform.py:185]) and it must be retained (it is the "writable session"
shared precondition in section 4). The `session=` parameter is removed from the
public surface.

### 4. Dissolve the dispatch core

Delete `_transform_dispatch`, `_TransformParams`, `TransformAPI`, the module
`transform` singleton, and every handler's `unsupported_kwargs` defense block.
The `_op_*` business logic and `_persist_transform_frame` are retained and
invoked directly by the precise per-op methods, which pass the exact arguments
each handler needs. Shared preconditions (writable session, ownership,
single-metric guard for MetricFrame) run once per call.

`_SUPPORTED_OPS` is currently imported by `help._transform_content`
([help.py:299]) to build the op matrix, so it is not orphaned. Retain a single
canonical op tuple (renamed to reflect the frame namespace, or kept as-is) as
the source both the help matrix and any op-registry checks read from; help
derives its rows from that tuple, not from a `session.transform` reference. The
`TopKDirection` `Literal` is removed (see section 6); the `TransformOp`
`Literal` is removed only if nothing outside the deleted dispatch consumes it.

### 5. Evidence shrink (suppress findings without relabeling the family)

The naive change — flipping `extractor_family` to `"projection"` — is rejected.
`extractor_family` is overloaded in `commit_result`: beyond selecting the
finding extractor, it is written as `artifacts.artifact_type`
([pipeline.py:771]) and passed as the followup `source_family`
([pipeline.py:813]). Setting it to `"projection"` would relabel a transform's
MetricFrame / DeltaFrame as a projection throughout the audit and followup
layers — a larger, wrong change. `select_metric` can legitimately record as
`projection` (it *is* one); a transform output is still a real frame of its
input family and must be audited as such.

Instead, decouple the audit family from the finding-emission decision.
`extractor_family` stays the true frame kind (`"metric_frame"` /
`"delta_frame"`), so `artifact_type` and `source_family` remain truthful. A new
explicit signal suppresses evidence seeding for reshapes. Two candidate
mechanisms for the implementation plan to choose between:

- **`emit_evidence: bool = True` on `commit_result`** (recommended): when
  `False`, skip `_extract_findings` (findings stay `[]`) and skip the
  seeding + followup savepoint block, while still inserting the artifact row
  with the real family. `_persist_transform_frame` passes `emit_evidence=False`.
  Greppable, and expresses "this step seeds no evidence" directly.
- **Gate inside `_extract_findings` on `step_type == "transform"`**: pass
  `step_type` through and short-circuit to `[]`. Suppresses findings but leaves
  followup generation (which keys off `source_family` + `semantic_kind`) intact,
  so it only half-solves the "reshape seeds no evidence" goal.

Effect either way: transform still writes the artifact row, lineage step, and
job record (ref stability, audit, crash recovery preserved) and is audited under
its real family, but emits no `observation` digest, no `metric_value` findings,
and — under the recommended mechanism — no seeded propositions or followups.
`knowledge().observations()` then contains only `observe` /
`derive_metric_frame`, matching its docstring.

`Subject(...)` and `semantic_anchors` are still passed to `commit_result` (the
artifact row needs them). The DeltaFrame branch keeps `analysis_axis="change"`.

Open decision for the plan: whether reshapes should suppress *followups* too
(recommended: yes — a `topk` of a segmented frame would otherwise re-seed the
same decompose / discover followups the parent already surfaced). The
`emit_evidence` mechanism suppresses them; the `step_type` gate does not.

### 6. topk / bottomk mirror semantics

| Method               | Behavior                       | Implementation            |
| -------------------- | ------------------------------ | ------------------------- |
| `topk(by, limit)`    | descending by `by`, take N     | `_ordered_take(ascending=False)` |
| `bottomk(by, limit)` | ascending by `by`, take N      | `_ordered_take(ascending=True)`  |

Remove the `TopKDirection` `Literal`, the `order` validation in `_op_topk`, and
the "does not accept order" branch in `_op_bottomk`. `_ordered_take` is
retained. The persisted `op_params` no longer carry `"order"`, which changes the
`params_digest` and therefore the deterministic `artifact_id`; this is expected
and acceptable pre-1.0 (no artifact compatibility burden).

Caller-facing semantics captured in `describe(bottomk)` and the skill:

- Largest decline (most-negative delta): `bottomk(by="delta")`
- Largest increase: `topk(by="delta")`

### 7. Telemetry

Span names change from `marivo.analysis.transform.<op>` to
`marivo.analysis.frame.transform.<op>` (e.g.
`marivo.analysis.frame.transform.bottomk`), aligning with the
`marivo.analysis.<namespace>.<op>` shape used by `discover.*` and
`escape_hatch.*`. The `family="transform"` attribute is unchanged. The span is
emitted inside each frame method.

Two files carry these names and must move together, or the runtime event name
drifts from the registry:

- `marivo/telemetry/__init__.py` — the `TELEMETRY_INTENTS` whitelist
  ([telemetry/__init__.py:41-48]) lists the 8 `marivo.analysis.transform.*`
  names; each gains the `frame.` infix.
- `tests/test_telemetry.py` ([~208-234]) asserts the sorted registry set; the 8
  entries update in lockstep.

## Error handling

- Cross-session frame: `CrossSessionFrameError` (unchanged).
- Missing / invalid per-op arguments: existing `TransformArgError`,
  `TransformDimensionNotFoundError`, `TransformShapeUnsupportedError`,
  `WindowInvalidError` are retained; the per-op argument validation moves from
  the dispatch defense blocks into each method's own signature-scoped checks.
- `normalize` on a DeltaFrame is no longer reachable: the method does not exist
  on `DeltaFrameTransforms`, so it fails at authoring/type time instead of
  raising `TransformArgError` at runtime.
- Multi-metric MetricFrame still hits the `single_metric` precondition before
  any op runs.
- `TransformOpUnsupportedError` loses all raisers. It is raised only inside
  `_transform_dispatch` (input is not a MetricFrame / DeltaFrame, or the op
  string is unknown); after the dissolution both branches are structurally
  unreachable (`.transform` exists only on the two frame families, and there is
  no op string). Remove the class (with its `transform.py` import and any public
  export), or document a retained raiser. Two tests reference it and update with
  that decision: `test_analysis_transform.py`
  (`pytest.raises(TransformOpUnsupportedError)`) and `test_analysis_errors.py`
  (constructs it directly).

## Deletion of the old path

Removing `session.transform` touches these wired-together surfaces, not just the
namespace class. The plan must rewire all of them, or imports break and
agent-facing error hints go stale:

- **Session surface**: remove `Session.transform` property and the
  `SessionTransformNamespace` class (~230 lines in `session/core.py`). Old calls
  get a bare `AttributeError`.
- **Intent export**: `intents/transform.py` no longer exposes a `transform`
  singleton or `TransformAPI` (dissolved in section 4). Remove `transform` from
  `intents/__init__.py`'s imports and `__all__` ([intents/__init__.py:11]).
- **Introspection**: `test_analysis_session_intent_introspection.py` imports
  `SessionTransformNamespace` and lists it in `_NAMESPACE_CLASSES`
  ([test:22]). Decide the introspection target for `transform`: since it is no
  longer a session namespace, drop it from `_NAMESPACE_CLASSES` /
  `_INTENT_TO_METHOD`, and — if transform introspection is still wanted — point
  it at the frame namespace classes instead.
- **help topic resolution**: `help` resolves the `transform` topic's
  signature/doc via `from marivo.analysis.intents.transform import transform`
  ([help.py:1099]). That import dies with the singleton. Re-source the topic
  from the frame namespace (e.g. `MetricFrameTransforms`) or from the retained
  canonical op tuple. `help` also: rewrite `_transform_content` /
  `_transform_text` from a `session.transform` matrix to a `frame.transform`
  matrix; remove the `transform` entry from `_SESSION_METHODS` (it lists session
  methods) while keeping the `transform` help *topic* so `help("transform")`
  still resolves; rewrite example strings `session.transform.<op>(...)` ->
  `frame.transform.<op>(...)`; update `_HELP_TOPICS["transform"]`.
- **Error-hint fix snippets**: `ForecastInputQualityError` embeds
  `clean = session.transform.window(history, window={...})` as its `fix_snippet`
  ([errors.py:665]). This is agent-facing guidance, not an import, so nothing
  breaks at import time — the agent is simply told to call a deleted API. Rewrite
  it to `frame.transform.window(window=...)`. The scoped grep in Success criteria
  matches this line, so it must move for that gate to pass.
- **Decision to pin in the plan**: is `transform` a help-only topic after the
  move, what does `intents.__all__` become, and which frame-namespace method is
  the signature source? These are resolved before implementation, not during.

## Testing

- `test_analysis_transform.py`: rewrite all `session.transform.<op>(frame, ...)`
  to `frame.transform.<op>(...)`; delete `order=` cases; add/adjust `bottomk`
  semantics cases (most-negative delta); resolve the now-unreachable
  `pytest.raises(TransformOpUnsupportedError)` case per Error handling.
- `test_analysis_errors.py`: update or drop the direct
  `TransformOpUnsupportedError` construction test per the Error-handling decision.
- `test_analysis_help.py` (lines ~180-263): assertions
  `session.transform.topk` -> `frame.transform.topk`, etc.
- `test_telemetry.py` (lines ~227-234): the 8 span-name assertions gain the
  `frame.` prefix.
- `test_analysis_purpose.py`, `test_analysis_session_surface.py`,
  `test_analysis_candidate_select.py`, `test_analysis_observe_sampled_fold.py`:
  update call sites.
- `test_analysis_session_intent_introspection.py`: remove the
  `SessionTransformNamespace` import and its `_NAMESPACE_CLASSES` /
  `_INTENT_TO_METHOD` entries; re-point transform introspection at the frame
  namespace if still covered.
- New regression: after a transform, `knowledge().observations()` excludes it,
  while the artifact row, lineage step, and job record are present **and the
  artifact's `artifact_type` is the real frame kind** (`metric_frame` /
  `delta_frame`), not `projection` — guards against the rejected relabeling.
- New assertion: `hasattr(session, "transform")` is `False`.
- **Negative-typing test** for the `normalize`-absent-on-delta criterion:
  `make typecheck` runs only `mypy marivo` ([Makefile:25]) and cannot see
  negative user code, so this needs a dedicated check. Add a mypy-subprocess
  test that runs mypy over an inline fixture containing
  `delta.transform.normalize(...)` and asserts an `attr-defined` error is
  reported. A `reveal_type` / `assert_type` fixture cannot express a *missing*
  attribute, so the subprocess-plus-expected-error form is required.
- `test_public_surface.py` / `test_agent_api_drift.py`: update snapshots if
  `SessionTransformNamespace` appears; `_FrameTransforms` family stays out of
  `__all__` (not a result type).

## Documentation

- `docs/specs/analysis/python-analysis-design.md`: `session.transform.*` and
  `transform.<op>(frame)` references become frame-shaped; update the topk /
  bottomk order-semantics passage.
- `marivo/skills/marivo-analysis/`: rewrite transform call sites in workflow and
  references; record the `bottomk` = "largest decline" mapping.
- `site/src/content/docs/{en,zh-cn}/latest/concepts/analysis-workflow.mdx`:
  rewrite examples (latest edition only; frozen v0.1-v0.3 untouched), both
  English and Chinese in sync.
- `marivo/skills/marivo-analysis/references/cumulative-frames.md`: rewrite the
  `session.transform.window` example.

## Success criteria

- `make test`, `make typecheck`, `make lint`, `make examples-check` all pass.
- The negative-typing test confirms mypy reports `attr-defined` on
  `delta.transform.normalize(...)` (see Testing). "mypy rejects it" is proven by
  that test, not by `make typecheck`, which only checks the `marivo` package.
- `frame.transform.<op>` return types are the exact input family, no union.
- A **scoped** grep finds no residual `session.transform` in the active surface:
  `marivo/**`, `tests/**`, `marivo/skills/**`, `site/**/latest/**`, and this
  spec's own prose aside. Frozen and historical surfaces are deliberately
  excluded and expected to still match: `site/**/v0.*`,
  `docs/superpowers/plans/**`, `docs/superpowers/specs/**` (prior specs),
  `docs/archive/**`. A repo-wide grep is the wrong gate — it contradicts the
  frozen-edition policy in Documentation.
