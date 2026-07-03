# Catalog Agent Browsing Design

## Status

Accepted design. Documentation only for now; implementation is deferred.

Date: 2026-07-03

## Context

`SemanticCatalog` browsing is the highest-frequency entry action for agents,
yet a field report showed an agent needing five failed attempts to list the
metrics in a project:

1. `cat.metrics` — `AttributeError`; no collection attributes exist.
2. `cat.list("metric")` — the positional string is parsed as a scope ref and
   rejected with `invalid_ref`, demanding `'<kind>.<semantic_id>'`.
3. `cat.list(kind="metric")` — rejected with `unsupported_kind`;
   `_validate_kind` (`marivo/semantic/catalog.py`) requires a `SemanticKind`
   enum instance even though the enum is a `StrEnum` and string coercion is
   free.
4. `import marivo.semantic.enums` — `ModuleNotFoundError`; the error hint
   named `SemanticKind` but gave no importable path (it is exported as
   `ms.SemanticKind`, which the agent could not discover from the error).
5. Full `cat.list()` plus manual filtering — failed again because
   `SemanticObject` exposes `.ref.id` but no flat `.id`.

Root cause: listing by kind is the primary browsing intent, but the current
signature `list(scope=None, *, kind=None)` centers the hierarchy-browsing
scope, treats kind as a strictly-typed optional filter, and its errors do not
teach a working call.

The current implementation detail that makes the redesign cheap: scoped kind
filtering is already subtree-transitive (for example
`list("domain.sales", kind=SemanticKind.DIMENSION)` enumerates every dimension
in the domain, including ones that never appear in the mixed view), so the
query layer already matches kind-first semantics.

## Decision

Rebuild `SemanticCatalog.list` around kind-first enumeration as a breaking
change, done clean in one pass with no compatibility shims.

### New signature

```python
def list(
    self,
    kind: SemanticKind | str,          # required, positional, accepts strings
    *,
    scope: str | None = None,          # optional '<kind>.<semantic_id>' typed id
) -> SemanticObjectList
```

```python
catalog.list("metric")                           # all metrics
catalog.list("domain")                           # all domains (replaces top-level view)
catalog.list("dimension", scope="domain.sales")  # all dimensions in the sales subtree
catalog.list("metric", scope="entity.sales.orders")
```

Semantics: enumerate every object of `kind` within the `scope` subtree;
omitting `scope` means the whole project.

- `kind` accepts both the string form (`"metric"`) and the enum
  (`ms.SemanticKind.METRIC`). Strings coerce through `SymbolKind(value)`;
  the string form becomes the documented default spelling.
- `catalog.list()` with no arguments raises Python's native
  `TypeError: missing ... 'kind'`. The signature stays honest (no sentinel
  default); docstring and `describe` carry runnable examples.
- Mixed browsing (listing a scope without a kind) is removed entirely.
  Hierarchy navigation is served by the existing
  `catalog.get("domain.sales").children`.

### kind x scope support matrix

Structurally unsupported combinations raise a teaching error rather than
returning an empty list, so an agent never misreads "unsupported" as
"none exist". Supported combinations that are genuinely empty return an
empty list rendered as today.

| scope        | supported kinds                                              |
|--------------|--------------------------------------------------------------|
| (none)       | all 8 kinds                                                  |
| `domain.*`   | entity, metric, measure, dimension, time_dimension, relationship |
| `datasource.*` | entity, measure                                            |
| `entity.*`   | dimension, time_dimension, measure, relationship, metric     |

The teaching error for an unsupported combination lists the kinds that are
actually listable under that scope type.

### Error surface: every failure teaches a working call

- **kind receives a dotted string** (old habit `list("domain.sales")`):
  recognized as scope-shaped; the error suggests
  `catalog.list("entity", scope="domain.sales")` and
  `catalog.get("domain.sales")` as copyable alternatives.
- **Invalid kind string** (for example `"metrics"`): the error lists all 8
  valid kind strings and adds an edit-distance suggestion such as
  `Did you mean catalog.list("metric")?` built from the real kind set.
- **Wrong kind type**: the error shows both copyable spellings —
  `kind="metric"` and `ms.SemanticKind.METRIC` including the
  `import marivo.semantic as ms` statement.
- **Attribute guesses** (`cat.metrics`, `cat.dimensions`, ...):
  `SemanticCatalog.__getattr__` intercepts the plural form of each of the 8
  kinds and raises an `AttributeError` whose message points to
  `catalog.list("metric")`. Other attribute names raise a plain
  `AttributeError`. This adds no public path: it is not in `__all__` and not
  in the help index.

### SemanticObject.id

Add an `id` property returning `self.ref.id`, with the standard public-API
docstring. Together with the existing `.kind`, `.name`, and `.domain` this
gives scripts a flat field set without requiring knowledge of the two-level
`.ref.id` structure. `SemanticObjectList.ids()` already exists and is
unchanged.

## Breaking migration (single change set)

- All internal call sites in `marivo/` and every test in `tests/`.
- `marivo/skills/marivo-semantic/SKILL.md` and the `marivo-analysis`
  cheatsheet, pitfalls, and examples. Note: the analysis skill examples are
  executed in-process by `test_semantic_agent_tightening`, so they must be
  updated in the same change or the commit gate breaks.
- `site/` English and Chinese editions: quick-start and semantic-layer pages.
- `docs/specs/semantic/python-semantic-layer.md` catalog table row.
- Top-level view migration: `list()` becomes `list("domain")` plus
  `list("datasource")`; mixed scope browsing becomes `get(...).children`.
- `ms.help` / `describe` entries for `catalog.list` and `SemanticObject`.

## Testing

- `tests/test_semantic_catalog.py`: kind is required; string coercion; enum
  pass-through; every supported matrix combination; the four teaching-error
  shapes; the `SemanticObject.id` property.
- `tests/test_public_surface.py` snapshot: no new public symbols (`.id` is a
  property; `__getattr__` is not public surface).
- Finish with full `make test` and `make typecheck`.

## Out of scope

- No convenience collection attributes (`cat.metrics` as a real property),
  no `catalog.overview()`, no special `"all"` kind value.
- `catalog.get` and its typed-ref contract are unchanged.
- String kind acceptance is not extended to kind-typed parameters outside
  catalog browsing.

## Alternatives considered

- **Shape-dispatched single positional** (`list("metric")` vs
  `list("domain.sales")` disambiguated by the presence of a dot): rejected —
  one parameter with two meanings raises long-term cognitive cost, and the
  kind-first requirement makes the dispatch unnecessary.
- **Convenience attributes** (`cat.metrics`, `cat.dimensions`, ...):
  rejected — duplicates the `list` capability against the
  one-path-per-capability rule and grows `__all__`, help, and snapshot
  surface by 8 entries.
- **Error-message-only fix** (keep the enum requirement, improve hints):
  rejected — still costs agents an extra round trip on the most frequent
  browsing action.
- **`kind="all"` mixed value or `catalog.overview()`**: rejected — "all" is
  not a real kind, and hierarchy browsing is already served by
  `get(...).children`.
