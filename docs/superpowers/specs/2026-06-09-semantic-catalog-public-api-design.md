# Semantic Catalog Public API Design

Date: 2026-06-09

Status: draft design, pending written-spec review

## Problem

The current semantic reader surface exposes too many shapes to agents after a
model is loaded:

- `SemanticProject.load()` returns a project lifecycle result, not the loaded
  semantic layer that the agent wants to consume.
- Agents must learn separate `list_*`, `get_*`, `search`, `describe`,
  `dependencies`, and `dependents` methods before they can answer a simple
  question such as "which metrics can I use on this dataset?"
- `list_*` returns summary DTOs, `get_*` returns IR objects, `search` returns
  hits, `describe` returns a different description DTO, and dependency methods
  return tree nodes. The agent has to stitch those surfaces together.
- Semantic-object descriptive fields are split between `description` and
  `ai_context`, so agents must decide which field is authoritative for business
  meaning.
- Source locations currently risk pointing at authoring implementation helpers
  rather than user semantic files, which blocks reliable source edits.

The result is not a progressive-disclosure API. It is a low-level reader API
with helpful pieces, but the agent has to understand implementation categories
before it can browse, select, and use semantic refs.

## Goals

- Make `ms.load()` the normal public entrypoint for consuming a semantic layer.
- Return a loaded `SemanticCatalog` directly; do not expose `SemanticProject` in
  agent-facing docs or skills.
- Let agents progressively browse the semantic hierarchy with one list method.
- Let agents retrieve one `SemanticObject` with one get method.
- Let every semantic object expose a stable ref that can be passed directly to
  analysis APIs.
- Align semantic-object descriptive information with `ai_context`; remove
  `description` from the public semantic object contract.
- Keep the public API small. Add only primitives needed for browsing, object
  detail, and ref handoff.
- Follow the agent-friendly result contract from
  `2026-06-09-agent-friendly-public-api-design.md`: result-producing APIs return
  typed objects and do not write stdout; inspection happens through
  `render()` / `show()`.

## Non-Goals

- No compatibility or migration path for `description`. The field is removed
  from target public authoring and reader contracts.
- No public `SemanticProject` lifecycle API for normal agent use.
- No `display=True` / `display=False` arguments.
- No generic object recommendation engine. The catalog exposes reliable facts;
  the agent decides what to use.
- No second semantic definition layer. Python authoring files remain the source
  of truth.
- No YAML, JSON, prompt-only, or runtime-mutated semantic model.
- No natural-language fuzzy matching in the first catalog API. Search can be
  added later only as an explicit evidence-bounded query result.
- No broad public registry escape hatch.

## Core Decision

The normal agent entrypoint becomes:

```python
import marivo.semantic as ms

catalog = ms.load()
```

`catalog` is the loaded semantic layer. It is the only object an agent needs for
ordinary semantic consumption:

```python
catalog.list().show()
catalog.list("sales").show()
catalog.list("sales.orders").show()

metric = catalog.get("sales.revenue")
metric.details().show()

session.observe(metric=metric.ref)
```

`SemanticProject` may remain internally or in private/testing modules, but it is
not the public API taught to agents.

## Public Surface

### `ms.load(...)`

```python
catalog = ms.load(
    *,
    workspace_dir: str | Path | None = None,
    root: str | Path | None = None,
)
```

Behavior:

- Discovers and loads the semantic project.
- Returns `SemanticCatalog` on success.
- Raises a typed load error on failure.
- Does not print.
- Does not return a half-loaded project object.

`workspace_dir` and `root` are mutually exclusive. They preserve the two real
project-root use cases without requiring agents to construct `SemanticProject`.

Failure example:

```python
try:
    catalog = ms.load()
except ms.SemanticLoadError as exc:
    exc.show()
    raise
```

The error text should include the failed files, structured error kinds, refs,
and fix hints. The agent should not inspect a partially loaded catalog after
load failure.

### `SemanticCatalog`

`SemanticCatalog` is a read-only object graph over loaded semantic objects.

Minimal public methods:

```python
catalog.list(parent: SemanticRefInput | None = None, *, kind: SemanticKindInput | None = None) -> SemanticObjectList
catalog.get(ref: SemanticRefInput) -> SemanticObject
```

