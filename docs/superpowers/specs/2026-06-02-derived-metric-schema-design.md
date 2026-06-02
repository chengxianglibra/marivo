# Derived Metric Schema Redesign

Date: 2026-06-02

Status: approved scope, pending written-spec review

## Problem

The current derived-metric authoring contract has accumulated three frictions,
surfaced by a real `avg_execution_time` ratio declaration:

```python
@ms.metric(
    datasets=[],
    additivity="non_additive",
    decomposition=ms.ratio(
        numerator="trino_query.total_execution_time",
        denominator="trino_query.query_count",
    ),
    name="avg_execution_time",
    verification_mode="python_native",
    ai_context={...},
verification_mode="python_native",)
def avg_execution_time():
    return ms.component("numerator") / ms.component("denominator")
```

1. **The body restates the decomposition.** `ms.ratio(numerator=..., denominator=...)`
   already declares the structure; `return ms.component("numerator") / ms.component("denominator")`
   says `/` a second time. The `kind="ratio"` tag carries the analytical
   (change-attribution) meaning already, so for canonical kinds the body adds no
   information.

2. **`verification_mode` is misleading on derived metrics.** It defaults to `None`
   (self-status `UNVERIFIED`), but for a derived metric the effective parity
   status propagates from its component metrics; the self `UNVERIFIED` is
   deliberately excluded from propagation
   (`marivo/semantic/parity.py:341-366`). Declaring `verification_mode="python_native"`
   on a derived metric is therefore redundant when components are already
   `python_native`, and actively harmful when components are `verified` — it
   downgrades the propagated result from `VERIFIED` to `VERIFIED`.

3. **Component references are stringly typed.** `ms.component("numerator")` relies
   on magic strings that must match keys produced by the decomposition builder,
   and the mapping is non-obvious: `ms.weighted_average(value=..., weight=...)`
   stores component keys `{"numerator", "weight"}` (`marivo/semantic/authoring.py:1077`),
   so the body has to read `ms.component("numerator")` for the `value=` argument.
   Removing the body removes this footgun: with no body, the component keys are
   never typed by a human, so the `value`/`numerator` mismatch becomes invisible
   rather than something to rename (see Decomposition builders).

A fourth issue is a documentation/code drift: the spec
`docs/specs/semantic/python-semantic-layer.md` documents the provenance
parameter as `provenance=` (lines 409, 538-539), but the decorator only accepts
`verification_mode=` (`marivo/semantic/authoring.py:710`). There is no `provenance`
parameter in code.

### Key finding: custom-arithmetic derived metrics are unused

Investigation of the repository shows that every real semantic model (examples
and fixtures) declares derived metrics as canonical `ratio` or
`weighted_average` with the body `component / component`. Non-canonical
arithmetic (three components, numeric constants, `+`/`-`, e.g.
`(a + b) / c`) appears only in AST-validator and materializer capability tests
(`tests/test_semantic_validator.py`, `tests/test_semantic_materializer.py`), and
no decomposition builder can declare arbitrarily named components in the first
place (`DecompositionBuilder.kind` is `Literal["sum", "ratio", "weighted_average"]`,
`marivo/semantic/authoring.py:214`). The arithmetic freedom in the derived-body
AST whitelist is latent and unreachable through any builder. The spec already
pushes complex logic down into base metrics
(`docs/specs/semantic/python-semantic-layer.md:512,529`).

This "push into base metrics" guidance is precise about its boundary:
computability in ibis is not the base-vs-derived dividing line. Any formula that
fits within a single dataset at a single grain (including non-additive ones such
as `mean`/`median`/`count(distinct)`, since base metrics recompute from raw rows
at each query grain) can be authored as a base metric. Derived metrics exist for
two things base metrics cannot do: (1) expose change-attribution decomposition as
an analysis surface, and (2) combine quantities that are aggregated
*independently* across grains/datasets and combined last (the fan-trap), which
cannot be written as one reduction. The combine-last cases that have a canonical
shape are exactly `ratio` and `weighted_average`.

Custom-arithmetic derived metrics are therefore treated as a feature to remove,
not preserve.

## Goals

