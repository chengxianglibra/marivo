# Marivo Investigation Planning Reference

Use this file when the task spans **multiple dependent investigation steps** and you need a clear client-side orchestration pattern.

Skip this file if the task is a single investigation step or a semantic modeling change. For exact route bodies and parameters, use the matching tool instead.

This file owns client-side orchestration guidance. Transport and session invariants live in `http-contracts.md`. Intent behavior and read-surface rules live in `steps.md`.

## Important Boundary

Current Marivo exposes sessions, typed intents, semantic surfaces, and canonical read surfaces.

It does **not** expose a public `/plans` API.

So when a task says "build", "patch", or "rework" a multi-step Marivo plan:

- keep the plan in the client or agent
- execute the plan through typed intents
- reuse typed refs between steps
- use state and context reads to decide whether to continue or revise the plan

## What Planning Means Here

Treat planning as orchestration over these primitives:

1. discovery
2. execution
3. decision reads
4. deep explanation
5. runtime troubleshooting when needed

## Default Patterns

### Investigate A Metric Change

1. resolve a published metric
2. create a session
3. `observe` the current window
4. `observe` the baseline window
5. `compare`
6. `decompose` if the delta is worth explaining
7. read state
8. read context for the proposition that matters most

### Diagnose Anomalies

1. resolve a metric
2. create a session
3. if the abnormal current and baseline windows are known, submit `diagnose(mode="explicit_compare")`
4. otherwise submit `diagnose(mode="auto_detect")` with the right detection pattern
5. read state
6. drill into one proposition with context

Pattern choice:

- use `patterns=["point_anomaly"]` for bucket-level spikes and dips
- use `patterns=["period_shift"]` or `profile="level_shift"` for whole-window degradation
- when `auto_detect` returns `needs_attention` with `no_detect_candidates`, either switch to `explicit_compare` with known windows or widen the scan window and include `period_shift`

### Validate A Hypothesis

1. create a session
2. choose the published metric and exact left/right scopes
3. use `validate` if the pattern fits the derived contract
4. otherwise use `observe`, `observe`, then `test`
5. read state and context

### Predicate-Scoped Investigation

1. resolve the metric and applicable `predicate.*` refs
2. create a session
3. submit an `observe` with predicate qualifiers
4. `compare` or `test` with the same predicate scope
5. read state and context

## How To Patch A Plan

There is no public `PATCH /plans/{id}`.

Instead:

1. keep your own ordered investigation outline
2. replace future intended steps in that outline
3. continue submitting only the revised next typed intent
4. reuse existing artifacts via typed refs where appropriate
5. re-read state after each meaningful branch point

## Choosing Atomic Versus Derived Intents

- use atomic intents when you need tight control over the investigation path
- use derived intents when the task already fits a bounded built-in expansion
- stop using derived intents when you keep needing side branches they were not designed to carry

## Read Next

- Read `steps.md` for the actual guardrails of each investigation surface.
- Read `http-contracts.md` when the orchestration question is really about session scope or request ownership.
