# Analysis Architecture

Status: AnalysisFrame cutover contract, 2026-10-19. Implementation, local
acceptance, provider Runtime qualification and release gates are separate.

## Read the owning contract

| Document | Owns |
| --- | --- |
| [AnalysisFrame](query-result-reuse.md) | Final data, coordinate, carrier and source-boundary contract |
| [Operators and Frames](operators-and-frames.md) | Nullable values, complete keys, contribution occurrences and current-row operator meaning |
| [Core Algebra](algebra-theory.md) | Mathematical definitions and conditional laws, with separate current DSL and optimizer coverage |
| [Session State and Runtime](session-state-and-runtime.md) | Evaluation, publishing Runs, stage scheduling, resource ownership, Store 15 and reading |
| [Analysis Evidence Access](evidence-access-surface.md) | Saved source facts, current evaluation, Findings and their interpretation limits |
| [AnalysisFrame cutover](analysis-frame-cutover.md) | Breaking API/value-name changes, older Store refusal and explicit source reconstruction |

Datasource and semantic work starts at [the semantic overview](../semantic/overview.md).
Cross-layer time follows [Temporal Semantics](../temporal-semantics.md).
Static API facts, captured instance state and errors follow
[Agent-Facing Public Surface](../agent-friendly-public-surface.md).

Design proposals, implementation plans and progress records, and test evidence
are kept in the local-only `docs/design/`, `docs/implementation/`, and
`docs/testing/` directories. They are not part of the tracked architecture
reference.

## Public construction and acquisition

Use `mv.*` for explicit descriptions and composition, `session.*` to introduce
Catalog, Store, execution ownership or the frozen default timezone, and receiver
methods/properties for existing context. `mv.session.create/resume/recent/inspect`
establish or discover lifecycle context. Public types support annotations and
inspection; they do not authorize direct construction or extension.
`mv.attribute_endpoint(metrics, during=None, where=None, memberships=())`
captures immutable endpoint inputs; `session.attribute` owns binding and eligibility.

## Execution and output boundary

```text
datasource: qualified Ibis execution returning pandas
    -> semantic: immutable definitions and explicit domain dependencies
    -> analysis: Population / AnalysisFrame / Column / exact FrameAxis handles
    -> compiler: explicit source and fixed inputs, complete checks and one schedule
    -> compute: complete immutable memory data, frozen coordinates and one audit
    -> persist: publish that existing result through the Store 15 transaction
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
Frame axes retain data types, roles and time interpretation without original
Entity/Field nominal bindings. Explicit ordinary-axis correspondence and bridge
targets declare caller business intent; validation does not prove original
semantic identity. Current-row sum/mean retain units without certifying Metric additivity.
Static Help owns operation discovery; `.show()` owns captured facts and errors own repairs.

All Frames use Store15, `marivo.frame_output/v4`, `frame.v4` and `frame-output-v4`.
Cards/reopen read bounded descriptors; `to_pandas()` reads current data only;
`method_facts` reads complete owned explanation payloads. Stored compute and
evaluated derivatives consume both. Saved derivatives remain self-contained.
Contribution requires the qualified physical DuckDB table route; default
investigation has its own valid observation boundary and no contribution proof.
