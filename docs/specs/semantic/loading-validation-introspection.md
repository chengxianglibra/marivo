# Loading, Validation, and Introspection

Status: accepted target design; amended 2026-09-07 for lazy Analysis. The amended
identity, version-resolution, and aggregation validation requirements below are
design commitments, not implementation evidence. Current eager behavior and
live Help remain executable until the coordinated cutover.

This document describes the runtime side of
`marivo.semantic`: how authored Python files become a loaded registry, how agents
and analysis read that registry, how objects materialize to Ibis, and how the
multi-stage fail-closed validation model reports problems. It complements
[semantic-object-model.md](semantic-object-model.md) (object contracts) and
[authoring-workflow.md](authoring-workflow.md) (the write loop).

See also:

- [overview.md](overview.md) — the design goals these mechanics enforce.
- `../agent-friendly-public-surface.md` — the cross-module result protocol this
  layer implements.

## Registry and loader

A semantic project is one explicit workspace boundary. Its local declarations
live under `models/semantic/`, while optional external authored `models/` roots
come from `marivo.toml [semantic].layer_paths`. `ms.load(...)` executes the
trusted local Python files in those semantic directories, assembles the
decorators' side effects into an in-memory registry, and returns a
`SemanticCatalog`.

```python
import marivo.semantic as ms

catalog = ms.load()  # env, nearest ancestor manifest, or current directory
catalog = ms.load(workspace_dir=".", domains=["sales"])  # exact workspace root + filter
catalog.domains.show()
```
Loader rules:

- Each domain calls `ms.domain(name=..., owner=...)` once in
  `<root>/<domain>/_domain.py`, with `name` equal to the directory. The
  `_domain.py` is the domain entrypoint and may hold all of that domain's
  objects.
- Object identity comes from an explicit `domain=` or the domain's default
  domain (`default=True`), **not** from the file path. File paths are used only to
  discover candidate files and to run organization checks.
- Loading is **two-pass**: pass one collects all declarations, pass two resolves
  refs and validates dependencies. Filenames and sibling sort order do not affect
  whether a valid model loads.
- Model roots are **layered / multi-root**: a project can compose a shared base
  root with a local overlay.
- Python files are trusted local code and are not sandboxed. With no explicit
  workspace, `ms.load()` resolves `MARIVO_PROJECT_ROOT`, then the nearest
  ancestor manifest, then the current directory. An absent or empty local
  `models/semantic/` is a valid empty project. The low-level loader still
  accepts an exact semantic directory and fails closed when that path exists but
  is not a directory.
- On success the registry is `ready`; on failure it becomes `errored` with
  structured `load_errors` retained for the fix loop.

## Reader and introspection

`ms.load()` returns a `SemanticCatalog` — the deterministic, agent-facing read
surface. It does not re-parse files or rely on process-global state, and it does
not use fuzzy or embedding-based recall.

```python
import marivo.semantic as ms

catalog = ms.load()
catalog.metrics.show()

sales = catalog.domains.get("sales")
orders = sales.entities.get("orders")
orders.dimensions.show()

revenue = catalog.require(ms.ref.metric("sales.revenue"))
revenue.details().show()
```
`SemanticCatalog` exposes one global collection per object type:
`catalog.domains`, `catalog.datasources`, `catalog.entities`,
`catalog.dimensions`, `catalog.time_dimensions`, `catalog.measures`,
`catalog.metrics`, `catalog.relationships`, `catalog.events`,
`catalog.business_orders`, `catalog.state_models`, `catalog.period_calendars`, `catalog.temporal_sets`,
and `catalog.work_schedules`. Each is a
`CatalogCollection[T]` with `.items`, `.refs`, `.get(key)`, `.render()`,
`.show()`, `len()`, and iteration. `catalog.require(ref)` is the
exact lookup entry point for IDs obtained from errors, logs, or persisted state.
`SemanticCatalog` itself follows the bounded result protocol: `repr(catalog)`
points to `catalog.show()`, whose zero-query card lists only non-empty
collections as copyable `catalog.<collection>.show()` calls and compresses all
empty collections into one summary.

| API | Meaning |
|---|---|
| `ms.load(workspace_dir=None)` | Load the project and return a `SemanticCatalog`. |
| `catalog.require(ms.ref.<kind>(path))` | Resolve and validate one `CatalogEntry` by exact typed ref; this global lookup remains ref-only. |
| `catalog.domains`, `catalog.metrics`, … | Typed global or scoped collections; `.get(...)` accepts a local name, full path, displayed same-kind typed key, or exact same-kind ref within that collection's scope. |
| `catalog.preview(entry_or_ref, scope=scope_or_mapping, source_bindings=None)` | Read one current semantic object through an explicit datasource scope. |
| `catalog.preview_many(entries_or_refs, scope=scope_or_mapping, source_bindings=None)` | Validate an ordered batch and its complete exact entity-ref scope mapping before connection. |
| `catalog.source_health(entries_or_refs, checks=(), scope=None)` | Check current connectivity and schema/capability identity, plus only explicitly declared bounded data expectations, independently from readiness. |
| `catalog.readiness(refs=[entry_or_ref_or_runtime_expr])` | Zero-query readiness gate over current entries, exact refs, or closed runtime metric expressions. |
| `ms.richness(demand=None)` | Advisory demand-ranked coverage/depth report. |

