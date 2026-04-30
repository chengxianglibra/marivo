# Marivo Intents And Read Surfaces Reference

Use this file when the task is about **session investigation execution**: choosing intents, chaining typed refs, reading state, reading proposition context, or troubleshooting why a grouped or bounded investigation request failed.

Skip this file if you only need the top-level routing choice from `SKILL.md`, or if the task is primarily semantic modeling rather than investigation execution.

This file owns investigation behavior and read-surface sequencing. Global HTTP/session invariants live in `http-contracts.md`. Semantic lifecycle and dependency order live in `semantic-layer.md`. For exact body shapes and examples, use the matching tool.

Boundary reminder:

- keep session ownership, termination transport, and structured time-window contract details in `http-contracts.md`
- keep semantic object design and dependency-order decisions in `semantic-layer.md`
- keep this file focused on choosing intents, sequencing reads, and interpreting investigation outcomes

## Mental Model

Marivo separates analytics work into different surfaces:

1. **Action surface**: typed session-scoped intents
2. **State surface**: session-level decision state
3. **Context surface**: proposition-level canonical closure
4. **Runtime status surfaces**: operator-facing execution progress and failure detail

Do not collapse these into one generic "run analysis and summarize it" surface.

## Investigation Flow

Use this default pattern:

1. Resolve or discover the domain and semantic metric or object you want.
2. Create a session (include `execution_identity` when engine auth requires it).
3. Start with `detect` or `observe`.
4. Read state after each meaningful branch point.
5. If one proposition matters, read context for that proposition.
6. Either submit a bounded follow-up intent or stop.
7. When you stop, explicitly terminate the session.

Practical heuristics:

- use `/semantic/domains` and `/semantic/domain-objects` when the user names a business area but not the exact typed ref
- remember that domains are discovery metadata only; they do not authorize access or prove compiler compatibility
- start with `detect` when anomaly discovery is the first task
- start with `observe` when you already know the metric and time window
- for gradual degradation with known current and baseline windows, prefer `diagnose(mode="explicit_compare")`; do not force a z-score detector to invent a candidate first
- for unknown degradation windows, use `detect` or `diagnose(mode="auto_detect")` with `patterns=["period_shift"]` or `profile="level_shift"` when whole-window level shift is the target pattern
- if you need both trend and composition, plan two `observe` steps; do not try to combine time bucketing and grouped dimensions in one request
- use derived intents only when the problem already fits their bounded expansion
- prefer atomic intents when you need tight control over branching
- use `predicate.*` refs as qualifiers in `observe`, `compare`, or `test` intents when governed filter semantics are needed
- stopping means committing lifecycle closure, not just ending the conversation or agent turn

## Session Close-out

When an investigation has reached its stopping point:

1. decide that no further intent writes are needed in the current session
2. terminate the session explicitly
3. continue with read surfaces only if you still need to inspect the final evidence

Close-out primitives:

- canonical HTTP: `POST /sessions/{session_id}/terminate`

Practical guidance:

- prefer `terminal_reason="answered"` when the investigation reached a normal conclusion
- use `terminal_reason="user_closed"` when the caller is just ending the session and no stronger terminal reason applies
- once terminated, do not submit more typed intents into that session; create a new session if the investigation needs to continue as a new write flow
- terminal sessions remain readable through session root, state, and proposition context surfaces

## Intent Families

Atomic intents:

- `observe`
- `compare`
- `decompose`
- `correlate`
- `detect`
- `test`
- `forecast`

Derived intents:

- `attribute`
- `diagnose`
- `validate`

Derived intents are fixed bounded expansions. They are not a public planner surface.

## Typed Ref Rules

Downstream intents should consume canonical upstream artifacts, not ad hoc projections.

Key rules:

- keep refs inside the owning session unless the route explicitly says otherwise
- use the correct upstream artifact type for the downstream intent
- let the path choose the intent; do not invent a second action discriminator
- if you already know the exact semantic object, prefer the canonical typed ref such as `metric.watch_time`

Typed metric rule:

- typed intent `metric` parameters must use canonical refs such as `metric.watch_time`
- do not pass bare names such as `watch_time`

Predicate ref rule:

- use `predicate.*` refs when an intent needs governed filter semantics
- predicate refs are resolved through lineage and reuse resolution for `compare` and `test` intents

## State Versus Context

Use **state** when you need:

- the session-level picture
- current assessments or blockers
- the next branch decision
- the proposition that deserves deeper follow-up

Use **context** when you need:

- one proposition's local canonical closure
- the evidence needed to explain a specific claim
- a proposition-scoped follow-up

Use state first, then context only for the proposition that matters.

Boundary reminder:

- `get_session_state` is not a list of executed steps or produced artifacts
- after a successful `observe`, state may still be empty if no externally visible proposition has been seeded yet
- when that happens, continue from the returned observe artifact or typed refs instead of treating empty state as a failed run

## `observe` Request Guardrails

`observe` is the basic measurement primitive. Prefer it when you need a clean bounded starting point.

Request rules:

- `granularity` and `dimensions` are mutually exclusive; send only one
- both fields are only valid when `result_mode="standard"`
- for snapshot-style `time_scope.kind` values such as `snapshot_now`, `latest_available`, or `as_of`, do not send `granularity`
- default to `granularity` for time-series output and `dimensions` for grouped comparisons
- common workflow split: use one `observe(..., granularity="day")` to establish persistence over time, then a second `observe(..., dimensions=["dimension.country"])` to explain which segment is driving the shape
- do not probe by sending both fields first; the contract rejects that combination and the follow-up step should be planned explicitly instead

Grouped dimension rules:

- each requested `dimension.*` must already be consumable by the metric
- a metric can consume a requested `dimension.*` when the metric's observed entity exposes that dimension as a stable descriptor
- cross-entity dimension use requires explicit relationship/profile support; do not add metric-owned grounding to bridge it
- when cross-entity support is missing, first search existing relationships/profiles by entity pair;
  create a new `relationship.*` only for key/time/grain/snapshot alignment, then reference it from
  `compiler_profile.*` if the metric/process needs explicit compile-time preconditions
- if the dimension is backed by a missing source field, fix the referenced `entity.<entity>.field.<field>` or entity grounding before changing the metric

Useful failure interpretation:

- `COMPILER_DIMENSION_IMPORT_MISSING`: legacy runtime still could not resolve a usable dimension bridge; prefer fixing the observed entity descriptor/profile path
- `COMPILER_DIMENSION_IMPORT_AMBIGUOUS`: multiple legacy bridge paths expose the same `dimension.*`, so the compiler refuses to guess

## `detect` Request Guardrails

`detect` is the candidate discovery primitive. It returns anomaly candidates, not confirmed root causes.

Request rules:

- send `time_scope` as `{"kind":"range","start":"YYYY-MM-DD","end":"YYYY-MM-DD"}` plus top-level `granularity`
- do not send legacy `time_scope.mode`, `time_scope.current`, or `time_scope.grain`
- `time_scope.kind` must be `range`; snapshot-style observe windows are not valid for `detect`
- use `split_by` for at most one semantic dimension when the scan should run per segment
- use `patterns=["point_anomaly"]` for bucket-level spikes or dips within the scan window
- use `patterns=["period_shift"]` for whole-window current-vs-previous-adjacent movement
- omitted `patterns` defaults to point anomaly scanning, except `profile="level_shift"` enables period-shift scanning

Period-shift interpretation:

- the candidate window is the submitted request range
- the baseline window is the previous adjacent range with equal length
- `candidate_type` is `period_shift`
- `candidate_score` is based on absolute relative deviation when the baseline value is non-zero, otherwise absolute delta
- sensitivity thresholds are `0.30` conservative, `0.20` balanced, and `0.10` aggressive

Consumption rules:

- inspect `detectability.status`, `scan_summary`, `truncation`, and each candidate `candidate_type`
- inspect candidate `baseline_window` for `period_shift`
- inspect candidate `slice` before treating a split candidate as global
- use `candidate_ref` for downstream references; do not cite a projection rank as identity

## `diagnose` Request Guardrails

`diagnose` is a bounded derived intent. Choose the mode based on what the caller already knows.

Use `mode="explicit_compare"` when current and baseline windows are known:

- provide `current.time_scope` and `baseline.time_scope`, each using the observe-aligned range shape
- do not provide top-level `time_scope` or `granularity` in this mode
- the expansion is `observe(current scalar)`, `observe(baseline scalar)`, `compare(mode="scalar")`, then one `decompose` per requested `candidate_dimensions` entry
- `detect_summary` is `null` because no detect step runs

Use `mode="auto_detect"` when the abnormal window is not known:

- provide top-level `time_scope` and `granularity`
- do not provide `current` or `baseline`
- pass `patterns=["period_shift"]` or `profile="level_shift"` when slow degradation is the suspected pattern
- the expansion is `detect`, then current/baseline scalar observe, compare, and decompose for top candidates

If auto-detect returns zero candidates:

- treat `validation.status="needs_attention"` with issue code `no_detect_candidates` as a real diagnostic outcome
- switch to `explicit_compare` when the caller can provide current and baseline windows
- otherwise widen the scan window or enable `period_shift`

## Decompose Additivity Gate

When using `decompose`, the additivity gate validates metric additivity constraints:

- metrics use structured `additivity_constraints` instead of a flat `additivity` field
- the gate checks per-dimension additivity blockers and dimension-level validation
- `additivity_constraints` encode whether the metric is additive, semi-additive, or non-additive
- for semi-additive metrics, the gate validates which dimensions are blocked from decomposition
- gate failures use `ExecutionError` (HTTP 409) with enriched error metadata identifying the specific blocker

## Post-modeling Smoke Test

After creating or revising semantic objects, run one small typed intent before you consider the work done:

1. resolve the target `metric.*` and one representative `dimension.*`
2. create a fresh session
3. submit a bounded `observe` against a narrow time window
4. if grouped analysis is expected, request one known-ready `dimension.*`
5. confirm the response contains real output rather than only readiness or compiler failures

Smoke-test guardrails:

- for `observe`, use a contract-valid `result_mode` such as `standard` unless you explicitly need a sample-summary mode
- if grouped `observe` fails, inspect whether the observed entity exposes the requested `dimension.*` and whether any required relationship/profile is present
- do not repair relationship/profile failures by adding SQL or generic rule fields; fix entity fields,
  relationship alignment, or profile requirements instead
- if failure metadata names type, grain, time, or governance blockers, fix the semantic contract or profile that owns that requirement; do not add physical grounding fields to the intent or metric
- treat a successful resolve plus catalog readiness as necessary but not sufficient; the smoke test is what proves the evidence engine can consume the object graph

For structured time-window rules and `422` repair flow, read `http-contracts.md` instead of expanding those rules here.

## Runtime Status Boundary

Use runtime status or jobs when you need operational progress or failure detail.

Do not use them as substitutes for state or context.

## Read Next

- Read `planning.md` when the task spans multiple dependent investigation steps and you need an orchestration pattern.
- Read `semantic-layer.md` when the real problem is missing semantic meaning, not investigation execution.
- Read `http-contracts.md` when the question is about session ownership, transport success, or cross-surface contract rules.
