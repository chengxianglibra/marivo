# Semantic Authoring Surface Redesign

Date: 2026-06-16

Status: draft

## Problem

The `marivo.semantic` public surface is consumed primarily by coding agents
through a write-run-read loop: an agent reads a `prepare_*` Brief, writes a
decorator/builder into `_domain.py`, runs `verify_object` / load, and reacts to
structured errors. Two classes of friction make that loop more expensive and
error-prone than it needs to be.

1. **Vocabulary drift across layers.** The same concept is named differently
   depending on which layer surfaces it. The authoring verbs say `entity` /
   `dimension`, but error kinds, constraint text, registry keys, and the
   `ms.ref` docstring still say `dataset` / `field` / `root_dataset`
   (`MISSING_DATASETS`, `METRIC_DATASETS_REQUIRED`, `reg.datasets`,
   `"Multi-dataset base metrics must declare root_dataset"`). An agent that hits
   an error reads one word, then fails to find it in the signature it must edit.
   This drift is leftover from the partially executed
   `2026-06-10-semantic-terminology-rename-design.md` rename.

2. **Optional-field mega-classes whose legal combinations live in prose and
   runtime checks instead of in types.** `agent-guide.md` states the intended
   principle:

   > Prefer one entry shape with closed, kind-dispatched variants over
   > optional-field mega-classes: precise types fail loudly, optional-field
   > unions fail silently.

   The codebase already applies this in three places (`versioning=ms.snapshot()
   | ms.validity()`, `additivity="additive" | "non_additive" |
   ms.semi_additive(...)`, and the `ratio` / `weighted_average` / `linear` split
   of derived metrics), but several authoring objects still violate it:

   - `time_dimension(data_type, date_format?, required_prefix?, timezone?,
     sample_interval?)` carries roughly six interdependent cross-field rules
     enforced by inline `_raise` plus a docstring paragraph.
   - `dimension(kind="categorical" | "measure", additivity?, unit?)` welds a
     categorical dimension and a measure into one signature; `additivity` and
     `unit` are runtime-rejected unless `kind="measure"`.
   - `simple_metric(..., source_sql?, source_dialect?)` carries a co-required
     pair as two independent `str | None` kwargs, and `root_entity` is required
     for multi-entity metrics but typed `| None`.
   - `validity(open_end: tuple[Any, ...])` and `file(path, *, format, **options:
     Any)` leak `Any` / untyped `**kwargs` into the public surface, which
     `agent-guide.md` forbids.

   The strongest evidence that prose-encoded rules rot: the current
   `python-semantic-layer.md` "Sampled Semi-Additive" example passes
   `additivity="semi_additive"`, `time_fold=`, and `status_time_dimension=` to
   `simple_metric` — none of which the implemented signature accepts. The
   example cannot run. Encoding the rules in types would have made that drift a
   type error.

## Goals

- One word per concept across every layer: authoring API, `SemanticKind` /
  catalog kind strings, IR/Ref classes, registry keys, error kinds, constraint
  ids and text, hints, prepare parameters and Brief fields, reader details, and
  prose. Completes the `2026-06-10` rename's own acceptance criteria.
- Collapse every cluster of co-dependent parameters into a single argument whose
  value comes from a closed set of constructors, so illegal combinations are
  unconstructable rather than runtime-rejected. Extends the pattern already used
  for `versioning` / `additivity`.
- Zero `Any` and zero `**kwargs` on the public authoring surface — including
  `ai_context`, whose type narrows from `AiContext | dict[str, Any] | None` to
  `AiContext | None` (`AiContext` is a `TypedDict`, so dict literals still pass,
  now key-checked).
- Co-required pairs collapse into one value object.
- Discriminator-unlocked fields live on the variant, not on the parent
  signature.
- Errors that can be decided at decorator-execution time are raised there, not
  deferred to load/assembly time.

These goals serve two ranked priorities, in order: **agent-friendliness** and
**soundness**. This is a clean breaking redesign: no compatibility shim, no
deprecation window, no migration helper. Authored files are rewritten by hand.

## Non-Goals

- No aliases, wrappers, or deprecation period for removed names or shapes.
- No migration tool for externally authored `_domain.py` files.
- No change to the result/help contract defined by
  `2026-06-09-agent-friendly-public-api-design.md` (`repr` / `render` / `show`,
  no stdout, `mv.help(...)`). This design conforms to it; it does not redefine
  it.
