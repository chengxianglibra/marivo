# Entity Expressions over a Single Source

Status: proposed design; not implemented

Date: 2026-09-21

Implementation plan: [Entity expression implementation](../plans/2026-09-21-entity-expression-implementation.md).

## Purpose

Allow an Entity to define a reusable logical relation over one declared physical
Source. Filtering, deduplication, projection, derived columns, and aggregation
belong in that relation when they define the business dataset consumed by all
downstream semantic objects. Metrics then express calculations over the Entity
output instead of repeating source preparation.

An Entity's output schema is authoritative. It is the Source schema for a direct
declaration and the returned Ibis Table schema for an expression declaration.
The two schemas need not be equal.

This document records the intended extension. The current implementation still
has body-free Entities. It does not authorize implementation, publication, or
changes to external database objects.

## Decisions and boundaries

1. Keep `ms.entity` as the only public Entity authoring entrypoint. Add a decorator
   form without introducing filter, deduplication, or preprocessing constructors.
2. Use the same single-expression body convention as Metric decorators: one
   optional leading docstring followed by exactly one `return <Ibis expression>`.
   “Lambda-like” describes this restriction; Python lambda syntax is not a new
   authoring form.
3. Inject exactly one Table, resolved from the declared `datasource` and `source`.
   Implicit or additional data sources are not supported.
4. Require an Ibis Table result. Do not require preservation of source columns,
   types, order, grain, or row count. Do not impose an operation whitelist that
   forbids projection or aggregation.
5. Make downstream fields, keys, versioning, relationships, and metrics consume
   the Entity output relation.
6. Leave predicate pushdown, partition pruning, and SQL optimization to Ibis and
   the SQL engine. Marivo does not prove predicate commutativity, move predicates
   through Entity expressions, or promise a bounded physical scan from a result
   filter.

Multiple Source inputs, Entity-to-Entity expression dependencies, authored SQL
bodies, auxiliary Python functions, and data-dependent Python execution are
outside this design. Existing backend execution capabilities remain applicable;
accepting an expression does not add support for an operation to every backend.

## Authoring API

### Direct declaration

The existing call remains unchanged and returns `Ref[entity]` immediately:

```python
import marivo.datasource as md
import marivo.semantic as ms

warehouse = ms.ref.datasource("warehouse")

orders = ms.entity(
    name="orders",
    datasource=warehouse,
    source=md.table("orders"),
    primary_key=["order_id"],
)
```

### Expression declaration

Omitting `name` selects decorator construction. The decorated function name is
the Entity name; decorating returns the same `Ref[entity]` family as the direct
form. This is a proposed API, not runnable against the current implementation.

```python
import ibis
from ibis import _

@ms.entity(
    datasource=warehouse,
    source=md.table("order_changes"),
    primary_key=["dt", "order_id"],
)
def daily_orders(raw):
    """One latest, non-deleted, non-test order per day and order ID."""
    return raw.filter(
        ibis.row_number().over(
            ibis.window(
                group_by=[raw["dt"], raw["order_id"]],
                order_by=[
                    raw["updated_at"].desc(),
                    raw["revision_id"].desc(),
                ],
            )
        ) == 0
    ).filter(
        _["is_deleted"] == False,
        _["is_test"] == False,
    )
```

The order in the authored expression is meaningful: selecting the latest row
before excluding deletion records differs from excluding deletion records first.
The author owns tie-breaking and null semantics. A declared primary key does not
itself prove uniqueness or make an ambiguous ordering deterministic.

The `name` distinction avoids an ambiguous factory: a call with explicit `name`
already has an established meaning and returns a data-only Ref. This design does
not make that Ref callable, register a temporary direct Entity, or later replace
its declaration when Python applies a decorator. Explicit decorator name
overrides are not part of the initial interface. Public overloads distinguish
the direct Ref return from the decorator return.

Other existing Entity arguments, including `domain`, `primary_key`, `versioning`,
and `ai_context`, retain their meaning but describe the output Entity. An omitted
primary key remains omitted; neither source keys nor aggregation keys are
automatically inferred as business identity.