Ordinary preview returns current execution results and never persists an
authoring checkpoint. Dedicated period-calendar, temporal-set, and work-schedule
preview may publish their immutable certified artifact after an exhaustive
bounded read.
Ordinary Trino, PostgreSQL, and MySQL previews also acquire isolated readers
before source binding, configured with each normalized scope's timeout. Each
batch row or metric group uses its own reader and timeout; success or failure
releases that reader and restores the prior connection cache. The authoring
timeout guard covers source binding and collection. PostgreSQL uses a read-only
transaction with `statement_timeout`, Trino uses `query_max_run_time`, and MySQL
uses its owned-reader cancellation guard with an independent control connection.
Certification uses isolated read-only connections configured with the authored
scope timeout before binding or collecting rows. They close on success or
failure, and the prior ordinary-preview connection cache is restored. The
adapter's authoring-timeout guard remains mandatory; a backend without one
rejects certification rather than issuing an unbounded read.
MySQL certification uses a separately approved, session-scoped SELECT limit on
the fresh connection. Both installation and readback are audited; a denied or
mismatched limit rejects before collecting certification rows. Native source
capture failure reports a structured SemanticRuntimeError with backend code,
scoped timeout and actual query-submission state. The isolated connection closes,
and no new or replacement certified snapshot is published. Existing typed errors
retain their original repair. MySQL's certified SELECT limit remains distinct
from ordinary preview's cancellation guard, raw-SQL timeout and Analysis cancellation.
For an exact BusinessOrder ref or a StateModel bound to one, scoped readiness
includes the order's Event, role, and sequence-field dependencies. It reports
an advisory that source sequence values and Event history remain unverified;
the BusinessOrder declaration itself is not an executable analysis input.
R7 must validate those values before matcher or replay consumption.

### Navigation matrix

Navigation is limited to explicit ownership or applicability relationships. Each
container object exposes typed collection properties:

| Object | Navigation properties |
|---|---|
| `Domain` | `entities`, `dimensions`, `time_dimensions`, `measures`, `metrics`, `relationships`, `events`, `business_orders`, `state_models` |
| `Datasource` | `entities` |
| `Entity` | `dimensions`, `time_dimensions`, `measures`, `metrics`, `relationships`, `events`, `business_orders`, `state_models` |
| `Relationship` | `from_entity`, `to_entity` |
| `Dimension` / `TimeDimension` / `Measure` / `Metric` | leaf objects — use `details()` for dependency information |

Scoped collections are the normal way to remove ambiguity:
`catalog.domains.get("sales").entities.get("orders").dimensions.get("region")`.

### Self-teaching object cards

Every container object's bounded `render()` / `show()` card advertises non-empty
navigation properties as copyable `entry.<collection>.show()` calls with counts.
Empty child collections are compressed into one summary, so the agent can still
distinguish empty state from unsupported navigation without reading one line per
zero-count collection.
Every entry card names its exact kind and full path, exposes `.ref`, and
advertises `details()`, `render()`, and `show()`. Metric cards
additionally render bounded exact refs for effective
entities, candidate dimensions, candidate time dimensions, required
relationships, and component/measure lineage when present. An empty time-axis
set is explicit (`candidate_time_dimensions: none`). Omitted members include an
omitted count and a concrete full read such as `details().show()`; cards never
rank axes or recommend an operator.

`entry.show()` is the first read for key definition and dependency facts;
`entry.details()` provides structured expansion. Details cards do not prescribe
readiness merely because a definition was inspected.

`marivo.help(entry)` composes that current catalog identity and usage navigation
with the analysis registry's kind-level handoff. It shows only the first focused analysis target
or policy choices, their registered call shapes, and the artifact family of an
operator result. It does not infer readiness, enumerate downstream operators,
or create a second programmable navigation result. The caller inspects the
focused target for companion inputs, executes it only after readiness, and then
uses the returned analysis artifact for state-specific continuation. A bare `Ref`
does not receive this handoff because project membership and readiness are
unknown until it is resolved to a current entry.

The ordered catalog-member contract owns the global collection names used by
the runtime catalog, semantic type help, and analysis catalog help. Adding a
semantic kind must update that contract and pass the live-property consistency
check; parallel hand-maintained discovery lists are not allowed.

### Analysis-agent discovery and handoff

The analysis agent uses the question-scoped session catalog. The packaged
`marivo-analysis` skill owns only routing and boundary decisions; it does not
duplicate collection or entry API recipes. The live help surface owns the
mechanical loop:

```python
import marivo
import marivo.analysis as mv

marivo.help("analysis.catalog")
marivo.help("analysis.catalog.metrics")

session = mv.session.get_or_create(
    "investigation",
    question="Why did revenue decline?",
)
catalog = session.catalog
catalog.show()
collection = catalog.metrics
collection.show()                              # bounded list when identity is unknown
entry = collection.get("metric:sales.revenue")  # full path or displayed typed key
entry.show()
entry.details().show()
marivo.help(entry)                             # identity, usage navigation, kind handoff
dataset = session.observe(entry, time_scope=mv.time_scope(start='2026-07-01', end='2026-10-01')).with_time_axis(ms.ref.time_dimension("sales.orders.order_date"), grain=mv.grain('month')).aggregate()
```
`ms.load()` and `session.catalog` build separate immutable catalog snapshots
over the same semantic project. They share the same browse contract and normally
share a definition fingerprint when project state is unchanged, but a
`CatalogEntry` remains owned by the instance that produced it. Analysis must
reacquire entries from the current `session.catalog`; stale or cross-catalog
entries fail closed with a current-catalog repair.

