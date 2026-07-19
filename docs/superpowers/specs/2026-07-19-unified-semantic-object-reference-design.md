# Marivo Unified Semantic Object and Reference Design

Status: proposed; written-review revisions integrated; implementation blocked
until the Tier-2 Semantic Expression Reference Binding Design is accepted

Date: 2026-07-19

Revision policy: destructive cutover to the smallest coherent final contract.
Current public names, signatures, aliases, serialized identities, loader wiring,
and persisted analysis payloads have no compatibility weight. There is no
migration path, compatibility adapter, dual reader, deprecated alias, or legacy
payload interpretation.

This design supersedes the reference-input and runtime-handoff portions of:

- [`2026-07-10-catalog-object-navigation-design.md`](2026-07-10-catalog-object-navigation-design.md);
- [`2026-07-17-typed-metric-composition-design.md`](2026-07-17-typed-metric-composition-design.md).

Their catalog-navigation and runtime-metric-expression decisions remain active
unless this document explicitly changes them. In particular, typed object-graph
browsing and recursive runtime metric expressions remain selected. This design
replaces the exact-ref-subclass hierarchy, `CatalogObject` handoff, broad input
aliases, string identity format, and replay normalization beneath those
features.

## Summary

Marivo has one semantic identity value and one loaded catalog view:

```text
Ref[K]                 stable cross-layer identity
CatalogEntry[K]        one object in one immutable compiled catalog
```

All authoring helpers that define an independently addressable semantic object
return a pure immutable `Ref[K]`; nested value builders such as join keys and
provenance values remain ordinary closed values. All semantic, analysis,
planning, materialization, persistence, and replay identity boundaries consume
that same ref value. The runtime has one `Ref` class tagged by a closed
`SemanticKind`; kind-specific typing is expressed through `Ref[K]`, not through
runtime subclasses with different behavior.

`SemanticCatalog` is the immutable compiled form of the currently selected
local and external semantic layers. It is not another identity family. It owns
closed-world linking, validation, inferred graph facts, hierarchical browsing,
object details, readiness state, and membership checks. Its concrete
`CatalogEntry[K]` values are read and navigation results. They never enter
semantic or analysis operators; callers cross that boundary explicitly with
`.ref`.

Authoring Python files remain the only durable semantic source of truth, but
they are loader-executed declaration sources rather than importable analysis
libraries. Analysis scripts do not traverse or import authoring modules. They
obtain refs from a compiled catalog or construct a known identity with an exact
kind factory such as `Ref.metric(...)` or `Ref.dimension(...)`, after which the
active catalog must resolve the ref.

The complete public mental model is:

```text
author in Python -> compile with ms.load() -> browse CatalogEntry -> pass Ref
```

## Selected Decisions

- Runtime semantic identity is one frozen `Ref[K]` value class.
- `SemanticKind` is the single closed kind registry across datasource,
  semantic, catalog, analysis, persistence, and replay layers.
- The current `SymbolKind` name and `SemanticKind = SymbolKind` alias are both
  removed. The enum is defined and exported once as `SemanticKind`.
- Kind-specific runtime ref subclasses are removed.
- Known refs are constructed only through exact factories on that one class:
  `Ref.metric(path)`, `Ref.dimension(path)`, and the corresponding factory for
  every addressable kind.
- Raw `Ref(...)` construction is mechanically unavailable. `Ref` uses a sealed
  private construction path, cannot be subclassed, and every public value
  originates from an exact kind factory, the validated persisted-payload
  decoder, or standard copy/pickle reconstruction that preserves or invokes
  those paths.
- `SemanticRef`, `MetricRef`, `DimensionRef`, `TimeDimensionRef`, `MeasureRef`,
  `EntityRef`, `DomainRef`, `RelationshipRef`, and `DatasourceRef` are not
  public runtime classes.
- Static type annotations use `Ref[MetricKind]`, `Ref[DimensionKind]`, and the
  corresponding exact generic form directly. There are no kind-specific ref
  aliases hidden behind different names.
- A ref has one canonical identity: `kind:path`, for example
  `metric:sales.revenue`.
- `.id` is removed from refs and catalog entries. `.key`, `.path`, `.kind`, and
  `.name` have non-overlapping meanings.
- Refs are pure values. They are never callable, catalog-bound, resolver-bound,
  readiness-bearing, mutable, or late-wired by the loader.
- Authoring Python files are executed only by the semantic compiler/loader.
  Direct analysis imports of authoring modules are unsupported.
- `ms.load()` returns a new immutable `SemanticCatalog`. Catalog
  mutation and in-place reload are removed.
- A catalog entry is always bound to exactly one compiled catalog and contains
  one pure `.ref`.
- Catalog collections and entries remain the only browse/navigation surface.
- `catalog.require(ref)` is the only membership probe. There is deliberately no
  non-raising `catalog.contains(ref)` and no `ref in catalog` protocol.
- Global catalog resolution accepts only `Ref[K]`; known refs are constructed
  by exact kind factories, and local names are accepted only inside an already
  typed, scoped catalog collection.
- Semantic and analysis APIs accept exact `Ref[K]` kinds. They reject
  `CatalogEntry`, strings, generic mappings, callables, and wrong-kind refs.
- `SemanticInput`, `MetricInput`, `DimensionInput`, `SemanticAnchorInput`, and
  equivalent convenience mega-unions are deleted.
- Internal resolver and materializer APIs consume refs only. They do not accept
  catalog entries or strings.
- Persisted semantic identity is one structured payload with `kind` and `path`.
  Replay reconstructs a `Ref[K]` and resolves it against the active catalog; it
  never fabricates a missing ref and continues.
- Whole-catalog definition fingerprints are lineage provenance. Replay
  compatibility is gated by an exact digest of the semantic dependency closure
  used by the recorded operation, so unrelated catalog changes do not block it.
- Readiness remains the required semantic-to-analysis workflow gate. A source
  declaration or resolvable ref alone is insufficient evidence that the
  dependency closure is ready, but `Ref` carries no readiness brand and
  analysis does not automatically run readiness.
- There is no generated refs module, ref-export lifecycle, or second
  import-oriented semantic manifest.
- Removal of callable field refs cannot begin until a companion Tier-2
  expression-binding design is accepted. That prerequisite preserves the
  committed `@ms.metric(entities=[...])` positional-alias contract and selects
  the explicit replacement for applying a field ref to an entity alias.
- The unified Ref foundation and structured payload land before the in-flight
  metric-expression graph persistence. No bare-string identity schema is
  released or stabilized as metric-expression v1.

## Problem

### One identity is currently represented several ways

The current surface exposes all of the following as plausible references to one
semantic object:

```text
MetricRef / DimensionRef / other authoring ref subclasses
SemanticRef
CatalogObject.ref
CatalogObject itself
"metric.sales.revenue"
"sales.revenue"
SemanticInput / MetricInput / DimensionInput unions
```

Different layers accept different subsets and normalize them independently.
The result is not ergonomic polymorphism. It is a distributed parsing protocol
whose behavior an agent must rediscover at every call.

The current catalog makes the ambiguity visible: a metric catalog object may
have `id == "metric.sales.revenue"` while its ref has
`id == "sales.revenue"`. Analysis help may pass a metric catalog object directly
but use `.ref` for dimensions. Resolver code may accept either representation or
a string. Persisted payloads then erase the kind and reconstruct it later.

The current dimension normalizer also performs a silent unique-suffix repair:
a bare local name may be matched against every available dimension and accepted
when exactly one suffix matches. The Pydantic ref hook accepts strings and
serializes refs as bare IDs, erasing kind. The kind registry itself is exposed
under both `SymbolKind` and `SemanticKind`. These are all consequences of the
same missing canonical identity boundary and are deleted by this cutover.

