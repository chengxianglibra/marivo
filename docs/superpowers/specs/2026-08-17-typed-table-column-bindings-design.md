# Marivo Typed Table Column Bindings Design

Status: proposed

Date: 2026-08-17

## Summary

Extend `md.table(...)` with an optional, explicitly typed column-binding
contract for physical tables whose queryable schema is not represented
faithfully by backend catalog metadata or by Ibis.

The public contract maps each stable output column name to exactly one physical
source identifier and one declared Ibis data type:

```python
source = md.table(
    "events",
    database="warehouse",
    columns={
        "event_time": md.source_column("event.timestamp", data_type="timestamp"),
        "user_id": md.source_column("$user.identifier", data_type="string"),
        "score": md.source_column("_generated_score", data_type="float64"),
    },
)
```

This is a physical-source schema adapter, not a derived semantic expression and
not a public SQL source. It is useful when a backend has any of the following
shapes:

- hidden, virtual, generated, pseudo, or otherwise catalog-invisible columns;
- physical identifiers that contain dots, reserved words, or other names that
  Ibis cannot address faithfully as ordinary table fields;
- incomplete metadata visibility under a restricted account;
- a very wide physical table from which the semantic project intentionally
  exposes a small, stable allowlist;
- physical names that should be normalized to stable source-facing aliases
  before semantic dimensions and measures bind to them.

The datasource layer renders one identifier-only inner projection without a
table alias and supplies the declared output schema to `backend.sql(...)`.
Outer filtering, joins, grouping, aggregation, and analysis continue to use
ordinary Ibis expressions over the stable output columns.

The motivating ClickHouse MapV2 case is one acceptance fixture, not the public
abstraction. No MapV2 name, setting, key syntax, or backend-specific switch is
added to the Marivo API.

## Problem

`TableSourceIR` currently identifies only a table and optional database. Every
runtime path resolves it through `backend.table(...)`, which assumes the
backend catalog schema is both complete and addressable through ordinary Ibis
fields.

That assumption is not universal. A database may accept a physical identifier
in `SELECT` while omitting it from `system.columns`, information schema, or the
schema returned to Ibis. A physical identifier may also need to be quoted as one
atomic name even when it contains punctuation that Ibis interprets as
qualification or nested-field navigation.

The resulting failure has three distinct layers:

1. the database can query the physical value;
2. metadata inspection does not expose a usable Ibis field for that value;
3. Ibis qualifies ordinary fields with a generated table alias, which some
   virtual-column implementations do not accept.

Today the only public fallback is `md.raw_sql(...)`. That path is deliberately
terminal: its result cannot become an entity, metric, frame, or governed
analysis input. Asking users to create a database view can restore a normal
table schema, but it requires external DDL authority and creates an operational
artifact whose ownership and drift are outside the semantic project.

Adding arbitrary `md.sql(query, schema)` as an entity source would solve the
immediate execution problem while weakening several current contracts:

- table and datasource lineage would have to be repeated outside and inside
  authored SQL and could disagree;
- metadata-only inspection could not prove source tables, partitions, row
  grain, or output meaning without parsing vendor SQL;
- unrestricted SQL could hide joins, filters, aggregation, sampling, or
  fanout behind an entity that still claims one stable physical grain;
- SQL parsing or transpilation would corrupt exactly the vendor syntax for
  which the escape was introduced;
- it would contradict the public boundary that `md.raw_sql(...)` is the sole
  arbitrary SQL execution entry and remains terminal.

The missing capability is narrower: declare a stable typed interface over one
physical table while preserving one-to-one row identity and keeping every
binding mechanically inspectable.

## Goals

- Add a backend-neutral typed column-binding contract to `md.table(...)`.
- Preserve the original physical table as the sole datasource, lineage,
  partition, extent, and row-grain owner.
- Allow catalog-visible and catalog-invisible physical identifiers to be bound
  under stable output names.
- Quote each physical identifier atomically and never qualify it with a table
  alias inside the generated projection.
- Require a declared output type for every projected column so materialization
  never needs a schema-inference query.
- Generate SQL internally from identifiers; never accept an authored SQL
  string or expression fragment.
- Make inspection distinguish catalog-verified bindings from declared-only
  bindings and require runtime evidence for the latter.
- Preserve partition scope correctness when a physical partition column is
  renamed in the projected output.
- Keep ordinary `md.table(name, database=...)` behavior and identity unchanged
  when `columns=` is omitted.
- Preserve analysis APIs, semantic expression rules, datasource read-only
  enforcement, timeout behavior, evidence identity, and query capture.
- Ship one coherent current contract without compatibility aliases or dual
  source representations.

## Non-Goals

- No arbitrary SQL entity source and no `md.sql(...)`, `ms.sql(...)`, or
  `SqlSourceIR`.
- No user-authored SQL expressions, functions, casts, predicates, joins,
  grouping, ordering, limits, table functions, or subqueries in a column
  binding.
- No MapV2-specific constructor, key syntax, naming convention, or ClickHouse
  setting in the public API.
- No automatic discovery of catalog-invisible columns.
- No implicit type inference for a projected source and no preliminary
  `LIMIT 0` query during `md.inspect(...)`.
- No implicit cast from the physical value to the declared data type. The type
  is an Ibis schema assertion, not a transformation request.
- No duplicate aliases for one physical source identifier.
- No change to CSV, JSON, or Parquet source contracts. Those source families
  retain their existing schema and reader semantics.
- No source-owned time-window or required-filter policy. Semantic time
  dimensions and analysis `time_scope` remain the temporal owners.
- No promise that every backend optimizer pushes an outer predicate through
  the generated projection. Backend-specific acceptance must verify pruning
  where a datasource requires it.
- No database view creation, DDL privilege management, or migration of existing
  views into bindings.

## Ownership and execution chain

```text
physical datasource and base table
  -> typed table column bindings
  -> stable Ibis table schema
  -> semantic dimensions, measures, and metrics
  -> analysis windowing, slicing, aggregation, and evidence
```