When an exact ref comes from configuration, persistence, or logs, the agent
uses the exact-ref contract instead of browsing. `CatalogEntry` help owns the
choice between passing the current entry and passing `entry.ref`; `Ref` help
owns the distinction between typed identity and current catalog membership.
The analysis operator's focused help remains authoritative for accepted input
families, readiness, and the consuming call shape.

### Lookup rules

`CatalogCollection.get(key)` accepts a local name, a full semantic path, a
displayed same-kind typed key such as `metric:sales.revenue`, or an exact
same-kind `Ref`. A local name must be unique in the current collection view; a
full path, typed key, or ref must still be visible within that view. A scoped
collection therefore cannot be escaped by passing a global identity. If a local
name is ambiguous, lookup raises a structured error listing bounded exact
full-path calls. Wrong-kind typed keys and refs point to the owning global
collection without being resolved implicitly.

`catalog.require(ref)` remains the strict cross-kind global membership operation
and accepts only exact typed refs. It does not share the collection string
grammar; rejected short names may be searched for teaching suggestions but are
never resolved implicitly.

### Structured lookup errors

Catalog lookup errors follow the shared semantic error model. They state the
expected input, received input, relevant scope, and a concrete next call derived
from the loaded index:

- **Ambiguous local name:** list bounded exact full paths and show
  `collection.get("<full-path>")`.
- **Wrong object type:** identify the typed ID's real type and point to the
  corresponding global collection.
- **Outside current scope:** state that the object exists globally, identify its
  owning path, and show the strict global `catalog.require(ref)` alternative.
- **Not found:** show bounded close matches from the current collection.
- **Stale/cross-catalog entry:** reject the ephemeral handle. A stale
  same-project entry receives an exact reacquisition retry only when the same
  path and kind still exist; entries owned by another catalog do not.

`catalog.require(ref).details()` returns a structured details dataclass (not just
text). Every details type exposes `ref`, `kind`, `name`, `domain`, `context`,
`business_definition`, `guardrails`, `python_symbol`, `source_location`,
`parents`, `children`, and
`dependents`, plus type-specific facts (datasource `backend_type`/`fields`/
`env_refs`; entity `datasource`/`source`/`primary_key`/`versioning`; measure
`additivity`/`unit`; time dimension parse/granularity/timezone; metric
entity/composition/additivity/unit; relationship join keys).
Metric details also expose `effective_entities`, `candidate_dimensions`,
`candidate_time_dimensions`, and role-keyed `measure_lineage`. Derived metrics
keep their authored `entities=()` shape; effective entities and measures are
projected recursively from composition components. Candidate axes are dimensions
owned directly by those effective entities. They are static discovery facts, not
a promise that every cross-entity relationship or fanout plan is executable;
`session.observe(...)` remains the authority for plan validity.

The compiled catalog derives identity and intrinsic aggregation facts from the
same canonical declarations used by validation and analysis. Entity identity is
its ordered `primary_key` (`K`); versioned source row keys derive from `K` plus
the version coordinate. Metric facts retain computation roots per component,
spatial-before-temporal order, fixed null/empty rules, unit algebra, and exact
state requirements. They are shared internal contracts, not new authoring
fields, a second public capability index, or a promise that every materialized
Dataset can perform every transformation. Bounded details and Help disclose
facts through their existing native owners; Dataset `contract()` combines them
with current coordinates, selection, and retained state.

An analysis observation resolves its Metric contract once and passes that result
to dispatch and component binding. Within one runtime-expression forest lowering,
successful lowering of the same exact catalog Metric Ref is reused; every root,
occurrence path and presentation label remains ordered and independently counted
against expression budgets. These results do not survive the consuming call.
Later consumers still interpret the original callable bodies and current declarations.
The shallowly frozen registry and compiled dependency inventory are not an
authority for a load-lifetime interpretation cache. Source schema and unknown
physical types remain the responsibility of each current component binding.

Relationship key coverage alone cannot advertise an unresolved historical
Entity as a unique join side. The consuming operation must supply the exact
temporal anchor; source cardinality follows the Entity declaration without an
automatic source-data proof. Population identity,
current source bindings, and persisted Artifact state remain separate authority.
`DerivedMetricDetails.render()` / `.show()` additionally include an
`expression_tree` table that expands every authored component occurrence through
intermediate metric refs to its named measure or entity inputs. The table preserves
ratio roles, linear signs and declaration order, cumulative axes and anchors,
aggregate filters and folds, and weighted-mean inputs. A metric implemented by an
Ibis function body ends honestly at `expression_body` when it has no named base
measure. This is a rendering projection only: `DerivedMetricDetails` adds no
`expression_tree` field or traversal API.
Secrets appear only as env-var *names* — a resolved secret value is never
rendered.

`python -m marivo help` is an environment bootstrap only.
`marivo.help(target=None)` is the sole public help coordinator, usable without
an active project. Qualified content is rendered from its native datasource,
semantic, or analysis registry; `md` and `ms` expose no `.help()` aliases.
`marivo.help("semantic.constraints")` is the focused entry to the authoring /
validation constraint catalog. Help describes what parameters must satisfy; it
carries no runtime data.

