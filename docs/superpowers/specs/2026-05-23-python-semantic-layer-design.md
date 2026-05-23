# Python Semantic Layer (semantic_py) Design

Status: design draft (2026-05-23)

## Background

Marivo currently exposes semantic-model definition through OSI YAML/JSON files plus
a SQLite-backed metadata store (`marivo/runtime/semantic/`). The OSI track keeps
canonical entities (`SemanticModel`, `Dataset`, `Field`, `Metric`, `Relationship`)
with multi-dialect SQL expressions and MARIVO custom extensions for routing
(`datasource_id`), time-field metadata, and metric decomposition semantics.

This design adds a parallel Python-native semantic layer (`marivo.semantic_py`)
intended for agent-first usage: agents (especially general-purpose agents such as
Claude Code) write Python files that use decorators plus native `ibis` expressions
to declare semantic models. The two tracks coexist; the OSI path is untouched and
no migration/conversion path is built in v1.

## Scope

In scope:

- A Python API (`marivo.semantic_py`) for declaring `SemanticModel`, `Dataset`,
  `Field`, `Metric`, `Relationship` aligned to the OSI core spec capabilities and
  MARIVO custom extensions (`osi-marivo-spec/schema/osi-marivo.schema.json`).
- A convention-based discovery and lazy-load mechanism for project-local model
  files.
- An in-memory registry plus read API consumable by analysis runtime callers.
- Materialization of metric/field functions into executable ibis expressions
  given a caller-injected backend.

Out of scope:

- Replacing or migrating the OSI/SQLite track.
- Persisting Python-track definitions to SQLite or any other store.
- Bidirectional conversion between the two tracks.
- File-change watching and automatic reload (manual `reload()` only).
- MCP transport adapters for the Python track (will follow in a later design).

## Design Principles

1. `.py` files are the source of truth. Re-running marivo with the same files
   reproduces the same registry.
2. The two tracks are siloed. Callers explicitly select which track to use per
   session/request. The OSI track sees no changes.
3. Agent-first ergonomics. Function-and-decorator patterns chosen for highest
   match with LLM training data; explicit error reporting with structured fields
   and English messages.
4. `ibis` provides the single expression language for the Python track. No
   multi-dialect concept on this track; dialect translation happens automatically
   at materialization time via the caller-provided backend.
5. The registry is pure metadata. Backend connections are caller-injected at
   materialization time; the registry holds no live connections.

## Non-goals

- Cross-track `SemanticModel.name` uniqueness checks. The two registries are
  independent.
- Inline `ExpressionComponent` for metric decomposition components
  (`numerator`/`denominator`/`weight`). All decomposition components must be
  function references to other registered metrics.
- Cross-dataset field references. A field function may only reference its own
  dataset (its single argument). Metrics, by contrast, may declare multiple
  datasets in their function signature and may reference other metrics that
  span different datasets within the same model.
- Local variable assignment, control flow, or imports inside metric/field
  function bodies. Single-return ibis expression style only.
- Derived datasets that depend on other datasets via function injection.
  A dataset function only receives the backend.

## Module Layout

New subpackage `marivo/semantic_py/` (does not touch the existing
`marivo/runtime/semantic/` tree):

```text
marivo/semantic_py/
├── __init__.py        # re-exports the public surface as `marivo.semantic_py`
├── decorators.py      # @ms.dataset / @ms.field / @ms.time_field / @ms.metric / @ms.relationship
├── builders.py        # ms.model(...), ms.sum(), ms.ratio(), ms.weighted_average()
├── registry.py        # process-level _REGISTRY singleton and the IR dataclasses
├── ir.py              # ModelIR / DatasetIR / FieldIR / MetricIR / RelationshipIR / DecompositionIR
├── loader.py          # convention directory scan + importlib import
├── validator.py       # decorator-time + assembly-time validators
├── reader.py          # the read API consumed by analysis runtime callers
└── testing.py         # scoped_registry context manager for unit tests
```