The datasource layer owns:

- the physical table identity;
- the physical source identifiers;
- the output aliases and declared physical types;
- identifier quoting and generated projection SQL;
- catalog comparison, partition-name projection, and bounded acquisition.

The semantic layer owns:

- business meaning attached to output aliases;
- entity grain, primary keys, dimensions, measures, metrics, and versioning;
- static dependency validation and preview/readiness state.

The analysis layer owns:

- time scopes, slices, joins selected from governed relationships,
  aggregation, comparison, attribution, and evidence.

A column binding supplies no business meaning. A binding named `score` is only
a typed physical fact until a semantic dimension or measure defines what that
score means.

## Public contract

### `md.source_column(...)`

Add one datasource-owned value constructor:

```python
def source_column(
    name: str,
    /,
    *,
    data_type: str,
) -> TableColumnBindingIR:
    """Declare one typed physical identifier for a table column binding."""
```

Parameters:

- `name` is one complete physical identifier. It is not a dotted path grammar
  and not an SQL fragment. For example, `"host.region"` is quoted and emitted
  as one identifier, not split into `host`.`region`.
- `data_type` must be a non-empty type string accepted by `ibis.dtype(...)`.
  The canonical Ibis type is used for validation and materialization schema.

The constructor returns a frozen `TableColumnBindingIR`. It does not know its
output alias; the owning `md.table(columns=...)` mapping key supplies that
single identity. This avoids a duplicated `(alias in mapping, alias in value)`
contract that could disagree.

`source_column` rejects:

- non-string or empty identifiers;
- identifiers containing NUL;
- non-string, empty, or invalid Ibis type strings.

Quotation marks, dots, spaces, reserved words, and punctuation are legal
identifier contents because the engine profile quotes and escapes the complete
identifier. Their presence never switches the value into an expression mode.

There is intentionally no shorthand such as
`{"output": ("physical", "string")}` and no bare-string form. One typed value
shape keeps invalid or partially typed bindings unrepresentable.

### Extended `md.table(...)`

Extend the existing builder:

```python
def table(
    name: str,
    /,
    *,
    database: str | tuple[str, ...] | None = None,
    columns: Mapping[str, TableColumnBindingIR] | None = None,
) -> TableSourceIR:
    ...
```

The mapping key is the stable output column name visible to Ibis, semantic
authoring, snapshot evidence, and analysis. The value identifies the one
physical source column and its output type.

Two closed modes exist:

| Authored form | Meaning | Runtime path |
| --- | --- | --- |
| `md.table(name, database=...)` | Catalog-backed complete table schema | Existing `backend.table(...)` path |
| `md.table(name, database=..., columns={...})` | Explicit typed table interface | Generated identifier-only projection |

`columns=None` means the existing catalog-backed mode. An explicitly empty
mapping is rejected; it does not act as another spelling of `None`.

The projected mode requires:

- non-empty string output names that do not contain NUL;
- exact `TableColumnBindingIR` values;
- at least one output column;
- unique physical source identifiers;
- canonical ordering by output name for deterministic IR, SQL, serialization,
  display, and identity.

The output name and physical identifier may be equal. Requiring all bindings
to be explicit in projected mode is intentional: mixing inferred and declared
types would reintroduce a schema-discovery dependency and make source identity
depend on live metadata.

### Example: stable aliases over unusual identifiers

```python
events_source = md.table(
    "raw.events",
    database="warehouse",
    columns={
        "occurred_at": md.source_column(
            "event.timestamp",
            data_type="timestamp(3)",
        ),
        "schema_name": md.source_column(
            "schema",
            data_type="string",
        ),
        "generated_score": md.source_column(
            "_virtual_score",
            data_type="float64",
        ),
    },
)

events = ms.entity(
    name="events",
    datasource=ms.ref.datasource("warehouse"),
    source=events_source,
)

occurred_at = ms.time_dimension_column(
    name="occurred_at",
    entity=events,
    column="occurred_at",
    granularity="minute",
    parse=ms.timestamp(timezone="UTC"),
    is_default=True,
)
```

Semantic code binds only to output aliases. It never refers to the unusual
physical identifier again.

## IR and serialization

### `TableColumnBindingIR`

Add the frozen datasource IR value:

```python
@dataclass(frozen=True)
class TableColumnBindingIR:
    source: str
    data_type: str

    def __post_init__(self) -> None:
        ...

    def to_dict(self) -> dict[str, str]:
        return {
            "source": self.source,
            "data_type": self.data_type,
        }
```

The stored `data_type` is the canonical `str(ibis.dtype(data_type))`, so
equivalent accepted spellings do not create different source identities.

No nested `kind` discriminator is necessary. `TableSourceIR.columns` is a
closed collection whose values can only be `TableColumnBindingIR`.

### `TableSourceIR.columns`

Extend `TableSourceIR`:

```python
@dataclass(frozen=True)
class TableSourceIR:
    table: str
    database: str | tuple[str, ...] | None = None
    columns: tuple[tuple[str, TableColumnBindingIR], ...] = ()
    kind: Literal["table"] = "table"
```

The tuple provides frozen value semantics. The builder normalizes a mapping to
this tuple in ascending output-name order before construction. Direct IR
construction applies the same validation and canonical ordering, so mapping
insertion order is never semantic state.

`TableSourceIR.to_dict()` adds `columns` only when bindings exist:

```python
{
    "kind": "table",
    "table": "events",
    "database": "warehouse",
    "columns": {
        "event_time": {
            "source": "event.timestamp",
            "data_type": "timestamp",
        }
    },
}
```

When bindings are absent, the serialized dictionary must remain exactly the
current `{"kind", "table", "database"}` shape. This prevents definition,
snapshot, metric-graph, and analysis cache fingerprints from changing for every
existing table entity.

