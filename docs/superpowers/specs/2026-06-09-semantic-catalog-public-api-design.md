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
- Semantic-object descriptive fields do not have clear roles. Agents need to know
  whether `description` is only a short display summary or whether it carries
  business meaning that competes with `ai_context`.
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
- Keep `description` as a short display summary while aligning business-facing
  semantic guidance with `ai_context`.
- Keep the public API small. Add only primitives needed for browsing, object
  detail, readiness, and ref handoff.
- Follow the agent-friendly result contract from
  `2026-06-09-agent-friendly-public-api-design.md`: result-producing APIs return
  typed objects and do not write stdout; inspection happens through
  `render()` / `show()`.

## Non-Goals

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

revenue = catalog.get("sales.revenue")
details = revenue.details()
details.additivity
details.entities

report = catalog.readiness(refs=[revenue.ref])
if report.blocked:
    report.show()
    raise SystemExit

session.observe(metric=revenue.ref)
```

`SemanticProject` may remain internally or in private/testing modules, but it is
not the public API taught to agents.

## Public Surface

### `ms.load(...)`

```python
catalog = ms.load(
    *,
    workspace_dir: str | Path | None = None,
)
```

Behavior:

- Discovers and loads the semantic project rooted at `workspace_dir`.
- Defaults to the current working directory when `workspace_dir` is omitted.
- Returns `SemanticCatalog` on success.
- Raises a typed load error on failure.
- Does not print.
- Does not return a half-loaded project object.

Failure example:

```python
try:
    catalog = ms.load()
except ms.errors.SemanticLoadFailed as exc:
    print(exc)
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
catalog.readiness(refs: Sequence[SemanticRefInput] | None = None) -> ReadinessReport
```

Notes:

- `parent`, `ref`, and `refs` accept either `SemanticRef` values or full semantic
  ref strings such as `"sales.revenue"`. Short names are not accepted.
- `get(...)` requires a full semantic ref and raises a typed not-found error
  when no object exists. It does not return `None`.
- `list(..., kind=...)` accepts only validated strings or `SemanticKind` values.
  Unsupported values raise a typed error listing supported values.
- `list(parent=None)` returns top-level models and datasources.
- `list(parent="sales")` returns datasets/entities, metrics, and relationships
  under the model.
- `list(parent="sales.orders")` returns fields, time fields, relationships
  touching the dataset, and a filtered metric view containing only model-owned
  metrics that involve that dataset.
- Cross-dataset metrics are returned by each participating dataset's filtered
  metric view.
- Metric ownership, hierarchy children, and refs are model-scoped. The same
  metric object keeps its canonical metric ref, such as `"sales.revenue"`, no
  matter which dataset-filtered list exposes it. Dataset-prefixed metric aliases
  such as `"sales.orders.revenue"` are not created by browsing.
- `list(parent=...)` is hierarchy browsing, not dependency traversal. Non-container
  refs such as metrics, fields, time fields, and relationships raise a typed
  unsupported-parent error with a hint to use `catalog.get(ref).details()`.
- `readiness(refs=...)` is the catalog-level closeout gate before analysis
  handoff. It replaces the normal agent need to reach for project-level
  readiness APIs.

This is enough for progressive disclosure without a separate public `tree()`.

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

filtered metrics:
  metric      sales.revenue               additivity=additive parity=verified

next steps:
  catalog.get("sales.revenue")    # retrieve a SemanticObject by full ref
  result.refs()                   # obtain all SemanticRef values for analysis handoff
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
obj.domain: str | None
obj.description: str | None
obj.context: AiContextView
obj.source_location: SourceLocation
```

Detail helper:

```python
obj.details() -> SemanticObjectDetails
```

Boundaries:

- `SemanticObject` exposes top-level `description`.
- `description` is a short display summary. It is not a substitute for
  `ai_context.business_definition` or `ai_context.guardrails`.
- Business meaning and usage guidance live under `obj.context`.
- Structural facts live on the typed details object returned by `obj.details()`.
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

`description` remains a public short summary for quick display. If an object
needs semantic explanation, it must provide it via
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
metric.ref                                   # SemanticRef("sales.revenue", kind="metric")
str(metric.ref)                              # "sales.revenue"