Public surface available as `import marivo.semantic_py as ms`.

## Discovery and Load Lifecycle

### Convention directory

Default: `<project_root>/.marivo/semantic/`.

Override: `MARIVO_MODELS_DIR=<path>` environment variable.

Layout:

```text
.marivo/semantic/
├── <model_name>/
│   ├── __init__.py     # may be empty
│   ├── _model.py       # contains exactly one ms.model(...) call
│   ├── datasets.py
│   ├── fields.py
│   ├── metrics.py
│   └── relationships.py
└── <other_model>/
    └── ...
```

The subdirectory name is the model boundary. Every dataset / field / metric /
relationship declared in a subdirectory belongs to the model declared in that
subdirectory's `_model.py`. File names within a subdirectory are not significant;
the loader scans recursively.

### Lazy load

The registry starts in state `"unloaded"`. The first call to any
`reader.*` function (or an explicit `reader.ensure_loaded()`) transitions it to
`"loading"`, runs the loader, and transitions to `"ready"` or `"errored"`.

```text
unloaded → (ensure_loaded) → loading → ready
                              ↘ errored
ready → (reload) → loading → ...
errored → (reload) → loading → ...
```

Concurrent `ensure_loaded` calls during `"loading"` block until completion.

### Load sequence

1. Resolve the semantic root directory.
2. Walk subdirectories. For each subdirectory:
   a. Verify exactly one `_model.py` exists.
   b. `importlib.import_module` every `.py` file in the subtree.
   c. Decorator side effects populate the registry as imports execute.
3. After all imports complete, run `validator.validate_all(_REGISTRY)`:
   string references resolve to registered entities, time-field metadata
   constraints hold, metric decomposition references hit registered metrics,
   relationship endpoints exist, no cyclic metric references.
4. On success, set state to `"ready"`. On any failure, set `"errored"` and
   raise an aggregated `SemanticLoadError` listing all individual errors.

### Manual reload

`reader.reload()` clears `_REGISTRY` and re-runs the load sequence. Documented
as development-only. The reader contract does not guarantee in-flight
materializations survive a `reload()` call.

## Decorator API Surface

### `ms.model(...)` — module-level call

```python
ms.model(
    name="sales",
    description="Sales analytics semantic model",
    ai_context={"instructions": "...", "synonyms": ["transactions"]},
)
```

A module-level call (not a decorator). Must appear exactly once in
`.marivo/semantic/<model_name>/_model.py`. The loader asserts
`name == <model_name>` (subdirectory name).

### `@ms.dataset(...)` — Dataset declaration

```python
@ms.dataset(
    name="orders",
    datasource_id="warehouse_main",
    primary_key=["order_id"],
    unique_keys=[["order_id"], ["external_ref"]],
    description="Order events",
    ai_context={"synonyms": ["transactions"]},
)
def orders(backend):
    return backend.table("prod.orders")
```

- `name` defaults to the function name when omitted.
- `datasource_id` is required (MARIVO routing identifier).
- `primary_key` / `unique_keys` optional.
- The function body **must** return an `ibis.Table`. The body's purpose is to
  build the dataset's ibis Table view from the backend. Simple cases just call
  `backend.table("schema.table")`; derived views may apply filter / project /
  cast operations on that table.
- Single function argument representing the backend. The argument name has no
  semantic meaning (typically `backend`). This differs from field / metric
  functions, where argument names must match registered dataset names.
- No `source=` kwarg. The physical reference is embedded in the function body.

### `@ms.field(...)` — non-time Field declaration

```python
@ms.field(
    dataset="orders",
    name="region",
    label="dimension",
    description="Order region (normalized)",
    ai_context=None,
)
def region(orders):
    return orders.region.upper()
```

- `dataset` is a string reference to a registered dataset name; validated at
  assembly time.
- `name` defaults to the function name when omitted.
- Function takes exactly one argument: the dataset (injected as ibis Table at
  materialization time). The argument name must match the registered dataset
  name.