- Remove the restated formula for canonical derived metrics: derived metrics
  declare structure only, with no Python body.
- Make the derived-metric registration API explicit and body-free.
- Stop encouraging `verification_mode` on derived metrics where it is redundant or
  harmful; advise against it.
- Reconcile the spec/code provenance naming drift.
- Delete the now-unused component-sentinel machinery and its tests.
- Keep all changes fix-forward with no backward-compatibility shims.

## Non-Goals

- No support for custom (non-canonical) arithmetic in derived metric bodies. A
  formula that fits within a single dataset and grain must be authored as a base
  metric (`@ms.metric` with an ibis reduction body); cross-metric combination is
  limited to the canonical `ratio` / `weighted_average` builders.
- **Known gap, explicitly out of scope.** Cross-dataset additive/difference
  composition of N metrics (e.g. `net = gross(orders) - refunds(refunds)`,
  `total = a + b + c` across fact tables) is neither a single base metric nor a
  `ratio`/`weighted_average`. No public builder can declare such components today
  (the latent arithmetic AST path is unreachable), so this redesign removes no
  usable capability — but it also adds none. If the pattern is needed it is a
  separate feature (an additive/composite N-term builder carrying sign,
  decomposition, and materialization semantics) with its own spec.
- No change to the base/aggregate authoring surface (`@ms.metric` with
  `datasets`, `ms.sum()`, ibis reduction bodies). `@ms.metric` loses only its
  derived branch.
- No change to the parity-status propagation algorithm
  (`propagated_parity_status`).
- No change to the meaning of decomposition `kind` for analysis/change
  attribution.

## Decisions

These design forks were resolved during brainstorming:

| Fork | Decision |
| --- | --- |
| Backward compatibility | Break freely, fix-forward, no deprecation shims. |
| Derived body form | Canonical kinds only, body always omitted; remove `ms.component` and the sentinel arithmetic system. |
| Custom arithmetic | Not preserved. Derived metrics are `ratio` or `weighted_average` only. |
| Registration API | Direct call `ms.derived_metric(...)` returning a `MetricRef`; `@ms.metric` becomes base/aggregate only. |
| Provenance naming | Code name `verification_mode` wins; the spec is aligned to it. |

## New Contract

### Derived metric registration: `ms.derived_metric`

Derived metrics are registered by a direct call that returns a `MetricRef`. No
function, no body.

```python
avg_execution_time = ms.derived_metric(
    name="avg_execution_time",
    decomposition=ms.ratio(
        numerator=total_execution_time,
        denominator=query_count,
    ),
    additivity="non_additive",
    ai_context={
        "business_definition": "Average execution (run) time per query in seconds.",
        "guardrails": [
            "Unit is seconds. Distinct from avg_elapsed_time which includes "
            "queue+analysis+planning.",
        ],
    },
)
```

Signature (keyword-only):

- `name: str` — required (no function to default from).
- `decomposition: DecompositionBuilder` — must be `ms.ratio(...)` or
  `ms.weighted_average(...)` (a builder with components). `ms.sum()` is rejected
  here because it has no components.
- `additivity: Literal["additive", "semi_additive", "non_additive"] | None` —
  **new decorator-time validation**: `ms.derived_metric` rejects
  `additive`/`semi_additive` (only `non_additive` or `None` are allowed). This is
  new enforcement, not "same as today": the current assembly-time additivity check
  skips derived metrics entirely (`marivo/semantic/validator.py:836`,
  `if m_ir.is_derived: continue`), so the rule is documented but never enforced
  for derived metrics today. Add explicit tests for the rejection.
- `model_name`, `description`, `ai_context` — same as `@ms.metric`.
- Provenance kwargs: `source_sql`, `source_dialect`, `source_document`,
  `source_notes`, `verification_mode` — accepted and stored on `ProvenanceIR` as
  **documentation metadata only**. A derived metric cannot be SQL-parity-verified:
  `parity_check` rejects `is_derived` metrics before it looks at `source_sql`
  (`marivo/semantic/parity.py:121`). So `source_sql` on a derived metric is not a
  parity oracle, does not change the propagated parity status, and does not
  suppress the `verification_mode` advisory below. Adding direct derived parity is
  out of scope for this redesign.

