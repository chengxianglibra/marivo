# Semantic Column Authoring Design

Date: 2026-06-21
Status: Approved design, pre-implementation
Related: `agent-guide.md` ("Agent-Facing Surface Principles"),
`docs/superpowers/specs/2026-06-16-semantic-authoring-surface-redesign-design.md`,
`docs/superpowers/specs/2026-06-21-datasource-semantic-agent-surface-fix-design.md`

> Historical note: this spec predates removal of the public semantic
> `prepare_*` authoring stage. Current agents must use
> `help -> discover -> settle/grill -> author -> verify`; remaining
> `prepare_*` text below is historical context only.

## Problem

Marivo semantic files are executable Python, and Python should remain the
single source of truth. A parallel YAML or Markdown semantic spec would create
an avoidable consistency problem: the spec grammar, Python DSL, loader,
validator, materializer, docs, and skills would all need to agree on the same
expressive power. Any gap between them would leave agents and users unsure
which layer is authoritative.

At the same time, the current default authoring shape is too Python-heavy for
business review. Common row-level semantic objects are declared with decorators
and function bodies even when they only point at a physical column:

```python
@ms.dimension(entity=orders, name="region")
def region(table):
    return table.region

@ms.measure(entity=orders, name="amount", additivity="additive", unit="CNY")
def amount(table):
    return table.amount
```

This shape is expressive and useful for custom expressions, but it forces
business reviewers to understand decorator syntax, function names, table
parameters, return bodies, and Ibis attribute access for simple declarations
whose real business payload is `name`, `column`, `unit`, `additivity`, and
`ai_context`. It also makes agent-generated semantic files longer and easier
to get wrong, especially around attribute shadowing (`table.count`,
`table.info`) and duplicated `name` / function-symbol choices.

The desired direction is simpler: keep executable Python as the truth source,
but make the common path read like structured declarations.

## Goals

- Keep Python semantic files as the only authoritative authoring artifact.
- Add a declarative Python authoring path for direct physical column
  references.
- Preserve existing decorator APIs as the expression escape hatch.
- Reuse existing IR, registry, loader, sidecar, materializer, catalog, preview,
  readiness, and analysis behavior. This is an authoring-surface improvement,
  not a second runtime.
- Make the default examples easier for non-Python business reviewers to audit:
  object name, source column, business definition, guardrails, unit, and
  additivity should be visible in one call block.
- Require typed refs for new helper dependencies. The common path must pass
  `EntityRef` values, not guessed semantic-id strings.
- Keep the design aligned with `agent-guide.md` public API principles: concrete
  public types, teachable errors, bounded discovery, pinned public surface, and
  closed entry shapes instead of optional-field mega-classes.

## Non-Goals

- No YAML, Markdown, TOML, or other parallel semantic spec format.
- No expression builder such as `ms.col("amount") - ms.col("refund")` in this
  phase.
- No removal of `@ms.dimension`, `@ms.time_dimension`, or `@ms.measure`.
- No split of `DimensionIR` / `MeasureIR` / `MetricIR` in this phase.
- No change to metric tiering: `ms.aggregate`, `ms.count`, `ms.ratio`,
  `ms.weighted_average`, `ms.linear`, and `@ms.metric` keep their current
  roles.
- No attempt to make physical column names business-approved labels. Semantic
  `name=` remains explicit.

## Design

Add three public authoring helpers:

```python
region = ms.dimension_column(
    name="region",
    entity=orders,
    column="region",
    ai_context=ms.ai_context(
        business_definition="Customer sales region at order time.",
        guardrails=["Do not treat missing region as a separate market."],
    ),
)

amount = ms.measure_column(
    name="amount",
    entity=orders,
    column="amount",
    additivity="additive",
    unit="CNY",
    ai_context=ms.ai_context(
        business_definition="Order amount before refunds.",
    ),
)

log_date = ms.time_dimension_column(
    name="log_date",
    entity=orders,
    column="dt",
    granularity="day",
    parse=ms.strptime("%Y%m%d"),
    is_default=True,
    ai_context=ms.ai_context(
        business_definition="Partition date used for default order reporting windows.",
    ),
)
```

These helpers are equivalent to the current decorator shape with a one-line
body:

```python
@ms.dimension(entity=orders, name="region")
def region(table):
    return table["region"]
```

The helper uses bracket access internally (`table[column]`), not attribute
access, so column names that shadow Ibis methods stay safe by default.

### Public Signatures