`source_to_dict(...)` delegates to `TableSourceIR.to_dict()` rather than
maintaining a second table serializer. `source_from_dict(...)` accepts the one
new nested mapping and treats a missing `columns` key as the existing empty
binding tuple. Unknown nested keys or malformed binding values fail loudly.

The binding collection is included in:

- entity source persistence and details;
- datasource authoring source identity;
- discovery snapshot identity;
- semantic definition and dependency fingerprints;
- metric graph physical-leaf lineage;
- analysis cache and replay identity where the entity source already
  participates.

Binding order has no business or evidence meaning. Reordering an authored
mapping therefore produces the same IR tuple, serialized dictionary, generated
SQL, semantic definition fingerprint, snapshot identity, analysis cache
identity, and evidence validity. All renderers use the same canonical
output-name order rather than preserving mapping insertion order.

The current snapshot encoder already sorts mapping keys, but semantic
definition identity traverses dataclass tuples in order. Canonicalizing at the
IR boundary makes both owners agree instead of relying on one downstream JSON
encoder to erase insertion order.

Adding, removing, renaming, rebinding, or retyping a column changes the source
identity and makes evidence over the old source stale. Existing unprojected
tables retain their byte-stable dictionary shape and existing identities. This
rule is tested at each identity owner; it is not left to incidental dictionary
ordering behavior.

## Generated relation contract

Ibis 12 does not declare `sql(...)` on `BaseBackend`, and Marivo's current
semantic backend protocol does not accept the `schema=` keyword. The design
therefore does not type this helper as `BaseBackend` and does not use a broad
cast or `Any` to bypass the mismatch.

Add two narrow datasource-owned runtime protocols and matching type guards:

```python
class TableLookupBackend(Protocol):
    def table(
        self,
        name: str,
        /,
        *,
        database: str | tuple[str, ...] | None = None,
    ) -> ir.Table: ...


class TableSqlBackend(Protocol):
    def sql(
        self,
        query: str,
        /,
        *,
        schema: Mapping[str, str],
    ) -> ir.Table: ...


def supports_table_lookup(value: object) -> TypeGuard[TableLookupBackend]: ...


def supports_table_sql(value: object) -> TypeGuard[TableSqlBackend]: ...
```

Each guard performs the localized runtime
`callable(getattr(value, "table" | "sql", None))` probe. The concrete call is
made only after the guard narrows the object. `marivo.semantic.typing.IbisBackend`
inherits these two protocols and retains its file-reader members, replacing its
current schema-less `sql(...)` declaration. Snapshot acquisition can continue
to receive `object`; it uses the same guards and helper rather than a cast.

Add one internal datasource helper, shared by semantic materialization and
authoring snapshot acquisition:

```python
def table_source_expression(
    backend: object,
    source: TableSourceIR,
) -> ir.Table:
    ...
```

Behavior:

1. With no bindings, require `supports_table_lookup(backend)` and call
   `backend.table(...)` exactly as today.
2. With bindings, resolve the backend engine profile.
3. Quote every database, table, physical column, and output alias as an atomic
   identifier with the profile's identifier quote.
4. Generate one `SELECT <physical> AS <output>, ... FROM <qualified table>`.
5. Do not add an alias to the source table.
6. Require `supports_table_sql(backend)` and call
   `backend.sql(query, schema=declared_schema)` without a `dialect=` argument.
7. Return the resulting typed Ibis table.

For a ClickHouse profile, the source above renders conceptually as:

```sql
SELECT
  `event.timestamp` AS `event_time`,
  `schema` AS `schema_name`,
  `_virtual_score` AS `generated_score`
FROM `warehouse`.`raw.events`
```

The exact invariants are:

- the inner `FROM` target has no `AS t` alias;
- no selected physical identifier has a `t.` prefix;
- dots in a physical column or table name remain inside one quoted identifier;
- the query contains no wildcard;
- the query contains no authored expression;
- the output schema order matches canonical output-name order;
- no SQL transpilation is requested.

Ibis may wrap this relation and qualify the stable output aliases in outer SQL:

```sql
SELECT `t0`.`generated_score`
FROM (
  SELECT `_virtual_score` AS `generated_score`
  FROM `warehouse`.`raw.events`
) AS `t0`
WHERE `t0`.`event_time` >= ...
```

That qualification is legal because the output aliases are ordinary subquery
columns. The contract makes no attempt to disable normal outer Ibis aliases.

If a backend does not expose callable `sql(...)`, materialization fails closed
with a structured source-capability error. It never falls back to
`backend.table(...)`, because that would silently drop the binding semantics.
The protocol makes the required keyword statically visible, while the runtime
probe remains necessary because registered backend objects and test doubles can
be supplied dynamically. Fake backends cover both protocol conformance and the
missing-capability failure.

The helper belongs in the datasource package because identifier rendering and
physical source resolution are datasource concerns. The semantic materializer
must not implement a second renderer.

## SQL and safety boundary

This design does not weaken the terminal raw-SQL boundary.

`md.raw_sql(...)` remains the sole public API accepting arbitrary SQL text. A
table binding accepts identifiers and types only. The generated SQL is adapter
implementation derived entirely from validated IR, like SQL generated by an
Ibis compiler.

Safety properties:

- each identifier is quoted and quote characters are escaped by the engine
  profile;
- no value is concatenated as an SQL expression or literal;
- semicolons, braces, parentheses, and operators inside `source_column(name)`
  remain identifier characters after quoting;
- one physical identifier produces one scalar output column per source row;
- no binding can introduce a join, filter, aggregate, fanout, DDL statement, or
  second table;
- connection read-only settings and execution timeout guards remain unchanged;
- generated SQL is captured in ordinary analysis query evidence.

A vendor expression such as `map['key']`, `payload:key`, or
`string_map{'key'}` is therefore not a valid binding expression. The backend
must expose an identifier form for that value, or the user must rely on a
database view or terminal `md.raw_sql(...)`.

## Metadata inspection

