# Catalog Object Navigation Design

## Status

Accepted design. Documentation only; implementation requires a separate plan.

Date: 2026-07-10

This design supersedes
[`2026-07-03-catalog-agent-browsing-design.md`](2026-07-03-catalog-agent-browsing-design.md).

## Context

The current `SemanticCatalog` browse API exposes the registry through
`catalog.list(kind, scope=...)`. That shape makes agents learn an implicit
kind-by-scope support matrix before they can perform ordinary discovery. It
also differs from the datasource result protocol: `SemanticObjectList` exposes
`.objects` while `md.list()` exposes `.items`, and catalog IDs are split between
typed lookup IDs and bare semantic IDs.

These are one design problem rather than isolated signature bugs. The public
surface exposes registry query mechanics instead of the semantic hierarchy an
agent is trying to explore.

## Goals

- Make browsing discoverable through typed attributes and normal Python
  completion.
- Give every catalog collection the same result and consumption protocol.
- Make every public string ID directly reusable with `catalog.get(...)`.
- Expose only the small set of fields agents need on the high-frequency object
  surface.
- Preserve direct handoff of catalog objects or their refs into semantic and
  analysis APIs.
- Remove the old browse model in one breaking change, without compatibility
  aliases.

## Non-goals

- A generic graph query language.
- Arbitrary filtering, predicates, or a replacement `scope=` DSL.
- Mutation of authored semantic objects through the catalog.
- Backward compatibility for `catalog.list(...)`, `SemanticObject`, or
  `SemanticObjectList`.
- Rewriting historical versioned documentation or historical design records.

## Decision

`SemanticCatalog` becomes a typed object graph backed by global and scoped
read-only collections. Agents browse through named collections and exact object
relationships, not through a kind/scope matrix.

```python
import marivo.semantic as ms

catalog = ms.load()

sales = catalog.domains.get("sales")
orders = sales.entities.get("orders")
revenue = orders.metrics.get("revenue")

assert revenue.id == "metric.sales.revenue"
assert catalog.get(revenue.id) == revenue

revenue.details().show()
catalog.preview(revenue).show()
```

## Public types

The catalog exports these object families:

- `CatalogObject`
- `Domain`
- `Datasource`
- `Entity`
- `Dimension`
- `TimeDimension`
- `Measure`
- `Metric`
- `Relationship`
- `CatalogCollection[T]`
- `SemanticCatalog`

`CatalogObject` is the common input type used by APIs that accept any loaded
catalog object. Agents normally interact with concrete subclasses. The base
class is not a generic semantic record and does not expose a public `kind`
field.

Every object exposes the same minimal high-frequency contract:

```python
obj.id          # typed ID, for example "metric.sales.revenue"
obj.name        # local name, for example "revenue"
obj.ref         # typed SemanticRef for authoring and runtime handoff
obj.details()   # kind-specific structured details
obj.render()
obj.show()
```

Objects do not expose top-level `semantic_id`, `kind`, `domain`, `context`,
`source_location`, or `python_symbol` fields. Rich business, dependency, source,
and diagnostic data remains available through the explicit `details()` escape
hatch. Internal IR may continue to use `semantic_id`; it is not a public catalog
concept.

Catalog objects are immutable value views. Equality and hashing use the
concrete object type plus typed ID. Object identity is not part of the contract.

## Catalog entry points

`SemanticCatalog` exposes one global collection per object type:

```python
catalog.domains
catalog.datasources
catalog.entities
catalog.dimensions
catalog.time_dimensions
catalog.measures
catalog.metrics
catalog.relationships
```

Global collections support project-wide discovery. Scoped collections support
hierarchical discovery. `catalog.get(typed_id)` remains the exact lookup entry
point for IDs obtained from errors, logs, persisted state, or another API.

`catalog.get(...)` accepts only a typed ID. It does not accept short names or
bare semantic IDs.

## Navigation matrix

Navigation is limited to explicit ownership or applicability relationships:

| Object | Navigation properties | Meaning |
|---|---|---|
| `Domain` | `entities`, `dimensions`, `time_dimensions`, `measures`, `metrics`, `relationships` | Objects defined or applicable within the domain |
| `Datasource` | `entities` | Entities backed by the datasource |
| `Entity` | `dimensions`, `time_dimensions`, `measures`, `metrics`, `relationships` | Objects directly owned by or applicable to the entity |
| `Relationship` | `from_entity`, `to_entity` | Typed relationship endpoints |
| `Dimension` | none | Leaf object |
| `TimeDimension` | none | Leaf object |
| `Measure` | none | Leaf object |
| `Metric` | none | Leaf object; dependencies remain in `details()` |

`Entity.relationships` includes incoming and outgoing relationships. Direction
is read from `Relationship.from_entity` and `Relationship.to_entity`.

`Datasource` deliberately does not expose `measures`; measures are owned by
entities, so the path is `datasource.entities -> entity.measures`. The public
surface does not expose weakly typed `.parent`, `.children`, `.related`, or
generic dependency traversal.

## Collection protocol

Every global and scoped collection is a `CatalogCollection[T]` with the same
read-only protocol:

```python
collection.items      # tuple[T, ...]
collection.ids()      # list[str], always typed IDs
collection.refs()     # tuple[SemanticRef, ...]
collection.get(key)   # exact typed ID or unique local name
collection.render()   # bounded text, no stdout
collection.show()     # bounded display, returns None

len(collection)
for obj in collection: ...
collection[0]
```