Source-mutating constructors are described by one internal authoring-source
registry. Focused constructor help therefore identifies the declaration as a
loader fragment, gives its exact placement under
`models/semantic/<domain>/`, links prerequisite help targets, states the
required dependencies and static constraints, and ends with the generated
loaded-object postcondition:

```python
catalog = ms.load()
entry = catalog.<collection>.get("<canonical-identity>")
entry.show()
```

The ordered catalog-member contract supplies `<collection>`, so focused help,
the live catalog, and the acquisition path cannot drift independently.

## Result contract

Semantic result construction and inspection are silent by default. Only
explicit display writes stdout:

- `result.show()` — print a bounded result card and return `None`.
- `result.render()` — return the same bounded text without writing stdout.
- `repr(result)` — a one-line cold-start hint pointing to `.show()`.

Semantic authoring results expose bounded detail plus structured errors and
typed repairs. Callable operations, effects, and input facts come from the
native registry without a shared lifecycle-state result.

Readiness cards disclose the checked dependency closure, ready inputs,
blockers, warnings, affected refs, and available repairs with qualified Help.
Source-health reports summarize every check and expose non-successful checks'
affected refs and repair; individual check cards include existing observed facts,
user-data query disclosure, and exact scopes. Cards read only retained facts and
use the shared output budget and full-read recovery.

Error classes have static Help contracts. Every registered error instance has a
current briefing, whether or not a repair exists: kind, message, expected,
received, refs, and location remain visible. `SemanticLoadFailed` shows ordered
child errors. Dynamic values and child lists may be explicitly omitted to fit
the current-briefing budget, with full reads through error fields or `.errors`.
Static Help remains strictly budgeted; instance display never loads or queries.

Catalog browsing returns a `CatalogCollection` (not a raw list); use `.items`,
`.refs`, `.render()`, and `.show()`. This is the semantic-layer instance of the
cross-module agent result protocol described in
`../agent-friendly-public-surface.md`.

## Materialization

Materialization recombines registered Python functions into Ibis objects. It is
an implementation detail of semantic internals and the analysis runtime — it is
**not** a public `SemanticProject` method. Agent-facing reads and previews go
through the catalog:

```python
catalog = ms.load()
revenue = catalog.metrics.get("sales.revenue")
catalog.preview(
    revenue,
    scope=md.unpruned(max_rows=1000, timeout_seconds=30),
).show()
catalog.readiness(refs=[revenue]).show()
```
These runtime methods accept an exact entry from the current compiled catalog or
its exact ref and normalize immediately to the canonical ref. Ordered batches
are normalized completely before preview begins. No `CatalogEntry`, catalog
pointer, or object identity reaches readiness output, persistence, replay, or
recovery. Semantic authoring constructors and
decorators remain ref-only.

Backend resolution rules:

- The compile target defaults to the `backend_type` of the metric's datasource.
- The backend is obtained through the internal datasource connection service; the
  live backend dialect must match the declared `backend_type` or the operation
  fails closed.
- With no live backend, a dry compiler for that `backend_type` is used when
  available; otherwise a structured `compile_error` is returned rather than
  executing a query.
- Multi-datasource metrics fail closed in compile (federation is a separate
  design).

Target-design temporal materialization receives the exact temporal boundary and
its closed interpretation from the consuming Analysis operation: an instant or
immediately before an excluded endpoint. This is an internal binding, not a new
public authoring argument. Snapshot resolution selects the period containing
the instant or the endpoint's left limit under the declared grain and timezone;
the latter selects the preceding period at an exact period boundary. A missing
exact snapshot follows the consuming operation's empty-result semantics. No arbitrary timestamp tick, implicit latest/nearest
available snapshot, per-Entity last-known selection, or unconditional
observe-window-end anchor is inserted. For closed-open validity, instant
resolution uses `valid_from <= at < valid_to`, while endpoint-left resolution
uses `valid_from < end <= valid_to`, with the declared open-end rule. Other
admitted interval closures retain their exact boundary rules. Overlapping
matches fail. Only the resolved relation can claim uniqueness by Entity `K`.

Temporal declaration and source evidence remain distinct. Snapshot declares an
expected complete cross-section; runtime must independently establish required
coverage and integrity. A present partition or a successful bounded preview is
not proof that it is complete. Materialization cannot repair an incomplete
snapshot by mixing older Entity rows into it.

Semantic Ibis construction is not publication of a lazy Analysis Dataset.
Logical Analysis uses current governed definitions; materialized Analysis reads
the exact committed rows and parts authorized by its Artifact. Semantic lineage
never permits reconstructing absent retained state or replaying an Artifact's
original source.

Entity source provenance is source-aware. An ordinary table is `IBIS_TABLE`; a
table with a column projection is `TABLE_PROJECTION` and never carries a raw
SQL snippet; a retained Ibis SQL node is `SQL_VIEW`. A projected table still has
one physical source. Metric-graph physical leaves therefore record one
`physical_sources` item per entity with the entity, datasource, and the source's
single canonical `to_dict()` payload. Output aliases remain inside that source's
`columns` mapping rather than appearing as synthetic physical tables. The same
source payload participates in the semantic dependency digest, so canonical
reordering is identity-stable while rebinding or renaming a projected field
changes identity. Physical types are not semantic declarations; the observed
source types enter downstream realized schemas when execution first needs them.