`md.inspect(datasource, projected_table)` remains metadata-only. It does not
execute the generated projection or issue a schema-inference query.

Inspection normally has two stages:

1. inspect the unprojected base table through the existing backend metadata
   path to capture physical extent, base schema, partitions, comments,
   nullability, constraints, and backend capabilities;
2. project that metadata through the authored binding contract to produce the
   effective source interface.

For a projected source, failure to read the base catalog is not automatically
a fatal inspection error. After the datasource backend has been constructed,
the adapter classifies base-table resolution failures into:

- metadata/schema unavailable, including a read account that can query data
  but cannot read the catalog: degrade to an all-declared-only inspection;
- authentication, connection, configuration, timeout, or other unclassified
  failures: fail closed through the existing structured metadata error.

The degraded result does not claim that the table exists. It exposes exactly
the authored schema, marks every binding declared-only with unknown nullability
and its normal per-binding warning, marks physical extent, partitioning, and
constraints unknown, sets `partition_predicate_supported=False`, and also
emits one stable `base_table_metadata_unavailable` warning with the sanitized
backend reason.
Acquisition must therefore use an explicit bounded `md.unpruned(...)` scope;
the first bounded sample or preview is the authority that the table and its
identifiers are queryable. Unprojected tables continue to fail closed because
they have no authored schema from which a truthful effective interface can be
built.

This fallback is implemented as a projected-source inspection branch around
the current base metadata path; it must not broadly catch every backend error
or silently turn an invalid connection into a ready inspection. The adapter
must return a structured metadata-availability category; exception-message
substring matching is not an acceptable classifier.

### Effective schema

`SourceInspection.schema` contains exactly the output aliases, in canonical
output-name order. For each binding:

- `ColumnMetadata.name` is the output alias;
- `ColumnMetadata.type` is the canonical declared type;
- if the physical identifier exists in base metadata, nullability and comment
  are copied from that physical column;
- if the physical identifier is absent from base metadata, nullability is
  unknown and comment is absent;
- ordinal position follows canonical output-name order rather than base-table
  order.

Bindings fall into two evidence states:

| State | Condition | Inspection result |
| --- | --- | --- |
| catalog verified | physical identifier exists and canonical type equals the declaration | normal effective column |
| declared only | physical identifier is absent from base metadata | effective column plus warning naming output, source, and declared type |

If a catalog-visible physical identifier has a different canonical type,
inspection fails before user-data execution with `declared_type_mismatch`.
The repair names the output alias, observed catalog type, declared type, and the
exact `md.source_column(..., data_type=...)` field to correct. A projected
source does not silently reinterpret a catalog-visible column.

A declared-only binding is not assumed to exist merely because it was authored.
It remains usable because catalog invisibility is the feature's purpose, but
the inspection warning states that runtime acquisition is still required to
prove queryability.

Extend the internal structured metadata warning vocabulary with exact kinds
for `base_table_metadata_unavailable`, `declared_column_unverified`,
`projected_partition_unavailable`, and `projected_constraint_incomplete`.
`SourceInspection.warnings` may retain its current public string tuple, but
those strings must be rendered from the structured warning values rather than
ad hoc branches.

### Physical extent and constraints

When base metadata is available, physical extent remains the base table's
extent. Projecting fewer columns does not change row count or on-disk size
evidence. Primary-key and unique-constraint columns are mapped to output
aliases only when every participating physical column has exactly one binding.
Otherwise the effective projected source does not claim that constraint, and
inspection warns which physical constraint was not exposed. Under the
all-declared-only fallback, extent and all constraints are unknown.

No projected binding creates a new uniqueness claim.

### Partition mapping

When base metadata is available, partition metadata is first captured using
base physical names. The effective projected source then maps every physical
partition field through the binding whose `source` matches it. Under the
all-declared-only fallback, partition state remains unknown and no partition
values are offered.

- When all physical partition fields are bound, `Partitioning.fields` uses the
  corresponding output aliases. Captured partition-value mappings are renamed
  to the same aliases. `inspection.partitions().contract()` therefore produces
  a scope that can be applied directly to the projected Ibis relation.
- When any physical partition field is omitted, the effective projected source
  reports partition state as `unknown`, does not expose captured values, and
  warns that the base table is partitioned but the projected interface cannot
  express a complete partition scope. Its effective
  `partition_predicate_supported` capability is `False`.
- `md.unpruned(max_rows=..., timeout_seconds=...)` remains the explicit
  acquisition path when a projected source omits partition columns. It is not
  selected automatically.

This avoids returning a physical partition name that does not exist in the
projected output and avoids suggesting a mechanically invalid
`md.partition(...)` call.

Transformed partition behavior remains unchanged. If the current backend
cannot safely enumerate values for a transformed partition, bindings do not
upgrade that capability.

### Bounded rendering

Projected sources can contain dozens of bindings, so current full-dictionary
rendering would violate bounded result principles.

Update `SourceInspection.show()` and `EntityDetails.show()` to render:

- source kind, base table, database, and projected-column count;
- a bounded table of output alias, physical source identifier, and declared
  type;
- an omitted-binding count and a programmatic full-read affordance through
  `.source.to_dict()`.

The full serialized source remains available as structured data but is never
dumped unbounded into one card field.

## Snapshot acquisition and authoring evidence

`SourceInspection.sample(...)` uses the shared `table_source_expression(...)`
helper.

- `columns=(...)` names stable output aliases.
- `PartitionScope.values` names projected partition aliases produced by
  inspection.
- filters and limits apply outside the generated inner projection.
- timeout and read-only enforcement remain backend-owned and unchanged.
- the exact projected `TableSourceIR.to_dict()` participates in snapshot
  identity.
- a snapshot acquired from the unprojected source does not satisfy a projected
  source, and changing one binding makes prior evidence stale.

Successful acquisition proves that the generated projection executed for the
selected output columns and scope. It does not prove business meaning, complete
source coverage, or the correctness of a catalog-invisible column's authored
type beyond the observed execution.

