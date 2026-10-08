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

`marivo.help("authoring")` selects the owning surface; focused Help owns
constructors, effects, constraints, and examples. `.show()` and structured
errors own result detail and repair. Project Python evaluated by `ms.load()` is
the semantic source of truth. The agent interprets evidence and drafts Python;
current business authority establishes reusable meaning.

## Task boundary and exit

Use the host-selected interpreter and start from current project state. Select
the task's exit before acquiring evidence or changing definitions:

- Explain an existing definition: load and read the current catalog. Use the
  entry card for key facts, details for structured expansion, and focused Help
  for its usage contract. Readiness and preview are unnecessary for explanation.
- Set up or repair a datasource: reuse a suitable definition or register the
  required one, validate the requested connection, and stop at that outcome.
- Add or repair reusable semantics: identify the exact roots the parent task
  needs and author the smallest dependency-coherent slice that supplies them.
  Select checks according to the delivery goal and remaining risk.

Keep question-specific calculations and presentation in analysis. Return to
semantic authoring only when another reusable gap prevents the parent task.

## Reuse and business authority

Inspect current identities and definitions before mutation. Reuse matching refs,
repair the smallest conflict, and author only genuine gaps. Keep grain, identity,
time, units, additivity, cardinality, and guardrails explicit when they affect
interpretation.

Use `marivo.help("semantic.objects")` to select the object kind, then follow its
object page for the stable decision checklist, legal construction modes, and
evidence limitations. Use `marivo.help("semantic.builders")` when a constructor
needs a nested value or typed handle, and `marivo.help("semantic.checks")` when
the unresolved question is what a particular inspection or check proves.

Author dependencies before their consumers and build derived objects only from
governed dependencies. Never hide a guessed join or reusable business choice in
a downstream calculation. Names, timestamp-like columns, key candidates,
samples, and familiar formulas are evidence to evaluate, not semantic authority.

Before the first typed analysis use of a new or changed definition, every
material unresolved choice needs one current, non-conflicting authority:

1. the user's explicit request or answer in the current task;
2. an approved existing project definition;
3. attributable, sufficiently explicit project documentation or provenance.

When authority already establishes the meaning, proceed without asking for
redundant confirmation.
Otherwise name the earliest material choice, summarize the evidence and its
limit, ask one question, and stop before typed analysis handoff for the affected
branch. Continue independent authorized work. Do not create approval tokens or
batch unrelated business questions.

## Evidence and validation choices

Prefer physical metadata when it answers the modeling question. Acquire bounded
rows or profiles only for a concrete unresolved question. Governed `md.raw_sql`
is available for source-specific questions inspection cannot answer; its rows
remain terminal evidence. Every user-data read needs explicit positive row and
timeout budgets; a returned-row limit is not a scan bound. Reuse evidence only
when its source, schema, and scope identity still answer the current question.
Record unknown or conflicting facts instead of guessing.

A coherent slice is the smallest dependency set that can load and be reviewed
meaningfully: an entity with required fields and base metrics, a relationship
with participating fields, or a derived object with new governed dependencies.
It is not a one-object checkpoint loop.

Author the whole slice in Python, then run one `ms.load()`. Repair its structural
failures together, reload, and call `catalog.require(ref)` for every authored
root. Do not add a separate verification checkpoint.

Run scoped readiness when the delivery requires analysis-ready roots or before
handing new or changed definitions to analysis. Choose preview only for a
concrete runtime risk or a current repair, and source health when current source
or data drift matters. Focused check Help owns what each check proves; the
returned report owns the actual scope, findings, and repair. Read those facts
without treating one successful check as another check's evidence or as business
authority. Do not repeat completed checks while their identity and scope remain
applicable.

## Delivery and return to the parent task

Leave authoring when its selected exit is satisfied. For analysis handoff, pass
the exact ready refs or `analysis_ready_inputs` to `marivo-analysis` and continue
the original question. Kind-level Help routes identify candidate consumers;
their focused contracts own companion inputs, and returned analysis artifacts
own state-specific continuations.

For authoring-only work, report the slice, evidence and scope, material business
authority, checks performed, their outcomes, exact roots, and remaining risks.
Identify roots as ready only when readiness established that result. Disclose
data reads or source changes. If blocked, name the affected objects, all known
material blockers, and the smallest next action; do not hide blockers to fit
a fixed template.

## Hard boundaries

- Do not bypass Marivo safety with direct Ibis, DuckDB, pandas, backend clients,
  or ad hoc SQL.
- Caller-stated read-count, row, and timeout budgets override retries.
- Author credentials as references, never plaintext project source.
- Never substitute a physical column, guessed join, neighboring metric, or
  silent fallback for missing governed meaning.
- Follow focused help and structured repair; stop the affected branch when the
  public contract cannot produce the required definition or evidence.
