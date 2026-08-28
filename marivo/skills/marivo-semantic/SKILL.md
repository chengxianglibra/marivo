---
name: marivo-semantic
description: Use for Marivo datasource setup and reusable semantic authoring or repair, including semantic gaps exposed by analysis. Guides demand framing, reuse, evidence, modeling, validation, and handoff.
---

# marivo-semantic

## Purpose and ownership

Use this skill to define datasources and reusable semantic objects that Marivo
analysis can reference safely. Enter when a business question needs new or
changed governed meaning, or when `marivo-analysis` identifies a reusable gap.
Keep question-specific calculations and presentation logic in analysis.

This skill owns decision order, evidence and business-authority boundaries,
coherent checkpoint choice, validation routing, and handoff. Mechanical
contracts stay in the installed environment:

- `marivo.help("authoring")` selects the owning surface;
- `marivo.help("semantic.authoring")`, `marivo.help("semantic.objects")`,
  `marivo.help("semantic.builders")`, and `marivo.help("semantic.checks")`
  own progressive semantic routing, while exact focused help owns constructors,
  parameters, effects, constraints, and examples;
- `.show()` and structured errors own result detail and repair;
- project Python evaluated by `ms.load()` is the semantic source of truth.

The agent interprets evidence and drafts Python; current authority owns reusable
business meaning. Do not copy API inventories or error taxonomies into this
skill.

## Semantic modeling principles

- Start from reusable business demand, not available tables or columns.
- Reuse matching governed objects; repair changed shared meaning; add only a
  distinct reusable concept.
- Separate physical facts, reusable business meaning, and analysis choices.
- Make grain, identity, time, unit, additivity, relationship cardinality, and
  metric guardrails explicit when they affect interpretation.
- Author one dependency-coherent semantic slice, never an unrelated domain-wide
  rewrite.

## General construction method

### 1. Frame the reusable demand

From the parent question, identify the smallest reusable concepts required for
the answer. Separate organizational truth from question-scoped runtime metrics
and one-off presentation logic. Name the exact roots analysis will need and the
business choices that could change their meaning. Those roots define success.

### 2. Start from current project state

Use the host-selected interpreter. When environment identity matters, run
`<selected-python> -m marivo doctor` once, then route through
`<selected-python> -m marivo help`, `marivo.help("authoring")`, and
`marivo.help("semantic.authoring")`.

```python
import marivo.datasource as md
import marivo.semantic as ms

datasources = md.load()
catalog = ms.load()
```

Inspect current identities and definitions before mutation. Reuse matching refs,
repair the smallest conflict, and author only genuine gaps. For a
datasource-only request, declare, register, test the connection, and stop.

### 3. Establish necessary physical facts

Use registration, connection testing, and `md.inspect(...)` for source identity,
columns, physical types, partitions, and capabilities. Prefer metadata when it
answers the modeling question without reading user data.

Acquire a bounded sample only when rows or profiles are necessary.
`md.raw_sql(...)` is a normal governed exploration option only for a concrete
source-specific question inspection cannot answer. It remains read-only, bounded,
and terminal; its result cannot be passed to typed analysis. Every user-data read
stays within explicit positive row and timeout budgets; a returned-row limit is
not a scan bound.

Record unknown or conflicted facts instead of guessing. Reuse matching current
evidence when source, schema, and scope identity still answer the same question.

### 4. Model in dependency order

Use `marivo.help("semantic.objects")` to select the object kind, then follow its
object page for the stable decision checklist, legal construction modes, and
evidence limitations. Use `marivo.help("semantic.builders")` when a constructor
needs a nested value or typed handle, and `marivo.help("semantic.checks")` when
the unresolved question is what a particular inspection or check proves.

Author dependencies before their consumers and build derived objects only from
governed dependencies. Never hide a guessed join or reusable business choice in
a downstream calculation. Names, timestamp-like columns, key candidates,
samples, and familiar formulas are evidence to evaluate, not semantic authority.

### 5. Settle reusable business meaning

Before the first typed analysis use of a new or changed definition, every
material unresolved choice needs one current, non-conflicting authority:

1. the user's explicit request or answer in the current task;
2. an approved existing project definition;
3. attributable, sufficiently explicit project documentation or provenance.

When authority already establishes the meaning, proceed without asking for redundant confirmation.
Otherwise name the earliest material choice, summarize the evidence and its
limit, ask one question, and stop before typed analysis handoff. Do not create
approval tokens or batch unrelated business questions.

### 6. Author and validate one coherent slice

A coherent slice is the smallest dependency set that can load and be reviewed
meaningfully: an entity with required fields and base metrics, a relationship
with participating fields, or a derived object with new governed dependencies.
It is not a one-object checkpoint loop.

Author the whole slice in Python, then run one `ms.load()`. Repair its structural
failures together, reload, and call `catalog.require(ref)` for every authored
root. Do not add a separate verification checkpoint.

Validate only what the remaining risk requires:

- `ms.load()` establishes project-level static coherence;
- `catalog.require(...)` establishes exact current identity;
- scoped readiness supplies ready roots through `analysis_ready_inputs`;
- scoped preview probes a concrete runtime risk;
- `catalog.source_health(...)` checks requested current source or data drift and
  never changes readiness.

Preview and source health are conditional branches, not a mandatory authoring
ladder. Runtime evidence cannot replace static validation or business authority.

### 7. Hand off or stop

Leave authoring when the requested refs are ready. If the parent task includes
analysis, pass current refs or `analysis_ready_inputs` to `marivo-analysis` and
continue the original question. Return only for another reusable semantic gap.

For authoring-only work, report the slice, evidence and scope, material business
authority, validation outcome, ready roots, and remaining risks. If blocked,
name the exact object and single blocker, disclose data reads or source changes,
and state the one action required to continue.

## Hard boundaries

- Do not bypass Marivo safety with direct Ibis, DuckDB, pandas, backend clients,
  or ad hoc SQL.
- Caller-stated read-count, row, and timeout budgets override retries.
- Author credentials as references, never plaintext project source.
- Never substitute a physical column, guessed join, neighboring metric, or
  silent fallback for missing governed meaning.
- Follow focused help and structured repair; stop the affected branch when the
  public contract cannot produce the required definition or evidence.