session.observe(metric=metric.ref)
```

`catalog.list(...).refs()` and `catalog.get(...)` return `SemanticRef` objects,
while catalog lookup methods also accept full string refs. Analysis APIs accept
`SemanticRef` wherever they currently accept a semantic-id string. `SemanticRef`
carries enough metadata for agent debugging, but its `str()` representation is
the plain ref string for handoff compatibility.

## Object Hierarchy

The catalog exposes one canonical hierarchy:

```text
catalog
  datasource
  model
    dataset
      time_field
      field
      relationship
    metric                # dataset-backed, derived, and cross-dataset metrics
```

Placement rules:

- Datasources are top-level because they are project-shared, not owned by a
  model.
- Datasets are under their model.
- Fields and time fields are under their owning dataset.
- All metric kinds are under their model. This is the metric tree position, the
  internal child relationship, and the ref namespace.
- A metric that uses one dataset is returned by `catalog.list(<model>)` and by
  `catalog.list(<dataset>)` when that dataset is involved. A cross-dataset metric
  is returned by the model list and by every participating dataset's filtered
  metric view.
- Derived metric components are not exposed as metric children. They are
  accessible via `metric_obj.details()`, which lists the component metric refs
  and their backing datasets.
- Relationships appear under every dataset they connect.

These rules make browsing deterministic. They do not imply ownership changes in
the authored Python files.

## Kind-Specific Details

`SemanticObject.details()` returns a typed details object. The common detail
fields are:

```python
details.ref
details.kind
details.name
details.domain
details.description
details.context
details.source_location
details.parents
details.children
details.dependents
```

Each kind adds concrete fields. There is no public `properties` bag.

Dependency fields have fixed meanings:

- `details.parents` are refs required to define or analyze the object. For a
  metric this includes backing dataset refs, component metric refs, and required
  relationship refs when the metric spans datasets.
- `details.children` are hierarchy children. For a model this includes datasets
  and metrics. For a dataset this includes fields, time fields, and
  relationships, but not metrics. Metrics have no hierarchy children, even when
  they are derived from component metrics.
- `details.dependents` are refs for semantic objects that depend on this object.

Dataset:

```python
details.datasource          # SemanticRef("warehouse", kind="datasource")
details.source              # TableSource(table="orders", database=None)
details.primary_key         # ("order_id",)
details.versioning          # None | SnapshotVersioning | ValidityVersioning
```

Field:

```python
details.entity              # SemanticRef("sales.orders", kind="entity")
details.dimension_kind      # "categorical" | "measure"
```

Time field:

```python
details.entity
details.data_type
details.granularity
details.format
details.timezone
details.required_prefix
details.is_default
details.sample_interval
```

Metric:

```python
details.entities
details.root_entity
details.is_derived
details.component_metrics
details.components
details.required_relationships
details.decomposition
details.additivity
details.fanout_policy
details.verification_mode
details.parity_status
details.source_sql
details.source_dialect
```

For metrics, `details.entities` is the full set of entities that make the metric
analyzable; `details.component_metrics` is empty for non-derived metrics and
contains exact metric refs for derived metrics; `details.components` preserves
role names such as `numerator` and `denominator`; `details.required_relationships`
contains exact relationship refs needed for cross-entity analysis. Metrics are
retrieved by their canonical domain-scoped metric ref.

Relationship:

```python
details.from_entity
details.to_entity
details.from_dimensions
details.to_dimensions
```

Datasource:

```python
details.backend_type
```

Details fields are typed and documented. They may contain `SemanticRef` values,
plain scalars, tuples, or small public DTOs such as source/versioning objects.
No details field returns internal IR objects.

## Authoring Contract Changes

This design keeps `description` in semantic authoring APIs and narrows its role:
it is a short display summary, while business meaning and usage constraints live
in `ai_context`.

```python
ms.domain(name="sales", description="Sales analytics", ai_context={...})
ms.entity(
    name="orders",
    datasource=warehouse,
    source=ms.table("orders"),
    description="Order facts.",
    ai_context={...},
)