### Output schema may change

```python
@ms.entity(
    datasource=warehouse,
    source=md.table("orders"),
    primary_key=["dt", "shop_id"],
)
def daily_sales(raw):
    return raw.group_by("dt", "shop_id").aggregate(
        sales_amount=raw["amount"].sum(),
        order_count=raw.count(),
    )

sales_amount = ms.measure_column(
    name="sales_amount",
    entity=daily_sales,
    column="sales_amount",
    additivity="additive",
    unit="CNY",
)

revenue = ms.aggregate(name="revenue", measure=sales_amount, agg="sum")
```

`daily_sales` exposes only `dt`, `shop_id`, `sales_amount`, and `order_count`.
Downstream access to `amount` fails even though the physical Source has that
column. Counting Entity rows counts shop-day records. Counting underlying orders
requires summing the declared `order_count` measure.

Entity aggregation defines a reusable dataset grain. Metric definitions still
own the business meaning of calculations over that grain. Additivity, units,
ratio components, and valid reaggregation remain explicit downstream contracts;
Marivo does not infer them from a returned schema or silently recover discarded
detail. For example, averaging precomputed group averages is not automatically
equivalent to averaging the underlying observations.

## Expression and source contracts

### Body shape

The decorator takes one ordinary positional Table parameter. Reject variadic,
keyword-only, defaulted, or additional parameters. Allow an optional docstring
and one return expression. Reject assignments, auxiliary function calls, nested
functions, lambdas, loops, comprehensions, statement conditionals, and exception
handling. Ibis conditionals and method chains remain expression construction.

Reuse the existing expression compiler and AST validation conventions where
applicable, extending them for a Table-valued Entity body. Do not weaken Metric
or field contracts while adding Entity support. AST validation owns body shape
and prohibited escape hatches; it does not infer the result schema or decide
whether relational operators preserve business meaning.

Only the injected parameter, inline authored literals, and supported Ibis
expression-building symbols may supply the expression. Do not admit captured
Tables, helper callables, mutable external configuration, environment lookups,
clock reads, random values, or execution/materialization calls such as
`execute()` and `to_pandas()`. Support normal Ibis import aliases and deferred
expressions such as `_` through resolved symbol identity, not spelling guesses.
No `ms.bind` dependency on this Entity's downstream fields is allowed: those
fields do not exist as inputs to their own Entity relation.

These are semantic authoring rules, not a sandbox for model modules. The loader
continues to execute trusted local Python modules under its existing contract.

### Single declared Source

Validate the returned expression's relation dependencies against the exact
injected Source relation. The check concerns source ownership, not a list of
allowed relational operations.

- Projection, filters, windows, aggregation, and distinct operations may change
  the output freely within the backend's supported Ibis capabilities.
- Reusing the injected relation, including a self-join or union of branches from
  that relation, does not introduce a second Source. It still requires an
  appropriate output key and business definition.
- Captured Tables, newly connected tables, independent unbound tables,
  independently created in-memory tables, and file readers are rejected, even
  when their names or schemas match the declared Source.
- A detached constant relation is not a substitute for the declared Source.
  Constant columns inside a relation derived from that Source are allowed.
- Datasource-owned physical preparation, including typed table projections and
  file readers, is encapsulated by the injected relation. Internal generated SQL
  inside that relation is not an authored SQL escape hatch.

Do not identify ownership by matching table names. Treat the injected relation
as the trusted boundary and reject additional relation roots. Preserve the
declared datasource and Source in lineage alongside the Entity transformation.

## Schema authority and validation stages

There are two separate interfaces:

| Interface | Authority | Consumers |
| --- | --- | --- |
| Source input schema | Datasource metadata or declared typed Source schema | Entity expression input, physical inspection, explicit physical scopes |
| Entity output schema | Returned Ibis Table; Source Table for direct declarations | Dimensions, time dimensions, measures, metrics, output keys and versioning |