### Ref objects currently mix identity and execution

Field-kind refs can carry a late-bound resolver and be called inside expression
bodies. That means the behavior of a value described as immutable identity
depends on whether a loader has attached runtime state to that particular
instance.

Identity, expression resolution, project loading, and materialization are four
different responsibilities. Combining them creates state-dependent refs,
loader-specific object identity, hidden mutation, and replay values that cannot
faithfully reproduce the original object behavior.

### Authoring source is not a safe analysis import surface

Semantic objects are declared in Python, but those files are executed under a
loader context that collects pending IR, applies domain defaults, resolves
cross-file and cross-layer dependencies, and performs a second assembly pass.
Their Python variable names are not semantic identities. Aliases and imported
symbols are legal Python but do not create new semantic objects.

The effective project may also include external `layer_paths`, filtered domains,
and separately authored datasources. Traversing one local directory cannot
reconstruct the semantic world attached to an analysis session.

Directly importing authoring modules from an analysis script would therefore
either fail outside loader context, repeat declaration side effects, or produce
unlinked refs that do not prove membership, validation, readiness, or execution
authority in the active session.

### Source declarations do not contain the compiled semantic graph

Several facts required by analysis exist only after whole-project assembly:

- resolved dependency and dependent edges;
- derived metric component closure;
- effective entities and join requirements;
- candidate dimensions and time dimensions;
- measure lineage;
- resolved additivity and unit algebra;
- cumulative time-axis resolution;
- source and datasource compatibility;
- graph budget and operator admission;
- parity, preview, and readiness state.

A Python variable proves only that declaration code produced a local ref during
one execution. It does not prove that the complete project loaded or that the
object is analysis-ready.

## Goals

- Give agents one invariant answer to “what value do I pass for a semantic
  object?”
- Make malformed, wrong-kind, non-member, and mechanically unplannable inputs
  fail at the earliest public boundary.
- Preserve discoverable hierarchical catalog browsing without making catalog
  entries another operator-input family.
- Make authoring, catalog, analysis, persistence, and replay share one exact
  identity and serialization contract.
- Keep refs immutable, context-free, hash-stable, and safe to use as mapping
  keys.
- Preserve Python as the explicit durable semantic authoring format.
- Give the catalog a precise compiler-output role rather than treating it as a
  mirror of source variables.
- Support generated analysis scripts through explicit `Ref.<kind>(...)` values
  without importing authoring modules or generating another refs file.
- Make live help, structured errors, type annotations, and examples teach the
  same single path.
- Cover datasource refs and future semantic kinds without adding another ref
  family.

## Non-goals

- Replacing Python semantic authoring with YAML, JSON, prompts, or generated IR.
- Generating a Python refs module, `.pyi` ref manifest, or other import-oriented
  mirror of catalog identity.
- Treating catalog state as the durable source of business meaning.
- Making Python variable names or file paths semantic identities.
- Making arbitrary authoring modules safe for normal application import.
- Automatically authoring or approving missing semantic objects.
- Creating a generic graph query language.
- Adding implicit catalog lookup to `Ref` methods.
- Encoding catalog or project fingerprints into stable ref identity.
- Allowing runtime metric expressions to become durable semantic refs.
- Preserving current IDs, ref subclasses, aliases, serialized payloads, or
  replay fallbacks.

## Terminology

### Semantic definition

An immutable internal definition/IR record emitted while the loader executes
authoring Python. It describes business meaning and dependencies. It is not a
public runtime input.

### Ref

A pure, stable, context-free identity value consisting of one semantic kind and
one semantic path. It does not prove catalog membership, validity, readiness, or
executability.

### Compiled catalog

One immutable successfully compiled semantic world produced by `ms.load()` from
the selected workspace, datasource definitions, authored semantic roots,
external layer paths, and optional domain filter.

### Catalog entry

A read-only, catalog-bound view of one compiled semantic definition plus
derived catalog facts and navigation. It contains a ref but is not itself a ref.

### Analysis readiness

A catalog- and evidence-scoped result stating that a ref's dependency closure
has no readiness blocker. This is workflow state, not a distinct ref type,
token, brand, constructor provenance, or fact recoverable from the ref value.

## Architecture

```text
Durable Python authoring sources
    local models/ + external layer_paths
                |
                | loader execution in explicit context
                v
Pending typed definitions and source sidecars
                |
                | assembly / linking / inference / validation
                v
Immutable compiled SemanticCatalog
    - definition_fingerprint
    - private compiled registry
    - dependency graph
    - catalog entries and collections
    - verification / preview / readiness views
        |
        | CatalogEntry[K].ref or explicit Ref.<kind>(path)
        v
      Ref[K]
                        |
        +---------------+-------------------+
        |               |                   |
        v               v                   v
 semantic lifecycle  analysis intents   persisted lineage/jobs
        |               |                   |
        +---------------+-------------------+
                        |
                        v
                  resolver/materializer
```

The durable source and compiled catalog are deliberately distinct. The source
owns explicit business declarations. The catalog owns the result of compiling
the complete selected source graph. Ref is the only value that crosses between
the compiled world and semantic/analysis execution.

## Unified Ref Model

### Runtime shape

The runtime has one ref class:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, Never, TypeVar, cast, final

KindT = TypeVar("KindT", bound=SemanticKindTag, covariant=True)


@final
@dataclass(frozen=True, slots=True, init=False)
class Ref(Generic[KindT]):
    kind: SemanticKind
    path: str

    def __new__(cls, *args: object, **kwargs: object) -> Never:
        raise TypeError(
            "Ref has no public raw constructor; use Ref.metric(...), "
            "Ref.dimension(...), or another exact kind factory."
        )

    def __init_subclass__(cls, **kwargs: object) -> Never:
        raise TypeError("Ref is sealed and cannot be subclassed.")

    @staticmethod
    def _create(kind: SemanticKind, path: str) -> Ref[SemanticKindTag]:
        validated = _validate_ref_path(kind, path)
        value = object.__new__(Ref)
        object.__setattr__(value, "kind", kind)
        object.__setattr__(value, "path", validated)
        return cast("Ref[SemanticKindTag]", value)

    @staticmethod
    def metric(path: str) -> Ref[MetricKind]:
        return cast(
            "Ref[MetricKind]",
            Ref._create(SemanticKind.METRIC, path),
        )

    def __copy__(self) -> Ref[KindT]:
        return self

    def __deepcopy__(self, memo: dict[int, object]) -> Ref[KindT]:
        memo[id(self)] = self
        return self

    def __reduce_ex__(self, protocol: int) -> tuple[object, tuple[object, ...]]:
        del protocol
        return (
            _restore_ref_payload,
            (RefPayloadV1.from_ref(self),),
        )

    @property
    def key(self) -> str:
        return f"{self.kind.value}:{self.path}"

    @property
    def name(self) -> str:
        return self.path.rsplit(".", 1)[-1]

    def __str__(self) -> str:
        return self.key


def _restore_ref_payload(payload: RefPayloadV1) -> Ref[SemanticKindTag]:
    return _decode_ref_payload(payload)
