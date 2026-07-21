# Unified Semantic Object Reference and Tier-2 Binding Implementation

Status: implemented; acceptance verified against both normative designs

Date: 2026-07-19

Normative design sources:

- [`Unified Semantic Object and Reference Design`](2026-07-19-unified-semantic-object-reference-design.md)
- [`Tier-2 Semantic Expression Reference Binding Design`](2026-07-19-tier2-semantic-expression-reference-binding-design.md)

This document specifies the concrete code structure, internal records,
algorithms, runtime call flow, persistence schemas, and activation boundary for
the two accepted designs. It is not a task list or migration plan.

## Final Runtime Shape

The final runtime has one semantic identity class, one kind enum, one compiled
catalog, and one catalog-entry family:

```text
Ref[K]
SemanticKind
SemanticCatalog
CatalogEntry[K]
CatalogCollection[K]
```

Authoring code produces refs. Loading compiles definitions and expression
bodies into an immutable catalog. Catalog navigation produces entries.
Lifecycle and analysis APIs consume refs only.

The public flow is:

```python
import marivo.semantic as ms

catalog = ms.load()

revenue_entry = catalog.domains.get("sales").metrics.get("revenue")
revenue = revenue_entry.ref

known_country = ms.ref.dimension("sales.orders.country")
country_entry = catalog.require(known_country)

frame = session.observe(
    revenue,
    dimensions=[country_entry.ref],
)
```

There is no generated refs module, public parser, generic ref constructor,
catalog-entry coercion, string coercion, or legacy payload reader.

## Concrete Module Layout

| Module | Final responsibility |
| --- | --- |
| `marivo/refs.py` | `SemanticKind`, private kind markers, sealed `Ref[K]`, path grammar, `RefPayloadV1`, canonical private decoder, Pydantic integration, pickle restore |
| `marivo/semantic/_expression_binding.py` | binding records, AST binding extraction support, immutable expression sidecar, task-local context, nested body frames, field-ref application |
| `marivo/semantic/_definition_identity.py` | canonical compiled-definition encoding, whole-catalog fingerprint, exact dependency-closure digest |
| `marivo/semantic/_persistence.py` | preview/readiness owner schemas and entity-to-snapshot ref bindings |
| `marivo/semantic/_authoring_context.py` | pending typed definitions and expression bodies during controlled loader execution |
| `marivo/semantic/loader.py` | root discovery, controlled execution, assembly, binding validation, immutable compiled-state construction |
| `marivo/semantic/catalog.py` | immutable catalog, entry families, collections, navigation, `require`, lifecycle facade |
| `marivo/semantic/materializer.py` | ref-only materialization and root expression-context installation |
| `marivo/semantic/validator.py` | static declaration/AST/assembly validation over compiled refs and private IR |
| `marivo/analysis/_semantic_persistence.py` | role-preserving ref-bearing records shared by frames, jobs, evidence, policies, and replay |
| `marivo/semantic/metric_graph*.py` | structured-ref metric graph, canonical encoding, lowering, exact dependency digest |

`marivo/semantic/refs.py` disappears. It is not retained as a compatibility
import module. `marivo.datasource` does not define or export another ref class.

The dependency direction is:

```text
marivo.refs
    ^
    |
datasource authoring     semantic authoring/compiler/catalog
                                  ^
                                  |
                              analysis
```

`marivo.refs` has no import-time dependency on datasource, semantic, analysis,
Ibis, or Pydantic. Optional integrations use local imports at the call site.

## Unified Ref Core

### Kind registry and typing markers

`SemanticKind` is defined once in `marivo/refs.py`:

```python
class SemanticKind(StrEnum):
    DOMAIN = "domain"
    DATASOURCE = "datasource"
    ENTITY = "entity"
    DIMENSION = "dimension"
    TIME_DIMENSION = "time_dimension"
    MEASURE = "measure"
    METRIC = "metric"
    RELATIONSHIP = "relationship"
```

Static precision uses private phantom marker classes:

```python
class SemanticKindTag: ...
class DomainKind(SemanticKindTag): ...
class DatasourceKind(SemanticKindTag): ...
class EntityKind(SemanticKindTag): ...
class DimensionKind(SemanticKindTag): ...
class TimeDimensionKind(SemanticKindTag): ...
class MeasureKind(SemanticKindTag): ...
class MetricKind(SemanticKindTag): ...
class RelationshipKind(SemanticKindTag): ...

KindT = TypeVar("KindT", bound=SemanticKindTag, covariant=True)
type FieldKind = DimensionKind | TimeDimensionKind | MeasureKind
```

The marker classes are not values, are not exported from `marivo.semantic`, and
do not receive help topics. The help/type renderer maps them to
`Ref[dimension]`, `Ref[metric]`, and corresponding closed unions.

One private registry connects static markers to runtime tags:

```python
_KIND_BY_MARKER = {
    DomainKind: frozenset({SemanticKind.DOMAIN}),
    DatasourceKind: frozenset({SemanticKind.DATASOURCE}),
    EntityKind: frozenset({SemanticKind.ENTITY}),
    DimensionKind: frozenset({SemanticKind.DIMENSION}),
    TimeDimensionKind: frozenset({SemanticKind.TIME_DIMENSION}),
    MeasureKind: frozenset({SemanticKind.MEASURE}),
    MetricKind: frozenset({SemanticKind.METRIC}),
    RelationshipKind: frozenset({SemanticKind.RELATIONSHIP}),
}
```

