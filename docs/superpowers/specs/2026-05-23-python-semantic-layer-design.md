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

- A Python API (`marivo.semantic_py`) for declaring `Datasource`, `SemanticModel`,
  `Dataset`, `Field`, `Metric`, `Relationship` aligned to the OSI core spec
  capabilities and MARIVO custom extensions (`osi-marivo-spec/schema/osi-marivo.schema.json`).
  `Datasource` is a new Python-track concept that gives the MARIVO
  `datasource_id` extension a first-class declared identity so datasets can
  bind to it via function reference (instead of free-form strings).
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
5. The registry is metadata-first. Backend connections are produced by
   registered `@ms.datasource` functions (the default path) and cached
   in the registry for the process lifetime; callers may pass a
   `backend_factory` override per materialization for tests or
   environment-specific routing. `reader.reload()` invalidates both
   the IR registry and the cached backends.
6. **Business-definition provenance is first-class.** When a metric/field is
   migrated from existing SQL knowledge bases, the original SQL and its
   natural-language definition are stored alongside the ibis expression. This
   preserves the audit chain and, more importantly, gives future agent
   maintenance an anchor against silent semantic drift as the ibis expression
   evolves over time.
7. **A curated helper primitive set bridges SQL idioms to ibis.** LLMs read
   SQL more reliably than they recall ibis API. The Python track ships a
   small starter set of helpers (`ms.count_if`, `ms.sum_if`, etc.) that map
   common SQL business-definition patterns to ibis without making the agent
   guess the right idiom.

## Non-goals

- Cross-track `SemanticModel.name` uniqueness checks. The two registries are
  independent.
- Inline `ExpressionComponent` for metric decomposition components
  (`numerator`/`denominator`/`weight`). All decomposition components must be
  function references to other registered metrics.
- Cross-dataset field references. A field function may only reference its own
  dataset (its single argument). Field bodies cannot call other field
  functions or metric functions.
- Metrics, by contrast, are model-level. A metric function may declare
  multiple datasets in its signature, call field functions from any dataset
  in the same model, and call other metric functions in the same model.
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
│   ├── __init__.py        # may be empty
│   ├── _model.py          # contains exactly one ms.model(...) call
│   ├── datasources.py     # @ms.datasource declarations
│   ├── datasets.py
│   ├── fields.py
│   ├── keys.py            # ms.set_keys(...) calls (primary / unique keys via field refs)
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

### `@ms.datasource(...)` — Datasource declaration

```python
import ibis
import os
import marivo.semantic_py as ms

@ms.datasource(
    name="warehouse_main",
    description="Main analytics warehouse",
    ai_context=None,
)
def warehouse_main():
    return ibis.duckdb.connect(os.environ["WAREHOUSE_PATH"])
```

- `name` defaults to the function name when omitted.
- **Function body returns an `ibis.BaseBackend`** by calling an ibis connect
  factory (`ibis.duckdb.connect(...)`, `ibis.connect("mysql://...")`,
  `ibis.snowflake.connect(...)`, etc.). This is the same idiom an ibis user
  would write outside marivo, so agents migrating from raw ibis code
  recognize it immediately.
- The fn is called lazily at materialization time (see Materialization Flow)
  and the returned backend is cached for the process. `reader.reload()`
  invalidates the cache.
- Credentials should not be hardcoded in fn bodies. Use `os.environ[...]`,
  `os.getenv(...)`, or a project-local config helper. Marivo does not provide
  a credential manager in v1; this is a project conventions concern.
- The wrapper carries `__marivo_datasource__` so other decorators
  (notably `@ms.dataset`) can take it as a function reference.

Examples for other backends:

```python
@ms.datasource(name="warehouse_mysql")
def warehouse_mysql():
    return ibis.connect(os.environ["MYSQL_DSN"])

@ms.datasource(name="warehouse_snowflake")
def warehouse_snowflake():
    return ibis.snowflake.connect(
        account=os.environ["SNOWFLAKE_ACCOUNT"],
        user=os.environ["SNOWFLAKE_USER"],
        password=os.environ["SNOWFLAKE_PASSWORD"],
        warehouse=os.environ["SNOWFLAKE_WAREHOUSE"],
        database=os.environ["SNOWFLAKE_DATABASE"],
    )
```

