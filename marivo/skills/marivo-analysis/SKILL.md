---
name: marivo-analysis
description: Use for governed metric, Event Journey, Lifecycle, quality, evidence-aware, or resumed Marivo analysis.
---

# marivo-analysis

## Trigger

Use this skill for analysis over Marivo metrics, typed Events, StateModels, or
persisted analysis artifacts. Do not trigger for generic SQL, pandas, or
reporting work that does not use Marivo.

## Mission and authority

This skill is a boundary kernel, not an API manual or analysis recipe. The
verified installed Marivo environment owns the live contract:

- `marivo.help("analysis.<target>")` owns static signatures, constraints,
  examples, and error contracts;
- semantic object details own governed business meaning;
- `.show()` owns bounded current state;
- `.contract()` owns mechanically valid next actions;
- structured errors own repair guidance.

Never replace those sources with cached skill knowledge.

## Environment entry

Use one interpreter for discovery and execution. Run
`<analysis-python> -m marivo help` and verify its Marivo version, Python
executable, and package path. A bare `marivo` from `PATH` is authoritative only
when its fingerprint matches the execution interpreter.

Then start with:

```python
import marivo
import marivo.analysis as mv

marivo.help("analysis")
session = mv.session.get_or_create(
    "investigation",
    question="<business question>",
)
```

If the intended interpreter or package cannot be verified, stop and repair the
environment rather than guessing.

## Bounded investigation loop

1. Resolve exact semantic inputs from the current catalog and readiness handoff.
2. Use focused help before the first unfamiliar capability.
3. Run a rerunnable session-local script with the verified interpreter.
4. Read the returned object with `.show()`.
5. Before an unfamiliar continuation, read `.contract()`.
6. Follow one structured repair when a call fails.
7. Stop when the minimum sufficient evidence answers the question.

Do not enumerate unrelated catalog objects, operators, help targets, source
files, or private implementation details after the answer is supported.

## Deterministic stop rule

Treat failures as the same root cause when their structured error kind, failed
capability, and rejected semantic or artifact condition are unchanged.

- After the first failure, follow its structured repair or one focused-help
  recovery.
- If the same root cause occurs twice, stop that branch.
- Report the blocker or choose an explicit terminal exit; do not inspect private
  Marivo source to invent a workaround.

Historical sessions are reference memory only. Inspect them only when resuming
work or when the same failure recurs, and inspect no more than three candidates.
Their conclusions never support current claims without current artifacts.

## Hard boundaries

### Semantic authority

Metrics, dimensions, Events, StateModels, participant roles, relationships,
units, and business definitions come from the semantic catalog. Analysis may
choose windows, policies, cohorts, seeds, and completeness declarations, but it
must not add or edit semantic definitions.

A missing or disputed business object stops the affected typed branch. Record
the gap and request approval for the smallest semantic-authoring change at
closeout.

### Typed execution

Do not read business rows directly through Ibis, DuckDB, pandas, backend
connections, or private datasource handles to bypass Marivo. Passing an Ibis
backend into `mv.session.get_or_create(...)` is allowed; querying it for the
answer is not.

Typed analysis begins only through the registered public entry:

| Question family | Entry |
| --- | --- |
| Governed metric or runtime metric expression | `marivo.help("analysis.observe")` |
| Event subject journey | `marivo.help("analysis.events.match")` |
| Normative state replay | `marivo.help("analysis.lifecycle.replay")` |

After entry, continue through the concrete artifact `.contract()` or focused
live help. Do not synthesize unregistered reducers or cross-family operations.

### Evidence integrity

Scripts, chat summaries, historical sessions, and artifact digests are not
substitutes for current artifacts. Material claims remain recoverable to the
semantic object, session/job, artifact, and analysis scope.

Computed findings and affordances are facts or compatibility information, not
business conclusions. Keep Marivo facts, agent interpretation, and hypotheses
distinct. Preserve coverage, censoring, reconciliation, and quality boundaries;
never silently strengthen a claim.

## Terminal exits and handoffs

`frame.to_pandas()` and `md.raw_sql(...)` are the only business-row terminal
exits. Their results cannot re-enter typed analysis.

- Use `frame.to_pandas()` only when the remaining work is intentionally custom
  and the typed evidence chain is already established.
- Use `md.raw_sql(...)` only when a semantic gap blocks typed work. Name the
  missing object, datasource, purpose, temporary assumptions, and loss of typed
  lineage/evidence continuity.
- A deliverable or publication request hands off to the corresponding
  independent delivery capability.
- Repository maintenance follows repository-local maintainer instructions, not
  this public skill.

When a governed runtime metric expression supports a material claim, apply
`references/runtime-metric-closeout.md`.

## Script and session discipline

Store the rerunnable script under
`<project_root>/.marivo/analysis/sessions/<session.id>/scripts/`. Repair and
rerun the same script instead of replacing it with disposable snippets.

A prior script may be consulted only when its session has a succeeded job and
its artifact remains recoverable. Never execute it directly or copy it
wholesale. Re-resolve refs, windows, policies, and scope against current state.

## Closeout

Closeout must:

- trace each material claim to current semantic inputs, artifact/job, and scope;
- disclose blockers, warnings, quality limits, omissions, and terminal exits;
- separate observed facts, interpretation, and unverified hypotheses;
- propose, but never perform, unapproved semantic changes;
- stop after the minimum sufficient evidence instead of continuing exploratory
  calls without a decision purpose.

Record Marivo product friction privately under
`<project_root>/.marivo/analysis/internal_feedback/<session.id>.md`; disclose it
to the user only when it affects validity, coverage, confidence, or completion.
Do not put absolute interpreter or package paths in user-facing deliverables.
