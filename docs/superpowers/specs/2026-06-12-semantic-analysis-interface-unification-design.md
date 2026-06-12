# Semantic/Analysis Interface Unification Design

Date: 2026-06-12
Status: Approved design, pre-implementation
Related: `2026-06-09-semantic-catalog-public-api-design.md`,
`docs/specs/semantic/python-semantic-layer.md`,
`docs/specs/analysis/python-analysis-operator-design.md`

## Problem

The semantic and analysis modules grew three parallel read surfaces and two
ibis materialization implementations. The catalog's promised handoff to
analysis is broken in practice.

1. **Three read layers for the same objects.**
   - `SemanticCatalog.list()/get()` (`marivo/semantic/catalog.py`) is the
     documented agent surface.
   - `SemanticProject.list_domains/list_datasources/list_entities/
     list_dimensions/list_time_dimensions/list_metrics/list_relationships/
     get_entity/get_metric` (`marivo/semantic/reader.py`) are all marked
     "Internal helper — agents should use catalog instead", yet analysis
     consumes exactly this layer (`marivo/analysis/intents/observe_planner.py`).
   - Analysis also reaches through private state: `project._registry`,
     `project._sidecar` (`observe.py`, `observe_planner.py`).
   The `*Summary` DTO family returned by `SemanticProject.list_*` is a
   parallel schema to `SemanticObject` + `*Details`.

2. **The catalog → analysis ref handoff does not work.**
   `SemanticRef` (`catalog.py`) documents `session.observe(metric=r)`, but
   `observe()` hard-requires `isinstance(metric, MetricRef)` where
   `MetricRef` is the authoring-time ref from `marivo/semantic/ir.py`,
   re-exported by `marivo/analysis/refs.py` as `mv.MetricRef`. An agent that
   browses the catalog must throw away the `SemanticObject` it just fetched
   and re-wrap the id string in a second ref type.

3. **Two ibis materialization implementations.**
   `marivo/semantic/materializer.py` (`Materializer`) is the complete
   implementation: entity → `ibis.Table`, dimension/metric → `ir.Value`,
   derived-metric recursion, cross-datasource checks, typed error wrapping,
   provenance detection. Analysis does not use it. Instead
   `marivo/analysis/intents/observe.py` carries `_EntityIRAdapter`,
   `_DimensionIRAdapter`, `_TimeFieldMetaAdapter`, `_build_dataset_adapter`
   (whose `_source_fn` duplicates `Materializer._materialize_dataset_source`
   line for line), plus `_field_fn` / `_execute_base` / `_execute_sampled_base`
   / `_execute_derived` that pull callables straight from `sp._sidecar`.
   `observe_planner.py` does the same for its expression-match strategy.
   Backend construction is also duplicated (`semantic`'s
   `_session_backend_factory` vs `analysis/session/_runtime.py`'s
   `_compile_backend_factory`).

## Goals

- `SemanticCatalog` is the single read surface for loaded semantic objects.
  Analysis (and agents) never touch `SemanticProject`, registry IRs, or the
  sidecar.
- Objects and refs returned by the catalog pass directly into analysis
  operators with no re-wrapping.
- The semantic-object → ibis-expression conversion is defined once, in
  `marivo.semantic`. Analysis composes expressions; it never converts.

## Non-Goals

- Backward compatibility or data migration. This is a breaking change.
- Changing analysis operator semantics (planning rules, frame shapes,
  persistence, evidence) — only how they obtain semantic facts and ibis
  expressions.
- Changing the authoring surface (`ms.entity`, `@ms.dimension`,
  `@ms.metric`, ...). Authoring refs (`EntityRef`, `DimensionRef`,
  `TimeDimensionRef`, `MetricRef` in `ir.py`) remain, but become
  authoring-internal: they no longer appear in any analysis signature.
- Moving time-axis normalization (strptime parsing, timezone conversion,
  bucketing) into semantic. That is listed under Future Work.

## Core Decision

`ms.load()` already returns a `SemanticCatalog`. We finish that move:

- `SemanticProject` becomes an internal loader. Its public read surface
  (`list_*`, `get_entity`, `get_metric`, `materialize_*`, `preview_*`) is
  deleted; the catalog absorbs the lifecycle and read responsibilities.