### `@ms.dataset(...)` — Dataset declaration

```python
@ms.dataset(
    name="orders",
    datasource=warehouse_main,            # function reference (no string)
    description="Order events",
    ai_context={"synonyms": ["transactions"]},
)
def orders(backend):
    return backend.table("prod.orders")
```

- `name` defaults to the function name when omitted.
- `datasource` is required and **must be a function reference** to a
  `@ms.datasource`-decorated function in the same model. String references
  are not accepted (the decorator validates the value carries
  `__marivo_datasource__`).
- **Primary and unique keys are declared separately via `ms.set_keys(...)`**
  after the relevant fields are decorated (see below). They are not kwargs
  on `@ms.dataset`. This separation is required because PK / UK reference
  fields, and `@ms.field` cannot run before the dataset it binds to exists.
  Declaring keys after fields lets PK / UK use function references rather
  than typo-prone strings.
- The function body **must** return an `ibis.Table`. The body's purpose is to
  build the dataset's ibis Table view from the backend. Simple cases just call
  `backend.table("schema.table")`; derived views may apply filter / project /
  cast operations on that table.
- Single function argument representing the backend. The argument name has no
  semantic meaning (typically `backend`). This differs from field / metric
  functions, where argument names must match registered dataset names.
- No `source=` kwarg. The physical reference is embedded in the function body.

### `ms.set_keys(...)` — Primary / unique key declarations

```python
from .datasets import orders
from .fields import order_id, tenant_id, external_ref

ms.set_keys(
    orders,
    primary=[order_id],                       # single-column PK
    unique=[
        [order_id],                            # single-column unique key
        [external_ref],
    ],
)

# Composite primary key:
ms.set_keys(
    orders,
    primary=[tenant_id, order_id],            # composite PK
)
```

A module-level call (not a decorator). Lives in `keys.py` by convention but
may appear in any `.py` under the model directory; what matters is that the
referenced fields are imported at the time of the call.

- First positional argument: a `@ms.dataset` function reference. Validated
  to carry `__marivo_dataset__`.
- `primary`: a list of `@ms.field` / `@ms.time_field` function references.
  Length 1 for single-column PK, length >1 for composite. May be omitted.
- `unique`: a list of lists, each inner list a unique-key definition
  (length 1 or more for composite). May be omitted.
- All field references must belong to the same dataset as the first
  argument. Validated at call time using `__marivo_field__.dataset_name`.
- Calling `ms.set_keys` more than once on the same dataset raises
  `DuplicateKeysError`. To change keys, edit the single declaration and
  reload.
- PK / UK on raw physical columns that the agent has not exposed as a
  `@ms.field` are not supported. The user must declare a trivial field
  (`def order_id(orders): return orders.order_id`) before referencing it
  in `set_keys`. This is by design: forces the field surface to enumerate
  the columns that participate in the dataset's identity contract.

### `@ms.field(...)` — non-time Field declaration

```python
from .datasets import orders

@ms.field(
    dataset=orders,                       # function reference to a registered dataset
    name="region",
    is_dimension=True,
    label="region_classification",
    description="Order region (normalized)",
    ai_context=None,
)
def region(orders):
    return orders.region.upper()
```

- `dataset` is a **function reference** to a `@ms.dataset`-decorated
  function in the same model. The decorator validates the value carries
  `__marivo_dataset__`; string references are not accepted.
- `name` defaults to the function name when omitted.
- `is_dimension: bool = True` — declares whether this field is a dimension
  (groupable / filterable). Default `True` since most declared fields are
  dimensions. When `False`, marivo emits no OSI `dimension` block for this
  field (consistent with OSI semantics: presence of `dimension` ⇒ field is
  a dimension).
- `label: str | None` — optional free-form classification tag, mapped to
  OSI `Field.label`. Independent of `is_dimension`.
- Function takes exactly one argument: the dataset (injected as ibis Table at
  materialization time). The argument name must match the registered dataset
  name (i.e., the `name=` of the referenced datasource, or the function name
  if `name=` was omitted).
- Function body returns a row-level ibis expression (no aggregates).
- No time-field kwargs allowed on `@ms.field`. Time fields use
  `@ms.time_field`.

### `@ms.time_field(...)` — time Field declaration

