# Marivo's Agent-Facing Public Surface

Marivo is consumed through a write-run-read loop. The agent chooses the business
question, evaluates evidence and owns conclusions. The library owns deterministic
meaning, typed computation, bounded disclosure, exact ownership and concrete repair.

## One public coordinator

Import `marivo` for Help, `marivo.datasource as md`, `marivo.semantic as ms`, and
`marivo.analysis as mv`. `marivo.help()` introduces the concepts and routes to
`marivo.help("authoring")` or `marivo.help("analysis")`. Focused targets are
progressively discovered beneath those roots. Optional ontology capabilities
belong to their independent extension contract.

The analysis root has bounded entry, methods, inputs, artifacts, evidence and
runtime hubs. Native descriptors own callable bindings, signatures, output
families, examples, effects, errors and navigation. Public exports are pinned by
independent snapshots, and every focused target resolves through the coordinator.
There is no renderer-owned shadow inventory or compatibility execution alias.

Discovery membership is distinct from exact resolvability. Each discovery member
has one primary group; contextual prerequisite/result links may reference that
same canonical leaf without creating aliases or duplicate membership. Task groups
have stable descriptive names, not pages partitioned by descriptor position.
Types and receiver members remain focused contracts; the explicit Dataset model
topics provide its required type navigation without mixing all types into inputs
or evidence discovery. Roots explain responsibilities, states and route choice.

Exact examples declare only their actual external inputs and bind public helpers
through standard imports. Session bootstrap needs no prior Session or Artifact.
Catalog examples reuse the question's existing Session. Callable output types and
input prerequisites link to their native owning contracts; a returned value's
methods can be passed directly to the public Help coordinator.

For cold discovery, choose one task route. Once an analysis object exists, go
from its contract directly to the selected exact Help target or pass its bound
method to `marivo.help`; do not repeat the static method directory. Current-row
statistics are available directly under `analysis.methods.metric.summary` from
`analysis.methods`. They create a new quantity; original Metric rollup merges
retained original state.

Callable parameter semantics and minimal examples belong to the operation's
own documentation. Reflection checks the binding and signature. Native
`discovery_family` declarations group equivalent receiver variants under one
summary while retaining every exact route; different meanings remain separate.
Type producers name bounded real acquisition paths, not a generic entry point
or an exhaustive method inventory. Signature parameters and the return contract
are displayed once, followed by one example and its prerequisite/result links.

Context acceptance measures complete fixed public journeys, including repeated
Help reads, contracts, retained previews and errors. Character counts and page
counts are reproducible proxies, not token counts or measured Agent efficiency.
The eight-journey regression retains a pre-change SHA and output-size baseline;
its cumulative normalized characters must fall at least 15%, no journey may
grow more than 10%, and page counts must not increase. Only environment paths
and explicitly known generated identities are normalized. Single-page budgets
remain independent hard limits; complete contracts and repairs are not truncated
for this optimization.

Dataset contracts join admitted consumer identities to their native callable
descriptors and disclose the public call plus canonical Help target. Shape and
retained-state admission remain Dataset-owned; Help does not create another
continuation registry. Omitted continuations disclose family-level navigation.
Unknown-target suggestions preserve relevance and qualification. Error instances
retain concrete facts even when an optional repair is absent.


## State distinguishes computation from inspection

Source constructors and Dataset methods return Logical Datasets silently. Their
repr identifies kind, shape and definition and points to `execute()`. Construction
and `contract()` do not read source rows. Explicit execution returns the paired
Materialized Dataset, whose repr points to `show()` for bounded retained inspection.

```python
members = session.members(entity_ref)
logical = members.observe(revenue, during=mv.time_scope(
    start="2026-06-01", end="2026-06-08"
), via=relationship_ref).rollup()
result = logical.execute()
result.show()
```

This example requires a Session, an Entity Ref, a governed Metric, and its relationship to the member Entity. It creates no implicit
intermediate execution while constructing the definition. A Materialized Dataset
provides guarded `show()` and complete `to_pandas()` reads, with separate row/byte
limits. A preview limit is not a complete-input execution budget.

Public terminal read values have a bounded single-line repr and bounded explicit
inspection. Authoring and runtime value cards expose render/show according to
their native type contract. Dataset states have deliberately different methods:
a Logical Dataset is not a terminal table and a Materialized Dataset is not a
mutable dataframe. Do not add a generic three-method adapter that erases this
state distinction.

