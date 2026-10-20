# Analysis Architecture

Status: AnalysisFrame cutover contract, 2026-10-19. Implementation, local
acceptance, provider Runtime qualification and release gates are separate.
See the implementation progress record for the accepted source and remaining work.

## Read the owning contract

| Document | Owns |
| --- | --- |
| [AnalysisFrame](query-result-reuse.md) | Final data, coordinate, carrier and source-boundary contract |
| [Python Analysis Design](python-analysis-design.md) | Population, AnalysisFrame, FrameAxis, Column, audit Artifact and binding/compiler boundaries |
| [Operators and Frames](operators-and-frames.md) | Nullable values, complete keys, contribution occurrences and current-row operator meaning |
| [Session State and Runtime](session-state-and-runtime.md) | Evaluation, publishing Runs, stage scheduling, resource ownership, Store 14 and reading |
| [Timezones and Calendars](timezone-and-calendar-design.md) | Exact time authorities, windows, frozen calendar directories and paired coordinates |
| [Analysis Evidence Access](evidence-access-surface.md) | Saved source facts, current evaluation, Findings and their interpretation limits |
| [AnalysisFrame cutover](analysis-frame-cutover.md) | Breaking API/value-name changes, older Store refusal and explicit source reconstruction |

Datasource and semantic work starts at [the semantic overview](../semantic/overview.md).
Cross-layer time follows [Temporal Semantics](../temporal-semantics.md).
Static API facts, captured instance state and errors follow
[Agent-Facing Public Surface](../agent-friendly-public-surface.md).

## Complete design and implementation sources

| Document | Owns |
| --- | --- |
| [Lazy Analysis DSL](lazy-analysis-dsl-redesign.md) | Source inputs, Frame composition and lifecycle |
| [Semantic Expressions](../semantic/expression-model-redesign.md) | Controlled native Deferred declarations and operator-local policies |
| [Execution and Handoff](lazy-analysis-dsl-execution.md) | Check timing, one schedule, complete data, publication and pandas layout |
| [Implementation Architecture](analysis-implementation-architecture.md) | Module responsibilities and dependency boundaries |
| [Examples](lazy-analysis-dsl-examples.md) | Multi-round analysis and explicit source follow-up |
| [Scenario Validation](lazy-analysis-dsl-scenario-validation.md) | Independent numerical witnesses and current acceptance criteria |
| [Algebra Theory](algebra-theory.md) | Definitions, laws and information requirements |
| [Algebra Methods](algebra-methods.md) | Separate model-method proposals beyond the current surface |
| [Implementation plan](analysis-frame-implementation-plan.md) | Historical S0-S8 task authority; not implementation evidence |

[Attribute design](attribute-design.md) owns the frozen explicit-source contract;
`session.attribute` binds two `AttributeEndpoint` inputs for investigation and
optional qualified contribution. The [Attribute implementation progress](attribute-implementation-progress.md)
records accepted execution/storage and public-disclosure status. Completed Frames
retain inert `method_facts`, never original Metric authority or a producer graph.

[Period comparison](period-comparison-design.md) defines named Frame comparison
with `periods.shift(mv.offset(...))`, ISO-week year correspondence, exact
same-progress scopes and explicit missing-baseline semantics. Its
[implementation plan](period-comparison-implementation-plan.md) records staged
implementation, independent review and the exact accepted evidence; documentation
does not imply package, provider Runtime or release acceptance.
[Correlation statistics](correlation-statistics-design.md) defines current-row
Pearson/Spearman through AnalysisFrame.correlate with fixed coefficient/n_pairs
outputs, retaining corr/pair_count composition through reduce; its candidate and
acceptance evidence are tracked in the [implementation plan](correlation-statistics-implementation-plan.md).
[The Population refactor design and plan](population-refactor-design-and-plan.md)
records unified literal domains, consumer-owned membership rules and general
tuple counts; the [implementation record](population-refactor-implementation-progress.md)
tracks the current candidate and its validation separately.
A completed Frame does not regain original Metric contributions or its producer graph.
Historical acceptance records remain evidence only for their exact recorded scope.

## Execution and output boundary

```text
datasource: qualified Ibis execution returning pandas
    -> semantic: immutable definitions and explicit domain dependencies
    -> analysis: Population / AnalysisFrame / Column / exact FrameAxis handles
    -> compiler: explicit source and fixed inputs, complete checks and one schedule
    -> compute: complete immutable memory data, frozen coordinates and one audit
    -> persist: publish that existing result through the Store 14 transaction
    -> persisted Frame: source-free data operations and isolated pandas export
    -> frame.artifact: submission, Findings and Evidence audit
```

`session.observe` binds Metrics; `attribute` binds explicit endpoints and new
classifications; `read` binds nonempty fields to one Entity
identity domain; `values` declares literal rows; `frame` binds a general Population
key domain. All return AnalysisFrame. Construction and `.show()` do not evaluate.
Use the returned `compute()` or `persist()` Frame to fix values and keys. Reopen
with an exact Session and `session.persisted_frame(frame.artifact.id)`.
New source questions require explicit source inputs; an ordinary-axis
`session.population_from(frame, on=...)` bridge can constrain that new question.
Static Help owns operation discovery; `.show()` owns captured facts and errors own repairs.

All Frames use Store14, `marivo.frame_output/v3`, `frame.v3` and `frame-output-v3`.
Cards/reopen read bounded descriptors; `to_pandas()` reads current data only;
`method_facts` reads complete owned explanation payloads. Stored compute and
evaluated derivatives consume both. Saved derivatives remain self-contained.
Contribution requires the qualified physical DuckDB table route; default
investigation has its own valid observation boundary and no contribution proof.