To inspect a metric's caliber without executing analysis, use typed details and
scoped readiness after the project has loaded successfully. Use
`catalog.preview(..., scope=...)` for a scoped runtime check. Historical SQL
does not execute as a Semantic parity diagnostic.

## Validation and failure semantics

The semantic layer validates in fail-closed stages. Each stage proves a
different class of contract; a stage that cannot prove its contract raises a
structured error instead of degrading.

### Decorator-time

Checks that a single declaration is locally self-consistent: duplicate
domain/datasource/entity/dimension/metric names; wrong ref types; illegal
cross-domain/cross-entity refs; an expression-bearing decorator with no explicit
`domain=` and no default domain in context; a base metric missing `entities=[...]`;
a derived metric that carries entity parameters, lacks composition components, or
reads an entity table in its body; a decorator/metadata call executed outside a
loader context; a metric body that violates the single-`return`-expression rule
or calls a decorated metric function / an Ibis SQL escape hatch.

Entity `primary_key` declares identity, not physical version-row uniqueness.
Version fields are declared separately and are not required in `primary_key`.
Snapshot/validity declarations must supply their own well-formed temporal
coordinates and boundary metadata. No `business_key`, `physical_key`, generic
allocation policy, or author-set `rollup_safe`/`membership_stable` field is added.

### Load / assembly-time

After the loader executes project files, assembly validation checks cross-object
relationships: a missing or mismatched `_domain.py`; `ms.domain(...)` in the wrong
file or a `_domain.py` declaring multiple domains; an entity referencing an
unknown datasource; a metric referencing an unknown entity or component; a
cross-domain `ms.ref.<kind>(path)` that is missing, type-mismatched, or cyclic; an
`entities=[...]` count that disagrees with the function arity; an hour time
dimension missing its required prefix; invalid relationship endpoints, join
dimension refs, entity membership, or arity. Relationship assembly also resolves
each join ref to one direct source column on its declared endpoint, rejects
repeated key columns, and derives structural cardinality from coverage of each
endpoint's complete stable `primary_key`. Target match completeness is not
declared on Relationship and remains unknown at static load. The consuming
operation owns any required-match policy for its selected members, role, and
exact version; an allowed absence also needs that operation's explicit result
semantics. Versioned one sides still need exact version resolution, and missing
matches or duplicate source rows are not proven at load.
Tier-1 metric filters must resolve
every local key to a declared dimension on the target entity; failures use
`invalid_filter` with focused `semantic.where` repair. On failure the registry
is `errored` and retains `load_errors`.

When `K` is declared, assembly derives the versioned source row key as
`(K, snapshot_coordinate)` or `(K, valid_from)`, without rewriting `K`.
An unkeyed computation source contributes no identity-based uniqueness proof.
Version coordinate refs must resolve
on the owning Entity with coherent temporal types and timezone rules. A
source-only Entity may omit `K`; Population input, Event participant subject,
and StateModel subject require a complete non-empty identity signature. Analysis
checks the first of those consumer boundaries; semantic Event/StateModel
assembly checks their own subject references. Static assembly does not inspect
actual nulls, duplicates, or overlapping validity intervals; Analysis trusts
those source-data declarations.

Metric assembly lowers each component's computation root and intrinsic
aggregate, filter, fold, unit, null/empty, and cumulative contract. Different
component roots alone do not invalidate a derived graph. Analysis later binds
every occurrence to one chosen Population and exact coordinates and validates
paths, allocation, time compatibility, and required state. An opaque Tier-2
expression is not granted reaggregation from an additivity label: an absent
exact transformation contract yields a targeted consumer blocker.

For an entity backed by `md.table(columns=...)`, assembly also proves that every
`primary_key` entry and every direct `ms.dimension_column(...)`,
`ms.time_dimension_column(...)`, or `ms.measure_column(...)` reference names a
declared stable output alias. A missing alias is `invalid_ref` with the object,
received columns, and a bounded canonical alias list; all missing aliases for one
entity are aggregated into one `SemanticLoadError`, while structured `details`
retain every alias, referencing object, field, and source location. Repair changes
`column=` or adds the matching output-to-source entry to `columns=`. This check
is static: it does not connect or query. General expression decorators keep their
existing runtime materialization boundary and do not gain inferred column typing.

ClickHouse inspection augments catalog columns with safe adapter-only physical
columns from active `system.parts_columns`. Type conflicts across active parts or
unparseable types are omitted with inspection warnings. A projection never
asserts an expected physical type; execution checks the observed type only when
the dependency closure consumes that column.

### Runtime / materialization-time

Materialization executes user functions and composes Ibis objects. Failures come
from backend factories, missing Ibis tables/columns, user-function exceptions, or
incompatible expressions. A registered-but-failing object raises a runtime error —
never a "metric not found" error, which is reserved for genuinely absent objects.
Filtered metrics apply the declared dimension expression rather than assuming its
semantic name is the physical column name. Once an Ibis schema is available,
equality and membership literals are checked for type compatibility before query
submission. A legal declaration whose literal cannot be compared with the
runtime physical dtype raises `filter_value_runtime_incompatible`, with
`query_executed=False` and `declaration_preserved=True`. This is distinct from
assembly-time `invalid_filter`: Marivo preserves the authored business literal,
does not infer a code/label mapping from physical types or sample values, and
routes the required decision to the current business authority. Project loading
and `semantic_static` readiness may continue without the unavailable runtime
evidence.

