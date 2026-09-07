# Semantic and Datasource Layer — Design Overview

Status: accepted target design; amended 2026-09-07 for lazy Analysis. The amended
identity, temporal-resolution, and aggregation boundaries are not implemented
by this documentation change. Current eager APIs and live Help remain the
executable surface until the coordinated public cutover.

This is the entry point for the design of Marivo's
datasource and semantic layers (`marivo.datasource` and `marivo.semantic`). It
states the design goals, the layered architecture, and the principles that the
per-topic documents below elaborate. Read it first, then follow the topic that
matches your task.

## This directory

| Document | Owns |
|---|---|
| [overview.md](overview.md) | Design goals, architecture, and principles (this file). |
| [datasource-layer.md](datasource-layer.md) | `marivo.datasource` — connections, typed specs, file sources, secrets, discovery/evidence. |
| [semantic-object-model.md](semantic-object-model.md) | `marivo.semantic` object contracts — identity/versioning, dimensions, measures, Metric graphs and intrinsic aggregation, Relationships, Event/StateModel boundaries, provenance, `ai_context`. |
| [authoring-workflow.md](authoring-workflow.md) | Agent-native exploration, coherent-slice authoring, scoped readiness, and targeted runtime/source-health probes. |
| [loading-validation-introspection.md](loading-validation-introspection.md) | The runtime — loader/registry, catalog reader, result contract, materialization, multi-stage validation, readiness/richness. |

For the cross-module (datasource + semantic + analysis) result and guidance
protocol, see [`../agent-friendly-public-surface.md`](../agent-friendly-public-surface.md).
For the current cross-layer period-calendar, temporal-set, work-schedule,
time-scope, and alignment contract, see
[`../temporal-semantics.md`](../temporal-semantics.md).
For the analysis layer, see [`../analysis/python-analysis-design.md`](../analysis/python-analysis-design.md).
For the accepted lazy cutover, see the
[design decomposition](../../superpowers/specs/2026-09-01-lazy-analysis-design-decomposition-plan.md)
and [observation model](../../superpowers/specs/2026-09-01-lazy-analysis-observation-model-design.md).

## Audience and intent

These layers are consumed primarily by general coding agents (Claude Code, Codex)
through a write–run–read loop. The goal is not to make an agent memorize a private
DSL, but to let it maintain business semantics like an ordinary Python project:
read the existing objects, declare explicit models, express calculation caliber in
Ibis, retain SQL provenance, run validation, and hand stable semantic refs to
`marivo.analysis`.

## Design goals

`marivo.semantic` is the business-object contract of the Python-native analysis
stack. It answers "which stably referenceable business objects exist in this
project" — not "how to wrap YAML, SQL, or a runtime API into another entry point".
`marivo.datasource` is the connection and evidence layer underneath it.

The design holds to these goals:

- **Python is the source of truth.** Changing a business caliber means editing a
  Python authoring file, not a generated artifact or a runtime store.
- **Datasources are shareable project config.** They live in
  `models/datasources/*.py` and are referenced by global name; a semantic domain
  only references a datasource, it does not define one.
- **Objects are statically readable.** Entities, dimensions, time dimensions,
  measures, metrics, relationships, decompositions, and provenance all have
  explicit Python declarations.
- **Caliber is never guessed.** Business meaning is not inferred from column
  names, table names, or natural language. An agent converges through decorated
  refs, function signatures, `provenance=ms.from_sql(...)`, parity results, and
  structured errors.
- **Ownership is explicit.** Domain membership comes from an explicit `domain=`
  or an explicit default domain — never from a file path. A metric's entity comes
  from `entities=[...]`, not a parameter name. The reader binds to a project root,
  not a thread-local guess.
- **Entity identity is stable across versions.** `primary_key` identifies one
  Entity instance; `versioning` identifies its historical representations.
  Snapshot and validity row keys derive from both authorities. A physical
  partition is not semantic versioning, and no second business-key authoring
  parameter or planner-side key subtraction is needed.
