# Incremental Semantic Authoring on a Ready Model

Date: 2026-06-02

Status: approved design, pending written-spec review

## Problem

The `marivo-semantic` skill documents one authoring path: cold-start bulk
discovery — inspect a datasource, `propose_candidates(...)` over its sources,
classify with `open_questions(...)`, author the whole `_model.py`, then close
out. It has no guidance for the common case of adding **one** object (typically a
metric) to a model that is already authored and readiness-clear.

Without that guidance, agents fall back to a manual `record_decision` workaround.
The root cause is a structural asymmetry, not a missing primitive:

- The cold-start path generates `OpenQuestion`s via
  `propose_candidates → open_questions`, and dangerous decisions are settled
  cleanly with `project.answer(question, ...)`.
- Adding one object directly **skips that loop**, so no `OpenQuestion` is
  generated for the new object. Two gaps follow:
  1. **No question to answer.** For a dangerous decision on the new object (a new
     field's `amount_unit`, a metric exclusion rule, a `python_native`
     provenance claim), there is no `OpenQuestion`, so agents hand-build a
     `DecisionRecord` and call `record_decision` directly.
  2. **Re-running discovery is noisy.** Re-running `propose_candidates` on the
     existing table to reuse the loop re-surfaces questions for already-authored
     fields, because `open_questions` dedups only against confirmation records,
     not auto-recorded object decisions (`marivo/semantic/reader.py:1101`,
     `_confirmed_question_ids`).

The common metric case is already half-solved: declaring `@ms.metric` /
`ms.derived_metric` and calling `project.reload()` auto-records the object-level
`metric_decomposition` decision. That happy path is simply undocumented as the
incremental route.

## Goals

- Document an incremental authoring loop for adding one object to a ready model:
  reuse existing refs → author one object → `reload()` auto-records → settle only
  genuinely-needed decisions → scoped readiness → handoff.
- Make `reload()` auto-record the documented happy path that replaces manual
  `record_decision` for the common metric case.
- Keep incremental dangerous-decision settlement **symmetric** with cold-start:
  the engine surfaces the question, the agent answers with evidence.
- Make scoped re-discovery clean by deduping `open_questions` against
  auto-recorded object decisions.
- Lead with metrics (base and derived); cover field, time_field, and
  relationship as concise notes.

## Non-Goals

- No guided incremental API (`project.add_metric(...)` and similar). The
  primitives exist; the gap is guidance plus one dedup hole.
- No change to scoped `readiness(refs=...)` semantics. It checks exactly the
  passed refs (`marivo/semantic/readiness.py:549`); the docs instruct passing the
  new ref plus its direct dependencies. No auto-expansion is added.
- No change to staleness handling. `audit(...)` remains the dedicated path that
  re-opens stale decisions; the dedup fill only suppresses currently-settled
  ones.
- No new derived-metric syntax. Derived examples use the `ms.derived_metric`
  schema defined by the derived-metric redesign; this design depends on that
  landing.

## Decisions

These forks were resolved during brainstorming:

| Fork | Decision |
| --- | --- |
| Derived syntax | Coordinate with the derived-metric redesign; examples use `ms.derived_metric`. This design depends on that redesign. |
| Scope | Docs plus one targeted engine fill (the `open_questions` dedup). Not docs-only, not a guided API. |
| Settlement model | Incremental dangerous decisions are settled via scoped `open_questions → answer()`, symmetric with cold-start. Raw `record_decision` remains a fallback for bespoke decisions with no generated question. |
| Doc structure | A dedicated `references/incremental.md`, matching the skill's one-topic-per-reference layout; not folded into `workflow.md`. |
| Scoped readiness | Doc-only: pass `[new_id, *project.dependencies(new_id).dependencies]`. No second code change. |
| Object-kind emphasis | Metrics-led (base + derived worked examples); field/time_field/relationship as notes; new dataset routes back to `workflow.md`. |

## Design

### Deliverables and file map

| File | Change |
| --- | --- |
| `marivo-skills/marivo-semantic/references/incremental.md` | New. The incremental loop, per-kind notes, worked examples. |
| `marivo-skills/marivo-semantic/SKILL.md` | "Choosing a workflow" note (cold-start vs incremental) and a reference-routing row. |
| `marivo-skills/marivo-semantic/references/workflow.md` | Top pointer: on an already-ready model, use `incremental.md`. |
| `marivo-skills/marivo-semantic/references/pitfalls.md` | New "Re-discovering settled objects" entry; cross-ref "Multi-file sprawl". |
| `marivo/semantic/reader.py` | Dedup fill in `open_questions` (below). |
| `tests/test_semantic_reader.py` (or nearest `open_questions` module) | Coverage for the dedup fill. |

### The incremental loop

1. **Load and locate.** `project.load()`; pick the target model; enumerate
   existing refs with `list_datasets`, `list_fields`, `list_time_fields`,
   `list_metrics`, `list_relationships`.
2. **Reuse first.** Bind existing objects as ref variables; never re-declare an
   existing dataset, field, or component metric. (Existing reuse guidance in
   `workflow.md` applies.)