@ms.dimension(dataset=orders, description="Sales region.", ai_context={...})
def region(table):
    return table.region

@ms.metric(
    datasets=[orders],
    decomposition=ms.sum(),
    description="Gross revenue.",
    ai_context={...},
)
def revenue(table):
    return table.amount.sum()
```

Unknown `ai_context` keys remain fail-closed. `description` must not be used for
business definitions, guardrails, examples, instructions, or owner notes.

## Agent Workflow

### Browse

```python
catalog = ms.load()
catalog.list().show()                              # top-level: models and datasources

catalog.list("sales").show()                       # datasets, metrics, relationships
catalog.list("sales.orders").show()                # fields, time fields, relationships, filtered metrics
catalog.list("sales.orders", kind="metric").show() # metrics analyzable from orders
```

The agent starts broad and narrows using full semantic refs. It does not call
separate kind-specific list methods.

### Inspect

```python
revenue = catalog.get("sales.revenue")
details = revenue.details()
```

Object details are bounded and include:

- ref, kind, domain, name;
- description;
- `context` fields;
- kind-specific typed details;
- for derived metrics: component metric refs and backing dataset refs;
- readiness or parity status when known;
- source location in the user semantic file.

### Handoff to analysis

```python
metric = catalog.get("sales.revenue")
region = catalog.get("sales.orders.region")
time_field = catalog.get("sales.orders.created_at")

readiness = catalog.readiness(refs=[metric.ref, region.ref, time_field.ref])
if readiness.blocked:
    readiness.show()
    raise SystemExit

