# Tier-2 Semantic Expression Reference Binding Design

Status: accepted; implementation pending

Date: 2026-07-19

Revision policy: destructive cutover. Resolver-bearing refs and loader mutation
have no compatibility weight. The existing field-call spelling is retained
because it is the lowest-cognitive-cost authoring form.

This document is the accepted companion prerequisite required by
[`2026-07-19-unified-semantic-object-reference-design.md`](2026-07-19-unified-semantic-object-reference-design.md).
It defines how a pure unified ref applies a semantic field inside dimension,
time-dimension, measure, and Tier-2 metric bodies.

## Summary

Semantic refs remain immutable catalog-free identities. Field-kind refs retain
one context-bound expression operation:

```python
@ms.measure(entity=orders, additivity="additive", unit="USD")
def amount(order_rows):
    return order_rows.amount


@ms.metric(entities=[orders], additivity="additive", unit="USD")
def revenue(order_rows):
    return amount(order_rows).sum()
```

Calling `amount(order_rows)` does not read a resolver stored on `amount`.
Instead, the semantic materializer installs one private catalog-bound
`ContextVar` immediately around body execution. `Ref.__call__` delegates to
that context, which resolves the referenced field sidecar and applies it to the
exact positional entity alias. The context is always reset in `finally`.

The complete model is:

```text
authoring AST records one exact field-ref-to-alias binding
    -> loader compiles binding metadata into the expression sidecar
    -> materializer enters one catalog-bound expression context
    -> field_ref(entity_alias) applies the referenced sidecar body
    -> context is reset before control returns to caller code
```

## Selected Decisions

- `field_ref(entity_alias)` is the only semantic field-ref application syntax.
- Callability is available only to static field kinds: dimension,
  time-dimension, and measure.
- Metric, entity, domain, datasource, and relationship refs reject calls.
- `@ms.metric(entities=[...])` continues to inject raw Ibis tables positionally
  and in declaration order. Parameter names never establish identity.
- Direct physical-column expressions such as `orders.amount` and
  `orders["schema"]` remain unchanged.
- A ref contains only `(kind, path)`. It contains no resolver, callable,
  catalog, readiness state, source location, or mutable field.
- The active expression runtime is private task-local state implemented with a
  `ContextVar` and token-reset in `finally`.
- Every field call is validated and recorded before catalog compilation.
  Runtime evaluation cannot introduce an undeclared dependency.
- The expression sidecar records deterministic binding metadata in addition to
  its private Python callable.
- Definition fingerprints and semantic dependency digests include exact bound
  refs and entity positions, never callable identity or parameter spelling.
- The current `_FieldRef._resolver` slot and loader mutation are deleted in the
  same atomic cutover.

## Why Context-Bound Callability Is Acceptable

The rejected design treated any method on `Ref` as identity/execution mixing.
That boundary was too strict for an agent-facing authoring DSL. The harmful
coupling is stored execution authority, not a context-checked value operation.

After this cutover:

- copying, hashing, pickling, serializing, or constructing an equal ref never
  copies or depends on execution state;
- a ref cannot retain a catalog or make one catalog win over another;
- calling a ref outside a registered semantic body fails before any effect;
- the same ref value resolves against the exact catalog whose materializer is
  evaluating the current body;
- static typing restricts the call self-type to field kinds;
- runtime checks reject every non-field kind.

This keeps identity semantics pure while preserving the existing minimal
authoring spelling.

## Goals

- Preserve every committed single- and multi-entity Tier-2 body capability.
- Preserve the lowest-cognitive-cost `amount(order_rows)` spelling.
- Remove all mutable and catalog-bound state from refs.
- Make field dependencies available to catalog fingerprints, readiness,
  materialization, and replay before execution.
- Keep authoring bodies inside the restricted Ibis expression track.
- Prevent execution context from leaking across tasks, errors, or sessions.

## Non-goals

- This design does not create a general public resolver or `ms.bind(...)` API.
- It does not make non-field refs executable.
- It does not add operator overloading beyond the field application call.
- It does not replace or wrap Ibis tables.
- It does not allow analysis scripts to execute authoring bodies.
- It does not infer semantic fields from physical column names.
- It does not permit dynamic field selection inside restricted bodies.
- It does not change body-free metric composition APIs.

## Ref Call Contract

The single unified runtime class declares a restricted self-type:

```python
def __call__(
    self: Ref[DimensionKind | TimeDimensionKind | MeasureKind],
    entity_alias: ibis.expr.types.Table,
) -> ibis.expr.types.Value:
    ...
```

This is not a second ref subtype or alias. Static typing rejects calls on known
`Ref[MetricKind]`, `Ref[EntityKind]`, and other non-field refs. Runtime checks
remain mandatory because Python callers may erase or widen generic precision.

The call accepts exactly one positional entity alias and no keywords. That
retains the established spelling and keeps AST analysis closed.

### Valid shapes

