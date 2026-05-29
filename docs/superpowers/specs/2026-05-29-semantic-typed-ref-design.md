# Semantic Typed Reference Design

Status: approved design.

This design tightens semantic object references in `marivo.semantic` authoring.
It removes naked string semantic references while preserving an explicit,
typed `ms.ref(...)` builder for generated definitions, forward references, and
other cases where a normal Python import is not practical.

## Context

The current semantic authoring surface accepts both decorated reference objects
and plain strings in many semantic object reference positions:

- `@ms.field(dataset="sales.orders")`
- `@ms.metric(datasets=["sales.orders"], ...)`
- `ms.ratio(numerator="sales.revenue", denominator="sales.orders_count")`
- `ms.relationship(from_fields=["sales.order_id"], ...)`

`ms.ref(...)` currently returns the input string unchanged, so it marks intent
for readers but does not add type information. This makes agent-authored code
easy to get subtly wrong: a dataset position can receive a metric id, a typo can
look like a valid Python string, and refactoring depends on broad text search.

The target authoring style is already Python-native: decorated objects return
reference values such as `DatasetRef`, `FieldRef`, `TimeFieldRef`, `MetricRef`,
and `RelationshipRef`. Same-model files should import these values with normal
relative imports; cross-model references should prefer the referenced model's
`_exports.py` boundary. This design completes that direction by making every
semantic object reference either a decorated ref object or an explicit typed
builder ref.

Datasource names are not semantic object refs in this design. They remain string
configuration keys in `@ms.dataset(datasource="warehouse")`.

## Goals

- Reject naked string semantic object references in authoring APIs.
- Keep `ms.ref(...)` as an explicit, typed escape hatch for generated or forward
  references.
- Make reference kind available at decorator-time and assembly-time.
- Preserve normal Python import ergonomics for same-model and cross-model refs.
- Produce structured, agent-actionable errors when refs have the wrong shape,
  wrong kind, or missing target.
- Update tests, docs, and skill examples to teach one mechanical rule: import a
  decorated ref, or call `ms.ref("kind.model.name")`.

## Non-Goals

- No compatibility mode for naked strings in semantic object ref positions.
- No automatic migration command in this change.
- No change to datasource authoring keys such as `datasource="warehouse"`.
- No change to analysis runtime refs such as `MetricRef("sales.revenue")`; this
  design is scoped to `marivo.semantic` authoring declarations.
- No new import graph policy beyond the existing `_exports.py` recommendation.

## Reference Model

Add a typed builder reference value, tentatively named `SemanticRef`, in
`marivo.semantic.ir`:

```python
@dataclass(frozen=True)
class SemanticRef:
    kind: SymbolKind
    semantic_id: str
```

`SemanticRef` represents a reference produced by `ms.ref(...)`, not an object
declared by a decorator. Decorated refs remain the preferred path because they
are ordinary Python symbols and preserve jump-to-definition behavior.

`ms.ref(...)` accepts exactly one string in this format:

```text
<kind>.<model>.<name>
```

For the first implementation, supported kinds are:

- `dataset`
- `field`
- `time_field`
- `metric`
- `relationship`

Examples:

```python
ms.ref("dataset.sales.orders")
ms.ref("field.sales.order_user_id")
ms.ref("time_field.sales.order_date")
ms.ref("metric.sales.revenue")
ms.ref("relationship.sales.orders_to_users")
```

`ms.ref("sales.revenue")` is invalid because it omits the kind. Unknown kinds,
empty segments, and malformed ids raise `SemanticDecoratorError` with
`kind=invalid_ref`.

## Authoring API Contract

Semantic object reference positions accept only decorated refs of the expected
kind or `SemanticRef` values of the expected kind.

### Dataset Positions

`@ms.field(dataset=...)`, `@ms.time_field(dataset=...)`, relationship endpoint
datasets, and metric `datasets=[...]` accept `DatasetRef` or
`ms.ref("dataset.<model>.<name>")`.

```python
from .datasets import orders

@ms.field(dataset=orders)
def paid_amount(orders):
    return orders.amount.where(orders.pay_status == 1, 0)

@ms.metric(datasets=[ms.ref("dataset.sales.orders")], decomposition=ms.sum())
def revenue(orders):
    return orders.amount.sum()
```

The following is invalid:

```python
@ms.metric(datasets=["sales.orders"], decomposition=ms.sum())
def revenue(orders):
    return orders.amount.sum()
```

### Field Positions