```

`SemanticKindTag` and its kind-specific marker types are typing machinery, not
runtime value families and not public help concepts. The exact implementation
may use protocols or phantom marker classes, but these constraints are fixed:

- every runtime ref has exact concrete class `Ref`; subclass creation is
  rejected and public boundaries defensively require `type(value) is Ref`;
- kind is a closed runtime tag, not inferred from Python subclass;
- equality and hashing use exactly `(kind, path)`;
- no ref contains a catalog, registry, resolver, callable, source location,
  readiness flag, or mutable field;
- public errors and help call every instance a `Ref[kind]`.

`KindT` is intentionally phantom runtime state: the closed `kind` field carries
the value, while the generic parameter carries static precision. A normal
dataclass initializer would allow `Ref(kind=..., path=...)` to infer
`Ref[Never]`; covariance would then make that value assignable to every
`Ref[K]`, bypassing factory typing and path validation. `init=False`, the sealed
`__new__`, the forbidden `__init_subclass__`, exact-class allocation, and
exact-class boundary checks remove those construction sites. Runtime tests must
prove that `Ref()`, `Ref(kind=..., path=...)`, every positional raw form, and
`class X(Ref): ...` fail before creating a value. A typing fixture checked by
the repository's static type checker must separately prove that no expression
can construct or widen a `Ref[Never]` into an exact `Ref[K]`; runtime tests
alone cannot establish that static property.

All exact factories and the private persisted-payload decoder call the same
validated `_create` primitive. `copy.copy(ref)` and `copy.deepcopy(ref)` return
the same immutable value. Pickle reconstruction invokes the private
module-level restore function, which delegates to that validated decoder; it
never invokes the sealed public constructor or assigns unchecked fields. The
private methods and restore function are not exported, documented, registered
in help, or treated as supported caller entrances. Pickle is supported for
ordinary Python object transport, but it is not Marivo's durable persistence
format and must never replace `RefPayloadV1` in jobs, lineage, or artifacts.

`dataclasses.replace(ref, ...)` is intentionally unsupported. Semantic identity
cannot be edited field-by-field; callers construct another exact identity with
the corresponding `Ref.<kind>(...)` factory.

Implementation annotations use the exact generic form directly. They do not
introduce `MetricRefT`, `DimensionRefT`, or other aliases that would recreate a
parallel type vocabulary in internal signatures and error handling.

### Closed kind registry

`SemanticKind` is owned below datasource, semantic, and analysis modules and has
one member per addressable compiled object. The initial closed set is:

```text
domain
datasource
entity
dimension
time_dimension
measure
metric
relationship
```

Any accepted future top-level kind, such as `event` or `state_model`, joins this
same registry and uses the same ref implementation. Nested authoring values that
are not independently addressable do not receive refs.

### Canonical identity

The canonical textual identity is:

```text
<kind>:<semantic-path>
```

Examples:

```text
domain:sales
datasource:warehouse
entity:sales.orders
dimension:sales.orders.country
time_dimension:sales.orders.created_at
measure:sales.orders.amount
metric:sales.revenue
relationship:sales.order_customer
```

The colon separates the kind namespace from the domain-qualified semantic path.
Dots belong only to the semantic path. Parsing never guesses where kind ends.

Fields have one meaning:

```python
ref.kind   # SemanticKind.METRIC
ref.path   # "sales.revenue"
ref.key    # "metric:sales.revenue"
ref.name   # "revenue"
```

`.id` and `.semantic_id` are not public ref fields. Catalog entries expose the
same `.key`, `.path`, and `.name` meanings by delegation to `.ref`; they do not
invent a second typed ID.

### Path grammar

The path grammar is normative and shared by authoring-name validation, exact
factories, private persisted-payload decoding, catalog indexing, CLI decoding,
and error rendering.

Each segment matches this ASCII lowercase snake-case grammar:

```text
segment := [a-z][a-z0-9_]*
path    := segment ("." segment)*
```

Factories reject rather than normalize leading/trailing whitespace, uppercase
letters, Unicode identifiers, hyphens, empty segments, leading digits, colons,
or extra/missing segments. Identity is case-sensitive, and no factory silently
lowercases input. Domains are one segment and cannot nest.

The initial kind-to-path grammar is closed:

| Kind | Path shape | Segment count | Example |
| --- | --- | ---: | --- |
| `domain` | `<domain>` | 1 | `sales` |
| `datasource` | `<datasource>` | 1 | `warehouse` |
| `entity` | `<domain>.<entity>` | 2 | `sales.orders` |
| `dimension` | `<domain>.<entity>.<dimension>` | 3 | `sales.orders.country` |
| `time_dimension` | `<domain>.<entity>.<time_dimension>` | 3 | `sales.orders.created_at` |
| `measure` | `<domain>.<entity>.<measure>` | 3 | `sales.orders.amount` |
| `metric` | `<domain>.<metric>` | 2 | `sales.revenue` |
| `relationship` | `<domain>.<relationship>` | 2 | `sales.order_customer` |

Authoring names use the same segment validator before definitions enter pending
IR. This is a deliberate breaking tightening over two current contracts:
several semantic ref kinds accept arbitrary non-empty strings, while datasource
authoring separately accepts `^[A-Za-z0-9_-]+$`. The central segment validator
replaces that datasource-specific rule as well. Existing datasource names such
as `prod-mysql`, `Warehouse`, and `1warehouse` become invalid; the error teaches
an explicit valid rename such as `prod_mysql`, `warehouse`, or
`warehouse_1`. No layer silently normalizes a legacy name.

A datasource rename changes its semantic ref, catalog identity, compiled
fingerprints, and any source, job, evidence, or policy payload that names it.
The local plaintext credential cache is keyed by environment-variable name,
not directly by datasource ref. Explicit `*_env` bindings therefore remain
unchanged. A conventional binding reuses the cached value only when
`conventional_env_var(old_name) == conventional_env_var(new_name)`; common
hyphen-to-underscore and uppercase-to-lowercase repairs satisfy that equality.
If the normalized environment-variable name changes, the old cache entry is
left untouched but is no longer selected, so the user must export/cache the new
variable and validate the renamed datasource. The cutover performs no secret
cache migration or deletion.

A future independently addressable kind must add one explicit grammar row to
the central kind registry and update authoring, factory, serializer, catalog,
help, and tests atomically. It cannot inherit an unspecified generic path rule.

### Construction

Authoring helpers construct refs internally and return them with precise static
kind typing:

```python
sales = ms.domain(...)                  # Ref[domain]
orders = ms.entity(domain=sales, ...)  # Ref[entity]
amount = ms.measure(entity=orders, ...) # Ref[measure]
revenue = ms.aggregate(measure=amount, ...)  # Ref[metric]
```

Known refs are constructed through closed kind factories on the same runtime
class:

```python
revenue = ms.Ref.metric("sales.revenue")
country = ms.Ref.dimension("sales.orders.country")
created_at = ms.Ref.time_dimension("sales.orders.created_at")
```

The complete factory set mirrors `SemanticKind`:

```text
Ref.domain(path)          -> Ref[domain]
Ref.datasource(path)      -> Ref[datasource]
Ref.entity(path)          -> Ref[entity]
Ref.dimension(path)       -> Ref[dimension]
Ref.time_dimension(path)  -> Ref[time_dimension]
Ref.measure(path)         -> Ref[measure]
Ref.metric(path)          -> Ref[metric]
Ref.relationship(path)    -> Ref[relationship]
```

Each factory validates the exact path shape for its kind and returns a precisely
typed `Ref[K]`. It proves syntax only. It does not load a project, resolve
existence, infer readiness, or inspect source files.

There is no public generic `ms.ref(...)`, `Ref.parse(...)`, or raw constructor
that accepts user-supplied `kind=` plus `path=`. This removes dynamic kind
ambiguity from ordinary Python and gives static type checkers the exact kind at
the factory call site.

Wrong textual shapes are rejected by the selected factory:

```python
ms.Ref.metric("metric:sales.revenue")  # invalid_ref: pass semantic path only
ms.Ref.metric("revenue")               # invalid_ref: missing domain path
ms.Ref.dimension("sales.revenue")       # invalid_ref: missing entity path
```

`ms.Ref` is the only public Python owner and help target. `marivo.datasource`
returns `Ref[datasource]` values from datasource authoring but does not re-export
`Ref` as `md.Ref`; callers that need an explicit datasource identity use
`ms.Ref.datasource(...)`. The lower core module that implements `Ref` is private.

The canonical `kind:path` key remains the complete human-facing rendered and
CLI identity. CLI and persistence adapters use one private canonical decoder;
they do not expose a second Python construction API.

### Serialization

Canonical persisted identity is structured and versioned:

```json
{
  "schema": "marivo.semantic_ref/v1",
  "kind": "metric",
  "path": "sales.revenue"
}
```

The internal immutable DTO is `RefPayloadV1(schema, kind, path)`. It is the
only semantic-identity payload embedded by jobs, frames, metric graphs, evidence,
policies, and replay records. It is not a second runtime identity and is never
accepted as a Python argument by public semantic or analysis operations;
JSON/Pydantic persistence adapters accept its wire object and decode a
validated `Ref[K]` before the operation boundary.

The canonical string key may be used in bounded human-facing rendering, map
keys, CLI arguments, and logs. Durable JSON does not persist only the string or
only the path.

Deserialization validates schema, kind, and path and produces one `Ref`. Catalog
membership is a separate required step owned by the consumer's active catalog.

### Pydantic boundary

`Ref[K]` has one central Pydantic core-schema adapter (or an equivalent central
integration) so public models cannot accidentally fall back to dataclass field
construction. Its contract is mode-specific:

- Python validation accepts only an already constructed value whose concrete
  type is exactly `Ref` and whose runtime `kind` is permitted by `K`. It rejects
  strings, mappings, `CatalogEntry`, subclasses, and wrong-kind refs.
- JSON validation accepts only the complete `RefPayloadV1` object, validates
  its schema, closed kind, path grammar, and generic kind constraint, then calls
  the private persisted-payload decoder. It never calls `Ref.__new__`.
- `model_dump(mode="python")` preserves the immutable `Ref` value.
  `model_dump(mode="json")` and `model_dump_json()` emit the complete structured
  `RefPayloadV1`, never a bare path or canonical string key.
- JSON schema for `Ref[MetricKind]` fixes `kind` to `"metric"`; a declared
  closed kind union emits only that union's enum values. Public model fields
  may not use an unparameterized `Ref`.

The implementation maintains one runtime registry from the static kind marker
to the allowed `SemanticKind` values. Pydantic DTO tests cover exact-kind and
closed-union fields in both Python and JSON modes, structured JSON schema,
round-trip decoding, and rejection of every implicit input shape. Public API
error adapters may wrap validation failures in their domain-specific error
types, but cannot widen the accepted value contract.

## Authoring Contract

### Python remains the durable source

Explicit project Python remains the only durable source of semantic business
definitions. The loader executes it in a controlled declaration context and
collects typed definitions plus expression sidecars. Catalog state, manually
constructed refs, prompts, session scripts, and analysis artifacts cannot
author or modify semantic meaning.

### Authoring variables hold refs, not definitions

Every addressable authoring helper registers one internal definition and returns
its ref:

```python
sales = ms.domain(name="sales", owner="finance")
orders = ms.entity(name="orders", domain=sales, ...)
country = ms.dimension_column(name="country", entity=orders, column="country")
amount = ms.measure_column(name="amount", entity=orders, column="amount", ...)
revenue = ms.aggregate(name="revenue", measure=amount, agg="sum")
```

The Python names `sales`, `orders`, `country`, `amount`, and `revenue` are local
source conveniences. Renaming a variable without changing its constructor
arguments does not change semantic identity. Aliasing a variable does not create
another definition.

Object-to-object parameters accept exact refs only. No authoring parameter
accepts catalog entries, bare strings, arbitrary objects with `.ref`, callables
standing in for refs, or broad semantic unions.

### Authoring modules are not analysis libraries

Analysis code must not walk `models/semantic`, inspect Python ASTs, import
authoring modules, or depend on their variables. Authoring modules may rely on:

- loader-only context;
- synthetic package names;
- deterministic multi-file execution order;
- default-domain state;
- external semantic roots;
- second-pass linking and validation;
- loader-owned source and expression sidecars.

Normal import cannot reproduce that environment and would make analysis depend
on source organization rather than semantic identity.

The loader is the sole executor of authoring modules. A tool that needs current
semantic objects calls `ms.load()` and consumes the resulting compiled catalog.

### Refs are not expression resolvers

All callable and late-bound ref behavior is removed. Field expression
resolution belongs to an explicit loader-owned expression binding, not to
`Ref.__call__`.

This document deliberately does not invent the replacement spelling. The
required companion specification is **Tier-2 Semantic Expression Reference
Binding Design**. It must be written, accepted, and linked here before
implementation of this ref cutover begins.

That companion design must preserve the currently committed Tier-2 declaration
shape:

```python
@ms.metric(entities=[orders], ...)
def paid_revenue(order_rows):
    ...