```python
@ms.measure(entity=orders, additivity="additive", unit="USD")
def net_amount(order_rows):
    return gross_amount(order_rows) - order_rows.discount


@ms.metric(entities=[orders], additivity="additive", unit="USD")
def revenue(order_rows):
    return net_amount(order_rows).sum()


@ms.metric(
    entities=[orders, customers],
    root_entity=orders,
    fanout_policy="aggregate_then_join",
    additivity="additive",
)
def vip_revenue(order_rows, customer_rows):
    return (amount(order_rows) * vip_weight(customer_rows)).sum()
```

Renaming Python variables or parameters changes neither semantic identity nor
binding identity. Compiled metadata stores the resolved ref and zero-based
entity position.

### Rejected shapes

```python
revenue(order_rows)                    # metric is not a field kind
orders(order_rows)                     # entity is not a field kind
Ref.measure("sales.orders.amount")()   # missing direct entity alias
select_field()(order_rows)             # dynamic field selection
amount(order_rows.filter(...))         # not a direct body alias
amount(unrelated_local)                # not a body parameter
amount(order_rows, other_rows)         # wrong arity
amount(table=order_rows)                # keywords are not accepted
```

Calling any ref outside registered semantic body evaluation fails. It never
guesses the current project or most recently loaded catalog.

## Declaration-Time Binding Analysis

### Closed AST shape

The single-return validator recognizes a field application only as:

```text
Call(
    func=Name(<field_symbol>),
    args=[Name(<body_parameter>)],
    keywords=[],
)
```

The implementation resolves `<field_symbol>` from the function's globals or
closure cells at decoration time without arbitrary attribute traversal or user
code execution. The concrete value must have exact type `Ref` and a field kind.
`<body_parameter>` must be one direct positional parameter.

The validator rejects dynamic callees, aliases whose value is not an exact ref,
derived alias expressions, keyword calls, extra arguments, and field calls that
cannot be resolved deterministically. Ordinary Ibis method calls remain owned
by the existing AST whitelist.

### Positional identity

The decorator owns the ordered entity refs and maps each positional parameter
to its entity position. Compiled metadata is:

```text
ExpressionBindingV1
    field_ref: RefPayloadV1
    entity_position: int
```

Assembly requires the field's owning entity to equal the entity ref at that
position. Sharing a datasource or physical table does not make two semantic
entities interchangeable.

Bindings are ordered by first AST occurrence and deduplicated by
`(field_ref, entity_position)`. Reusing a field call does not create duplicate
dependency edges.

### Expression sidecar

The current callable-only sidecar becomes one private immutable record per
expression-bearing object:

```text
ExpressionBody
    callable                 private process-local function
    body_ast_hash            stable source-derived identity
    parameter_count          validated positional arity
    bindings                 tuple[ExpressionBindingV1, ...]
```

The callable is excluded from canonical serialization. The body hash and
bindings enter the compiled definition fingerprint and exact dependency digest.
Catalog entries may show bindings as compiled facts but never expose callables.

## Runtime Binding

### Private context

Before a body is called, the materializer installs one immutable context:

```text
ExpressionBindingContext
    catalog_definition_fingerprint
    expression_sidecar
    body_frames

ExpressionBodyFrame
    owning_ref
    ordered_entity_refs
    ordered_entity_aliases
    declared_bindings
```

The context is installed with a `ContextVar` token and reset in `finally` for
dimension, time-dimension, measure, and metric evaluation, including preview,
parity, verification probes, and analysis materialization.

`body_frames` is one immutable stack. The root body starts with one frame. A
nested field call first validates against the current frame, then pushes a new
frame for the referenced field body with that field as `owning_ref`, its owning
entity as the sole ordered entity ref, the selected Ibis alias as the sole
ordered alias, and the referenced body's own declared bindings. The nested body
therefore validates its calls against its own compiled metadata rather than the
outer metric's bindings.

Re-entry of an `owning_ref` already present in `body_frames` fails with a
deterministic cycle error. Every nested frame is popped in `finally`; the root
context token is reset in `finally` as well.

No context exists while authoring modules merely declare objects. A module-level
field call therefore fails before any query or project mutation.

### Runtime validation order

`field_ref(entity_alias)` checks:

1. the concrete receiver type is exactly `Ref`;
2. the receiver kind is dimension, time dimension, or measure;
3. an active loader-owned expression context exists;
4. the argument is exactly one direct alias supplied to the active body;
5. the `(field_ref, alias position)` pair is declared in compiled metadata;
6. the active catalog contains the field ref;
7. the field owner equals the entity at that alias position;
8. the referenced expression sidecar exists;
9. the body-frame stack is acyclic;
10. the referenced body returns one allowed Ibis value.

These checks compile expressions only. They do not execute a backend query.

### Alias identity