A catalog-invisible binding therefore follows the normal evidence loop:

```text
md.inspect(...) declared-only warning
  -> inspection.sample(...) executes the bounded projection
  -> snapshot evidence over output aliases
  -> semantic authoring
  -> catalog.preview(..., using=snapshot)
  -> catalog.readiness(...)
```

No acquisition is triggered by `md.inspect`, `ms.load`, `catalog.verify`, or
`catalog.readiness`.

## Semantic materialization

The semantic materializer replaces its table-specific `backend.table(...)`
branch with the shared datasource helper. File-source branches are unchanged.

Dimensions, time dimensions, and measures bind to projected output aliases:

```python
score_level = ms.dimension_column(
    name="score_level",
    entity=events,
    column="score_level",
)
```

The semantic layer never receives the physical identifier and never needs to
know whether it was catalog-visible. `metric_on`, `measure_on`, joins,
windowing, filters, and aggregation consume the same Ibis table shape as any
ordinary entity.

`catalog.verify(...)` remains static. It verifies the binding IR, source
dependency, output column references, and semantic object graph without
opening a datasource. It does not claim that declared-only physical columns
exist.

For a projected source, static assembly validates every column named by
`ms.dimension_column(...)`, `ms.time_dimension_column(...)`,
`ms.measure_column(...)`, and `ms.entity(primary_key=...)` against the authored
output aliases. A missing output alias is a static load error with the bounded
available aliases and an exact repair. Expression-decorator bodies retain their
existing AST and binding validation; this proposal does not add a second
general expression type-inference engine at load time.

`catalog.preview(...)` and ordinary analysis execution are the runtime
authorities. A backend unknown-identifier or type failure is wrapped in the
existing structured datasource/semantic error boundary with the entity ref,
output alias, physical source name where recoverable, and whether a query was
executed.

`catalog.readiness(...)` continues to distinguish static readiness from
runtime preview evidence. A static pass over a declared-only binding is not a
runtime acceptance claim.

## Runtime provenance and lineage

A projected table is neither an authored SQL view nor an ordinary
catalog-materialized Ibis table. Add:

```python
class EntityProvenance(StrEnum):
    IBIS_TABLE = "ibis_table"
    TABLE_PROJECTION = "table_projection"
    SQL_VIEW = "sql_view"
```

Runtime provenance classification becomes source-aware:

- unbound `TableSourceIR` -> `IBIS_TABLE`;
- bound `TableSourceIR` -> `TABLE_PROJECTION`;
- any retained internal SQL-view path -> `SQL_VIEW`.

The source IR, not an Ibis operation-tree heuristic, is authoritative for a
table projection. `raw_sql_snippet` remains `None` for `TABLE_PROJECTION`
because no user-authored SQL exists; the deterministic generated query is
already recoverable from the source IR and appears in executed query evidence.

Provenance consumers must branch exhaustively on the enum. In particular,
they must not infer `SQL_VIEW` from `provenance != IBIS_TABLE` or assume that
every non-table relation has user-authored SQL. `TABLE_PROJECTION` carries no
raw SQL ownership and must remain reconstructible from typed source IR.

Physical lineage remains the one base table and datasource. Entity dependency
digests and metric graph physical leaves include the complete binding mapping.
No output alias is reported as a second physical source.

Parity continues to qualify provenance SQL from the base `TableSourceIR.table`
and `database`. Because the source remains `TableSourceIR`, the existing
single-table qualifier path remains applicable. Typed bindings do not rewrite
authored metric provenance SQL or make projected aliases available inside that
separate SQL statement.

## Diagnostic and error contract

Local authoring mistakes fail synchronously from the builders with
`TypeError` or `ValueError`, matching existing source value objects. Live and
metadata failures use the existing structured datasource and semantic error
families.

| Stage | Stable condition | Query executed | Required repair |
| --- | --- | --- | --- |
| builder | invalid output name, binding type, source identifier, duplicate physical source, or invalid data type | no | correct the exact `md.table(columns=...)` or `md.source_column(...)` argument |
| inspect | catalog-visible source type differs from declared type | no | use the observed canonical type or bind a different physical source |
| inspect warning | base metadata is classified unavailable for a projected source | no | inspect the declared-only schema, then use explicit bounded `md.unpruned(...)` acquisition to prove the table and identifiers |
| inspect warning | projected source omits one or more base partition fields | no | add bindings for those fields or explicitly acquire with `md.unpruned(...)` |
| materialize | backend lacks `sql(query, schema=...)` | no | use an unprojected table, a supported SQL backend, or a database view |
| acquire/preview | declared-only physical identifier is not queryable | yes | correct the physical identifier or create/use a database view |
| acquire/preview | server rejects the declared type during downstream operation | yes | correct `data_type` and reacquire evidence |
| analysis | backend requires a pruning predicate and the outer semantic window is absent or not pushed down | yes | pass an appropriate `time_scope`, expose the partition field, or use a backend view that guarantees pruning |

Errors must state:

- expected source mode or type;
- received output alias, physical identifier, and declared type;
- entity and datasource identity when known;
- `query_executed` and a sanitized backend summary;
- one concrete repair derived from current source and inspection state.

There is no silent fallback from projected to catalog-backed materialization,
no alias retry, and no automatic raw-SQL retry.

## Public surface and help

Add to `marivo.datasource`:

- `source_column`;
- `TableColumnBindingIR` for concrete annotation and type inspection.

Update the pinned datasource `__all__` snapshot, import tests, capability
registry, and type contract registry. Do not add semantic aliases.

`marivo.help("datasource.source_column")` owns the binding constructor's exact
signature, input types, validation, one runnable example, and constraints.

`marivo.help("datasource.table")` gains the optional `columns` mapping and
shows both closed modes. Its focused example should use a catalog-invisible or
unusual identifier so the capability is not mistaken for ordinary semantic
dimension selection.

Help must state explicitly:

- bindings are identifier-only;
- `data_type` asserts schema and does not cast;
- every projected output needs a binding;
- `md.inspect(...)` remains metadata-only;
- declared-only bindings require bounded runtime evidence;
- arbitrary SQL remains terminal through `md.raw_sql(...)`.

The `marivo-semantic` packaged skill may route an agent from an inspection
warning to the focused datasource help target, but it must not duplicate the
parameter table or backend-specific syntax.

## Documentation changes

Implementation must update the same change set across:

- `docs/specs/semantic/datasource-layer.md` — physical-source table, inspection,
  snapshot, raw-SQL boundary, and the typed-binding example;
- `docs/specs/semantic/loading-validation-introspection.md` — materialization,
  runtime provenance, static/runtime evidence boundary;
- `docs/specs/semantic/authoring-workflow.md` — declared-only inspection and
  preview/readiness loop;
- generated API documentation for `md.table`, `md.source_column`,
  `TableSourceIR`, and `TableColumnBindingIR`;
- English and Chinese `latest` datasource and semantic-authoring site pages;
- packaged semantic skill routing only where it currently enumerates physical
  source kinds or repair paths.

`agent-guide.md` does not change because this is a feature contract, not a new
repository-wide coding or testing rule.

The datasource-layer specification must explicitly distinguish three SQL
categories: Ibis compiler SQL, datasource-adapter-generated SQL derived only
from validated typed IR, and user-authored terminal `md.raw_sql(...)`. Only the
last category is authored SQL text and subject to the terminal-source rule.

## General use cases

The core feature is justified by one general boundary: a table's queryable
schema is not always identical to its catalog-discoverable Ibis schema.

### Catalog-invisible or generated columns

A backend may expose a scalar generated column only at query time. The binding
declares its stable output type while inspection labels it declared-only.

### Atomic identifiers with punctuation

A physical table may contain literal column names such as `host.region`,
`$event.id`, `select`, or `a b`. A binding ensures the full name is quoted as
one identifier and gives semantics a conventional alias.

### Restricted metadata accounts

A read account may have `SELECT` permission but incomplete information-schema
visibility. The project can declare the known typed interface and prove it
through bounded acquisition without granting metadata or DDL authority.

### Stable allowlist over a wide table

A semantic project may intentionally expose only the columns it governs. The
binding mapping makes that allowlist part of source identity and prevents new
physical columns from entering through `SELECT *` after schema drift.

### Backend adapter columns

A backend or database extension may synthesize special identifiers that are
queryable but not represented as standard SQL types. Marivo consumes the
identifier form and declared scalar output type without learning the
extension's business vocabulary.

## ClickHouse MapV2 acceptance case

The bili ClickHouse MapV2 source validates the design's hardest path without
owning its API.

```python
vod_playurl_source = md.table(
    "infra.mg.vod-playurl-v3",
    database="billions",
    columns={
        "timestamp": md.source_column("timestamp", data_type="timestamp(3)"),
        "host_region": md.source_column("host.region", data_type="string"),
        "host_zone": md.source_column("host.zone", data_type="string"),
        "host_name": md.source_column("host.name", data_type="string"),
        "host_deploy_env": md.source_column(
            "host.deploy_env",
            data_type="string",
        ),
        "service_name": md.source_column("service.name", data_type="string"),
        "log_level": md.source_column("log.level", data_type="string"),
        "log_msg": md.source_column("log.msg", data_type="string"),
        "mcdn_fail_reason": md.source_column(
            "string_map_ICDS_mcdn_fail_reason",
            data_type="string",
        ),
        "score_level": md.source_column(
            "number_map_ICDS_score_level",
            data_type="float64",
        ),
        "served_by": md.source_column(
            "string_map_ICDS_served_by",
            data_type="string",
        ),
        "user_isp": md.source_column(
            "string_map_ICDS_user_isp",
            data_type="string",
        ),
        "user_province": md.source_column(
            "string_map_ICDS_user_province",
            data_type="string",
        ),
        "user_region": md.source_column(
            "string_map_ICDS_user_region",
            data_type="string",
        ),
    },
)
```

This acceptance fixture has 14 bindings. Exactly these six physical
identifiers are expected to be absent from `system.columns` and become the six
governed MapV2 business dimensions:

| Output dimension | Catalog-invisible physical identifier |
| --- | --- |
| `mcdn_fail_reason` | `string_map_ICDS_mcdn_fail_reason` |
| `score_level` | `number_map_ICDS_score_level` |
| `served_by` | `string_map_ICDS_served_by` |
| `user_isp` | `string_map_ICDS_user_isp` |
| `user_province` | `string_map_ICDS_user_province` |
| `user_region` | `string_map_ICDS_user_region` |

The other eight bindings—`timestamp`, `host.region`, `host.zone`, `host.name`,
`host.deploy_env`, `service.name`, `log.level`, and `log.msg`—are
catalog-visible physical columns in the current table. They are included to
preserve the entity's existing physical surface and to exercise atomic quoting
for dotted identifiers; they are not counted among the six MapV2 dimensions.

The generated inner query uses the ICDS virtual identifiers directly. It does
not parse or emit the MapV2 brace syntax and does not qualify ICDS identifiers
with a table alias.

Live acceptance on the exact internal ClickHouse version must prove:

1. all six catalog-invisible ICDS identifiers listed above execute through the
   projected entity;
2. missing MapV2 keys remain nullable rather than becoming empty strings or
   zeroes;
3. `score_level` behaves as `float64` in average and comparison operations;
4. `served_by == "MCDN"` works in a governed metric filter;
5. a semantic outer half-open timestamp predicate satisfies the server's
   `force_index_by_date` requirement through the generated subquery;
6. compiled inner SQL contains no table alias and no qualified ICDS column;
7. the six named MapV2 dimensions and dependent metrics pass static
   verification, bounded preview, readiness, and real
   `session.observe(...)` execution.