It produces the same `MetricIR` field shape as today (`is_derived=True`,
`datasets=()`, `decomposition` populated, `provenance` populated), so the loader,
registry, readiness, parity, and analysis layers consume it unchanged. Two fields
have no function to derive from and get explicit synthetic values that keep the IR
contract intact:

- `python_symbol` stays a non-null `str` (the field is `str` at
  `marivo/semantic/ir.py:312` and is surfaced by `list_metrics()` /
  `MetricSummary` at `marivo/semantic/reader.py:685`). For a body-free derived
  metric it is set to the metric `name` as a synthetic symbol — not `None` — so no
  reader, summary, or `MetricSummary` consumer needs to handle a missing symbol.
- `body_ast_hash` is computed from the decomposition structure (kind plus ordered
  component refs) instead of source text, so decomposition edits remain
  detectable.

### `@ms.metric` becomes base/aggregate only

`@ms.metric` now requires non-empty `datasets` and an ibis-reduction body. It
drops all derived branching: `is_derived` detection, the `datasets=[]`
derived-shape path, the component-scope checks, and the `"derived"` mode of body
AST validation. The decomposition for a base metric remains `ms.sum()` (or the
appropriate builder describing its attribution structure).

### Decomposition builders

- `ms.ratio(numerator=<metric ref>, denominator=<metric ref>)` →
  components `{"numerator": ..., "denominator": ...}` (unchanged).
- `ms.weighted_average(value=<metric ref>, weight=<metric ref>)` →
  components `{"numerator": ..., "weight": ...}` (**unchanged**). The internal key
  stays `"numerator"` even though the kwarg is `value=`. Renaming it was considered
  and rejected: with bodies removed the key is no longer typed by any human, and
  the analysis layer hard-codes these role names — `numerator`/`weight` in
  `_component_parent_columns` (`marivo/analysis/intents/observe.py:248`) and as the
  paired measure role in `decompose` (`marivo/analysis/intents/decompose.py:122`),
  which together form the public component-frame column contract. Renaming would
  force an analysis-frame / decompose / evidence migration for zero user-facing
  benefit, so the key is left as-is.
- `ms.sum()` → base-only, no components (unchanged).
- `DecompositionBuilder.kind` stays `Literal["sum", "ratio", "weighted_average"]`.
  No generic/custom kind is added.
- `numerator` / `denominator` / `value` / `weight` accept a `MetricRef` or a
  qualified `"<model>.<metric>"` string (unchanged). Authoring guidance and
  examples use `MetricRef` variables or `ms.ref(...)` rather than bare strings.

### Provenance on derived metrics

- `verification_mode` stays optional and defaults to `None`. The effective parity
  status of a derived metric continues to propagate from its components via the
  unchanged `propagated_parity_status`.