session.observe(
    metric=metric.ref,
    by=[region.ref],
    time_field=time_field.ref,
)
```

Analysis APIs consume refs, not catalog objects. This keeps semantic discovery
separate from analysis execution. Catalog readiness is the required semantic
gate before handoff.

`catalog.readiness(refs=...)` resolves the semantic dependency closure for the
refs supplied. For a metric this includes backing datasets, component metrics,
and required relationships. Prospective analysis inputs such as dimensions and
time fields must still be included when the agent intends to use them.

## Error Handling

Errors must be typed and agent-actionable.

Unsupported kind:

```text
Unsupported semantic kind 'datasets'. Supported values:
model, datasource, dataset, field, time_field, metric, relationship.
```

Not found:

```text
Semantic object 'revenue' was not found. `catalog.get(...)` requires a
full semantic ref such as 'sales.revenue'.
Use catalog.list().show(), catalog.list('<model>').show(), and then
catalog.list('<model.dataset>').show() to browse object refs.
```

Source-location failure:

```text
Semantic object 'sales.revenue' has no user source location.
Reload the catalog after fixing authoring source-location capture.
```

Unsupported parent:

```text
Semantic object 'sales.revenue' is a metric and cannot be used as a catalog
list parent. Use catalog.get('sales.revenue').details() to inspect dependencies.
```

The catalog should never silently return an empty result because the caller
misspelled a kind. Empty result is valid only after all filters are valid.

## Source Location Requirement

Every loaded object returned by `catalog.get(...)` must point to the user
semantic file that declared it:

```text
.marivo/semantic/sales/_domain.py:42
```

It must not point at `marivo/semantic/authoring.py`. This is a public contract
because agents need source locations to edit semantic definitions safely.

## Relationship to Existing Reader APIs

The target public API does not expose the legacy `SemanticProject` reader
methods to agents, including:

- `SemanticProject`
- legacy domain, datasource, dataset, field, metric, and relationship list
  methods
- `project.get_*`
- `project.describe`
- `project.dependencies`
- `project.dependents`

Implementation may use existing reader internals while building the catalog.
The skill and public help should teach only `ms.load()`, `catalog.list(...)`,
`catalog.get(...)`, `catalog.readiness(...)`, object details, and refs.
`SemanticCatalog` should not grow additional public methods unless a normal
agent workflow cannot be expressed with those primitives.

Advanced internal debugging can keep lower-level readers outside the public
agent contract, but no new public documentation should route agents through
them.

## Minimal Implementation Shape

Suggested modules:

| Module | Responsibility |
| --- | --- |
| `marivo/semantic/catalog.py` | `SemanticCatalog`, `SemanticObject`, `SemanticObjectList`, `SemanticRef`, typed details, navigation logic. |
| `marivo/semantic/__init__.py` | Export `load`, catalog/object/ref types, and authoring functions. |
| `marivo/semantic/reader.py` | Remain as internal loader/registry bridge or be split later. |
| `marivo/semantic/authoring.py` | Keep `description` kwargs and document that business meaning belongs in `ai_context`. |
| `marivo/semantic/help.py` | Teach `ms.load()` and catalog APIs; clarify description vs `ai_context`. |

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
- replace legacy reader list examples with `catalog.list(...)`;
- replace `project.describe(...)` examples with `catalog.get(...).details()`;
- keep `description=` examples but make them short display summaries;
- state that business meaning and guardrails live in `ai_context`;
- show `catalog.readiness(...)` before analysis handoff;
- show `obj.ref` handoff into analysis APIs.

## Testing

Add coverage for:

- `ms.load()` returns `SemanticCatalog` on a ready project.
- `ms.load()` defaults to the current working directory when `workspace_dir` is
  omitted.
- Load failure raises a typed error and does not return a catalog.
- `catalog.list()` returns top-level models and datasources.
- `catalog.list("sales")` returns datasets/entities, metrics, and relationships
  under the model.
- `catalog.list("sales.orders")` returns fields, time fields, relationships
  touching the dataset, plus a filtered metric view for metrics involving the
  dataset.
- `catalog.list("sales", kind="metric")` returns every metric owned by the model.
- `catalog.list("sales.orders", kind="metric")` returns model-owned metrics whose
  `details.entities` include `sales.orders`.
- A cross-dataset metric returned by multiple dataset-filtered lists has the same
  canonical model-scoped `SemanticRef` in every list.
- Dataset-filtered metric browsing does not create dataset-qualified aliases;
  `get` accepts only authored full refs.
- `catalog.list(metric_ref)` raises an unsupported-parent error rather than
  returning component metrics.
- `catalog.list(...)` and `catalog.get(...)` accept full string refs and
  `SemanticRef` values.
- `catalog.list(...)` and `catalog.get(...)` reject short names with actionable
  full-ref guidance.
- `catalog.get(ref)` returns a `SemanticObject` for every kind.
- `SemanticObject.context` matches authored `ai_context`.
- `SemanticObject.description` matches authored `description`.
- Object details include `description` and typed kind-specific fields, not a
  public `properties` mapping.
- Derived metric details include component metric refs and backing entity refs
  in `details.component_metrics`, `details.components`, `details.entities`, and
  `details.parents`.
- Cross-dataset metric details include required relationship refs in
  `details.required_relationships` and `details.parents`.
- Model details include metric refs in `details.children`; dataset details do
  not. Metric details have no hierarchy children; downstream semantic objects
  appear in `details.dependents`.
- `catalog.readiness(refs=...)` delegates the semantic closeout gate and blocks
  unsafe analysis handoff after resolving the semantic dependency closure for
  supplied refs.
- `obj.ref` can be passed to representative analysis APIs.
- Unsupported `kind` values raise supported-value errors.
- Source locations point to user semantic files.
- Catalog objects and lists implement `render()` / `show()` and do not write
  stdout during ordinary result-producing calls.

## Acceptance Criteria

- A new agent can load a project with `catalog = ms.load()` (defaulting to the
  current directory) and browse semantic objects without learning
  `SemanticProject`.
- The agent can progressively disclose the hierarchy with only
  `catalog.list(...)`, using full ref strings or `SemanticRef` values.
- The agent can retrieve any object with `catalog.get(ref)` by passing a full
  ref string or `SemanticRef`.
- The agent can read business context from `obj.context`.
- The agent can read short display summaries from `obj.description`.
- The agent can run `catalog.readiness(refs=...)` before analysis handoff.
- The agent can pass `obj.ref` directly to analysis operations.
- Invalid kind filters fail with explicit supported values.
- Object source locations identify user-authored semantic files.
- Public docs and skill examples use the catalog API only.