- Function body returns a row-level ibis expression (no aggregates).
- No time-field kwargs allowed on `@ms.field`. Time fields use
  `@ms.time_field`.

### `@ms.time_field(...)` — time Field declaration

```python
@ms.time_field(
    dataset="orders",
    name="order_date",
    data_type="date",
    granularity="day",
    description="Order date",
)
def order_date(orders):
    return orders.created_at.cast("date")
```

- `data_type` required, one of `date | timestamp | string | integer`.
- `granularity` required, one of `hour | day | week | month | quarter | year`.
- `format` required when `data_type ∈ {string, integer}`; otherwise optional.
  Accepts any string (no enum restriction).
- `required_prefix` required when `format ∈ {hh, h}`; otherwise forbidden.
  Value is a **function reference** to another time-field decorated function
  on the same dataset that provides date context.

Example with `required_prefix`:

```python
@ms.time_field(dataset="orders", data_type="date", granularity="day")
def order_date(orders):
    return orders.created_at.cast("date")

@ms.time_field(
    dataset="orders",
    data_type="integer",
    granularity="hour",
    format="hh",
    required_prefix=order_date,
)
def order_hour(orders):
    return orders.hour_of_day
```

### `@ms.metric(...)` — Metric declaration

```python
@ms.metric(
    name="revenue",
    decomposition=ms.sum(),
    description="Total revenue in USD",
    ai_context=None,
)
def revenue(orders):
    return orders.amount.sum()
```

- `name` defaults to function name.
- `decomposition` is required. No default. Value is a `DecompositionSpec`
  produced by one of the factories in the next section.
- Model membership is inferred from the file's directory location
  (`<root>/<model_name>/...`); no `model=` kwarg required.
- Function takes the datasets the metric uses as arguments (one or more).
  Argument names must match registered dataset names within the same model.
- Function body returns an ibis aggregate expression. The expression must
  include at least one aggregation operation.

### `@ms.relationship(...)` — Relationship declaration

```python
@ms.relationship(
    name="orders_to_users",
    from_="orders",
    to="users",
    from_columns=["user_id"],
    to_columns=["user_id"],
)
def orders_to_users():
    ...
```

- `from_` (Python reserved-word friendly), `to`, `from_columns`, `to_columns`
  required.
- Function body is empty (`...` or `pass`). Relationship is pure metadata.
- Endpoints validated at assembly time.

## Decomposition Semantics

Three factories produce `DecompositionSpec` markers.

### `ms.sum()`

Additive quantity. No components.

```python
@ms.metric(decomposition=ms.sum())
def revenue(orders): ...
```

### `ms.ratio(numerator=..., denominator=...)`

Proportion or rate.

```python
@ms.metric(
    decomposition=ms.ratio(numerator=converted_users, denominator=total_users),
)
def conversion_rate(orders): ...
```

- `numerator` and `denominator` are **function references** to other
  `@ms.metric`-decorated functions in the same model.
- Inline expression components (`ExpressionComponent` in OSI terms) are
  **not supported** in v1.

### `ms.weighted_average(numerator=..., weight=...)`

Ratio-of-sums.

```python
@ms.metric(
    decomposition=ms.weighted_average(numerator=revenue, weight=total_users),
)
def arpu(orders): ...
```

Same constraints as `ms.ratio`.

### Cross-metric calls in function bodies

A `@ms.metric`-decorated function's wrapper preserves the original function as
callable. Calling it from another metric's body returns the ibis expression
inline:

```python
@ms.metric(decomposition=ms.sum())
def total_users(orders):
    return orders.user_id.nunique()

@ms.metric(decomposition=ms.sum())
def converted_users(orders):
    return orders.filter(orders.status == "completed").user_id.nunique()

@ms.metric(decomposition=ms.ratio(numerator=converted_users, denominator=total_users))
def conversion_rate(orders):
    return converted_users(orders) / total_users(orders)
```

The wrapper additionally exposes `__marivo_metric__` carrying the registered
IR entry. The decomposition factories use that attribute to extract the
referenced metric's name.