```

`entities=[...]` remains the ordered dependency declaration; function
parameters remain positional entity aliases injected in that order, and
parameter names never establish entity identity. The companion design selects
the explicit replacement for current field-ref application such as
`is_paid(order_rows)` and must cover dimension, time-dimension, and measure refs,
single- and multi-entity bodies, validator restrictions, sidecar execution,
Ibis attribute shadowing, help, and typing.

Until that specification is accepted, current callable refs remain in place.
Once the companion design lands, this destructive cutover removes ref
callability atomically with the new expression binding; there is no interval in
which committed Tier-2 bodies have no executable replacement.

## Compiled Catalog Contract

### SemanticCatalog is immutable compiled state

`ms.load()` compiles the selected semantic world and returns a new catalog:

```python
catalog = ms.load()
```

Successful construction includes:

- project and external-layer discovery;
- controlled authoring module execution;
- datasource definition loading;
- typed dependency linking;
- whole-graph inference;
- assembly validation;
- deterministic catalog indexing;
- one `definition_fingerprint` over the compiled definition graph.

Load fails closed. An errored load does not return a partially usable catalog.
Warnings may accompany a successful catalog when the registered contract marks
them non-blocking.

Catalog instances are immutable. `catalog.load()` and other in-place reload
methods are removed. Reloading calls `ms.load()` again and returns another
compiled catalog with its own fingerprint and entries. An analysis session
retains the exact catalog supplied when the session was created or resumed.

### Definition fingerprint

`catalog.definition_fingerprint` identifies the canonical compiled definition
graph, including selected roots, kinds, identities, definition fields,
dependency edges, and expression identities. It excludes transient preview age,
connection instances, display state, and process-local object addresses.

The fingerprint is not part of `Ref` equality. The same ref can intentionally
exist in several catalog versions. Operations resolve it against one active
compiled catalog and record that catalog's fingerprint in persisted lineage.
It is whole-catalog provenance, not a replay compatibility gate: an unrelated
definition added elsewhere in the catalog changes this fingerprint without
changing the semantics of the recorded operation. Replay compatibility is
decided by the operation's exact semantic dependency digest defined below.

### Catalog entries

Each loaded object is exposed as one concrete entry family:

```text
DomainEntry
DatasourceEntry
EntityEntry
DimensionEntry
TimeDimensionEntry
MeasureEntry
MetricEntry
RelationshipEntry
```

The common base is `CatalogEntry[K]`. Every entry exposes:

```python
entry.ref       # Ref[K]
entry.kind      # delegated from ref
entry.path      # delegated from ref
entry.key       # delegated from ref
entry.name      # local display/lookup name
entry.details()
entry.contract()
entry.render()
entry.show()
```

Container entries expose the typed navigation selected by the catalog object
navigation design. Details contain compiled definition facts, dependency facts,
source provenance, inferred facts, and current catalog-bound status.

Entries are not refs, do not inherit from `Ref`, and are rejected by ref-input
APIs. They contain private catalog ownership so their navigation and contract
methods cannot silently read another catalog.

### Collections and navigation

The public hierarchy remains navigation-first:

```python
sales = catalog.domains.get("sales")
orders = sales.entities.get("orders")
revenue_entry = sales.metrics.get("revenue")
country_entry = orders.dimensions.get("country")