If item 5 fails on the exact server build, the core binding contract remains
valid but does not solve that datasource's pruning requirement. Predicate
rewriting into generated subqueries is a separate backend-compiler design and
must not be smuggled into this proposal.

## Alternatives considered

### Arbitrary `md.sql(query, schema)` source

Rejected. It solves more than the demonstrated problem while making lineage,
row grain, partition evidence, inspection, safety, and SQL preservation
unverifiable from typed IR.

### Database view

Operationally valid and still recommended when the organization owns durable
view DDL and governance. It is not sufficient as the framework contract because
some read accounts lack DDL privileges and the view becomes an external drift
surface.

### Backend-specific MapV2 API

Rejected. `md.mapv2_column(...)`, brace-key values, or an
`unqualified=True` switch would expose one vendor implementation instead of the
general schema-adapter boundary.

### Ibis compiler patch or global alias suppression

Rejected. Ordinary outer aliases are legal and important. Disabling them
globally would affect every query and still would not add catalog-invisible
columns to the Ibis schema. A custom compiler patch would also be version-fragile.

### Semantic calculated column

Rejected. The value cannot become a semantic Ibis expression until the
physical column is present in the input relation. Physical addressability and
business meaning belong to different layers.

### Implicit schema merge

Rejected for V1. Allowing projected bindings to omit types for catalog-visible
columns would make one source depend partly on live metadata and partly on
authored state. Requiring a complete typed projected interface is more
deterministic and easier to persist, inspect, and reproduce.

### New `ProjectedTableSourceIR` union member

Rejected. The underlying source remains the same physical table and existing
table-specific metadata, partition, parity, and lineage paths should continue
to recognize it as `TableSourceIR`. A new union member would create duplicated
table identity and force every closed-union site to rediscover the base table.

## Compatibility and migration

This is an additive change to the current table source:

- existing `md.table(...)` calls construct `columns=()` and follow the exact
  old runtime path;
- existing serialized table dictionaries omit `columns` and reconstruct
  identically;
- unprojected table dictionaries and fingerprints do not change;
- no old source is automatically converted to projected mode;
- no database view or semantic object is rewritten;
- no compatibility alias, deprecated constructor, dual read, or migration
  command is introduced.

A project adopts bindings by changing one entity's `source=md.table(...)` and
then updating that entity's column-based semantic objects to use the stable
output aliases. Its existing runtime preview and analysis evidence becomes
stale because the source identity changed; reacquisition is required.

## Implementation map

The implementation must inspect and update every affected current owner rather
than treating materialization as the only change.

### Datasource IR and builders

- `marivo/datasource/ir.py`
  - `TableColumnBindingIR`;
  - `TableSourceIR.columns` validation and serialization;
  - canonical type normalization;
  - `source_to_dict(...)` single serialization path.
- `marivo/datasource/source.py`
  - `source_column(...)`;
  - extended `table(..., columns=...)` signature and docstring.
- `marivo/datasource/__init__.py`
  - public exports.

### Physical expression and engine behavior

- new internal datasource table-expression helper;
- engine-profile identifier quoting reuse;
- datasource-owned `TableLookupBackend` and schema-capable `TableSqlBackend`
  protocols with runtime TypeGuards;
- semantic `IbisBackend` composition from those protocols without broad casts
  or `Any` escapes;
- semantic materializer table branch;
- snapshot acquisition table branch;
- fake/protocol backend expectations for callable `sql(..., schema=...)`.

### Inspection and evidence

- base-table inspection followed by effective schema projection;
- classified metadata-unavailable fallback to an all-declared-only projected
  inspection, while connection and unclassified failures remain closed;
- structured adapter-owned metadata-availability classification without error
  string matching;
- canonical type comparison;
- declared-only warnings;
- primary/unique constraint alias mapping;
- partition field and value alias mapping;
- effective partition capability downgrade when fields are omitted;
- bounded inspection rendering and source identity.

### Semantic runtime and persistence

- `source_from_dict(...)` round trip;
- preview source-code rendering;
- source labels and details rendering;
- `EntityProvenance.TABLE_PROJECTION` classification;
- dependency and metric graph source payloads;
- parity regression coverage.

### Public contract

- datasource capability and type registries;
- public import snapshots;
- focused help examples and constraints;
- API documentation;
- English and Chinese latest site content;
- packaged skill routing where affected.

## Delivery slices

The slices describe implementation and review order. They land as one coherent
feature change; no release may expose Slice 1's authoring surface before the
runtime, inspection, evidence, help, and documentation slices are complete.

### Slice 1: closed IR and public authoring contract

- add `TableColumnBindingIR` and `md.source_column(...)`;
- extend `TableSourceIR` and `md.table(...)`;
- implement strict validation, canonical types, serialization, and source
  identity;
- update exports, type contracts, live help, and focused unit tests.

This slice does not claim runtime support until Slice 2 lands.

### Slice 2: one shared generated-relation path

- add the internal table-source expression helper;
- use it from semantic materialization and snapshot acquisition;
- prove no-alias inner SQL, atomic quoting, explicit schema, and unchanged
  unprojected behavior;
- add structured capability and runtime error coverage.

### Slice 3: truthful metadata and evidence

- project base metadata to output aliases;
- implement declared-only and type-mismatch behavior;
- map constraints and partitions;
- keep inspection metadata-only;
- bound source rendering;
- verify snapshot identity, scope application, and stale-evidence behavior.

### Slice 4: semantic and agent-facing coherence

- add source-aware provenance;
- update details, preview code generation, dependency payloads, help, specs,
  API docs, site content, and packaged skill routing;
- run full public-surface and semantic project tests.

### Slice 5: exact backend acceptance

- run generic SQL-backend compilation and execution probes;
- run the internal ClickHouse MapV2 journey on an isolated bounded time range;
- restore and verify the dependent semantic objects only after runtime proof;
- record unsupported optimizer/pruning behavior honestly if the exact server
  cannot push the outer predicate.