Union annotations are flattened into the union of their registered runtime
tags. An unparameterized `Ref` has no allowed public kind set and is rejected by
the Pydantic adapter and public annotation audit.

### Concrete class

The final class follows this shape:

```python
@final
@dataclass(frozen=True, slots=True, init=False)
class Ref(Generic[KindT]):
    kind: SemanticKind
    path: str

    def __new__(cls, *args: object, **kwargs: object) -> Never:
        raise TypeError(
            "Ref has no public raw constructor; use ref.metric(...), "
            "ref.dimension(...), or another exact kind factory."
        )

    def __init_subclass__(cls, **kwargs: object) -> Never:
        raise TypeError("Ref is sealed and cannot be subclassed.")

```

Private `_create_ref(kind, path)` performs validated construction. A final
`_RefFactory` exposes eight exact methods on the singleton public namespace
`ms.ref`; for example, `ms.ref.metric(path) -> Ref[MetricKind]`. Neither the
class nor its instances expose kind factories.

Every public boundary begins with `type(value) is Ref`, not `isinstance`, then
checks the runtime kind against the annotation's closed allowed set. This is a
defensive boundary check in addition to the sealed class.

`Ref` has no upward import into the semantic runtime. Merely importing or
constructing a ref does not import semantic runtime modules.

### Value behavior

The dataclass-generated equality and hash use exactly `(kind, path)`. No
catalog fingerprint or construction provenance participates.

The remaining behavior is explicit:

```python
@property
def key(self) -> str:
    return f"{self.kind.value}:{self.path}"

@property
def name(self) -> str:
    return self.path.rsplit(".", 1)[-1]

def __str__(self) -> str:
    return self.key

def __repr__(self) -> str:
    return f"Ref[{self.kind.value}]({self.key})"

def __copy__(self) -> Ref[KindT]:
    return self

def __deepcopy__(self, memo: dict[int, object]) -> Ref[KindT]:
    memo[id(self)] = self
    return self

def __reduce_ex__(self, protocol: int) -> tuple[object, tuple[object, ...]]:
    del protocol
    return (_restore_ref_payload, (RefPayloadV1.from_ref(self),))
```

`_restore_ref_payload` calls the same validated private payload decoder used by
durable JSON. It performs no catalog or datasource I/O. `dataclasses.replace`
remains unavailable because `init=False`; identities are replaced through a
new exact factory call, not field editing.

### Path grammar

One compiled regular expression and one exact segment-count table implement the
grammar:

```python
_SEGMENT_RE = re.compile(r"^[a-z][a-z0-9_]*$")

_SEGMENT_COUNT = {
    SemanticKind.DOMAIN: 1,
    SemanticKind.DATASOURCE: 1,
    SemanticKind.ENTITY: 2,
    SemanticKind.DIMENSION: 3,
    SemanticKind.TIME_DIMENSION: 3,
    SemanticKind.MEASURE: 3,
    SemanticKind.METRIC: 2,
    SemanticKind.RELATIONSHIP: 2,
}
```

`_validate_ref_path(kind, path)` requires `type(path) is str`, rejects any value
for which `path != path.strip()`, splits on `.`, validates the exact count, then
validates every non-empty segment. It never lowercases, strips, replaces
hyphens, or guesses a missing domain/entity.

`_validate_segment(value, role=...)` is shared by datasource names and semantic
authoring names. Authoring validation therefore fails before a pending
definition is created.

Datasource errors remain `DatasourceError` family values. The datasource layer
catches the private grammar failure and renders a domain-owned error with:

- expected `[a-z][a-z0-9_]*`;
- received name;
- one explicit valid rename;
- the resulting datasource identity change;
- credential-cache reuse only when the conventional environment-variable name
  remains equal.

The implementation neither renames nor deletes existing secret-cache entries.

## Structured Ref Payload and Pydantic

### Internal DTO

The only durable semantic identity payload is:

```python
@dataclass(frozen=True, slots=True)
class RefPayloadV1:
    schema: Literal["marivo.semantic_ref/v1"]
    kind: SemanticKind
    path: str

    @classmethod
    def from_ref(cls, ref: Ref[SemanticKindTag]) -> RefPayloadV1:
        return cls(
            schema="marivo.semantic_ref/v1",
            kind=ref.kind,
            path=ref.path,
        )
```

`__post_init__` verifies the schema literal and calls `_validate_ref_path`. The
DTO is private and is not accepted by public semantic or analysis functions.

The wire form is:

```json
{
  "schema": "marivo.semantic_ref/v1",
  "kind": "metric",
  "path": "sales.revenue"
}
```

`_decode_ref_payload` dispatches through a closed internal factory map. It does
not call `Ref.__new__`, assign unchecked fields, or consult a catalog.

The canonical text decoder is also private:

```text
metric:sales.revenue
```

It is used only by CLI adapters and diagnostic tooling. Python callers use an
exact factory and pass the path without the kind prefix.

### Pydantic schema implementation

`Ref.__get_pydantic_core_schema__` builds a `json_or_python_schema` from the
generic argument in `source_type`.

