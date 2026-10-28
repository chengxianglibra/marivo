# Semantic Loading, Validation and Introspection

Loading establishes a coherent semantic project. Readiness, source health,
ordinary preview and temporal certification answer different questions; no
single success flag covers all of them.

## Project selection and loading

```python
import marivo.semantic as ms

catalog = ms.load(workspace_dir=project_root)
orders_entry = catalog.get("entity:sales.orders")
assert orders_entry.kind == "entity"
orders = orders_entry.value
revenue_entry = catalog.get("metric:sales.revenue")
assert revenue_entry.kind == "metric"
revenue = revenue_entry.value
catalog.get("metric:sales.revenue").show()
```

workspace_dir is an exact workspace root. If omitted, root resolution uses
MARIVO_PROJECT_ROOT, the nearest ancestor manifest, then cwd. Local models and
configured external model roots form one load scope. Duplicate identities fail
with both declaration locations; later roots do not override earlier ones.
`domains=None` loads all domain directories. An explicit domain name or sequence
selects only those directories, without automatic dependency expansion. Include
every domain required by the selected declarations. Filtered Relationship
references may warn; unresolved required Metric dependencies fail loading.

The loader inspects restricted source, captures declarations, resolves complete
references and validates organization, kind, dependency cycles and expression
contracts. It does not connect, inspect source schemas, prove key uniqueness or
run business queries. Entity/Source and field Ibis bodies remain restricted;
Measure/Metric declaration functions are captured without invocation.

Compilation owns an incremental definition graph and resolution caches for one
load. Physical definitions are assembled once; validated declarations append
their facts and dependencies in publication order. Physical resolution can be
reused within that load, while derived identities are refreshed as declarations
are published. Failed loads discard this private state. Final publication copies
and freezes the completed graph; caches never carry definitions across loads.

Ordinary imported modules are not guaranteed to hot-reload. Restart Python after
model edits/deletions or package changes when import state could be stale.