- **New advisory (issue #4).** When a derived metric sets
  `verification_mode="python_native"`, emit a warning at load and in readiness. On a
  derived metric this is always redundant or harmful: it does not raise the metric
  above its components, and when all components are `verified` it caps the
  propagated status at `VERIFIED` instead of `VERIFIED`
  (`docs/specs/semantic/python-semantic-layer.md:548`). The advisory fires
  regardless of `source_sql` (which, per the signature note above, is not a parity
  oracle for derived metrics). It is scoped to `python_native` only:
  `verification_mode="unverified"` and `None` are **not** warned, because an
  `unverified` self-status does not downgrade `verified` components, so the
  downgrade rationale does not apply. It is emitted as a readiness advisory
  alongside a load-time warning next to the existing unverified-provenance warning
  (`marivo/semantic/validator.py:1071-1082`). This is a warning, not a hard error,
  consistent with the provenance philosophy that only `--strict-provenance` fails
  closed (`docs/specs/semantic/python-semantic-layer.md:543`).
- **Spec naming alignment (issue #2).** Replace `provenance=` with
  `verification_mode=` throughout `docs/specs/semantic/python-semantic-layer.md`
  so the spec matches the implemented decorator.

### Materialization

A derived metric materializes by synthesizing the division directly from its
`kind` and components, replacing the sentinel-tree walk:

- `ratio`: `metric(components["numerator"]) / metric(components["denominator"])`
- `weighted_average`: `metric(components["numerator"]) / metric(components["weight"])`

This preserves current numeric semantics — the existing body is also a
`component / component` division — while removing the sentinel tree and its
sidecar storage. Cycle detection over decomposition components is retained
(`marivo/semantic/validator.py:1087-1099`).

## Removals (break-freely)

Public surface:

- `ms.component` (`marivo/semantic/authoring.py:1090`).
- `ComponentExpr` from `marivo/semantic/typing.py`.
- The `ms.help("component")` topic; update `ms.help` for `metric` and
  `decomposition`, and the constraints output, to describe the body-free
  contract.

Internal machinery:

- `_ComponentSentinel`, `_BinOpSentinel`, `_UnaryNegSentinel`
  (`marivo/semantic/authoring.py:81-202`).
- `_ACTIVE_DECOMPOSITION` ContextVar and `_metric_body_uses_component`.
- `_DerivedMetricASTValidator` and the `"derived"` mode of
  `validate_metric_body_ast` (`marivo/semantic/validator.py:355-554`). The
  `"base"` body validation path is retained.
- Materializer sentinel walking: `_materialize_derived_metric`,
  `_eval_sentinel`, `_eval_sentinel_or_literal`, and the derived sentinel-tree
  entry in the sidecar.
- Derived-specific error kinds tied to component bodies that become unreachable
  (e.g. component-scope / derived-shape checks). Error kinds still referenced by
  retained validation stay.

## Validation and readiness changes

- `ms.derived_metric` validates at decorator-time: `decomposition` has components
  and is `ratio` or `weighted_average` (`ms.sum()` rejected); `additivity` is
  `non_additive` or `None` (new — see signature). Assembly-time validation
  (unchanged paths) resolves each component ref to a registered metric and runs
  cycle detection (`marivo/semantic/validator.py:1087-1099`). There is no body to
  AST-validate.
- Readiness gains the derived `verification_mode="python_native"` advisory described
  above (independent of `source_sql`). Existing parity/unverified readiness
  behavior is otherwise unchanged.

## Spec, docs, examples, and tests

All updated within the same change (per the repository agent guide):

- `docs/specs/semantic/python-semantic-layer.md`: rewrite the derived-metric
  shape rules (lines 381-421), the decomposition/component sections (494-529),
  the provenance naming (531-550), and the closing summary (805-812) to the new
  body-free contract and `ms.derived_metric` API; rename `provenance=` →
  `verification_mode=`.
- `marivo-skills/marivo-semantic/` and `marivo-skills/marivo-analysis/`:
  migrate examples and fixtures using `ms.component` or derived `@ms.metric`
  (e.g. `marivo-analysis/references/examples/_fixtures/tiny_semantic.py`) to
  `ms.derived_metric`. Required by the agent guide's rule that public
  symbol/signature changes update the matching examples.
- `tests/`: remove or rewrite derived-arithmetic tests
  (`tests/test_semantic_validator.py` derived cases,
  `tests/test_semantic_materializer.py` derived cases,
  `tests/test_semantic_reader.py:112`, and the derived `ms.component` fixtures in
  `tests/test_semantic_phase3_fanout_policy.py` and
  `tests/test_analysis_observe_cross_dataset_phase2.py`). Add coverage for
  `ms.derived_metric` (including `additive`/`semi_additive` rejection), body-free
  materialization for both `ratio` and `weighted_average`, the synthetic
  `python_symbol` and decomposition-derived `body_ast_hash`, and the new
  `verification_mode="python_native"` advisory.

## Success criteria

- `make typecheck` passes for the touched modules.
- `make lint` passes.
- `make test` passes, including new `ms.derived_metric` coverage and the removal
  of derived-body tests.
- `make examples-check` passes with migrated examples.
- The `avg_execution_time` example declares with no body, no `datasets=[]`, and
  no `verification_mode`, and resolves to the same materialized expression and
  propagated parity status as before.