revenue = revenue_entry.ref
country = country_entry.ref
```

Every `CatalogCollection[K]` has the same protocol:

```python
collection.items   # tuple[CatalogEntry[K], ...]
collection.refs    # tuple[Ref[K], ...]
collection.get(local_name)
collection.render()
collection.show()
```

`collection.get(...)` accepts a local name only because the collection has
already established kind and scope. It requires uniqueness within that view and
raises a typed ambiguity error with executable alternatives. It rejects refs,
canonical `kind:path` keys, old typed IDs, and bare multi-segment paths. The
collection exposes complete `.refs` for identity consumption and does not expose
`.ids()`.

Known full identities use the catalog membership entry point:

```python
revenue = ms.Ref.metric("sales.revenue")
revenue_entry = catalog.require(revenue)
```

`catalog.require(ref)` accepts only `Ref[K]` and returns `CatalogEntry[K]`. The
current global `catalog.get(str)` path is removed. This gives Python one exact
kind constructor family and refs one catalog resolver.

There is deliberately no `catalog.contains(ref)`, `catalog.has(ref)`, or
`ref in catalog` path. Membership failure is operationally important because it
may mean a wrong project, filtered domain, stale script, or definition drift;
`catalog.require(ref)` preserves the structured error and real-state repair
instead of collapsing those cases to `False`.

### Catalog value

The catalog exists because it contains information and authority that source
variables cannot provide:

- the exact local plus external semantic world selected for this load;
- successful whole-project assembly and validation;
- one closed kind and identity index;
- resolved dependency, ownership, applicability, and relationship graphs;
- inferred metric entities, dimensions, time dimensions, lineage, additivity,
  unit, and planning facts;
- source locations and Python symbols as provenance, not identity;
- verify, preview, parity, readiness, and repair contracts;
- the definition fingerprint bound to analysis lineage.

If a future catalog implementation merely mirrors authoring variables without
these compiled facts, that implementation is redundant and should be deleted.

## Semantic Lifecycle APIs

Lifecycle operations accept refs only:

```python
catalog.verify(ref)
catalog.preview(ref, using=evidence_snapshot)
catalog.preview_many(refs, using=evidence_snapshots)
catalog.readiness(refs=refs)
```

Arity is deliberate and does not use an optional singular/plural mega-shape:

- `verify(ref)` statically verifies exactly one authored object, matching the
  one-object authoring loop;
- `preview(ref, using=...)` previews exactly one object and returns
  `PreviewResult`;
- `preview_many(refs, using=...)` previews one non-empty sequence and returns
  `PreviewBatchResult`; it is the registered readiness-repair batching
  capability, not a second spelling for a single preview;
- `readiness(refs=None)` evaluates one dependency-closed set and returns one
  report; `None` deliberately means all loaded objects, while a supplied
  sequence must be non-empty and unique.

`using=` always names a `DiscoverySnapshot` or exact entity-to-evidence-snapshot
mapping. The term “snapshot” is reserved for datasource evidence in lifecycle
APIs; the compiled semantic world is called a catalog throughout this design.

`CatalogEntry.contract()` may render exact object-bound calls using its `.ref`,
but entry objects are never accepted as shorthand.

The boundaries remain distinct:

- a ref proves typed identity syntax;
- `catalog.require(ref)` proves membership in one compiled catalog;
- verification proves the registered static contract for that catalog;
- preview supplies scoped runtime evidence where required;
- readiness reports whether that ref's dependency closure satisfies the
  registered semantic-to-analysis policy at that point in time;
- the agent workflow must obtain and inspect that report before analysis, but
  the returned refs are ordinary refs and carry no runtime authorization.

Readiness returns exact refs:

```python
report.analysis_ready_refs       # tuple[Ref[SemanticKindTag], ...]
report.preview_required_refs     # tuple[Ref[SemanticKindTag], ...]
```

`SemanticKindTag` is the already defined covariant generic bound representing
an intentionally heterogeneous set of exact refs. It is not a kind alias, ref
constructor, runtime union, or public help concept.

The report and each issue also record the catalog definition fingerprint. Ref
objects themselves remain free of readiness state. The report does not mint a
token, branded subtype, alternate constructor provenance, or session permit.
Consequently, an analysis boundary cannot distinguish a ref obtained from
`report.analysis_ready_refs` from the equal value built by `Ref.metric(...)`.

## Analysis Intent Contract

### Exact kind constraints

Every analysis semantic parameter declares its accepted ref kind directly:

```text
observe.metric              Ref[metric] | RuntimeMetricExpr
observe.dimensions          sequence[Ref[dimension | time_dimension]]
observe.slice_by keys       Ref[dimension | time_dimension]
observe.time_dimension      Ref[time_dimension]
attribute.axes              sequence[Ref[dimension | time_dimension]]
discover.search_space       sequence[Ref[dimension | time_dimension]]
policy.metric               Ref[metric]
policy.axis                 Ref[dimension | time_dimension]
```

The exact existing operator-by-operator kind matrix remains owned by the
analysis design. The invariant is that no semantic parameter uses a convenience
union of representations.

Runtime metric expressions remain a separate closed analysis value family. A
metric root may be `Ref[metric] | RuntimeMetricExpr` only because those are two
different selected subject semantics, not two wrappers for the same semantic
identity. Runtime expressions may contain exact refs as leaves but never acquire
a ref or catalog entry.

### Deleted input aliases

The breaking cutover deletes:

```text
SemanticInput
MetricInput
DimensionInput
SemanticAnchorInput
AnalysisDimensionRef as a runtime union of ref subclasses
CatalogObject | SemanticRef unions
```

Internal annotations retain the direct `Ref[K]` form, and public help renders
the accepted semantic kinds rather than an alias name.

### Boundary behavior

Analysis rejects all implicit conversions:

```python
session.observe(metric_entry)         # invalid_input
session.observe("sales.revenue")      # invalid_input
session.observe(entity_ref)           # semantic_kind_mismatch
session.observe(metric_ref, dimensions=[measure_ref])  # semantic_kind_mismatch
```

Errors teach the one valid conversion or browse path:

```text
observe(metric=...) expected Ref[metric] or RuntimeMetricExpr.
Received MetricEntry metric:sales.revenue.
Pass revenue_entry.ref.
```

Suggestions are derived from the active catalog and never silently applied.

Analysis admission resolves every ref against the session's fixed catalog and
checks exact kind plus operator-specific mechanical planning constraints. It
does not invoke `catalog.readiness(...)` automatically and does not claim that
a pure `Ref` proves readiness. Skipping the explicit readiness workflow is an
agent/policy violation rather than a state that can be detected from ref
constructor provenance. Skills, help, and generated analysis guidance retain
readiness-before-analysis as the required workflow.

## Resolver and Materializer Contract

Internal semantic resolvers consume exact refs only:

```python
resolver.entity(ref: Ref[entity])
resolver.dimension(ref: Ref[dimension | time_dimension])
resolver.measure(ref: Ref[measure])
resolver.metric(ref: Ref[metric])
```

They do not accept strings, catalog entries, authoring definitions, objects with
a `.ref` attribute, or `Any`-typed semantic values.

The resolver first requires the ref in its compiled catalog, validates its kind,
and then obtains the private compiled definition. Materializer and planner code
may use path strings as private dictionary keys after this boundary, but those
strings never reappear as public semantic inputs.

There is no fallback from a missing catalog object to direct source import or
ref fabrication. `unresolved_ref` reports the exact ref, catalog fingerprint,
and state-derived candidates.

## Persistence, Lineage, and Replay

Every persisted semantic identity uses the canonical structured ref payload.
Jobs, frame lineage, evidence records, policies, runtime metric expression
leaves, and replay descriptors share one serializer and parser.

### Role-preserving identity placement

A flat `semantic_refs` collection is insufficient as an authoritative record:
it loses whether a ref was the metric root, an axis, a slice predicate key, a
time axis, a policy subject, or an expression leaf. JSON object keys also cannot
carry structured ref payloads. Each owning schema therefore persists refs in
named, role-preserving fields and represents semantic-keyed maps as ordered
entry sequences.

The common conceptual records are:

```text
AxisBindingV1
    ref: RefPayloadV1  # decoded kind: dimension | time_dimension
    column: str
    role: "dimension" | "time_dimension"