Python validation performs this sequence:

```text
type(value) is Ref
    -> runtime kind belongs to the closed allowed set
    -> return the same immutable value
```

JSON validation performs this sequence:

```text
exact object keys schema/kind/path
    -> schema literal is current
    -> kind is closed and allowed by K
    -> path matches that kind
    -> private payload decoder
```

The serializer inspects Pydantic mode. Python mode returns the `Ref`; JSON mode
returns the complete `RefPayloadV1` object. JSON schema for an exact kind uses a
`const` kind; a closed union uses only its allowed enum values.

Strings, mappings in Python mode, catalog entries, subclasses, wrong-kind refs,
bare paths, and canonical keys all fail without coercion.

## Authoring Representation

### Pending definition record

The loader context no longer stores `(ir, callable)` pairs and a separate list
of mutable refs. It stores one record per addressable definition:

```python
@dataclass(frozen=True, slots=True)
class PendingDefinition:
    ref: Ref[SemanticKindTag]
    definition: DefinitionIR
    expression_body: ExpressionBody | None
```

The private IR may continue to use path strings as internal storage keys after
the exact ref boundary. `PendingDefinition` is the invariant that prevents the
IR string from becoming a second public identity: assembly verifies that the
IR kind/path exactly equals `ref.kind/ref.path` before registration.

Domain and datasource declarations also receive pending refs. Nested values
such as join keys, source descriptors, time folds, provenance, and additivity
records remain ordinary closed values.

### Helper implementation pattern

An addressable semantic helper follows one pattern:

```python
def dimension_column(
    *,
    name: str,
    entity: Ref[EntityKind],
    column: str,
    ...,
) -> Ref[DimensionKind]:
    _require_exact_ref(entity, SemanticKind.ENTITY, parameter="entity")
    path = f"{entity.path}.{_validate_segment(name, role='dimension name')}"
    ref = ref.dimension(path)
    definition = DimensionIR(
        semantic_id=ref.path,
        entity=entity.path,
        ...,
    )
    body = ExpressionBody.for_column(column)
    _push_pending(ref, definition, body)
    return ref
```

Decorator helpers compute the final ref before body validation so binding
metadata records the owning identity:

```python
def decorator(fn: Callable[..., object]) -> Ref[MetricKind]:
    ref = ref.metric(f"{domain.path}.{name or fn.__name__}")
    body = compile_expression_body(
        fn,
        owning_ref=ref,
        ordered_entity_refs=tuple(entities),
    )
    _push_pending(ref, metric_ir, body)
    return ref
```

Every dependency parameter uses the exact generic type and repeats the exact
runtime kind check. No helper accepts a catalog entry, string, callable, or
object with `.ref`.

Datasource specification constructors remain datasource specification
builders because those specs also serve `md.register`, `md.test`, and
`md.inspect`. Their `.ref` property becomes `Ref[DatasourceKind]`. Explicitly
constructing a known datasource identity uses
`ms.ref.datasource("warehouse")`; `md.ref` and `md.DatasourceRef` are removed.

## Expression Binding Compilation

### Records

`marivo/semantic/_expression_binding.py` owns:

```python
@dataclass(frozen=True, slots=True)
class ExpressionBindingV1:
    field_ref: RefPayloadV1
    entity_position: int


@dataclass(frozen=True, slots=True)
class ExpressionBody:
    callable: Callable[..., object]
    body_ast_hash: str
    parameter_count: int
    bindings: tuple[ExpressionBindingV1, ...]
```

The callable is process-local and excluded from canonical serialization.
`body_ast_hash`, `parameter_count`, and `bindings` are definition identity.

Direct-column helpers create a deterministic `ExpressionBody` with one
parameter and no semantic bindings. Their body hash is derived from the closed
column descriptor, not the synthetic Python callable's source. Expression
decorators compile the function source and capture bindings.

### AST extraction

`compile_expression_body` receives the callable, owning ref, and ordered entity
refs. It first applies the existing single-return and Ibis-expression
whitelist, then performs binding extraction.

Source availability is mandatory for decorator bodies. Failure to obtain or
parse source is a structured authoring error; it does not produce an
`<unavailable>` hash and skip binding analysis. Loader-generated direct-column
bodies are the only source-free case because their closed descriptor is already
known.

The body parameter contract is exact:

- only direct positional or positional-or-keyword parameters are aliases;
- parameter count equals the decorator's entity count;
- the entity ref at position `i` owns parameter `i`;
- parameter spelling has no semantic meaning.

The only semantic field application AST is:

```text
Call(
    func=Name(field_symbol),
    args=[Name(body_parameter)],
    keywords=[],
)
```

Symbol resolution uses `fn.__globals__` and `inspect.getclosurevars(fn)`.
Only the exact value bound to the `Name` is read. The compiler does not perform
attribute traversal, invoke descriptors, import modules, or execute caller
code.

When the resolved value is a ref, compilation requires:

```text
type(value) is Ref
value.kind in {dimension, time_dimension, measure}
one positional Name argument
argument name is a direct body parameter
no keywords
```

Other allowed Ibis calls continue through the existing AST whitelist.
Unrecognized `Name(...)` calls remain invalid expression DSL operations rather
than becoming dynamic semantic dependencies.

For every valid field call, the compiler records:

```python
ExpressionBindingV1(
    field_ref=RefPayloadV1.from_ref(value),
    entity_position=parameter_positions[argument.id],
)
```

Bindings preserve first AST occurrence order and deduplicate on the pair
`(field_ref.kind, field_ref.path, entity_position)`. Source locations and
Python names are not serialized.

The body hash is computed from a normalized AST, not the raw `ast.dump`. The
normalizer removes the function name and docstring, rewrites entity parameter
names to positional tokens such as `entity_0`, and rewrites semantic field
callee names to binding-position tokens. Exact ref payloads and entity
positions are encoded separately in `bindings`. Renaming a function, entity
parameter, or Python variable holding the same field ref therefore leaves the
body hash and dependency digest unchanged. Changing a physical column,
operator, literal, field ref, or entity position changes them.

Assembly later resolves the field ref and verifies that its owning entity is
the entity declared at that position. Decoration can validate syntax and kind;
assembly owns closed-world membership and ownership.

## Expression Binding Runtime

### Catalog-bound sidecar

The final sidecar is an immutable catalog-local value:

```python
@dataclass(frozen=True, slots=True)
class CompiledExpressionSidecar:
    bodies: Mapping[Ref[SemanticKindTag], ExpressionBody]
    field_owners: Mapping[Ref[FieldKind], Ref[EntityKind]]
    catalog_refs: frozenset[Ref[SemanticKindTag]]
```

The mappings are copied into immutable mappings when compilation succeeds.
There is no later wiring step.

### Active context and frames

The task-local records are:

```python
@dataclass(frozen=True, slots=True)
class ExpressionBodyFrame:
    owning_ref: Ref[SemanticKindTag]
    ordered_entity_refs: tuple[Ref[EntityKind], ...]
    ordered_entity_aliases: tuple[IbisTable, ...]
    declared_bindings: tuple[ExpressionBindingV1, ...]


@dataclass(frozen=True, slots=True)
class ExpressionBindingContext:
    catalog_definition_fingerprint: str
    expression_sidecar: CompiledExpressionSidecar
    body_frames: tuple[ExpressionBodyFrame, ...]
```

One module-private `ContextVar[ExpressionBindingContext | None]` holds the
current value. A root body evaluation installs a one-frame context and retains
the returned token. The token is reset in `finally`.

Nested calls do not mutate the current context. They create a replacement
context with one appended immutable frame, install it with another token, and
reset that token in `finally`.

### `bind` algorithm

The public `ms.bind(ref, alias)` operation owns field application. Its implementation
performs these steps in order:

```text
1. require type(ref) is Ref
2. require ref.kind is dimension/time_dimension/measure
3. require an active ExpressionBindingContext
4. inspect the top body frame
5. find alias positions using Python `is`
6. require exactly one matching position
7. require (ref, position) in top-frame declared bindings
8. require ref in sidecar.catalog_refs
9. require sidecar.field_owners[ref] equals frame entity at that position
10. require an ExpressionBody for ref
11. reject ref already present as owning_ref in the frame stack
12. push a one-entity frame for the referenced field body
13. call that body with the exact selected alias
14. require one allowed Ibis value
15. reset the nested context in finally
```

Alias matching never uses `==`, Ibis expression equality, table name, backend
identity, or schema. If the same Python table object occupies more than one
entity position, the call fails as ambiguous.

A filtered, selected, joined, or otherwise transformed table is not a direct
alias. Authors apply the semantic field first and transform its returned value.

The nested frame uses the referenced field body's own `declared_bindings`, so a
nested semantic field call is checked against the field definition rather than
the outer metric definition.

Binding errors propagate as their structured semantic error. Arbitrary
exceptions from the body are wrapped once as a body-execution error while
retaining owning ref and cause. A `NameError` retains the existing teaching
hint about imports. No error is retried.

### Materializer integration

`Materializer` has one internal body executor:

```python
def _evaluate_expression_body(
    self,
    *,
    owning_ref: Ref[SemanticKindTag],
    body: ExpressionBody,
    entity_refs: tuple[Ref[EntityKind], ...],
    aliases: tuple[ibis.Table, ...],
) -> ir.Value:
    ...
```

It verifies arity, installs the root binding context, calls the body, validates
the result, and resets the context. Dimension, time-dimension, measure, Tier-2
metric, preview, parity, readiness probe, and analysis materialization paths
all use this executor.

Tier-1 and derived metrics continue through their closed composition logic.
When they need a measure on a supplied table, they call the same expression
body executor rather than invoking a raw callable.

Materializer caches remain keyed privately after a ref-only entrance. They may
use `ref.path` strings, but public and cross-module resolver methods receive
exact refs.

## Loader and Immutable Compiled State

### Assembly

Loader execution produces pending definitions from local and external roots.
Assembly performs:

```text
merge selected roots
    -> reject duplicate Ref identities
    -> build kind-specific private registry maps
    -> validate every PendingDefinition ref/IR pair
    -> resolve dependency refs
    -> validate expression bindings and entity ownership
    -> add binding dependency edges
    -> infer metric/entity/dimension/unit/additivity/planning facts
    -> run assembly validation
    -> freeze registry, graph, sidecar, and index
    -> compute definition_fingerprint
    -> construct SemanticCatalog
```