- The catalog grows an internal expression resolver backed by
  `Materializer` (canonical `table`/`dimension`/`metric` forms plus pure
  `*_on` application forms), with connections sourced from the datasource
  module's `DatasourceConnectionService` — no backend-factory seam.
- Analysis sessions hold a `SemanticCatalog` (public as `session.catalog`).
  Operators accept `SemanticObject | SemanticRef`. `mv.MetricRef` and
  `mv.DimensionRef` are deleted.

## Semantic Surface Changes

### SemanticCatalog absorbs lifecycle and read access

`SemanticCatalog` gains:

```python
catalog.load() -> None              # (re-)load from disk; replaces project.load() retry loops
catalog.semantic_root -> Path       # .marivo/semantic/
catalog.workspace_dir -> Path       # project root
```

`catalog.load()` raises `SemanticLoadFailed` on failure — same contract
as `ms.load()` — and prints load warnings (bounded) on success. There are
no `load_errors()`/`load_warnings()` accessor methods: errors reach the
agent as the raised typed error at the point of failure, not as state to
poll afterwards. Readiness state (`_is_ready`) is internal; a failed
`load()` leaves the catalog not-ready and any subsequent semantic access
raises `SemanticLoadFailed` with the stored errors.

Public `ms.load()` is unchanged: it still raises `SemanticLoadFailed` on
failure and never returns a partial catalog (the existing
`test_ms_load_failure_raises_semantic_load_error` contract stands). A
catalog obtained from `ms.load()` is always ready at creation. Not-ready
catalogs exist only on the internal session-construction path (see
analysis changes).

Deleted from `SemanticProject` (the class remains as the internal loader
used by `ms.load()`, `ms.prepare_*`, parity, and readiness internals):

- `list_domains`, `list_datasources`, `list_entities`, `list_dimensions`,
  `list_time_dimensions`, `list_metrics`, `list_relationships`
- `get_entity`, `get_metric`
- `materialize_dataset`, `materialize_field`, `materialize_metric`
  (replaced by the resolver; `parity.py` switches to the resolver)