SlicePredicateV1
    dimension_ref: RefPayloadV1  # decoded kind: dimension | time_dimension
    value: JsonValue
```

Their placement is normative:

| Owner | Authoritative semantic identity fields | Fields that remain strings |
| --- | --- | --- |
| metric frame metadata | `metric_ref`, ordered `axis_bindings`, ordered `slice_predicates`, optional `status_time_dimension_ref`, ordered component bindings with an exact named ref role | output column names, artifact/frame IDs |
| job/replay descriptor | root `metric_ref` or structured runtime-expression subject, ordered `dimension_refs`, ordered `slice_predicates`, optional `time_dimension_ref` | job/session IDs, physical output names |
| evidence and policy records | explicit named subject/axis/datasource `RefPayloadV1` fields chosen by that record's schema | evidence/check/snapshot IDs |
| preview/readiness records | explicit checked/blocked `RefPayloadV1` fields | issue, snapshot, and check IDs |
| metric-expression graph | the structured fields in the metric-graph cutover below | graph/node/expression IDs |

Generic `dict[str, Any]` semantic-anchor bags and string-keyed semantic maps
cannot own persisted identity. A top-level record may include a derived flat
`semantic_refs` list only as a redundant search index; readers reconstruct from
the role fields, verify any index matches their derived refs, and never use the
index to recover roles. Likewise, `.key` may be stored as a denormalized display
or search value only when the same owning record contains the canonical
structured payload.

Every owning top-level schema has its own explicit version and rejects its old
bare-string form. `RefPayloadV1` unifies embedded identity encoding; it does not
replace the version or role contract of the frame, job, evidence, policy,
readiness, or metric-graph schema that contains it.

### Dependency digest and replay

Persisted analysis records include whole-catalog provenance and the exact
semantic dependency digest for that operation:

```json
{
  "catalog_definition_fingerprint": "sha256:...",
  "semantic_dependency_digest": {
    "schema": "marivo.semantic_dependency_digest/v1",
    "digest": "sha256:..."
  },
  "metric_ref": {
    "schema": "marivo.semantic_ref/v1",
    "kind": "metric",
    "path": "sales.revenue"
  }
}
```

The digest covers the canonical compiled definitions and relevant dependency
edges in the exact transitive semantic closure used by the recorded operation,
including refs reached through runtime metric-expression leaves. It excludes
unrelated catalog objects and transient evidence state. Metric-expression
graphs use the already selected `SemanticDependencyDigestV1`; other persisted
operations use the same closure semantics and canonical definition encoder
rather than inventing per-feature drift heuristics.

Replay follows one route:

```text
read and validate the owning typed payload
    -> deserialize Ref[K]
    -> load/select active compiled catalog
    -> require every role-preserving ref in that catalog
    -> validate operator kind constraints
    -> recompute the exact operation dependency closure and digest
    -> require the recorded and active dependency digests to match
    -> plan and execute
```

If the whole-catalog fingerprint differs while the exact dependency digest is
equal, replay may continue and records both the original and active catalog
fingerprints as provenance; the mismatch is advisory because only unrelated
catalog state changed. If the dependency digest differs, replay stops before
execution with the changed dependency closure and repair guidance. There is no
"acknowledged replay" override. Running the intent against changed semantic
dependencies is an exploratory rerun with a new execution identity and lineage,
not replay of the prior record.

If a ref is missing, wrong-kind, or no longer mechanically executable, replay stops
with a typed error. It never guesses a kind, imports an authoring module, creates
a fallback ref, interprets an old bare path, reconstructs roles from a flat ref
list, or continues with partial inputs. As at initial analysis admission, replay
does not infer readiness authorization from ref provenance.

## Help and Agent Contract

The semantic root help teaches three concepts in this order:

```text
Authoring returns Ref[kind]
Catalog entries browse and inspect compiled objects
Semantic and analysis operations consume Ref[kind]
```

Focused help uses one consistent example:

```python
catalog = ms.load()
sales = catalog.domains.get("sales")
orders = sales.entities.get("orders")

revenue_entry = sales.metrics.get("revenue")
country_entry = orders.dimensions.get("country")

frame = session.observe(
    revenue_entry.ref,
    dimensions=[country_entry.ref],
)
```

Help never alternates between passing one catalog entry directly and extracting
another entry's ref. Signatures never expose broad input aliases. Error repair,
`.contract()`, docs, skills, examples, and generated API pages all use the same
canonical key format and explicit `.ref` handoff.

`repr(ref)` is bounded and exact:

```text
Ref[metric](metric:sales.revenue)
```

`repr(entry)` is also bounded and makes the distinction visible:

```text
MetricEntry metric:sales.revenue -> .show()
```

## Public Surface

The target public semantic identity/catalog surface is intentionally small:

```text
Ref
SemanticKind
SemanticCatalog
CatalogEntry
CatalogCollection
DomainEntry
DatasourceEntry
EntityEntry
DimensionEntry
TimeDimensionEntry
MeasureEntry
MetricEntry
RelationshipEntry
```

Concrete entry types are folded under one catalog-entry family in root help.
`Ref` is the only public semantic identity type and the only ref help target.

The following are removed from public exports and help:

```text
SemanticRef
DomainRef
DatasourceRef
EntityRef
DimensionRef
TimeDimensionRef
MeasureRef
MetricRef
RelationshipRef
CatalogObject
Domain / Datasource / Entity / Dimension / TimeDimension / Measure / Metric /
Relationship when those names mean loaded catalog views
SemanticInput
MetricInput
DimensionInput
SemanticAnchorInput
```

Renaming catalog view classes to `*Entry` prevents collisions between semantic
kinds, authoring concepts, and loaded views in prose, typing, errors, and future
extensions.

## Error Contract

Every ref-related typed error includes:

- the operation and parameter;
- expected `Ref[kind]` set;
- received concrete type and, when available, canonical key;
- active catalog definition fingerprint when resolution was attempted;
- bounded real-state candidates;
- one mechanically valid next call.

Required cases include:

- invalid canonical key syntax;
- unknown kind;
- malformed semantic path;
- legacy datasource name rejected by the central segment grammar, with an
  explicit valid rename and the datasource-identity/cache consequences above;
- catalog entry passed where ref is required;
- string passed where ref is required;
- wrong-kind ref;
- ref absent from active catalog;
- ref present but outside selected domain filter;
- semantic dependency digest mismatch during replay;
- readiness blocker reported by the explicit readiness operation;
- old or unsupported persisted ref schema;
- ambiguous local name inside a scoped collection.

No error performs the repair automatically. No error suggests importing an
authoring module or passing a catalog entry directly.

An old persisted identity produces `unsupported_persisted_schema` before
catalog resolution. The error reports the expected `marivo.semantic_ref/v1`
schema, the received bare string or legacy schema, and the destructive-cutover
policy. Its taught next step is to start a new analysis session and regenerate
the required artifacts from current semantic refs and source evidence. Marivo
does not delete old `.marivo/` state, reinterpret it, or offer an in-place
migration command.

## Internal Ownership

```text
private lower core (currently marivo.refs)
    sealed Ref, SemanticKind, path validator, canonical parser/serializer,
    Pydantic integration, private pickle restore path