- No change to analysis *operator semantics*, parity execution, the evidence
  ledger, or datasource connection runtime. This is not "no analysis code
  changes": the new `MEASURE` kind requires updating the analysis
  input-validation and resolver kind-sets (see "Measure at the analysis
  boundary"), and the typed source variants reach into `marivo.datasource` (see
  "Source surface spans `marivo.datasource`"). Both are kind/vocabulary
  alignment, not new semantics.
- No full UCUM grammar validation (unchanged from current scope).

## Relationship to Prior Specs

This design sits on top of three committed specs and must be read against them.

- **`2026-06-10-semantic-terminology-rename-design.md` — extends and completes.**
  That spec renamed `model→domain`, `dataset→entity`, `field→dimension`,
  `time_field→time_dimension`, `FieldKind→DimensionKind` (with
  `DIMENSION→CATEGORICAL`), and `_model.py→_domain.py`. The authoring and IR
  layers implement it, but `errors.py`, `constraints.py`, the registry, and
  several hints still contain `dataset` / `field` tokens — violating that spec's
  stated acceptance criterion that `grep` for the old terms returns nothing.
  This design finishes that rename across the remaining layers. The canonical
  vocabulary below is identical to that spec's target.

- **`2026-06-10-semantic-terminology-rename-design.md` — one deliberate
  supersession.** That spec explicitly kept `measure` as
  `DimensionKind.MEASURE`, i.e. a kind of dimension, not a standalone object.
  This design **promotes `measure` to a top-level authoring verb with its own
  `SemanticKind`, `MeasureIR`, and `MeasureRef`**. Rationale: keeping measure as a
  dimension kind is the direct
  cause of the `dimension(kind=, additivity?, unit?)` mega-class — `additivity`
  and `unit` are meaningful only for measures, so under the unified-dimension
  model they must be optional-and-runtime-checked. Splitting `measure` out makes
  `additivity` required and `unit` available exactly where they are valid, and
  unconstructable where they are not. This is the single intentional divergence
  from `2026-06-10` and is called out again at every affected point below.

- **`2026-06-09-agent-friendly-public-api-design.md` — conforms to.** That spec
  owns the agent-facing result contract: result-producing APIs return typed
  objects and do not write stdout; every result has a one-line `repr`, a
  `render()`, and a `show()`; help is `mv.help(...)` returning `None`. The
  reader/catalog/details changes in this design add a `measure` kind and rename
  fields, but keep that contract intact. Where that spec's examples use older
  metric shapes (`datasets=`, `decomposition=ms.sum()`), the current code and
  this design supersede them; the result/help contract itself is unchanged.

- **`agent-guide.md` — amends.** The repo guide currently states that
  expression-bearing semantic decorators keep provenance kwargs such as
  `source_sql`. This design replaces those kwargs with
  `provenance=ms.from_sql(sql=, dialect=)`, so the guide's rule is restated in
  terms of the value object. The underlying contract is unchanged: SQL text is
  provenance metadata only, never an executable body. Updating `agent-guide.md`
  is part of this work.

## Design Principles

1. **One concept, one word, everywhere.** `domain · entity · dimension · measure
   · time_dimension · metric · relationship`. No layer uses a synonym.
2. **Closed variants over optional fields.** Each co-dependent cluster is a
   single parameter whose value is one of a closed constructor set. Illegal
   combinations cannot be constructed.
3. **No `Any`, no `**kwargs` on the public authoring surface.** Every parameter
   is concretely typed.
4. **Co-required pairs collapse into one object.** No "if you pass A you must
   also pass B" expressed as two independent `| None` kwargs.
5. **Discriminator-unlocked fields live on the variant.** Fields valid only for
   a sub-shape are attached to that sub-shape's constructor, not the parent.
6. **Fail at decorator-execution time when the information is locally
   available.** Push checks as early as the call where the inputs exist.

## Canonical Vocabulary