`md.table(columns=...)` still defines the complete Source input interface. An
Entity body cannot access omitted physical columns by their original names.
It may expose new or renamed output columns for downstream definitions.

Loading performs declaration, body-shape, reference, and dependency checks without
introducing mandatory datasource connections or row queries. When an input
schema is already available, output validation may build the expression against
an equivalent unbound input. Otherwise schema-dependent checks are deferred to
existing runtime validation, preview, or first-use paths and must not be reported
as already passed.

Building the expression must return a Table; inspect its schema without executing
its rows. Validate downstream column references and required output key fields
against that schema. Missing input columns are Entity-definition failures;
missing output columns referenced by a Measure or Dimension are downstream
definition failures. Repairs must distinguish the two and show bounded actual
available columns.

Source health continues to inspect physical availability and schema. It must no
longer compare downstream Entity fields directly to physical column names. For
an expression Entity, use the inspected Source schema to construct and validate
the output relation where possible; report inability to establish the output
separately from physical connectivity or missing source columns.

Snapshot and validity declarations remain supported under their existing rules,
but all referenced fields and primary-key components belong to Entity output.
Transformations that remove required fields fail with a concrete repair; no
fallback to source columns or inference of replacement versioning is allowed.

## Materialization, scope, and execution order

The common Entity resolver becomes:

```text
resolve declared Source and its runtime bindings
    -> apply any explicitly supplied physical input scope
    -> evaluate Entity expression, or use identity for direct declarations
    -> validate Table result, source ownership, and output requirements
    -> apply existing preview/sample row limits to Entity output
    -> expose Entity Table to semantic fields and analysis
```

All consumers must share this boundary: ordinary observation, cumulative
calculations, relationships, Event/Lifecycle subject relations, previews, and
explicit source-health data checks. No consumer may substitute the physical
table for a transformed Entity.

An existing `md.PartitionScope` is an explicitly requested physical input scope
over Source-exposed column names, before the Entity expression. It may reference
a column omitted from Entity output. This is scoped computation, not a rewrite
of an output filter and not evidence that the same result holds over all source
partitions. Preview and health evidence must retain the input scope and its
coverage. Applying that scope before aggregation or deduplication can change
the result, which is why it must remain explicit.

Analysis filters and time windows continue to target semantic fields over the
Entity output. Marivo must not reinterpret them as physical input scopes, move
them through windows or aggregation, or reconstruct an input partition predicate
from an output field. Ibis and the database may optimize equivalent expressions.
Actual pruning remains backend execution behavior, not an authoring guarantee.

### Row budgets, sampling evidence, and cost

Row budgets refer to different stages. Preserve the distinction in execution,
Help, and preview evidence:

| Control | Application point | Meaning |
| --- | --- | --- |
| Explicit physical partition/time scope | Source input, before Entity expression | Defines the requested input relation; does not establish global coverage. |
| Semantic-preview `scope.max_rows` | Entity output, after its expression | Bounds acquired Entity rows, with the existing extra-row probe where applicable. |
| Metric-preview `sample_size` | Entity output, before Metric aggregation | Bounds Entity rows supplied to the Metric; this remains an input sample for that Metric. |
| Final preview `limit` | Preview result | Bounds returned/displayed rows; does not establish computational completeness. |
| `timeout_seconds` | Adapter-governed execution | Limits execution time under existing adapter behavior, not scanned rows, bytes, or monetary cost. |

This interpretation of `scope.max_rows` applies to semantic previews and their
Entity boundary. Datasource acquisition APIs continue to limit acquired Source
rows under their existing contracts; this design does not globally redefine
`md.partition(...)` or change datasource sampling.

Never truncate raw candidates before the Entity expression merely to meet an
output budget. That would change authored aggregates or which row wins a
deduplication. This is a regression to prevent when expression Entities are
introduced, not a claim that current direct-Entity Metric previews are defective.
Their existing pre-aggregate truncation is intentional and disclosed.

For Metric preview, the sequence is explicitly:

```text
explicit Source input scope
    -> Entity expression
    -> Entity-output row budgets
    -> Metric aggregation over those Entity rows
    -> final preview result limit
```

Keep `PreviewSamplePolicy(method="pre_aggregate_limit", ...)` and the warning
that Metric preview does not prove a full-scope aggregate. Explain that the
limited input consists of Entity output rows. For example, if an Entity produces
100,000 shop-day aggregates and preview supplies 10,000 of them to a revenue
Metric, each retained shop-day aggregate preserves the Entity definition, but
their sum is not the complete scoped revenue. One scalar result does not prove
complete input coverage. Preserve both scope and sampling evidence; do not
upgrade completeness based on result cardinality or remove the existing warning.

Accept the potential increase in execution cost: aggregation or window-based
deduplication may require processing the entire explicitly scoped Source input,
or the unpruned Source when that scope was requested, before producing a small
Entity output. Do not add an implicit raw-input cap or automatic sampled-input
fallback. Existing explicit scopes, timeouts, and backend execution controls
remain the cost controls.

Neither the old nor new `LIMIT N` placement guarantees that only N physical rows
are scanned. Depending on filters, ordering, views, and execution plans, a limit
may permit early termination or still require substantial scanning. Therefore
describe this change as moving truncation from Source input to Entity output,
not as changing a guaranteed N-row scan into a full scan. Only backend execution
evidence can establish actual pruning and scan volume.

## Definition identity and disclosure

Capture the Entity body in the existing compiled-expression sidecar and include
its normalized definition digest in Entity dependency fingerprints. Include the
declared Source, datasource, output key, and versioning as today. A body change
must invalidate dependent semantic/evidence compatibility even when output
schema stays unchanged.

No helper functions or mutable external values can supply hidden business logic;
the single-expression restriction makes the authored body and declared Source
the definition boundary. Schema observations remain source-dependent evidence;
they are not a replacement for the expression fingerprint. Runtime source
bindings and input scopes retain their existing identity and evidence roles.

Preserve existing direct-Entity identity when no body is present; do not add a
synthetic identity function to every existing declaration. Do not create a
second Ref family or a second Entity registry for expression declarations.

Help describes both construction forms and their dispatch. Entity cards disclose
direct versus expression form and the declared Source without claiming that
physical columns are the output schema. Resolved output schema belongs to
bounded current-state/preview reporting, not speculative static metadata.
Definition displays reuse captured normalized expression presentation where
supported; reading a definition never calls the Entity body or queries a Source.

## Current implementation seams

The following files identify concrete adaptation points, not a requirement for a
particular internal class layout:

| Area | Current implementation | Required adaptation |
| --- | --- | --- |
| Authoring | `marivo/semantic/_authoring_decorators.py`, `ir.py` | Two construction forms; one Entity model with optional expression identity. |
| Body compilation | `marivo/semantic/_expression_binding.py`, `validator.py` | Entity body ownership, one Source parameter, Table result, existing expression restrictions. |
| Source aliases | `validator.py::_validate_projected_source_aliases` | Stop using Source aliases as the downstream schema for expression Entities. |
| Materialization | `materializer.py::Materializer.entity` | Build the output relation once through the shared resolver; separate input predicates from output row limits. |
| Field evaluation | `materializer.py::dimension`, `measure`, field-on-table paths | Continue consuming Entity output; never recover dropped physical fields. |
| Analysis | `analysis/intents/_observe_catalog.py::_build_entity_adapter` | Preserve the resolver-based Entity input and verify related planning paths. |
| Source health | `source_health.py::_schema_missing_fields` | Separate physical input validation from downstream output validation. |
| Preview | `catalog.py` preview and certification paths | Report output columns/types and preserve explicit input scope and output-limit semantics. |
| Identity | `metric_graph_lowering.py::_entry_for`, `_DependencyCollector`, `_definition_identity.py` | Replace the expression Entity's current absent body digest; capture its complete declared dependency closure. |
| Provenance | `materializer.py::_detect_and_store_provenance`, catalog cards | Preserve physical origin while disclosing that the output is transformed. |