Any error aborts catalog construction. No partial catalog is returned.

### Compiled state

The catalog owns one immutable internal state:

```python
@dataclass(frozen=True, slots=True)
class CompiledSemanticState:
    definition_fingerprint: str
    selected_roots: tuple[CompiledRootIdentity, ...]
    filtered_domains: tuple[str, ...]
    registry: RegistrySnapshot
    definitions: Mapping[Ref[SemanticKindTag], DefinitionIR]
    dependencies: Mapping[
        Ref[SemanticKindTag],
        tuple[Ref[SemanticKindTag], ...],
    ]
    sidecar: CompiledExpressionSidecar
```

All input dictionaries/lists are copied before being wrapped as immutable
mappings/tuples. Frozen IR values are shared; mutable declaration contexts and
loader lists are discarded.

Runtime services such as datasource connection factories, preview-check store,
and parity store are held in a separate private `CatalogRuntimeServices` value.
They may update runtime evidence, but they cannot replace or mutate compiled
definitions, index, dependencies, sidecar, or fingerprint.

`SemanticCatalog` therefore remains definition-immutable while lifecycle
operations can read or write explicitly scoped evidence.

### Definition fingerprint

`marivo/semantic/_definition_identity.py` encodes one canonical tuple per
definition, sorted by `ref.key`. Each tuple contains:

- structured ref payload;
- kind-specific definition fields;
- ordered structured dependency refs;
- semantic source-root role and configured ordering at the catalog level;
- body AST hash and parameter count when present;
- ordered expression bindings as `(RefPayloadV1, entity_position)`;
- inferred facts that affect planning or semantic meaning.

It excludes relative/absolute source file location, Python symbol spelling,
absolute process object addresses, callables, connection objects, preview age,
parity result, display state, and runtime caches. Source file and symbol remain
catalog provenance, not semantic compatibility input.

Selected roots are encoded once in the whole-catalog envelope as
`CompiledRootIdentity(role, ordinal, declared_key)`. The local root uses the
stable key `local`; external roots use their normalized configured layer key in
declaration order. Root identities affect the whole-catalog fingerprint but are
not injected into an operation's semantic dependency digest.

Canonical bytes use the metric-graph canonical scalar rules rather than
`repr`. The fingerprint is `sha256:<lowercase hex>`.

The exact dependency digest reuses the same per-definition encoder but includes
only the transitive semantic closure reached from the operation's role refs and
runtime-expression leaves. Unrelated definitions cannot affect it.

## Catalog Implementation

### Entry base and ownership

The common entry is:

```python
@dataclass(frozen=True, slots=True, repr=False, eq=False)
class CatalogEntry(Generic[KindT], RenderableResult):
    ref: Ref[KindT]
    _details: CatalogDetails
    _catalog: SemanticCatalog

    @property
    def kind(self) -> SemanticKind:
        return self.ref.kind

    @property
    def path(self) -> str:
        return self.ref.path

    @property
    def key(self) -> str:
        return self.ref.key

    @property
    def name(self) -> str:
        return self.ref.name
```

Equality requires the same concrete entry class, equal ref, and the same
catalog object by identity. Hashing uses the concrete class, owning catalog
identity, and ref. Entries from two loads do not silently become
interchangeable even when their refs and definition fingerprints match.

Details values expose compiled dependency and inferred facts using refs, not
typed string IDs. Source file and Python symbol remain provenance fields.

The concrete classes are:

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

Container entries navigate through their owning catalog only.

### Collections

`CatalogCollection[K]` contains its catalog, exact kind, optional scope ref,
and deterministic tuple of entries.

```python
collection.items
collection.refs
collection.get(local_name)
collection.render()
collection.show()
```

`get` accepts one valid local segment only. A dot, colon, ref, canonical key,
or non-string fails before lookup. Ambiguity is computed within the already
typed/scoped collection and returns exact `catalog.require(ref.<kind>(...))`
alternatives derived from live entries.

There is no `.ids()` because `.refs` is the complete identity projection.

### Global membership

The only global lookup is:

```python
def require(self, ref: Ref[KindT], /) -> CatalogEntry[KindT]:
    ...
```

It requires `type(ref) is Ref`, looks up the exact `(kind, path)` in the frozen
index, and returns the concrete entry. Failure includes the ref, selected domain
filter, catalog definition fingerprint, bounded candidates from the frozen
index, and one exact next call.

There is no non-raising membership API and no containment protocol.

### Loading and reload

`ms.load(...)` is the sole constructor facade. It creates a fresh loader,
compiled state, runtime services value, and catalog. `SemanticCatalog.__init__`
is private-by-convention and not documented as caller API.

`catalog.load()` is deleted. Reload is:

```python
catalog = ms.load(...)
```

An analysis session retains the catalog instance with which it was created or
resumed. Loading another catalog does not update an existing session.

## Semantic Lifecycle

The final methods are:

```python
catalog.verify(ref)
catalog.preview(ref, using=snapshot_or_mapping)
catalog.preview_many(refs, using=snapshot_or_mapping)
catalog.readiness(refs=None)
```

Their concrete behavior is:

| Method | Input | Output | Data effect |
| --- | --- | --- | --- |
| `verify` | one exact ref | `VerifyResult` | query-free |
| `preview` | one exact ref plus `DiscoverySnapshot` binding | `PreviewResult` | bounded explicit read and row-free check persistence |
| `preview_many` | non-empty unique refs plus exact evidence bindings | `PreviewBatchResult` | bounded explicit reads and independent checks |
| `readiness` | `None` or non-empty unique refs | `ReadinessReport` | query-free read of compiled state and persisted evidence |

`PreviewUsing` becomes:

```python
DiscoverySnapshot | Mapping[Ref[EntityKind], DiscoverySnapshot]
```

Entry keys and strings are rejected. The mapping requires exact entity refs
from the active catalog and exact coverage of the required dependency entities.

`ReadinessReport.analysis_ready_refs` and `preview_required_refs` contain
ordinary refs. The report and every issue carry
`catalog_definition_fingerprint`. No authorization token or branded ref is
created.

The preview-check persistence record becomes:

```python
@dataclass(frozen=True, slots=True)
class PreviewCheckV1:
    schema: Literal["marivo.semantic_preview_check/v1"]
    checked_ref: RefPayloadV1
    catalog_definition_fingerprint: str
    semantic_dependency_digest: SemanticDependencyDigestV1
    entity_snapshot_bindings: tuple[EntitySnapshotBindingV1, ...]
    ...
```

The old schema-less `semantic_ref: str` record is unsupported. Datasource
`DiscoverySnapshot` remains the evidence type and is not renamed.

## Resolver and Materializer Boundaries

The resolver exposes only exact internal methods:

```python
table(ref: Ref[EntityKind]) -> ibis.Table
dimension(ref: Ref[DimensionKind | TimeDimensionKind]) -> ir.Value
measure(ref: Ref[MeasureKind]) -> ir.Value
metric(ref: Ref[MetricKind]) -> ir.Value
```

Each method calls `catalog.require(ref)` before accessing private compiled IR.
Wrong-kind refs fail before membership lookup. Missing refs fail with catalog
fingerprint and live candidates. Strings and entries never reach materializer.

After this boundary, registry dictionaries and backend caches may use
`ref.path`. That representation is private implementation state and is never
returned or persisted as semantic identity.

## Analysis Admission and Runtime Metrics

### Exact inputs

Analysis signatures use direct generic forms:

```python
metric: Ref[MetricKind] | RuntimeMetricExpr
dimensions: Sequence[Ref[DimensionKind | TimeDimensionKind]] | None
slice_by: Mapping[
    Ref[DimensionKind | TimeDimensionKind],
    SliceValue,
] | None
time_dimension: Ref[TimeDimensionKind] | None
```

There is no `AnalysisDimensionRef`, `MetricInput`, `DimensionInput`,
`SemanticInput`, or `SemanticAnchorInput` alias.

Admission helpers return validated refs, not normalized strings:

```text
exact runtime class
    -> allowed semantic kind
    -> session.catalog.require(ref)
    -> operator-specific ownership/applicability/planning checks
    -> retain ref through planning
```

Private planners obtain `ref.path` only when addressing an already validated
registry/cache. The unique-suffix dimension repair and catalog-object unwrapping
are removed.

Readiness is not invoked during admission. Membership, kind, and mechanical
planning are runtime constraints; readiness-before-analysis remains workflow
guidance owned by help and the analysis skill.

### Runtime metric expressions

The closed values become:

```python
@dataclass(frozen=True, slots=True)
class RuntimeAggregateExpr:
    measure: Ref[MeasureKind]
    ...

@dataclass(frozen=True, slots=True)
class FrozenSliceMap:
    _items: tuple[
        tuple[Ref[DimensionKind | TimeDimensionKind], FrozenSliceValue],
        ...,
    ]
```

Catalog metric leaves are `Ref[MetricKind]`. Runtime expressions never receive
a semantic ref of their own.

Replay encoding embeds `RefPayloadV1` for metric leaves, measures, and slice
keys. Decoding validates the current structured payload then calls the public
closed runtime-expression constructors with already decoded exact refs.

## Metric Graph Schema

The metric graph v1 is defined only in its structured-ref form.

### Dependency digest

```python
@dataclass(frozen=True, slots=True)
class SemanticDependencyEntryV1:
    ref: RefPayloadV1
    body_digest: str | None
    fields: tuple[CanonicalField, ...] = ()
    bindings: tuple[ExpressionBindingV1, ...] = ()


@dataclass(frozen=True, slots=True)
class SemanticDependencyDigestV1:
    schema: Literal["marivo.semantic_dependency_digest/v1"]
    entries: tuple[SemanticDependencyEntryV1, ...]
    digest: str
```

There is no `semantic_kind`, `semantic_id`, `fingerprint`, or
`semantic-dependency/v1` field/literal in the final record.

### Ref-bearing graph nodes

```python
@dataclass(frozen=True, slots=True)
class CatalogBodyLeafV1:
    kind: Literal["catalog_body_leaf"]
    metric_ref: RefPayloadV1
    dependency_fingerprint: str
    unit_override: str | None = None


@dataclass(frozen=True, slots=True)
class AggregateNodeV1:
    kind: Literal["aggregate"]
    target_ref: RefPayloadV1  # measure or entity
    dependency_fingerprint: str
    agg: AggKind
    fold: AggregateFoldInput
    filter: tuple[CanonicalSliceEntryV1, ...] = ()


@dataclass(frozen=True, slots=True)
class CumulativeNodeV1:
    kind: Literal["cumulative"]
    child_id: str
    time_dimension_ref: RefPayloadV1 | None
    anchor: CumulativeAnchorV1
    dependency_fingerprint: str
```