marivo.datasource
    datasource authoring definitions; adopts the central segment validator,
    preserves DatasourceError adaptation, returns Ref[datasource]

marivo.semantic authoring
    sole public ms.Ref / ms.SemanticKind re-export
    explicit Python declarations; returns exact Ref[K]

marivo.semantic loader/compiler
    authoring execution, IR assembly, linking, validation, fingerprint

marivo.semantic catalog
    immutable compiled state, entries, collections, navigation, membership

marivo.semantic resolver/materializer
    private compiled-definition resolution from exact refs

marivo.analysis
    exact-kind intent admission, planning, artifacts, lineage, replay
```

The private compiled registry remains the execution bridge. Public catalog
entries are not reverse-translated into IR by planners, and public refs do not
carry IR.

## Breaking Deletions

The implementation cutover removes, rather than adapts:

- `SymbolKind`, the `SemanticKind = SymbolKind` alias, and every second kind
  enum name; one canonical `SemanticKind` remains;
- all kind-specific runtime ref subclasses;
- ref callability and late-bound resolver state;
- `SemanticRef` as a second public/base value family;
- `ms.ref(...)`, generic `Ref.parse(...)`, and raw `Ref(...)` construction;
- `.id` / `.semantic_id` ambiguity on public refs and entries;
- old `<kind>.<semantic_id>` textual typed IDs;
- bare semantic path acceptance at public boundaries;
- `CatalogObject` as an operator input;
- `CatalogObject | SemanticRef` and equivalent unions;
- global `catalog.get(str)`; exact membership becomes `catalog.require(ref)`;
- `CatalogCollection.ids()` plus typed-ID, canonical-key, ref, and
  multi-segment-path acceptance in scoped `collection.get(...)`;
- `catalog.verify_object(...)`; the singular ref-only capability is
  `catalog.verify(ref)`;
- the optional `preview(ref=None, refs=None, ...)` mega-signature; singular
  preview is `preview(ref, ...)` and batch repair is `preview_many(refs, ...)`;
- `SemanticInput`, `MetricInput`, `DimensionInput`, and
  `SemanticAnchorInput`;
- `as_ref`, `as_ref_id`, attribute-based unwrapping, and generic ref
  normalizers;
- resolver and materializer string tolerance;
- Pydantic string-to-ref coercion and bare-path ref serialization;
- the datasource-specific `^[A-Za-z0-9_-]+$` name rule and datasource names
  containing uppercase, hyphens, or leading digits; callers rename declarations
  and update every semantic identity consumer, with credential-cache reuse only
  under the normalized environment-variable rule defined above;
- mutable/in-place catalog reload;
- replay fallback ref construction;
- old persisted ref readers and writers;
- help and errors that teach more than one semantic input representation.

No compatibility imports preserve the old names. Historical versioned docs and
artifacts remain historical; current runtime refuses old payloads with an exact
schema error.

## Relationship to Runtime Metric Expressions

The typed metric composition design remains active with these substitutions:

```text
old MetricRef                         -> Ref[metric]
old MeasureRef                        -> Ref[measure]
old DimensionRef                      -> Ref[dimension]
old TimeDimensionRef                  -> Ref[time_dimension]
old AnalysisDimensionRef              -> Ref[dimension | time_dimension]
old CatalogObject[RefT]               -> CatalogEntry[K]
old CatalogCollection[ObjectT, RefT]  -> CatalogCollection[K]
```

`RuntimeMetricExpr` remains a closed immutable analysis expression, not a
semantic object. Its catalog leaves use `Ref[K]`; canonical expression payloads
embed the structured ref representation. `session.observe(...)` continues to
be the sole materialization entry and accepts:

```text
Ref[metric] | RuntimeMetricExpr
```

This is a semantic union, not an identity-shape compatibility union.

### Metric-graph persisted schema cutover

The in-flight metric-expression graph currently sketches v1 dataclasses with
bare string identities. Those payloads do not land as a stable schema. The
metric-graph v1 contract is re-cut on the unified structured ref payload before
merge or persistence:

```text
SemanticDependencyEntryV1.semantic_kind + semantic_id
    -> ref: RefPayloadV1
SemanticDependencyDigestV1 schema `semantic-dependency/v1` + `fingerprint` -> schema `marivo.semantic_dependency_digest/v1` + `digest`

CatalogBodyLeafV1.metric_id
    -> metric_ref: RefPayloadV1

AggregateNodeV1.target_kind + target_id
    -> target_ref: RefPayloadV1

CanonicalSlice dimension-id keys
    -> dimension_ref: RefPayloadV1

CumulativeNodeV1.over
    -> time_dimension_ref: RefPayloadV1 | None

CatalogMetricIdentity.metric_id
    -> metric_ref: RefPayloadV1

CatalogMetricSubjectV1.metric_id
    -> metric_ref: RefPayloadV1