Notes:

- `get(...)` requires a full semantic ref and raises a typed not-found error
  when no object exists. It does not return `None`.
- `list(..., kind=...)` accepts only validated strings or `SemanticKind` values.
  Unsupported values raise a typed error listing supported values.
- `list(parent=None)` returns top-level models and datasources.
- `list(parent="sales")` returns datasets, model-level metrics, and
  relationships under the model.
- `list(parent="sales.orders")` returns fields, time fields, dataset-backed
  metrics, and relationships touching the dataset.
- `list(parent=<metric>)` returns component metrics for derived metrics and
  backing datasets for dataset-backed metrics.

This is enough for progressive disclosure without a separate public `tree()`.
If callers want a tree display, `catalog.list().show(depth=...)` can be added on
the result object rather than as another catalog method.

### `SemanticObjectList`

`catalog.list(...)` returns a result object:

```python
items = catalog.list("sales.orders")
items.show()
items.refs()
items.objects
```

Responsibilities:

- `render()` returns bounded plain text.
- `show()` prints `render()` and returns `None`.
- `refs()` returns `tuple[SemanticRef, ...]`.
- `objects` returns `tuple[SemanticObject, ...]`.
- Empty results render as an actionable message naming the parent and filter.

Example render:

```text
sales.orders
  time_field  sales.orders.created_at     grain=day default=true
  field       sales.orders.region
  metric      sales.revenue               additivity=additive parity=verified

available:
  catalog.get("sales.revenue")
  result.refs()
```

The render is a browsing aid. It must not omit any item in the returned
`objects` tuple unless the result explicitly says it is truncated.

### `SemanticObject`

`SemanticObject` is the single read shape for all loaded semantic objects.

Common fields:

```python
obj.ref: SemanticRef
obj.kind: SemanticKind
obj.name: str
obj.model: str | None
obj.context: AiContextView
obj.source_location: SourceLocation
obj.properties: Mapping[str, object]
```

Detail helper:

```python
obj.details() -> SemanticObjectDetails
```

Boundaries:

- `SemanticObject` does not expose top-level `description`.
- All descriptive business meaning lives under `obj.context`.
- Structural facts live in `obj.properties`, not in `context`.
- `properties` is kind-specific but JSON-safe and documented by kind.
- Hierarchy browsing stays on `catalog.list(parent=...)`. `SemanticObject` does
  not duplicate catalog navigation methods.
- Dependency and dependent refs are exposed through `obj.details()`, not through
  additional public graph methods.

### `AiContextView`

`AiContextView` mirrors the semantic authoring `ai_context` schema:

```python
obj.context.business_definition
obj.context.guardrails
obj.context.synonyms
obj.context.examples
obj.context.instructions
obj.context.owner_notes
```

There is no separate public `description` field. Short display labels come from
`name` and ref. If an object needs semantic explanation, it must provide it via
`ai_context.business_definition`. If it needs safety constraints, it must use
`ai_context.guardrails`.

Search, help, catalog rendering, and object details use `ai_context` as the only
business-description source. They may display `name` and `ref` as identifiers,
but not as business meaning.

### `SemanticRef`

`SemanticRef` is a stable semantic id that can be passed directly to analysis
operations.

Target behavior:

```python
metric = catalog.get("sales.revenue")
metric.ref             # SemanticRef("sales.revenue", kind="metric")
str(metric.ref)        # "sales.revenue"

session.observe(metric=metric.ref)
```

Analysis APIs should accept `SemanticRef` wherever they currently accept a
semantic-id string. `SemanticRef` carries enough metadata for agent debugging,
but remains string-compatible for handoff.

## Object Hierarchy

The catalog exposes one canonical hierarchy:

```text
catalog
  datasource
  model
    dataset
      time_field
      field
      metric              # dataset-backed metrics
    metric                # derived or cross-dataset metrics
    relationship
```

Placement rules:

- Datasources are top-level because they are project-shared, not owned by a
  model.
- Datasets are under their model.
- Fields and time fields are under their owning dataset.
- Dataset-backed metrics appear under their dataset and in model-level metric
  lists.
- Derived metrics appear under the model and list component metric parents.
- Cross-dataset metrics appear under the model and list all backing datasets as
  parents.
