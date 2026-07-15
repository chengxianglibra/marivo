# Semantic and Datasource Layer — Design Overview

Status: draft design. This is the entry point for the design of Marivo's
datasource and semantic layers (`marivo.datasource` and `marivo.semantic`). It
states the design goals, the layered architecture, and the principles that the
per-topic documents below elaborate. Read it first, then follow the topic that
matches your task.

## This directory

| Document | Owns |
|---|---|
| [overview.md](overview.md) | Design goals, architecture, and principles (this file). |
| [datasource-layer.md](datasource-layer.md) | `marivo.datasource` — connections, typed specs, file sources, secrets, discovery/evidence. |
| [semantic-object-model.md](semantic-object-model.md) | `marivo.semantic` object contracts — domain, entity, dimension, time dimension, measure, metric, derived/cumulative metrics, relationship, provenance, `ai_context`. |
| [authoring-workflow.md](authoring-workflow.md) | The single evidence-snapshot write loop from help and inspection through scoped preview and readiness. |
| [loading-validation-introspection.md](loading-validation-introspection.md) | The runtime — loader/registry, catalog reader, result contract, materialization, multi-stage validation, readiness/richness. |

For the cross-module (datasource + semantic + analysis) result and guidance
protocol, see [`../agent-friendly-public-surface.md`](../agent-friendly-public-surface.md).
For the analysis layer, see [`../analysis/python-analysis-design.md`](../analysis/python-analysis-design.md).

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
        ↓ DatasourceRef + TableSource + DiscoverySnapshot
marivo.semantic     domain / entity / dimension / metric / relationship
        ↓ Ibis materialization + typed semantic refs
marivo.analysis     observe / compare / attribute / correlate / ...
        ↓ typed frames + session persistence + lineage
```

- The **datasource** layer owns *how to reach the data and what it physically
  looks like* — and nothing about business meaning. A datasource is the execution
  source of an entity, never the caliber of a metric.
- The **semantic** layer owns *what each business object is and how it
  materializes*. It produces Ibis expressions and typed refs, not frames.
- The **analysis** layer owns *what to do with those objects*. It reads through
  refs and never re-defines a caliber, guesses an entity/time dimension, or reads
  a table behind the registry.

If an analysis needs a new business object, extend the semantic layer first, then
let analysis consume it — business definitions never hide in one-off scripts.

## Guidance layering

Authoring guidance is split so each surface has one job (elaborated in
[authoring-workflow.md](authoring-workflow.md)):

- **`ms.help` / `md.help` — static contract.** Constructors, required and
  optional parameters, allowed values, defaults, omit rules, and static
  constraints. Help says *what must be settled*; it carries no runtime data.
- **`md.inspect` and snapshots — runtime evidence.** Metadata inspection precedes
  one explicit-scope sample; entity, dimension, value, time, measure, and
  relationship projections reuse that immutable snapshot without queries.
- **Catalog verification, preview, and readiness — validation.** Static
  verification executes no query, preview requires `using=` evidence scope, and
  readiness consumes fresh checks without querying.

The `marivo-semantic` skill owns workflow and routing only:

```text
help/browse -> inspect -> explicit scope -> sample once -> project evidence -> settle/grill -> author one Python object -> load typed object -> static verify -> scoped preview -> readiness -> analysis
```

It does not duplicate parameter tables from `ms.help` or `md.help`. Uncommon
formats and semantic judgments remain agent-owned.

## Ownership

The live `ms.help(...)` / `md.help(...)` surfaces own the static contracts;
the registry behind them owns mechanical continuation facts but is not itself a
public API; the `marivo-semantic` skill owns workflow and routing only; the
runtime has no canonical link to packaged skill files, so skill content is never
read or executed by the library. This mirrors the ownership split stated in
`agent-guide.md`.

| Concern | Canonical owner |
|---|---|
| Datasource constructors, connections, scope, effects, snapshots, evidence | `marivo.datasource` (`md`) |
| Semantic constructors, typed refs, dependencies, verification, preview | `marivo.semantic` (`ms`) |
| Readiness and analysis-ready refs | `ReadinessReport` |
| Mechanically available calls, effects, and transition facts | private authoring-state registry (not a public API) |
| Current failed-operation repair | typed error/result repair object |
| Ordered routing discipline and handoff policy | `marivo-semantic` skill |
| Evidence interpretation and technical drafting | agent |
| Unresolved business meaning and caliber acceptance | user or business owner |

The private authoring-state registry is descriptive, not executable orchestration:
it states which calls are mechanically available from current typed state and what
they require, but it cannot choose which object to author, acquire data on the
agent's behalf, or advance to readiness automatically. It is not a third public
`marivo.authoring` module and is not exposed for user mutation.

The typed semantic-to-analysis handoff is a module-internal result field —
`ReadinessReport.analysis_handoff: SemanticToAnalysisHandoff | None` — not a
top-level constructor or a public `__all__` entry. Analysis consumes the
handed-off refs only after `Session.validate_semantic_handoff(...)` returns a
`SemanticHandoffReceipt`. The handoff and receipt values are module-internal
handoff types consumed from result/error fields; agents never construct or
import them as an authoring API.

## Relationship to prior schema designs

Earlier (removed) schema designs are a semantic reference for object boundaries —
domain, entity, dimension, relationship, metric, time granularity, AI context,
and decomposition — but they are not a compatibility target. Python is the source
of truth for this stack; there is no promised round-trip to a legacy YAML/JSON or
metadata store, and the Python API is free to make different ergonomic choices for
agents.
