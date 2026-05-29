# marivo-semantic readiness reference

Phase 0 readiness is an agent-authored closeout based on evidence collected
with APIs that exist today. Target `project.readiness(...)` does not exist yet.

## Blockers

Do not hand refs to `marivo-analysis` when any blocker remains:

- project load or reload failed
- datasource required for validation is unreachable
- new dataset lacks raw preview evidence
- required comments, knowledge, or user confirmation are missing
- time field preview or cast failed
- metric materialization or compilation failed
- metric source SQL parity is drifted
- metric is unverified in a strict workflow
- relationship join keys are unconfirmed
- metric spans multiple datasources in a workflow without federation support
- metric body requires raw SQL to express the business logic

## Warnings

Warnings may allow analysis handoff when the user accepts the residual risk:

- metric is explicitly `declared_status="python_native"`
- preview sample is small but materialization succeeds
- primary key uniqueness was not sampled
- string refs resolve but are refactor-fragile
- comments are missing but source SQL, knowledge, and user confirmation are sufficient

## Closeout format

Use this shape after authoring:

```text
Semantic readiness: ready_with_warnings

Analysis-ready refs:
- sales.revenue
- sales.orders_count

Warnings:
- sales.aov is python_native; no source SQL parity oracle.
- sales.orders primary_key was declared but uniqueness was not sampled.

Blocked refs:
- none

Evidence used:
- datasource warehouse tested
- orders schema/comments fetched
- orders raw preview completed
- revenue source SQL parity passed
```

Use `blocked` when any blocker exists, `ready_with_warnings` when only warnings
remain, and `ready` when there are no blockers or warnings.

## Parity status rules

- `drifted` blocks readiness.
- `unverified` blocks strict readiness and is otherwise a warning.
- `python_native` is visible but does not block by itself.
- derived metrics inherit the weakest component status.