- **Time has an explicit consuming role.** Semantic declarations own source
  time meaning, snapshot periods, validity intervals, and Metric status folds.
  Analysis supplies exact temporal boundaries with their fixed instant or
  immediately-before-endpoint interpretation and keeps membership selection, Metric
  observation, and output coordinates separate. An absent exact snapshot fails;
  neither implicit latest nor per-Entity last-known selection repairs it.
- **Aggregation follows the governed equation.** Metric computation roots,
  component equations, spatial-before-temporal order, fixed null/empty rules,
  units, and intrinsic state requirements have one semantic owner. Analysis
  combines these facts with its selected contributions and retained coordinates
  to admit a transformation. Additivity alone does not prove disjoint buckets,
  fold commutation, or sufficient materialized state.
- **Ibis is the only expression language.** SQL is retained as provenance and a
  parity oracle, but it is metadata, never an executable authoring body.
- **Downstream depends only on refs.** Analysis, operators, skills, and scripts
  consume stable semantic refs and materialized Ibis expressions, not a project's
  internal file layout.
- **Fail closed.** When decoration, loading, assembly, materialization, or parity
  cannot prove a contract holds, the layer emits a structured error rather than a
  best-effort guess.

The governing test: if a business object will be referenced by downstream
analysis, it must first enter the semantic layer; a rule that lives only in an
agent's prompt or a SQL draft is not yet stable semantics.

## Layered architecture

Marivo's Python-native stack is three layers with a strict, one-directional
dependency:

```text
marivo.datasource   connection + physical source + evidence
        ↓ Ref[datasource] + TableSource + DiscoverySnapshot
marivo.semantic     domain / entity / dimension / metric / relationship
        ↓ normalized business contracts + typed refs + Ibis construction
marivo.analysis     observe / compare / attribute / correlate / ...
        ↓ Logical/Materialized Datasets + persistence + lineage
```

- The **datasource** layer owns *how to reach the data and what it physically
  looks like* — and nothing about business meaning. A datasource is the execution
  source of an entity, never the caliber of a metric.
- The **semantic** layer owns *what each business object is and how it
  contributes to a governed computation*. It normalizes identity, versioning,
  Metric graphs, and intrinsic fold/state requirements and constructs Ibis
  expressions. This is not publication of an Analysis Dataset.
- The **analysis** layer owns *what to do with those objects*. It reads through
  refs and never re-defines a caliber, guesses an entity/time dimension, or reads
  a table behind the registry. It selects Population membership, observational
  units, windows, coordinates, and exact contribution subsets; logical operators
  compose before explicit execution, while a materialized Artifact authorizes
  its committed rows and retained parts without semantic-origin replay.

A Metric's computation root is distinct from the analysis Entity and reporting
grain. A multi-root graph can be semantically coherent while requiring an
explicit Population and a safe per-component mapping at the analysis boundary.
Relationship describes mapping facts; Metric-path semantics own contribution
allocation. Shared intrinsic resolvers derive these requirements instead of
adding author-set `rollup_safe`, `membership_stable`, or generic allocation
switches.

Event and StateModel retain business occurrence and normative transition
meaning. Population membership, sampling, censoring, and scoped completeness
assumptions belong to Analysis and source evidence. Neither an Event declaration,
a StateModel, nor a snapshot declaration establishes observed source completeness.

If an analysis needs a new business object, extend the semantic layer first, then
let analysis consume it — business definitions never hide in one-off scripts.

## Guidance layering

Authoring guidance is split so each surface has one job (elaborated in
[authoring-workflow.md](authoring-workflow.md)):

- **`marivo.help(...)` — public help coordinator.** `python -m marivo help`
  only confirms the active interpreter, package version, and environment
  fingerprint before handing off to Python. `marivo.help(...)` is the only
  public focused-help entry point; qualified semantic and datasource targets
  expose constructors, required and
  optional parameters, allowed values, defaults, omit rules, and static
  constraints from their native registries. `md` and `ms` execute their
  domain APIs and intentionally expose no separate `.help()` aliases. Help says
  *what must be settled*; it carries no runtime data.