`CanonicalSliceEntryV1(dimension_ref, value)` is defined in the dependency-neutral
metric-graph module. `SliceNodeV1` stores an ordered tuple of those values; it
never uses dimension IDs as JSON object keys and does not import analysis
persistence types. Catalog metric identities and subjects use
`metric_ref: RefPayloadV1`.

Graph child IDs, node IDs, expression fingerprints, artifact IDs, session IDs,
physical output column names, and backend profile fingerprints remain strings.

Canonical decoding requires exact object keys and schema literals. Old bare
fields are rejected rather than upgraded in memory.

## Role-Preserving Analysis Persistence

### Common records

`marivo/analysis/_semantic_persistence.py` owns:

```python
@dataclass(frozen=True, slots=True)
class AxisBindingV1:
    ref: RefPayloadV1
    column: str
    role: Literal["dimension", "time_dimension"]


@dataclass(frozen=True, slots=True)
class SlicePredicateV1:
    dimension_ref: RefPayloadV1
    value: JsonValue
```

`AxisBindingV1` validates that ref kind matches role.
`SlicePredicateV1` accepts only dimension/time-dimension kinds.

`EntitySnapshotBindingV1` is instead defined in
`marivo/semantic/_persistence.py`, because semantic preview/readiness cannot
depend upward on `marivo.analysis`. It accepts only entity kind. Both modules
depend only on `marivo.refs` for semantic identity.

```python
@dataclass(frozen=True, slots=True)
class EntitySnapshotBindingV1:
    entity_ref: RefPayloadV1
    snapshot_id: str
```

Semantic-keyed maps are always encoded as ordered records sorted by ref key.
Python runtime mappings may remain mappings, but persistence adapters convert
them before serialization and reconstruct them only after ref validation.

### Artifact schema

The destructive cutover bumps persisted frame metadata to
`analysis-artifact/v4`. A metric frame's authoritative semantic fields are:

```text
catalog_definition_fingerprint
semantic_dependency_digest
metric_identity / metric_identities
axis_bindings
slice_predicates
status_time_dimension_ref
component bindings with explicit named ref roles
```

`CatalogMetricIdentity.metric_ref` is structured.
`RuntimeExpressionIdentity` keeps expression schema and fingerprint because a
runtime expression is not a semantic ref.

The old authoritative fields are removed from v4:

```text
metric_id
axes semantic-id maps
where semantic-id maps
status_time_dimension
semantic_model as reconstructed identity
string component metric IDs
```

Human-facing metric path/key fields may be emitted as denormalized display
fields only beside the complete structured identity. Readers ignore them for
reconstruction and verify them when present.

Physical output columns, frame/artifact IDs, content hashes, window timestamps,
units, aggregation names, row counts, and presentation labels remain strings or
scalars because they are not semantic object identity.

### Job schema

Previously unversioned job JSON becomes `marivo.analysis_job/v1`. Each intent
has a typed params payload whose semantic roles are explicit:

```json
{
  "schema": "marivo.analysis_job/v1",
  "intent": "observe",
  "catalog_definition_fingerprint": "sha256:...",
  "semantic_dependency_digest": {
    "schema": "marivo.semantic_dependency_digest/v1",
    "digest": "sha256:...",
    "entries": []
  },
  "subject": {
    "kind": "catalog_metric",
    "metric_ref": {
      "schema": "marivo.semantic_ref/v1",
      "kind": "metric",
      "path": "sales.revenue"
    }
  },
  "dimension_refs": [],
  "slice_predicates": [],
  "time_dimension_ref": null
}
```

Runtime-expression subjects embed the current metric-expression graph identity
instead of fabricating a metric ref.

### Evidence and policy records

`Subject.metric`, `Subject.entity`, `AnalysisScope.metric_ids`, generic
`semantic_anchors`, and semantic string-keyed `slice` maps are replaced with
closed typed subject/scope records.

Catalog metric evidence uses `metric_ref`; runtime expression evidence uses its
expression identity; delta evidence uses its structured current/baseline
subjects. Segment keys are `SlicePredicateV1` records.

Policies contain exact Python refs. When persisted inside a job or artifact,
their serializer emits named metric/axis `RefPayloadV1` fields under the owner
schema. A policy never accepts or stores catalog entries or bare strings.

A flat `semantic_refs` array may exist only as a derived search index. The
writer derives it from role fields. The reader derives the same set, compares
it to the supplied index, and never uses it to infer roles.

## Replay Implementation

Replay is implemented as a typed fail-closed pipeline:

```text
read owner JSON
    -> require current owner schema
    -> validate role-preserving payload structure
    -> decode each RefPayloadV1
    -> load/select active SemanticCatalog
    -> catalog.require every role ref
    -> validate intent kind matrix
    -> reconstruct exact operation dependency closure
    -> recompute semantic dependency digest
    -> compare recorded and active digest
    -> plan and execute
```

