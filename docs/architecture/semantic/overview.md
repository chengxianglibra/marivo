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

`marivo.help("authoring")` links directly to project loading, existing definitions,
connections, physical evidence, reusable modeling, expression composition and
validation. Optional ontology context is a separate exit. `semantic.load` owns
workspace selection and source layout; initialization is optional and an empty
project includes the built-in `default` datasource.
Datasource navigation separates connections, sources, inspection and the
semantic handoff. Semantic navigation separates model definitions, expression
roles, time, catalog and validation. Exact references remain with their API or
semantic-role owner; navigation does not duplicate their contracts.

| Topic | Owning document |
| --- | --- |
| Connections, secret resolution and datasource registration | [Datasource](../datasource/README.md) |
| Physical descriptors and provider execution | [Sources and execution](../datasource/sources-and-execution.md) |
| Inspection, bounded sampling and discovery snapshots | [Discovery](../datasource/discovery.md) |
| Domain, stable identity, versioning, fields and relationships | [Object model](semantic-object-model.md) |
| Physical capture, Deferred grammar, units and reductions | [Expressions](expressions.md) |
| Loading, Catalog, readiness, preview and source health | [Loading and validation](loading-validation-introspection.md) |
| Choosing evidence and completing an authoring task | [Authoring workflow](authoring-workflow.md) |
| Period calendars and temporal sets | [Temporal authority](temporal-authority.md) |
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

Physical constructors and `ms.ref.*` return native typed Deferred symbols.
Compose symbols directly; the loader binds the complete dependency graph and
publishes immutable closed Catalog values. Measure/Metric declarations capture
restricted recipes without executing authored functions.
Entity and categorical/time-field bodies retain their restricted Ibis contract.
SQL is available only through the terminal datasource escape hatch.

Completed scopes resolve by kind/path. A changed same-path definition does not
by itself require rejection against the input's original version. Existing
expressions remain immutable, and consumers still enforce kind, root, grain,
unit, time and null constraints. Computation fingerprints describe the actual
consumed definitions; this policy permits neither live refresh nor import replay.

## Handoff and proof boundaries

`ms.load()` establishes project coherence without source reads. Catalog search
and canonical get return immutable typed directory entries; complete definitions
explain captured meaning and executable variants expose native closed `.value`. Readiness checks
physical dependency closures and dedicated temporal artifacts without a query.
Its `analysis_ready_inputs` contains only directly requested ready physical refs.

Ordinary preview and source-health reports answer scoped external-data questions.
Temporal certification is different: its finite source values define executable
calendar authority, so preview publishes a dedicated validated snapshot.
Analysis preparation qualifies the actual source types, method and backend.
None of these stages certifies another stage merely by succeeding.

If an analysis needs new reusable meaning, author it in Semantic first. If it
needs a new observation of existing meaning, construct explicit Analysis inputs.
Existing closed inputs can also form question-scoped equations, conditions,
internal samples and windows. Analysis inputs and Metric parameter acquisition link directly
to `semantic.expressions` for that shared grammar; a missing same-name published
Metric does not require a new declaration when meaning and aggregation authority
are already established.
Fixed Frame values carry types, complete keys and time interpretation, without
recoverable nominal Entity/Field authority or hidden Metric state.

## Implementation map

| Responsibility | Code |
| --- | --- |
| Physical adapters and managed reads | [datasource/runtime.py](../../../marivo/datasource/runtime.py), [engines](../../../marivo/datasource/engines/) |
| Declaration capture and restricted syntax | [authoring.py](../../../marivo/semantic/authoring.py), [validator.py](../../../marivo/semantic/validator.py) |
| Project assembly and materialization | [loader.py](../../../marivo/semantic/loader.py), [reader.py](../../../marivo/semantic/reader.py), [materializer.py](../../../marivo/semantic/materializer.py) |
| Catalog, validation and snapshots | [catalog.py](../../../marivo/semantic/catalog.py), [readiness.py](../../../marivo/semantic/readiness.py), [source_health.py](../../../marivo/semantic/source_health.py) |
