# Semantic Loading, Validation and Introspection

Loading establishes a coherent semantic project. Readiness, source health,
ordinary preview and temporal certification answer different questions; no
single success flag covers all of them.

## Project selection and loading

```python
import marivo.semantic as ms

catalog = ms.load(workspace_dir=project_root)
orders = catalog.entities.get("sales.orders")
revenue = catalog.metrics.get("sales.revenue")
catalog.describe(ms.ref.metric("sales.revenue")).show()
```

workspace_dir is an exact workspace root. If omitted, root resolution uses
MARIVO_PROJECT_ROOT, the nearest ancestor manifest, then cwd. Local models and
configured external model roots form one load scope. Duplicate identities fail
with both declaration locations; later roots do not override earlier ones.
The optional domains filter selects a requested domain view with its required
dependency closure.

The loader inspects restricted source, captures declarations, resolves complete
references and validates organization, kind, dependency cycles and expression
contracts. It does not connect, inspect source schemas, prove key uniqueness or
run business queries. Entity/Source and field Ibis bodies remain restricted;
Measure/Metric declaration functions are captured without invocation.
Ordinary imported modules are not guaranteed to hot-reload. Restart Python after
model edits/deletions or package changes when import state could be stale.

An existing Catalog retains its loaded scope. Changing cwd, environment variables
or a same-path file does not mutate that Catalog or an already closed expression.
A new load establishes a new scope; see [definition identity](semantic-object-model.md#context-and-immutable-definitions).

## Catalog consumption

Collections accept a unique local name or complete path string within their
scope. They do not accept kind-prefixed strings or Ref objects as get keys.
Domain-scoped collections cannot escape their domain. Empty collections,
unknown names and ambiguous short names remain distinct outcomes.

| Collection | Returned value |
| --- | --- |
| entities | Immutable resolved Entity |
| fields | Native Deferred categorical/time/Measure expression |
| metrics | Native Deferred Metric expression |
| relationships | Immutable resolved Relationship |
| domains / datasources | Entry with details and narrower collections |
| period_calendars / temporal_sets | Entry backed by dedicated temporal authority |

Use catalog.describe(ref) for captured expression metadata; Deferred has no
Marivo show/contract method. Entry details and typed fields describe captured
facts. Help describes static operation syntax. [Public surface](../agent-friendly-public-surface.md)
defines these disclosure responsibilities.

## Validation boundaries

| Operation | Reads | Establishes | Does not establish |
| --- | --- | --- | --- |
| ms.load | Project Python and configuration | Static project/dependency coherence | Physical types, external health, numerical execution |
| catalog.readiness | Loaded definitions and local temporal artifacts | Ready physical requested inputs and closures | Backend/method qualification or business completeness |
| catalog.preview | Explicit bounded current source scope | Scoped runtime values; ordinary Metric preview is approximate | Whole-source correctness or ordinary readiness promotion |
| catalog.source_health | Connectivity/metadata; scoped data only for explicit checks | Requested current observations | Persistent readiness, business meaning or universal match coverage |
| Temporal preview | Complete declared finite authority source | Certified immutable calendar/set/schedule snapshot | Business fact capture or arbitrary Analysis support |
| Frame.compute / persist | Explicit prepared analysis dependencies | Actual admitted computation; persist also commits it | Unrun providers, installed packages or release qualification |

Readiness accepts physical inputs, rather than published derived expressions.
Describe an expression and check its physical dependencies. Structured repairs
list actual dependency candidates; the caller explicitly chooses the retry.
analysis_ready_inputs includes only directly requested physical refs whose
closures have no blocker. Dependency refs remain diagnostic inputs. Warnings
and blockers stay on the same ReadinessReport.

## Runtime preview and source health

Build preview scopes with md.partition, md.time_range or md.unpruned. Positive
row guards and requested native timeouts bound acquisition; a returned-row limit
is not a scan bound or complete-call deadline. Multiple dependency Entities use
an exact Entity-ref-to-scope mapping. JSON preview can supply explicit non-secret
source_bindings. Ordinary preview reads the current datasource and does not
persist a general validation checkpoint.

Metric preview supports ordinary source reductions/formulas over at most 10,000
Entity output rows and reports an approximate result. Population/internal-sample
preparation is rejected there; use an actual Analysis observation for that work.
The display limit is independent from acquisition and aggregate scope.

Source health with checks omitted tests connectivity and schema/capability
metadata without a business-data query, and needs no data scope. Explicit
ms.source_check rules for missingness, enum, uniqueness, freshness, relationship
matches or cardinality require a bounded scope when reading rows. Results report
that actual scope and remain ephemeral. A passing relationship check does not
authorize an unrelated consumer's required-match assumption.

Temporal previews intentionally publish dedicated artifacts because the source
values define executable semantic authority. Their exhaustive certification and
staleness rules are owned by [Temporal authority](temporal-authority.md).

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