```python
def dimension_column(
    *,
    name: str,
    entity: EntityRef,
    column: str,
    domain: DomainRef | None = None,
    ai_context: AiContextValue | None = None,
) -> DimensionRef

def measure_column(
    *,
    name: str,
    entity: EntityRef,
    column: str,
    additivity: Additivity,
    unit: str | None = None,
    domain: DomainRef | None = None,
    ai_context: AiContextValue | None = None,
) -> MeasureRef

def time_dimension_column(
    *,
    name: str,
    entity: EntityRef,
    column: str,
    granularity: Literal[
        "year", "quarter", "month", "week", "day", "hour", "minute", "second"
    ],
    parse: SemanticParse | None = None,
    is_default: bool = False,
    domain: DomainRef | None = None,
    ai_context: AiContextValue | None = None,
) -> TimeDimensionRef
```

`entity` is intentionally stricter than the existing decorators. The helpers
are top-level declarations like `ms.count(...)`, and `ms.count(...)` already
rejects strings so agents do not guess raw ids. Requiring `EntityRef` makes the
new default path typed from the start. Existing decorators keep their current
`EntityRef | str` compatibility shape as expression escape hatches; this phase
does not broaden the new helpers to match that legacy tolerance.

## Agent-Facing Public API Check

This design conforms to the `agent-guide.md` public API rules as follows:

- **Concrete public types:** all new helper parameters and return annotations
  use concrete semantic value types. `entity` is `EntityRef`, not
  `EntityRef | str`; `column` and `name` are explicit `str`; `ai_context` uses
  the existing typed `AiContextValue`.
- **Errors teach:** passing a raw string for `entity` must fail at helper call
  time with a `SemanticDecoratorError` that states an `EntityRef` was expected
  and points to `orders = ms.entity(...)` as the next step. Missing physical
  columns still fail through existing preview/readiness materialization
  evidence.
- **One path per capability:** direct physical column declarations use
  `*_column(...)`; expression-bearing row-level objects use decorators. These
  are separate capabilities, not two public spellings for the same operation.
- **Discovery stays bounded:** the helpers join the existing semantic authoring
  family in `help()`, and `describe("dimension_column")`,
  `describe("time_dimension_column")`, and `describe("measure_column")` must
  include minimal runnable examples.
- **Surface growth is gated:** the helpers are added to `marivo.semantic.__all__`
  and the pinned public-surface tests in the same change.
- **Closed variants over optional fields:** direct-column objects and expression
  objects use different entry shapes. The helper signatures do not add optional
  `expr` / `body` / `kind` parameters.

### Validation

The helpers reuse the existing validation rules from the decorator path:

- default domain resolution;
- duplicate object checks, including the shared dimension / measure namespace;
- typed entity-ref validation and entity-domain consistency checks;
- `ai_context` construction and validation;
- measure `additivity` normalization and unit validation;
- time-dimension parse / granularity / sample-interval compatibility checks.

Additional direct-column validation:

- `column` must be a non-empty string;
- `name` remains required and must not be inferred from `column`.

The generated sidecar callable is a generation guarantee, not user input to
validate: it must return `table[column]`. Because there is no user-authored
body, the helper skips body-AST validation. This is safe because
`DimensionIR` and `MeasureIR` do not store `body_ast_hash`; only `MetricIR`
does.

The loader still catches missing physical columns through the existing
materialization / preview / readiness flow. The helper should not require live
datasource access at declaration time.

Raw string entity inputs are invalid for all three helpers. They should raise
the same structured error family as other invalid authoring refs, with a hint
that the user should pass the `EntityRef` returned by `ms.entity(...)`.

### Source Location and Python Symbol

For direct helpers, `python_symbol` should be the semantic `name`, because no
user-defined Python function exists. The `SourceLocation` should point to the
helper call site, matching the current `domain`, `entity`, `aggregate`, and
derived metric constructors.

The generated sidecar callable can be private and synthetic. Its function name
is not part of the public contract; catalog details should continue to expose
the semantic object name and call location.

### Runtime Model

No new IR is introduced.

- `dimension_column(...)` pushes a `DimensionIR` with
  `is_time_dimension=False`, `kind=DimensionKind.CATEGORICAL`, and a generated
  sidecar callable. It returns a `DimensionRef` and appends that ref to
  `ctx.pending_refs`.
- `time_dimension_column(...)` pushes a `DimensionIR` with
  `is_time_dimension=True`, `kind=DimensionKind.TIME`, parse metadata, and a
  generated sidecar callable. It returns a `TimeDimensionRef` and appends that
  ref to `ctx.pending_refs`.
- `measure_column(...)` pushes a `MeasureIR` with the existing additivity and
  unit fields plus a generated sidecar callable. It returns a `MeasureRef` and
  appends that ref to `ctx.pending_refs`.

The materializer already executes sidecar callables for dimensions, time
dimensions, and measures. Because these helpers produce the same IR and
callable shape as the decorators, preview, readiness, observe planning,
analysis execution, and catalog details should not branch on declaration style.
The `pending_refs` registration is required for declaration-style parity:
loader wiring attaches the field resolver only to refs captured in
`ctx.pending_refs`, and metric bodies must be able to call helper-authored field
refs just like decorator-authored refs.