## Test plan

### IR and authoring tests

- ordinary `md.table(...)` equality, dictionary shape, and fingerprint remain
  unchanged;
- projected table round trip preserves output names, sources, canonical types,
  and canonical output-name order;
- reordering an authored mapping produces identical IR, serialization, SQL,
  semantic fingerprints, snapshot identity, and evidence validity;
- empty mappings, empty names, NUL in output aliases or physical identifiers,
  invalid types, wrong binding objects, and duplicate physical sources fail
  deterministically;
- direct IR construction enforces the same rules as the builder;
- no shorthand or unknown nested key is accepted.

### SQL generation tests

- profile-specific quoting for DuckDB/SQLite, Postgres, MySQL, Trino, and
  ClickHouse;
- database tuples are segmented by tuple parts while table names containing
  dots remain one quoted identifier;
- physical column names containing dots remain one quoted identifier;
- quote characters inside identifiers are escaped;
- inner table has no alias and physical selections have no qualifier;
- output aliases are quoted and typed;
- no wildcard, expression, semicolon statement boundary, or dialect
  transpilation is introduced;
- unprojected sources still call `backend.table(...)` and never call
  `backend.sql(...)`.

### Inspection tests

- catalog-visible exact-type bindings project comments and nullability;
- catalog-visible type mismatch blocks before execution;
- catalog-invisible bindings appear with declared-only warnings and unknown
  nullability;
- schema order follows canonical output-name order, not authored insertion
  order or base-table ordinals;
- a classified base-metadata denial returns an all-declared-only schema,
  unknown extent/partition/constraints, disabled partition predicates, and an
  `md.unpruned(...)` repair without executing user data;
- connection, authentication, configuration, and unclassified metadata errors
  still fail closed;
- when base metadata is available, physical extent remains the base extent;
- complete primary/unique constraints map to aliases and incomplete ones are
  omitted with warnings;
- complete partition bindings remap fields and captured value dictionaries;
- omitted partition bindings produce unknown effective partition state and an
  `md.unpruned(...)` repair;
- inspection makes no user-data or schema-inference query.

### Snapshot and semantic tests

- snapshot acquisition executes the projected relation once under existing
  scope, timeout, and read-only guards;
- selected columns and partition scopes use output aliases;
- changed bindings invalidate source and snapshot identity;
- entity, dimension, time dimension, measure, simple metric, ratio, and filter
  materialization work over projected aliases;
- `metric_on` and `measure_on` receive the already projected table and do not
  rematerialize the source;
- relationships use projected key aliases without changing join ownership;
- static verify does not claim runtime queryability;
- preview evidence for the projected source does not satisfy the unprojected
  source or vice versa;
- provenance is `TABLE_PROJECTION`, not `SQL_VIEW`;
- provenance consumers distinguish `TABLE_PROJECTION` exhaustively and never
  treat it as an authored SQL view;
- parity still qualifies the one base table correctly;
- query evidence contains the generated inner projection.

### Public surface and documentation tests

- datasource imports and pinned `__all__` snapshot;
- live help registry input/output/constraint/example coverage;
- datasource type-contract coverage for `TableColumnBindingIR`;
- source preview code round trip;
- bounded `SourceInspection` and `EntityDetails` rendering with omitted counts;
- API documentation build;
- English/Chinese latest content verification and site build;
- packaged skill deterministic checks where content changes.

### Backend and adversarial probes

- real DuckDB table with reserved words and literal dotted column names;
- compile-only coverage for every SQL backend profile;
- static fake-backend coverage proves the shared helper accepts the narrow
  lookup and schema-capable SQL protocols under `make typecheck`;
- backend without callable `sql(...)` fails before query execution;
- malicious-looking identifier text remains one escaped identifier;
- declared-only unknown identifier surfaces a structured executed-query error;
- nullable values survive without sentinel conversion;
- exact internal ClickHouse MapV2 six-ICDS-column, filter, average, ratio, and
  partition-pruning journey.

## Validation gates

Implementation is complete only after:

```bash
make check
cd site && npm run verify:content && npm run build
git diff --check
```

The full suite is required because the change affects shared source IR,
serialization, inspection, snapshot identity, semantic materialization,
runtime provenance, public exports, and help.

The internal ClickHouse probe is an additional external acceptance gate. Local
green tests prove the framework contract but not acceptance by a customized
server build.

## Success criteria

- `md.table(...)` has exactly two documented modes: unchanged catalog-backed
  mode and complete typed-column-binding mode.
- Every projected output has one stable alias, one physical identifier, and one
  canonical Ibis type.
- The generated inner projection contains no source table alias, no qualified
  physical columns, no wildcard, and no authored expression.
- `md.inspect(...)` stays metadata-only and clearly separates catalog-verified
  from declared-only bindings.
- Snapshot acquisition and semantic preview provide bounded runtime evidence
  over output aliases.
- Partition scope templates are mechanically valid after aliasing and are not
  offered when required physical partition fields are absent.
- Source identity, cold-start reconstruction, metric graph lineage, analysis
  cache identity, and stale-evidence behavior include every binding.
- Existing unprojected table source dictionaries, fingerprints, SQL, and
  runtime behavior do not change.
- `md.raw_sql(...)` remains the sole public arbitrary SQL path and remains
  terminal.
- No MapV2-specific symbol or backend setting enters the public API.
- The motivating MapV2 entity can restore all six governed dimensions and
  dependent metrics only after exact live runtime and partition-pruning proof.

## Final decision

Marivo will model this capability as typed column bindings on the existing
physical `TableSourceIR`.

The public abstraction is a deterministic table schema adapter:

```text
stable output alias -> atomic physical identifier -> declared Ibis type
```

The framework internally realizes that adapter with a generated, no-table-alias
projection and an explicit Ibis schema. Arbitrary SQL, derived relational
logic, backend-specific virtual-column vocabularies, and temporal execution
policy remain outside the contract.