```python
from .datasets import orders

@ms.time_field(
    dataset=orders,                       # function reference to a registered dataset
    name="order_date",
    data_type="date",
    granularity="day",
    label=None,
    description="Order date",
)
def order_date(orders):
    return orders.created_at.cast("date")
```

- `dataset` is a **function reference** to a `@ms.dataset`-decorated function
  in the same model (same rule as `@ms.field`).
- `data_type` required, one of `date | timestamp | string | integer`.
- `granularity` required, one of `hour | day | week | month | quarter | year`.
- `format` required when `data_type ∈ {string, integer}`; otherwise optional.
  Accepts any string (no enum restriction).
- `required_prefix` required when `format ∈ {hh, h}`; otherwise forbidden.
  Value is a **function reference** to another time-field decorated function
  on the same dataset that provides date context.
- `is_dimension` is **implicitly `True`** for time fields (OSI requires
  `dimension.is_time = true` ⇒ `dimension` is present). The kwarg is not
  exposed; passing it raises a decoration-time error.
- `label: str | None` — optional free-form classification tag.

Example with `required_prefix`:

```python
from .datasets import orders

@ms.time_field(dataset=orders, data_type="date", granularity="day")
def order_date(orders):
    return orders.created_at.cast("date")

@ms.time_field(
    dataset=orders,
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
from .datasets import orders, users

@ms.relationship(
    name="orders_to_users",
    from_=orders,                         # function reference to dataset (many side)
    to=users,                             # function reference to dataset (one side)
    from_columns=["user_id"],             # string column names
    to_columns=["user_id"],
)
def orders_to_users():
    ...
```

- `from_` (Python reserved-word friendly) and `to` are **function references**
  to `@ms.dataset`-decorated functions in the same model. The decorator
  validates the values carry `__marivo_dataset__`.
- `from_columns`, `to_columns` remain string lists of physical column names.
  These reference columns on the dataset's ibis Table; validated at
  assembly time (soft check against declared fields on the dataset) and at
  materialize time (hard check against actual ibis Table columns).
- Function body is empty (`...` or `pass`). Relationship is pure metadata.

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

### Cross-references in metric bodies

`@ms.metric`, `@ms.field`, and `@ms.time_field` decorators each return a
wrapper that preserves the original function as callable. From inside a
metric body the agent may:

- Call another metric function:

  ```python
  @ms.metric(decomposition=ms.ratio(numerator=converted_users, denominator=total_users))
  def conversion_rate(orders):
      return converted_users(orders) / total_users(orders)
  ```