## Authoring Guidance

The default semantic authoring ladder becomes:

1. `ms.entity(...)`
2. `ms.time_dimension_column(...)` for physical time columns
3. `ms.dimension_column(...)` for physical categorical columns
4. `ms.measure_column(...)` for physical quantitative facts
5. `ms.verify_object(measure_ref)`
6. `ms.aggregate(...)` or `ms.count(...)`
7. `ms.ratio(...)` / `ms.weighted_average(...)` / `ms.linear(...)` for derived
   metrics

Decorator APIs remain documented as escape hatches:

```python
@ms.measure(entity=orders, additivity="additive", unit="CNY")
def net_amount(table):
    return table.amount - table.refund_amount
```

The skill and docs language should make this explicit:

- use `*_column(...)` when the semantic object maps directly to one physical
  column;
- use decorators when the semantic object is an Ibis expression over one or
  more columns;
- use `@ms.metric(...)` only for expression-body tier-2 metrics that cannot be
  represented as measure + aggregate or a derived composition constructor.

## Affected Files

Implementation should touch the following surfaces as one contract slice:

- `marivo/semantic/authoring.py` — helper implementations and docstrings.
- `marivo/semantic/__init__.py` — public exports.
- `marivo/semantic/help.py` and help tests — summary / describe entries with
  minimal runnable examples. The file may already have unrelated in-progress
  changes; preserve them.
- `marivo/semantic/prepare.py` and prepare tests — Brief next-step hints and
  `ibis_attribute_shadowing` remediation should point direct-column cases at
  `ms.dimension_column(...)`, `ms.time_dimension_column(...)`, or
  `ms.measure_column(...)` instead of only teaching bracket notation in
  decorator bodies. Decorator bracket guidance still applies to expression
  escape hatches.
- `tests/test_semantic_authoring.py` or focused semantic authoring tests —
  helper IR construction, typed entity-ref validation, raw-string rejection,
  duplicate handling, and call-site location.
- `tests/test_semantic_materializer.py` or focused materializer tests —
  direct-column sidecar materializes with bracket access.
- `tests/test_public_surface.py` / import tests — new public names and help
  coverage.
- `marivo/skills/marivo-semantic/**` — default examples and guidance.
- `docs/specs/semantic/python-semantic-layer.md` — target contract. The update
  must also clean stale current examples such as `@ms.dimension(...,
  description=...)` where the live API uses `ai_context=...`, and the
  "Field vs Metric" decision table should prefer column helpers for direct
  physical columns.
- `site/src/content/docs/{en,zh-cn}/{latest,v0.1}/**` — public docs in both
  languages and versions.
- README or quick-start docs if they show semantic authoring examples.

## Testing

Recommended verification sequence:

```bash
make test TESTS='tests/test_semantic_authoring.py'
make test TESTS='tests/test_semantic_materializer.py'
make test TESTS='tests/test_semantic_prepare.py'
make test TESTS='tests/test_public_surface.py tests/test_semantic_imports.py'
make examples-check
make typecheck
make lint
```

Run the narrowest relevant tests first while implementing, then broaden before
commit because this changes the public semantic authoring surface.

## Acceptance Criteria

- A semantic file can define entity, dimensions, time dimensions, measures,
  aggregate metrics, counts, and derived metrics without any decorator body
  when all row-level fields are direct physical columns.
- `catalog.get(ref).details()` and `.show()` produce the same kind-specific
  information for helper-authored objects as for decorator-authored objects.
- `catalog.preview(...)` works for helper-authored dimensions, time dimensions,
  measures, and metrics.
- `ms.verify_object(ref)` accepts and validates helper-authored dimensions,
  time dimensions, and measures with the same evidence semantics as
  decorator-authored objects.
- Passing `entity="sales.orders"` to any `*_column(...)` helper is rejected
  with a structured error that points callers to an `EntityRef`.
- `catalog.readiness(...)` / `ms.readiness(...)` treat helper-authored objects
  the same as decorator-authored objects.
- A helper-authored `DimensionRef`, `TimeDimensionRef`, or `MeasureRef` can be
  called inside an expression-body decorator after load, proving its resolver
  was wired through `ctx.pending_refs`.
- `prepare_*` Briefs and shadowing advisories steer direct physical column
  authoring to `*_column(...)`, while preserving bracket-notation advice for
  decorator expression bodies.
- A physical column named like an Ibis method, for example `count` or `info`,
  works through the helper because generated callables use `table[column]`.
- Docs and skills present `*_column(...)` as the default path and decorators as
  expression escape hatches.
- No YAML or secondary semantic spec is added.

## Open Decisions

No open product decisions remain for this phase. The next step is an
implementation plan that breaks this design into code, tests, docs, skill, and
site updates.