```

Graph child IDs, node IDs, expression fingerprints, artifact IDs, and session
IDs remain strings because they are content or runtime identities, not semantic
object refs.

The required landing order is:

1. accept this design and the Tier-2 expression-binding prerequisite;
2. land the unified `SemanticKind`, sealed `Ref[K]`, path validator, and
   `RefPayloadV1` serializer/decoder;
3. rebase the metric-graph contracts and canonical encoder on that payload;
4. land metric-graph lowering, persistence, readiness, analysis, and replay
   consumers;
5. cut over the remaining semantic/catalog/analysis surfaces atomically.

During steps 2–4 the new foundation is private and feature-internal: `Ref`,
`SemanticKind`, `RefPayloadV1`, and their adapters do not enter public
`__all__`, root/focused help, CLI contracts, generated API pages, site examples,
or public Pydantic input models. Existing public identity surfaces remain the
only supported contract until the atomic step-5 switch. Public-surface snapshot
tests enforce that transition boundary. Internal metric-graph schemas may be
implemented and tested during steps 2–4, but their persisted writers and public
readers are not activated before step 5.

Metric-graph implementation may continue locally while steps 1–2 are prepared,
but no commit, release, persisted fixture, or public contract may establish a
bare-string `metric-expression/v1` or `semantic-dependency/v1`. Because there is
no compatibility policy, allowing those schemas to land first would create
immediately unreadable state and is explicitly forbidden.

## Security and Effects

- Factory construction or private persisted-payload decoding has no I/O or
  mutation.
- Browsing an already loaded catalog has no datasource access.
- Loading a catalog executes explicit project authoring Python and reads local
  or configured model metadata; live help must disclose that effect.
- Calling a `Ref.<kind>(...)` factory has no I/O and no semantic side effect.
- Copy and deepcopy preserve the immutable value; pickle restore performs only
  validated payload decoding and no catalog or datasource I/O.
- Marivo never unpickles untrusted job, artifact, or lineage bytes; durable
  records remain structured JSON decoded through the versioned payload path.
- `catalog.require(ref)` performs catalog-local metadata lookup only.
- Verification remains query-free.
- Preview and materialization retain their explicit scoped-data-read contracts.
- Ref payloads never contain datasource credentials.
- Datasource renaming does not mutate or delete credential cache entries;
  conventional cache reuse follows normalized environment-variable equality.

## Acceptance Criteria

### Identity

- One concrete runtime `Ref` class represents every addressable semantic kind.
- Raw `Ref(...)` construction and subclassing are sealed; runtime tests cover
  every raw constructor form and subclass declaration.
- A static typing fixture using `assert_type` assertions or an expected
  type-checker snapshot proves there is no covariant `Ref[Never]`
  construction/assignability bypass; this is not claimed from runtime tests
  alone.
- Equality and hashing use exactly `(kind, path)`.
- `copy.copy(ref)` and `copy.deepcopy(ref)` return `ref`; every supported pickle
  protocol round-trips through the private validated decoder;
  `dataclasses.replace(ref, ...)` fails intentionally.
- `metric:sales.revenue` round-trips through canonical text and structured JSON.
- Old dot-typed and bare-path strings are rejected.
- Every current kind enforces the normative segment grammar and exact segment
  count at authoring, factory, and persisted-payload boundaries.
- Datasource authoring rejects uppercase, hyphenated, and leading-digit names
  with a valid rename; tests cover conventional credential-cache reuse when the
  normalized environment-variable key is equal and non-reuse when it differs.
- `SemanticKind` is the only enum name; `SymbolKind` and enum aliases are absent.
- No ref is callable or mutable after construction.

### Authoring and loading

- Every authoring helper returns the exact statically typed `Ref[K]`.
- Every dependency parameter accepts only its exact ref kind.
- Normal import of authoring modules is not a supported analysis path.
- The Tier-2 Semantic Expression Reference Binding Design is accepted before
  callable refs are removed, preserves `entities=[...]` positional aliases, and
  supplies an executable replacement for every committed field-ref body shape.
- Local and external layers compile into one immutable catalog.
- Reload creates a new compiled catalog and fingerprint without mutating the old
  one.

### Catalog

- Every entry has exactly one `.ref` and one compiled-catalog owner.
- Global resolution is `catalog.require(ref)`; no global string lookup remains.
- Membership has no non-raising public alternative that discards structured
  diagnostics.
- Scoped collections accept only local names and return concrete entries.
- Scoped collections expose `.refs`, not `.ids()`, and reject typed keys and
  refs in `.get(...)`.
- Entry details expose compiled/inferred graph facts unavailable from source
  variables alone.
- Passing an entry to a ref-only API fails with a copyable `.ref` repair.

### Semantic and analysis boundaries

- Verify, preview, readiness, resolver, materializer, and analysis inputs accept
  refs only, subject to exact kind constraints.
- Lifecycle arity is exact: `verify` and `preview` are singular,
  `preview_many` is batch-only, and `readiness` owns set scope.
- Broad semantic input aliases and normalization helpers are absent.
- Readiness outputs ordinary exact refs and records catalog fingerprint; it
  mints no token or runtime brand.
- Analysis does not invoke readiness automatically or infer readiness from ref
  provenance; readiness-before-analysis remains an explicit skill/help
  workflow, while the runtime still enforces membership, kind, and mechanical
  planning constraints.
- Runtime metric expressions embed exact refs without acquiring semantic
  identity.

### Persistence and replay

- All persisted semantic identities use one versioned structured payload.
- Frames, jobs, evidence, policies, preview/readiness records, and metric graphs
  place structured refs in explicit role-preserving fields; string-keyed maps
  become ordered entry records, and a flat `semantic_refs` list is never the
  authoritative reconstruction source.
- `Ref[K]` Pydantic fields enforce exact runtime kind in Python mode, accept only
  `RefPayloadV1` in JSON mode, emit structured JSON/schema, and never fall back
  to the sealed dataclass constructor.
- Metric-expression graph and semantic-dependency v1 payloads use
  `RefPayloadV1` before their first stable merge or persisted fixture.
- Lineage and jobs record the whole-catalog definition fingerprint as provenance
  and the exact semantic dependency digest as the replay compatibility gate.
- Replay resolves every ref against its selected catalog before execution.
- An unrelated whole-catalog fingerprint change does not block replay when the
  exact dependency digest is unchanged.
- Missing refs, wrong kinds, old schemas, and dependency drift fail explicitly;
  running against changed dependencies creates a new exploratory execution,
  never an acknowledged replay override.
- Old persisted schemas teach “start a new session and regenerate artifacts”
  without deleting or reinterpreting existing state.
- No fallback ref fabrication or old-payload interpretation remains.

### Agent surface

- Root and focused help teach one explicit `CatalogEntry.ref -> Ref[K]` handoff.
- All examples pass refs consistently for metrics, dimensions, filters, axes,
  policies, and lifecycle operations.
- Errors derive candidates and repairs from the active catalog.
- Public-export snapshot tests contain `Ref` but no old ref subclasses or input
  aliases.
- Before the atomic public cutover, snapshot tests prove the new foundation is
  absent from public `__all__`, help, CLI, generated docs, and public models.
- `ms.Ref` is the only public Ref owner and help target; `md.Ref` is absent.
- Canonical specs, latest bilingual docs, skills, generated API pages, and tests
  move atomically with the runtime cutover.

## Resolved Questions

### Why not import semantic objects directly from authoring files?

Because those files are declaration sources executed by the loader, not the
compiled semantic world attached to a session. Their variables are source-level
aliases, while the catalog owns cross-layer linking, inference, validation,
readiness evaluation, and the session-bound compiled execution context.
Analysis scripts use
`Ref.<kind>(...)` when they already know an identity and catalog navigation when
they need discovery; neither path imports authoring code.

### Why not make CatalogEntry inherit from Ref?

Because catalog membership, details, readiness, and navigation are catalog-bound
while identity is stable and context-free. Inheritance would let
catalog-bearing entries cross ref-only APIs implicitly and recreate the
current ambiguity.

### Why require explicit `.ref`?

Because it is the visible transition from browsing a compiled object to passing
stable identity into an operation. One explicit attribute is lower cognitive
cost than a hidden normalization union whose accepted representations vary by
API.

### Why not store the catalog fingerprint inside Ref?

Because semantic identity should survive recompilation and exist before a
catalog is loaded. The operation and persisted lineage record which catalog
interpretation was used.

### Why one runtime Ref class instead of exact subclasses?

Because kind is data, not behavior. Exact static typing remains available
through `Ref[K]`, while one runtime class removes subclass factories,
cross-layer base-class leakage, mutable field-ref exceptions, and separate
serialization paths.

### Why is there no generated refs module?

Because it would introduce another artifact lifecycle, naming policy,
fingerprint-staleness state, and import surface without adding semantic
authority. An analysis script that already knows an object can construct its
pure identity with `Ref.<kind>(path)`; an agent that needs discovery uses the
catalog. Those two paths cover the use cases without generating a mirror file.

## Final Contract

```text
Definition says what the object means.
CatalogEntry says what one compiled catalog knows about it.
Ref says only which object it is.

Python source is authored.
Catalogs are compiled.
Refs cross layers.
Readiness evaluates the required analysis workflow gate.
```