`.items` replaces `SemanticObjectList.objects` and aligns semantic collections
with `md.list().items`. Collection order is ascending typed ID and therefore
stable across loads and module declaration order.

`render()` may truncate display rows to protect agent context, but it must state
the total and omitted counts. `.items`, `.ids()`, and `.refs()` always return the
complete collection.

Collections do not expose `.filter()`, `.where()`, or `scope=`. A named scoped
property is the only browse mechanism.

## Lookup rules

`CatalogCollection.get(key)` accepts:

1. An exact typed ID of the collection's concrete type.
2. A local name that is unique within that collection view.

It rejects bare semantic IDs such as `"sales.revenue"`. A typed ID remains
copy-pasteable across every catalog surface:

```python
revenue = catalog.metrics.get("metric.sales.revenue")
assert catalog.get(revenue.id) == revenue
```

Scoped collections are the normal way to remove ambiguity:

```python
orders = catalog.domains.get("sales").entities.get("orders")
region = orders.dimensions.get("region")
```

If `catalog.domains.get("sales").dimensions.get("region")` matches dimensions
from multiple entities, lookup raises a structured ambiguous-reference error
and lists executable typed-ID calls for the candidates.

## Error contract

Catalog lookup errors follow the shared semantic error model. They state the
expected input, received input, relevant scope, and a concrete next call derived
from the loaded index.

Required cases:

- **Ambiguous short name:** list bounded typed-ID candidates and show
  `collection.get("<typed-id>")`.
- **Wrong object type:** identify the typed ID's real type and point to the
  corresponding global collection.
- **Outside current scope:** state that the object exists globally, identify its
  owning path, and show the valid scoped or global lookup.
- **Not found:** show bounded close matches from the current collection.
- **Bare semantic ID:** reject it; when a unique typed ID can be derived, show
  the exact corrected call, otherwise show bounded candidates.
- **Empty collection:** return an empty collection, not an error; render
  `count=0`.

Errors do not suggest `catalog.list(...)` or expose a kind/scope matrix.

## Internal architecture

The public object graph is backed by one private index:

```text
Registry / IR
    -> _CatalogIndex
        -> typed CatalogObject views
        -> global and scoped CatalogCollection views
```

`_CatalogIndex` owns typed-ID lookup, local-name indexes, ownership edges,
applicability edges, relationship endpoints, and deterministic ordering.
`CatalogCollection` stores an index reference, concrete object type, and scope;
it does not independently scan Registry structures.

Typed objects delegate navigation and `details()` resolution to the index. This
keeps filtering logic out of DTOs and makes every browse path obey the same
lookup and error rules.

Execution and planning code that needs IR continues to use the private registry
bridge. It must not query `CatalogCollection` and reconstruct execution facts
from the public agent-facing views.

## Runtime handoff

Semantic and analysis APIs that currently accept `SemanticObject` switch to
`CatalogObject`. They continue to accept the appropriate `SemanticRef`. Input
normalization reads `CatalogObject.ref`; consumers do not enumerate the eight
concrete classes.

This applies to preview, verification, readiness, resolver, observe, transform,
derive, and escape-hatch bindings where the current contract accepts a loaded
object. Passing a concrete `Metric`, `Dimension`, or other valid catalog object
remains the most direct agent path.

## Breaking removal

The implementation removes:

- `SemanticCatalog.list()`
- `SemanticObject`
- `SemanticObjectList`
- `SemanticObjectList.objects`
- bare semantic IDs returned by catalog collection `.ids()`
- public catalog browsing parameters named `kind` or `scope`
- help, skill, and latest-documentation guidance for the old API

There are no deprecated aliases, compatibility warnings, or hidden acceptance
of old call shapes.

## Documentation migration

The implementation updates all active surfaces in the same change:

- canonical design contracts under `docs/specs/`
- `marivo-semantic` and `marivo-analysis` skills and runnable examples
- semantic and analysis help text and error templates
- latest English and Chinese site documentation
- generated API documentation
- public-surface, help, drift, example, and introspection tests

Historical versioned site documentation remains unchanged. Historical design
documents remain intact except that the superseded catalog browsing design is
marked as replaced by this document.

The canonical `docs/specs` files synchronized with this decision are:

- `docs/specs/semantic/loading-validation-introspection.md`
- `docs/specs/semantic/authoring-workflow.md`
- `docs/specs/agent-friendly-public-surface.md`
- `docs/specs/analysis/operators-and-frames.md`
- `docs/specs/analysis/session-state-and-runtime.md`
- `docs/specs/analysis/python-analysis-design.md`

## Test and acceptance criteria

Contract tests must prove:

- all eight collections return their concrete object types;
- all global and scoped collections implement the common collection protocol;
- all public IDs are typed and no public object exposes `semantic_id`;
- the navigation matrix is exact, including incoming and outgoing entity
  relationships;
- unique, ambiguous, wrong-type, outside-scope, missing, and bare-ID lookups
  follow the teaching error contract;
- direct `CatalogObject` handoff works anywhere `SemanticObject` previously
  worked;
- planners preserve current IR behavior without depending on public collection
  views;
- `catalog.list`, `SemanticObject`, `SemanticObjectList`, and `.objects` are
  absent from public exports, help, skills, and latest docs;
- documentation examples and generated API indexes match the runtime surface.

Implementation verification runs focused catalog and handoff tests first, then:

```bash
make test
make typecheck
make lint
```

The site content verification required by the repository also runs after latest
documentation and generated API updates.
