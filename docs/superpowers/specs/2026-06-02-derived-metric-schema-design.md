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
    declared_status="python_native",
    ai_context={...},
)
def avg_execution_time():
    return ms.component("numerator") / ms.component("denominator")
```

1. **The body restates the decomposition.** `ms.ratio(numerator=..., denominator=...)`
   already declares the structure; `return ms.component("numerator") / ms.component("denominator")`
   says `/` a second time. The `kind="ratio"` tag carries the analytical
   (change-attribution) meaning already, so for canonical kinds the body adds no
   information.

2. **`declared_status` is misleading on derived metrics.** It defaults to `None`
   (self-status `UNVERIFIED`), but for a derived metric the effective parity
   status propagates from its component metrics; the self `UNVERIFIED` is
   deliberately excluded from propagation
   (`marivo/semantic/parity.py:341-366`). Declaring `declared_status="python_native"`
   on a derived metric is therefore redundant when components are already
   `python_native`, and actively harmful when components are `verified` — it
   downgrades the propagated result from `VERIFIED` to `PYTHON_NATIVE`.

3. **Component references are stringly typed.** `ms.component("numerator")` relies
   on magic strings that must match keys produced by the decomposition builder.
   `ms.weighted_average(value=..., weight=...)` compounds this by producing
   component keys `{"numerator", "weight"}` (`marivo/semantic/authoring.py:1077`):
   the `value=` kwarg becomes the `"numerator"` key, so the body must read
   `ms.component("numerator")` for the value. The kwarg name does not match the
   component name.

A fourth issue is a documentation/code drift: the spec
`docs/specs/semantic/python-semantic-layer.md` documents the provenance
parameter as `provenance=` (lines 409, 538-539), but the decorator only accepts
`declared_status=` (`marivo/semantic/authoring.py:710`). There is no `provenance`
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
- Stop encouraging `declared_status` on derived metrics where it is redundant or
  harmful; advise against it.
- Align decomposition builder kwarg names with component key names.
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
| Provenance naming | Code name `declared_status` wins; the spec is aligned to it. |

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
  same validation as today; `additive`/`semi_additive` is rejected on derived
  metrics (current rule at `docs/specs/semantic/python-semantic-layer.md:454`).
- `model_name`, `description`, `ai_context` — same as `@ms.metric`.
- Provenance kwargs: `source_sql`, `source_dialect`, `source_document`,
  `source_notes`, `declared_status` — accepted, because a derived metric may
  carry its own SQL oracle (`docs/specs/semantic/python-semantic-layer.md:547`).

It produces the same `MetricIR` shape as today (`is_derived=True`,
`datasets=()`, `decomposition` populated, `provenance` populated), so the loader,
registry, readiness, parity, and analysis layers are unaffected downstream.
`body_ast_hash` is computed from the decomposition structure (kind plus ordered
component refs) so decomposition edits remain detectable; `python_symbol` is
`None` since there is no function.

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
  components `{"value": ..., "weight": ...}`. **Changed**: the key was
  `"numerator"`; it becomes `"value"` so component keys match kwarg names.
- `ms.sum()` → base-only, no components (unchanged).
- `DecompositionBuilder.kind` stays `Literal["sum", "ratio", "weighted_average"]`.
  No generic/custom kind is added.
- `numerator` / `denominator` / `value` / `weight` accept a `MetricRef` or a
  qualified `"<model>.<metric>"` string (unchanged). Authoring guidance and
  examples use `MetricRef` variables or `ms.ref(...)` rather than bare strings.

### Provenance on derived metrics

- `declared_status` stays optional and defaults to `None`. The effective parity
  status of a derived metric continues to propagate from its components via the
  unchanged `propagated_parity_status`.
- **New advisory (issue #4).** When a derived metric sets `declared_status` but
  has no `source_sql`, emit a warning at load and in readiness:
  the declaration is redundant (the effective status comes from components) and
  can downgrade `verified` components to `python_native`. It is emitted as a
  readiness advisory alongside a load-time warning next to the existing
  unverified-provenance warning (`marivo/semantic/validator.py:1071-1082`). This
  is a warning, not a hard error, consistent with the provenance philosophy that
  only `--strict-provenance` fails closed
  (`docs/specs/semantic/python-semantic-layer.md:543`). A derived metric that
  sets both `declared_status` and `source_sql` is not warned (it has its own
  oracle).
- **Spec naming alignment (issue #2).** Replace `provenance=` with
  `declared_status=` throughout `docs/specs/semantic/python-semantic-layer.md`
  so the spec matches the implemented decorator.

### Materialization

A derived metric materializes by synthesizing the division directly from its
`kind` and components, replacing the sentinel-tree walk:

- `ratio`: `metric(components["numerator"]) / metric(components["denominator"])`
- `weighted_average`: `metric(components["value"]) / metric(components["weight"])`

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

- Loading a derived metric now validates: `decomposition` has components and is
  `ratio` or `weighted_average`; `additivity` is `non_additive` or omitted; each
  component ref resolves to a registered metric; no cycles. There is no body to
  AST-validate.
- Readiness gains the derived `declared_status`-without-`source_sql` advisory
  described above. Existing parity/unverified readiness behavior is otherwise
  unchanged.

## Spec, docs, examples, and tests

All updated within the same change (per the repository agent guide):

- `docs/specs/semantic/python-semantic-layer.md`: rewrite the derived-metric
  shape rules (lines 381-421), the decomposition/component sections (494-529),
  the provenance naming (531-550), and the closing summary (805-812) to the new
  body-free contract and `ms.derived_metric` API; rename `provenance=` →
  `declared_status=`.
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
  `ms.derived_metric`, the `weighted_average` `value`/`weight` keys, body-free
  materialization, and the new `declared_status`-without-`source_sql` advisory.

## Success criteria

- `make typecheck` passes for the touched modules.
- `make lint` passes.
- `make test` passes, including new `ms.derived_metric` coverage and the removal
  of derived-body tests.
- `make examples-check` passes with migrated examples.
- The `avg_execution_time` example declares with no body, no `datasets=[]`, and
  no `declared_status`, and resolves to the same materialized expression and
  propagated parity status as before.