- **Datasource exploration — runtime evidence.** Metadata inspection is the
  preferred schema path. Optional explicitly scoped sampling supplies generic
  bounded rows and profiles; governed raw SQL handles source-specific bounded
  scratch investigation. None of these paths decides business meaning.
- **Project load, catalog navigation, preview, source health, and readiness — validation.** One
  `ms.load()` validates a dependency-coherent authored slice. Exact refs are
  confirmed with `catalog.require(...)`; scoped preview, explicit source health,
  and readiness keep independent runtime, drift, and static contracts.

The `marivo-semantic` skill owns workflow and routing only:

```text
load current catalogs -> inspect -> optional bounded sample and/or governed raw SQL -> author one coherent semantic slice -> one ms.load() -> catalog.require(...) -> scoped readiness -> first typed analysis use
```

It does not duplicate parameter tables from `marivo.help(...)`. Uncommon
formats and semantic judgments remain agent-owned.

## Ownership

The public `marivo.help(...)` coordinator routes qualified targets to the
native semantic and datasource registries that own static contracts and
mechanical continuation facts. Those registries are not public APIs; the
`marivo-semantic` skill owns workflow and routing only; the runtime has no
canonical link to packaged skill files, so skill content is never read or
executed by the library. This mirrors the ownership split stated in
`AGENTS.md`.

| Concern | Canonical owner |
|---|---|
| Datasource constructors, connections, scope, effects, snapshots, evidence | `marivo.datasource` (`md`) |
| Semantic constructors, typed refs, dependencies, project validation, preview | `marivo.semantic` (`ms`) |
| Entity identity, version row grain, intrinsic Metric graph and fold/state requirements | semantic declarations and their shared normalized resolver |
| Membership/time choices, current coordinate/state admission, explicit Dataset execution | `marivo.analysis` (`mv`) |
| Snapshot/validity integrity and operation-required source coverage | exact runtime validation and source evidence |
| Readiness and analysis-ready inputs | `ReadinessReport` |
| Callable operations, effects, input facts, and constraints | private native registries (not public APIs) |
| Current failed-operation repair | typed error/result repair object |
| Ordered routing discipline and readiness policy | `marivo-semantic` skill |
| Evidence interpretation and technical drafting | agent |
| Unresolved business meaning and caliber acceptance | user or business owner |

The native registries describe callable operations, effects, input facts, and
constraints without constructing a shared authoring lifecycle graph. They cannot
choose a semantic slice, acquire data on the agent's behalf, or advance to
readiness automatically. There is no third public `marivo.authoring` module.

`ReadinessReport.analysis_ready_inputs` is the direct semantic-to-analysis
contract. It lists only directly requested refs or runtime expressions whose
dependency closures have no blocker; dependency refs remain diagnostic input
rather than leaking into the handoff. Blockers and warnings remain on the same
report. No additional transfer object or hidden authoring API exists between
readiness and ordinary analysis operations.

The lazy cutover is accepted only when one coherent snapshot preserves identity
without last-known substitution, validity resolution rejects overlap, independent
membership/observation periods compose, and multi-root observations preserve
per-component contributions. Numerical acceptance must additionally reject
overlapping-bucket sums and non-commuting semi-additive folds, preserve null and
empty semantics, and prove admitted retained-state folds equal direct governed
computation. Static design alignment alone does not satisfy those runtime gates.

## Relationship to prior schema designs

Earlier (removed) schema designs are a semantic reference for object boundaries —
domain, entity, dimension, relationship, metric, time granularity, AI context,
and decomposition — but they are not a compatibility target. Python is the source
of truth for this stack; there is no promised round-trip to a legacy YAML/JSON or
metadata store, and the Python API is free to make different ergonomic choices for
agents.
