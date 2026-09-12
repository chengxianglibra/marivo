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

## State distinguishes computation from inspection

Source constructors and Dataset methods return Logical Datasets silently. Their
repr identifies kind, shape and definition and points to `execute()`. Construction
and `contract()` do not read source rows. Explicit execution returns the paired
Materialized Dataset, whose repr points to `show()` for bounded retained inspection.

```python
logical = session.observe(revenue, time_scope=mv.time_scope(
    start="2026-06-01", end="2026-06-08"
)).aggregate()
result = logical.execute()
result.show()
```

This example requires a Session and a governed Metric. It creates no implicit
intermediate execution while constructing the definition. A Materialized Dataset
provides guarded `show()` and complete `to_pandas()` reads, with separate row/byte
limits. A preview limit is not a complete-input execution budget.

Public terminal read values have a bounded single-line repr and bounded explicit
inspection. Authoring and runtime value cards expose render/show according to
their native type contract. Dataset states have deliberately different methods:
a Logical Dataset is not a terminal table and a Materialized Dataset is not a
mutable dataframe. Do not add a generic three-method adapter that erases this
state distinction.

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