- Call a field or time-field function (from any dataset declared in the
  metric's signature):

  ```python
  @ms.metric(decomposition=ms.sum())
  def revenue_north(orders):
      return orders.filter(region(orders) == "NORTH").amount.sum()
  ```

In each case the call returns an ibis expression that composes into the
metric's op tree inline.

The wrappers additionally expose `__marivo_metric__`, `__marivo_field__`, or
`__marivo_time_field__` attributes carrying the registered IR entry. The
decomposition factories use `__marivo_metric__` to extract referenced metric
names; the validator uses these attributes to detect cross-references during
AST analysis.

## Provenance Fields

All expression-bearing decorators (`@ms.dataset`, `@ms.field`, `@ms.time_field`,
`@ms.metric`) accept two optional provenance kwargs:

```python
@ms.metric(
    decomposition=ms.sum(),
    source_sql="""
    SUM(CASE WHEN pay_status = 1 THEN pay_amount ELSE 0 END)
    """,
    source_definition="GMV of paid orders; excludes refunds and test accounts.",
)
def revenue(orders):
    return ms.sum_if(orders.pay_amount, orders.pay_status == 1)
```

### `source_sql: str | None`

Raw SQL text from the originating business-definition knowledge base. Single
string; no multi-dialect structure in v1. Purpose:

- Audit anchor (which SQL did this Python definition originally encode).
- Drift detector (future agents diff this against the current ibis expression
  to spot accidental semantic changes).
- Optional input to a future parity-check workflow (see "Migration Workflow"
  below).

Not used by the runtime materialization path. Stored only as metadata.

### `source_definition: str | None`

Natural-language definition of what the entity measures. Stored as metadata.
Agents reading the model find this faster than parsing either SQL or ibis
when they need to understand business intent.

### Why not `source_doc` (URL / KB reference)

Deliberately excluded in v1. External document references are unstable
(links rot, KB platforms migrate, paths reorganize). The two fields above
travel with the code; a URL field would degrade silently.

### Provenance on the IR

Every `DatasetIR` / `FieldIR` / `MetricIR` carries a `provenance: Provenance`
field (canonical dataclass definition in the IR section below).

The `parity_*` fields are v1-contract placeholders; the parity-check engine
that fills them ships in v1.5 or later (see "Migration Workflow"). v1 always
records `parity_status="unverified"` or `"n/a"` (when no `source_sql` is
provided).

## Helper Primitives

The Python track ships a curated set of helper functions that wrap common
SQL business-definition idioms in ibis. These are not new semantic concepts;
they exist purely to reduce agent error rate when translating SQL knowledge
bases.

### v1 starter set

| Helper | SQL idiom it maps from | Returns |
|---|---|---|
| `ms.count_if(condition)` | `COUNT(CASE WHEN cond THEN 1 END)`, Trino `count_if` | aggregate ibis expr |
| `ms.sum_if(value, condition)` | `SUM(CASE WHEN cond THEN value ELSE 0 END)` | aggregate ibis expr |
| `ms.safe_divide(num, denom, default=0)` | `num / NULLIF(denom, 0)` | row-level ibis expr |
| `ms.approx_distinct(col)` | Trino `approx_distinct`, BigQuery `APPROX_COUNT_DISTINCT` | aggregate ibis expr |
| `ms.coalesce(*exprs)` | SQL `COALESCE(a, b, c, ...)` | row-level ibis expr |

Each helper is a thin function that returns an ibis expression equivalent to
its mapped SQL form. The validator treats helper calls as opaque ibis
expressions (no special AST rules).

### Boundary: what's explicitly out of the starter set

- `ms.case(...)`: not added. `ibis.cases(...)` already covers this with no
  ambiguity; a marivo alias would only duplicate the API surface.
- `ms.date_parse` / `ms.time_bucket`: not added in v1. These overlap
  conceptually with `@ms.time_field`'s `format` / `granularity` kwargs; the
  responsibility split must be designed before adding either.

### Extension mechanism

The helper set grows by observed need, not speculation. A new helper is
added when a real business KB pattern would otherwise force the agent to
write a non-obvious ibis idiom. Adding helpers does not require IR or
validator changes — they are simple Python functions in
`marivo/semantic_py/helpers.py`.

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

### `@ms.datasource` body extension

`@ms.datasource` fn bodies have a different role from the expression-bearing
decorators: they build a backend connection rather than an ibis expression.
The whitelist is relaxed to permit common connect-config patterns:

- `Call` to `ibis` namespace functions (`ibis.duckdb.connect`,
  `ibis.connect`, `ibis.snowflake.connect`, etc.).
- `Call` to `os.environ` / `os.getenv` for credential and config lookup.
- `Subscript` for dict-style config access (e.g., `config["url"]`,
  `os.environ["WAREHOUSE_PATH"]`).
- `Attribute` access through the ibis module (`ibis.duckdb.connect`).
- All standard literal nodes (string interpolation via `f"..."` is
  allowed at this whitelist level as a `JoinedStr` node).

Still forbidden:

- `Import` / `ImportFrom` inside the function body (use module-level imports).
- File / network / subprocess calls beyond ibis and os.environ.
- `Assign` inside the body (single-return style retained for consistency
  with the rest of the spec).

### `@ms.dataset` body extension

Additionally allows calls on the injected backend object:
`backend.table(...)`, `backend.sql(...)`, and other ibis backend methods.
Also allows `ibis._` deferred references and `ibis.literal(...)`.

### `@ms.metric` body extension

Additionally allows calls into:

- Other registered metric functions in the same model (carrying
  `__marivo_metric__`).
- Registered field / time-field functions from any dataset declared in the
  metric's argument list, plus any field belonging to other datasets in the
  same model (carrying `__marivo_field__` / `__marivo_time_field__`).

These calls compose into the ibis op tree inline, since the wrapper preserves
the original function callability.

### `@ms.field` / `@ms.time_field` body restrictions

Strict row-level, single-dataset mode:

- Aggregation calls (`.sum()`, `.nunique()`, etc.) are forbidden. The validator
  inspects called method names against a known ibis aggregation set.
- Calls into other field / time-field / metric wrappers are forbidden.
  A field body operates only on its single injected dataset argument with
  ibis row-level operations.

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
- `@ms.dataset`'s `datasource` argument carries `__marivo_datasource__`
  (decorated datasource function).
- `@ms.field` / `@ms.time_field`'s `dataset` argument carries
  `__marivo_dataset__` (decorated dataset function).
- `@ms.relationship`'s `from_` and `to` arguments carry `__marivo_dataset__`.
- `ms.set_keys(...)` is a module-level call (not a decorator) but runs the
  same checks: the dataset positional argument carries `__marivo_dataset__`;
  every field in `primary=[...]` and in each `unique=[[...], ...]` inner
  list carries `__marivo_field__` and its `dataset_name` matches the
  target dataset; calling `ms.set_keys` twice on the same dataset raises
  `DuplicateKeysError`.

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

- Every `dataset=` function reference (on `@ms.field` / `@ms.time_field`)
  and every `from_=` / `to=` function reference (on `@ms.relationship`)
  resolves to a registered dataset in the same model.
- Every dataset's `datasource` function reference resolves to a registered
  datasource in the same model.
- Every metric function argument name matches a registered dataset in the
  same model.
- Every field function argument name matches the field's declared dataset
  (i.e., the dataset name resolved from the `dataset=` function reference).
- `from_columns` and `to_columns` (string column lists on relationships)
  reference columns derivable from each relationship endpoint dataset. v1
  performs a soft check at assembly time: if the column name matches a
  declared field name on the same dataset, the reference is confirmed.
  Names that do not match a declared field are permitted (they may be raw
  physical columns) but produce a lint-level note. Hard validation against
  actual ibis Table columns happens at materialization.
- `primary_key` and `unique_keys` are populated by `ms.set_keys(...)` calls
  using field function references; the validator confirms at call time
  that each field reference belongs to the target dataset and carries
  `__marivo_field__`. By the time phase 2 runs, the structure is already
  resolved to field-name strings; no additional check is needed.
- No cyclic metric reference graph (metric → decomposition component → metric).
- `required_prefix` references resolve to time fields on the same dataset
  whose `format` is a complete date or timestamp pattern (not `hh`/`h`).
- Metric body AST may call `@ms.field` / `@ms.time_field` / `@ms.metric`
  functions; all such calls must resolve to entries in the same model.
- Field body AST must not call field / metric / time-field wrappers
  (pure ibis ops on its single dataset argument only).

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
    _backend_cache: dict[str, ibis.BaseBackend]
    # ^ keyed by datasource_name; populated lazily by the default
    # materialization path. reader.reload() clears this dict and
    # attempts .disconnect() on each backend.
```

### IR types

```python
@dataclass
class ModelIR:
    name: str
    description: str | None
    ai_context: dict | None
    datasources: dict[str, DatasourceIR]
    datasets: dict[str, DatasetIR]
    relationships: dict[str, RelationshipIR]
    metrics: dict[str, MetricIR]
    source_files: list[str]

@dataclass
class DatasourceIR:
    name: str
    fn: Callable                          # fn() -> ibis.BaseBackend
    description: str | None
    ai_context: dict | None
    source_location: SourceLocation

@dataclass
class DatasetIR:
    name: str
    fn: Callable                          # original function, fn(backend) -> ibis.Table
    datasource_name: str                  # resolved from datasource function ref
    primary_key: list[str]                # field names resolved from ms.set_keys(primary=...)
    unique_keys: list[list[str]]          # composite key field-name lists, from ms.set_keys(unique=...)
    fields: dict[str, FieldIR]
    description: str | None
    ai_context: dict | None
    provenance: Provenance
    source_location: SourceLocation

@dataclass
class FieldIR:
    name: str
    dataset_name: str                     # resolved from dataset function ref
    fn: Callable                          # fn(dataset_table) -> ibis.Expr (row-level)
    is_dimension: bool                    # False permitted for measure-shaped fields;
                                          # implicitly True when is_time=True
    is_time: bool
    time_meta: TimeFieldMeta | None
    label: str | None
    description: str | None
    ai_context: dict | None
    provenance: Provenance
    source_location: SourceLocation

@dataclass
class TimeFieldMeta:
    data_type: Literal["date", "timestamp", "string", "integer"]
    granularity: Literal["hour", "day", "week", "month", "quarter", "year"]
    format: str | None
    required_prefix: str | None           # field name (resolved from function ref)

@dataclass
class Provenance:
    """Business-definition provenance metadata. v1 records source_sql and
    source_definition at decoration time; parity_* fields are filled in by
    the parity-check engine (v1.5+, see 'Migration Workflow')."""
    source_sql: str | None = None
    source_definition: str | None = None
    parity_status: Literal["unverified", "passed", "failed", "n/a"] = "unverified"
    parity_metadata: dict | None = None
    last_parity_check_at: datetime | None = None

@dataclass
class MetricIR:
    name: str
    model_name: str
    fn: Callable                          # fn(*datasets) -> ibis.Expr (aggregate)
    decomposition: DecompositionIR
    description: str | None
    ai_context: dict | None
    references: MetricReferences
    provenance: Provenance
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
    from_dataset: str                     # resolved from from_ function ref
    to_dataset: str                       # resolved from to function ref
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

def list_datasources(model: str) -> list[DatasourceSummary]
def get_datasource(model: str, name: str) -> DatasourceIR

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
    backend_factory: Callable[[str], ibis.BaseBackend] | None = None,
) -> ibis.Table