- Relationships appear under the model and as dependents of the datasets they
  connect.

These rules make browsing deterministic. They do not imply ownership changes in
the authored Python files.

## Kind-Specific Properties

`SemanticObject.properties` carries non-descriptive structural facts.

Dataset:

```python
{
    "datasource": SemanticRef("warehouse", kind="datasource"),
    "source": {"kind": "table", "table": "orders", "database": None},
    "primary_key": ("order_id",),
    "versioning": None,
}
```

Field:

```python
{
    "dataset": SemanticRef("sales.orders", kind="dataset"),
    "field_kind": "dimension",
}
```

Time field:

```python
{
    "dataset": SemanticRef("sales.orders", kind="dataset"),
    "data_type": "string",
    "granularity": "day",
    "format": "%Y%m%d",
    "timezone": None,
    "required_prefix": None,
    "is_default": True,
}
```

Metric:

```python
{
    "datasets": (SemanticRef("sales.orders", kind="dataset"),),
    "root_dataset": SemanticRef("sales.orders", kind="dataset"),
    "is_derived": False,
    "decomposition": {"kind": "sum", "components": {}},
    "additivity": "additive",
    "fanout_policy": "block",
    "verification_mode": "python_native",
    "parity_status": "verified",
    "source_sql": None,
    "source_dialect": None,
    "source_document": None,
    "source_notes": None,
}
```

Relationship:

```python
{
    "from_dataset": SemanticRef("sales.orders", kind="dataset"),
    "to_dataset": SemanticRef("sales.customers", kind="dataset"),
    "from_fields": (SemanticRef("sales.orders.customer_id", kind="field"),),
    "to_fields": (SemanticRef("sales.customers.customer_id", kind="field"),),
}
```

Datasource:

```python
{
    "backend_type": "duckdb",
}
```

Every property value must be JSON-safe or a `SemanticRef` that has a stable
string representation. No property returns internal IR objects.

## Authoring Contract Changes

This design removes `description` from semantic authoring APIs:

```python
ms.model(name="sales", ai_context={...})
ms.dataset(name="orders", datasource=warehouse, source=ms.table("orders"), ai_context={...})

@ms.field(dataset=orders, ai_context={...})
def region(table):
    return table.region

@ms.metric(datasets=[orders], decomposition=ms.sum(), ai_context={...})
def revenue(table):
    return table.amount.sum()
```

Deleted public kwargs:

- `ms.model(description=...)`
- `ms.dataset(description=...)`
- `ms.field(description=...)`
- `ms.time_field(description=...)`
- `ms.metric(description=...)`
- `ms.derived_metric(description=...)`
- `ms.relationship(description=...)`
- datasource authoring `description=...`

The only descriptive semantic fields are `ai_context` fields. Unknown
`ai_context` keys remain fail-closed.

## Agent Workflow

### Browse

```python
catalog = ms.load()
catalog.list().show()
catalog.list("sales").show()
catalog.list("sales.orders").show()
```

The agent starts broad and narrows by ref. It does not call separate
kind-specific list methods.

### Inspect

```python
revenue = catalog.get("sales.revenue")
revenue.details().show()
```

Object details are bounded and include:

- ref, kind, model, name;
- `context` fields;
- kind-specific `properties`;
- parents, children, and dependents refs;
- readiness or parity status when known;
- source location in the user semantic file.

### Handoff to analysis

```python
metric = catalog.get("sales.revenue")
region = catalog.get("sales.orders.region")
time_field = catalog.get("sales.orders.created_at")

session.observe(
    metric=metric.ref,
    by=[region.ref],
    time_field=time_field.ref,
)
```

Analysis APIs consume refs, not catalog objects. This keeps semantic discovery
separate from analysis execution.

## Error Handling

Errors must be typed and agent-actionable.

Unsupported kind:

```text
Unsupported semantic kind 'datasets'. Supported values:
model, datasource, dataset, field, time_field, metric, relationship.
```

Not found:

```text
Semantic object 'revenue' was not found. `catalog.get(...)` expects a full
semantic ref such as 'sales.revenue'.
Use catalog.list().show() to browse models or catalog.list('<model>').show()
to browse model objects.
```

Source-location failure:

```text
Semantic object 'sales.revenue' has no user source location.
Reload the catalog after fixing authoring source-location capture.
```

The catalog should never silently return an empty result because the caller
misspelled a kind. Empty result is valid only after all filters are valid.

## Source Location Requirement

Every loaded object returned by `catalog.get(...)` must point to the user
semantic file that declared it:

```text
.marivo/semantic/sales/_model.py:42
```

It must not point at `marivo/semantic/authoring.py`. This is a public contract
because agents need source locations to edit semantic definitions safely.

## Relationship to Existing Reader APIs

The target public API does not expose these methods to agents:

- `SemanticProject`
- `project.list_models`
- `project.list_datasources`
- `project.list_datasets`
- `project.list_fields`
- `project.list_time_fields`
- `project.list_metrics`
- `project.list_relationships`
- `project.get_*`
- `project.describe`
- `project.dependencies`
- `project.dependents`

Implementation may use existing reader internals while building the catalog.
The skill and public help should teach only `ms.load()`, `catalog.list(...)`,
`catalog.get(...)`, object details, and refs. `SemanticCatalog` should not grow
additional public methods unless a normal agent workflow cannot be expressed
with those primitives.

Advanced internal debugging can keep lower-level readers outside the public
agent contract, but no new public documentation should route agents through
them.

## Minimal Implementation Shape

Suggested modules:

| Module | Responsibility |
| --- | --- |
| `marivo/semantic/catalog.py` | `SemanticCatalog`, `SemanticObject`, `SemanticObjectList`, `SemanticRef`, navigation logic. |
| `marivo/semantic/__init__.py` | Export `load`, catalog/object/ref types, and authoring functions. |
| `marivo/semantic/reader.py` | Remain as internal loader/registry bridge or be split later. |
| `marivo/semantic/authoring.py` | Remove `description` kwargs and write only `ai_context` descriptions. |
| `marivo/semantic/help.py` | Teach `ms.load()` and catalog APIs; remove description guidance. |

Implementation can initially wrap current registry data, but public catalog
objects must not leak IR instances.

## Documentation and Skill Updates

Update:

- `docs/specs/semantic/python-semantic-layer.md`
- `docs/specs/semantic/authoring-pipeline-design.md`
- `marivo-skills/marivo-semantic/SKILL.md`
- `marivo-skills/marivo-semantic/references/workflow.md`
- `marivo-skills/marivo-semantic/references/authoring-patterns.md`
- runtime `ms.help(...)` output

Required doc changes:

- replace `SemanticProject` normal entrypoint examples with `ms.load()`;
- replace `project.list_*` examples with `catalog.list(...)`;
- replace `project.describe(...)` examples with `catalog.get(...).details()`;
- remove all `description=` examples from semantic object authoring;
- state that business meaning lives only in `ai_context`;
- show `obj.ref` handoff into analysis APIs.

## Testing

Add coverage for:

- `ms.load()` returns `SemanticCatalog` on a ready project.
- Load failure raises a typed error and does not return a catalog.
- `catalog.list()` shows top-level models and datasources.
- `catalog.list("model")` returns model children.
- `catalog.list("model.dataset")` returns fields, time fields, and
  dataset-backed metrics.
- `catalog.get(ref)` returns a `SemanticObject` for every kind.
- `SemanticObject.context` matches authored `ai_context`.
- `description` kwargs are rejected by authoring functions.
- Object details do not include a public `description` field.
- `obj.ref` can be passed to representative analysis APIs.
- unsupported `kind` values raise supported-value errors.
- source locations point to user semantic files.
- catalog objects and lists implement `render()` / `show()` and do not write
  stdout during ordinary result-producing calls.

## Acceptance Criteria

- A new agent can load a project with `catalog = ms.load()` and browse semantic
  objects without learning `SemanticProject`.
- The agent can progressively disclose the hierarchy with only
  `catalog.list(...)`.
- The agent can retrieve any object with `catalog.get(ref)`.
- The agent can read all descriptive business context from `obj.context`.
- No target public object exposes `description`.
- The agent can pass `obj.ref` directly to analysis operations.
- Invalid kind filters fail with explicit supported values.
- Object source locations identify user-authored semantic files.
- Public docs and skill examples use the catalog API only.