The runtime compares the argument by Python object identity against the exact
ordered table arguments. It never uses Ibis equality or physical table names.
If one object occupies several entity positions, the call is ambiguous and
fails. A filtered, projected, or joined table is not a direct alias and is
rejected; callers apply the field before transforming its returned value.

## Dependency and Replay Contract

Every field call adds one typed dependency edge from the owning expression to
the field ref at one entity position. The transitive closure includes the
field's own definition and bindings plus entity, relationship, and datasource
dependencies reached through it.

The canonical encoder includes `RefPayloadV1` plus the integer entity position.
It excludes function parameter names, Python variable names, callables,
context state, and object addresses.

Changing a bound field, its alias position, or its definition changes the
semantic dependency digest. Renaming variables or parameters without changing
the resolved ref and position does not.

Replay consumes compiled binding metadata. It never imports authoring modules
to rediscover missing dependencies.

## Errors

Required structured failures are:

- `binding_context_missing` for a field call outside body evaluation;
- `invalid_binding_ref` for a non-field ref;
- `binding_alias_not_direct` for a non-parameter expression;
- `binding_alias_ambiguous` when one object occupies several positions;
- `binding_entity_mismatch` for the wrong semantic entity;
- `binding_not_declared` for a runtime call absent from compiled metadata;
- `binding_target_missing` for a missing catalog object or sidecar;
- `binding_cycle` when the selected ref already owns a frame in the active body
  stack;
- `binding_result_invalid` for a non-Ibis result.

Each error includes the owning ref when available and teaches one valid field
call. No error imports modules, selects candidates, mutates refs, or retries.

## Help and Agent Contract

Root and focused help retain one form:

```python
return amount(order_rows).sum()
```

Help distinguishes semantic field application from direct physical-column
access, states that only field-kind refs are callable, and explains that calls
work only inside registered semantic bodies. It never exposes `ms.bind`,
resolver attachment, sidecars, or catalog lookup as authoring concepts.

## Internal Ownership

```text
marivo.refs
    sealed Ref, SemanticKind, restricted context-bound __call__ delegation

marivo.semantic authoring/validator
    AST extraction and positional binding validation

marivo.semantic loader/compiler
    immutable ExpressionBody creation, ownership validation, dependency edges

marivo.semantic materializer
    private ContextVar lifecycle, body-frame stack, alias mapping, nested
    sidecar execution

marivo.semantic catalog/readiness
    compiled binding facts and dependency-closure participation

marivo.analysis/replay
    consume compiled definitions and digests; never call field refs directly
```

## Breaking Deletions

The cutover removes:

- kind-specific ref subclasses and `_FieldRef`;
- `_resolver` slots and loader mutation of pending refs;
- the loader's `_make_field_resolver` bridge;
- callable-only unstructured sidecars;
- arbitrary name-call acceptance without exact ref/alias resolution;
- dynamic field dependencies absent from compiled metadata;
- errors that describe a ref as a decorator.

The call spelling remains, but its implementation and guarantees are replaced
atomically. There is no compatibility resolver path.

## Landing Order

1. land the unified private `Ref`, structured payload, expression binding
   records, and context runtime;
2. re-cut metric-graph dependencies on structured refs and bindings;
3. validate all body families and concurrency/error cleanup privately;
4. atomically publish the unified surface and delete old ref/resolver families;
5. update help, generated docs, latest bilingual docs, skills, and snapshots;
6. run typing, behavior, docs, lint, and full regression gates.

Before the atomic public switch, the new identity and binding implementation
remain absent from public help and exports.

## Acceptance Criteria

- One concrete `Ref` class represents all kinds and contains no resolver state.
- `field_ref(entity_alias)` remains the only field application spelling.
- Static typing restricts calls to field-kind refs.
- Runtime checks reject every non-field kind and non-exact ref.
- Single-entity dimension, time-dimension, measure, and metric bodies execute.
- Multi-entity bodies preserve entity order independent of parameter names.
- Wrong-entity, transformed-alias, ambiguous-alias, metric-ref,
  missing-context, missing-target, cycle, and invalid-result cases fail with
  structured repair.
- Nested calls work without mutating refs or global catalog state.
- Concurrent evaluations in different tasks/catalogs cannot observe each
  other's contexts.
- Every root context token and nested body frame resets after success and every
  error.
- AST validation records exact field refs plus alias positions before compile.
- Definition fingerprints and dependency digests change for binding changes but
  not variable or parameter renames.
- Catalog details show deterministic dependencies without exposing callables.
- Preview, parity, readiness, analysis materialization, and replay use the same
  binding runtime.
- `_resolver`, loader ref mutation, old ref subclasses, and callable-only
  sidecars are absent from runtime, help, current docs, and tests.

## Final Contract

`Ref` says which semantic object.

`entities=[...]` says which entity aliases a body receives and in what order.

`field_ref(entity_alias)` applies one declared field to one exact positional
entity alias through the current loader-owned expression context.

The catalog records the dependency; the ref never stores the executor.