def materialize_field(
    model: str,
    dataset: str,
    field: str,
    backend_factory: Callable[[str], ibis.BaseBackend] | None = None,
) -> ibis.Expr

def materialize_metric(
    model: str,
    metric: str,
    backend_factory: Callable[[str], ibis.BaseBackend] | None = None,
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
   a. Resolve `DatasetIR.datasource_name`.
   b. Obtain the backend:
      - If the caller passed a `backend_factory` override, call
        `backend_factory(datasource_name)`.
      - Otherwise look up `DatasourceIR.fn` for that name and call it.
        Results are cached in `_REGISTRY._backend_cache[datasource_name]`
        for the process lifetime; subsequent calls reuse the same backend
        instance.
   c. Call `dataset_ir.fn(backend)` to obtain the `ibis.Table`.
   d. Cache the resulting table per dataset name within this materialization
      scope.
5. Call `MetricIR.fn(**dataset_tables)`. The function body may invoke other
   metric functions and field / time-field functions in the same model;
   those are Python function calls that compose into the ibis op tree
   (`@ms.metric`, `@ms.field`, and `@ms.time_field` wrappers all preserve
   the original function's callability). The caller of a field function
   passes the dataset table explicitly (e.g. `region(orders)`), so no
   implicit injection is performed inside the wrapper.
6. Return the resulting `ibis.Expr`.

Backend caching is **per-materialization-scope only**. The registry never
holds connections across calls.

## Backend Binding

The Python track supports two ways to bind a `datasource_name` to a live
`ibis.BaseBackend`:

### Default: registered datasource function

When the caller passes no `backend_factory`, marivo resolves each
`datasource_name` by calling its registered `@ms.datasource` function. This
is the typical path:

- The fn is called once per process per datasource (lazy).
- The returned backend is cached in the registry's `_backend_cache`.
- `reader.reload()` invalidates the cache. Marivo attempts to call
  `.disconnect()` on cached backends before discarding them; failures are
  logged but do not block reload.

This path puts connection config in the same place as the model definition,
which is what ibis users expect.

### Override: caller-injected factory

For tests, multi-environment routing, connection pool integration, or when
credentials cannot live in the model directory, the caller can pass a
`backend_factory` to any `materialize_*` call:

```python
def backend_factory(datasource_name: str) -> ibis.BaseBackend: ...

reader.materialize_metric(
    model="sales",
    metric="revenue",
    backend_factory=my_test_factory,
)
```

When `backend_factory` is provided, it is consulted **instead of** the
registered datasource fn. The argument is the datasource name as declared
by `@ms.datasource(name=...)` and resolved through each dataset's
`datasource=` function reference.

Constraints on the returned backend (both paths):

- Have `.table(name)` resolving the dataset functions' physical references.
- Be safe to use across multiple materializations.
- Be managed (connect / disconnect / pool) by either the registered fn or
  the caller, depending on the path.

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

`<project_root>/.marivo/semantic/sales/datasources.py`:

```python
import os
import ibis
import marivo.semantic_py as ms

@ms.datasource(name="warehouse_main", description="Main analytics warehouse")
def warehouse_main():
    return ibis.duckdb.connect(os.environ["WAREHOUSE_PATH"])
```

`<project_root>/.marivo/semantic/sales/datasets.py`:

```python
import marivo.semantic_py as ms
from .datasources import warehouse_main

@ms.dataset(
    name="orders",
    datasource=warehouse_main,
    description="Order events",
)
def orders(backend):
    return backend.table("prod.orders")

@ms.dataset(
    name="users",
    datasource=warehouse_main,
)
def users(backend):
    return backend.table("prod.users")
```

`<project_root>/.marivo/semantic/sales/fields.py`:

```python
import marivo.semantic_py as ms
from .datasets import orders, users

@ms.time_field(
    dataset=orders,
    data_type="date",
    granularity="day",
    description="Order date",
)
def order_date(orders):
    return orders.created_at.cast("date")

@ms.field(dataset=orders, description="Region (normalized)")
def region(orders):
    return orders.region.upper()

@ms.field(dataset=orders, description="Conversion flag")
def is_converted(orders):
    return orders.status == "completed"

# Trivial raw-column fields, declared so they can be referenced by set_keys
# and relationship column lists.
@ms.field(dataset=orders, is_dimension=False, description="Order id (raw)")
def order_id(orders):
    return orders.order_id

@ms.field(dataset=orders, is_dimension=False, description="User id on orders")
def orders_user_id(orders):
    return orders.user_id

@ms.field(dataset=users, is_dimension=False, description="User id (raw)")
def users_user_id(users):
    return users.user_id
```

`<project_root>/.marivo/semantic/sales/keys.py`:

```python
import marivo.semantic_py as ms
from .datasets import orders, users
from .fields import order_id, users_user_id

ms.set_keys(orders, primary=[order_id])
ms.set_keys(users, primary=[users_user_id])
```

`<project_root>/.marivo/semantic/sales/metrics.py`:

```python
import marivo.semantic_py as ms
from .fields import region

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
    decomposition=ms.sum(),
    description="Revenue from completed orders only",
    source_sql="SUM(CASE WHEN status = 'completed' THEN amount ELSE 0 END)",
    source_definition="GMV restricted to orders whose status reached 'completed'.",
)
def completed_revenue(orders):
    # Helper primitive maps the SQL CASE-WHEN-SUM idiom to ibis
    return ms.sum_if(orders.amount, orders.status == "completed")

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

@ms.metric(decomposition=ms.sum(), description="Revenue in North region")
def revenue_north(orders):
    # Metric body calls a field function (region) to reuse its logic
    return orders.filter(region(orders) == "NORTH").amount.sum()
```

`<project_root>/.marivo/semantic/sales/relationships.py`:

```python
import marivo.semantic_py as ms
from .datasets import orders, users

@ms.relationship(
    name="orders_to_users",
    from_=orders,
    to=users,
    from_columns=["user_id"],
    to_columns=["user_id"],
)
def orders_to_users():
    ...
```

Caller usage — default path (uses registered datasource fns):

```python
from marivo.semantic_py import reader

reader.ensure_loaded()

# No backend_factory: marivo calls @ms.datasource fns, caches the backends.
expr = reader.materialize_metric(model="sales", metric="conversion_rate")
df = expr.to_pandas()
```

Caller usage — override path (e.g., tests or environment-specific routing):

```python
import ibis
from marivo.semantic_py import reader

def test_backend_factory(name: str) -> ibis.BaseBackend:
    # Route all datasources to an in-memory DuckDB seeded with test fixtures
    return get_test_duckdb_for(name)

expr = reader.materialize_metric(
    model="sales",
    metric="conversion_rate",
    backend_factory=test_backend_factory,
)
df = expr.to_pandas()
```

## Migration Workflow (forward-looking; v1.5+)

This section is a forward-looking statement, not a v1 deliverable. v1 lays
the structural foundation (provenance fields on every IR, helper primitives,
parity_status placeholder). The full workflow ships incrementally.

### The risk this addresses

Converting business-definition SQL knowledge bases into Python/ibis is the
highest-risk activity an agent can perform on the Python track. SQL → ibis
translation has well-known silent-failure modes: `count_if` vs filter+count,
NULL semantics in aggregations, integer division behavior, dialect-specific
date functions, timezone handling, approximation operators. The wrong
translation typically still produces a number — just the wrong one. Without
a parity safeguard, accumulated agent edits over months produce silent
business-metric drift no one can attribute to a specific change.

### Intended workflow

```text
1. Read source: existing OSI metric or raw business SQL.
2. Emit Python skeleton:
   - source_sql pre-filled
   - source_definition pre-filled (from OSI description or KB blurb)
   - fn body stub with the closest ibis translation marivo can offer
3. Agent fills / corrects the fn body using ibis + helper primitives.
4. Parity check: run both the original SQL and the new ibis expression
   against a shared sample, compare aggregate results within tolerance.
5. Save: provenance.parity_status set to "passed" or "failed" with metadata.
```

### What v1 provides toward this

- `source_sql` / `source_definition` kwargs on every expression-bearing
  decorator (described above).
- Helper primitives that cover high-frequency SQL idioms (`ms.count_if`,
  `ms.sum_if`, `ms.safe_divide`, `ms.approx_distinct`, `ms.coalesce`).
- `Provenance.parity_status` and `parity_metadata` fields in the IR
  (always `"unverified"` or `"n/a"` in v1).

### What is explicitly deferred

- The parity-check execution engine (sample harness, dual-backend
  execution, tolerance comparison).
- A `marivo.semantic_py.migration.from_osi_metric(...)` entry point that
  generates Python skeletons from OSI definitions.
- MCP / CLI tools that drive the migration workflow end-to-end.

These ship in v1.5+ once the v1 surface stabilizes. Locking the IR fields
and provenance kwargs now avoids a contract break later.

## v1 Boundaries (explicitly out)

| Item | Rationale |
|---|---|
| OSI → Python automatic conversion entry point | Deferred to Migration Workflow (v1.5+); v1 provides the receiving surface only |
| Parity-check execution engine | Deferred to Migration Workflow (v1.5+); v1 only locks IR contract |
| `source_doc` URL / KB reference field | Excluded; external doc references are unstable |
| Built-in credential manager | Out of scope; `@ms.datasource` fn bodies read from `os.environ` / project config helpers |
| Connection pool / lifecycle manager | Registered fns return whatever ibis backend they want; pool integration is the caller's concern (use `backend_factory` override) |
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

1. IR dataclasses + registry singleton (`ir.py`, `registry.py`), including
   `DatasourceIR` and the `Provenance` dataclass (with `parity_status`
   placeholder fields).
2. Helper primitives starter set (`helpers.py`): `ms.count_if`, `ms.sum_if`,
   `ms.safe_divide`, `ms.approx_distinct`, `ms.coalesce`. Each is a thin
   wrapper over ibis; ship with docstrings showing the SQL idiom each maps
   from.
3. Decorator implementations and their decorator-time validators
   (`decorators.py`, `builders.py`, `validator.py` phase 1), in order:
   `@ms.datasource` → `@ms.dataset` → `@ms.field` / `@ms.time_field` →
   `ms.set_keys` → `@ms.metric` → `@ms.relationship` → `ms.model`. All
   expression-bearing decorators accept `source_sql` and `source_definition`
   kwargs. `ms.set_keys` lands in `builders.py` next to the decomposition
   factories since it is a module-level binding call.
4. Loader with convention scan (`loader.py`) and assembly-time validator
   (`validator.py` phase 2), including datasource ref resolution and the
   cross-reference graph for metric → field / metric calls.
5. Read API and materialization flow (`reader.py`): default path calls
   registered datasource fns and caches resulting backends in the registry;
   override path uses caller-supplied `backend_factory`. `reload()`
   discards the cache and attempts `.disconnect()` on each backend.
6. Testing helpers (`testing.py`).
7. Documentation under `docs/specs/semantic/` covering the public API surface.

Migration Workflow components (parity engine, OSI → Python conversion entry,
MCP tooling) are intentionally not in this list; they ship in v1.5+ on top
of the v1 surface.

## Open Questions

None blocking; design decisions are listed in "v1 Boundaries". Items that
become relevant in v2 (MCP adapter, OSI conversion, multi-root discovery,
derived datasets, broader AST surface) are explicitly deferred.