The recorded whole-catalog fingerprint is provenance. When it differs but the
exact dependency digest matches, replay continues and records both old and
active fingerprints. When the dependency digest differs, replay stops before
planning backend work.

There is no acknowledgement override. Executing with changed dependencies is a
new exploratory intent and receives new job/artifact identity and lineage.

An unsupported owner schema or old bare semantic identity fails before catalog
loading with `unsupported_persisted_schema`. Its repair is to start a new
analysis session and regenerate artifacts from current refs and evidence.
Marivo does not delete or rewrite old `.marivo/` files.

## Error Implementation

Ref-related errors remain in the owning datasource, semantic, or analysis error
hierarchy. Their structured fields are:

```text
operation
parameter
expected_ref_kinds
received_type
received_key
catalog_definition_fingerprint
candidates
repair
```

`received_key` is optional and only populated when safely available without
coercion. `candidates` are sorted, bounded, and derived from the active frozen
catalog.

Required semantic binding codes are:

```text
binding_context_missing
invalid_binding_ref
binding_alias_not_direct
binding_alias_ambiguous
binding_entity_mismatch
binding_not_declared
binding_target_missing
binding_cycle
binding_result_invalid
```

Required identity/persistence cases include malformed path, wrong kind,
catalog entry passed instead of ref, string passed instead of ref, absent ref,
filtered-domain ref, datasource legacy name, unsupported payload schema, and
dependency digest mismatch.

Errors teach one exact next call but never execute the repair.

## Public Surface and Help

`marivo.semantic.__all__` contains:

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

plus the already selected non-identity authoring values, lifecycle results, and
authoring helpers.

The following identity/catalog names are absent:

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
Relationship as loaded views
SemanticInput
MetricInput
DimensionInput
SemanticAnchorInput
```

`marivo.datasource` exports datasource specs and results but not `Ref`,
`DatasourceRef`, or `ref`. A datasource spec's `.ref` returns the shared exact
value.

Root semantic help teaches:

```text
Authoring returns Ref[kind].
Catalog entries browse and inspect compiled objects.
Semantic and analysis operations consume Ref[kind].
```

Focused help for `Ref` shows exact factories and makes clear that factories
validate identity syntax only. Catalog help shows `entry.ref` and
`catalog.require(ref)`. Expression help teaches
`return ms.bind(amount, order_rows).sum()` and does not mention context
variables, sidecars, resolver attachment, or catalog lookup.

Help, docstrings, errors, capability contracts, canonical specs, latest English
and Chinese site docs, generated API pages, and skills use the same names in one
atomic public change.

## Activation Boundary

The private `Ref`, payload decoder, Pydantic adapter, expression compiler,
binding runtime, canonical definition encoder, and structured metric-graph
records can be implemented and tested without entering public exports.

Before activation, existing public snapshot tests continue to assert the old
surface and no public persisted writer emits the private schemas. In-flight
metric-graph code is re-cut before any fixture or writer makes its bare-string
draft a stable v1.

Activation replaces all of the following together:

```text
authoring return values and parameter annotations
loader pending records and sidecar
materializer expression execution
compiled catalog and lifecycle methods
analysis admission and runtime metric leaves
frame/job/evidence/policy/preview writers and readers
replay
public exports
help, contracts, docs, skills, and snapshots
old class/helper/schema deletion
```

There is no supported commit or release state with both identity families in
`__all__`, both ref payload readers, or both resolver-bearing and context-bound
field refs.

## Verification Contract

The implementation must demonstrate these properties directly:

- every raw `Ref(...)` form and subclass declaration fails;
- exact factories return exact static `Ref[K]` types;
- a typing fixture proves no `Ref[Never]` construction/widening bypass;
- copy/deepcopy return the same value and every supported pickle protocol uses
  the validated restore path;
- Pydantic Python and JSON modes enforce their distinct exact contracts;
- all eight kinds and datasource authoring share the normative path grammar;
- AST binding captures exact field refs and entity positions before assembly;
- variable/parameter renames do not alter fingerprints, while ref/position
  changes do;
- nested field bindings use their own frames, detect cycles, and clean up after
  success and failure;
- concurrent task/catalog evaluations cannot observe another context;
- equal refs resolve independently in distinct immutable catalogs;
- lifecycle arity and `DiscoverySnapshot` bindings are exact;
- analysis rejects entries, strings, generic mappings, and wrong kinds at the
  first public boundary;
- every persisted semantic role uses `RefPayloadV1` under a versioned owner;
- unrelated catalog changes do not block replay when the dependency digest is
  equal;
- changed dependencies, missing refs, wrong kinds, and old schemas stop before
  execution;
- public snapshots contain `Ref` and entry names but no deleted identity names;
- current help, docs, skills, generated API pages, typing, tests, and lint all
  agree with the final contract.

The repository-wide completion gates are:

```bash
make typecheck
make lint
make test
make docs-api
npm --prefix site run verify:content
npm --prefix site run build
```

## Final Implemented Invariant

```text
Definition IR stores compiled meaning privately.
ExpressionBody stores a callable plus declared binding metadata privately.
SemanticCatalog owns one immutable compiled interpretation.
CatalogEntry exposes that interpretation for browsing.
Ref carries only kind and path across every public and persisted boundary.
```

The catalog records execution authority and dependency facts. The ref never
stores them.