- `preview_dataset`, `preview_field`, `preview_metric` (move to
  `catalog.preview(ref, *, limit=..., include_types=..., context_columns=None)`,
  dispatching on the ref's kind). Kind-specific behavior is preserved:
  `context_columns` selects parent-table context for dimension /
  time-dimension refs and raises a typed error for other kinds; metric
  previews keep the pre-aggregate sample strategy
  (`METRIC_PREVIEW_SAMPLE_SIZE`); entity previews keep the bounded-limit
  policy.

The `DomainSummary`/`EntitySummary`/`DimensionSummary`/`MetricSummary`/
`RelationshipSummary` DTOs and `DiscoveryResult` are deleted with them.
Internal semantic callers (`parity.py`, `prepare.py`, readiness) may keep
using registry IRs directly — the IR/sidecar boundary is private to
`marivo.semantic`, not to its submodules.

### Expression surface (single definition of "to ibis")

One internal resolver, implemented by `Materializer` (which stays in
`materializer.py` as the engine). It is **not** agent-facing (see "Public
vs internal surface" below): analysis obtains it via an internal catalog
constructor.

```python
resolver = catalog._resolver(connections=...)  # internal; see connection sourcing

# Root materialization (opens connections on demand):
resolver.table(entity_ref) -> ibis.Table   # entity source -> ibis.Table
resolver.dimension(ref) -> ir.Value        # over the entity's own table
resolver.metric(ref) -> ir.Value           # base + derived (recursive)

# Pure application to caller-supplied tables (never opens a connection;
# works with joined, filtered, or unbound tables):
resolver.dimension_on(ref, table: ibis.Table) -> ir.Value
resolver.metric_on(ref, *tables: ibis.Table) -> ir.Value
    # Base metrics only, tables positional in the metric's declared
    # entities order. Derived metrics are rejected: their composition is
    # decomposition arithmetic the caller drives component-by-component.
```

Connection sourcing: the resolver never builds backends. It maps
entity → datasource ref →
`DatasourceConnectionService.session_backend(name)`
(`marivo/datasource/runtime.py`), so connection construction, caching,
secrets, and disconnect stay entirely inside the datasource module. The
`connections` argument is the service instance whose lifecycle the caller
owns (the analysis session passes its own; semantic-internal callers like
parity and `catalog.preview` use a catalog-owned default). There is no
`backend_factory` seam.

Resolver lifetime is one per intent execution (matching today's per-call
`Materializer`/adapter lifetime and the session's query-capture window);
connection reuse across executions comes from the shared
`DatasourceConnectionService`, not from resolver caches. The existing
`Materializer` validation — sidecar callable presence, "returned X
instead of an ibis expression" diagnostics, cross-datasource check,
derived recursion — becomes the only copy.

Cache rule: the ref-keyed expression caches apply **only** to the
canonical forms (`resolver.dimension(ref)` / `resolver.metric(ref)`,
which always evaluate over the entity's own materialized tables). The
`*_on` forms are pure, uncached applications — the same ref is legitimately
applied to different joined/filtered/unbound tables, so caching by
semantic id alone would return expressions bound to the wrong table.

Accepted ref inputs for all expression APIs: `SemanticRef`,
`SemanticObject`, or full ref string (internal callers); kind is validated
against the API (e.g. `dimension_on` rejects metric refs) with typed errors.

### Details gaps (planner requirements)

The planner must work from `SemanticObject.details()` instead of IRs.
Audit of `observe_planner.py` / `observe.py` against the current details
classes shows three gaps to fill:

- `MetricDetails.components: tuple[tuple[str, SemanticRef], ...]` —
  role-keyed (`numerator`/`denominator`/`weight`) component pairs. A tuple
  of pairs, not a `Mapping`: details must stay hashable because
  `SemanticObject` is accepted as a `where` dict key. `component_metrics`
  (unordered tuple) stays for browsing.
- `TimeDimensionDetails.sample_interval: SampleInterval | None` — needed by
  the sampled/folded execution path.
- `catalog.list(domain_ref, kind="relationship")` — relationships are
  currently only listable under a dataset parent; the planner needs the
  domain-wide view it gets today from `project.list_relationships()`.

Planner global scans (`_all_fields(project)`) are rewritten as targeted
`catalog.get(ref)` lookups or per-parent `catalog.list(entity_ref, kind=...)`
calls; no "list everything in the project" API is added.

### Shared value types (IR/details dedup)

The catalog layer carries field-for-field copies of IR value types:
`AiContextView` ≡ `AiContextIR`, `SnapshotVersioning` ≡
`SnapshotVersioningIR`, `ValidityVersioning` ≡ `ValidityVersioningIR`,
`DatasetSource` mirrors `TableSourceIR`/`FileSourceIR`. These are pure
data with no layering reason to exist twice. Each pair collapses to a
single public frozen type shared by IR and details; the catalog-side
copies and their conversion code are deleted.

This is deliberately the limit of merging the two layers. `SemanticObject`
/ `*Details` and the IRs stay separate: IRs mirror the authoring surface
and change with it, while the object layer is the stable consumption
contract; details carry registry-computed graph fields
(`parents`/`children`/`dependents`/`parity_status`) that cannot live on
stored IRs without eager computation or registry back-references; and the
kind shapes differ (`DimensionIR` splits into `dimension` /
`time_dimension` objects).

### SemanticRef contract fixed

The `SemanticRef` docstring claim "passable directly to analysis APIs"
becomes true (see analysis changes below). Its shape is unchanged.

### Public vs internal surface

Only what agents need is public; everything that exists solely for the
analysis module is an internal interface — typed and tested as the
semantic↔analysis contract, but not exported via `ms.*` and exempt from
the public-API docstring/`describe` requirements.

- **Public (agent-facing):** `ms.load()`, `catalog.list()/get()/
  readiness()/preview()/load()`, `SemanticObject`, `SemanticRef`,
  `*Details`, `SemanticObjectList`.
- **Internal (analysis-facing):** `catalog._resolver(...)` and the
  resolver's five primitives, the `DatasourceConnectionService` handoff,
  readiness state queries, and the registry/IR/sidecar layer below them.

## Analysis Surface Changes

### Session holds the catalog

- `Session._semantic_project` → `Session._catalog`, public read-only
  property `session.catalog -> SemanticCatalog`.
- `_runtime._build_semantic_project` → `_build_semantic_catalog`. It
  constructs the internal `SemanticProject`, loads it, and wraps it in a
  `SemanticCatalog` even when not ready (operators raise
  `SemanticLoadFailed` with the stored errors on first semantic access,
  which preserves today's behavior for read-only sessions).
- Intents replace `session._semantic_project.semantic_root` with
  `session.catalog.semantic_root`; the `if not sp.is_ready(): sp.load()`
  retry becomes an internal readiness check + `catalog.load()`.
- Connection management moves to the datasource module: the session owns
  one `DatasourceConnectionService` (closed in `session.close()`) and
  passes it to `catalog._resolver(connections=...)` once per intent
  execution. `BackendCache` is deleted — its connection caching is the
  service's job; its analysis-side bookkeeping (datasource validation
  marks, the job-scoped query-capture buffer filled by the executor)
  moves to a small analysis-owned struct on the session. The
  `backends={...}` / `backend_factory=...` session kwargs remain as the
  test-override path, implemented as an override hook on the service —
  analysis forwards them and never builds backends itself.

### Operator input contract

All operators that take semantic identifiers accept
`SemanticObject | SemanticRef`:

```python
session.observe(
    catalog.get("sales.revenue"),                       # SemanticObject
    dimensions=[catalog.get("sales.orders.country").ref],  # SemanticRef
    where={catalog.get("sales.orders.country").ref: "US"},
    time_dimension=...,
)
```

- Normalization happens once at the operator boundary: extract the ref
  string and kind, validate kind (`metric` where a metric is expected,
  `dimension`/`time_dimension` where a dimension is expected), raise
  `SemanticKindMismatchError` with the actual kind otherwise.
- Bare strings remain rejected (unchanged design intent: kind-carrying
  inputs prevent silent misuse).
- `where` keys: both types accepted; normalized to ref strings immediately.
- The contract applies to **every** public signature currently typed
  `MetricRef` / `DimensionRef`, not just observe. Known consumers to
  migrate in stage 2 (verified inventory; stage 2 ends with a repo-wide
  sweep confirming no `ir.MetricRef`/`ir.DimensionRef` remains in any
  analysis signature):
  - `Session.observe` (metric, dimensions, where, time_dimension)
  - `Session.decompose` (`axis`)
  - `SessionDiscoverNamespace` (`driver_axes` `search_space`, and peers)
  - `SessionTransformNamespace.slice` (`where` keys) and the transform
    intent validators (`_require_dimension_refs` in
    `marivo/analysis/intents/transform.py`)
  - `policies.PromotionSemanticAnchors` (`metric`, `subject`): this is a
    **persisted** pydantic model, so it does not adopt the object/ref
    union — its fields become plain semantic-id strings (`str`),
    validated at construction. Existing persisted records are not
    migrated (breaking change accepted).
- Deleted: `mv.MetricRef`, `mv.DimensionRef` (and their re-export in
  `marivo/analysis/refs.py`; `CalendarRef`/`ArtifactRef` stay).
- `marivo/analysis/__init__.py` re-exports `SemanticRef` (and
  `SemanticObject` for typing) so analysis scripts can annotate without
  importing `marivo.semantic`.

### Adapter layer deleted

Removed entirely, replaced by catalog/resolver primitives:

- `observe.py`: `_TimeFieldMetaAdapter`, `_DimensionIRAdapter`,
  `_EntityIRAdapter`, `_build_dataset_adapter`, `_field_fn`, and all
  `sp._sidecar` access in `_execute_base`, `_execute_sampled_base`,
  `_execute_folded_component`, `_execute_derived`, `observe()`.
- `observe_planner.py`: `project._sidecar` access in
  `_effective_key_semantic_ids` (strategy 2 becomes
  `resolver.dimension_on(field_ref, dummy_unbound_table)` in a try/except —
  a `*_on` call never opens a connection; the pk-columns-as-int64
  dummy-schema heuristic stays in the planner), and all
  `project.list_* / get_*` calls move to catalog/details.
- `marivo/analysis/executor/runner.py` (the execution helpers the adapters
  were built to feed) is rewritten against `resolver.table()` +
  `resolver.dimension_on()` + `resolver.metric_on()`. Time-axis metadata
  comes from `TimeDimensionDetails` instead of `_TimeFieldMetaAdapter`.
- `publish/replay_check.py` and `escape_hatch.py` switch from
  `project.list_metrics()` / `_semantic_project` to the catalog.

## Error Handling

- **Ref resolution at the operator boundary** stays analysis-owned:
  unknown metric → `MetricNotFoundError`, wrong input kind →
  `SemanticKindMismatchError`, planning failures → `ObservePlanningError`.
  Available-id suggestions are built from `catalog.list(...)`.
- **Expression/materialization failures pass through** as semantic errors
  (`SemanticRuntimeError` with `MATERIALIZE_FAILED` etc.). Analysis does not
  re-wrap them; there is no error-mapping table. Both hierarchies render
  through the shared template style, so agent-facing output stays uniform.

## Testing

- New semantic tests: `catalog.load()` lifecycle (success with warning
  printing, failure raising `SemanticLoadFailed`, not-ready access
  raising), resolver `dimension_on`/`metric_on` (success, kind mismatch,
  non-expression return, unbound-table use, no connection opened),
  resolver canonical-form parity with current `Materializer` tests,
  connection sourcing via `DatasourceConnectionService` (including the
  test-override hook), new details fields,
  `list(domain, kind="relationship")`.
- Projection completeness test: for each IR class, every field must either
  map to a field on the corresponding `*Details` class or appear in an
  explicit internal-fields allowlist. This pins the IR→details projection
  so gaps like `MetricDetails.components` and
  `TimeDimensionDetails.sample_interval` cannot silently reappear.
- Analysis tests updated to the new operator input contract; the existing
  observe/compare/decompose integration suites are the regression net —
  they must pass unchanged in behavior (same frames, same plans) with the
  new plumbing.
- Existing tests asserting `mv.MetricRef` / adapter internals are updated to
  the current contract, not preserved as legacy shapes.

## Rollout (single branch, three reviewable stages)

1. **Semantic first (no analysis changes):** `catalog.load()` lifecycle,
   the internal resolver (canonical + `*_on` forms, connection sourcing
   via `DatasourceConnectionService` including the override hook), details
   gap fills, relationship listing, `catalog.preview`, shared value types
   (IR/details dedup) plus the projection completeness test.
   `SemanticProject` surface not yet deleted, so analysis keeps working.
2. **Analysis re-plumb:** session holds catalog and a
   `DatasourceConnectionService` (`BackendCache` deleted, capture and
   validation bookkeeping rehomed); operator input contract applied to
   the full consumer inventory (observe, decompose, discover and
   transform namespaces, transform validators,
   `PromotionSemanticAnchors`); observe/planner/runner rewritten on the
   new primitives; adapter layer and `mv.MetricRef`/`mv.DimensionRef`
   deleted; ends with a repo-wide sweep confirming no `ir.MetricRef` /
   `ir.DimensionRef` remains in analysis signatures.
3. **Deletions and docs:** remove `SemanticProject` public read surface and
   the `*Summary`/`DiscoveryResult` DTOs; update
   `docs/specs/semantic/python-semantic-layer.md`,
   `docs/specs/analysis/python-analysis-operator-design.md`,
   `docs/specs/analysis/python-track-evidence-surface.md`, and the
   `marivo-skills/marivo-semantic/` + `marivo-skills/marivo-analysis/`
   examples to the new handoff.

Each stage ends green (`make test`, `make typecheck`, `make lint`).

## Future Work

- `resolver.time_axis(ref, table)`: move strptime/timezone/prefix time-axis
  normalization from the analysis runner into semantic, next to the
  metadata that defines it.
- Fold `ms.prepare_*` / parity / readiness internals onto the catalog so
  `SemanticProject` can be deleted outright.
- Full IR/details merge: if graph navigation later moves off details onto
  catalog methods (`catalog.parents(ref)` etc., computed on demand),
  details degenerate to stored data near-1:1 with IRs and the registry
  could store details directly, deleting the IR layer. Evaluated and
  deferred — not bundled with this refactor.