## Function Body Conventions and AST Whitelist

Validation walks the function body AST at decoration time. The allowed node
set depends on the decorator.

### Common allowed nodes (all expression-bearing decorators)

- `Name`, `Attribute`, `Constant`
- `BinOp`, `UnaryOp`, `BoolOp`, `Compare`
- `Call` — callee must be: a method on an injected argument's expression chain,
  an `ibis` module function (whitelisted prefix), or another registered metric
  function
- `IfExp` (ternary)
- `List`, `Tuple`, `Dict` literals
- `Subscript` — allowed only in `table[<string literal>]` form for ibis column access

### Common forbidden nodes

- `Import`, `ImportFrom`
- `FunctionDef`, `ClassDef`, `Lambda`
- `Assign`, `AugAssign`, `AnnAssign` (no local variables — single-return style)
- `If`, `For`, `While`, `Try`, `With`, `Raise`
- `Global`, `Nonlocal`, `Yield`, `Await`

### `@ms.dataset` body extension

Additionally allows calls on the injected backend object:
`backend.table(...)`, `backend.sql(...)`, and other ibis backend methods.
Also allows `ibis._` deferred references and `ibis.literal(...)`.

### `@ms.metric` body extension

Additionally allows calls into other registered metric functions (resolved at
assembly time, validated as carrying `__marivo_metric__`).

### `@ms.field` / `@ms.time_field` body restrictions

Strict row-level mode: aggregation calls (`.sum()`, `.nunique()`, etc.) are
forbidden. The validator inspects called method names against a known ibis
aggregation set.

## Validation Phases

### Phase 1 — decorator time (per declaration)

Triggered as each decorator executes during import.

Checks:

- Function signature: argument names match Python identifier rules; argument
  count matches decorator type.
- Function body AST conforms to the applicable whitelist.
- Decorator kwargs: types, required fields, mutual exclusions
  (notably the `@ms.time_field` `data_type ↔ format ↔ required_prefix` matrix).
- `decomposition` argument is a `DecompositionSpec` instance.
- `decomposition` component values carry `__marivo_metric__` (i.e., are
  decorated metric functions).
- `required_prefix` value carries `__marivo_time_field__` (decorated time
  field function).

Errors raise immediately, halting import:

```python
class SemanticDecoratorError(SemanticError):
    phase: Literal["decorator"]
    kind: str                # e.g. "AstNodeForbidden", "DecompositionRefInvalid"
    location: SourceLocation # file + line
    function: str
    message: str             # English
    hint: str | None
    refs: list[str]
```

### Phase 2 — assembly time (per model, after all imports)

Triggered after the loader finishes importing all files.

Checks:

- Every `dataset=` string reference resolves to a registered dataset in the
  same model.
- Every metric function argument name matches a registered dataset in the
  same model.
- Every field function argument name matches the field's declared dataset.
- Every relationship endpoint (`from_`, `to`) is a registered dataset.
- `from_columns` and `to_columns` reference columns derivable from each
  dataset (via fields or known physical columns from the dataset's ibis Table;
  v1 only checks that the names are syntactically valid since physical columns
  are resolved at materialization).
- No cyclic metric reference graph (metric → decomposition component → metric).
- `required_prefix` references resolve to time fields on the same dataset
  whose `format` is a complete date or timestamp pattern (not `hh`/`h`).

Errors aggregate into `SemanticLoadError(errors: list[SemanticError])`.

### Phase 3 — runtime (at materialization)

Triggered when a `materialize_*` call executes.

Checks:

- The `backend_factory` returns a working ibis backend for each
  `datasource_id`.
- Calling a dataset function returns an `ibis.Table`.
- Calling a field/metric function returns an ibis expression of the expected
  shape (row-level vs aggregate).
- ibis raises its own type / column errors; the materializer catches and wraps
  them as `SemanticRuntimeError`.

## In-memory Registry and IR

### Singleton

`marivo/semantic_py/registry.py` holds:

```python
_REGISTRY: PySemanticRegistry  # process-wide singleton

@dataclass
class PySemanticRegistry:
    models: dict[str, ModelIR]
    state: Literal["unloaded", "loading", "ready", "errored"]
    load_errors: list[SemanticError]
```

### IR types

```python
@dataclass
class ModelIR:
    name: str
    description: str | None
    ai_context: dict | None
    datasets: dict[str, DatasetIR]
    relationships: dict[str, RelationshipIR]
    metrics: dict[str, MetricIR]
    source_files: list[str]

@dataclass
class DatasetIR:
    name: str
    fn: Callable                          # original function, fn(backend) -> ibis.Table
    datasource_id: str
    primary_key: list[str]
    unique_keys: list[list[str]]
    fields: dict[str, FieldIR]
    description: str | None
    ai_context: dict | None
    source_location: SourceLocation

@dataclass
class FieldIR:
    name: str
    dataset_name: str
    fn: Callable                          # fn(dataset_table) -> ibis.Expr (row-level)
    is_time: bool
    time_meta: TimeFieldMeta | None
    label: str | None
    description: str | None
    ai_context: dict | None
    source_location: SourceLocation

@dataclass
class TimeFieldMeta:
    data_type: Literal["date", "timestamp", "string", "integer"]
    granularity: Literal["hour", "day", "week", "month", "quarter", "year"]
    format: str | None
    required_prefix: str | None           # field name (resolved from function ref)

@dataclass
class MetricIR:
    name: str
    model_name: str
    fn: Callable                          # fn(*datasets) -> ibis.Expr (aggregate)
    decomposition: DecompositionIR
    description: str | None
    ai_context: dict | None
    references: MetricReferences
    source_location: SourceLocation

@dataclass
class DecompositionIR:
    kind: Literal["sum", "ratio", "weighted_average"]
    numerator: str | None                 # metric_name
    denominator: str | None
    weight: str | None

@dataclass
class MetricReferences:
    datasets: list[str]                   # extracted from fn signature
    metrics: list[str]                    # extracted from fn body AST + decomposition
    fields: list[str]                     # extracted from fn body AST

@dataclass
class RelationshipIR:
    name: str
    from_dataset: str
    to_dataset: str
    from_columns: list[str]
    to_columns: list[str]
    source_location: SourceLocation

@dataclass
class SourceLocation:
    file: str
    line: int
```

The IR stores `fn` callables rather than pre-compiled ibis expressions so that
backend binding happens lazily at materialization. The registry remains
backend-agnostic.

## Read API

`marivo/semantic_py/reader.py` exposes:

```python
def ensure_loaded() -> None
def reload() -> None

def list_models() -> list[ModelSummary]
def get_model(name: str) -> ModelIR

def list_datasets(model: str) -> list[DatasetSummary]
def get_dataset(model: str, name: str) -> DatasetIR

def list_metrics(model: str) -> list[MetricSummary]
def get_metric(model: str, name: str) -> MetricIR

def list_fields(model: str, dataset: str) -> list[FieldSummary]
def get_field(model: str, dataset: str, name: str) -> FieldIR

def list_relationships(model: str) -> list[RelationshipSummary]
def get_relationship(model: str, name: str) -> RelationshipIR

def materialize_dataset(
    model: str,
    dataset: str,
    backend_factory: Callable[[str], ibis.BaseBackend],
) -> ibis.Table

def materialize_field(
    model: str,
    dataset: str,
    field: str,
    backend_factory: Callable[[str], ibis.BaseBackend],
) -> ibis.Expr

def materialize_metric(
    model: str,
    metric: str,
    backend_factory: Callable[[str], ibis.BaseBackend],
) -> ibis.Expr

def diagnostics() -> RegistryDiagnostics
```

Lookup functions raise structured `PySemanticNotFound` errors when entities
are missing.

## Materialization Flow

`materialize_metric("sales", "conversion_rate", backend_factory)` proceeds as
follows:

1. `ensure_loaded()` if not already loaded.
2. Look up `MetricIR` via `registry.metrics["conversion_rate"]`.
3. Build the dataset dependency set from `MetricIR.references.datasets`.
4. For each dataset id:
   a. Resolve `DatasetIR.datasource_id`.
   b. Call `backend = backend_factory(datasource_id)`; cache per
      `datasource_id` within this materialization scope.
   c. Call `dataset_ir.fn(backend)` to obtain the `ibis.Table`.
   d. Cache the resulting table per dataset name within this scope.
5. Call `MetricIR.fn(**dataset_tables)`. The function body may invoke other
   metric functions; those are simply Python function calls that compose into
   the ibis op tree (because `@ms.metric` wrappers preserve the original
   function's callability).
6. Return the resulting `ibis.Expr`.

Backend caching is **per-materialization-scope only**. The registry never
holds connections across calls.

## Backend Binding

Callers are responsible for providing `backend_factory`. The contract:

```python
def backend_factory(datasource_id: str) -> ibis.BaseBackend: ...
```

The returned backend must:

- Have `.table(name)` resolving the dataset functions' physical references.
- Be safe to use throughout the materialization call (the registry does not
  outlive the call).
- Be managed (connect / disconnect / pool) by the caller.

Marivo's analysis runtime is expected to maintain a `datasource_id → backend`
registry separately and pass an appropriate factory in. That registry is out
of scope for this design.

## Testing Pattern

`marivo/semantic_py/testing.py` exposes:

```python
@contextmanager
def scoped_registry() -> Iterator[PySemanticRegistry]:
    """Temporarily replace the module-level _REGISTRY with a fresh one.

    Inside the block, decorator side effects populate the scoped registry
    instead of the global one. On exit, the previous registry is restored.
    """
```

Usage:

```python
from marivo.semantic_py import testing
import marivo.semantic_py as ms

def test_revenue_metric():
    with testing.scoped_registry() as reg:
        @ms.dataset(name="orders", datasource_id="t")
        def orders(backend):
            return backend.table("orders")

        @ms.metric(decomposition=ms.sum())
        def revenue(orders):
            return orders.amount.sum()

        # Manually trigger assembly validation (no convention dir scan)
        reg.assemble()

        expr = reg.materialize_metric("default", "revenue", mock_factory)
        assert expr.to_pandas().iloc[0, 0] == ...
```

The `scoped_registry` context bypasses convention-directory loading; entities
are registered directly via decorator calls inside the block. For models that
need to span the OSI track or use the real loader, separate tests should
exercise the loader against a fixture directory.

## Dual-track Coexistence

The OSI/SQLite track and the Python track operate independently:

- The Python track has its own in-memory registry; it never reads from or
  writes to the SQLite metadata store.
- The OSI track is unmodified.
- Callers (analysis runtime, MCP transports, etc.) explicitly select the track
  per session/request. v1 introduces an explicit `semantic_track: "osi" |
  "python"` selector wherever a semantic model is referenced.
- No name uniqueness is enforced between tracks. The two are siloed.
- No automatic fallback or merging.

How the analysis runtime wires the selector is out of scope for this design;
this design only defines what the Python track offers and what its surface
looks like.

## Worked Example

`<project_root>/.marivo/semantic/sales/_model.py`:

```python
import marivo.semantic_py as ms

ms.model(
    name="sales",
    description="Sales analytics semantic model",
    ai_context={
        "instructions": "Use for revenue, conversion, and unit economics.",
        "synonyms": ["revenue model", "transactions"],
    },
)
```

`<project_root>/.marivo/semantic/sales/datasets.py`:

```python
import marivo.semantic_py as ms

@ms.dataset(
    name="orders",
    datasource_id="warehouse_main",
    primary_key=["order_id"],
    description="Order events",
)
def orders(backend):
    return backend.table("prod.orders")

@ms.dataset(
    name="users",
    datasource_id="warehouse_main",
    primary_key=["user_id"],
)
def users(backend):
    return backend.table("prod.users")
```

`<project_root>/.marivo/semantic/sales/fields.py`:

```python
import marivo.semantic_py as ms

@ms.time_field(
    dataset="orders",
    data_type="date",
    granularity="day",
    description="Order date",
)
def order_date(orders):
    return orders.created_at.cast("date")

@ms.field(dataset="orders", description="Region (normalized)")
def region(orders):
    return orders.region.upper()

@ms.field(dataset="orders", description="Conversion flag")
def is_converted(orders):
    return orders.status == "completed"
```

`<project_root>/.marivo/semantic/sales/metrics.py`:

```python
import marivo.semantic_py as ms

@ms.metric(decomposition=ms.sum(), description="Total revenue (USD)")
def revenue(orders):
    return orders.amount.sum()

@ms.metric(decomposition=ms.sum(), description="Distinct users")
def total_users(orders):
    return orders.user_id.nunique()

@ms.metric(decomposition=ms.sum(), description="Distinct converted users")
def converted_users(orders):
    return orders.filter(orders.status == "completed").user_id.nunique()

@ms.metric(
    decomposition=ms.ratio(numerator=converted_users, denominator=total_users),
    description="Conversion rate",
)
def conversion_rate(orders):
    return converted_users(orders) / total_users(orders)

@ms.metric(
    decomposition=ms.weighted_average(numerator=revenue, weight=total_users),
    description="ARPU (revenue per user)",
)
def arpu(orders):
    return revenue(orders) / total_users(orders)
```

`<project_root>/.marivo/semantic/sales/relationships.py`:

```python
import marivo.semantic_py as ms

@ms.relationship(
    name="orders_to_users",
    from_="orders",
    to="users",
    from_columns=["user_id"],
    to_columns=["user_id"],
)
def orders_to_users():
    ...
```

Caller usage:

```python
import ibis
from marivo.semantic_py import reader

def make_backend(datasource_id: str) -> ibis.BaseBackend:
    config = my_datasource_registry.get(datasource_id)
    return ibis.connect(config.connection_string)

reader.ensure_loaded()

expr = reader.materialize_metric(
    model="sales",
    metric="conversion_rate",
    backend_factory=make_backend,
)
df = expr.to_pandas()
```

## v1 Boundaries (explicitly out)

| Item | Rationale |
|---|---|
| OSI ↔ Python track conversion / projection | Dual-track independence; conversion is a separate design |
| Persistence of Python-track definitions | `.py` is canonical |
| File-change watching / auto-reload | Manual `reload()` only |
| Cross-model metric references | Models are closed boundaries |
| Multiple semantic root directories | `MARIVO_MODELS_DIR` is a single path |
| `ms.expr(lambda)` inline expression components | Component refs must be metric functions |
| AST local variables / control flow / lambdas | Single-return ibis style |
| Auto-projection of ibis Expr to OSI multi-dialect SQL | Dual-track independence |
| Cross-track `SemanticModel.name` conflict checks | Two registries are independent |
| MCP transport adapters for the Python track | Separate design |
| Derived datasets depending on other datasets | Dataset fn takes backend only |
| Cross-dataset field references | Field fn restricted to its own dataset |

## Implementation Order (rough)

Not a plan; a hint for the subsequent writing-plans pass.

1. IR dataclasses + registry singleton (`ir.py`, `registry.py`).
2. Decorator implementations and their decorator-time validators
   (`decorators.py`, `builders.py`, `validator.py` phase 1).
3. Loader with convention scan (`loader.py`) and assembly-time validator
   (`validator.py` phase 2).
4. Read API and materialization flow (`reader.py`).
5. Testing helpers (`testing.py`).
6. Documentation under `docs/specs/semantic/` covering the public API surface.

## Open Questions

None blocking; design decisions are listed in "v1 Boundaries". Items that
become relevant in v2 (MCP adapter, OSI conversion, multi-root discovery,
derived datasets, broader AST surface) are explicitly deferred.
