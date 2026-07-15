# Semantic Authoring Workflow

Status: draft design. This document defines the active agent workflow for
building a Marivo semantic layer — the write loop that turns datasource evidence
into verified semantic objects. It complements
[semantic-object-model.md](semantic-object-model.md) (which owns the object
contracts) and [datasource-layer.md](datasource-layer.md) (which owns connections
and evidence).

See also:

- [overview.md](overview.md) — the three-layer picture and guidance layering.
- [loading-validation-introspection.md](loading-validation-introspection.md) —
  what `ms.load()`, `catalog.verify_object(...)`, scoped preview, and
  `catalog.readiness(...)` return.
- `ms.help("authoring")` / `md.help("authoring")` — the runnable checklists.

## Current public flow

The semantic-authoring lifecycle is a registered state model, not a hardcoded
method-by-method runbook. `ms.help("authoring")` renders the lifecycle view
generated from registered authoring states; `md.help("authoring")` renders the
matching datasource lifecycle ending at acquired/projected evidence and linking
to the semantic root. The conceptual policy order is:

```text
help/browse -> inspect -> explicit scope -> sample once -> project evidence -> settle/grill -> author one Python object -> load typed object -> static verify -> scoped preview -> readiness -> analysis
```

Invocation details — exact signatures, required inputs, effects, and
preconditions — live in focused `ms.help(<target>)` and the current object's
`.contract()`, not in this document. Each state-bearing result exposes
`.contract()` for its mechanically available continuations, and each structured
error exposes typed repair pointing back to live help. This document states the
policy order and judgment boundaries; it does not duplicate parameter tables or
reconstruct mechanical requirements from prose.

`marivo-semantic` is the one-file packaged skill that applies this flow's
ordered routing discipline. The library supplies facts and validation; the
agent settles business intent and writes ordinary Python definitions. There is
no separate "prepare" or handoff-brief stage — an agent settles constructor
values before authoring one object.

## Layer ownership

Each layer of guidance has exactly one job:

- **`ms.help("<constructor-or-object>")` — static authoring contract.**
  Constructors, required and optional parameters, allowed values, defaults, omit
  rules, nested parse shapes, and static constraints. Help says *what must be
  settled*; it carries no runtime data.
- **`md.inspect(...)` and one snapshot — runtime datasource evidence.** Inspect
  metadata, choose explicit scope, acquire one selected-column sample, and reuse
  local projections. Evidence never authors objects or infers meaning.
- **Catalog verify, preview, readiness — validation.** Static verification is
  query-free; runtime preview requires `using=`; readiness is query-free and
  consumes fresh checks after authoring.

The agent settles constructor values from help, discovery evidence, catalog
state, project docs, source SQL/provenance, prior decisions, and user answers. If
a semantic decision is still unresolved after the evidence pass, the agent grills
the user one decision at a time — it does not silently pick a default for
business-caliber questions (failure handling, ratio denominators, time-axis
choice, scope).

## File organization

The standard layout is one datasource file per connection and one
`_domain.py` per domain:

```text
models/
  datasources/
    warehouse.py          # md.duckdb(...) / md.trino(...) / ...
  semantic/
    sales/
      _domain.py          # ms.domain(...) + entities → metrics for this domain
    marketing/
      _domain.py
```

- `models/datasources/*.py` is project-level, shareable, and secret-free (see
  [datasource-layer.md](datasource-layer.md)).
- `<root>/<domain>/_domain.py` is the domain entrypoint. It calls `ms.domain(...)`
  once (with `name` equal to the directory) and holds that domain's entities,
  dimensions, time dimensions, measures, metrics, and relationships in dependency
  order.
- The loader can still execute sibling `.py` files in a domain directory, but
  that is a lower-level capability. The standard workflow keeps a domain in its
  single `_domain.py`; multi-file authoring needs its own import-order and
  default-domain rules and is not the default.

## Authoring ladder

Build datasource-backed objects in dependency order:

```text
domain -> entity -> dimension -> time_dimension -> measure -> metric
       -> relationship -> cross-entity metric -> derived metric
```