Analysis trusts the declared Entity source grain: `K` for a non-versioned keyed
Entity, `(K, snapshot_coordinate)` for a keyed snapshot, and `(K, valid_from)`
for keyed validity. It does not preflight source identity, snapshot availability,
interval well-formedness or overlap, or selected-version identity. Time-axis
source cells receive no automatic parse, gap/fold or engine/runtime timezone
rule scan. Declared filters and conversions still run; malformed cells may
produce NULL, a backend error, or incorrect results according to the backend.
An absent snapshot follows the consuming operation's empty-result contract.
Artifact row-key and retained-state integrity remain separately enforced.

Retained aggregation state must reconcile with its primary values, obey the
same selection and coordinate binding, and preserve the exact empty/null and
component equations. Mean, weighted mean, and ratio use their named state;
distinct/distribution folds require their admitted state; spatial and temporal
folds cannot exchange order without a valid proof. A corrupt or insufficient
Artifact fails dependency validation without rereading the semantic source.

### Historical SQL verification

The public parity executor has been removed. `ms.load()` does not execute
historical SQL or infer verification status from it. If historical SQL explains
a metric, put that explanation in `ai_context`; compare the current Ibis metric
against an independent business source and report that evidence separately.
`PreviewResult.show()` and `render()` use `n=None, max_output_bytes=8192`:
no default row cap, a UTF-8 budget including the printed newline, and explicit
omission counts. `n=0` displays metadata and columns; `max_output_bytes=None`
removes only the display byte limit. Source preview limits, coverage, sampling
and warnings remain separate. `PreviewBatchResult` is still a summary, not a
flattened data result. See the
[business data display contract](../agent-friendly-public-surface.md#business-data-display).

`md.raw_sql` is terminal and cannot supply a Semantic or Analysis input.

### Static policy-time

Data-free policy checks prohibit
`backend.sql(...)` / raw-SQL escape hatches / dialect-specific SQL in metric
bodies (vendor differences belong in datasource compilation, not in a body).
The SQL-escape-hatch check scans the materialized Ibis expression tree;
decorator-time only rejects obvious method names to avoid false positives on
ordinary column access.

Bounded uniqueness observations belong to explicit source health, not static
policy or automatic Analysis execution. They cannot certify full-source
identity, version integrity, or completeness.

## Error model

Errors are structured and teach: every typed error states what was expected, what
was received, and the concrete next step, with a stable `kind`, the `refs`
involved, a `source location`, and a human-readable hint. New exceptions subclass
`SemanticError`, carry structured fields, and render through the shared template
style. Structured `semantic_refs` contain canonical path strings; a target
Dimension contract supplied while constructing an error is recorded by its
`ref.path`. The mapping from error kind to agent action is mechanical:

| Error kind | Agent action |
|---|---|
| `duplicate_name` | Remove the duplicate declaration or change `name=`, then reload. |
| `missing_domain` | Add `ms.domain(...)` in `<root>/<domain>/_domain.py`, or pass an explicit `domain=`. |
| `missing_entity_ref` | Ensure the entity is declared; for forward references use a decorated ref or `ms.ref.<kind>(path)`. |
| `invalid_decomposition` | Check that `ms.ratio(...)` / `ms.linear(...)` components point to registered metrics. |
| `invalid_component_body` | Remove component calls from the metric body; use `ms.ratio`/`ms.linear`. |
| `outside_loader_context` | Move the declaration fragment into `models/semantic/<domain>/_domain.py` or its domain module and follow `marivo.help("semantic.authoring")`. |
| `invalid_project` | Fix the explicit `marivo.toml` configuration or pass the exact workspace root shown by `marivo.help("semantic.authoring")`; do not pass `models/semantic/` as `workspace_dir`. |
| `domain_file_missing` | Add `models/semantic/<domain>/_domain.py` and declare the matching domain there. |
| `domain_file_mismatch` | Make the directory name and `ms.domain(name=...)` identity agree, then reload. |
| organization errors | Restore the minimal datasource/domain layout reported by structured repair, then reload from the same project root. |
| `sql_escape_hatch` | Use typed datasource inspection or a governed Ibis reference for Semantic authoring; raw SQL in Semantic expression bodies remains rejected. `md.raw_sql` is available only for terminal custom analysis outside Semantic and cannot repair this declaration or feed typed Analysis. |

Loader/layout errors obtain these repair targets, path templates, and fragments
from the same semantic registry used by focused help. An error does not embed a
second handwritten workflow.

## Readiness and richness

Two checks sit at the end of the write loop:

- **`catalog.readiness(refs=[entry_or_ref_or_runtime_expr])`** runs pure
  in-memory checks over the compiled definition graph's dependency closure of exact current
  entries, refs, and closed runtime metric expressions selected for
  certification. Entries normalize to refs before duplicate detection or
  dependency lowering. Runtime expressions lower through
  the same bounded graph contract as analysis, including weighted-mean,
  datasource-domain, depth, and occurrence checks. It is
  the explicit certification and diagnostic at the end of an authoring change,
  never writes stdout, and never queries. Analysis APIs do not invoke it
  automatically.
  Every `ReadinessReport` exposes `scope="semantic_static"` in its bounded
  rendering and dictionary form. This certifies the selected semantic
  dependency closures only; it does not promise that a particular analysis
  operation is executable. Operation-specific temporal selection, fold,
  grain, and artifact-shape checks remain owned by the consuming
  analysis call.
  A versioned Entity therefore does not need an ambient "current" anchor to pass
  intrinsic static checks, and readiness cannot select its latest rows. Likewise,
  a coherent multi-root Metric graph is distinct from proof that a particular
  Population binding or coordinate fold is admissible. Readiness validates shared
  semantic facts without assigning analysis choices or retained-state authority.
  Readiness is independent of discovery snapshots and ordinary preview history.
  It evaluates only the current semantic project, the requested dependency
  closure, and dedicated certified temporal artifacts. Ordinary preview cannot
  change its status or ready inputs. For an explicit scope, load warnings about
  unrelated definitions are excluded; warnings on a requested root or any
  transitive dependency remain visible.
  A native `ms.datetime()` or `ms.timestamp()` axis without `timezone=` is a
  blocker (`undeclared_naive_time_axis`): runtime would otherwise fall back to
  the datasource read timezone while report windows use the analysis-session
  timezone. Its structured repair requires declaring the source timezone; the
  zero-query gate does not guess or probe either runtime timezone. A time
  dimension with no `parse` is not inferred from historical preview types.
  Current preview reports a `time_parse_risk` warning when it directly observes
  a native timezone-naive timestamp.
- **`ms.richness(demand=None)`** returns a demand-ranked `RichnessReport`. It is
  purely advisory — it never blocks and never mutates readiness — and seeds
  ranking from example questions, analysis intents, run-history refs, and the
  build purpose.

`ms.load()` is the authoritative project-level static validation event. Author a
dependency-coherent slice, load once, repair reported structural errors, and
confirm every exact authored root with `catalog.require(ref)`.
For projected tables, successful loading proves declaration coherence only.
Datasource inspection may classify bindings as declared-only when catalog
metadata cannot confirm them. Authoring may then run an explicitly scoped
preview against the current datasource; loading alone does not prove that a
declared physical column exists or is queryable, and preview does not alter
readiness.
`ms.load()`, scoped readiness, and `ms.richness()` return bounded result
objects with `.show()` / `.render()`. None executes historical SQL.

## Explicit source health

`catalog.source_health(refs)` composes the existing connection roundtrip and
authoritative source inspection seams. It returns current datasource/source
identity, affected semantic refs, schema and capability fingerprints, per-check
status and time, typed repair direction, and exact user-data/scope disclosure.
It stores no history and neither reads nor changes readiness.
The connection roundtrip uses the datasource provider's Ibis literal probe;
requested business checks read bounded rows through the same bound
`SourceSession` owner as preview. Optional metadata facts may be unavailable;
connectivity or a bounded sample cannot supply a missing fact required for
method admission.

With no `checks`, only connectivity and metadata checks run and `scope` must be
omitted. Data expectations are closed, call-time values from `ms.source_check`:
`not_null`, `allowed_values`, `unique`, `freshness`,
`relationship_matches`, and `relationship_cardinality`. They are never inferred
from samples and require a positive bounded `AuthoringScope`, or an exact
entity-ref scope mapping for a multi-entity check. Per-check states are
`current`, `failed`, `unavailable`, or `unknown`; failures identify current
downstream affected refs without modifying the project.

### Analysis-ready inputs

`ReadinessReport.analysis_ready_inputs` is the ordered result-owned list of
directly requested refs and runtime expressions whose full dependency closures
contain no blocker. Governed leaf refs remain visible in `input_summary.refs` but are
never substituted for the originally requested runtime expression. Warnings
remain visible on the same report and require an explicit proceed-or-stop
decision by the caller.

The report and every issue carry the same `catalog_definition_fingerprint`.

The optional ontology is loaded separately with `mo.load(semantic=catalog)`.
Its `definition_fingerprint` and `semantic_catalog_fingerprint` jointly identify
the contextual association to the exact loaded definitions. Ontology edges
do not alter readiness or grant causality, computation, or Artifact authority;
binding them to a future Artifact belongs to the Runtime handoff. R4 must first
establish the Artifact identity, validated receipt, and exact semantic dependency
lineage. R10 may then expose an optional, read-only association only after
checking the ontology context and endpoint roles against those exact identities;
a mismatch cannot be repaired by a current catalog guess or grant admission.

The report does not create a second transfer object or validation token. After
readiness succeeds, an agent passes the listed canonical refs or runtime
expressions to the ordinary analysis APIs. Independently navigated current
catalog entries are also valid at the qualifying analysis boundaries; both
forms normalize to refs at the actual operation boundary. Readiness remains
explicit and is not invoked automatically by `session.observe(...)` or another
analysis operator.

## Relationship to analysis

The boundary is firm: `semantic` owns *business identity, historical
representation, intrinsic Metric equations, and their normalized materialization
requirements*; `analysis` owns *Population membership, observation windows,
coordinates, selection, typed operators, and explicit materialization with
persistence and lineage*. At qualifying
catalog-bound runtime inputs, analysis accepts an exact current `CatalogEntry`
or its exact `Ref`, then immediately normalizes to the ref. It never re-defines a
caliber, guesses an entity or time dimension, persists an entry, or bypasses the
registry to read a table directly. When an analysis needs a new business object,
extend `semantic` first, then let `analysis` consume it — business definitions do
not hide inside one-off analysis scripts.

Semantic readiness is the explicit certification boundary:
`analysis_ready_inputs` carries the requested canonical refs or runtime
expressions whose dependency closures passed. A missing required semantic object activates
`marivo-semantic` through the structured `semantic_authoring` repair and returns
to the same semantic entry, requiring matching scoped readiness before
resuming.

The coordinated cutover must verify these seams without duplicating owners:

1. An identity-keyed snapshot declaration loads, repeated `K` across snapshots
   is valid, duplicate `(K, snapshot)` rows are not preflighted, and an absent
   requested snapshot uses the consuming operation's empty-result semantics
   without another partition being substituted.
2. Static readiness succeeds without querying or inventing a temporal anchor;
   action admission separately rejects missing temporal context, incomplete
   subject identity, and unsupported coordinate folds. Overlapping source
   validity intervals are not preflighted.
3. One explicit Population supports safely mapped different Metric roots while
   invalid paths fail at the responsible occurrence. Event and StateModel
   subjects retain exact Entity identity and never derive it from occurrence keys.
4. Direct computation and an admitted retained-state fold agree for selected
   mean/weighted/ratio contributions, including empty/all-null/zero inputs;
   incompatible semi-additive folds and overlapping additive buckets fail.
5. Native Help, catalog projections, Dataset contracts, structured repairs,
   retained-state validation, and cold recovery derive the same facts. Until
   those implementation and public-disclosure checks pass, this amendment must
   not be presented as live lazy behavior.

## R5.1 frozen resolution handoff

Status: target frozen; new R5 runtime variants remain unverified. Resolution
consumes the [Semantic handoff](semantic-object-model.md#r51-frozen-semantic-handoff)
and [Analysis input variants](../analysis/python-analysis-design.md#r51-frozen-public-target).
It does not add a second catalog, readiness object, or named statistical-weight
role. `ms.statistical_weight` was withdrawn; neither it nor the dependent
`mv.statistical_weight` is reactivated by this handoff.

Resolve the closed observation input (Metric Ref or RuntimeMetricExpr) into the existing canonical graph with effective dependency
fingerprints. Runtime expressions do not register persistent business definitions.
The five factories share the same resolver as governed Metrics; they cannot
accept CatalogEntry, SQL, callback or name-string substitutes for Ref. Preserve the definition-owned aggregate kind and q in algorithm selection;
observation cannot override exactness.
Every recursive occurrence retains its own root, filters, role/path, version,
time requirement, contribution unit and component policy. Same-table or same-Ref
occurrences with different bindings remain independent. Graph sharing does not
supply a common database snapshot or whole-history coverage.

Read resolution preserves Measure/Dimension/TimeDimension and the resolved
boolean kind; only single-valued, type-compatible mappings can become scalar
reads. Member identity is all ordered primary-key fields, distinct from snapshot
or validity coordinates. Version/field/path resolution produces static premises,
not proof that the requested snapshot exists or a source mapping is complete.
A unique direct/definition-bound route may be omitted; additional, duplicate,
missing or incompatible explicit roots reject. A same-root role conflict names
its exact component occurrences rather than choosing a path.

Readiness continues to return directly requested refs/expressions whose static
dependency closure has no blocker. It cannot mark duplicate-key, classification,
coverage, nonfinite, overflow, time-rule or state-completeness checks completed.
The graph registers each data-dependent obligation against its actual consumed
domain and before-consume/before-publish deadline. Source rows used by these
checks pass through the same admitted Ibis source boundary. No whole-source
uniqueness scan is inserted merely because an Entity has a declared key.

Every occurrence resolved from one input is separately ready or separately
blocked: a blocked occurrence names its own component and never silently drops or
substitutes for a ready sibling, and an observation whose occurrences bind
different contribution roots reports each root's own obligation set. Occurrence
combinations add no dependency of their own beyond their member occurrences. An
opaque Metric contributes only its declared permissions and the state it actually
declares, never components inferred from a function body, a result column name or
numerically equal values.

Construction rejects wrong Ref kind, cross-Session identity, conflicting temporal
arguments, unavailable required declaration and mixed source/fixed dependencies
before a Run or business-data read. Schema-only R1 preflight retains its existing
boundary. Source-only new observations and verified fixed continuations do not
share fallback resolution: fixed recovery uses committed definitions and parts,
never loads current Semantic state to repair missing facts. Errors name expected
and received facts, bound occurrence/Ref/field, and a concrete repair based on the
actual catalog or retained parts. R5.2-R5.6 must align the native Help, errors and
typed surfaces in the package that activates each variant; a frozen signature
here does not make it callable.


R5.2's Analysis consumer now resolves complete member identity, native temporal
version axes and four scalar field kinds from the frozen registry and R1 schema.
The loader still performs no source identity/coverage scan. Analysis rejects a
missing required attribute anchor or non-to-one path before business I/O; runtime
checks distinguish a missing representation from a represented null value.
Native boolean physical fields resolve boolean Dimensions; integer indicators
remain categorical and do not acquire boolean semantics.


### Analysis declaration trust

Analysis consumes Entity primary-key/version grain, field owners, complete
relationship keys, structural cardinality and declared source-time interpretation
as semantic premises. It does not automatically audit these declarations while
executing an analysis. Incorrect declarations do not promise detection.
Cardinality does not imply that every selected row has a matching owner;
unknown matching remains an Analysis call premise. Explicit source audits keep
their independent `source_health` route. Physical schema, decoding and actual
numeric conversion failures remain mandatory execution boundaries.