The existing Dimension/Measure materializers and analysis adapter already use a
resolved Entity Table. They do not inherently require equality with physical
schema. Static projected-column validation and source-health comparisons are
the confirmed places where Source and Entity interfaces are currently conflated.

## Errors

Use existing semantic exception families and shared rendering. Include the
Entity or downstream ref, expected contract, received shape or schema, and a
repair based on actual declarations. Relevant failures include:

- invalid body or signature: return one Ibis Table expression over the one input;
- scalar, column, DataFrame, or other non-Table return: author a Table expression;
- additional or detached Source: derive the relation from the declared input;
- missing input field: correct the Entity expression or Source column binding;
- missing output field/key/version axis: expose it or update the consuming
  declaration to match the intended output grain;
- unsupported backend expression: identify the failed operation/backend through
  existing capability/error handling without silently switching execution.

Schema availability, source connectivity, expression validity, successful query
execution, and business approval remain distinct claims.

## Acceptance criteria

1. Existing direct declarations retain their Ref type, behavior, and definition
   identity. Decorator declarations infer names without temporary registration
   or callable Refs. Duplicate-name handling remains consistent.
2. A renamed or derived output column can be consumed by column constructors and
   expression fields. Dropped source columns cannot be consumed downstream.
   This also works with `md.table(columns=...)` input aliases.
3. A grouped Entity exposes its new schema and grain. Entity counts, measure
   aggregation, dimensions, and a relationship using its output key agree on
   those output rows. No test assumes the original physical grain survives.
4. A deterministic deduplication expression followed by filtering returns the
   expected rows through Entity preview and metric observation. Reverse-order
   fixtures demonstrate the business difference without Marivo rewriting it.
5. Single-expression bodies pass. Assignments, helpers, additional parameters,
   nested lambdas, SQL/execution escapes, and captured external Tables fail.
   Self-derived branches remain allowed; a same-named unrelated Table fails.
6. Physical Source health and output-field checks distinguish an unavailable
   input field from a valid derived field absent in physical metadata. No load
   path adds an unconditional metadata connection or row query.
7. Explicit physical scope applies before the body, including when its column is
   absent from output. Output limits apply after the body. Evidence records the
   scope; an analysis output filter is never relabeled as an input scope.
   A group with more raw rows than the output budget still produces its correct
   Entity aggregate or deduplication winner. A Metric over a truncated Entity
   output remains marked `pre_aggregate_limit` and is not reported as complete.
   Datasource acquisition limits retain their existing meaning.
8. Snapshot/validity fields and Event/Lifecycle identity paths consume output
   fields; removed required fields fail rather than falling back to the Source.
9. Changing only the Entity body changes downstream definition identity and
   compatibility. Unrelated definitions and unchanged direct Entities retain
   their established identity behavior.
10. Focused Help, catalog disclosure, source-health repairs, and preview types
    consistently describe the output relation. Representative supported-backend
    compilation/execution checks distinguish actual support from mere loading.
    Cost guidance does not equate row limits with scan limits or timeouts with
    row/byte/cost caps. Execution failure does not trigger raw-input sampling.

Use focused fixtures and checks for these contracts during implementation.
Broaden to shared behavior and repository gates when the common resolver,
expression compiler, or dependency identity changes. Backend SQL compilation
does not establish real-service acceptance or physical partition pruning.

## Documentation integration when implemented

Update the owning semantic overview, object model, loading/validation, authoring,
and definition-reading documentation alongside implementation. In particular,
replace the current blanket statements that Entities have no Python body and
cannot perform aggregation with the logical-relation/output-grain contract.

Align `semantic.entity` Help, native registries and budgets, examples, CLI
guidance where affected, packaged semantic/analysis workflow references, and
English and Chinese latest site documentation in the same disclosure change.
Do not duplicate parameter inventories in packaged skills. No new public
preprocessing helpers or public result families are required.

This proposal intentionally leaves the current normative documents describing
implemented behavior until that change is made.