Datasource registration is a prerequisite owned by `marivo.datasource`, not a
rung on this ladder. Each active batch is one entity plus one semantic kind
(e.g. `entity.sales.orders + dimension`, then `+ time_dimension`, then
`+ measure`). Relationship and cross-entity batches span multiple entities only
when the kind requires it.

## Per-object cycle

The per-object cycle is the registered lifecycle applied to one object. Its
mechanical transitions — load, verify, preview, readiness, analysis handoff —
and their exact inputs, effects, and preconditions are disclosed by focused
`ms.help(<target>)` and the current object/result's `.contract()`, not restated
here as a second runbook. `CatalogObject.contract()` exposes the exact
object-bound verify, preview, and readiness continuations; this is the canonical
read point after `ms.load()`. The judgment-shaped steps, which no registry can
perform, are:

- Settle one candidate from evidence, registry facts, project docs, source
  SQL/provenance, prior decisions, and user answers. Reuse the matching
  query-free projection from the one explicitly scoped snapshot acquired for the
  active batch; inspect current catalog state with `ms.load()` when reuse or
  dependencies matter.
- Ask the user only when semantic intent or business policy is still unresolved
  after the evidence pass.
- Author exactly one semantic object in Python, reload, and navigate to its
  typed catalog object.

Authoring several objects and validating later is forbidden — each object is
verified before the next. Verification is result-local (a current `VerifyResult`
proves one check passed) and not a persisted gate; the skill enforces
verify-before-preview as policy, while the runtime leaves preview mechanically
callable from a loaded object.

## Datasource evidence handoff

Physical evidence comes from `marivo.datasource`. Before authoring a
datasource-backed object, inspect metadata, choose an explicit scan scope, and
project evidence from one acquisition:

```python
inspection = md.inspect(warehouse, md.table("orders"))
inspection.show()
inspection.partitions().show()
scope = md.partition(
    {"dt": "20260710"}, max_rows=1000, timeout_seconds=30
)
snapshot = inspection.sample(
    scope=scope,
    columns=("order_id", "region", "created_at", "amount"),
)
snapshot.entity(columns=("order_id",)).show()
snapshot.dimensions(columns=("region",)).show()
snapshot.time_dimensions(columns=("created_at",)).show()
snapshot.measures(columns=("amount",)).show()
```

`DatasourceRef`, `TableSource`, and the source constructors (`md.table(...)`,
`md.parquet(...)`, `md.csv(...)`, `md.json(...)`) are datasource-owned. Once an
entity is registered, do not re-supply `(datasource, source)` tuples for semantic
object parameters — physical facts stay datasource-owned, semantic refs stay
semantic-owned.

## Reference model

Object-to-object parameters use ref objects. Prefer refs returned by earlier
declarations or imported from sibling semantic modules. Use
`ms.ref("<kind>.<semantic_id>")` only for explicit forward/cross-file references,
import cycles, or generated code:

```python
sessions_per_user = ms.ratio(
    name="sessions_per_user",
    numerator=ms.ref("metric.marketing.sessions"),
    denominator=total_users,
)
```

Cross-domain refs are allowed but are existence-, cycle-, and contract-checked at
resolve time; they never fall back to copying another domain's definition into
SQL provenance. Bare semantic-id strings are not valid authoring arguments.

## Decision rules

The workflow includes a few mechanical rules so the agent does not guess.

**Field vs metric** — what to declare for a value:

| Situation | Choice |
|---|---|
| Readable from one physical column (country, platform, order date) | `ms.dimension_column` / `ms.time_dimension_column` |
| Numeric fact readable from one physical column (amount, quantity, bytes) | `ms.measure_column`, then `ms.aggregate` |
| Row-level value needing an Ibis expression (normalize, case, cast, cross-column) | `@ms.dimension` / `@ms.time_dimension` / `@ms.measure` |
| A one-off condition with no reuse value | inline in the metric expression |
| Reused across several metrics, filters, relationships, or slices | promote to a dimension/time_dimension |

Hard rule: a metric body contains only aggregation plus references to declared
dimensions/time_dimensions/measures. Row-level `.filter(...)`, `.cast(...)`,
complex `case`, or multi-step chains are extracted first.

**Sum vs ratio vs weighted average** — the decomposition:

| Metric shape | Decomposition |
|---|---|
| Directly summable absolute quantity | `ms.aggregate(..., agg="sum")` (base metric) |
| `numerator / denominator` (conversion/success rate) | `ms.ratio(numerator=..., denominator=...)` |
| Segment mean needing weights to explain mix effects | `ms.weighted_average(value=..., weight=...)` |

If the decomposition is unclear, do not default to a sum — settle the structure
from the business definition, source SQL, existing component metrics, or a user
answer.

**When to use `ms.ref`** — prefer decorated object refs for static readability
and refactoring; reserve `ms.ref("<kind>.<semantic_id>")` for forward,
cross-domain, or generated references. Its single positional argument is
`<kind>.<semantic_id>` (e.g. `ms.ref("metric.marketing.sessions")`,
`ms.ref("dimension.sales.orders.user_id")`).

### What to settle vs ask

Settle values from evidence; ask the user only for business caliber the evidence
cannot decide. Ask when:

- business definitions conflict (source SQL vs comments vs project docs);
- an amount's unit is ambiguous;
- a status/enum code's meaning is undocumented;
- several time axes are plausible;
- refund, cancellation, test-data, or other exclusion rules are unstated;
- a metric has no source SQL and may need to be trusted without provenance.

Do not ask for anything the datasource can provide — column lists, types,
comments, sample values, existing objects, or datasource shape. Fetch those with
`md.inspect(...)`, one scoped snapshot and its projections, and `ms.load()`.

## Read the current state first

Before adding or changing semantics, read the current registry:

```python
import marivo.semantic as ms

catalog = ms.load()                 # find_project() locates models/semantic/ upward
catalog.metrics.show()

sales = catalog.domains.get("sales")
orders = sales.entities.get("orders")
orders.metrics.show()
```

`ms.load()` fails closed when no project root is found — do not guess a root;
prompt for initialization or an explicit root. For diagnostics,
`python -m marivo.semantic.check` loads a `SemanticProject` and prints structured
errors, warnings, readiness, and (optionally) parity; `--strict-provenance`
treats any `unverified` metric (including via derived propagation) as a failure.

## Verification and readiness

- **`catalog.verify_object(obj)`** is a static, zero-query per-object gate. Its
  result is result-local: it proves one check passed but is not persisted as a
  workflow checkpoint and is not a runtime prerequisite for preview. The skill
  enforces verify-before-preview as policy.
- **`catalog.preview(obj, using=snapshot)`** is the explicit scoped runtime gate;
  multi-entity objects use an exact entity-keyed snapshot mapping.
- **`catalog.readiness(refs=[obj])`** is the final zero-query closeout gate. It
  reads fresh static and runtime-check evidence, enforces fresh preview evidence
  for executable families, and never refreshes automatically. On success it
  populates `ReadinessReport.analysis_handoff: SemanticToAnalysisHandoff | None`.

`ms.parity_check(...)` is an optional, potentially unbounded provenance SQL
diagnostic and is never readiness-required. `ms.richness(...)` remains advisory.

## Handoff stop conditions

The crossing to `marivo-analysis` is a typed handoff, not a bare ready-ref.
`ReadinessReport.analysis_handoff` is `None` while any blocker remains; the
agent routes a non-`None` handoff to the analysis
`boundary.semantic_handoff` target, whose sole public receiver
`Session.validate_semantic_handoff(...)` returns a `SemanticHandoffReceipt` only
after query-free validation. Do not hand off while any blocker remains:

- project load or check failed;
- a datasource required for live validation is unreachable;
- a new entity lacks its bounded discovery/preview evidence;
- a time dimension preview or cast failed;
- metric materialization or compilation failed;
- a relationship join key is unconfirmed;
- a metric spans multiple datasources without federation support;
- a metric body needs raw SQL to express the business logic.

These are warnings, not blockers — handoff may proceed with them recorded:

- a provenance-bearing metric is still `unverified`, or its parity is `drifted`;
- a metric is trusted without provenance;
- the preview sample is small but materialization succeeded;
- primary-key uniqueness was not sampled;
- string refs resolve but are refactor-fragile;
- comments are missing but source SQL, knowledge, and user confirmation suffice.