An existing Catalog retains its loaded scope. Changing cwd, environment variables
or a same-path file does not mutate that Catalog or an already closed expression.
A new load establishes a new scope; see [definition identity](semantic-object-model.md#context-and-immutable-definitions).

## Catalog consumption

`ms.load(...)` and `session.catalog` return the same `SemanticCatalog` type.
The first loads the requested project/domain scope; the latter uses the Session's
saved project and domain selection. They may be different snapshots, but there
is one discovery/lookup contract. Its canonical Help owners are
`semantic.semantic_catalog.search`, `semantic.semantic_catalog.get` and
`semantic.readiness`. `analysis.inputs.catalog` explains acquisition and links
to these native owners; it does not register parallel Catalog methods.

Use `catalog.search(query, kind=..., domain=..., entity=..., limit=10)` to discover
business objects. Empty query browses deterministically without model calls.
Every page contains immutable typed directory entries, captured scope, explicit
filters, query matching records, retrieval metadata and a continuation cursor.
`candidate_count` counts the fixed candidate pool; it is not a global semantic
match count. Display budgets do not change `items` or `next_cursor`; `.to_dict()`
retains the full API page. Resume with the same query and filters; stale cursors
fail explicitly instead of silently reranking.

`catalog.get("metric:sales.revenue")` retrieves one canonical `kind:path`
identity. It returns a closed union: check `entry.kind` before using specialized
members. Executable variants expose native closed `.value`; Domain/Datasource
entries have no executable value. Calendar and temporal-set entries retain
certified period/occurrence operations.

```python
page = catalog.search("accepted revenue", kind="metric", limit=5)
page.show()
entry = catalog.get("metric:sales.revenue")
if entry.kind == "metric":
    entry.show()
    revenue = entry.value
```

`entry.definition` retains complete captured metadata, readable formulas, time
bindings and context. `.show()` is a bounded projection; `.to_dict()` excludes
execution values and internal registries. Entries do not proxy expression
attributes. Query scores and snippets belong to pages, not definitions.
`entry.related("dependencies")` and reverse navigation use the full loaded
Catalog scope, independently from discovery filters. Known unloaded identities
are disclosed separately as `outside_scope`; navigation does not auto-load them.
A new load acquires current same-path definitions; existing entries retain their
captured snapshot.

### Search configuration and derived state

Without model configuration, text search is lexical. Configure
`[semantic.search]` with `mode = "hybrid"`, `embedding_model`, a pinned
`embedding_revision`, `query_prefix`, `document_prefix`, `max_tokens`,
`batch_size` and `candidate_window` to enable multilingual vector recall.
`[semantic.search.relevance]` requires a project-specific `calibration_id` and
`min_similarity`; optional reranking also requires `min_reranker_score` and
`[semantic.search.reranker]` model/revision/length settings.

Install the `semantic-search` extra and explicitly prepare the pinned model in
the environment's Hugging Face cache. Runtime model loading is local-only and
does not download weights. `ms.load` freezes configuration and does not run
inference. Only configured text search loads models, encodes missing documents
and queries, and optionally reranks. Input overflow and model failures raise
structured semantic errors; configured hybrid never silently falls back.

Both recall channels apply explicit filters before their top-k windows. RRF
combines ranks; canonical identity/path exact matches remain first and survive
relevance gating within those filters. Similarity does not establish identical
business meaning or execution authority. Author `ms.ai_context(aliases=[...])`
for explicit search aliases; aliases do not create alternate identities.

Rebuildable vector, index and fixed-ranking caches live in
`<project_root>/.marivo/semantic/search/`. Keys capture text projection, model
revision/encoding parameters and scope. Changed definitions invalidate affected
projections; stale/deleted members cannot reappear through vector reuse. No
business datasource is read by load, search, get, show or related navigation.

## Validation boundaries

| Operation | Reads | Establishes | Does not establish |
| --- | --- | --- | --- |
| ms.load | Project Python and configuration | Static project/dependency coherence | Physical types, external health, numerical execution |
| catalog.readiness | Loaded definitions and local temporal artifacts | Ready physical requested inputs and closures | Backend/method qualification or business completeness |
| catalog.preview | Explicit bounded current source scope | Scoped runtime values; ordinary Metric preview is approximate | Whole-source correctness or ordinary readiness promotion |
| catalog.source_health | Connectivity/metadata; scoped data only for explicit checks | Requested current observations | Persistent readiness, business meaning or universal match coverage |
| Temporal preview | Complete declared finite authority source | Certified immutable calendar/set/schedule snapshot | Business fact capture or arbitrary Analysis support |
| Frame.compute / persist | Explicit prepared analysis dependencies | Actual admitted computation; persist also commits it | Unrun providers, installed packages or release qualification |

Readiness accepts selected entries and expands their physical dependency closures.
Requested and checked identities remain distinct in the report. Structured repairs
list actual dependency candidates; derived expression selection is not execution proof.
analysis_ready_inputs includes only directly requested physical refs whose
closures have no blocker. Dependency refs remain diagnostic inputs. Warnings
and blockers stay on the same ReadinessReport.

## Runtime preview and source health

Build preview scopes with md.partition, md.time_range or md.unpruned. Positive
row guards and requested native timeouts bound acquisition; a returned-row limit
is not a scan bound or complete-call deadline. For catalog.preview and
catalog.preview_many, limit must be an integer from 1 to 100 inclusive and
bounds retained preview rows per result. The scope.max_rows acquisition guard
is independent. Invalid limits raise a structured SemanticRuntimeError before
connection acquisition, with the accepted bounds and a repair. Multiple dependency
Entities use an ordered sequence of `(EntitySymbol, AuthoringScope)` pairs. JSON preview can supply explicit non-secret
source_bindings as `(EntitySymbol, parameter_mapping)` pairs. A single Entity scope
still accepts one AuthoringScope; source_bindings always explicitly names its Entity.
Duplicate, missing and unrelated Entity pairs fail before source access. Ordinary preview reads the current datasource and does not
persist a general validation checkpoint.

Ordinary Metric preview supports DuckDB source reductions/formulas over at most
10,000 Entity output rows and reports an approximate result. Other backends reject
Metric preview before source access; evaluate those Metrics with
`session.observe(...).compute()` and explicit analysis inputs. This restriction
applies to Metrics, independently from Entity and physical field preview support.
Population/internal-sample preparation is rejected there; use an actual Analysis
observation for that work. The display limit is independent from acquisition and
aggregate scope.

Source health with checks omitted tests connectivity and schema/capability
metadata without a business-data query, and needs no data scope. Explicit
ms.source_check rules for missingness, enum, uniqueness, freshness, relationship
matches or cardinality require a bounded scope when reading rows. Results report
that actual scope and remain ephemeral. Analysis trusts declared identity,
relationship cardinality and version grain without automatically running these
checks. Missing scalar matches follow the consuming operator's NULL policy;
explicit source_check is the route for validating source matching expectations.

Temporal previews intentionally publish dedicated artifacts because the source
values define executable semantic authority. Their exhaustive certification and
staleness rules are owned by [Temporal authority](temporal-authority.md).
PreviewResult.is_truncated reports whether the retained preview omits observed
rows or certified temporal entries, independently from acquisition completeness
in coverage. Its card labels preview_limit,
preview_rows_truncated and scope_coverage separately. A successfully certified
temporal snapshot can therefore have is_truncated=True with
exhaustive/scope_exact coverage; the smaller retained preview does not invalidate
the certificate. .show(n=...) only bounds display of those retained rows.

## Errors and implementation

Semantic errors retain structured expected/received facts, declaration locations
and concrete repairs. Missing business definitions cannot be repaired by guessed
source names. Execution failures preserve their original cause; inspect the
specific owner rather than treating load, readiness and source access as one stage.

[loader.py](../../../marivo/semantic/loader.py) and
[reader.py](../../../marivo/semantic/reader.py) own project assembly;
[catalog.py](../../../marivo/semantic/catalog.py) owns public consumption and
preview dispatch; [readiness.py](../../../marivo/semantic/readiness.py) and
[source_health.py](../../../marivo/semantic/source_health.py) own their independent
reports. [check.py](../../../marivo/semantic/check.py) exposes project checking
through the CLI without creating another semantic state authority.
