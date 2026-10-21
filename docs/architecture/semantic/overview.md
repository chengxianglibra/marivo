# Semantic and Datasource Architecture

Marivo consumes Python-authored business definitions through a write-run-read
loop. Datasource owns physical access; Semantic owns reusable meaning; Analysis
owns the selected question and execution. Source schemas and sampled rows do not
establish a business definition.

## Module boundaries

```text
marivo.datasource
  typed connection specs + physical sources + scoped evidence
        |
marivo.semantic
  project/domain + Entity/Relationship + row/Metric expressions
  immutable resolved definitions + certified temporal snapshots
        |
marivo.analysis
  Population + explicit observations + AnalysisFrame operations
  complete memory/stored values + separate Artifact audit
```

Lower layers do not import Analysis to discover business meaning or execution
policy. Shared definition, temporal, error and introspection infrastructure lives
below these public modules. Private registries implement each domain's native
Help; `marivo.help` is the common public coordinator.

| Topic | Owning document |
| --- | --- |
| Connections, secret resolution and datasource registration | [Datasource](../datasource/README.md) |
| Physical descriptors and provider execution | [Sources and execution](../datasource/sources-and-execution.md) |
| Inspection, bounded sampling and discovery snapshots | [Discovery](../datasource/discovery.md) |
| Domain, stable identity, versioning, fields and relationships | [Object model](semantic-object-model.md) |
| Physical capture, Deferred grammar, units and reductions | [Expressions](expressions.md) |
| Loading, Catalog, readiness, preview and source health | [Loading and validation](loading-validation-introspection.md) |
| Choosing evidence and completing an authoring task | [Authoring workflow](authoring-workflow.md) |
| Period calendars, temporal sets and work schedules | [Temporal authority](temporal-authority.md) |
| Exact boundaries, windows and frozen time coordinates | [Temporal semantics](../temporal-semantics.md) |
| Analysis source binding, data operations and Runtime | [Analysis](../analysis/README.md) |
| Help, state display and repair ownership | [Public surface](../agent-friendly-public-surface.md) |

## Semantic authority

Python files are the source of truth. Datasources are project-wide names under
`models/datasources/`; domains explicitly own semantic names under
`models/semantic/`. Domain membership comes from `domain=` or the declared
default domain, rather than being inferred from arbitrary module paths.

Entity primary keys identify stable business instances; snapshot/validity
coordinates select representations. Metric contribution roots come from its
receivers and dependencies. Analysis membership, observation time and reporting
coordinates remain independent choices. A relationship describes mappings;
the consuming expression determines contribution selection or allocation.

Physical constructors return lookup Refs. Finalization closes the physical
dependency scope and resolves immutable values for composition. Measure/Metric
declarations capture closed expressions without executing authored functions.
Entity and categorical/time-field bodies retain their restricted Ibis contract.
SQL is available only through the terminal datasource escape hatch.

Completed scopes resolve by kind/path. A changed same-path definition does not
by itself require rejection against the input's original version. Existing
expressions remain immutable, and consumers still enforce kind, root, grain,
unit, time and null constraints. Computation fingerprints describe the actual
consumed definitions; this policy permits neither live refresh nor import replay.

## Handoff and proof boundaries

`ms.load()` establishes project coherence without source reads. Catalog
collections expose physical resolved values and published Deferred expressions;
`catalog.describe(ref)` describes a published expression. Readiness checks
physical dependency closures and dedicated temporal artifacts without a query.
Its `analysis_ready_inputs` contains only directly requested ready physical refs.

Ordinary preview and source-health reports answer scoped external-data questions.
Temporal certification is different: its finite source values define executable
calendar authority, so preview publishes a dedicated validated snapshot.
Analysis preparation qualifies the actual source types, method and backend.
None of these stages certifies another stage merely by succeeding.

If an analysis needs new reusable meaning, author it in Semantic first. If it
needs a new observation of existing meaning, construct explicit Analysis inputs.
Fixed Frame values carry types, complete keys and time interpretation, without
recoverable nominal Entity/Field authority or hidden Metric state.

## Implementation map

| Responsibility | Code |
| --- | --- |
| Physical adapters and managed reads | [datasource/runtime.py](../../../marivo/datasource/runtime.py), [engines](../../../marivo/datasource/engines/) |
| Declaration capture and restricted syntax | [authoring.py](../../../marivo/semantic/authoring.py), [validator.py](../../../marivo/semantic/validator.py) |
| Project assembly and materialization | [loader.py](../../../marivo/semantic/loader.py), [reader.py](../../../marivo/semantic/reader.py), [materializer.py](../../../marivo/semantic/materializer.py) |
| Catalog, validation and snapshots | [catalog.py](../../../marivo/semantic/catalog.py), [readiness.py](../../../marivo/semantic/readiness.py), [source_health.py](../../../marivo/semantic/source_health.py) |