| Concept | Canonical term | Notes |
|---|---|---|
| Business namespace | `domain` | `ms.domain`, `SemanticKind.DOMAIN`, `DomainRef`, `_domain.py`. Prose drops "model". |
| Physical table view | `entity` | `ms.entity`, `SemanticKind.ENTITY`, `EntityRef`, `reg.entities`. All `dataset` tokens removed. |
| Categorical row attribute | `dimension` | `ms.dimension`, `SemanticKind.DIMENSION`. `categorical` is the only remaining dimension kind. |
| Quantitative row attribute | `measure` | **New top-level** `ms.measure`, `SemanticKind.MEASURE`, `MeasureRef`. Leaves the dimension family. |
| Time axis attribute | `time_dimension` | `ms.time_dimension`, `SemanticKind.TIME_DIMENSION`, `TimeDimensionRef`. |
| Aggregatable analytic | `metric` | Body metric is `@ms.metric` (was `simple_metric`). |
| Measure aggregation | `aggregate` | `ms.aggregate` (tier-1, no body). |
| Composed analytic | `ratio` / `weighted_average` / `linear` | Unchanged shape; verb names the kind. |
| Join | `relationship` | `ms.relationship`, paired keys (below). |

`metric`, `relationship`, and `datasource` carry no analytics-context ambiguity
and keep their names (consistent with `2026-06-10`).

## Object Model and Authoring Signatures

All authoring entry points are keyword-only except where a single leading
positional argument reads naturally (`table("orders")`, `strptime("%Y%m%d")`,
`on(left, right)`).

### Domain, entity, sources

```python
def domain(*, name, default=True, description=None, ai_context=None) -> DomainRef

def entity(*, name, datasource, source, primary_key=None,
           versioning=None, domain=None, description=None, ai_context=None) -> EntityRef
#   source:     TableSource | ParquetSource | CsvSource
#   versioning: SnapshotVersioning | ValidityVersioning | None   (kept; see below)
```

Source builders replace `file(path, *, format, **options: Any)`:

```python
def table(name, /, *, database=None) -> TableSource
def parquet(path, /, *, hive_partitioning=False, columns=None) -> ParquetSource
def csv(path, /, *, header=True, delimiter=",", columns=None) -> CsvSource
```

Each source builder exposes only its real, named options. The exact option set
for `parquet` / `csv` is enumerated from the `marivo.datasource.scan` reader
contract; no `**options` bag survives.

#### Source surface spans `marivo.datasource`

These builders and their IR are shared with `marivo.datasource`, so the change
is not contained to `marivo.semantic`. Today `EntitySourceIR = TableSourceIR |
FileSourceIR`, where `FileSourceIR` carries `format` plus `options: dict[str,
Any]`, and `md.file(path, *, format, **options)` is public. This design replaces
the `FileSourceIR` arm with typed `ParquetSource` / `CsvSource` IR (no `options:
dict[str, Any]`), removes `md.file` alongside `ms.file`, and adds `md.parquet` /
`md.csv` mirroring the semantic builders. Source serialization (`to_dict`) and
the materializer's file-reading path move to the typed variants. Without this,
the untyped bag stays reachable through `md.file` and the IR, defeating the
zero-`Any` goal.

### Dimension (categorical only)

```python
@ms.dimension(*, name=None, entity, domain=None, description=None, ai_context=None)
def region(orders):
    return orders.region.upper()
```

`kind`, `additivity`, and `unit` are removed. A dimension is always categorical;
measures are a separate object.

### Measure (new top-level)

```python
@ms.measure(*, name=None, entity, additivity, unit=None,
            domain=None, description=None, ai_context=None)
def amount(orders):
    return orders.amount
#   additivity: "additive" | "non_additive" | ms.semi_additive(over=, fold=)   (required)
#   unit:       str | None
```

`measure` is the authoritative declaration site for `additivity` and `unit`.
Both are unconstructable on a categorical dimension because they do not exist on
that signature. Returns a `MeasureRef` backed by a dedicated `MeasureIR` (not a
`DimensionIR` variant), so `DimensionKind` drops `MEASURE`, leaving `CATEGORICAL`
and `TIME`. `help` and the `prepare_measure` Brief frame a measure as *a
row-level quantity that metrics aggregate*, not a metric itself, to keep it
distinct from `metric` / `aggregate`. (Supersedes `2026-06-10`'s
`DimensionKind.MEASURE`; see Relationship to Prior Specs.)

#### Measure at the analysis boundary

Promoting `measure` to its own kind changes what analysis input validation must
accept. Today `normalize_dimension_input` (`semantic_inputs.py`), the anchor
policy (`policies.py`), and the resolver (`resolver.py`) accept only `DIMENSION`
/ `TIME_DIMENSION`, and measures reach them as `DIMENSION` objects with
`dimension_kind="measure"`. Under this design:

- **Agent-facing axis / filter / anchor inputs reject `MEASURE`** with a teaching
  error ("`<ref>` is a measure, which is aggregated, not a group-by axis; slice
  by a categorical dimension or aggregate it into a metric"). A measure is never
  a valid `dimension=` / `where=` / semantic-anchor argument.
- **Measure expressions stay resolvable internally.** The materializer/resolver
  gain a `MEASURE` resolution path so `aggregate(measure=...)` and unit
  propagation can resolve a measure's row-level expression. Operator semantics
  are unchanged.

So the `MEASURE` kind is added to the kind-sets in `semantic_inputs.py`,
`policies.py`, and `resolver.py` — rejected at axis inputs, resolved internally —
rather than silently flowing through the dimension path.

### Time dimension and parse variants

```python
@ms.time_dimension(*, name=None, entity, granularity, parse, is_default=False,
                   domain=None, description=None, ai_context=None)
def order_date(orders):
    return orders.created_at
#   granularity: "year" | "quarter" | "month" | "week" | "day" | "hour" | "minute" | "second"  (required)
#   parse:       DateParse | DatetimeParse | TimestampParse | StrptimeParse | HourPrefixParse   (required)
```

`data_type`, `date_format`, `required_prefix`, `timezone`, and `sample_interval`
leave the top-level signature and move into the closed `parse` variants:

```python
def date() -> DateParse
#   already-temporal date column; no timezone, no format

def datetime(*, timezone, sample_interval=None) -> DatetimeParse
#   timezone REQUIRED (no default); sample_interval allowed for sampled axes

def timestamp(*, timezone, sample_interval=None) -> TimestampParse
#   timezone REQUIRED (no default)

def strptime(format, /, *, data_type, timezone=None, sample_interval=None) -> StrptimeParse
#   data_type: "string" | "integer"; format is the whole point, so it is required

def hour_prefix(prefix, /, *, data_type) -> HourPrefixParse
#   data_type: "string" | "integer"; hour-only partition column; no format, granularity is hour
```

This makes previously prose-encoded rules unconstructable:

- `timezone` is required exactly on `datetime` / `timestamp` and absent on
  `date` — the "omitting timezone for datetime/timestamp is a blocking readiness
  issue" rule disappears as a runtime check.
- `date_format` cannot be attached to a temporal column (no such field on
  `date` / `datetime` / `timestamp`) and is required where a string/integer is
  parsed (`strptime` requires `format`).
- `sample_interval` exists only on sampled-capable parse variants
  (`datetime` / `timestamp` / `strptime`), so it cannot be attached to `date`
  or `hour_prefix`.

One residual cross-field check remains: `granularity` versus the `parse` variant
(for example `minute` / `second` require `datetime` / `timestamp`; `hour_prefix`
fixes hour). It is validated once at decorator-execution time under the new
constraint `TIME_GRANULARITY_PARSE_COMPATIBLE`, replacing roughly six prose
rules with one.

Value-validity checks that are not combinatorial are retained, relocated into
the variant constructors: strptime format parseability, IANA timezone validity,
and `sample_interval` divisibility/positivity.

### Metric family

```python
# tier-2: body metric (was simple_metric)
@ms.metric(*, name=None, entities, additivity, root_entity=None,
           fanout_policy="block", unit=None, provenance=None,
           domain=None, description=None, ai_context=None)
def revenue(orders):
    return orders.amount.sum()
#   entities:      list[EntityRef | str]   (non-empty)
#   additivity:    "additive" | "non_additive" | ms.semi_additive(over=, fold=)   (required)
#   root_entity:   EntityRef | str | None — required (decorator-time) when len(entities) > 1
#   provenance:    SqlProvenance | None
```

```python
# tier-1: aggregate a declared measure (no body). name is REQUIRED (see Identity Rule).
def aggregate(*, name, measure, agg, fold=None, unit=None,
              domain=None, description=None, ai_context=None) -> MetricRef
#   measure: MeasureRef | str
#   fold:    TimeFold | None — meaningful only for semi-additive measures (cross-object residual)
```

```python
# derived (body-free; verb names the kind). Unchanged shape.
def ratio(*, name, numerator, denominator, unit=None, domain=None, description=None, ai_context=None) -> MetricRef
def weighted_average(*, name, value, weight, unit=None, domain=None, description=None, ai_context=None) -> MetricRef
def linear(*, name, add, subtract=(), unit=None, domain=None, description=None, ai_context=None) -> MetricRef
```

Metric value objects:

```python
def semi_additive(*, over, fold) -> SemiAdditive          # kept
def from_sql(*, sql, dialect) -> SqlProvenance            # new: collapses source_sql + source_dialect
```

`provenance=ms.from_sql(sql=..., dialect=...)` enables parity verification;
omitting `provenance` marks the metric python-native and trusted. The
`source_sql` / `source_dialect` co-required pair is gone. Derived metrics must
omit `provenance` (a `from_sql` on a derived metric is a constraint error, as
today).

### Relationship

```python
def relationship(*, name, from_entity, to_entity, keys,
                 domain=None, description=None, ai_context=None) -> RelationshipRef
#   keys: list[JoinKey]

def join_on(from_key, to_key, /) -> JoinKey
#   from_key, to_key: DimensionRef | str

# ms.relationship(name="orders_to_customers", from_entity=orders, to_entity=customers,
#                 keys=[ms.join_on(order_customer_id, customer_id)])
```

Each `JoinKey` binds one left and one right key, so the two parallel lists
(`from_dimensions` / `to_dimensions`) and their "lengths must align" rule are
gone; mismatched arity is unconstructable. The positional `(from_key, to_key)`
order mirrors `from_entity` / `to_entity`.

A composite join is a list of atomic pairs, AND-ed together:

```python
ms.relationship(
    name="orders_to_line_items",
    from_entity=orders, to_entity=line_items,
    keys=[
        ms.join_on(order_id, li_order_id),
        ms.join_on(tenant_id, li_tenant_id),
    ],
)
```

Because each pair binds its own left and right, multi-field joins cannot desync
the way parallel lists could. A single constructor holding parallel
`left=[...]` / `right=[...]` lists is intentionally rejected: it would
reintroduce the arity mismatch this shape exists to remove.

### Reference helper

```python
def ref(id: str) -> str   # unchanged: qualified "<domain>.<object>" pass-through
```

## Identity Rule

Identity is always mandatory; there is no nameless object. How it is carried
depends on whether the object has a body.

- **Body-bearing decorators — `dimension`, `measure`, `time_dimension`,
  `metric` — keep `name=None`.** The `def` name is the identity (last segment of
  the semantic id). Python guarantees the `def` has a name, it is visible at the
  declaration site, and it cannot duplicate or diverge from a separate kwarg.
  `name=` is an override for the rare case where the semantic id must differ from
  a valid Python identifier or must avoid a symbol collision.

- **Body-free calls — `domain`, `entity`, `relationship`, `ratio`,
  `weighted_average`, `linear`, and `aggregate` — `name` is required.** There is
  no `def` to carry identity, so `name=` is the only carrier and has no fallback.

This changes one current behavior: `aggregate` today defaults its name to the
measure's column name (`measure_id.rsplit(".", 1)[-1]`), which is
identity-by-inference from another object. `aggregate.name` becomes required.

It also resolves a contradiction in `python-semantic-layer.md`, which currently
states both "the omitted name falls back to the symbol name as identity" and
"the symbol name is only a local alias, not part of the semantic id." The
unified rule: a body-bearing decorator's `def` name **is** the last segment of
its semantic id; assigned variables and body-free calls' Python variables are
local aliases, and identity is carried by `name=`.

## Value-Object Catalog

| Slot | Closed variants | Footgun removed |
|---|---|---|
| `entity(source=)` | `table(...)` · `parquet(...)` · `csv(...)` | `file(path, format=, **options: Any)` untyped bag + format discriminator |
| `entity(versioning=)` | `snapshot(...)` · `validity(...)` · `None` | kept; `validity.open_end` retyped `tuple[str \| None, ...]` (drops `Any`) |
| `time_dimension(parse=)` | `date()` · `datetime(timezone, sample_interval?)` · `timestamp(timezone, sample_interval?)` · `strptime(format, data_type, timezone?, sample_interval?)` · `hour_prefix(prefix, data_type)` | date_format/required_prefix/timezone/sample_interval cross-field tangle |
| `*.additivity=` | `"additive"` · `"non_additive"` · `semi_additive(over, fold)` | kept (already correct) |
| `metric(provenance=)` | `from_sql(sql, dialect)` · `None` | `source_sql` / `source_dialect` independent `\| None` pair |
| `relationship(keys=)` | `[join_on(left, right), ...]` | `from_dimensions` / `to_dimensions` parallel-list arity |

## prepare / Brief Conformance

Every authored object keeps a matching `prepare_*` entry point.

- **Vocabulary** in parameters and Brief fields uses `entity` / `dimension` /
  `measure`. The physical input parameter stays `column`: a `column` is the raw
  physical column being promoted into a `dimension` or `measure`, a distinct
  layer from the semantic object.

- **`measure` joins the ladder.** The authoring ladder becomes `domain → entity
  → dimension → measure → time_dimension → metric → relationship → cross-entity
  metric → derived`. A new `prepare_measure(*, entity, column, scope=None) ->
  MeasureBrief` profiles a measure candidate (numeric / additivity hints).
  `verify_object` accepts measure refs; ladder-order enforcement
  (`LadderOrderError`) includes measure.

- **Brief candidate fields emit the new variants.** `FormatCandidate` describes
  which `parse` variant to author rather than a bare `date_format` string — for
  example `{variant: "strptime", format: "%Y%m%d", data_type: "string"}`,
  `{variant: "datetime", timezone: "Asia/Shanghai"}`, or `{variant:
  "hour_prefix", prefix: "..."}`. `EntityBrief` source candidates distinguish
  `table` / `parquet` / `csv`. `RelationshipBrief` join-key probes map to
  `keys=[ms.join_on(left, right)]` pairs.

- **Parameter rename.** `prepare_relationship(from_dimensions=, to_dimensions=)`
  becomes `prepare_relationship(*, from_entity, to_entity, keys: list[tuple[str,
  str]], scope=None)`, structurally matching the authored `keys=[ms.join_on(...)]`.

## Reader / Catalog / Details / Readiness

Conforms to the result/help contract of `2026-06-09`; only kinds and field names
change.

- **New kind.** `SemanticKind` gains `MEASURE = "measure"`; a new `MeasureDetails`
  carries `additivity` and `unit`, which are removed from `DimensionDetails`.
  `catalog.list(kind="measure")` and `SemanticKindInput` support it.

- **Details reflect the new value objects.** `TimeDimensionDetails` exposes
  `parse_kind` / `format` / `timezone` / `sample_interval`; `EntityDetails`
  exposes the source variant (`table` / `parquet` / `csv`); `MetricDetails` uses
  `entities` / `root_entity` (never `dataset`) and exposes `provenance`
  (`from_sql` or none).

- **Entity children** now include dimensions, measures, time dimensions,
  metrics, and relationships.

- `repr` / `render` / `show` text and all kind strings use the canonical
  vocabulary.

## Errors and Constraints

- **Rename across all layers** (finishes the `2026-06-10` rename, which renamed
  the IR classes but left internal storage on the old terms):
  `MISSING_DATASETS → MISSING_ENTITIES`, `METRIC_DATASETS_REQUIRED →
  METRIC_ENTITIES_REQUIRED`, `METRIC_ROOT_DATASET_REQUIRED →
  METRIC_ROOT_ENTITY_REQUIRED`, `METRIC_ROOT_DATASET_VALID →
  METRIC_ROOT_ENTITY_VALID`; the `Registry` fields `models → domains` (still
  `models` — a `2026-06-10` miss), `datasets → entities`, and `fields →
  dimensions`, with the `registry.datasets` / `registry.fields` call sites in
  the loader and elsewhere updated; and every hint / message string mentioning
  `dataset` / `field` / `model`.

- **Delete the combinatorial-legality checks that become unconstructable.** The
  inline `time_dimension` raises for `date_format` on temporal columns,
  `date_format` required on string/integer, and `required_prefix` legality; the
  `dimension` raises for `additivity` / `unit` only on `kind="measure"`; and the
  "add `source_dialect` when `source_sql` is set" check. The
  `HOUR_TIME_DIMENSION_PREFIX` and `SUBDAY_GRANULARITY_WITHOUT_TIME` rules are
  absorbed into the `parse` variants and `TIME_GRANULARITY_PARSE_COMPATIBLE`.
  Only combinatorial legality is removed.

- **Keep value-validity and cross-object / data checks that cannot be typed.**
  strptime parseability and IANA timezone validity (relocated into the variant
  constructors); `TIME_DIMENSION_DTYPE_COMPAT` (declared type vs body ibis
  dtype); `METRIC_ROOT_ENTITY_VALID` (root is one of `entities`);
  `RELATIONSHIP_ENDPOINTS`; the `*_REF_EXISTS` family; `METRIC_GRAPH_ACYCLIC`;
  primary-key sample uniqueness; parity; `STATUS_TIME_DIMENSION_*`;
  `LINEAR_UNIT_COMMENSURABLE`.

- **Add / move earlier.** `METRIC_ROOT_ENTITY_REQUIRED` fires at
  decorator-execution time when `len(entities) > 1`. New
  `TIME_GRANULARITY_PARSE_COMPATIBLE` covers the single residual time cross-field
  rule.

## Public Surface Delta

The `__all__` snapshot test updates accordingly.

- **Added:** `measure`, `MeasureRef`, `MeasureDetails`, `MeasureBrief`,
  `prepare_measure`; `parquet`, `csv`, `TableSource`, `ParquetSource`,
  `CsvSource`; `date`, `datetime`, `timestamp`, `strptime`, `hour_prefix`,
  `DateParse`, `DatetimeParse`, `TimestampParse`, `StrptimeParse`,
  `HourPrefixParse`; `from_sql`, `SqlProvenance`; `join_on`, `JoinKey`; the
  `SemanticKind.MEASURE` kind string.

- **Removed:** `simple_metric` (renamed `metric`), `file`, the
  `dimension(kind=/additivity=/unit=)` parameters, the
  `time_dimension(data_type=/date_format=/required_prefix=/timezone=/sample_interval=)`
  parameters, `*.source_sql` / `*.source_dialect`,
  `relationship(from_dimensions=/to_dimensions=)`.

## Phasing

Scope is the whole public surface; implementation sequences into three plans,
each independently testable.

1. **Object model and authoring write-surface.** New verbs, value objects, refs,
   and IR; the identity rule; decorator-time checks; the errors/constraints
   rename plus deletion of the unconstructable checks. Everything else conforms
   to this layer.
2. **prepare / Brief.** `prepare_measure` and the measure ladder rung; Brief
   candidate fields emitting `parse` / source / provenance / keys variants; the
   `prepare_relationship` parameter rename.
3. **reader / catalog / details / readiness.** The `measure` kind and
   `MeasureDetails`; details surfacing the new variants; vocabulary in
   `repr` / `render` / `show`; the `__all__` snapshot update.

## Open Questions

- Exact named option set for `parquet` / `csv`, enumerated from
  `marivo.datasource.scan`.

## Acceptance Criteria

- A `grep` for the old *semantic-term identifiers* — `Registry.models` /
  `.datasets` / `.fields` and the `registry.datasets` / `registry.fields` call
  sites, the old IR/Ref class names, `kind="dataset"` / `"field"` strings,
  `ms.file` / `md.file`, `simple_metric`, `source_sql` / `source_dialect`,
  `from_dimensions` / `to_dimensions`, and `dimension_kind="measure"` — in
  `marivo/` and `tests/` returns nothing. The criterion targets semantic-term
  usages only; incidental matches such as `dataclasses.field(...)` are out of
  scope.
- `ms.measure`, `ms.metric` (body), `ms.aggregate`, `ms.ratio`,
  `ms.weighted_average`, `ms.linear` are the only metric/measure authoring
  names; `ms.table`, `ms.parquet`, `ms.csv` the only entity sources; `ms.date`,
  `ms.datetime`, `ms.timestamp`, `ms.strptime`, `ms.hour_prefix` the only time
  parse variants; `ms.from_sql` the only metric provenance carrier;
  `ms.relationship(keys=[ms.join_on(...)])` the only relationship shape.
- No public authoring parameter or return type is `Any` or carries `**kwargs`;
  `ai_context` is `AiContext | None` (the `dict[str, Any]` arm is removed).
- A measure declared as categorical-style (passing `additivity` / `unit` to
  `ms.dimension`) is a `TypeError`, not a runtime constraint error.
- A `time_dimension` with a temporal `parse` and a `date_format`, or a
  `datetime` parse without a timezone, is unconstructable.
- A multi-entity `ms.metric` without `root_entity` fails at decorator-execution
  time.
- `catalog.list(kind="measure")` returns measures; `MeasureDetails` carries
  `additivity` / `unit`; `DimensionDetails` does not.
- `make typecheck`, `make test`, and `make lint` pass.