## Business data display

Analysis materialized values and terminal tables, `PreviewResult`, and
`RawSqlResult` use `show(*, n: int | None = None, max_output_bytes: int | None = 8192)`.
Existing preview/raw-SQL `render()` methods accept the same controls and return
text without a newline; analysis does not acquire a public `render()` method.
The UTF-8 budget includes the newline printed by `show()`.

There is no default row cap. `n` is a nonnegative integer (not bool), or `None`;
zero displays metadata and columns only. `max_output_bytes` is a positive integer
(not bool), or `None` for unlimited output, still subject to `n`. These controls
never change execution, stored rows, or source query limits.

Cards order identity and row counts, interpretation facts, data, interpretation
boundaries and omissions, then state-dependent read hints. Required metadata is
reserved before fitting whole rows. Display counts distinguish retained/returned
rows from full-source cardinality, query truncation and business coverage. Omitted
rows have exact counts, row/byte reasons and an applicable recovery call. Wide
rows are omitted whole; cells and columns are never silently shortened. If even
mandatory metadata and omission detail cannot fit, a `ValueError` reports the
minimum budget. Data containing line breaks or separators is escaped losslessly.

Cell state counts cover the complete current result, not the displayed prefix.
Defined values retain precision; analysis Duration cells use exact integer ticks
with their unit. Non-Defined Cells retain distinct tags and reasons. Entity
identities remain redacted even with unlimited output. Interpretation facts come
from captured definitions and retained data, without reading current business
sources. Available saved Findings have a count and the receiver's actual read
entry; full operation directories remain in `contract()` and focused Help.
Non-data cards and bounded page protocols retain their existing purposes.

## Keep each guidance fact with its owner

| Owner | Facts |
| --- | --- |
| Live Help | Static API, examples, parameters and progressive navigation |
| Dataset contract | Current schema, roles and mechanically valid continuations |
| Retained result card | Bounded current state, quality and exact read continuations |
| Structured error | Expected input, received facts and concrete repair |
| Packaged skill | Workflow boundaries, analytical judgment and handoff |

Errors subclass the owning structured hierarchy. Repair suggestions derive from
actual current state, such as loaded catalog choices or owned fields. They do not
invent names, select a different business meaning, silently widen a scope, or
retry on another execution/storage target.

Bounded pages retain immutable items, limits, has_more and opaque cursors. Empty
results, unknown authority, unavailable records and corrupt stores remain distinct.
A bounded card's omission does not authorize dropping rows from computation.
No automatic truncation, sampling or approximate method substitutes for exact
input admission.

## Authoring workflow

Discover the physical source through datasource-owned schema and health surfaces.
Author a coherent semantic slice using typed declarations and restricted Ibis
expressions. Validate the project and inspect exact catalog entries; perform
runtime preview only for a concrete source/type/readiness question. Stable Entity
identity is separate from historical row coordinates.

Missing business meaning requires a decision from its owner. Missing reusable
semantic definitions go to the semantic authoring workflow; analysis does not
repair them by guessing from labels. Credentials use environment references and
must not enter project-local persistent analysis state.

## Analysis workflow

Use one Session for the investigation. Construct explicit membership, observation
windows and dimensions. Read a committed checkpoint when its facts can guide the
next decision. Use exact artifact/field identities for later work; foreign Session
inputs fail. Cold recovery reads committed authority and values without executing
origin recipes or choosing a current datasource.

Check mechanical legality through the object contract, then decide whether that
legal operation answers the question. Algebraic contribution is not cause;
association is not intervention evidence; Candidate scores are investigative
leads; forecasts retain model assumptions. A typed digest never stores the agent's
narrative conclusion as factual truth.

Terminal custom work through `to_pandas()` or `md.raw_sql(...)` remains outside
governed Dataset composition. The packaged semantic and analysis skills own this
handoff and the questions that require renewed business meaning.

## Change together and verify independently

A changed export, signature, Help route, state method or repair is one disclosure
contract change. Update the implementation, native registry, reachability/budget
checks, executable examples, skills and current EN/ZH documentation together.
Keep one canonical path per capability, concrete public types, immutable results,
and no legacy migration or alias unless its owning contract explicitly requires it.

See [analysis design](analysis/python-analysis-design.md),
[semantic overview](semantic/overview.md), and the packaged workflow skills for
their respective contracts.
