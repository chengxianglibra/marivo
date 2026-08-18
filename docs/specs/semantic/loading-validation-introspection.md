# Loading, Validation, and Introspection

Status: design. This document describes the runtime side of
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

A semantic project is one explicit boundary: a `models/semantic/` root with its
own registry and load lock. `ms.load(...)` executes the trusted local Python
files under that root, assembles the decorators' side effects into an in-memory
registry, and returns a `SemanticCatalog`.

```python
import marivo.semantic as ms

catalog = ms.load()  # locate the nearest models/semantic/ upward
catalog = ms.load(workspace_dir="models/semantic", domains=["sales"])  # explicit root + filter
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
- Python files are trusted local code and are not sandboxed. `find_project()`
  locates the nearest `models/semantic/` upward; an empty `models/semantic/` is a
  valid (empty) project. If the path exists but is not a directory, the loader
  fails closed.
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
`catalog.state_models`, `catalog.period_calendars`, `catalog.temporal_sets`,
and `catalog.work_schedules`. Each is a
`CatalogCollection[T]` with `.items`, `.refs`, `.get(key)`,
`.render()`, `.show()`, `len()`, and iteration. `catalog.require(ref)` is the
exact lookup entry point for IDs obtained from errors, logs, or persisted state.
`SemanticCatalog` itself follows the bounded result protocol: `repr(catalog)`
points to `catalog.show()`, whose zero-query card lists every collection, its
entry type, and its current object count.

| API | Meaning |
|---|---|
| `ms.load(workspace_dir=None)` | Load the project and return a `SemanticCatalog`. |
| `catalog.require(ms.ref.<kind>(path))` | Resolve and validate one `CatalogEntry` by exact typed ref; this global lookup remains ref-only. |
| `catalog.domains`, `catalog.metrics`, … | Typed global or scoped collections; `.get(...)` accepts a local name, full path, displayed same-kind typed key, or exact same-kind ref within that collection's scope. |
| `catalog.verify(entry_or_ref)` | Static, zero-query validation of one exact current entry or ref. |
| `catalog.preview(entry_or_ref, using=snapshot_or_mapping)` | Scoped runtime preview for one current entry or ref, bound to matching snapshot evidence. |
| `catalog.preview_many(entries_or_refs, using=snapshot_or_mapping)` | Normalize an ordered batch before execution, then persist an independent preview check for every canonical ref. |
| `catalog.readiness(refs=[entry_or_ref_or_runtime_expr])` | Zero-query readiness gate over current entries, exact refs, or closed runtime metric expressions. |
| `ms.richness(demand=None)` | Advisory demand-ranked coverage/depth report. |

`ReadinessReport.preview_required_refs` is the canonical typed input for batch
preview repair. The report keeps per-ref advisories for structured diagnostics,
groups them in bounded rendering, and exposes one batch preview transition from
`.contract()`. Entity preview evidence never satisfies child refs.

### Navigation matrix

Navigation is limited to explicit ownership or applicability relationships. Each
container object exposes typed collection properties:

| Object | Navigation properties |
|---|---|
| `Domain` | `entities`, `dimensions`, `time_dimensions`, `measures`, `metrics`, `relationships`, `events`, `state_models` |
| `Datasource` | `entities` |
| `Entity` | `dimensions`, `time_dimensions`, `measures`, `metrics`, `relationships`, `events`, `state_models` |
| `Relationship` | `from_entity`, `to_entity` |
| `Dimension` / `TimeDimension` / `Measure` / `Metric` | leaf objects — use `details()` for dependency information |

Scoped collections are the normal way to remove ambiguity:
`catalog.domains.get("sales").entities.get("orders").dimensions.get("region")`.

### Self-teaching object cards

Every container object's bounded `render()` / `show()` card advertises its live
navigation properties and counts. A domain card includes a `navigation:` section
listing each valid child collection with its count, so the agent discovers
`.entities`, `.metrics`, etc. from real state rather than memorizing a matrix.
Every entry card names its exact kind and full path, exposes `.ref`, and
advertises `details()`, `contract()`, `render()`, `show()`, and bounded
navigation. Metric cards additionally render bounded exact refs for effective
entities, candidate dimensions, candidate time dimensions, required
relationships, and component/measure lineage when present. An empty time-axis
set is explicit (`candidate_time_dimensions: none`). Omitted members include an
omitted count and a concrete full read such as `details().show()`; cards never
rank axes or recommend an operator.

`marivo.help(entry)` composes that current catalog identity with the analysis
registry's kind-level handoff. It shows only the first focused analysis target
or policy choices, their registered call shapes, and the artifact family of an
operator result. It does not infer readiness, enumerate downstream operators,
or create a second programmable navigation result. The caller inspects the
focused target for companion inputs, executes it only after readiness, and then
uses `result.contract().show()` for state-specific continuation. A bare `Ref`
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
collection = session.catalog.metrics
collection.show()                              # bounded list when identity is unknown
entry = collection.get("metric:sales.revenue")  # full path or displayed typed key
entry.show()
entry.details().show()
marivo.help(entry)                             # current details and kind handoff
frame = session.observe(
    entry,
    time_scope=mv.time_scope(start="2026-07-01", end="2026-10-01"),
    grain=mv.grain("month"),
)
```

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
entity/composition/additivity/provenance/parity/unit; relationship join keys).
Metric details also expose `effective_entities`, `candidate_dimensions`,
`candidate_time_dimensions`, and role-keyed `measure_lineage`. Derived metrics
keep their authored `entities=()` shape; effective entities and measures are
projected recursively from composition components. Candidate axes are dimensions
owned directly by those effective entities. They are static discovery facts, not
a promise that every cross-entity relationship or fanout plan is executable;
`session.observe(...)` remains the authority for plan validity.
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
business judgments that must already be settled, and ends with the generated
loaded-object postcondition:

```python
catalog = ms.load()
entry = catalog.<collection>.get("<canonical-identity>")
entry.show()
entry.contract().show()
```

The ordered catalog-member contract supplies `<collection>`, so focused help,
the live catalog, and the acquisition path cannot drift independently.

## Result contract

Every semantic result object follows the shared no-side-effect contract — the
methods **do not write stdout**; inspection is explicit and silent by default:

- `result.show()` — print a bounded result card and return `None`.
- `result.render()` — return the same bounded text without writing stdout.
- `repr(result)` — a one-line cold-start hint pointing to `.show()`.

State-bearing objects additionally return a structured continuation value from
`.contract()`. Its default representation and `show()`/`render()` output are
bounded transition summaries; callers that need machine detail read `states`,
`transitions`, or `model_dump()` explicitly.

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
catalog.verify(revenue).show()
catalog.preview(revenue, using=snapshot).show()
catalog.readiness(refs=[revenue]).show()
```

These runtime methods accept an exact entry from the current compiled catalog or
its exact ref and normalize immediately to the canonical ref. Ordered batches
are normalized completely before preview begins. No `CatalogEntry`, catalog
pointer, or object identity reaches preview evidence, readiness output,
persistence, replay, or recovery. Semantic authoring constructors and
decorators remain ref-only.

Backend resolution rules:

- The compile target defaults to the `backend_type` of the metric's datasource.
- The backend is obtained through the internal datasource connection service; the
  live backend dialect must match the declared `backend_type` or the operation
  fails closed.
- With no live backend, a dry compiler for that `backend_type` is used when
  available; otherwise a structured `compile_error` is returned rather than
  executing a query.
- Multi-datasource metrics fail closed in compile and parity (federation is a
  separate design).

Entity source provenance is source-aware. An ordinary table is `IBIS_TABLE`; a
table with typed column bindings is `TABLE_PROJECTION` and never carries a raw
SQL snippet; a retained Ibis SQL node is `SQL_VIEW`. A projected table still has
one physical source. Metric-graph physical leaves therefore record one
`physical_sources` item per entity with the entity, datasource, and the source's
single canonical `to_dict()` payload. Output aliases remain inside that source's
`columns` mapping rather than appearing as synthetic physical tables. The same
source payload participates in the semantic dependency digest, so canonical
reordering is identity-stable while rebinding, renaming, or changing a declared
type changes identity.

To inspect a metric's caliber without executing analysis, use typed details and
static verification. Use `catalog.preview(..., using=...)` for a scoped runtime
check. Parity is a separate potentially unbounded provenance SQL diagnostic.

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

### Load / assembly-time

After the loader executes project files, assembly validation checks cross-object
relationships: a missing or mismatched `_domain.py`; `ms.domain(...)` in the wrong
file or a `_domain.py` declaring multiple domains; an entity referencing an
unknown datasource; a metric referencing an unknown entity or component; a
cross-domain `ms.ref.<kind>(path)` that is missing, type-mismatched, or cyclic; an
`entities=[...]` count that disagrees with the function arity; an hour time
dimension missing its required prefix; invalid relationship endpoints, join
dimension refs, entity membership, or arity. Tier-1 metric filters must resolve
every local key to a declared dimension on the target entity; failures use
`invalid_filter` with focused `semantic.where` repair. On failure the registry
is `errored` and retains `load_errors`.

For an entity backed by `md.table(columns=...)`, assembly also proves that every
`primary_key` entry and every direct `ms.dimension_column(...)`,
`ms.time_dimension_column(...)`, or `ms.measure_column(...)` reference names a
declared stable output alias. A missing alias is `invalid_ref` with the object,
received columns, and a bounded canonical alias list; all missing aliases for one
entity are aggregated into one `SemanticLoadError`, while structured `details`
retain every alias, referencing object, field, and source location. Repair changes
`column=` or adds the matching `md.source_column(...)` binding. This check is
static: it does not connect or query. General expression decorators keep their
existing runtime materialization boundary and do not gain inferred column typing.

ClickHouse inspection augments catalog columns with safe adapter-only physical
columns from active `system.parts_columns`. A projected binding found there must
match the normalized backend type before any query; type conflicts or unparseable
types are omitted with inspection warnings, and only genuinely unverifiable
bindings retain the declared-only warning path.

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
routes the required decision to the user or business owner. Static verification
and `semantic_static` readiness may continue without the unavailable runtime
evidence.

### Parity-time

Parity compares SQL provenance against the Ibis expression. It can fail on
missing source SQL or dialect; a metric still `unverified` under a strict policy;
a missing datasource profile, unsupported backend type, or live/profile mismatch;
an inexecutable SQL or metric expression; a non-scalar side; or unequal scalars.
On a parity failure, locate the semantic difference first — do not simply widen
the tolerance.

### Static policy-time

Data-free policy checks: optional sample-uniqueness checks on entity primary keys
(non-blocking by default; unverified keys surface as warnings); a ban on
`backend.sql(...)` / raw-SQL escape hatches / dialect-specific SQL in metric
bodies (vendor differences belong in datasource compilation and parity, not in a
body). The SQL-escape-hatch check scans the materialized Ibis expression tree;
decorator-time only rejects obvious method names to avoid false positives on
ordinary column access.

## Error model

Errors are structured and teach: every typed error states what was expected, what
was received, and the concrete next step, with a stable `kind`, the `refs`
involved, a `source location`, and a human-readable hint. New exceptions subclass
`SemanticError`, carry structured fields, and render through the shared template
style. The mapping from error kind to agent action is mechanical:

| Error kind | Agent action |
|---|---|
| `duplicate_name` | Remove the duplicate declaration or change `name=`, then reload. |
| `missing_domain` | Add `ms.domain(...)` in `<root>/<domain>/_domain.py`, or pass an explicit `domain=`. |
| `missing_entity_ref` | Ensure the entity is declared; for forward references use a decorated ref or `ms.ref.<kind>(path)`. |
| `invalid_decomposition` | Check that `ms.ratio(...)` / `ms.linear(...)` components point to registered metrics. |
| `invalid_component_body` | Remove component calls from the metric body; use `ms.ratio`/`ms.linear`. |
| `outside_loader_context` | Move the declaration fragment into `models/semantic/<domain>/_domain.py` or its domain module and follow `marivo.help("semantic.authoring")`. |
| `invalid_project` | Create or select the exact `models/semantic/` project root shown by `marivo.help("semantic.authoring")`; do not guess a different root. |
| `domain_file_missing` | Add `models/semantic/<domain>/_domain.py` and declare the matching domain there. |
| `domain_file_mismatch` | Make the directory name and `ms.domain(name=...)` identity agree, then reload. |
| organization errors | Restore the minimal datasource/domain layout reported by structured repair, then reload from the same project root. |
| `unverified_provenance` | Add `provenance=ms.from_sql(...)`, or stop and confirm the business caliber. |
| `sql_escape_hatch` | Use `md.raw_sql(...)` for terminal raw SQL execution; raw SQL in semantic expression bodies is still rejected by the validator. |

Loader/layout errors obtain these repair targets, path templates, and fragments
from the same semantic registry used by focused help. An error does not embed a
second handwritten workflow.

## Readiness and richness

Two checks sit at the end of the write loop:

- **`catalog.readiness(refs=[entry_or_ref_or_runtime_expr])`** runs pure
  in-memory checks over the governed dependency closure of exact current
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
  operation is executable. Operation-specific snapshot identity, temporal
  fold, grain, and artifact-shape checks remain owned by the consuming
  analysis call.
  `catalog.preview(..., using=...)` persists scoped runtime metadata that
  readiness consumes. Missing datasource snapshots and missing or stale preview
  evidence for executable families (`static_only`, `single_snapshot`,
  `snapshot_mapping`) are visible advisories, not analysis blockers; authoring
  policy still requires repairing the applicable evidence before declaring a
  new or changed object complete. Snapshot and preview absence or age never
  block readiness or trigger implicit reacquisition. These issues use
  `severity="advisory"`, remain in the existing `ReadinessReport.warnings`
  collection, and are aggregated by evidence root with all affected refs.
  Advisory-only reports remain `ready`; only true warnings produce
  `ready_with_warnings`.
  A native `ms.datetime()` or `ms.timestamp()` axis without `timezone=` is a
  blocker (`undeclared_naive_time_axis`): runtime would otherwise fall back to
  the datasource read timezone while report windows use the analysis-session
  timezone. Its structured repair requires declaring the source timezone; the
  zero-query gate does not guess or probe either runtime timezone.
- **`ms.richness(demand=None)`** returns a demand-ranked `RichnessReport`. It is
  purely advisory — it never blocks and never mutates readiness — and seeds
  ranking from example questions, analysis intents, run-history refs, and the
  build purpose.

`catalog.verify(entry_or_ref)` completes the static per-object surface. A current
`VerifyResult` proves that one explicit check passed, but verification is
**result-local**: it is not persisted as a workflow checkpoint and is not a
runtime prerequisite for preview. The `marivo-semantic` skill enforces
verify-before-preview as a policy edge; the runtime does not consume a
`VerifyResult` in `catalog.preview(...)` or `catalog.readiness(...)`.
For projected tables, these static checks prove declaration coherence only.
Datasource inspection may classify bindings as declared-only when catalog
metadata cannot confirm them. Authoring must then obtain one explicitly bounded
sample, run the scoped preview, and let zero-query readiness consume matching
evidence; neither load nor static verification proves that a declared physical
column exists or is queryable.
`ms.parity_check(name)` is an optional potentially unbounded diagnostic and never
a readiness requirement. All three return silent result objects with `.show()` /
`.render()`.

### Analysis-ready refs

`ReadinessReport.analysis_ready_inputs` is the ordered result-owned list of
directly requested refs and runtime expressions whose full dependency closures
contain no blocker. `analysis_ready_refs` remains its refs-only compatibility
projection. Governed leaf refs remain visible in `input_summary.refs` but are
never substituted for the originally requested runtime expression. Warnings
remain visible on the same report and require an explicit proceed-or-stop
decision by the caller.

The report and every issue carry the same `catalog_definition_fingerprint`.
Persisted preview evidence matches only when its v1 checked-ref payload,
catalog fingerprint, semantic dependency digest, entity-snapshot bindings, and
backend all match the active compiled catalog.

The report does not create a second transfer object or validation token. After
readiness succeeds, an agent passes the listed canonical refs or runtime
expressions to the ordinary analysis APIs. Independently navigated current
catalog entries are also valid at the qualifying analysis boundaries; both
forms normalize to refs at the actual operation boundary. Readiness remains
explicit and is not invoked automatically by `session.observe(...)` or another
analysis operator.

## Relationship to analysis

The boundary is firm: `semantic` owns *what an object is, what its caliber is, and
how it materializes*; `analysis` owns *observe / compare / attribute / correlate
over those objects, with session persistence and lineage*. At qualifying
catalog-bound runtime inputs, analysis accepts an exact current `CatalogEntry`
or its exact `Ref`, then immediately normalizes to the ref. It never re-defines a
caliber, guesses an entity or time dimension, persists an entry, or bypasses the
registry to read a table directly. When an analysis needs a new business object,
extend `semantic` first, then let `analysis` consume it — business definitions do
not hide inside one-off analysis scripts.

Semantic readiness is the explicit certification boundary:
`analysis_ready_inputs` carries the requested canonical refs or runtime
expressions whose dependency closures passed; `analysis_ready_refs` remains the
refs-only projection. A missing required semantic object activates
`marivo-semantic` through the structured `semantic_authoring` repair and returns
to the same semantic entry, requiring matching scoped readiness before
resuming.
