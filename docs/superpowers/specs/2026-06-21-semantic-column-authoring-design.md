# Semantic Column Authoring Design

Date: 2026-06-21
Status: Approved design, pre-implementation
Related: `agent-guide.md` ("Agent-Facing Surface Principles"),
`docs/superpowers/specs/2026-06-16-semantic-authoring-surface-redesign-design.md`,
`docs/superpowers/specs/2026-06-21-datasource-semantic-agent-surface-fix-design.md`

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
- Keep typed refs as the normal handoff between objects.

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
    entity: EntityRef | str,
    column: str,
    domain: DomainRef | None = None,
    ai_context: AiContextValue | None = None,
) -> DimensionRef

def measure_column(
    *,
    name: str,
    entity: EntityRef | str,
    column: str,
    additivity: Additivity,
    unit: str | None = None,
    domain: DomainRef | None = None,
    ai_context: AiContextValue | None = None,
) -> MeasureRef

def time_dimension_column(
    *,
    name: str,
    entity: EntityRef | str,
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

`entity` remains `EntityRef | str` for parity with the existing decorators.
Examples and skills must prefer typed refs. Future tightening can make raw
strings explicit through `ms.ref(...)`, but that is not part of this phase.

### Validation

The helpers reuse the existing validation rules from the decorator path:

- default domain resolution;
- duplicate object checks, including the shared dimension / measure namespace;
- entity-domain consistency checks;
- `ai_context` construction and validation;
- measure `additivity` normalization and unit validation;
- time-dimension parse / granularity / sample-interval compatibility checks.

Additional direct-column validation:

- `column` must be a non-empty string;
- `name` remains required and must not be inferred from `column`;
- the generated callable must return `table[column]`.

The loader still catches missing physical columns through the existing
materialization / preview / readiness flow. The helper should not require live
datasource access at declaration time.

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
  sidecar callable.
- `time_dimension_column(...)` pushes a `DimensionIR` with
  `is_time_dimension=True`, `kind=DimensionKind.TIME`, parse metadata, and a
  generated sidecar callable.
- `measure_column(...)` pushes a `MeasureIR` with the existing additivity and
  unit fields plus a generated sidecar callable.

The materializer already executes sidecar callables for dimensions, time
dimensions, and measures. Because these helpers produce the same IR and
callable shape as the decorators, preview, readiness, observe planning,
analysis execution, and catalog details should not branch on declaration style.

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
- `marivo/semantic/help.py` and help tests — summary / describe entries. The
  file may already have unrelated in-progress changes; preserve them.
- `tests/test_semantic_authoring.py` or focused semantic authoring tests —
  helper IR construction, validation, duplicate handling, and call-site
  location.
- `tests/test_semantic_materializer.py` or focused materializer tests —
  direct-column sidecar materializes with bracket access.
- `tests/test_public_surface.py` / import tests — new public names and help
  coverage.
- `marivo/skills/marivo-semantic/**` — default examples and guidance.
- `docs/specs/semantic/python-semantic-layer.md` — target contract.
- `site/src/content/docs/{en,zh-cn}/{latest,v0.1}/**` — public docs in both
  languages and versions.
- README or quick-start docs if they show semantic authoring examples.

## Testing

Recommended verification sequence:

```bash
make test TESTS='tests/test_semantic_authoring.py'
make test TESTS='tests/test_semantic_materializer.py'
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
- A physical column named like an Ibis method, for example `count` or `info`,
  works through the helper because generated callables use `table[column]`.
- Docs and skills present `*_column(...)` as the default path and decorators as
  expression escape hatches.
- No YAML or secondary semantic spec is added.

## Open Decisions

No open product decisions remain for this phase. The next step is an
implementation plan that breaks this design into code, tests, docs, skill, and
site updates.