`ms.relationship(from_fields=..., to_fields=...)` accepts `FieldRef`,
`TimeFieldRef`, or matching `ms.ref("field...")` / `ms.ref("time_field...")`
values. It no longer accepts physical column names or naked semantic-id
strings.

```python
from .fields import order_user_id, user_id

ms.relationship(
    name="orders_to_users",
    from_dataset=orders,
    to_dataset=users,
    from_fields=[order_user_id],
    to_fields=[user_id],
)
```

### Metric Positions

`ms.ratio(...)` and `ms.weighted_average(...)` accept `MetricRef` or
`ms.ref("metric.<model>.<name>")` only.

```python
@ms.metric(
    datasets=[],
    decomposition=ms.ratio(
        numerator=ms.ref("metric.sales.revenue"),
        denominator=ms.ref("metric.sales.orders_count"),
    ),
)
def average_order_value():
    return ms.component("numerator") / ms.component("denominator")
```

Passing `"sales.revenue"` directly is invalid.

## Loader And Validation

Decorator-time validation handles reference shape:

- A `str` in any semantic object ref position raises `SemanticDecoratorError`.
- A `SemanticRef` whose `kind` is not accepted by the target parameter raises
  `SemanticDecoratorError`.
- `ms.ref(...)` validates string format immediately and returns `SemanticRef`.

Assembly-time validation handles reference target existence and cross-object
contracts:

- Dataset refs must resolve to registered datasets.
- Field and time field refs must resolve to registered fields, and relationship
  field refs must belong to their declared endpoint datasets.
- Metric component refs must resolve to registered metrics.
- Relationship refs must resolve to registered relationships when such refs are
  introduced in future APIs.

Kind mismatch errors should be distinct from missing-target errors when the id
exists under a different registry collection. For example,
`datasets=[ms.ref("metric.sales.revenue")]` should report an invalid ref kind,
not a missing dataset.

## Agent Rules

Agents should follow this order when authoring refs:

1. Same file: use the decorated ref variable directly.
2. Same model, different file: import with `from .datasets import orders` or the
   relevant sibling module.
3. Cross model: import from the referenced model's `_exports.py` when available.
4. Generated or forward reference: use `ms.ref("kind.model.name")`.

The governing principle is: if a decorated ref can be imported naturally, import
it. Use `ms.ref("kind.model.name")` only when importing would create a Python
import cycle, make generated code unnecessarily brittle, or pierce a model
boundary that should remain internal.

Agents must not write naked semantic-id strings in decorator or builder
arguments. The actionable repair is either to import the decorated ref or to wrap
the fully distinguished id with `ms.ref(...)` and include the object kind.
Agents must also not invent per-kind helper APIs such as `ms.dataset_ref(...)`,
`ms.field_ref(...)`, or `ms.metric_ref(...)`; the only builder ref API is
`ms.ref(...)`.

## Documentation And Test Updates

The implementation should update:

- `marivo/semantic/ir.py` for the new builder ref type.
- `marivo/semantic/authoring.py` for narrowed signatures and `ms.ref(...)`.
- `marivo/semantic/validator.py` for kind-aware assembly validation.
- `marivo/semantic/help.py` and constraints/help examples for the new rule.
- Semantic tests that currently use naked strings, replacing them with decorated
  refs or typed `ms.ref(...)` values.
- `docs/specs/semantic/python-semantic-layer.md` and
  `marivo-skills/marivo-semantic` examples so agents learn the strict contract.

Narrow tests should cover:

- `ms.ref(...)` returns a typed ref with the expected kind and semantic id.
- malformed `ms.ref(...)` values fail at decorator-time.
- naked string dataset, field, and metric refs fail at decorator-time.
- wrong-kind `SemanticRef` values fail in the receiving API.
- missing targets still fail at assembly-time with the existing missing-ref
  errors.
- valid cross-file relative imports and cross-model `_exports.py` imports load.

## Success Criteria

- No `marivo.semantic` authoring API accepts a naked string semantic object ref.
- `ms.ref("kind.model.name")` is the only string-based semantic ref entrypoint.
- Error messages tell agents whether to import a decorated ref or use typed
  `ms.ref(...)`.
- Existing docs and skill examples no longer teach direct string refs.
- Skill guidance tells agents to import decorated refs first and reserve
  `ms.ref("kind.model.name")` for import cycles, generated definitions, or
  protected model boundaries.
- The semantic test suite passes through repository entrypoints after the
  implementation.
