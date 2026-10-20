# Marivo's Agent-Facing Public Surface

Marivo is consumed through a write-run-read loop. The agent chooses the business
question, evaluates evidence and owns conclusions. The library owns deterministic
meaning, typed computation, bounded disclosure, exact ownership and concrete repair.

## One public coordinator

CLI `python -m marivo help [target]` and Python `marivo.help(target)` share
one native static Help registry. CLI pages render canonical string links as
copyable `marivo help <target>` commands; Python pages retain `marivo.help(...)`.
The facts and target identities are the same. Root Help routes to authoring or analysis.
Targets are exact lowercase snake_case semantic addresses, independent of Python
symbols. Pages disclose Target and Kind: topic, object, callable, attribute,
expression or error. Qualified strings, public callables and public types are
accepted; instance facts are read through their owning objects, not Help.

Task navigation and type/member indexes link to the same canonical leaves.
`semantic.objects` selects an object kind; `semantic.metric` describes its
meaning and operations; `semantic.metric.define` describes `ms.metric`.
`semantic.metric.where` is explicitly supported Deferred syntax, not a Python
class inferred by reflection. Native role pages disclose legal input acquisition.

Public fields and properties have independent attribute targets such as
`analysis.query.output_names`. Query them by string without executing a getter;
read the value directly in the original Python context. Type pages link every
supported member. All public capabilities are reachable without guessing paths.
Variant-only attributes state their public availability condition: for example,
`analysis.grain.calendar` applies when `grain.kind == 'semantic'`. Type and
attribute pages reuse the owning variant facts and use public receiver names.
Private analysis error subclasses reuse their nearest registered error contract;
the advertised target remains queryable by string and through the CLI.

Reflection owns installed signatures and supported members. Owning docstrings
and domain rules own semantics, constraints, effects and examples. Native
registries assemble these facts and explicit task memberships; renderers maintain
no shadow inventories. Public export snapshots and behavior-owned checks are
independent of render output. No old public target aliases are retained.

For an existing object, read show() only when its captured facts are needed;
read the exact operation Help when syntax or constraints are unknown. Do not
restart root discovery for a known target. Structured errors own the actual
expected/received facts and repairs derived from real state. Related routes are
optional, not a reading checklist. Object pages share repeated authority facts
and list each route once; callable pages retain exact constraints and examples.
Catalog lookup candidates are ranked within the requested kind before bounding,
and expression-stage repairs link directly to the owning grammar. Callable pages
link exact registered result types. They do not infer consumer lists from broad
type-family equality: Deferred expressions, for example, have distinct semantic
stages. Explicit related routes and type pages own operation discovery.

Complete public journeys count all Help, object displays, saved previews and
errors, including repeated reads. Character and page budgets are proxies, not
model token counts or measured agent efficiency. Retain historical baselines;
never rewrite them to manufacture reductions. Full task correctness, failed
lookups and repairs are assessed alongside text size.

## State distinguishes computation from inspection

Population and Query construction is silent. Their bounded repr identifies the
logical value and its disclosure entry. Construction and metadata `.show()` do not read
source rows. Explicit Query execution returns a terminal Artifact whose repr
points to `show()` for bounded saved-output inspection.

```python
members = session.members(entity_ref)
query = session.observe(
    {"revenue": revenue}, members=members,
    during=session.time_scope(start="2026-06-01", end="2026-06-08"),
)
result = query.execute()
result.show()
```

This example requires an explicit Session, an Entity Ref, and a resolved Metric
with a qualified relationship to the member Entity. It creates no intermediate
execution while constructing the definition. The terminal Artifact provides
bounded `show()` and complete isolated `to_pandas()` reads. A preview limit is not
a complete-input execution budget.

Public terminal read values have a bounded single-line repr and bounded explicit
inspection. Authoring and runtime value cards expose render/show according to
their native type contract. Population/Query display captured logical definitions;
Artifact provides terminal saved-output inspection and cannot re-enter the DSL.
Do not add a generic adapter that erases this distinction.

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
entry; full operation directories remain in static type Help.
Non-data cards and bounded page protocols retain their existing purposes.

## Keep each guidance fact with its owner

| Owner | Facts |
| --- | --- |
| Live Help | Static API, examples, parameters and progressive navigation |
| Logical object show / typed fields | Captured scope, coordinates, outputs and ownership |
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

Read captured facts through the object and operation constraints through Help;
actual calls validate compatibility. Decide whether the operation answers the question. Algebraic contribution is not cause;
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

See [analysis design](../design/analysis/python-analysis-design.md),
[semantic overview](semantic/overview.md), and the packaged workflow skills for
their respective contracts.

Unknown member Help targets preserve the registered receiver in repairs. A
real public method points to its canonical callable owner, including shared
display methods; other member guesses point to the receiver page. Close member
typos retain same-receiver suggestions before unrelated same-name methods.