3. **Targeted evidence.** Inspect only what the new object needs.
   - Over already-ready refs (a derived metric over existing component metrics,
     or `ms.sum()` over an existing field): no datasource re-inspection; evidence
     is the existing refs plus formula / source SQL / knowledge / user
     confirmation.
   - Needs an undeclared column: `mv.datasources.inspect_source(...)` for that
     one table to read the column type, comment, and nullability, then author the
     field from that evidence.
4. **Author one object** in the existing `.marivo/semantic/<model>/_model.py`,
   matching surrounding style and using ref variables. Do not add a new file
   (cross-ref "Multi-file sprawl").
5. **Reload to auto-record.** `project.reload()` auto-records the object-level
   `metric_decomposition` (metrics) or `time_field_identity` (time fields). This
   replaces manual `record_decision` for the common case.
6. **Settle only genuinely-needed decisions.** For a dangerous decision the
   declaration cannot settle, run a scoped `propose_candidates`/`open_questions`
   for the new object to generate the proper `OpenQuestion`, then
   `project.answer(question, ...)` with evidence. Use raw
   `project.record_decision(...)` only for a bespoke decision that generates no
   question.
7. **Scoped validation.** `project.audit(inspect_source=...)` (cheap,
   ledger-wide), then
   `project.readiness(refs=[new_id, *project.dependencies(new_id).dependencies], ...)`;
   re-run preview/parity only for affected refs. `project.richness()` is
   advisory.
8. **Handoff** only when readiness clears for the new ref.

### Worked examples (object-kind coverage)

Base metric over an existing field (reuses `orders`):

```python
# orders dataset already authored above in _model.py; the body reads the raw
# column, reusing only the orders dataset ref
@ms.metric(
    datasets=[orders],
    additivity="additive",
    decomposition=ms.sum(),
    name="orders_count",
    ai_context={
        "business_definition": "Count of orders.",
        "guardrails": ["Counts every row; exclude test orders upstream."],
    },
verification_mode="python_native",)
def orders_count(table):
    return table.order_id.count()
```

Derived metric over two existing component metrics (new `ms.derived_metric`
schema, body-free):

```python
# gross_revenue and orders_count already authored
average_order_value = ms.derived_metric(
    name="average_order_value",
    decomposition=ms.ratio(numerator=gross_revenue, denominator=orders_count),
    additivity="non_additive",
    ai_context={
        "business_definition": "Average gross amount per order.",
        "guardrails": ["Non-additive; do not sum across periods."],
    },
)
```

After either, `project.reload()` auto-records `metric_decomposition`;
`readiness(refs=[...])` over the new metric plus its dependencies should clear
with no hand-built `DecisionRecord`.

Field / time_field / relationship notes: author directly against existing
dataset refs exactly as in `authoring-patterns.md`; a new time_field auto-records
`time_field_identity` on reload; a new field that triggers a dangerous decision
(for example `amount_unit`) uses the scoped `open_questions → answer()` path from
step 6. A whole new dataset is closer to a mini cold-start — use `workflow.md`
for that source, then return here for objects on top.

### Engine fill: `open_questions` dedup

- Today, `open_questions` drops questions whose id is in confirmation records
  only (`reader.py:1101`).
- Fill: also drop questions already settled by auto-recorded object decisions.
  Build the same question identity from each object record's
  `(decision_kind, semantic_id)` and dedup against generated questions. Net
  effect: scoped re-discovery on a partially-authored table surfaces only the new
  object's unsettled questions.
- Boundary: staleness remains `audit`'s responsibility. The dedup suppresses
  currently-settled decisions; `audit(...)` is the dedicated path that re-opens
  stale decisions as low-verdict questions. Cold-start is unchanged: with no
  object records, behavior is identical to today.

### Scoped readiness (doc-only)

`readiness(refs=...)` checks exactly the passed refs with no auto-expansion
(`readiness.py:549`). The docs instruct computing direct dependencies via
`project.dependencies(new_id).dependencies` and passing `[new_id, *deps]`. No
code change.

## Sequencing and dependency

This design depends on the derived-metric redesign
(`docs/superpowers/specs/2026-06-02-derived-metric-schema-design.md`) landing
first, because `incremental.md` uses `ms.derived_metric` and a body-free derived
example. The redesign owns migrating `authoring-patterns.md` and the example
fixtures to the new schema; `incremental.md` only consumes it. If both ship
together, the derived example in `incremental.md` and the redesign's migrated
examples must use identical syntax.

## Spec, docs, examples, tests

Per the repository agent guide, all updated in the same change:

- New `references/incremental.md` with the loop, worked examples, and per-kind
  notes.
- `SKILL.md` workflow-selection note and routing row; `workflow.md` pointer;
  `pitfalls.md` entry.
- `reader.py` `open_questions` dedup fill.
- Tests: questions settled by auto-recorded object decisions are dropped by
  `open_questions`; confirmation-only dedup preserved; cold-start (no object
  records) unchanged.
- Any `incremental.md` example that is execution-checked must pass
  `make examples-check` under the new `ms.derived_metric` schema.

## Success criteria

- An agent can add a metric to a ready model following `incremental.md` and reach
  clear readiness without hand-building a `DecisionRecord` for the common case.
- Scoped re-discovery after the fill surfaces only the new object's questions,
  proven by a new test.
- `make examples-check`, `make test`, `make typecheck`, and `make lint` pass.
